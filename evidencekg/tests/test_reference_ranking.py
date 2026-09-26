"""Full-dictionary OLD execution equivalence on synthetic sources only."""

import random
from collections import Counter
from email.message import EmailMessage

import pytest

from evidencekg import benchmark, reference_ranking
from evidencekg.benchmark import retrieve
from evidencekg.config import configure
from evidencekg.db import dump
from evidencekg.ingest import ingest
from evidencekg.reference_ranking import ReferenceRanking, _Reasons
from evidencekg.retrieval import API


class SmallPages(API):
    page_size = 100

    def page(self, snapshot, scope, rows, cursor=None, limit=100, key=lambda x: x["id"]):
        return super().page(snapshot, scope, rows, cursor, min(limit, self.page_size), key)


def assert_equivalent(store, snapshot, questions, limits=(1, 6, 1000), page_size=100):
    adapter = ReferenceRanking(store, snapshot)
    native = SmallPages(store)
    native.page_size = page_size
    for i, question in enumerate(questions):
        for arm in ("baseline", "graph"):
            limit = limits[i % len(limits)]
            expected = retrieve(native, snapshot, question, arm, limit=limit)
            actual = adapter.retrieve(question, arm, limit=limit)
            assert actual == expected, (question, arm, page_size)
            assert dump(actual) == dump(expected)
    return adapter


@pytest.mark.parametrize("seed", range(16))
def test_seeded_full_dictionary_equivalence(vault, seed):
    root, store = vault
    rng = random.Random(seed)
    configure(
        store,
        {
            "segment_chars": rng.choice([32, 91, 6000]),
            "overlap_chars": 0,
            "names": ["Li", "Jo", "Alex Smith", "Éva"],
            "terms": ["not", "only", "unless"],
        },
    )
    phrases = [
        "Case: 123",
        "Case: 789",
        "Li and Jo",
        "Alex Smith",
        "Éva",
        "2026-09-25",
        "01/02/2026",
        "only unless not agreed",
        "CAFÉ café",
        "[[0.txt]] [[missing.txt]]",
        "violet indigo",
        "violet not indigo",
        "A repeated passage preserves negation and precise punctuation.",
    ]
    for i in range(rng.randint(5, 9)):
        text = "\n\n".join(rng.choices(phrases, k=rng.randint(3, 8)))
        (root / f"{i}.txt").write_text(f"marker{i}\n\n{text}\n\n{text}")
    (root / "copy.txt").write_bytes((root / "0.txt").read_bytes())
    if seed % 4 == 0:
        target = EmailMessage()
        target["Message-ID"] = "<target@example.org>"
        target.set_content("violet Case: 123 Li")
        (root / "target.eml").write_bytes(target.as_bytes())
        reply = EmailMessage()
        reply["Message-ID"] = "<reply@example.org>"
        reply["In-Reply-To"] = "<target@example.org>"
        reply["References"] = "<missing@example.org> <target@example.org>"
        reply.set_content("not agreed [[0.txt]]")
        nested = EmailMessage()
        nested.set_content("violet only unless Case: 123")
        nested.add_attachment(b"Li Case: 123", maintype="text", subtype="plain", filename="nested.txt")
        reply.add_attachment(nested)
        (root / "reply.eml").write_bytes(reply.as_bytes())
        if seed % 8 == 0:
            (root / "ambiguous.eml").write_bytes(target.as_bytes())
    snapshot = ingest(store)
    assert_equivalent(
        store,
        snapshot,
        [
            "marker0",
            "violet not only unless Li 123",
            "CAFÉ Éva 2026",
            "nohitsxyz",
            "the und oder",
            " ".join(rng.sample(["marker1", "indigo", "Smith", "agreed", "789", "Jo"], 4)),
        ],
        page_size=[1, 7, 100][seed % 3],
    )


def test_exact_unique_counts_duplicate_sources_and_self_links(vault):
    root, store = vault
    for i in range(12):
        (root / f"{i}.txt").write_text(f"violet Case: 123 Case: 123 marker{i}")
    snapshot = ingest(store)
    docs = store.manifest(snapshot)["documents"]
    segments = [API(store).sections(snapshot, d["document_version_id"])["items"][0] for d in docs[:2]]
    with store.write():
        for i, target in enumerate([docs[1]["document_version_id"], docs[0]["document_version_id"]]):
            lid = f"Lsynthetic{i}"
            store.db.execute(
                "INSERT INTO explicit_links VALUES(?,?,?,?,?,?,?,?)",
                (
                    lid,
                    docs[0]["document_version_id"],
                    "EXPLICIT_DOCUMENT_REFERENCE",
                    target,
                    "resolved",
                    dump({"qualifier": "only if not superseded", "unicode": "Éva"}),
                    "test",
                    "test-v1",
                ),
            )
            store.db.execute("INSERT INTO snapshot_links VALUES(?,?)", (snapshot, lid))
            for segment in segments:
                for start in range(3):
                    store.db.execute(
                        "INSERT INTO link_evidence VALUES(?,?,?,?)", (lid, segment["id"], start, start + 1)
                    )
    adapter = assert_equivalent(
        store, snapshot, ["violet", "marker0", "marker1"], limits=(1000,), page_size=3
    )
    result = adapter.retrieve("violet", "graph", 1000)
    assert any(r["type"] == "additional_reasons_omitted" for why in result["reasons"].values() for r in why)
    expected = retrieve(API(store), snapshot, "marker0", "graph", limit=1000)
    assert any(
        len(r.get("detail", {}).get("sources", [])) == 6 for why in expected["reasons"].values() for r in why
    )


def test_verifies_every_posting_exactly_once_and_reuses_snapshot(vault, monkeypatch):
    root, store = vault
    for i in range(5):
        (root / f"{i}.txt").write_text(f"violet Li Case: 123 Case: 123 marker{i}")
    snapshot = ingest(store)
    seen = Counter()
    original = reference_ranking.verify_posting

    def verify(store, row, cache=None):
        seen[row["id"]] += 1
        return original(store, row, cache)

    monkeypatch.setattr(reference_ranking, "verify_posting", verify)
    adapter = ReferenceRanking(store, snapshot)
    ids = {
        r["occurrence_id"]
        for r in store.rows("SELECT occurrence_id FROM snapshot_occurrences WHERE snapshot_id=?", (snapshot,))
    }
    assert seen == Counter({oid: 1 for oid in ids})
    for question in ["marker0", "violet", "Case 123", "missing"]:
        assert adapter.retrieve(question, "graph") == retrieve(API(store), snapshot, question, "graph")
    assert seen == Counter({oid: 1 for oid in ids})
    assert adapter.execution_identity["verified_postings"] == len(ids)
    assert adapter.execution_identity["enhanced_run_id"] is None
    returned = adapter.retrieve("violet", "graph")
    returned["segments"][0]["text"] = "caller mutation"
    returned["reasons"].clear()
    assert adapter.retrieve("violet", "graph") == retrieve(API(store), snapshot, "violet", "graph")
    (root / "0.txt").write_text("replacement marker0")
    newer = ingest(store)
    assert newer != snapshot
    assert adapter.retrieve("violet", "graph") == retrieve(API(store), snapshot, "violet", "graph")
    assert_equivalent(store, newer, ["violet", "replacement", "marker0"])


def test_seed_membership_is_not_restricted_to_snapshot_postings(vault):
    root, store = vault
    (root / "seed.txt").write_text("violet Case: 123")
    (root / "target.txt").write_text("Case: 123")
    snapshot = ingest(store)
    seed = next(d for d in store.manifest(snapshot)["documents"] if d["path"] == "seed.txt")
    # Construct a synthetic frozen membership subset before creating the index.
    with store.write():
        store.db.execute(
            """DELETE FROM snapshot_occurrences WHERE snapshot_id=? AND occurrence_id IN
            (SELECT o.id FROM occurrences o JOIN segments s ON s.id=o.segment_id
             JOIN extractions e ON e.id=s.extraction_id WHERE e.document_version_id=?)""",
            (snapshot, seed["document_version_id"]),
        )
    adapter = assert_equivalent(store, snapshot, ["violet"], limits=(1000,))
    assert adapter.retrieve("violet", "graph")["candidate_counts"]["mechanical"] == 1


def test_reason_top_eight_matches_full_canonical_sort():
    for seed in range(30):
        rng = random.Random(seed)
        values = [{"type": "lexical_bm25", "term": f"t{i}", "score": -i / 3} for i in range(seed % 12)]
        values += [
            {
                "type": "mechanical_neighbour",
                "seed_document": f"D{i}",
                "detail": {
                    "id": f"O{rng.randrange(9)}",
                    "sources": [{"raw_value": rng.choice(["ü", '"', "\\", "Li"])}],
                },
            }
            for i in range(seed * 3)
        ]
        rng.shuffle(values)
        compact = _Reasons()
        for value in values:
            compact.add(dump(value), lexical=value["type"] == "lexical_bm25")
            assert len(compact.top) <= 8
        expected = sorted(
            {dump(v): v for v in values}.values(), key=lambda v: (v["type"] != "lexical_bm25", dump(v))
        )
        selected = expected[:8]
        if len(expected) > 8:
            selected.append({"type": "additional_reasons_omitted", "count": len(expected) - 8})
        assert compact.selected() == selected


def test_original_60000_byte_envelope_and_exact_boundary(vault, monkeypatch):
    root, store = vault
    for i in range(30):
        (root / f"{i}.txt").write_text(f"violet marker{i} " + "über " * 650)
    snapshot = ingest(store)
    adapter = assert_equivalent(store, snapshot, ["violet", "marker0"], limits=(1000,))
    result = adapter.retrieve("violet", "baseline", 1000)
    assert result["max_evidence_bytes"] == 60000
    assert 0 < result["selected_count"] < result["candidate_counts"]["union"]
    assert result["omitted_count"] == result["candidate_counts"]["union"] - result["selected_count"]
    one = adapter.retrieve("marker0", "baseline", 1)
    for budget in [one["evidence_bytes"], one["evidence_bytes"] - 1, 1]:
        monkeypatch.setattr(benchmark, "MAX_EVIDENCE_BYTES", budget)
        assert adapter.retrieve("marker0", "baseline", 1) == retrieve(
            API(store), snapshot, "marker0", "baseline", 1
        )


def test_lexical_more_than_one_hundred_hits_and_document_diversity(vault):
    root, store = vault
    configure(store, {"segment_chars": 32, "overlap_chars": 0})
    (root / "large.txt").write_text(("violet " + "x" * 24 + "\n") * 130)
    (root / "small.txt").write_text("violet")
    snapshot = ingest(store)
    adapter = assert_equivalent(store, snapshot, ["violet"], limits=(2,))
    result = adapter.retrieve("violet", "baseline", 2)
    assert result["candidate_counts"]["lexical"] == 131
    assert len({s["document_version_id"] for s in result["segments"]}) == 2


@pytest.mark.parametrize(
    "question,arm,limit",
    [
        ("", "graph", 6),
        (" ", "graph", 6),
        (None, "graph", 6),
        ("a" * 4097, "graph", 6),
        ("word", "unknown", 6),
        ("word", "graph", True),
        ("word", "graph", 0),
        ("word", "graph", 1001),
    ],
)
def test_validation_matches_original(vault, question, arm, limit):
    root, store = vault
    (root / "a.txt").write_text("word")
    snapshot = ingest(store)
    adapter = ReferenceRanking(store, snapshot)
    with pytest.raises(ValueError) as native:
        retrieve(API(store), snapshot, question, arm, limit)
    with pytest.raises(ValueError) as indexed:
        adapter.retrieve(question, arm, limit)
    assert str(indexed.value) == str(native.value)


def test_corrupt_posting_fails_index_construction(vault):
    root, store = vault
    (root / "a.txt").write_text("violet Case: 123")
    snapshot = ingest(store)
    with store.write():
        store.db.execute(
            "UPDATE occurrences SET raw_value='tampered' WHERE id=(SELECT id FROM occurrences LIMIT 1)"
        )
    with pytest.raises(ValueError, match="Corrupt occurrence"):
        ReferenceRanking(store, snapshot)


def test_oversized_neighbour_row_fails_only_when_reached(vault):
    root, store = vault
    (root / "a.txt").write_text("violet")
    (root / "b.txt").write_text("indigo")
    snapshot = ingest(store)
    doc = next(d for d in store.manifest(snapshot)["documents"] if d["path"] == "a.txt")
    with store.write():
        store.db.execute(
            "INSERT INTO explicit_links VALUES(?,?,?,?,?,?,?,?)",
            (
                "Lhuge",
                doc["document_version_id"],
                "EXPLICIT_DOCUMENT_REFERENCE",
                None,
                "unresolved",
                dump({"qualifier": "ü" * 375000}),
                "test",
                "test-v1",
            ),
        )
        store.db.execute("INSERT INTO snapshot_links VALUES(?,?)", (snapshot, "Lhuge"))
    adapter = assert_equivalent(store, snapshot, ["indigo", "nomatch"])
    assert adapter.retrieve("violet", "baseline") == retrieve(API(store), snapshot, "violet", "baseline")
    for call in [
        lambda: adapter.retrieve("violet", "graph"),
        lambda: retrieve(API(store), snapshot, "violet", "graph"),
    ]:
        with pytest.raises(ValueError, match="response budget"):
            call()


def test_neighbour_page_byte_budget_preserves_complete_candidate_universe(vault):
    root, store = vault
    (root / "seed.txt").write_text("violet")
    (root / "target.txt").write_text("indigo")
    snapshot = ingest(store)
    docs = {d["path"]: d for d in store.manifest(snapshot)["documents"]}
    target = API(store).sections(snapshot, docs["target.txt"]["document_version_id"])["items"][0]
    with store.write():
        for i in range(4):
            lid = f"Llarge{i}"
            store.db.execute(
                "INSERT INTO explicit_links VALUES(?,?,?,?,?,?,?,?)",
                (
                    lid,
                    docs["seed.txt"]["document_version_id"],
                    "EXPLICIT_DOCUMENT_REFERENCE",
                    docs["target.txt"]["document_version_id"],
                    "resolved",
                    dump({"qualifier": "q" * 240000}),
                    "test",
                    "test-v1",
                ),
            )
            store.db.execute("INSERT INTO snapshot_links VALUES(?,?)", (snapshot, lid))
            store.db.execute("INSERT INTO link_evidence VALUES(?,?,?,?)", (lid, target["id"], 0, 1))
    native = API(store)
    first = native.neighbours(snapshot, docs["seed.txt"]["document_version_id"], limit=100)
    assert len(first["items"]) == 3 and first["next_cursor"] is not None
    adapter = assert_equivalent(store, snapshot, ["violet", "indigo"], limits=(1000,))
    result = adapter.retrieve("violet", "graph", 1000)
    assert result["candidate_counts"]["union"] == 2
    assert result["selected_count"] == 1 and result["omitted_count"] == 1


def test_metadata_only_attachment_does_not_add_endpoint_passages(vault):
    root, store = vault
    email = EmailMessage()
    email.set_content("violet")
    email.add_attachment(b"indigo", maintype="text", subtype="plain", filename="child.txt")
    (root / "email.eml").write_bytes(email.as_bytes())
    snapshot = ingest(store)
    adapter = assert_equivalent(store, snapshot, ["violet", "indigo"], limits=(1000,))
    result = adapter.retrieve("violet", "graph", 1000)
    assert not any(s["text"] == "indigo" for s in result["segments"])
    links = store.rows("SELECT * FROM explicit_links WHERE relation_type='ATTACHMENT_OF'")
    assert links and not store.rows("SELECT * FROM link_evidence")


def test_original_formula_drift_fails_closed(vault, monkeypatch):
    root, store = vault
    (root / "a.txt").write_text("violet")
    snapshot = ingest(store)
    monkeypatch.setattr(reference_ranking, "_ORIGINAL_RETRIEVE_SHA", "different source")
    with pytest.raises(ValueError, match="re-prove"):
        ReferenceRanking(store, snapshot)
