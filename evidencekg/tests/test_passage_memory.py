"""Passage matching must not retain every complete shingle set."""

import json
import os
import subprocess
import sys

import pytest

from evidencekg import knowledge_links as links


def passage(key, text, document=None):
    return dict(id=key, category="dependence_passage", quote=text, document_version_id=document or key)


def test_exact_similarity_and_document_exclusion():
    words = [f"word{i}" for i in range(200)]
    original = " ".join(words)
    changed = " ".join(words[:100] + ["replacement"] + words[101:])
    rows = [passage("a", original), passage("b", changed), passage("c", original, "a")]
    nodes, edges, gaps = links.passage_dependence(rows)
    assert {tuple(n["member_ids"]) for n in nodes} == {("a", "b"), ("b", "c")}
    assert all(n["similarity"] == round(191 / 201, 6) for n in nodes)
    assert len(edges) == 4
    assert gaps == {}


def test_common_fingerprints_and_pair_limit_remain_visible(monkeypatch):
    text = " ".join(f"word{i}" for i in range(100))
    rows = [passage(str(i), text) for i in range(links.MAX_BLOCK + 1)]
    nodes, edges, gaps = links.passage_dependence(rows)
    assert nodes == edges == []
    assert gaps == {"dependence_common_fingerprint_skipped": 32}
    monkeypatch.setattr(links, "MAX_PAIRS", 2)
    nodes, _, gaps = links.passage_dependence(rows[:4])
    assert len(nodes) == 2
    assert gaps == {"dependence_pair_limit": 1}


@pytest.mark.skipif(sys.platform != "linux", reason="Linux address-space regression")
def test_library_shingles_fit_bounded_address_space():
    program = r"""
import json, resource
from evidencekg.knowledge_links import passage_dependence
resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, resource.RLIM_INFINITY))
rows = [dict(id=str(i), category="dependence_passage", document_version_id=str(i),
             quote=" ".join(f"w{i}_{j}" for j in range(500))) for i in range(5000)]
nodes, edges, gaps = passage_dependence(rows)
assert not nodes and not edges and not gaps
print(json.dumps({"passages": len(rows), "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passages"] == 5000
