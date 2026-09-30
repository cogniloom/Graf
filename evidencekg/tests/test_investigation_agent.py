"""Synthetic CLI subprocess fixtures; these are not live-provider verification."""

import base64
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from evidencekg.investigations.agent import AgentExecutionError, CodexSubscriptionAdapter

ANSWER = {
    "answer": "Synthetic answer",
    "questions": [],
    "citations": [{"segment_id": "s1", "quote": "one"}],
    "documents": [{"name": "report.md", "media_type": "text/markdown", "content": "one"}],
}


def cli(tmp_path, body, *, login="Logged in using ChatGPT", features=None):
    script = tmp_path / "synthetic-codex"
    feature_text = (
        features
        if features is not None
        else "\n".join(
            f"{name} stable true"
            for name in (
                "shell_tool",
                "unified_exec",
                "apps",
                "plugins",
                "memories",
                "multi_agent",
                "hooks",
                "skip_host_skill_discovery",
                "browser_use",
                "computer_use",
            )
        )
    )
    script.write_text(f"""#!{sys.executable}
import json, sys, os, time, subprocess
from pathlib import Path
args = sys.argv[1:]
if args == ['exec', '--help']:
    print('--ignore-user-config --ephemeral --output-schema --json --sandbox')
elif args == ['features', 'list']:
    print({feature_text!r})
elif args == ['login', 'status']:
    print({login!r}, file=sys.stderr)
else:
    Path('argv.json').write_text(json.dumps(args))
    Path('environment.json').write_text(json.dumps(sorted(os.environ)))
    Path('prompt.txt').write_text(sys.stdin.read())
{"".join("    " + line + chr(10) for line in body.splitlines())}
""")
    script.chmod(0o700)
    return str(script)


def success(answer=ANSWER):
    return "\n".join(
        [
            "print(json.dumps({'type':'thread.started','thread_id':'synthetic'}), flush=True)",
            "print('diagnostic café', file=sys.stderr, flush=True)",
            f"print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{json.dumps(answer)!r}}}}}), flush=True)",
            "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}), flush=True)",
        ]
    )


def execute(tmp_path, executable, **options):
    events = []
    adapter = CodexSubscriptionAdapter(executable, **options)
    result = adapter.execute("Synthetic prompt; $(false)", tmp_path, events.append, threading.Event())
    return result, events


def raw(events, phase, stream):
    return b"".join(
        base64.b64decode(e["data"])
        for e in events
        if e["type"] == "adapter.raw" and e["phase"] == phase and e["stream"] == stream
    )


def test_success_controls_and_lossless_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-thread")
    result, events = execute(tmp_path, cli(tmp_path, success()))
    assert result == ANSWER
    assert b"diagnostic caf\xc3\xa9" in raw(events, "execute", "stderr")
    assert b'"thread_id": "synthetic"' in raw(events, "execute", "stdout")
    receipt = events[-1]
    assert receipt["outcome"] == "succeeded"
    assert receipt["attested"] == {"provider": None, "model": None, "effort": None}
    assert receipt["requested"]["model"] == "gpt-6-astra"
    assert receipt["retry_attempts"] == 0
    args = json.loads((tmp_path / "argv.json").read_text())
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in args and "--ephemeral" in args
    assert 'forced_login_method="chatgpt"' in args
    assert "--no-daemon" in args and "--strict-config" in args
    for feature in ("shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "memories"):
        assert any(args[i : i + 2] == ["--disable", feature] for i in range(len(args)))
    assert not any("full-access" in a or "bypass" in a for a in args)
    env = json.loads((tmp_path / "environment.json").read_text())
    assert not {"OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_THREAD_ID"} & set(env)
    assert (tmp_path / "prompt.txt").read_text() == "Synthetic prompt; $(false)"
    assert not (tmp_path / "report.md").exists()


@pytest.mark.parametrize(
    ("body", "outcome"),
    [
        ("print('bad JSON')", "malformed"),
        ("os.write(1, b'\\xff\\n')", "malformed"),
        ("print('{}')", "malformed"),
        ("print(json.dumps({'type':'thread.started'}))", "unknown"),
        ("print(json.dumps({'type':'turn.failed','error':{'message':'failure'}}))", "failed"),
        ("print('stderr failure', file=sys.stderr); sys.exit(17)", "failed"),
        (success({"answer": "missing fields"}), "malformed"),
        (success({**ANSWER, "extra": True}), "malformed"),
        (success({**ANSWER, "citations": [{"segment_id": 4, "quote": "one"}]}), "malformed"),
        (success() + "\nprint(json.dumps({'type':'turn.completed'}))", "unknown"),
        ("print(json.dumps({'type':'item.completed','item':{'type':'command_execution'}}))", "unknown"),
    ],
)
def test_terminal_failures_preserved_without_retry(tmp_path, body, outcome):
    events = []
    adapter = CodexSubscriptionAdapter(cli(tmp_path, body))
    with pytest.raises(AgentExecutionError) as caught:
        adapter.execute("synthetic", tmp_path, events.append, threading.Event())
    assert caught.value.outcome == outcome
    assert events[-1]["outcome"] == outcome
    assert raw(events, "execute", "stdout") or raw(events, "execute", "stderr")
    assert caught.value.receipt["retry_attempts"] == 0


@pytest.mark.parametrize("login", ["Not logged in", "Logged in using an API key"])
def test_subscription_required_no_execution(tmp_path, login):
    with pytest.raises(AgentExecutionError, match="subscription") as caught:
        execute(tmp_path, cli(tmp_path, success(), login=login))
    assert caught.value.outcome == "authentication_required"
    assert not (tmp_path / "argv.json").exists()


def test_missing_isolation_feature_fails_closed(tmp_path):
    with pytest.raises(AgentExecutionError) as caught:
        execute(tmp_path, cli(tmp_path, success(), features="shell_tool stable true"))
    assert caught.value.outcome == "unsupported"
    assert not (tmp_path / "argv.json").exists()


@pytest.mark.parametrize("stream", [1, 2])
def test_output_limit_is_shared_and_preserves_overflow_byte(tmp_path, stream):
    events = []
    adapter = CodexSubscriptionAdapter(
        cli(tmp_path, f"os.write({stream}, b'x' * 1000000)"), max_output_bytes=2000
    )
    with pytest.raises(AgentExecutionError) as caught:
        adapter.execute("synthetic", tmp_path, events.append, threading.Event())
    assert caught.value.outcome == "limit_exceeded"
    assert caught.value.receipt["bytes_captured"] == 2001
    assert sum(len(base64.b64decode(e["data"])) for e in events if e["type"] == "adapter.raw") == 2001


def test_timeout_kills_descendant_that_holds_pipes(tmp_path):
    exe = cli(
        tmp_path,
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "Path('child.pid').write_text(str(p.pid))\nsys.exit(0)",
    )
    started = time.monotonic()
    with pytest.raises(AgentExecutionError) as caught:
        execute(tmp_path, exe, timeout_seconds=1)
    assert caught.value.outcome == "timeout"
    assert time.monotonic() - started < 3
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        stat = Path(f"/proc/{pid}/stat")
        if not stat.exists() or stat.read_text().split()[2] == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail("owned descendant survived timeout")


def test_cancellation_kills_running_process(tmp_path):
    cancel = threading.Event()
    events = []

    def capture(event):
        events.append(event)
        if event.get("phase") == "execute":
            cancel.set()

    exe = cli(tmp_path, "print('started', flush=True)\ntime.sleep(30)")
    with pytest.raises(AgentExecutionError) as caught:
        CodexSubscriptionAdapter(exe).execute("synthetic", tmp_path, capture, cancel)
    assert caught.value.outcome == "cancelled"
    assert raw(events, "execute", "stdout") == b"started\n"
    assert events[-1]["outcome"] == "cancelled"


def test_precancel_does_not_launch(tmp_path):
    cancel = threading.Event()
    cancel.set()
    events = []
    with pytest.raises(AgentExecutionError) as caught:
        CodexSubscriptionAdapter("/does/not/exist").execute("synthetic", tmp_path, events.append, cancel)
    assert caught.value.outcome == "cancelled"
    assert len(events) == 1


def test_project_config_rejected_and_prompt_bounded(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("")
    with pytest.raises(AgentExecutionError) as caught:
        execute(tmp_path, "/does/not/exist")
    assert caught.value.outcome == "blocked"
    (tmp_path / ".codex" / "config.toml").unlink()
    with pytest.raises(AgentExecutionError) as caught:
        execute(tmp_path, "/does/not/exist", max_prompt_bytes=1)
    assert caught.value.outcome == "limit_exceeded"


def test_callback_failure_aborts_and_is_not_success(tmp_path):
    events = []

    def capture(event):
        if event.get("phase") == "execute":
            raise OSError("synthetic persistence unavailable")
        events.append(event)

    with pytest.raises(AgentExecutionError) as caught:
        CodexSubscriptionAdapter(cli(tmp_path, success())).execute(
            "synthetic", tmp_path, capture, threading.Event()
        )
    assert caught.value.outcome == "unknown"
    assert events[-1]["outcome"] == "unknown"


@pytest.mark.parametrize("fail_on", ["all", "outcome"])
def test_unavailable_outcome_callback_never_returns_success(tmp_path, fail_on):
    def capture(event):
        if fail_on == "all" or event["type"] == "adapter.outcome":
            raise OSError("synthetic callback failure")

    with pytest.raises(AgentExecutionError) as caught:
        CodexSubscriptionAdapter(cli(tmp_path, success())).execute(
            "synthetic", tmp_path, capture, threading.Event()
        )
    assert caught.value.outcome == "unknown"
    assert caught.value.receipt["outcome_callback_error"] == "synthetic callback failure"


def test_cli_warning_items_are_retained_with_completed_answer(tmp_path):
    warning = "print(json.dumps({'type':'item.completed','item':{'type':'error','message':'Synthetic startup warning'}}))"
    result, events = execute(tmp_path, cli(tmp_path, warning + "\n" + success()))
    assert result == ANSWER
    assert events[-1]["item_diagnostics"][0]["message"] == "Synthetic startup warning"
    assert events[-1]["outcome"] == "succeeded"


@pytest.mark.parametrize("ids", [("same", "same"), ("", "other"), (" ", "other")])
def test_invalid_clarification_ids_are_rejected(tmp_path, ids):
    answer = dict(ANSWER, questions=[{"id": key, "question": "Question?", "options": []} for key in ids])
    with pytest.raises(AgentExecutionError, match="Invalid structured answer") as caught:
        execute(tmp_path, cli(tmp_path, success(answer)))
    assert caught.value.outcome == "malformed"
