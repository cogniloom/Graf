"""Native graph parity for bounded discovery; ranking doubles isolate graph semantics."""

from types import SimpleNamespace

import pytest

from evidencekg.hybrid.graph import LadybugGraph, prepare_graph
from evidencekg.hybrid.retrieval import HybridDiscovery, Policy


class Ranking:
    def score(self, question, texts):
        return SimpleNamespace(scores=[1.0] * len(texts), windows=[[0]] * len(texts))


class Worksets:
    def freeze(self, identity, rows, accounting):
        self.rows = rows
        self.accounting = accounting
        return "retained-workset"


class Lexical:
    def search(self, *args):
        return {"items": []}


@pytest.mark.parametrize("depth,budget", [(0, 4), (1, 2), (2, 3), (4, 8)])
def test_native_bounded_discovery_matches_legacy(tmp_path, depth, budget):
    segments = {
        name: dict(
            id=name,
            document_version_id=name,
            ordinal=0,
            text="original " + name,
            char_start=0,
            char_end=len("original " + name),
            source_path=name + ".txt",
            locators=[{"line": 1}],
        )
        for name in "abcdef"
    }
    # Deliberately non-sorted edge IDs: order controls the document cap.
    links = [
        dict(
            id=f"link-{9 - i}",
            from_node=a,
            to_node=b,
            status=status,
            relation_type="REPLY_TO",
            rule_id="reply",
            rule_version="1",
            derivation_json='{"source":"original"}',
        )
        for i, (a, b, status) in enumerate(
            [
                ("a", "b", "resolved"),
                ("c", "a", "resolved"),
                ("a", "a", "resolved"),
                ("a", "b", "resolved"),
                ("b", "d", "resolved"),
                ("d", "e", "resolved"),
                ("e", "a", "resolved"),
                ("c", "missing", "unresolved"),
                ("f", "missing", "resolved"),
            ]
        )
    ]
    graph_config = prepare_graph(tmp_path / "graphs", "snapshot", "a" * 64, segments, links)
    graph = LadybugGraph(graph_config)
    outputs, ledgers = [], []
    try:
        for provider in (None, graph):
            ledger = Worksets()
            ranker = HybridDiscovery(
                None,
                "snapshot",
                lambda q: [("a", 1.0)],
                Ranking(),
                ledger,
                sources=({"hybrid_manifest_sha": "a" * 64}, segments, links),
                typed=[],
                lexical=Lexical(),
                model_identity={},
                policy=Policy(seed_documents=1, graph_depth=depth, graph_documents=budget),
                graph=provider,
            )
            if provider:
                assert not ranker.adjacency  # no full Python adjacency projection
            result = ranker.retrieve("unmatched", limit=6)
            result.pop("timing")
            outputs.append(result)
            ledgers.append(ledger)
        assert outputs[0] == outputs[1]
        assert ledgers[0].rows == ledgers[1].rows
        assert ledgers[0].accounting == ledgers[1].accounting
        assert outputs[1]["candidate_accounting"]["unresolved_links"] == ["link-2"]
    finally:
        graph.close()


def test_runtime_requires_graph_generation(tmp_path):
    from evidencekg.hybrid.runtime import Runtime, publish_config

    target = tmp_path / "hybrid.json"
    publish_config(target, {"version": 1})
    with pytest.raises(ValueError, match="prepare-hybrid"):
        Runtime(tmp_path)
    publish_config(
        target,
        {
            "version": 2,
            "snapshot_id": "a",
            "manifest_sha": "a" * 64,
            "graph": {"snapshot_id": "b", "manifest_sha": "a" * 64},
        },
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        Runtime(tmp_path)
