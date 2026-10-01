"""Subscription-only, bounded CLI adapter; no vault or document writes.

Callbacks receive JSON-safe raw byte chunks BEFORE parsing (base64 preserves even
invalid UTF-8), followed by an outcome receipt. Chunk boundaries are not event
boundaries; concatenate by phase/stream to reconstruct exposed CLI events. Capture
covers only bytes read before cancellation/limits, never hidden provider reasoning,
server activity, or a complete network transcript. Callbacks must return promptly;
a failed callback aborts execution rather than silently losing evidence.
"""

from __future__ import annotations

import base64
import json
import os
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .evidence_contract import CONCLUSION, INSTRUCTIONS

EventCallback = Callable[[dict[str, Any]], None]
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "citations", "documents", "questions", "conclusions"],
    "properties": {
        "answer": {"type": "string"},
        "conclusions": {"type": "array", "maxItems": 40, "items": CONCLUSION},
        "questions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "question", "options", "kind", "reason"],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "question": {"type": "string", "minLength": 1, "maxLength": 500},
                    "kind": {"enum": ["identity", "time", "scope", "missing_evidence"]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "options": {
                        "type": "array",
                        "maxItems": 6,
                        "items": {"type": "string", "minLength": 1, "maxLength": 200},
                    },
                },
            },
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["segment_id", "quote"],
                "properties": {"segment_id": {"type": "string"}, "quote": {"type": "string"}},
            },
        },
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "media_type", "content"],
                "properties": {k: {"type": "string"} for k in ("name", "media_type", "content")},
            },
        },
    },
}


def validate_questions(result):
    questions = result.get("questions", [])
    if not isinstance(questions, list) or len(questions) > 3:
        raise ValueError("Codex must return at most three clarification questions")
    ids = set()
    for question in questions:
        key = question.get("id") if isinstance(question, dict) else None
        text = question.get("question") if isinstance(question, dict) else None
        options = question.get("options") if isinstance(question, dict) else None
        if (
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 64
            or key in ids
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > 500
            or not isinstance(options, list)
            or len(options) > 6
            or any(not isinstance(o, str) or not o.strip() or len(o) > 200 for o in options)
        ):
            raise ValueError("Invalid clarification questions: use distinct nonempty IDs and bounded text")
        ids.add(key)
    for question in questions:
        if question.get("kind") not in {"identity", "time", "scope", "missing_evidence"} or not (
            isinstance(question.get("reason"), str) and question["reason"].strip()
            and len(question["reason"]) <= 1000
        ):
            raise ValueError("Clarification questions require a kind and a reason that explains why the answer matters")


class AgentAdapter(Protocol):
    def execute(
        self,
        prompt: str,
        cwd: Path,
        on_event: EventCallback,
        cancel: threading.Event,
        model: str = "gpt-6-astra",
        effort: str = "medium",
    ) -> dict: ...


class AgentExecutionError(RuntimeError):
    """A terminal outcome. No automatic retries; receipt is suitable for storage."""

    def __init__(self, outcome: str, message: str, receipt: dict | None = None):
        super().__init__(message)
        self.outcome = outcome
        self.receipt = receipt or {}


# Discover supported names from the installed CLI, rather than passing silently
# ignored feature names. Removed flags do not establish a security boundary.
_DISABLED_FEATURES = {
    "shell_tool",
    "unified_exec",
    "unified_exec_tty",
    "shell_snapshot",
    "shell_snapshot_v2",
    "apps",
    "plugins",
    "remote_plugin",
    "plugin_sharing",
    "recommended_plugins",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "in_app_browser",
    "in_app_local_automation",
    "multi_agent",
    "multi_agent_v2",
    "agent_message_board",
    "memories",
    "external_agent_memory_import",
    "chronicle",
    "skill_search",
    "skill_mcp_dependency_install",
    "hooks",
    "code_mode",
    "code_mode_host",
    "code_mode_only",
    "code_mode_prewarm",
    "image_generation",
    "view_image",
    "artifact",
    "tool_suggest",
    "request_permissions_tool",
    "default_mode_request_user_input",
    "sleep_tool",
    "goals",
    "workspace_dependencies",
    "enable_mcp_apps",
    "auth_elicitation",
    "api_key_model_discovery",
    "unbounded_connection_retries",
    "standalone_web_search",
    "realtime_conversation",
    "daemon_auto_start",
}


class CodexSubscriptionAdapter:
    """POSIX process-group isolation and subscription authentication only.

    cwd must be a disposable directory provided by Graf, without project Codex
    configuration. Read-only is not a confidential-file read boundary. The CLI
    retains access to its existing authentication location; credentials are never
    inspected or copied. Managed CLI policy still applies and may reject a run.
    Requested identity is recorded; exec JSONL does not attest effective identity.
    """

    def __init__(
        self,
        executable: str = "codex",
        *,
        timeout_seconds: float = 600,
        max_output_bytes: int = 4 * 1024 * 1024,
        max_prompt_bytes: int = 1024 * 1024,
    ):
        if timeout_seconds <= 0 or max_output_bytes < 1 or max_prompt_bytes < 1:
            raise ValueError("Bounds must be positive")
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.max_prompt_bytes = max_prompt_bytes

    def execute(
        self,
        prompt: str,
        cwd: Path,
        on_event: EventCallback,
        cancel: threading.Event,
        model: str = "gpt-6-astra",
        effort: str = "medium",
    ) -> dict:
        receipt = {
            "type": "adapter.outcome",
            "adapter": "codex-cli-subscription",
            "requested": {"provider": "openai", "model": model, "effort": effort},
            "attested": {"provider": None, "model": None, "effort": None},
            "capture": "Exposed CLI stdout/stderr only; no hidden reasoning or network completeness",
            "bytes_captured": 0,
            "diagnostics": "",
            "retry_attempts": 0,
            "retry_scope": "Adapter process launches are not retried; CLI internal retries may occur",
        }
        deadline = time.monotonic() + self.timeout_seconds
        # Deliberately omit API tokens, endpoint overrides, tracing exporters,
        # inherited proxy/process injection settings and parent session identity.
        env = {
            k: os.environ[k]
            for k in (
                "PATH",
                "HOME",
                "CODEX_HOME",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_RUNTIME_DIR",
                "LANG",
                "LC_ALL",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
            )
            if k in os.environ
        }
        env["NO_COLOR"] = "1"

        def run(args: list[str], phase: str, stdin: bytes = b"") -> tuple[int, bytes, bytes]:
            if cancel.is_set():
                raise AgentExecutionError("cancelled", "Cancelled before process launch")
            if time.monotonic() >= deadline:
                raise AgentExecutionError("timeout", "Adapter deadline exceeded")
            with tempfile.TemporaryFile() as input_file:
                input_file.write(stdin)
                input_file.seek(0)
                proc = subprocess.Popen(
                    [self.executable, *args],
                    cwd=cwd,
                    env=env,
                    stdin=input_file,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    start_new_session=True,
                )
            streams = {"stdout": bytearray(), "stderr": bytearray()}
            try:
                with selectors.DefaultSelector() as selector:
                    for name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
                        selector.register(pipe, selectors.EVENT_READ, name)
                    while selector.get_map() or proc.poll() is None:
                        if cancel.is_set():
                            raise AgentExecutionError("cancelled", "Execution cancelled")
                        if time.monotonic() >= deadline:
                            raise AgentExecutionError("timeout", "Adapter deadline exceeded")
                        for key, _ in selector.select(timeout=min(0.05, max(0, deadline - time.monotonic()))):
                            remaining = self.max_output_bytes - receipt["bytes_captured"]
                            data = os.read(key.fileobj.fileno(), min(8192, remaining + 1))
                            if not data:
                                selector.unregister(key.fileobj)
                                continue
                            # Preserve bytes, including the overflow detection byte, before consuming.
                            on_event(
                                {
                                    "type": "adapter.raw",
                                    "phase": phase,
                                    "stream": key.data,
                                    "encoding": "base64",
                                    "data": base64.b64encode(data).decode("ascii"),
                                }
                            )
                            receipt["bytes_captured"] += len(data)
                            if key.data == "stderr":
                                receipt["diagnostics"] = (
                                    receipt["diagnostics"] + data.decode("utf-8", errors="replace")
                                )[-8192:]
                            if receipt["bytes_captured"] > self.max_output_bytes:
                                raise AgentExecutionError("limit_exceeded", "CLI output limit exceeded")
                            streams[key.data].extend(data)
                return proc.wait(), bytes(streams["stdout"]), bytes(streams["stderr"])
            finally:
                # Kill the owned group even if the leader already exited: descendants
                # can still hold pipes open. Never signal unrelated processes.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                proc.stdout.close()
                proc.stderr.close()

        try:
            if os.name != "posix":
                raise AgentExecutionError("unsupported", "POSIX process-group support required")
            cwd = Path(cwd).resolve(strict=True)
            if not cwd.is_dir():
                raise AgentExecutionError("blocked", "Working directory is not a directory")
            for parent in (cwd, *cwd.parents):
                if (parent / ".codex" / "config.toml").exists():
                    # User config is ignored explicitly, but project config could
                    # introduce MCP servers or alternate providers.
                    if parent != Path(env.get("HOME", "")).resolve():
                        raise AgentExecutionError("blocked", "Project Codex configuration is not allowed")
            if not isinstance(prompt, str) or len(prompt.encode("utf-8")) > self.max_prompt_bytes:
                raise AgentExecutionError("limit_exceeded", "Prompt exceeds input bound")
            if not isinstance(model, str) or not model or effort not in {"low", "medium", "high"}:
                raise AgentExecutionError("blocked", "Invalid model or effort")
            code, help_out, _ = run(["exec", "--help"], "help")
            required = ("--ignore-user-config", "--ephemeral", "--output-schema", "--json", "--sandbox")
            if code or any(flag.encode() not in help_out for flag in required):
                raise AgentExecutionError("unsupported", "CLI lacks required isolation/output flags")
            code, features, _ = run(["features", "list"], "features")
            if code:
                raise AgentExecutionError("unsupported", "Cannot inspect CLI feature controls")
            available = {
                line.split()[0]
                for line in features.decode().splitlines()
                if line.split() and "removed" not in line.split()
            }
            if not {"shell_tool", "unified_exec", "skip_host_skill_discovery"} <= available:
                raise AgentExecutionError("unsupported", "CLI lacks required tool isolation controls")
            code, out, err = run(["login", "status"], "login")
            if (
                code
                or "Logged in using ChatGPT" not in (out + err).decode("utf-8", errors="replace").splitlines()
            ):
                raise AgentExecutionError(
                    "authentication_required", "Existing ChatGPT subscription login required"
                )
            receipt["authentication"] = "CLI reports ChatGPT login; no credentials inspected"
            args = [
                "--no-daemon",
                "--ask-for-approval",
                "never",
                "exec",
                "--ignore-user-config",
                "--strict-config",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--json",
                "--color",
                "never",
                "--model",
                model,
            ]
            for flag in sorted(_DISABLED_FEATURES & available):
                args.extend(["--disable", flag])
            args.extend(["--enable", "skip_host_skill_discovery"])
            for config in (
                'model_provider="openai"',
                'forced_login_method="chatgpt"',
                'web_search="disabled"',
                "project_doc_max_bytes=0",
                "developer_instructions=" + json.dumps(INSTRUCTIONS),
                "model_reasoning_effort=" + json.dumps(effort),
            ):
                args.extend(["-c", config])
            receipt["disabled_features"] = sorted(_DISABLED_FEATURES & available)
            receipt["isolation_limits"] = (
                "CLI flags are requested controls, not independent tool-surface attestation; "
                "managed policy and built-in instructions may remain; read-only does not restrict reads"
            )
            with tempfile.TemporaryDirectory(prefix="graf-agent-schema-") as schema_dir:
                schema_path = Path(schema_dir) / "answer.json"
                schema_path.write_text(json.dumps(OUTPUT_SCHEMA), encoding="utf-8")
                args.extend(["--output-schema", str(schema_path), "-"])
                code, output, _ = run(args, "execute", prompt.encode("utf-8"))
            receipt["exit_code"] = code
            if code:
                raise AgentExecutionError("failed", "Codex exited unsuccessfully")
            answer = None
            completed = False
            for line in output.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise AgentExecutionError("malformed", "Invalid CLI JSONL") from exc
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise AgentExecutionError("malformed", "Invalid CLI event")
                kind = event["type"]
                if kind in {"error", "turn.failed"}:
                    raise AgentExecutionError("failed", "CLI reported a failed turn")
                if kind == "turn.completed":
                    if completed:
                        raise AgentExecutionError("unknown", "Multiple completed turns")
                    completed = True
                if kind == "item.completed":
                    item = event.get("item")
                    if not isinstance(item, dict):
                        raise AgentExecutionError("malformed", "Invalid completed item")
                    if item.get("type") == "agent_message":
                        answer = item.get("text")
                    elif item.get("type") == "error":
                        # Codex emits startup warnings as error items even when
                        # the subsequent turn succeeds. Preserve these diagnostics;
                        # only explicit turn completion can establish success.
                        receipt.setdefault("item_diagnostics", []).append(item)
                    elif item.get("type") not in {"reasoning"}:
                        raise AgentExecutionError("unknown", "Unexpected tool or completed item")
            if not completed or not isinstance(answer, str):
                raise AgentExecutionError("unknown", "Missing successful completion or answer")
            try:
                result = json.loads(answer)
                errors = list(Draft202012Validator(OUTPUT_SCHEMA).iter_errors(result))
                if errors:
                    raise ValueError("Answer does not match schema")
                validate_questions(result)
            except (ValueError, TypeError) as exc:
                raise AgentExecutionError("malformed", "Invalid structured answer") from exc
            if cancel.is_set():
                raise AgentExecutionError("cancelled", "Cancelled before returning answer")
            receipt["outcome"] = "succeeded"
        except Exception as exc:
            outcome = exc.outcome if isinstance(exc, AgentExecutionError) else "unknown"
            receipt.update(outcome=outcome, error=str(exc))
            try:
                on_event(receipt.copy())
            except Exception as callback_exc:
                receipt["outcome_callback_error"] = str(callback_exc)
            raise AgentExecutionError(outcome, str(exc), receipt) from exc
        try:
            on_event(receipt.copy())
        except Exception as exc:
            receipt.update(outcome="unknown", outcome_callback_error=str(exc))
            raise AgentExecutionError("unknown", "Outcome persistence callback failed", receipt) from exc
        return result
