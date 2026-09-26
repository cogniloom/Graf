"""Rebuildable, hash-bound exact dense index over original snapshot segments."""

import io
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from evidencekg.db import atomic, dump, sha
from evidencekg.experiments import _locked, _read, _write

from .semantic import exact_cosine_search


class DenseIndex:
    def __init__(self, directory, adapter, segments, snapshot, manifest_sha):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.adapter = adapter
        ids = sorted(sid for sid, s in segments.items() if s["text"].strip())
        identity = {
            "snapshot": snapshot,
            "manifest_sha": manifest_sha,
            "model": asdict(adapter.identity),
            "max_tokens": adapter.max_tokens,
            "batch_size": adapter.batch_size,
            "device": adapter.device,
            "source_sha": sha(dump([(sid, segments[sid]["text_sha"]) for sid in ids])),
            "semantic_sha": sha((Path(__file__).parent / "semantic.py").read_bytes()),
            "implementation_sha": sha(Path(__file__).read_bytes()),
        }
        with _locked(directory):
            marker = directory / "index.json"
            if marker.exists():
                data = _read(marker)
                if data["identity"] != identity:
                    raise ValueError("Dense index identity drift")
            else:
                begin = time.monotonic()
                encoded = adapter.encode_passages([segments[sid]["text"] for sid in ids])
                buffer = io.BytesIO()
                np.save(buffer, encoded.vectors, allow_pickle=False)
                atomic(directory / "vectors.npy", buffer.getvalue())
                data = {
                    "identity": identity,
                    "vectors_sha": sha(buffer.getvalue()),
                    "window_sources": [
                        {"segment_id": ids[i], "start": w.start, "end": w.end}
                        for i, w in zip(encoded.passage_indices, encoded.windows, strict=True)
                    ],
                    "index_seconds": time.monotonic() - begin,
                    "segments": len(ids),
                }
                _write(marker, data)
            raw = (directory / "vectors.npy").read_bytes()
            if sha(raw) != data["vectors_sha"]:
                raise ValueError("Dense vector corruption")
            self.matrix = np.load(io.BytesIO(raw), allow_pickle=False)
            self.sources = data["window_sources"]
            if len(self.matrix) != len(self.sources):
                raise ValueError("Dense window inventory mismatch")
            coverage = {sid: [] for sid in ids}
            for row in self.sources:
                if row["segment_id"] not in coverage:
                    raise ValueError("Foreign dense source")
                coverage[row["segment_id"]].append((row["start"], row["end"]))
            for sid, ranges in coverage.items():
                pos = 0
                for start, end in ranges:
                    if start != pos or end <= start:
                        raise ValueError("Dense coverage gap")
                    pos = end
                if pos != len(segments[sid]["text"]):
                    raise ValueError("Dense source tail missing")
            self.manifest = data

    def search(self, question):
        vector = self.adapter.encode_queries([question])[0]
        hits = exact_cosine_search(vector, self.matrix, k=len(self.matrix))
        scores = {}
        for i, score in hits:
            sid = self.sources[i]["segment_id"]
            scores.setdefault(sid, score)
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
