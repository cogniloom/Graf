import copy
import json

import pytest

from evidencekg.db import dump, sha
from evidencekg.hybrid.code_context import _documents, _index, build_code_context


def snapshot(files, size=80):
    segments = {}
    for path, text in files.items():
        for ordinal, lo in enumerate(range(0, len(text), size)):
            body = text[lo:lo + size]
            sid = f"{path}:{ordinal}"
            segments[sid] = dict(id=sid, document_version_id=path, source_path=path, ordinal=ordinal,
                                 char_start=lo, char_end=lo + len(body), text=body, text_sha=sha(body))
    return segments


def packet(segments):
    return dict(snapshot_id="frozen", segments=list(segments.values())[:12])


def test_cross_file_validation_and_unicode_spans():
    segments = snapshot({
        "pkg/select.py": "from .validation import validate\n\ndef choices(ref):\n    # café\n    validate(ref)\n",
        "pkg/validation.py": 'def validate(ref):\n    if ref != "é":\n        raise ValueError("invalid quote")\n',
    })
    original = copy.deepcopy(segments)
    result = build_code_context(segments, "How do choices validate a quote?", packet(segments))
    assert any(e.get("symbol") == "validate" for e in result["evidence"])
    for evidence in result["evidence"]:
        for span in [evidence] + evidence.get("enclosing_source", []):
            assert span["text"] == "".join(segments[r["segment_id"]]["text"][r["start"]:r["end"]]
                                           for r in span["source_refs"])
    assert segments == original


@pytest.mark.parametrize("damage", ["hash", "gap", "overlap", "path", "packet"])
def test_source_drift_fails_closed(damage):
    segments = snapshot({"a.py": "def hello():\n    return 'x'\n"}, size=10)
    p = copy.deepcopy(packet(segments))
    values = list(segments.values())
    if damage == "hash":
        values[0]["text"] += "changed"
    elif damage in ("gap", "overlap"):
        values[1]["char_start"] += 1 if damage == "gap" else -1
    elif damage == "path":
        values[1]["source_path"] = "other.py"
    else:
        p["segments"][0]["text"] = "foreign"
    with pytest.raises(ValueError):
        build_code_context(segments, "hello", p)


def test_else_and_except_are_preserved_for_every_split_statement():
    padding = "        value = '" + "x" * 6700 + "'\n"
    source = "def run(ok):\n    if ok:\n" + padding + (
        "    else:\n        first = 1\n        second = 2\n"
        "    try:\n" + padding + "    except ValueError:\n        recovery = 1\n        finish = 2\n")
    docs = _documents(snapshot({"a.py": source}))
    units, _ = _index(docs)
    for marker, header in [("second =", "else:"), ("recovery =", "except ValueError:"),
                           ("finish =", "except ValueError:")]:
        unit = next(u for u in units if marker in u["text"])
        assert header in "".join(source[a:b] for a, b in unit["parents"])


def test_shadowed_import_is_not_a_dependency():
    segments = snapshot({"pkg/a.py": "from .b import validate\n\ndef run(validate):\n    validate('x')\n",
                         "pkg/b.py": "def validate(value):\n    return value\n"})
    units, _ = _index(_documents(segments))
    assert next(u for u in units if u["symbol"] == "run")["calls"] == []


def test_relative_module_import_resolves_package_initializer():
    segments = snapshot({'pkg/ingest.py': 'from . import parsers\n\ndef run():\n    return parsers.parse()\n',
                         'pkg/parsers/__init__.py': 'def parse():\n    return "done"\n'})
    units, _ = _index(_documents(segments))
    assert next(u for u in units if u['symbol'] == 'run')['calls'] == ['pkg.parsers.parse']
    assert next(u for u in units if u['symbol'] == 'parse')['module'] == 'pkg.parsers'


def test_relative_module_import_shadow_remains_unresolved():
    segments = snapshot({'pkg/ingest.py': 'from . import parsers\n\ndef run(parsers):\n    return parsers.parse()\n'})
    units, _ = _index(_documents(segments))
    assert next(u for u in units if u['symbol'] == 'run')['calls'] == []


@pytest.mark.parametrize("rebind", ["validate = replacement\n", "def validate():\n    return 0\n",
                                    "if flag:\n    from pkg.other import validate\n"])
def test_module_rebound_import_is_not_a_dependency(rebind):
    segments = snapshot({"pkg/a.py": "from .b import validate\n" + rebind +
                         "\ndef run():\n    return validate()\n",
                         "pkg/b.py": "def validate():\n    return 1\n"})
    units, _ = _index(_documents(segments))
    assert next(u for u in units if u["symbol"] == "run")["calls"] == []


def test_unicode_line_separator_is_not_a_python_newline():
    text = 'banner = "hello\u2028world"\ndef target():\n    return 42\n'
    units, _ = _index(_documents(snapshot({"a.py": text})))
    assert next(u for u in units if u["symbol"] == "target")["text"] == "def target():\n    return 42\n"


def test_large_seed_retried_after_reserving_dependency_space():
    text = "def target():\n    return '" + "x" * 2200 + "'\n"
    segments = snapshot({"a.py": text}, size=6000)
    result = build_code_context(segments, "target", packet(segments), max_bytes=6000)
    assert any(e.get("symbol") == "target" for e in result["evidence"])


def test_skipped_python_original_is_retained_and_budget_is_enforced():
    segments = snapshot({"broken.py.txt": "def wrong(:\n", "valid.py": "def ok():\n    return 1\n"})
    result = build_code_context(segments, "wrong ok", packet(segments), max_bytes=4000)
    assert any(e["path"] == "broken.py.txt" for e in result["evidence"])
    assert result["limitations"]["skipped_python"]
    assert len(dump(result).encode()) <= 4000


def test_documents_only_and_unknown_language_keep_originals():
    segments = snapshot({"readme.md": "Guide to validation\n", "app.js": "function validate() {}\n"})
    result = build_code_context(segments, "validation", packet(segments))
    assert {e["path"] for e in result["evidence"]} == {"readme.md", "app.js"}
    assert result["limitations"]["indexed_units"] == 0


def test_nonfinite_reranker_is_rejected():
    class Bad:
        def score(self, question, texts):
            return type("Scores", (), {"scores": [float("nan")] * len(texts)})()

    segments = snapshot({"a.py": "def go():\n    return 1\n"})
    with pytest.raises(ValueError, match="reranker"):
        build_code_context(segments, "go", packet(segments), reranker=Bad())


def test_cli_opt_in_and_mechanical_rejection(tmp_path, monkeypatch, capsys):
    from evidencekg.cli import main
    from evidencekg.hybrid import runtime

    seen = []

    class Runtime:
        def __init__(self, *args):
            pass

        def retrieve(self, question, **kwargs):
            seen.append(kwargs)
            return {"segments": ["original"], "code_context": {"version": "test-view", "evidence": []}}

        def close(self):
            pass

    monkeypatch.setattr(runtime, "Runtime", Runtime)
    assert main(["--state", str(tmp_path), "discover", "go", "--code-context"]) == 0
    assert seen == [dict(limit=12, snapshot=None, code_context=True)]
    assert json.loads(capsys.readouterr().out) == {"version": "test-view", "evidence": []}
    assert main(["--state", str(tmp_path), "discover", "go", "--backend", "mechanical", "--code-context"]) == 1
    assert "requires --backend hybrid" in capsys.readouterr().err
    assert not (tmp_path / "evidence.sqlite3").exists()
