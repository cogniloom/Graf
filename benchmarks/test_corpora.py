"""Offline corpus contract tests; no model calls or provider dependencies."""

import subprocess
from pathlib import Path, PurePosixPath

import pytest

from benchmarks.corpora import LABEL, _exportable, build_code, build_documents

REPOSITORY = Path(__file__).resolve().parents[1]


def files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def validate_cases(root, cases):
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert set(case) == {
            "id",
            "question",
            "category",
            "expected_answer",
            "required_evidence",
            "answerable",
        }
        assert all(
            isinstance(case[key], str) and case[key].strip()
            for key in ("id", "question", "category", "expected_answer")
        )
        assert type(case["answerable"]) is bool
        assert case["required_evidence"]
        for evidence in case["required_evidence"]:
            assert set(evidence) == {"path", "quote"}
            relative = PurePosixPath(evidence["path"])
            assert not relative.is_absolute() and ".." not in relative.parts
            assert "\\" not in evidence["path"]
            assert evidence["quote"]
            assert evidence["quote"] in (root / evidence["path"]).read_bytes().decode(
                "utf-8"
            )


def test_documents_count_gold_and_determinism(tmp_path):
    small = tmp_path / "small"
    large = tmp_path / "large"
    repeated = tmp_path / "repeat"
    small_cases = build_documents(small, 30)
    cases = build_documents(large)  # Public default must create exactly 1,000 files.
    assert build_documents(repeated, 30) == small_cases == cases
    assert len(files(small)) == 30
    assert len(files(large)) == 1000
    assert files(small) == files(repeated)
    assert all(files(large)[path] == value for path, value in files(small).items())
    assert len(cases) >= 12
    assert len({case["category"] for case in cases}) >= 10
    assert any(not case["answerable"] for case in cases)
    for root in (small, large):
        validate_cases(root, cases)
        for path, content in files(root).items():
            assert Path(path).suffix in {".md", ".txt"}
            assert content.decode("utf-8").startswith(LABEL)
            assert len(content.splitlines()) >= 7
        assert not any("gold" in path or "cases" in path for path in files(root))
    distractors = [
        value for path, value in files(large).items() if path.startswith("operations/")
    ]
    assert len(set(distractors)) == len(distractors)


@pytest.mark.parametrize("count", [-1, 0, 29, True, 30.5, "30"])
def test_invalid_count_does_not_create_destination(tmp_path, count):
    target = tmp_path / "corpus"
    with pytest.raises(ValueError, match="integer >= 30"):
        build_documents(target, count)
    assert not target.exists()


def test_nonempty_destination_preserved(tmp_path):
    original = tmp_path / "keep.txt"
    original.write_text("existing user work", encoding="utf-8")
    with pytest.raises(ValueError, match="empty directory"):
        build_documents(tmp_path, 30)
    assert files(tmp_path) == {"keep.txt": b"existing user work"}


def git(repository, *args):
    return subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    ).stdout


def test_code_exports_head_exactly_with_valid_gold(tmp_path):
    # Local shared clone needs no network and lets the test prove dirty files are
    # ignored without touching the developer checkout or creating any commits.
    clone = tmp_path / "repository"
    git(
        REPOSITORY,
        "clone",
        "--shared",
        "--quiet",
        "--no-hardlinks",
        str(REPOSITORY),
        str(clone),
    )
    changed = clone / "evidencekg/src/evidencekg/ingest.py"
    changed.write_text("dirty worktree sentinel\n", encoding="utf-8")
    (clone / "untracked.txt").write_text("not HEAD\n", encoding="utf-8")
    output = tmp_path / "code"
    cases = build_code(output, clone)
    second = tmp_path / "second"
    assert build_code(second, clone) == cases
    assert files(output) == files(second)
    assert len(cases) >= 8
    validate_cases(output, cases)
    exported = files(output)
    assert "untracked.txt" not in exported
    assert exported["evidencekg/src/evidencekg/ingest.py.txt"] != changed.read_bytes()
    expected = {}
    for entry in git(clone, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, _ = metadata.split()
        path = raw_path.decode("utf-8")
        if (
            kind != b"blob"
            or mode not in (b"100644", b"100755")
            or not _exportable(path)
        ):
            continue
        raw = git(clone, "show", f"HEAD:{path}")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if b"\0" in raw:
            continue
        target = path if Path(path).suffix in {".md", ".txt"} else path + ".txt"
        expected[target] = raw
    assert exported == expected
    assert "README.md" in exported
    assert "evidencekg/src/evidencekg/app/schema.sql.txt" in exported
    assert "product/ui/src/App.tsx.txt" in exported
    assert not any(
        "benchmark" in path.lower() or "package-lock" in path for path in exported
    )


@pytest.mark.parametrize(
    "path",
    [
        "benchmarks/corpora.py",
        "evidencekg/tests/test_benchmark.py",
        "artifacts/report.md",
        "runs/output.json",
        "product/ui/package-lock.json",
        "uv.lock",
        "private/notes.md",
        "docs/private/customer.txt",
        ".env",
        ".env.example",
        "credentials.json",
        "id_rsa",
        "photo.png",
        "source.pdf",
        "private_data/source.txt",
        "secrets/key.txt",
    ],
)
def test_export_excludes_non_source_and_private_names(path):
    assert not _exportable(path)


@pytest.mark.parametrize(
    "path", ["README.md", "src/main.py", "ui/App.tsx", "schema.sql", "Dockerfile"]
)
def test_export_includes_source_and_docs(path):
    assert _exportable(path)


def test_negative_policy_answer_is_answerable(tmp_path):
    cases = {case["id"]: case for case in build_documents(tmp_path / "corpus", 30)}
    assert cases["doc-16"]["answerable"] is True
    assert cases["doc-14"]["answerable"] is False
