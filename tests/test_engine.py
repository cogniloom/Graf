"""Deterministic fake-worker tests; these are NOT live model/semantic proof."""
import copy
import itertools
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import lawcase_engine

import jsonschema

from lawcase_engine import Engine, EngineError, BudgetExceeded, STAGE_SCHEMAS, canonical, sha


def snapshot(root, documents):
    root.mkdir()
    manifest = {"documents": [], "units": []}
    for d, chunks in enumerate(documents):
        ident = f"doc-{d:04d}"
        text = "".join(chunks)
        name = ident + ".txt"
        (root / name).write_bytes(text.encode())
        manifest["documents"].append({"id": ident, "path": name, "source_sha256": sha(text),
            "text_sha256": sha(text), "text_file": name, "status": "ok", "error": None, "metadata": {}})
        start = 0
        for seq, chunk in enumerate(chunks):
            end = start + len(chunk)
            manifest["units"].append({"id": f"{ident}:{seq:04d}", "document_id": ident, "sequence": seq,
                "start": start, "end": end, "context_start": start, "context_end": end, "text_sha256": sha(chunk)})
            start = end
    (root / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def quote(unit):
    return {"unit_id": unit["id"], "start": unit["context_start"], "end": unit["context_end"], "text": unit["text"]}


class FakeWorker:
    """Predictable source-preserving accounting fixture, no inference capability."""
    def __init__(self, fail_at=None, mutate=None, followup=False, repeat_audit=False):
        self.calls = []
        self.fail_at = fail_at
        self.mutate = mutate
        self.followup = followup
        self.repeat_audit = repeat_audit
        self.audit_count = 0

    def call(self, stage, payload):
        self.calls.append((stage, copy.deepcopy(payload)))
        if self.fail_at == len(self.calls):
            raise KeyboardInterrupt("synthetic crash during external call")
        inputs = payload["inputs"]
        items = inputs.get("items", [])
        if stage in ("map", "reread"):
            u = inputs["units"][0]
            result = {"inspection_complete": True, "findings": [{"statement": u["text"], "epistemic": "source_statement", "quotes": [quote(u)],
                         "event_date": "", "document_date": "", "date_uncertainty": "Not established"}],
                      "issues": [], "blockers": []}
        elif stage == "discover":
            result = {"issues": [], "covered_ids": [x["id"] for x in items], "blockers": []}
        elif stage == "pair":
            left, right = inputs["units"]
            relation = None
            if left["text"] == right["text"]:
                relation = "duplicates"
            elif "amendment" in left["text"].lower() or "amendment" in right["text"].lower():
                relation = "amends"
            result = {"edges": [] if relation is None else [{"relation": relation, "explanation": relation,
                        "quotes": [quote(left), quote(right)], "independent_corroboration": False}], "issues": [], "blockers": []}
        elif stage in ("bundle", "synthesize"):
            quotes = {canonical(q): q for item in items for q in item["quotes"]}
            result = {"claims": [{"statement": "Source-linked synthetic fixture summary", "epistemic": "uncertain",
                         "basis_ids": [x["id"] for x in items], "quotes": list(quotes.values()), "authority_ids": [],
                         "event_date": "", "document_date": "", "date_uncertainty": "Not established"}],
                      "covered_ids": [x["id"] for x in items], "issues": [], "blockers": []}
        elif stage == "audit":
            self.audit_count += 1
            result = {"checked_ids": [x["id"] for x in items], "issues": [], "blockers": []}
            if self.followup and (self.audit_count == 1 or self.repeat_audit):
                result["issues"] = [{"question": "Does the amendment change the alleged event date?", "basis_ids": [items[0]["id"]]}]
        else:
            raise AssertionError(stage)
        if self.mutate:
            self.mutate(stage, result)
        return result


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def engine(self, docs=None, **config):
        snapshot(self.root / "snapshot", docs or [["Alice alleges an event on June 1.\n"], ["Document dated June 3.\n"]])
        engine = Engine(self.root / "state", self.root / "snapshot", "Investigate the record", {"evidence_only": True, **config})
        self.addCleanup(engine.db.close)
        return engine

    def test_strict_schemas_every_object_requires_every_field(self):
        def visit(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertFalse(value["additionalProperties"])
                    self.assertEqual(set(value["required"]), set(value["properties"]))
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)
        for schema in STAGE_SCHEMAS.values():
            jsonschema.Draft202012Validator.check_schema(schema)
            visit(schema)

    def test_amendment_duplicate_and_same_document_nonadjacent_pairs(self):
        engine = self.engine([["Original agreement.\n", "Allegation by Alice.\n", "Amendment replaces agreement.\n"], ["Original agreement.\n"]])
        worker = FakeWorker()
        result = engine.run(worker)
        self.assertTrue(result["accounting_complete"])
        self.assertFalse(result["semantic_completeness_guaranteed"])
        pairs = [tuple(u["id"] for u in p["inputs"]["units"]) for s, p in worker.calls if s == "pair"]
        self.assertEqual(set(pairs), set(itertools.combinations(sorted(engine.units), 2)))
        edges = [e for r in engine.db.execute("SELECT result FROM tasks WHERE stage='pair'") for e in json.loads(r[0])["edges"]]
        self.assertIn("amends", [e["relation"] for e in edges])
        self.assertIn("duplicates", [e["relation"] for e in edges])
        self.assertTrue(all(not e["independent_corroboration"] for e in edges))
        self.assertEqual(engine.verify(), [])
        report = json.loads(engine.report().read_text())
        self.assertTrue(report["claims"])
        self.assertTrue(all(c["quotes"] for c in report["claims"]))
        self.assertTrue(all("event_date" in c and "document_date" in c for c in report["claims"]))

    def test_over_1000_deterministic_tasks_and_idempotent_resume(self):
        # 46 source units require 1,035 unordered pair calls plus both maps.
        engine = self.engine([[f"Unique original passage {i}.\n"] for i in range(46)])
        worker = FakeWorker()
        result = engine.run(worker)
        self.assertEqual(result["stages"]["pair"]["succeeded"], 1035)
        self.assertGreater(len(worker.calls), 1100)
        self.assertEqual(len({p["task_id"] for _, p in worker.calls}), len(worker.calls))
        first = [tuple(r) for r in engine.db.execute("SELECT id,result_sha FROM tasks ORDER BY id")]
        resumed = Engine(engine.state)
        self.addCleanup(resumed.db.close)
        replay = FakeWorker()
        self.assertTrue(resumed.run(replay)["accounting_complete"])
        self.assertEqual(replay.calls, [])
        self.assertEqual(first, [tuple(r) for r in resumed.db.execute("SELECT id,result_sha FROM tasks ORDER BY id")])

    def test_1001_units_quadratic_count_and_explicit_session_budget(self):
        engine = self.engine([[f"Passage {i}\n"] for i in range(1001)], max_tasks=1)
        worker = FakeWorker()
        self.assertEqual(engine.status()["baseline_pairs_expected"], 500500)
        with self.assertRaisesRegex(BudgetExceeded, "Per-invocation"):
            engine.run(worker)
        self.assertEqual(len(worker.calls), 1)
        self.assertFalse(engine.status()["accounting_complete"])

    def test_crash_retries_only_uncommitted_task(self):
        engine = self.engine()
        crashed = FakeWorker(fail_at=2)
        with self.assertRaises(KeyboardInterrupt):
            engine.run(crashed)
        first_id = crashed.calls[0][1]["task_id"]
        failed_id = crashed.calls[1][1]["task_id"]
        resumed = Engine(engine.state)
        self.addCleanup(resumed.db.close)
        worker = FakeWorker()
        self.assertTrue(resumed.run(worker)["accounting_complete"])
        self.assertNotIn(first_id, [p["task_id"] for _, p in worker.calls])
        self.assertEqual(worker.calls[0][1]["task_id"], failed_id)
        statuses = [r[0] for r in resumed.db.execute("SELECT status FROM attempts WHERE task_id=? ORDER BY id", (failed_id,))]
        self.assertEqual(statuses, ["failed", "succeeded"])

    def test_bad_quote_is_persisted_failure_and_retryable(self):
        engine = self.engine()
        def bad(stage, result):
            if stage == "map":
                result["findings"][0]["quotes"][0]["text"] = "Invented quote"
        with self.assertRaisesRegex(EngineError, "exact source span"):
            engine.run(FakeWorker(mutate=bad))
        self.assertEqual(engine.status()["stages"]["map"]["failed"], 1)
        self.assertTrue(engine.run(FakeWorker())["accounting_complete"])

    def test_unknown_authority_citation_rejected(self):
        engine = self.engine()
        def bad(stage, result):
            if stage == "bundle":
                result["claims"][0]["authority_ids"] = ["invented-case-law"]
        with self.assertRaisesRegex(EngineError, "Unknown legal authority"):
            engine.run(FakeWorker(mutate=bad))
        self.assertFalse(engine.status()["accounting_complete"])

    def test_unknown_evidence_citation_rejected(self):
        engine = self.engine()
        def bad(stage, result):
            if stage == "bundle":
                result["claims"][0]["basis_ids"] = ["not-an-evidence-id"]
        with self.assertRaisesRegex(EngineError, "basis citation"):
            engine.run(FakeWorker(mutate=bad))

    def test_attested_coverage_cannot_drop_a_finding(self):
        engine = self.engine()
        def bad(stage, result):
            if stage == "bundle":
                result["claims"][0]["basis_ids"].pop()
        with self.assertRaisesRegex(EngineError, "preserve every input|omitted an original"):
            engine.run(FakeWorker(mutate=bad))

    def test_duplicate_cannot_corroborate(self):
        engine = self.engine([["Same statement"], ["Same statement"]])
        def bad(stage, result):
            if stage == "pair":
                result["edges"][0]["independent_corroboration"] = True
        with self.assertRaisesRegex(EngineError, "not independent"):
            engine.run(FakeWorker(mutate=bad))

    def test_followup_audit_persists_and_rereads_every_unit_and_pair(self):
        engine = self.engine()
        worker = FakeWorker(followup=True)
        result = engine.run(worker)
        self.assertTrue(result["accounting_complete"])
        self.assertEqual(result["rounds_finished"], 2)
        self.assertEqual(result["stages"]["reread"]["succeeded"], 4)
        self.assertEqual(result["stages"]["pair"]["succeeded"], 2)
        self.assertTrue(engine.db.execute("SELECT 1 FROM investigation_issues WHERE normalized LIKE ?", ("%amendment%",)).fetchone())

    def test_repeated_audit_issue_is_unresolved_not_silently_dropped(self):
        engine = self.engine()
        result = engine.run(FakeWorker(followup=True, repeat_audit=True))
        self.assertFalse(result["overall_complete"])
        self.assertIn("unverified", " ".join(result["blockers"]))

    def test_followup_budget_is_explicit_incomplete(self):
        engine = self.engine(max_rounds=1)
        result = engine.run(FakeWorker(followup=True))
        self.assertFalse(result["overall_complete"])
        self.assertTrue(result["unresolved_issues"])
        self.assertIn("max_rounds", " ".join(result["blockers"]))

    def test_question_config_and_snapshot_immutable(self):
        engine = self.engine()
        with self.assertRaisesRegex(EngineError, "question"):
            Engine(engine.state, question="different")
        with self.assertRaisesRegex(EngineError, "config"):
            Engine(engine.state, config={"evidence_only": True, "max_tasks": 99})
        (engine.snapshot_dir / "manifest.json").write_text("{}")
        self.assertIn("Snapshot manifest changed", engine.verify())

    def test_content_hash_and_quote_offset_are_exact_with_crlf_unicode(self):
        engine = self.engine([["Älice alleges\r\nan event.\r\n"]])
        self.assertTrue(engine.run(FakeWorker())["accounting_complete"])
        (engine.snapshot_dir / "doc-0000.txt").write_text("Changed")
        self.assertTrue(any("hash mismatch" in e for e in engine.verify()))

    def test_source_gap_prevents_complete(self):
        engine = self.engine()
        # Add an explicit inaccessible source before creating the actual run.
        manifest = engine.manifest
        manifest["documents"].append({"id": "missing", "path": "unreadable.pdf", "status": "failed", "error": "unreadable", "metadata": {}})
        (engine.snapshot_dir / "manifest.json").write_text(json.dumps(manifest))
        gap_engine = Engine(self.root / "gap-state", engine.snapshot_dir, engine.question, {"evidence_only": True})
        self.addCleanup(gap_engine.db.close)
        result = gap_engine.run(FakeWorker())
        self.assertFalse(result["overall_complete"])
        self.assertEqual(result["source_gaps"], ["missing"])

    def test_single_writer_lock(self):
        engine = self.engine()
        with engine._lock():
            with self.assertRaisesRegex(EngineError, "writer"):
                engine.run(FakeWorker())

    def test_payload_budget_fails_without_truncation(self):
        engine = self.engine([["x" * 6000]], max_payload_bytes=5000)
        worker = FakeWorker()
        with self.assertRaisesRegex(BudgetExceeded, "no content truncated"):
            engine.run(worker)
        self.assertEqual(worker.calls, [])

    def test_corrupted_result_hash_and_deleted_task_detected(self):
        engine = self.engine()
        engine.run(FakeWorker())
        task = engine.db.execute("SELECT id FROM tasks LIMIT 1").fetchone()[0]
        engine.db.execute("UPDATE tasks SET result_sha='broken' WHERE id=?", (task,))
        engine.db.commit()
        self.assertTrue(any("Result hash mismatch" in e for e in engine.verify()))
        engine.db.execute("DELETE FROM attempts WHERE task_id=?", (task,))
        engine.db.execute("DELETE FROM tasks WHERE id=?", (task,))
        engine.db.commit()
        self.assertIn("Completed run task coverage mismatch", engine.verify())

    def test_session_budget_resume_makes_progress_with_immutable_config(self):
        engine = self.engine(max_tasks=2)
        for _ in range(20):
            try:
                result = engine.run(FakeWorker())
                break
            except BudgetExceeded:
                self.assertEqual(engine.status()["status"], "paused")
        else:
            self.fail("Repeated resume failed to make progress")
        self.assertTrue(result["accounting_complete"])
        self.assertEqual(engine.config["max_tasks"], 2)

    def test_status_open_during_writer_lock(self):
        engine = self.engine()
        with engine._lock():
            reader = Engine(engine.state)
            try:
                self.assertEqual(reader.status()["status"], "pending")
            finally:
                reader.close()

    def test_synthesis_always_receives_original_passages(self):
        engine = self.engine([[f"Source {i} original words"] for i in range(40)])
        worker = FakeWorker()
        engine.run(worker)
        calls = [p for s, p in worker.calls if s == "synthesize"]
        self.assertGreater(len(calls), 1)
        for payload in calls:
            self.assertTrue(all(item["passages"] for item in payload["inputs"]["items"]))
            self.assertLessEqual(len(canonical(payload).encode()), engine.config["max_payload_bytes"])

    def test_snapshot_provenance_flags_block_complete(self):
        engine = self.engine()
        manifest = copy.deepcopy(engine.manifest)
        manifest.update(source_index_provenance_verified=False, ingestion_frozen=False)
        (engine.snapshot_dir / "manifest.json").write_text(json.dumps(manifest))
        unverified = Engine(self.root / "unverified", engine.snapshot_dir, engine.question, {"evidence_only": True})
        self.addCleanup(unverified.close)
        result = unverified.run(FakeWorker())
        self.assertFalse(result["overall_complete"])
        self.assertIn("manifest:source_index_provenance_verified=false", result["source_gaps"])

    def test_accepted_artifact_tampering_fails_status_closed(self):
        engine = self.engine()
        engine.run(FakeWorker())
        engine.db.execute("UPDATE artifacts SET payload='{}' WHERE kind='findings'")
        engine.db.commit()
        self.assertFalse(engine.status()["accounting_complete"])
        self.assertTrue(any("artifact" in e for e in engine.verify()))

    def test_runner_pause_is_not_retried_or_marked_complete(self):
        engine = self.engine()
        class Pause(Exception):
            pass
        class PausedWorker:
            def call(self, stage, payload):
                raise Pause("Subscription quota paused")
        with self.assertRaises(Pause):
            engine.run(PausedWorker())
        self.assertEqual(engine.status()["status"], "paused")
        self.assertEqual(engine.status()["attempts"], 1)
        self.assertEqual(engine.status()["stages"]["map"]["paused"], 1)

    def test_supplied_issue_id_can_support_new_question_but_not_claim(self):
        engine = self.engine([["Original agreement: pickup 17:00."], ["Amendment: pickup 18:00."]])
        class ContextWorker(FakeWorker):
            raised = False
            def call(self, stage, payload):
                value = super().call(stage, payload)
                if stage == "reread" and not self.raised:
                    self.raised = True
                    value["issues"] = [{"question": "Does the amendment explicitly replace the original term?",
                        "basis_ids": [payload["inputs"]["issue"]["id"], payload["inputs"]["units"][0]["id"]]}]
                return value
        worker = ContextWorker()
        status = engine.run(worker)
        self.assertTrue(status["accounting_complete"])
        self.assertEqual(status["rounds_finished"], 2)
        self.assertEqual(status["all_rounds_reread_expected"], 4)
        self.assertEqual(status["blockers"], [])
        unit = engine._passage(next(iter(engine.units)))
        payload = engine._payload("bundle", {"items": [{"id": "finding", "quotes": [quote(unit)]}],
            "issue": {"id": "context-only", "question": "Question"}}, "test")
        value = FakeWorker().call("bundle", payload)
        value["claims"][0]["basis_ids"] = ["context-only"]
        with self.assertRaisesRegex(EngineError, "basis citation"):
            engine._validate("bundle", payload, value)

    def test_unsupplied_issue_id_still_rejected(self):
        engine = self.engine()
        def bad(stage, value):
            if stage == "reread":
                value["issues"] = [{"question": "New question", "basis_ids": ["unknown-other-issue"]}]
        with self.assertRaisesRegex(EngineError, "basis citation"):
            engine.run(FakeWorker(mutate=bad))

    def test_local_context_absence_routes_to_issue_and_all_sources_are_checked(self):
        engine = self.engine([["Original agreement: 17:00"], ["Amendment: 18:00"]])
        class ScopedWorker(FakeWorker):
            def call(self, stage, payload):
                value = super().call(stage, payload)
                if stage in ("map", "reread"):
                    assert "not a whole-corpus comparison" in payload["instructions"]
                    assert "Never block because another document" in payload["instructions"]
                if stage == "map" and "Amendment" in payload["inputs"]["units"][0]["text"]:
                    value["issues"] = [{"question": "What original term does this amendment replace?",
                        "basis_ids": [payload["inputs"]["units"][0]["id"]]}]
                return value
        worker = ScopedWorker()
        status = engine.run(worker)
        self.assertTrue(status["accounting_complete"])
        self.assertEqual(status["rounds_finished"], 1)
        self.assertEqual(status["stages"]["reread"]["succeeded"], 2)
        self.assertEqual(status["stages"]["pair"]["succeeded"], 1)
        self.assertEqual(status["blockers"], [])

    def test_real_assigned_unit_defect_remains_blocker(self):
        engine = self.engine()
        class DefectWorker(FakeWorker):
            def call(self, stage, payload):
                value = super().call(stage, payload)
                if stage == "map":
                    value["blockers"] = [{"scope": "assigned_unit", "unit_ids": [payload["inputs"]["units"][0]["id"]],
                        "reason": "The supplied text contains an illegible operative clause."}]
                return value
        status = engine.run(DefectWorker())
        self.assertFalse(status["overall_complete"])
        self.assertTrue(all("Assigned-unit inspection defect" in b for b in status["blockers"]))

    def test_local_blocker_requires_assigned_unit_and_structured_scope(self):
        engine = self.engine()
        uid = next(iter(engine.units))
        payload = engine._payload("map", {"units": [engine._passage(uid)]}, "test")
        for blocker in ("Only amendment supplied; original agreement required", {
            "scope": "assigned_unit", "unit_ids": ["other-document"], "reason": "Missing"}):
            value = FakeWorker().call("map", payload)
            value["blockers"] = [blocker]
            with self.assertRaises(EngineError):
                engine._validate("map", payload, value)

    def test_old_contract_rejected_without_altering_preserved_evidence(self):
        engine = self.engine()
        engine.run(FakeWorker())
        old_identity = copy.deepcopy(engine.identity)
        old_identity.pop("contract")
        old_identity["version"] = 1
        engine._set("identity", old_identity)
        engine._set("identity_sha", sha(canonical(old_identity)))
        engine.db.commit()
        before = list(engine.db.iterdump())
        with self.assertRaisesRegex(EngineError, "new run is required"):
            Engine(engine.state)
        self.assertEqual(list(engine.db.iterdump()), before)

    def test_prompt_edit_without_manual_version_bump_is_incompatible(self):
        engine = self.engine()
        before = list(engine.db.iterdump())
        with mock.patch.dict(lawcase_engine.STAGE_INSTRUCTIONS, {"map": "changed instructions"}):
            with self.assertRaisesRegex(EngineError, "new run is required"):
                Engine(engine.state)
            with self.assertRaisesRegex(EngineError, "new run is required"):
                engine.run(FakeWorker())
        self.assertEqual(list(engine.db.iterdump()), before)

    def test_task_id_binds_question_snapshot_config_and_contract(self):
        engine = self.engine()
        other = Engine(self.root / "other-state", engine.snapshot_dir, "Different question", {"evidence_only": True})
        self.addCleanup(other.close)
        uid = next(iter(engine.units))
        inputs = {"units": [engine._passage(uid)]}
        original = engine._payload("map", inputs, ["map", uid])
        changed = other._payload("map", inputs, ["map", uid])
        self.assertNotEqual(original["task_id"], changed["task_id"])
        self.assertEqual(original["contract"], engine.identity["contract"])

    def test_synthesis_cannot_keep_basis_ids_while_dropping_original_quotes(self):
        engine = self.engine([["First source says 17:00."], ["Contrary source says 18:00."]])
        def bad(stage, value):
            if stage == "synthesize":
                value["claims"][0]["quotes"] = value["claims"][0]["quotes"][:1]
        with self.assertRaisesRegex(EngineError, "synthesize omitted an original"):
            engine.run(FakeWorker(mutate=bad))
        self.assertFalse(engine.status()["overall_complete"])
        self.assertEqual(engine.status()["stage_coverage"]["synthesize"]["failed"], 1)

    def test_large_original_passages_finish_as_audited_multipart(self):
        engine = self.engine([[f"{i:02d}" + "x" * 7000] for i in range(12)])
        worker = FakeWorker()
        status = engine.run(worker)
        self.assertTrue(status["overall_complete"])
        self.assertTrue(status["accounting_complete"])
        self.assertTrue(status["synthesis_multipart"])
        self.assertIn("no single-context", status["synthesis_limitation"])
        terminal = list(engine._artifacts("claims", group=engine._get("final_group")))
        terminal_ids = {item["id"] for item in terminal}
        audited_ids = {item["id"] for stage, payload in worker.calls if stage == "audit" for item in payload["inputs"]["items"]}
        self.assertEqual(terminal_ids, audited_ids)
        self.assertEqual({q["unit_id"] for item in terminal for q in item["quotes"]}, set(engine.units))
        self.assertTrue(all(len(canonical(p).encode()) <= engine.config["max_payload_bytes"] for _, p in worker.calls))
        self.assertEqual(engine.verify(), [])
        replay = FakeWorker()
        self.assertTrue(engine.run(replay)["overall_complete"])
        self.assertEqual(replay.calls, [])

    def test_mechanical_accounting_separate_from_source_confidence(self):
        engine = self.engine()
        manifest = copy.deepcopy(engine.manifest)
        manifest["source_index_provenance_verified"] = False
        (engine.snapshot_dir / "manifest.json").write_text(json.dumps(manifest))
        unverified = Engine(self.root / "coverage", engine.snapshot_dir, engine.question, {"evidence_only": True})
        self.addCleanup(unverified.close)
        status = unverified.run(FakeWorker())
        self.assertTrue(status["accounting_complete"])
        self.assertTrue(status["successful_analysis_complete"])
        self.assertFalse(status["source_confidence_complete"])
        self.assertFalse(status["overall_complete"])
        self.assertEqual(status["status"], "incomplete")
        for values in status["stage_coverage"].values():
            self.assertEqual(values["accounting_fraction"], 1.0)
            self.assertEqual(values["successful_analysis_fraction"], 1.0)
            self.assertEqual(values["unscheduled"], 0)

    def test_failed_and_unscheduled_accounting_do_not_look_successful(self):
        engine = self.engine()
        def bad(stage, value):
            if stage == "map":
                value["findings"][0]["quotes"][0]["text"] = "Wrong"
        with self.assertRaises(EngineError):
            engine.run(FakeWorker(mutate=bad))
        status = engine.status()
        mapped = status["stage_coverage"]["map"]
        self.assertEqual(mapped["expected"], 2)
        self.assertEqual(mapped["accounted"], 1)
        self.assertEqual(mapped["failed"], 1)
        self.assertEqual(mapped["unscheduled"], 1)
        self.assertEqual(mapped["accounting_fraction"], 0.5)
        self.assertEqual(mapped["successful_analysis_fraction"], 0.0)
        self.assertFalse(status["stage_coverage"]["pair"]["denominator_final"])

    def test_running_and_paused_calls_are_not_accounting_completion(self):
        engine = self.engine()
        class PausedWorker:
            def call(_, stage, payload):
                running = Engine.open_readonly(engine.state)
                try:
                    mapped = running.status()["stage_coverage"]["map"]
                    self.assertEqual(mapped["running"], 1)
                    self.assertEqual(mapped["accounted"], 0)
                    self.assertEqual(mapped["accounting_fraction"], 0.0)
                finally:
                    running.close()
                from corpus import Pause
                raise Pause("Synthetic pause")
        from corpus import Pause
        with self.assertRaises(Pause):
            engine.run(PausedWorker())
        mapped = engine.status()["stage_coverage"]["map"]
        self.assertEqual(mapped["paused"], 1)
        self.assertEqual(mapped["accounted"], 0)
        self.assertEqual(mapped["unaccounted"], 2)

    def test_6000_unit_root_scope_is_persisted_but_local_payload_is_compact(self):
        engine = self.engine([["x"] * 6000])
        issue = {"id": "root-question", "question": engine.question, "basis_ids": sorted(engine.units)}
        engine.blockers = []
        engine._collect({"issues": [issue]})
        stored = json.loads(engine.db.execute("SELECT payload FROM investigation_issues").fetchone()[0])
        self.assertEqual(len(stored["basis_ids"]), 6000)
        uid = next(iter(engine.units))
        inputs = {"units": [engine._passage(uid)], "issue": stored}
        self.assertGreater(len(canonical(engine._payload("reread", inputs, "test")).encode()), engine.config["max_payload_bytes"])
        inputs["issue"] = engine._local_issue(stored)
        payload = engine._payload("reread", inputs, "test")
        self.assertLess(len(canonical(payload).encode()), engine.config["max_payload_bytes"])
        self.assertEqual(payload["inputs"]["issue"], {"id": "root-question", "question": engine.question})

    def test_readonly_status_and_verify_in_subprocess_while_writer_locked(self):
        engine = self.engine()
        script = ("import sys; from lawcase_engine import Engine; "
                  "e=Engine.open_readonly(sys.argv[1]); "
                  "assert e.status()['status']=='pending'; assert e.verify()==[]; e.close()")
        with engine._lock():
            result = subprocess.run([sys.executable, "-c", script, str(engine.state)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            reader = Engine.open_readonly(engine.state)
            try:
                with self.assertRaisesRegex(EngineError, "Read-only"):
                    reader.run(FakeWorker())
                with self.assertRaisesRegex(EngineError, "Read-only"):
                    reader.report()
                with self.assertRaises(sqlite3.OperationalError):
                    reader.db.execute("DELETE FROM metadata")
            finally:
                reader.close()

    def test_readonly_queries_use_consistent_sqlite_snapshot(self):
        engine = self.engine()
        reader = Engine.open_readonly(engine.state)
        self.addCleanup(reader.close)
        with reader._read_snapshot():
            self.assertEqual(reader.status()["status"], "pending")
            engine._set("status", "running")
            engine.db.commit()
            self.assertEqual(reader.status()["status"], "pending")
        self.assertEqual(reader.status()["status"], "running")

    def test_unique_exact_quote_offset_is_repaired_with_raw_attempt_preserved(self):
        engine = self.engine([["padding " * 1200 + "Äunique phrase" + " tail" * 20]])
        class OffsetWorker(FakeWorker):
            def call(self, stage, payload):
                result = super().call(stage, payload)
                if stage == "map":
                    result["findings"][0]["quotes"][0].update(text="Äunique phrase", start=0, end=14)
                return result
        self.assertTrue(engine.run(OffsetWorker())["overall_complete"])
        accepted = json.loads(engine.db.execute("SELECT result FROM tasks WHERE stage='map'").fetchone()[0])
        actual = engine.texts["doc-0000"].index("Äunique phrase")
        self.assertEqual(accepted["findings"][0]["quotes"][0]["start"], actual)
        attempt = engine.db.execute("SELECT a.result,a.normalization FROM attempts a JOIN tasks t ON a.task_id=t.id WHERE t.stage='map'").fetchone()
        self.assertEqual(json.loads(attempt[0])["findings"][0]["quotes"][0]["start"], 0)
        self.assertEqual(json.loads(attempt[1])[0]["start"], actual)
        self.assertEqual(engine.verify(), [])

    def test_ambiguous_quote_requires_valid_explicit_offset(self):
        engine = self.engine([["Repeat Repeat"]])
        class QuoteWorker(FakeWorker):
            def __init__(self, correct):
                super().__init__()
                self.correct = correct
            def call(self, stage, payload):
                result = super().call(stage, payload)
                if stage == "map":
                    result["findings"][0]["quotes"][0].update(text="Repeat", start=7 if self.correct else 1, end=13 if self.correct else 7)
                return result
        with self.assertRaisesRegex(EngineError, "Ambiguous exact quote"):
            engine.run(QuoteWorker(False))
        self.assertTrue(engine.run(QuoteWorker(True))["overall_complete"])
        repair = engine.db.execute("SELECT a.normalization FROM attempts a JOIN tasks t ON a.task_id=t.id WHERE t.stage='map' AND a.status='succeeded'").fetchone()[0]
        self.assertEqual(json.loads(repair), [])

    def test_authorities_require_explicit_evidence_only_or_verification(self):
        snapshot(self.root / "snapshot", [["Original source"]])
        with self.assertRaisesRegex(EngineError, "explicitly"):
            Engine(self.root / "s1", self.root / "snapshot", "Question", {})
        with self.assertRaisesRegex(EngineError, "explicitly verified"):
            Engine(self.root / "s2", self.root / "snapshot", "Question", {"verified_authorities": [{"id": "fake"}]})


if __name__ == "__main__":
    unittest.main()
