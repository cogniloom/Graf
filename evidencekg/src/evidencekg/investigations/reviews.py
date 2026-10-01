"""Append-only human interpretations. Only committed ledger events are effective."""

import fcntl
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from evidencekg.app.manager import NotReady
from evidencekg.db import dump, sha


class ReviewActions:
    @contextmanager
    def review_guard(self):
        """Serialize review writes with erasure previews/intents across service instances."""
        fd = os.open(self.home / "review-write.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def reviews(self, run_id):
        with self.lock, self.db() as db:
            row = self._row(db, run_id)
            if row["state"] in {"erased", "deleting"}:
                return []
            records, latest = [], {}
            for event in self.vault.events(run_id):
                if event["event_type"] != "interpretation_reviewed":
                    continue
                key = event["payload"]["artifact_id"]
                value = self._json(key, None)
                if value is None:
                    continue
                if value["actor"] != event["actor"]:
                    raise ValueError("Review attribution drift")
                record = dict(value, id=key, ledger_seq=event["seq"], ledger_hash=event["hash"])
                target = (value["target_kind"], value["target_id"])
                if value["supersedes"] != latest.get(target):
                    raise ValueError("Review history is not a linear chain")
                latest[target] = key
                records.append(record)
            return [
                dict(
                    r,
                    current=latest[(r["target_kind"], r["target_id"])] == r["id"],
                    effective=latest[(r["target_kind"], r["target_id"])] == r["id"]
                    and r["decision"] != "retract",
                )
                for r in records
            ]

    def _review_targets(self, row):
        snapshot = self._json(row["snapshot_id"], {})
        result = self._json(row["result_id"], {})
        targets = {}

        def add(kind, key, value, sources):
            targets[(kind, key)] = {
                "target_kind": kind,
                "target_id": key,
                "record": value,
                "sources": sources,
                "target_sha": sha(dump(value)),
            }

        for key, segment in snapshot.get("segments", {}).items():
            source = {k: segment.get(k) for k in ("id", "text", "source_path", "locators")}
            add("segment", key, segment, [source])
            for field in ("knowledge", "knowledge_uncertainty"):
                annotation = segment.get(field, {})
                for item in annotation.get("items", []) + annotation.get("observations", []):
                    if item.get("id"):
                        existing = targets.get(("knowledge", item["id"]))
                        if existing:
                            existing["sources"].append(source)
                        else:
                            add("knowledge", item["id"], item, [source])
        for link in snapshot.get("links", []):
            sources = [
                t["sources"][0]
                for (kind, _), t in targets.items()
                if kind == "segment"
                and (
                    t["record"].get("document_version_id") in {link.get("from_node"), link.get("to_node")}
                    or t["target_id"] in {link.get("from_node"), link.get("to_node")}
                )
            ]
            add("link", link["id"], link, sources)
        for conclusion in result.get("conclusions", []):
            add("conclusion", conclusion["id"], conclusion, conclusion["supporting"] + conclusion["contrary"])
        return targets

    def review_targets(self, run_id, offset=0, limit=30):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid review target page")
        with self.lock, self.db() as db:
            row = self._row(db, run_id)
            if row["state"] in {"erased", "deleting"}:
                raise NotReady("Evidence is restricted by erasure")
            items = list(self._review_targets(row).values())
            return {
                "items": items[offset : offset + limit],
                "total": len(items),
                "next_offset": offset + limit if offset + limit < len(items) else None,
                "scope": "Retained snapshot interpretations only; annotations can be incomplete.",
            }

    def review(self, run_id, target_kind, target_id, decision, reason, correction="", supersedes=None):
        if decision not in {"confirm", "reject", "clarify", "retract"}:
            raise ValueError("Unknown review decision")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise ValueError("A bounded review reason is required")
        if not isinstance(correction, str) or len(correction) > 8000:
            raise ValueError("Invalid correction")
        if decision == "clarify" and not correction.strip():
            raise ValueError("Clarification requires a correction")
        with self.lock, self.review_guard(), self.db() as db:
            # Serialize with all session mutations, across processes, until ledger commit.
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, run_id)
            if row["state"] not in {"completed", "awaiting_input"}:
                raise ValueError("Only completed or awaiting-input runs can be reviewed")
            target = self._review_targets(row).get((target_kind, target_id))
            if target is None:
                raise ValueError("Review target is absent from this retained run")
            latest = next(
                (
                    r
                    for r in self.reviews(run_id)
                    if r["current"] and (r["target_kind"], r["target_id"]) == (target_kind, target_id)
                ),
                None,
            )
            if supersedes != (latest["id"] if latest else None):
                raise ValueError("Review changed; reload before superseding it")
            if decision == "retract" and (not latest or not latest["effective"]):
                raise ValueError("There is no effective review to retract")
            value = {
                "version": 1,
                "source_run_id": run_id,
                "target_kind": target_kind,
                "target_id": target_id,
                "target_sha": target["target_sha"],
                "snapshot_artifact_id": row["snapshot_id"],
                "decision": decision,
                "reason": reason.strip(),
                "correction": correction.strip(),
                "supersedes": supersedes,
                "actor": self.actor,
                "time": datetime.now(timezone.utc).isoformat(),
                "epistemic_status": "attributed_human_assertion_not_documentary_fact",
            }
            parents = [p for p in (row["snapshot_id"], row["result_id"], supersedes) if p]
            art = self._artifact(value, "interpretation_review", run_id, parents=parents)
            # Free text is erasable content; the permanent ledger stores opaque references only.
            event = self.vault.append_event(
                "interpretation_reviewed",
                self.actor,
                run_id,
                {"artifact_id": art["id"], "supersedes": supersedes},
            )
            return dict(
                value,
                id=art["id"],
                ledger_seq=event["seq"],
                ledger_hash=event["hash"],
                current=True,
                effective=decision != "retract",
            )
