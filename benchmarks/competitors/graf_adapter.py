"""JSON-lines adapter for the actual Graf hybrid runtime, with no fallback."""

import argparse
import contextlib
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    runtime = None
    output = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        from evidencekg.config import initialize
        from evidencekg.hybrid.runtime import Runtime, prepare
        from evidencekg.ingest import ingest
        args.workdir.mkdir(parents=True, exist_ok=True)
        for line in sys.stdin:
            request = json.loads(line)
            try:
                if request["op"] == "ingest":
                    sources = args.workdir / "sources"
                    sources.mkdir(exist_ok=False)
                    for doc in request["documents"]:
                        target = (sources / doc["path"]).resolve()
                        if not target.is_relative_to(sources.resolve()):
                            raise ValueError("Source path escapes corpus")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(doc["text"])
                    store = initialize(args.workdir / "index", sources, {"ocr": "off"})
                    try:
                        snapshot = ingest(store)
                    finally:
                        store.close()
                    result = prepare(args.workdir / "index", args.models, args.database_config,
                                     device=args.device)
                    runtime = Runtime(args.workdir / "index")
                    result = {"status": "ok", "documents": len(request["documents"]),
                              "snapshot": snapshot, "preparation": result, "generative_calls": 0}
                elif request["op"] == "query":
                    if runtime is None:
                        runtime = Runtime(args.workdir / "index")
                    result = runtime.retrieve(request["question"], limit=request.get("limit", 12))
                    result = {"status": "ok", "native": result, "context": "\n\n".join(
                        f"SOURCE: {segment['source_path']}\n{segment['text']}"
                        for segment in result["segments"])}
                else:
                    raise ValueError("Unknown operation")
            except Exception as error:
                result = {"status": "error", "error": type(error).__name__ + ": " + str(error)}
            print(json.dumps(result, ensure_ascii=False, default=str), file=output, flush=True)
        if runtime:
            runtime.close()


if __name__ == "__main__":
    main()
