"""Reproducible synthetic high-frequency-key benchmark; never production throughput claims."""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from evidencekg.config import initialize
from evidencekg.ingest import ingest
from evidencekg.retrieval import API


def measure(count=20000):
    with tempfile.TemporaryDirectory(prefix="evidencekg-benchmark-") as tmp:
        base = Path(tmp)
        root = base / "sources"
        root.mkdir()
        for i in range(count):
            (root / f"{i:06}.txt").write_text(f"Case: COMMON-KEY\n\nSynthetic evidence {i}.")
        source_bytes = sum(p.stat().st_size for p in root.iterdir())
        store = initialize(
            base / "state", root, {"identifiers": [{"namespace": "benchmark", "pattern": "COMMON-KEY"}]}
        )
        start = time.perf_counter()
        snapshot = ingest(store)
        seconds = time.perf_counter() - start
        feature = store.one("SELECT * FROM features WHERE kind='identifier'")["id"]
        memberships = store.db.execute(
            "SELECT count(*) FROM occurrences WHERE feature_id=?", (feature,)
        ).fetchone()[0]
        links = store.db.execute("SELECT count(*) FROM explicit_links").fetchone()[0]
        assert memberships == count and links == 0
        all_postings = store.db.execute("SELECT count(*) FROM occurrences").fetchone()[0]
        api = API(store)
        timings = []
        for _ in range(3):
            start = time.perf_counter()
            result = api.neighbours(snapshot, feature, limit=100)
            timings.append(time.perf_counter() - start)
            assert result["total"] == count and result["next_cursor"]
        store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        database_bytes = (store.state / "evidence.sqlite3").stat().st_size
        artifact_bytes = sum(p.stat().st_size for p in (store.state / "objects").rglob("*") if p.is_file())
        posting_pages = store.db.execute(
            "SELECT coalesce(sum(pgsize),0) FROM dbstat WHERE name IN ('occurrences','occurrence_feature','occurrence_segment','sqlite_autoindex_occurrences_1')"
        ).fetchone()[0]
        store.close()
        return dict(
            scope="synthetic local TXT high-frequency-key stress; no model calls",
            source_files=count,
            source_bytes=source_bytes,
            ingestion_seconds=seconds,
            files_per_second=count / seconds,
            common_key_memberships=memberships,
            explicit_pair_edges=links,
            total_postings=all_postings,
            database_bytes=database_bytes,
            artifact_bytes=artifact_bytes,
            bytes_per_posting_including_occurrence_indexes=posting_pages / all_postings,
            storage_amplification=(database_bytes + artifact_bytes) / source_bytes,
            neighbour_page_100_seconds=timings,
            neighbour_page_100_median_seconds=statistics.median(timings),
            limitations="Small synthetic files amplify per-document metadata. Warm-cache local timings; not production, semantic quality or million-document capacity.",
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=20000)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = measure(args.count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
