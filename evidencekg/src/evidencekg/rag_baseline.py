"""Optional isolated native Lance FTS comparator; never Haiku hybrid or inference.

Input JSON: {"snapshot_id": str, "segments": [original EvidenceKG segment, ...]}.
Only explicitly supplied segments are indexed. No live vault/database is opened.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

VERSION = "isolated-lance-fts-v1"
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_EVIDENCE_BYTES = 60000
MAX_SEGMENTS = 100000
DEFAULT_PYTHON = Path(__file__).resolve().parents[3] / ".venv/bin/python"


class OptionalRuntimeUnavailable(RuntimeError):
    """The explicitly selected optional Python runtime is absent."""


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Frozen input exceeds byte bound")
    return path.read_bytes()


def _validate(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"snapshot_id", "segments"}:
        raise ValueError("Expected snapshot_id and segments only")
    if not isinstance(data["snapshot_id"], str) or not data["snapshot_id"].strip():
        raise ValueError("Missing snapshot identity")
    segments = data["segments"]
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise ValueError("Invalid segment count")
    seen = set()
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Invalid segment")
        for key in ("id", "document_version_id", "extraction_id", "text"):
            if not isinstance(segment.get(key), str) or not segment[key].strip():
                raise ValueError("Missing segment identity or text")
        if segment["id"] in seen:
            raise ValueError("Duplicate segment ID")
        seen.add(segment["id"])
        if "text_sha" in segment and segment["text_sha"] != _sha(segment["text"].encode()):
            raise ValueError("Segment text hash differs")
    return data


def _tree(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed in the isolated index")
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            files[str(path.relative_to(root))] = digest.hexdigest()
    return files


def _run(request, python, timeout):
    python = Path(python).absolute()
    if not python.is_file():
        raise OptionalRuntimeUnavailable("Optional Lance Python executable is absent")
    if not 1 <= timeout <= 1800:
        raise ValueError("Timeout must be within 1..1800 seconds")
    # No inherited credentials, service configuration, PYTHONPATH or model caches.
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": request["root"],
        "OMP_NUM_THREADS": "2",
        "RAYON_NUM_THREADS": "2",
    }
    try:
        result = subprocess.run(
            [str(python), "-I", str(Path(__file__).resolve()), "--isolated-worker"],
            input=_dump(request),
            text=True,
            capture_output=True,
            timeout=timeout,
            cwd=request["root"],
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Isolated FTS subprocess timed out") from None
    if result.returncode:
        # Native errors can contain source/query text; do not relay their output.
        raise RuntimeError("Isolated FTS subprocess failed; no provider fallback attempted")
    if len(result.stdout.encode()) > MAX_INPUT_BYTES:
        raise RuntimeError("FTS subprocess output exceeds bound")
    return json.loads(result.stdout)


def build(frozen_json, destination, *, python=DEFAULT_PYTHON, timeout=300):
    """Create a NEW local directory; existing paths are never reused/overwritten."""
    raw = _read(Path(frozen_json))
    _validate(raw)
    root = Path(destination).absolute()
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    (root / "frozen.json").write_bytes(raw)
    return _run({"operation": "build", "root": str(root)}, python, timeout)


def search(
    destination,
    query,
    *,
    snapshot_id,
    limit=6,
    max_evidence_bytes=MAX_EVIDENCE_BYTES,
    python=DEFAULT_PYTHON,
    timeout=60,
):
    """Retrieve unchanged original passages, with explicit expected snapshot scope."""
    if not isinstance(query, str) or not query.strip() or len(query) > 4096:
        raise ValueError("Query must contain 1..4096 characters")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Limit must be within 1..1000")
    if type(max_evidence_bytes) is not int or not 30 <= max_evidence_bytes <= MAX_EVIDENCE_BYTES:
        raise ValueError("Invalid passage byte budget")
    root = Path(destination).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected isolated index directory")
    return _run(
        {
            "operation": "search",
            "root": str(root),
            "query": query,
            "snapshot_id": snapshot_id,
            "limit": limit,
            "max_evidence_bytes": max_evidence_bytes,
        },
        python,
        timeout,
    )


def _offline_guard():
    """Fail closed: Linux seccomp blocks outbound networking even from native code."""
    import ctypes
    import ctypes.util
    import errno

    if sys.platform != "linux":
        raise RuntimeError("This optional adapter requires Linux network confinement")
    lib = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2")
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not ctx:
        raise RuntimeError("Cannot initialize network confinement")
    try:
        for name in (b"connect", b"io_uring_setup"):
            syscall = lib.seccomp_syscall_resolve_name(name)
            if syscall < 0 or lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, syscall, 0):
                raise RuntimeError("Cannot install network confinement")

        # Permit anonymous Unix socketpairs used by asyncio thread wakeups, but
        # prohibit all non-Unix sockets (IPv4/IPv6/raw/packet), including UDP.
        class Compare(ctypes.Structure):
            _fields_ = [
                ("arg", ctypes.c_uint),
                ("op", ctypes.c_uint),
                ("datum_a", ctypes.c_uint64),
                ("datum_b", ctypes.c_uint64),
            ]

        lib.seccomp_rule_add_array.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.POINTER(Compare),
        ]
        comparison = Compare(0, 1, 1, 0)  # argument 0 != AF_UNIX (SCMP_CMP_NE)
        syscall = lib.seccomp_syscall_resolve_name(b"socket")
        if syscall < 0 or lib.seccomp_rule_add_array(
            ctx, 0x00050000 | errno.EPERM, syscall, 1, ctypes.byref(comparison)
        ):
            raise RuntimeError("Cannot prohibit network sockets")
        if lib.seccomp_load(ctx):
            raise RuntimeError("Cannot enforce network confinement")
    finally:
        lib.seccomp_release(ctx)

    import importlib.util

    class NoProviders:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            raise RuntimeError("Inference/provider imports prohibited")

        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {
                "haiku",
                "pydantic_ai",
                "ollama",
                "openai",
                "sentence_transformers",
                "transformers",
                "torch",
            }:
                return importlib.util.spec_from_loader(fullname, self)
            return None

    sys.meta_path.insert(0, NoProviders())
    # Exercise the prohibition locally; the connect syscall is rejected before routing.
    import socket

    try:
        with socket.socket() as sock:
            sock.connect(("127.0.0.1", 9))
    except PermissionError:
        pass  # An inherited sandbox may already reject socket creation itself.
    else:
        raise RuntimeError("Network confinement probe failed")
    try:
        __import__("haiku.rag.embeddings")
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Embedding prohibition probe failed")


def _worker(request):
    _offline_guard()
    import importlib.metadata

    import lance
    import pyarrow as pa
    from lance.query import MatchQuery

    root = Path(request["root"])
    if any((root / name).is_symlink() for name in ("frozen.json", "manifest.json", "index")):
        raise ValueError("Isolated artifacts must not be symlinks")
    raw = _read(root / "frozen.json")
    frozen = _validate(raw)
    provenance = {
        "adapter": VERSION,
        "backend": "direct-lance-native-fts",
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "pylance": importlib.metadata.version("pylance"),
        "pyarrow": importlib.metadata.version("pyarrow"),
        "adapter_sha256": _sha(Path(__file__).read_bytes()),
        "network_guard": "linux-seccomp-network-sockets-connect-io_uring-deny; probe passed",
        "inference_guard": "provider imports prohibited; embedding import probe passed",
        "query_policy": "MatchQuery OR, exact tokens, no fuzzy expansion",
        "index_policy": "simple tokenizer; lowercase; ascii folding; no stemming or stopword removal",
        "score_semantics": "native Lance BM25 relevance, higher is better; not comparable across engines",
    }
    if request["operation"] == "build":
        table = lance.write_dataset(
            pa.Table.from_pylist([{"id": s["id"], "text": s["text"]} for s in frozen["segments"]]),
            str(root / "index"),
            mode="create",
        )
        table.create_scalar_index("text", "INVERTED", stem=False, remove_stop_words=False)
        manifest = {
            "snapshot_id": frozen["snapshot_id"],
            "input_sha256": _sha(raw),
            "segment_count": len(frozen["segments"]),
            "table_version": table.version,
            "index_files": _tree(root / "index"),
            "provenance": provenance,
        }
        (root / "manifest.json").write_text(_dump(manifest))
        return manifest

    manifest = json.loads((root / "manifest.json").read_text())
    if (
        request["snapshot_id"] != frozen["snapshot_id"]
        or manifest["snapshot_id"] != frozen["snapshot_id"]
        or manifest["input_sha256"] != _sha(raw)
    ):
        raise ValueError("Frozen snapshot scope or input changed")
    if manifest["provenance"] != provenance:
        raise ValueError("Adapter runtime changed; rebuild the isolated comparator")
    if manifest["index_files"] != _tree(root / "index"):
        raise ValueError("Frozen index changed")
    table = lance.dataset(str(root / "index"), version=manifest["table_version"])
    originals = {s["id"]: s for s in frozen["segments"]}
    # Enumerate the bounded frozen scope for stable tie handling and byte-budget backfill.
    hits = table.to_table(
        full_text_query=MatchQuery(request["query"], "text"), limit=len(originals)
    ).to_pylist()
    hits.sort(key=lambda h: (-h["_score"], h["id"]))
    segments, scores = [], {}
    for hit in hits:
        segment = originals[hit["id"]]
        if hit["text"] != segment["text"]:
            raise ValueError("Index passage differs from frozen input")
        proposed = segments + [segment]
        if (
            len(segments) < request["limit"]
            and len(_dump({"segments": proposed, "reasons": {}}).encode()) <= request["max_evidence_bytes"]
        ):
            segments = proposed
            scores[segment["id"]] = hit["_score"]
    if manifest["index_files"] != _tree(root / "index"):
        raise ValueError("Index mutated during search")
    return {
        "snapshot_id": frozen["snapshot_id"],
        "segments": segments,
        "reasons": {},
        "scores": scores,
        "selected_count": len(segments),
        "candidate_count": len(hits),
        "omitted_count": len(hits) - len(segments),
        "limit": request["limit"],
        "max_evidence_bytes": request["max_evidence_bytes"],
        "evidence_bytes": len(_dump({"segments": segments, "reasons": {}}).encode()),
        "input_sha256": manifest["input_sha256"],
        "provenance": provenance,
    }


if __name__ == "__main__":
    if sys.argv[1:] != ["--isolated-worker"]:
        raise SystemExit("Use the optional Python build/search API")
    print(_dump(_worker(json.loads(sys.stdin.read(16384)))))
