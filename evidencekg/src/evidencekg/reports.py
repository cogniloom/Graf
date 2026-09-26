import csv
import io
import json
import shutil
import sqlite3
from pathlib import Path

from .db import Store, atomic, dump, ident, sha
from .review_queue import Queue


def verify(store):
    errors = []
    if store.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        errors.append("SQLite integrity check failed")
    errors += [str(tuple(r)) for r in store.db.execute("PRAGMA foreign_key_check")]
    for blob in store.rows("SELECT * FROM blobs"):
        try:
            data = store.get(blob["sha256"])
            if len(data) != blob["byte_length"]:
                errors.append("Blob length: " + blob["sha256"])
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    for ext in store.rows("SELECT * FROM extractions"):
        try:
            artifact = json.loads(store.get(ext["artifact_sha"]))
            segments = store.rows(
                "SELECT * FROM segments WHERE extraction_id=? ORDER BY ordinal", (ext["id"],)
            )
            position = 0
            for segment in segments:
                if segment["char_start"] != position or segment["char_end"] < position:
                    errors.append("Primary range partition: " + segment["id"])
                position = segment["char_end"]
                if (
                    segment["text"] != artifact["text"][segment["char_start"] : segment["char_end"]]
                    or sha(segment["text"]) != segment["text_sha"]
                ):
                    errors.append("Segment content: " + segment["id"])
                fts = store.rows("SELECT text FROM segment_fts WHERE segment_id=?", (segment["id"],))
                if fts != [{"text": segment["text"]}]:
                    errors.append("FTS content: " + segment["id"])
            if position != len(artifact["text"]):
                errors.append("Incomplete primary ranges: " + ext["id"])
        except (OSError, ValueError, KeyError) as exc:
            errors.append(str(exc))
    for obs in store.rows("SELECT o.*,s.text FROM occurrences o JOIN segments s ON s.id=o.segment_id"):
        if obs["text"][obs["start"] : obs["end"]] != obs["raw_value"]:
            errors.append("Occurrence span: " + obs["id"])
    previous = "0" * 64
    for event in store.rows("SELECT * FROM audit_events ORDER BY sequence"):
        expected = sha(dump([event["run_id"], event["event_type"], event["payload_json"], previous]))
        if event["prev_hash"] != previous or event["event_hash"] != expected:
            errors.append("Audit chain: " + str(event["sequence"]))
        if event["event_type"] in {"citation_choices_resolved", "citation_choices_rejected"}:
            from .citation_choices import resolve

            try:
                receipt = json.loads(event["payload_json"])
                attempt = store.one("SELECT * FROM task_attempts WHERE id=?", (receipt["attempt_id"],))
                raw = json.loads(store.get(receipt["raw_output_sha"]))
                payload = json.loads(store.get(receipt["input_sha"]))
                try:
                    resolution_matches = sha(dump(resolve(raw, payload))) == receipt["resolved_output_sha"]
                except (ValueError, KeyError, TypeError):
                    resolution_matches = False
                if (
                    attempt["task_id"] != receipt["task_id"]
                    or attempt["input_sha"] != receipt["input_sha"]
                    or attempt["output_sha"] != receipt["resolved_output_sha"]
                    or resolution_matches != (event["event_type"] == "citation_choices_resolved")
                ):
                    raise ValueError("Citation selection lineage mismatch")
            except Exception as exc:
                errors.append("Citation selection receipt: " + str(exc))
        previous = event["event_hash"]
    for snap in store.rows("SELECT * FROM snapshots"):
        try:
            manifest = store.manifest(snap["id"])
            expected = {(d["document_version_id"], d["extraction_id"]) for d in manifest["documents"]}
            actual = {
                (r["document_version_id"], r["extraction_id"])
                for r in store.rows("SELECT * FROM snapshot_documents WHERE snapshot_id=?", (snap["id"],))
            }
            if actual != expected:
                errors.append("Snapshot membership: " + snap["id"])
            for document in manifest["documents"]:
                version = store.one(
                    "SELECT * FROM document_versions WHERE id=?", (document["document_version_id"],)
                )
                if (
                    version["original_blob_sha"] != document["blob"]
                    or version["source_entry_id"] != document["source_entry_id"]
                    or version["parent_document_version_id"] != document["parent"]
                    or version["mime_part_path"] != document["mime_part"]
                ):
                    errors.append("Frozen source provenance: " + document["document_version_id"])
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    from .validation import validate_result

    for task in store.rows("SELECT * FROM review_tasks WHERE state='validated'"):
        try:
            result = json.loads(store.get(task["result_sha"]))
            validate_result(result, task, json.loads(store.get(task["input_manifest_sha"])))
            expected_findings = set()
            for ordinal, finding in enumerate(result["findings"]):
                fid = ident("G", task["id"], ordinal)
                expected_findings.add(fid)
                expected = (
                    fid,
                    task["run_id"],
                    task["id"],
                    finding["assertion"],
                    finding["epistemic_status"],
                    dump(finding),
                )
                actual = store.db.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
                if actual is None or tuple(actual) != expected:
                    errors.append("Finding ledger differs from accepted result: " + fid)
                expected_refs = {
                    (fid, ref["segment_id"], ref["start"], ref["end"]) for ref in finding["sources"]
                }
                actual_refs = {
                    tuple(row.values())
                    for row in store.rows("SELECT * FROM finding_sources WHERE finding_id=?", (fid,))
                }
                if expected_refs != actual_refs:
                    errors.append("Finding premise ledger: " + fid)
            if expected_findings != {
                row["id"] for row in store.rows("SELECT id FROM findings WHERE task_id=?", (task["id"],))
            }:
                errors.append("Complete finding ledger: " + task["id"])
            for proposal in result["interpretations"]:
                pid = ident("P", task["run_id"], proposal, task["worker_id"])
                actual = store.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
                if (
                    actual is None
                    or actual["dependencies_json"] != dump(proposal["evidence_refs"])
                    or actual["explanation"] != proposal["explanation"]
                ):
                    errors.append("Interpretation ledger differs from accepted result: " + pid)
            for issue in result["unresolved_questions"]:
                uid = ident("U", task["run_id"], issue.strip())
                if not store.db.execute("SELECT 1 FROM followups WHERE id=?", (uid,)).fetchone():
                    errors.append("Unresolved issue ledger: " + uid)
        except Exception as exc:
            errors.append("Accepted result invalid: " + task["id"] + ": " + str(exc))
    errors.extend(verify_derivations(store))
    return {
        "ok": not errors,
        "errors": errors,
        "audit_head": previous,
        "scope": "checksums, partition, citations, audit and snapshot membership; not source authenticity",
    }


def export(store, run_id, output):
    output = Path(output).resolve()
    root = Path(store.config()["root"])
    if output.is_relative_to(root) or output == store.state:
        raise ValueError("Export must be outside source corpus and database directory")
    status = Queue(store).review_status(run_id)
    findings = store.rows("SELECT * FROM findings WHERE run_id=? ORDER BY id", (run_id,))
    proposals = store.rows("SELECT * FROM proposals WHERE run_id=? ORDER BY id", (run_id,))
    issues = store.rows("SELECT * FROM followups WHERE run_id=? ORDER BY id", (run_id,))
    snapshot = store.manifest(status["snapshot_id"])
    from .retrieval import API
    from .validation import validate_refs

    api = API(store)
    active_extractions = {d["extraction_id"] for d in store.manifest()["documents"]}
    for finding in findings:
        payload = json.loads(finding["payload_json"])
        refs = payload["sources"]
        validate_refs(
            refs, {r["segment_id"]: api.segment(status["snapshot_id"], r["segment_id"]) for r in refs}
        )
    for proposal in proposals:
        refs = json.loads(proposal["dependencies_json"])
        validate_refs(
            refs, {r["segment_id"]: api.segment(status["snapshot_id"], r["segment_id"]) for r in refs}
        )
        proposal["current_dependency_state"] = (
            "current"
            if all(r["extraction_id"] in active_extractions for r in refs)
            else "stale_for_current_snapshot"
        )
    issue_groups = issue_report_groups(store, run_id, findings, proposals, issues)
    from .assurance import assurance_status
    from .review_views import assurance_records

    assurance = {"status": assurance_status(store, run_id), "records": assurance_records(store, run_id)}
    citation_receipts = []
    for event in store.rows(
        "SELECT event_type,payload_json FROM audit_events WHERE run_id=? AND event_type IN ('citation_choices_resolved','citation_choices_rejected') ORDER BY sequence",
        (run_id,),
    ):
        receipt = json.loads(event["payload_json"])
        citation_receipts.append(
            {
                **receipt,
                "event_type": event["event_type"],
                "raw_output": json.loads(store.get(receipt["raw_output_sha"])),
                "resolved_output": json.loads(store.get(receipt["resolved_output_sha"])),
            }
        )
    for name, value in [
        ("coverage", status),
        ("manifest", snapshot),
        ("findings", findings),
        ("interpretations", proposals),
        ("issues", issues),
        ("issue_report", issue_groups),
        ("assurance", assurance),
        ("citation_receipts", citation_receipts),
    ]:
        atomic(output / (name + ".json"), (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(["path", "document_version_id", "extraction_id", "status", "warnings"])
    for doc in snapshot["documents"]:
        cells = [
            doc["path"],
            doc["document_version_id"],
            doc["extraction_id"],
            doc["status"],
            "; ".join(doc["warnings"]),
        ]
        writer.writerow(["'" + x if x.startswith(("=", "+", "-", "@", "\t", "\r")) else x for x in cells])
    atomic(output / "inventory.csv", text.getvalue().encode())
    report = [
        "# Evidence review",
        "",
        f"Run: {run_id}",
        f"Snapshot: {status['snapshot_id']}",
        f"State: {status['state']}",
        f"Validated scheduled inputs: {status['result_validated']}/{status['scheduled']}",
        f"Extraction gaps: {status['extraction_gaps']}; inventory errors: {status['inventory_errors']}; unresolved issues: {status['followups_open']}",
        "",
        "Assumptions: observations are extracted representations. Allegations and model interpretations are not established facts. Source delivery is not comprehension or all-pairs examination.",
        "",
        "## Findings and source premises",
        "",
    ]
    report += ["## Issue-by-issue review", ""]
    for group in issue_groups:
        report += ["Issue " + group["id"], fenced_json(group), ""]
    # Escape untrusted Markdown by representing evidence as JSON inside a fence longer than any input fence.
    for finding in findings:
        data = json.loads(finding["payload_json"])
        encoded = json.dumps(data, ensure_ascii=False, indent=2)
        fence = "`" * (max((len(x) for x in __import__("re").findall(r"`+", encoded)), default=2) + 1)
        report += [
            f"Finding {finding['id']} ({finding['epistemic_status']})",
            fence + "json",
            encoded,
            fence,
            "",
        ]
    report += [
        "## Independent wording and interpretation checks",
        "",
        "See assurance.json for the complete attributed observations, candidate paths, stage coverage and human-review flags. Independent calls may share model biases; validated review is not proof of comprehension.",
        fenced_json(assurance["status"]),
        "",
        "## Unresolved issues and attributed interpretations",
        "",
        "See issue_report.json, issues.json and interpretations.json for the complete ledger. Interpretations remain unreviewed; no mechanical SUPPORTS/CONTRADICTS/SUPERSEDES edge is created.",
    ]
    atomic(output / "report.md", ("\n".join(report) + "\n").encode())
    return str(output)


def backup(store, destination):
    destination = Path(destination).resolve()
    if (
        destination.exists()
        or destination.is_relative_to(store.state)
        or destination.is_relative_to(Path(store.config()["root"]))
    ):
        raise ValueError("Backup requires a new directory outside state and source corpus")
    destination.mkdir(parents=True, mode=0o700)
    with store.write():
        # Separate read connection sees a consistent committed state while writer is excluded.
        reader = sqlite3.connect(store.state / "evidence.sqlite3")
        target = sqlite3.connect(destination / "evidence.sqlite3")
        try:
            reader.backup(target)
        finally:
            reader.close()
            target.close()
        shutil.copytree(store.state / "objects", destination / "objects")
    restored = Store(destination)
    try:
        checked = verify(restored)
    finally:
        restored.close()
    if not checked["ok"]:
        raise ValueError("Backup verification failed: " + dump(checked))
    atomic(destination / "checkpoint.json", dump(checked).encode())
    return checked


def restore(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("Restore destination must not exist")
    checkpoint = json.loads((source / "checkpoint.json").read_text())
    db = sqlite3.connect(f"file:{source / 'evidence.sqlite3'}?mode=ro", uri=True)
    try:
        root = Path(json.loads(db.execute("SELECT configuration_json FROM corpora").fetchone()[0])["root"])
    finally:
        db.close()
    if destination.is_relative_to(root) or destination.is_relative_to(source):
        raise ValueError("Restore must remain outside source corpus and backup")
    shutil.copytree(source, destination)
    store = Store(destination)
    try:
        checked = verify(store)
        if checked["audit_head"] != checkpoint["audit_head"] or not checked["ok"]:
            raise ValueError("Restored backup failed verification")
        return checked
    finally:
        store.close()


class DerivationRecorder:
    """Replay existing deterministic builders without changing the database."""

    def __init__(self, store):
        self.store = store
        self.db = self
        self.insertions = {}

    def rows(self, *args):
        return self.store.rows(*args)

    def one(self, *args):
        return self.store.one(*args)

    def get(self, *args):
        return self.store.get(*args)

    def manifest(self, *args):
        return self.store.manifest(*args)

    def execute(self, sql, parameters):
        import re

        match = re.match(r"INSERT OR IGNORE INTO (\w+) VALUES", sql)
        if not match:
            raise ValueError("Unexpected mutating operation during derivation replay")
        self.insertions.setdefault(match.group(1), set()).add(tuple(parameters))


def verify_derivations(store):
    from .features import index_extraction
    from .relationships import resolve
    from .snapshots import fts_name

    errors = []
    all_expected_link_evidence = set()
    for extraction in store.rows("SELECT * FROM extractions"):
        try:
            artifact = json.loads(store.get(extraction["artifact_sha"]))
            cfg = artifact["configuration"]
            if (
                sha(dump(cfg)) != extraction["config_hash"]
                or sha(dump(artifact["parser"])) != extraction["parser_version"]
            ):
                errors.append("Extraction configuration signature: " + extraction["id"])
            recorder = DerivationRecorder(store)
            blob = store.one(
                "SELECT original_blob_sha FROM document_versions WHERE id=?",
                (extraction["document_version_id"],),
            )["original_blob_sha"]
            index_extraction(recorder, extraction["id"], artifact["text"], cfg, blob)
            observed = {
                tuple(r.values())
                for r in store.rows(
                    "SELECT o.* FROM occurrences o JOIN segments s ON s.id=o.segment_id WHERE s.extraction_id=?",
                    (extraction["id"],),
                )
            }
            if observed != recorder.insertions.get("occurrences", set()):
                errors.append("Occurrence derivation set: " + extraction["id"])
            for feature in recorder.insertions.get("features", set()):
                actual = store.db.execute("SELECT * FROM features WHERE id=?", (feature[0],)).fetchone()
                if actual is None or tuple(actual) != feature:
                    errors.append("Feature derivation: " + feature[0])
        except (OSError, ValueError, KeyError) as exc:
            errors.append("Extraction derivation: " + extraction["id"] + ": " + str(exc))
    for snapshot in store.rows("SELECT * FROM snapshots"):
        sid = snapshot["id"]
        expected = {
            r["id"]
            for r in store.rows(
                """SELECT o.id FROM occurrences o JOIN segments s ON s.id=o.segment_id JOIN snapshot_documents sd ON sd.extraction_id=s.extraction_id WHERE sd.snapshot_id=?""",
                (sid,),
            )
        }
        actual = {
            r["occurrence_id"]
            for r in store.rows("SELECT occurrence_id FROM snapshot_occurrences WHERE snapshot_id=?", (sid,))
        }
        if expected != actual:
            errors.append("Snapshot occurrence membership: " + sid)
        recorder = DerivationRecorder(store)
        resolve(recorder, sid)
        expected_links = recorder.insertions.get("snapshot_links", set())
        actual_links = {
            tuple(r.values()) for r in store.rows("SELECT * FROM snapshot_links WHERE snapshot_id=?", (sid,))
        }
        if actual_links != expected_links:
            errors.append("Snapshot explicit-link membership: " + sid)
        for link in recorder.insertions.get("explicit_links", set()):
            actual = store.db.execute("SELECT * FROM explicit_links WHERE id=?", (link[0],)).fetchone()
            if actual is None or tuple(actual) != link:
                errors.append("Explicit link derivation: " + link[0])
        expected_evidence = recorder.insertions.get("link_evidence", set())
        all_expected_link_evidence.update(expected_evidence)
        actual_evidence = {
            tuple(r.values())
            for r in store.rows(
                """SELECT le.* FROM link_evidence le JOIN snapshot_links sl ON sl.link_id=le.link_id
                JOIN segments s ON s.id=le.segment_id
                JOIN snapshot_documents sd ON sd.snapshot_id=sl.snapshot_id AND sd.extraction_id=s.extraction_id
                WHERE sl.snapshot_id=?""",
                (sid,),
            )
        }
        if expected_evidence != actual_evidence:
            errors.append("Explicit link evidence: " + sid)
        name = fts_name(sid)
        try:
            actual_fts = sorted(tuple(r) for r in store.db.execute(f"SELECT segment_id,text FROM {name}"))
            expected_fts = sorted(
                tuple(r)
                for r in store.db.execute(
                    "SELECT s.id,s.text FROM segments s JOIN snapshot_documents sd ON sd.extraction_id=s.extraction_id WHERE sd.snapshot_id=?",
                    (sid,),
                )
            )
            if expected_fts != actual_fts:
                errors.append("Snapshot FTS content: " + sid)
        except sqlite3.Error as exc:
            errors.append("Snapshot FTS: " + sid + ": " + str(exc))
        manifest = store.manifest(sid)
        actual_history = {
            (r["document_version_id"], r["extraction_id"])
            for r in store.rows("SELECT * FROM snapshot_history WHERE snapshot_id=?", (sid,))
        }
        expected_history = {
            (r["document_version_id"], r["extraction_id"]) for r in manifest.get("history", [])
        }
        if actual_history != expected_history:
            errors.append("Snapshot historical membership: " + sid)
    if {tuple(r.values()) for r in store.rows("SELECT * FROM link_evidence")} != all_expected_link_evidence:
        errors.append("Global explicit-link evidence derivation set")
    for run in store.rows("SELECT * FROM runs"):
        errors.extend(task_plan_errors(store, run["id"]))
        expected_sources = {
            r["id"]
            for r in store.rows(
                "SELECT s.id FROM segments s JOIN snapshot_documents sd ON sd.extraction_id=s.extraction_id WHERE sd.snapshot_id=?",
                (run["snapshot_id"],),
            )
        }
        sources, memberships, links = set(), set(), set()
        for task in store.rows("SELECT * FROM review_tasks WHERE run_id=?", (run["id"],)):
            payload = json.loads(store.get(task["input_manifest_sha"]))
            if task["kind"] == "source":
                sources.update(s["id"] for s in payload["segments"])
            if task["pass_id"] == "B":
                descriptor = payload["descriptor"]
                memberships.update(descriptor.get("membership_ids", []))
                if descriptor.get("explicit_link"):
                    links.add(descriptor["explicit_link"]["id"])
        if sources != expected_sources:
            errors.append("Scheduled source coverage: " + run["id"])
        expected_memberships = {
            r["occurrence_id"]
            for r in store.rows(
                "SELECT occurrence_id FROM snapshot_occurrences WHERE snapshot_id=?", (run["snapshot_id"],)
            )
        }
        expected_links = {
            r["link_id"]
            for r in store.rows(
                "SELECT link_id FROM snapshot_links WHERE snapshot_id=?", (run["snapshot_id"],)
            )
        }
        if memberships != expected_memberships or links != expected_links:
            errors.append("Scheduled graph coverage: " + run["id"])
    return errors


def fenced_json(value):
    import re

    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    fence = "`" * (max((len(x) for x in re.findall(r"`+", encoded)), default=2) + 1)
    return fence + "json\n" + encoded + "\n" + fence


def issue_report_groups(store, run_id, findings, proposals, issues):
    run = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
    groups = {"question": {"id": "question", "issue": run["question"], "state": run["state"], "findings": []}}
    for issue in issues:
        groups[issue["id"]] = {
            "id": issue["id"],
            "issue": issue["issue"],
            "state": issue["state"],
            "findings": [],
        }
    for finding in findings:
        task = store.one("SELECT input_manifest_sha FROM review_tasks WHERE id=?", (finding["task_id"],))
        descriptor = json.loads(store.get(task["input_manifest_sha"]))["descriptor"]
        key = descriptor.get("issue_id", "question")
        groups.get(key, groups["question"])["findings"].append(finding["id"])
    by_id = {f["id"]: json.loads(f["payload_json"]) for f in findings}
    for group in groups.values():
        refs = {r["segment_id"] for fid in group["findings"] for r in by_id[fid]["sources"]}
        group["source_premises"] = [r for fid in group["findings"] for r in by_id[fid]["sources"]]
        relevant = [
            p for p in proposals if refs & {r["segment_id"] for r in json.loads(p["dependencies_json"])}
        ]
        group["supporting_interpretations"] = [
            p["id"] for p in relevant if p["relation_type"].upper() in ("SUPPORTS", "SUPPORT")
        ]
        group["opposing_interpretations"] = [
            p["id"] for p in relevant if p["relation_type"].upper() in ("CONTRADICTS", "OPPOSES")
        ]
        group["other_interpretations"] = [
            p["id"]
            for p in relevant
            if p["id"] not in group["supporting_interpretations"] + group["opposing_interpretations"]
        ]
        group["assumptions"] = [
            "Grouping follows scheduled issue tasks and cited sources; not an independently validated semantic classification.",
            "Empty support/opposition lists mean no such attributed classification was supplied.",
        ]
        group["uncertainties"] = [
            by_id[fid]["uncertainty"] for fid in group["findings"] if by_id[fid]["uncertainty"]
        ]
    return list(groups.values())


def task_plan_errors(store, run_id):
    expected = set()
    for row in store.rows(
        "SELECT payload_json FROM audit_events WHERE run_id=? AND event_type='task_scheduled'", (run_id,)
    ):
        item = json.loads(row["payload_json"])
        expected.add((item["task_id"], item["pass_id"], item["kind"], item["input_sha"]))
    actual = {
        tuple(row.values())
        for row in store.rows(
            "SELECT id,pass_id,kind,input_manifest_sha FROM review_tasks WHERE run_id=?", (run_id,)
        )
    }
    return ["Scheduled task ledger mismatch: " + run_id] if actual != expected else []
