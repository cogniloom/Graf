"""Real CLI and official SDK discovery access; no model calls."""
import json
import sys

import pytest

from evidencekg.cli import main
from evidencekg.ingest import ingest
from evidencekg.reports import verify


def test_discover_cli_reads_originals(vault, capsys):
    root, store = vault
    (root / "letter.txt").write_text("The agreement applies only after written consent. Case: 123")
    snapshot = ingest(store)
    assert main(["--state", str(store.state), "discover", "--backend", "mechanical", "written consent", "--snapshot", snapshot]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["snapshot_id"] == snapshot
    assert "only after written consent" in result["segments"][0]["text"]
    assert result["selected_count"] == len(result["segments"])
    assert verify(store)["ok"]


@pytest.mark.asyncio
async def test_discover_official_sdk_read_only(vault):
    from mcp import Client, StdioServerParameters

    root, store = vault
    (root / "letter.txt").write_text("A signed amendment is required. Case: 123")
    snapshot = ingest(store)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "evidencekg.cli", "--state", str(store.state), "serve", "--backend", "mechanical"],
    )
    async with Client(params, read_timeout_seconds=20) as client:
        listing = await client.list_tools()
        tool = next(t for t in listing.tools if t.name == "discover")
        assert tool.annotations.read_only_hint
        result = await client.call_tool("discover", {
            "snapshot_id": snapshot, "question": "signed amendment", "limit": 3,
        })
        assert not result.is_error
        assert result.structured_content["segments"][0]["text"].startswith("A signed amendment")
        sid = result.structured_content["segments"][0]["id"]
        locations = await client.call_tool("segment_locators", {
            "snapshot_id": snapshot, "segment_id": sid, "limit": 1,
        })
        assert not locations.is_error
        assert locations.structured_content["items"][0]["ordinal"] == 0
        assert "location" in locations.structured_content["items"][0]
        invalid = await client.call_tool("discover", {
            "snapshot_id": "fabricated", "question": "signed amendment", "limit": 3,
        })
        assert invalid.is_error
    assert verify(store)["ok"]


def test_duplicate_aliases_cli_complete_pagination(vault, capsys):
    from evidencekg.retrieval import API

    root, store = vault
    for i in range(13):
        (root / f"copy-{i}.txt").write_text("The allegation is not established.")
    snapshot = ingest(store)
    sid = API(store).search(snapshot, "allegation", "lexical")["items"][0]["id"]
    seen, cursor = set(), None
    while True:
        argv = ["--state", str(store.state), "duplicate-aliases", sid,
                "--snapshot", snapshot, "--limit", "3" if not seen else "5"]
        if cursor:
            argv += ["--cursor", cursor]
        assert main(argv) == 0
        result = json.loads(capsys.readouterr().out)
        ids = {row["segment_id"] for row in result["items"]}
        assert not ids & seen
        seen |= ids
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 13


def test_bilingual_index_to_cli_original_citations(vault, tmp_path, capsys):
    from test_discovery_index import FakeWorker

    from evidencekg.discovery_index import build_index, freeze_index

    root, store = vault
    original = "Eine Frist wurde nicht vereinbart."
    (root / "brief.txt").write_text(original)
    snapshot = ingest(store)
    output = tmp_path / "annotations"
    index = freeze_index(store, snapshot, output, FakeWorker(), partition_count=1)
    assert build_index(index)["complete"]
    assert main(["--state", str(store.state), "discover", "--backend", "mechanical", "deadline", "--snapshot", snapshot,
                 "--discovery-index", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["segments"][0]["text"] == original
    assert result["candidate_counts"]["observations"] == 1
    (root / "later.txt").write_text("Unrelated newer evidence")
    newer = ingest(store)
    assert main(["--state", str(store.state), "discover", "--backend", "mechanical", "deadline", "--snapshot", newer,
                 "--discovery-index", str(output)]) == 1
    assert "different snapshot" in capsys.readouterr().err
