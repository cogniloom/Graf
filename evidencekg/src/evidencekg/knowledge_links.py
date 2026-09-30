"""Reversible identity comparisons and bounded passage dependence detection.

No destructive merges, learned graph predictions, transitive weak identity, or
independent-support counts. Each decision retains the compared observations.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from itertools import combinations

from .db import ident, sha

MAX_BLOCK = 64
MAX_PAIRS = 20000
NAME = r"[^\W\d_][\w.'’&-]*(?: [^\W\d_][\w.'’&-]*){0,5}"
CONTACT = re.compile(rf"(?P<name>{NAME})\s*<(?P<email>[\w.!#$%&'*+/=?^`{{|}}~-]+@[\w.-]+\.[A-Za-z]{{2,}})>")
FIELDS = {
    "name": "name",
    "email": "email",
    "e-mail": "email",
    "organisation": "organisation",
    "organization": "organisation",
    "firma": "organisation",
    "address": "address",
    "adresse": "address",
    "uid": "registry_id",
    "vat": "registry_id",
}


def normalize(value):
    value = unicodedata.normalize("NFC", value).casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        value = value.replace(a, b)
    return " ".join(re.findall(r"\w+", value))


def field_value(field, value):
    if field == "email":
        local, _, domain = value.strip().rpartition("@")
        return local + "@" + domain.casefold()
    if field == "registry_id":
        return value.strip().casefold()
    return normalize(value)


def observations(text):
    for line in re.finditer(r"[^\n]+", text):
        attributes = {}
        match = CONTACT.search(line.group())
        if match:
            attributes = {"name": match["name"], "email": match["email"]}
        for field in re.finditer(r"(?:^|;)\s*([\w-]+)\s*:\s*([^;]+)", line.group()):
            key = FIELDS.get(field[1].casefold())
            if key:
                attributes[key] = field[2].strip()
        if "name" not in attributes or len(attributes) < 2:
            continue
        if "email" in attributes and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", attributes["email"]):
            attributes.pop("email")
        if len(attributes) < 2:
            continue
        yield dict(
            start=line.start(),
            end=line.end(),
            quote=line.group(),
            category="identity_record",
            attributes=attributes,
            alternatives=[],
            concepts=[],
            normalization_status="unresolved",
            identity_status="source_record_only",
            interpretation_status="rule_interpretation",
        )
    # Passage fingerprinting uses bounded windows; the full source remains in
    # the vault. Word changes, including negation, never remove original text.
    for start in range(0, min(len(text), 100000), 4000):
        passage = text[start : start + 4000]
        if len(re.findall(r"\w+", passage)) >= 16:
            yield dict(
                start=start,
                end=start + len(passage),
                quote=passage,
                category="dependence_passage",
                alternatives=[],
                concepts=[],
                normalization_status="surface_only",
            )


def relation(source, name, target, **extra):
    return dict(
        id=ident("KE", source, name, target),
        kind="edge",
        from_node=source,
        relation=name,
        to_node=target,
        **extra,
    )


def compare_identities(observations):
    records = {n["id"]: n for n in observations if n.get("category") == "identity_record"}
    blocks = defaultdict(set)
    for key, record in records.items():
        for field, value in record["attributes"].items():
            if field in {"name", "email", "registry_id"}:
                blocks[(field, field_value(field, value))].add(key)
    pairs, gaps = set(), {}
    for key in sorted(blocks):
        members = sorted(blocks[key])
        if len(members) > MAX_BLOCK:
            gaps["identity_block_limit"] = gaps.get("identity_block_limit", 0) + 1
            continue
        for pair in combinations(members, 2):
            if len(pairs) >= MAX_PAIRS:
                gaps["identity_pair_limit"] = 1
                break
            pairs.add(pair)
    nodes, edges = [], []
    for a, b in sorted(pairs):
        left, right = records[a]["attributes"], records[b]["attributes"]
        matched = sorted(
            k for k in left.keys() & right.keys() if field_value(k, left[k]) == field_value(k, right[k])
        )
        contradicted = sorted(
            k for k in left.keys() & right.keys() if field_value(k, left[k]) != field_value(k, right[k])
        )
        # A shared mailbox with differing names cannot establish person identity.
        identifier = "email" in matched or (
            "registry_id" in matched
            and bool(re.fullmatch(r"CHE-\d{3}\.\d{3}\.\d{3}", left["registry_id"], re.I))
        )
        strong = identifier and "name" in matched and not contradicted
        status = (
            "conflicting_attributes"
            if contradicted
            else "same_identified_record"
            if strong
            else "possible_identity"
        )
        qualified = any(
            records[item].get("quotation_depth")
            or records[item].get("revision_state", "none") != "none"
            or records[item].get("evidence_role")
            in {"deleted_revision", "inserted_revision", "comment", "note"}
            for item in (a, b)
        )
        if qualified and status == "same_identified_record":
            status = "structure_qualified_identity"
        key = ident("KI", a, b, matched, contradicted)
        nodes.append(
            dict(
                id=key,
                kind="identity",
                method="explicit-multi-field-comparison-v1",
                status=status,
                key=" / ".join(sorted({left["name"], right["name"]})),
                member_ids=[a, b],
                matched_fields=matched,
                conflicting_fields=contradicted,
                supporting_records=[
                    {
                        k: records[item].get(k)
                        for k in (
                            "id",
                            "attributes",
                            "source_path",
                            "sources",
                            "evidence_role",
                            "revision_state",
                            "quotation_depth",
                        )
                    }
                    for item in (a, b)
                ],
                calibrated_probability=None,
                decision="reversible_snapshot_hypothesis",
                scope="same supplied identifiers, not proof of real-world identity; no transitive merge",
            )
        )
        edges.extend([relation(a, "IDENTITY_COMPARISON", key), relation(b, "IDENTITY_COMPARISON", key)])
    return nodes, edges, gaps


def passage_dependence(observations):
    records = {n["id"]: n for n in observations if n.get("category") == "dependence_passage"}
    fingerprints, postings = {}, defaultdict(set)
    for key, row in records.items():
        words = re.findall(r"\w+", row["quote"].casefold())
        features = {sha(" ".join(words[i : i + 5]))[:20] for i in range(len(words) - 4)}
        # Stable bottom-k sketch limits index space. Exact Jaccard is checked on
        # the complete bounded-passage shingle sets, never on the sketch alone.
        fingerprints[key] = features
        for feature in sorted(features)[:32]:
            postings[feature].add(key)
    pairs, gaps = set(), {}
    for feature in sorted(postings):
        members = sorted(postings[feature])
        if len(members) > MAX_BLOCK:
            gaps["dependence_common_fingerprint_skipped"] = (
                gaps.get("dependence_common_fingerprint_skipped", 0) + 1
            )
            continue
        for a, b in combinations(members, 2):
            if records[a]["document_version_id"] == records[b]["document_version_id"]:
                continue
            if len(pairs) >= MAX_PAIRS:
                gaps["dependence_pair_limit"] = 1
                break
            pairs.add((a, b))
    nodes, edges = [], []
    for a, b in sorted(pairs):
        left, right = fingerprints[a], fingerprints[b]
        overlap = len(left & right) / len(left | right)
        if overlap < 0.8:
            continue
        key = ident("KD", a, b)
        nodes.append(
            dict(
                id=key,
                kind="dependence",
                method="passage-shingle-jaccard-v1",
                member_ids=[a, b],
                similarity=round(overlap, 6),
                calibrated_probability=None,
                status="possible_reproduction; independence_and_direction_unknown",
                quotation_roles=[records[k].get("evidence_role", "body") for k in (a, b)],
                rule_parameters=dict(shingle_words=5, candidate_sketch=32, threshold=0.8),
            )
        )
        edges.extend(
            [
                relation(a, "POSSIBLE_REPRODUCTION_MEMBER", key),
                relation(b, "POSSIBLE_REPRODUCTION_MEMBER", key),
            ]
        )
    return nodes, edges, gaps


def enrich(nodes, edges):
    gaps = {}
    observations = list(nodes.values())
    for algorithm in (compare_identities, passage_dependence):
        added, links, limitations = algorithm(observations)
        nodes.update({n["id"]: n for n in added})
        edges.extend(links)
        gaps.update(limitations)
    by_document = defaultdict(list)
    for row in observations:
        if row.get("kind") == "claim":
            by_document[row["document_version_id"]].append(row)
    for group in list(nodes.values()):
        if group.get("method") != "passage-shingle-jaccard-v1":
            continue
        for member in group["member_ids"]:
            passage = nodes[member]
            for claim in by_document[passage["document_version_id"]]:
                if (
                    claim["canonical_start"] < passage["canonical_end"]
                    and claim["canonical_end"] > passage["canonical_start"]
                ):
                    claim.setdefault("groups", []).append(group["id"])
                    edges.append(relation(claim["id"], "SHARED_PASSAGE_SIGNAL", group["id"]))
    contacts = defaultdict(list)
    for row in observations:
        if row.get("category") == "identity_record":
            contacts[normalize(row["attributes"]["name"])].append(row["id"])
    for claim in observations:
        if claim.get("kind") == "claim" and claim.get("actor_surface"):
            candidates = sorted(contacts.get(normalize(claim["actor_surface"]), []))
            if len(candidates) > MAX_BLOCK:
                gaps["actor_record_candidates_omitted"] = (
                    gaps.get("actor_record_candidates_omitted", 0) + len(candidates) - MAX_BLOCK
                )
            for target in candidates[:MAX_BLOCK]:
                edges.append(
                    relation(
                        claim["id"], "POSSIBLE_ACTOR_RECORD", target, identity_status="name_surface_only"
                    )
                )
    # Explicit source assertions, never inferred temporal precedence from age.
    for claim in observations:
        if claim.get("kind") != "claim" or not claim.get("object_key"):
            continue
        target = ident("KM", claim["object_key"])
        nodes.setdefault(
            target,
            dict(
                id=target,
                kind="entity",
                key=claim["object_key"],
                identity_status="unresolved_literal_surface",
            ),
        )
        edges.append(
            relation(
                claim["id"],
                "STATES_SUPERSESSION_OF" if claim["predicate"] == "supersession" else "STATES_DEPENDENCY_ON",
                target,
                epistemic_status="extracted_claim",
                premises=[claim["id"]],
            )
        )
    interval_groups = defaultdict(list)
    for claim in observations:
        interval = claim.get("validity", {})
        if (
            claim.get("kind") == "claim"
            and claim.get("modality") == "asserted"
            and claim.get("attribution") == "source_document"
            and interval.get("status") == "explicit_interval"
        ):
            interval_groups[
                (claim["entity_key"], claim["predicate"], claim["actor_surface"], claim["condition"])
            ].append(claim)
    for group in interval_groups.values():
        if len(group) > MAX_BLOCK:
            gaps["temporal_conflict_block_limit"] = gaps.get("temporal_conflict_block_limit", 0) + 1
            continue
        for left, right in combinations(sorted(group, key=lambda c: c["id"]), 2):
            if left["polarity"] == right["polarity"]:
                continue
            a, b = left["validity"], right["validity"]
            start, end = max(a["start"], b["start"]), min(a["end"] or "9999-12-31", b["end"] or "9999-12-31")
            if start > end:
                continue
            key = ident("KT", left["id"], right["id"])
            nodes[key] = dict(
                id=key,
                kind="conflict",
                method="overlapping-stated-validity-v1",
                member_ids=[left["id"], right["id"]],
                overlap_start=start,
                overlap_end=end,
                status="possible_boundary_conflict"
                if start == end
                else "potential_conflict; identity_and_truth_unresolved",
            )
            for claim in (left, right):
                claim.setdefault("groups", []).append(key)
                edges.append(relation(claim["id"], "POTENTIAL_CONFLICT_MEMBER", key))
    return gaps
