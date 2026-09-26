"""Question-only local inference runner; never loads benchmark gold/reference data."""

import time

from evidencekg.db import atomic, dump, sha
from evidencekg.experiments import _locked, _read, _write

from .benchmark import check_protocol
from .runtime import ROOT, setup


def main():
    protocol = check_protocol()
    queries = _read(ROOT / "queries.json")
    if (
        len(queries) != 100
        or sha(dump(queries)) != protocol["queries_sha"]
        or {q["question_id"] for q in queries} != {sha(x) for x in protocol["test_ids"]}
    ):
        raise ValueError("Frozen question inventory mismatch")
    store, ledger, ranker, timing = setup()
    try:
        if ranker.identity != protocol["ranker_identity"]:
            raise ValueError("Frozen model/index/ranking identity mismatch")
        if not (ROOT / "setup.json").exists():
            _write(ROOT / "setup.json", timing)
        for number, item in enumerate(queries, 1):
            folder = ROOT / "retrieval" / sha(item["question"])
            folder.mkdir(parents=True, exist_ok=True)
            with _locked(folder):
                request = {
                    "question": item["question"],
                    "question_id": item["question_id"],
                    "retrieval_identity": ranker.identity,
                    "protocol_sha": sha(dump(protocol)),
                }
                if (folder / "intent.json").exists():
                    if _read(folder / "intent.json") != request:
                        raise ValueError("Retrieval intent drift")
                    if not (folder / "packet.json").exists():
                        raise ValueError("Unresolved local retrieval intent")
                    continue
                _write(folder / "intent.json", request)
                start = time.monotonic()
                try:
                    result = ranker.retrieve(item["question"])
                    _write(folder / "packet.json", result)
                except BaseException as exc:
                    _write(folder / "failure.json", {"error": type(exc).__name__ + ": " + str(exc)})
                    raise
                print(
                    "RETRIEVED",
                    number,
                    "of100",
                    round(time.monotonic() - start, 2),
                    "seconds",
                    result["local_inference"],
                    flush=True,
                )
                atomic(
                    ROOT / "retrieval-status.json",
                    dump({"completed": number, "total": 100, "updated_at": time.time()}).encode(),
                )
        atomic(
            ROOT / "retrieval-completed.json", dump({"questions": 100, "completed_at": time.time()}).encode()
        )
    finally:
        ledger.close()
        store.close()


if __name__ == "__main__":
    main()
