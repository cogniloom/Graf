"""Automatic, source-bound interpretations. Never mechanical facts or truth scores.

English/German operational rule packs: deliberately full-clause anchored, with literal
typed subjects. Unknown grammar is retained in the source, not guessed. Graph
groups avoid quadratic claim-pair expansion and never resolve entity identity.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import accumulate, chain
from pathlib import Path

from . import knowledge_links, knowledge_readings
from . import knowledge_semantics as semantics
from .db import atomic, dump, ident, sha
from .knowledge_graph import Graph as DiskGraph
from .knowledge_language import DATE_PATTERN, ENTITY_TYPES, date_readings, query_concepts
from .knowledge_language import observations as span_observations
from .knowledge_nlp import MAX_DOCUMENT_CHARS
from .knowledge_nlp import identity as nlp_identity
from .knowledge_nlp import observations as nlp_observations
from .knowledge_storage import put as put_knowledge
from .knowledge_storage import put_graph
from .knowledge_storage import read as read_knowledge

VERSION = "document-knowledge-en-de-v3"
MAX_CLAUSE = 2000
MAX_CLAIMS = 2000  # per document; overflow is a visible extraction gap
MAX_OBSERVATIONS = 4000
LIMITATIONS = [
    "English/German only: bounded sentence/event rules plus lexical observations and optional unvalidated name/syntax candidates; surrounding discourse is not resolved.",
    "All claims are unreviewed interpretations, not established facts.",
    "Literal entity surfaces do not resolve identity or organisation scope.",
    "Event days and explicitly stated validity intervals remain distinct; unknown boundaries and authority remain unresolved.",
    "Source groups do not establish independence, copying direction, or corroboration.",
    "Scores are not calibrated probabilities; claim truth remains unknown.",
]
ENTITY = (
    r"(?P<entity_type>" + "|".join(ENTITY_TYPES) + r")\s+#?(?P<entity_value>[A-Za-z0-9][A-Za-z0-9_-]{0,79})"
)
# Unicode letters, followed by a separate uppercase/negator check. This preserves
# names such as Özlem Müller without accepting 'nobody' or 'niemand' as actors.
ACTOR = r"[^\W\d_][\w\u0300-\u036f-]*(?: [^\W\d_][\w\u0300-\u036f-]*){0,3}"
STATE = r"(?P<state>approved|cancelled|canceled|paid|delivered|rejected)"
VERB = r"(?P<verb>approve|cancel|pay|deliver|reject)"
# No arbitrary trailing prose: it could reverse polarity or attribution.
TAIL = rf"(?: on (?P<date>{DATE_PATTERN}))?(?:,? (?P<condition>(?:only if|only after|if|unless|provided that|after) .+))?"
PASSIVE = re.compile(
    rf"(?i:(?:the )?{ENTITY}) (?P<aux>is|was|has been|will be|may be|must be) "
    rf"(?P<not>not )?{STATE}(?: by (?P<actor>{ACTOR}))?{TAIL}",
    re.IGNORECASE,
)
ACTIVE = re.compile(
    rf"(?P<actor>{ACTOR}) (?:(?P<aux>did|will|may|must) (?P<not>not )?{VERB}|{STATE}) "
    rf"(?i:(?:the )?{ENTITY}){TAIL}",
    re.IGNORECASE,
)
EN_AUX_NEGATIVE = [
    (
        re.compile(
            rf"(?:the )?{ENTITY} (?P<aux>{aux}) (?P<not>not) {continuation} {STATE}"
            rf"(?: by (?P<actor>{ACTOR}))?{TAIL}",
            re.IGNORECASE,
        ),
        f"en-passive-negative-{aux}",
        None,
    )
    for aux, continuation in (("will", "be"), ("may", "be"), ("must", "be"), ("has", "been"))
]
EN_ACTIVE_PERFECT = re.compile(
    rf"(?P<actor>{ACTOR}) has (?P<not>not )?{STATE} (?:the )?{ENTITY}{TAIL}", re.IGNORECASE
)
EN_ACTIVE_PRESENT = re.compile(
    rf"(?P<actor>{ACTOR}) (?P<verb>approves|cancels|pays|delivers|rejects) (?:the )?{ENTITY}{TAIL}",
    re.IGNORECASE,
)
PREDICATES = {
    "delivered": "delivery",
    "deliver": "delivery",
    "delivers": "delivery",
    "rejected": "rejection",
    "reject": "rejection",
    "rejects": "rejection",
    "geliefert": "delivery",
    "liefert": "delivery",
    "lieferte": "delivery",
    "liefern": "delivery",
    "abgelehnt": "rejection",
    "ablehnen": "rejection",
    "approved": "approval",
    "approve": "approval",
    "cancelled": "cancellation",
    "canceled": "cancellation",
    "cancel": "cancellation",
    "paid": "payment",
    "pay": "payment",
    "approves": "approval",
    "cancels": "cancellation",
    "pays": "payment",
    "genehmigt": "approval",
    "genehmigte": "approval",
    "genehmigen": "approval",
    "bewilligt": "approval",
    "bewilligte": "approval",
    "bewilligen": "approval",
    "storniert": "cancellation",
    "stornierte": "cancellation",
    "stornieren": "cancellation",
    "annulliert": "cancellation",
    "annullierte": "cancellation",
    "annullieren": "cancellation",
    "bezahlt": "payment",
    "bezahlte": "payment",
    "bezahlen": "payment",
    "beglichen": "payment",
    "beglich": "payment",
    "begleicht": "payment",
    "begleichen": "payment",
}
DE_ENTITY = r"(?:(?:die|der|das|den) )?" + ENTITY
DE_STATE = r"(?P<state>genehmigt|bewilligt|storniert|annulliert|bezahlt|beglichen|geliefert|abgelehnt)"
DE_FINITE = (
    r"(?P<verb>genehmigte?|bewilligte?|stornierte?|annullierte?|bezahlte?|beglich|begleicht|lieferte?)"
)
DE_INFINITIVE = r"(?P<verb>genehmigen|bewilligen|stornieren|annullieren|bezahlen|begleichen|liefern|ablehnen)"
DE_DATE = rf"(?: am (?P<date>{DATE_PATTERN}))?"
DE_BY = rf"(?: von (?P<actor>{ACTOR}))?"
DE_CONDITION = (
    r"(?:,? (?P<condition>(?:nur wenn|nur nach|erst nach|wenn|falls|sofern|nachdem|"
    r"nach|vorausgesetzt, dass|unter der Bedingung, dass) .+))?"
)
DE_MODALITY = {
    "wird": "planned",
    "würde": "possible",
    "kann": "possible",
    "könnte": "possible",
    "muss": "required",
    "musste": "required",
    "darf": "permitted",
    "soll": "unresolved",
}


def german_rules():
    """Explicit word orders, not bag-of-words polarity/date inference."""
    rules = []
    # Never accept 'nicht am DATE': that can negate the date instead of the event.
    # Each variant binds a date exactly once, before negation or after a positive
    # participle. Negated trailing-date readings are rejected below as ambiguous.
    for position, middle, tail_date in (
        ("date_actor", DE_DATE + DE_BY, ""),
        ("actor_date", DE_BY + DE_DATE, ""),
        ("trailing_date", DE_BY, DE_DATE),
    ):
        for form, aux, ending, modality in (
            ("passive", "ist|war|wurde", "", "asserted"),
            ("perfect_passive", "ist|war", " worden", "asserted"),
            # 'wird bezahlt' may be present passive or future; do not choose.
            ("present_passive", "wird", "", "unresolved"),
            ("modal_passive", "|".join(DE_MODALITY), " werden", None),
        ):
            pattern = (
                rf"{DE_ENTITY} (?P<aux>{aux}){middle} (?P<not>nicht )?"
                rf"{DE_STATE}{ending}{tail_date}{DE_CONDITION}"
            )
            rules.append((re.compile(pattern, re.IGNORECASE), f"de-{form}-{position}", modality))
    for position, before, after in (("before_object", DE_DATE, ""), ("after_object", "", DE_DATE)):
        rules.append(
            (
                re.compile(
                    rf"(?P<actor>{ACTOR}) {DE_FINITE}{before} {DE_ENTITY}{after}(?P<not> nicht)?{DE_CONDITION}",
                    re.IGNORECASE,
                ),
                f"de-active-{position}",
                "asserted",
            )
        )
        for form, aux, verb, modality in (
            ("perfect_active", "hat|hatte", DE_STATE, "asserted"),
            ("modal_active", "|".join(DE_MODALITY), DE_INFINITIVE, None),
        ):
            rules.append(
                (
                    re.compile(
                        rf"(?P<actor>{ACTOR}) (?P<aux>{aux}){before} {DE_ENTITY}{after} (?P<not>nicht )?"
                        rf"{verb}{DE_CONDITION}",
                        re.IGNORECASE,
                    ),
                    f"de-{form}-{position}",
                    modality,
                )
            )
    return rules


RULES = [
    (PASSIVE, "en-passive", None),
    (ACTIVE, "en-active", None),
    (EN_ACTIVE_PERFECT, "en-perfect-active", "asserted"),
    (EN_ACTIVE_PRESENT, "en-present-active", "asserted"),
    *EN_AUX_NEGATIVE,
    *german_rules(),
]


@lru_cache(maxsize=1)
def signature():
    # Code changes invalidate enrichment without forcing expensive reparsing/ASR.
    modules = {p.name: sha(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("knowledge*.py"))}
    return {
        "version": VERSION,
        "implementation_sha": sha(dump(modules)),
        "modules": modules,
        "nlp": nlp_identity(),
    }


def clauses(text):
    """Keep sentence/line boundaries; never silently slice an oversized clause."""
    yield from semantics.sentences(text)


def read_cache_file(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Knowledge cache file exceeds read budget")
    return raw


def analyse_section(store, text, locator, nlp_allowed, use_cache=True):
    """Reuse unchanged sections across document versions; rebind spans later."""
    context = {
        key: locator[key]
        for key in (
            "source_day",
            "quotation_depth",
            "attributed_speaker_surface",
            "evidence_role",
            "revision_state",
            "inline_quote_scopes",
        )
        if key in locator
    }
    identity = dict(
        text=text,
        context=context,
        nlp_allowed=nlp_allowed,
        signature=signature(),
        budgets=[MAX_CLAIMS, MAX_OBSERVATIONS, MAX_CLAUSE],
    )
    key = sha(dump(identity))
    path = store.state / "knowledge-cache" / (key + ".json")
    shared = store.config().get("knowledge_cache_directory")
    shared = Path(shared) if shared else None
    if use_cache and not path.exists() and shared and (shared / "keys" / (key + ".json")).exists():
        index = json.loads(read_cache_file(shared / "keys" / (key + ".json"), 1024))
        digest = index["blob"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid shared knowledge cache digest")
        raw = read_cache_file(shared / "objects" / digest, 64 * 1024 * 1024)
        if sha(raw) != digest:
            raise ValueError("Corrupt shared knowledge cache artifact")
        store.put(raw)
        atomic(path, dump(index).encode())
    if use_cache and path.exists():
        cached = read_knowledge(store, json.loads(read_cache_file(path, 1024))["blob"])
        if cached["key"] != key:
            raise ValueError("Knowledge section cache identity mismatch")
        return cached["result"]
    predictions, gaps = nlp_observations(text) if nlp_allowed else ([], {"nlp_character_budget_sections": 1})
    if len(text) > 100000:
        gaps["dependence_unexamined_characters"] = len(text) - 100000
    observed = []
    for item in chain(knowledge_links.observations(text), span_observations(text), predictions):
        if len(observed) >= MAX_OBSERVATIONS:
            gaps["observation_budget_unexamined_sections"] = 1
            break
        observed.append(item)
    parsed = []
    quote_scopes = iter(locator.get("inline_quote_scopes", semantics.quotation_spans(text)))
    active_scopes = []
    upcoming = next(quote_scopes, None)
    for lo, hi, quote in clauses(text):
        if len(quote) > MAX_CLAUSE:
            gaps["oversized_clauses"] = gaps.get("oversized_clauses", 0) + 1
            continue
        while upcoming and upcoming[0] <= lo:
            active_scopes.append(upcoming)
            upcoming = next(quote_scopes, None)
        active_scopes = [scope for scope in active_scopes if scope[1] > lo]
        scoped_locator = dict(locator)
        if active_scopes:
            scoped_locator["quotation_depth"] = max(1, locator.get("quotation_depth", 0))
            speakers = [scope[2] for scope in active_scopes if scope[2]]
            if speakers:
                scoped_locator["attributed_speaker_surface"] = speakers[-1]
        result = semantics.parse(quote, parse_clause, scoped_locator)
        if result is None:
            gaps["unmatched_clauses"] = gaps.get("unmatched_clauses", 0) + 1
        else:
            if len(parsed) < MAX_CLAIMS:
                parsed.append([lo, hi, result])
            else:
                gaps["claim_limit"] = gaps.get("claim_limit", 0) + 1
    result = dict(observations=observed, claims=parsed, gaps=gaps)
    if use_cache:
        blob = put_knowledge(store, dict(key=key, result=result))
        atomic(path, dump({"blob": blob}).encode())
        if shared:
            atomic(shared / "objects" / blob, store.get(blob))
            atomic(shared / "keys" / (key + ".json"), dump({"blob": blob}).encode())
    return result


def parse_clause(quote):
    if "?" in quote:
        return None
    body = quote.strip()
    quoted = body.startswith((">", '"', "“", "'", "„", "»", "«"))
    if quoted:
        body = body.lstrip("> ").strip("\"“”'„»« ")
    body = body.rstrip(".!").strip()
    matched = next(
        ((m, rule, modality) for pattern, rule, modality in RULES if (m := pattern.fullmatch(body))), None
    )
    if matched is None:
        return None
    match, rule, modality = matched
    language = rule[:2]
    fields = match.groupdict()
    actor = fields.get("actor")
    if actor and (
        not re.fullmatch(ACTOR, actor)
        or not all(part[0].isupper() for part in actor.split())
        or set(actor.lower().split())
        & {
            "no",
            "one",
            "nobody",
            "none",
            "not",
            "neither",
            "never",
            "niemand",
            "niemandem",
            "niemanden",
            "keiner",
            "keine",
            "keinen",
            "keinem",
            "keines",
            "nicht",
        }
    ):
        return None
    date = fields.get("date")
    date_surface = date
    readings = []
    if date:
        readings = date_readings(date, language_hint=language)
        if not readings:
            return None
        date = readings[0] if len(readings) == 1 else None
        if language == "de" and fields.get("not") and rule.endswith("trailing_date"):
            return None
    aux = (fields.get("aux") or "").lower()
    condition = fields.get("condition")
    modality = modality or (
        DE_MODALITY.get(aux, "unresolved")
        if language == "de"
        else {
            "will": "planned",
            "will be": "planned",
            "may": "possible",
            "may be": "possible",
            "must": "required",
            "must be": "required",
        }.get(aux, "asserted")
    )
    polarity = "negative" if fields.get("not") else "positive"
    negation_scope = "proposition" if fields.get("not") else None
    if fields.get("not"):
        if language == "de" and aux in {"muss", "musste"}:
            modality, polarity, negation_scope = "not_required", "unresolved", "modality"
        elif (language == "de" and aux == "darf") or (language == "en" and aux in {"must", "must be"}):
            modality, negation_scope = "prohibited", "modality"
        elif (language == "de" and aux in {"kann", "könnte", "soll"}) or (
            language == "en" and aux in {"may", "may be"}
        ):
            modality, polarity, negation_scope = "unresolved", "unresolved", "unresolved"
    # Preserve conditions verbatim; don't parse away potentially crucial wording.
    entity_type = ENTITY_TYPES[fields["entity_type"].lower()]
    return dict(
        language=language,
        extraction_rule=rule,
        entity_key=entity_type + ":" + fields["entity_value"],
        entity_type=entity_type,
        entity_type_surface=fields["entity_type"],
        entity_value=fields["entity_value"],
        predicate=PREDICATES[(fields.get("state") or fields.get("verb")).lower()],
        polarity=polarity,
        modality=modality,
        negation_scope=negation_scope,
        condition=condition,
        applicable_on=date,
        applicable_date_surface=date_surface,
        applicable_date_candidates=readings,
        actor_surface=fields.get("actor"),
        actor_identity="unresolved",
        attribution="quoted_speaker_unresolved" if quoted else "source_document",
    )


def source_refs(segments, start, end, text):
    refs = []
    position = start
    for segment in segments:
        lo, hi = max(start, segment["char_start"]), min(end, segment["char_end"])
        if lo >= hi:
            continue
        a, b = lo - segment["char_start"], hi - segment["char_start"]
        if (
            lo != position
            or segment["text"][a:b] != text[lo:hi]
            or sha(segment["text"]) != segment["text_sha"]
        ):
            raise ValueError("Knowledge source partition mismatch")
        refs.append(
            dict(
                segment_id=segment["id"],
                extraction_id=segment["extraction_id"],
                start=a,
                end=b,
                quote=text[lo:hi],
            )
        )
        position = hi
    if position != end:
        raise ValueError("Knowledge source span missing")
    return refs


def paragraph_sections(artifact):
    """Stable paragraph cache units with original canonical offsets/structure."""
    for section in artifact["locators"]:
        if section.get("modality") == "asr":
            yield section
            continue
        text = artifact["text"][section["start"] : section["end"]]
        scopes = iter(semantics.quotation_spans(text))
        upcoming = next(scopes, None)
        active = []

        def paragraph(begin, end):
            nonlocal upcoming, active
            while upcoming and upcoming[0] < end:
                active.append(upcoming)
                upcoming = next(scopes, None)
            active = [scope for scope in active if scope[1] > begin]
            result = dict(section, start=section["start"] + begin, end=section["start"] + end)
            result["locator"] = dict(
                section.get("locator", {}),
                reading_section_start=section["start"],
                reading_section_end=section["end"],
                inline_quote_scopes=[
                    [max(0, lo - begin), min(end, hi) - begin, speaker] for lo, hi, speaker in active
                ],
            )
            return result

        begin = 0
        for match in re.finditer(r"\n\s*\n", text):
            if match.start() > begin:
                yield paragraph(begin, match.start())
            begin = match.end()
        if begin < len(text):
            yield paragraph(begin, len(text))


def extract_document(store, document, use_cache=True):
    extraction = store.one("SELECT * FROM extractions WHERE id=?", (document["extraction_id"],))
    if extraction["document_version_id"] != document["document_version_id"]:
        raise ValueError("Knowledge extraction identity mismatch")
    artifact = json.loads(store.get(extraction["artifact_sha"]))
    text = artifact["text"]
    segments = store.rows(
        "SELECT * FROM segments WHERE extraction_id=? ORDER BY ordinal", (extraction["id"],)
    )
    claims, observed, uncertainties, gaps = [], [], [], Counter()
    supported = 0
    nlp_remaining = MAX_DOCUMENT_CHARS
    for section in paragraph_sections(artifact):
        locator = section.get("locator", {})
        if locator.get("kind") == "email_header":
            continue
        confidence = locator.get("confidence")
        if confidence and confidence.get("withheld"):
            gaps["withheld_speech_sections"] += 1
            start, end = section["start"], section["end"]
            uncertainties.append(
                dict(
                    id=ident("KU", extraction["id"], start, end, signature()),
                    kind="uncertainty",
                    document_version_id=document["document_version_id"],
                    extraction_id=extraction["id"],
                    artifact_sha=extraction["artifact_sha"],
                    original_blob_sha=document.get("blob"),
                    canonical_start=start,
                    canonical_end=end,
                    quote=text[start:end],
                    sources=source_refs(segments, start, end, text),
                    locator=locator,
                    epistemic_status="unresolved_source_reading",
                    confidence=confidence,
                    audit_artifacts=[
                        a for a in document.get("artifacts", []) if a["name"] == "speech-transcription.json"
                    ],
                    assertion_scope="unclear audio interval; candidate words remain audit-only; not evidence of the opposite",
                )
            )
            continue
        section_text = text[section["start"] : section["end"]]
        analysis = analyse_section(
            store, section_text, locator, len(section_text) <= nlp_remaining, use_cache
        )
        nlp_remaining = max(0, nlp_remaining - len(section_text))
        gaps.update(analysis["gaps"])
        quote_scopes = locator.get("inline_quote_scopes", semantics.quotation_spans(section_text))
        quote_starts = [scope[0] for scope in quote_scopes]
        quote_ends = list(accumulate((scope[1] for scope in quote_scopes), max))
        for item in analysis["observations"]:
            if len(observed) >= MAX_OBSERVATIONS:
                # Stop consuming: the remaining candidate count is unknown,
                # not a measured zero. Do not allocate an unbounded list first.
                gaps["observation_budget_unexamined_sections"] += 1
                break
            start, end = section["start"] + item.pop("start"), section["start"] + item.pop("end")
            prior_quote = bisect_left(quote_starts, end - section["start"]) - 1
            inline_quoted = prior_quote >= 0 and quote_ends[prior_quote] > start - section["start"]
            observed.append(
                dict(
                    **item,
                    id=ident(
                        "KO",
                        extraction["id"],
                        signature(),
                        start,
                        end,
                        item["category"],
                        item.get("entity_type_candidate"),
                    ),
                    kind="observation",
                    epistemic_status="predicted_connection"
                    if item.get("interpretation_status") == "model_prediction"
                    else "source_observation",
                    assertion_scope="captured text contains this surface; normalized meanings are candidates",
                    document_version_id=document["document_version_id"],
                    extraction_id=extraction["id"],
                    artifact_sha=extraction["artifact_sha"],
                    canonical_start=start,
                    canonical_end=end,
                    original_blob_sha=document.get("blob"),
                    sources=source_refs(segments, start, end, text),
                    transcription={"raw": confidence, "calibrated_probability": None},
                    source_reading=knowledge_readings.reference(document, section, start, end),
                    section_canonical_start=section["start"],
                    evidence_role=locator.get("evidence_role", "body"),
                    quotation_depth=max(int(inline_quoted), locator.get("quotation_depth", 0)),
                    revision_state=locator.get("revision_state", "none"),
                )
            )
        for lo, hi, parsed in analysis["claims"]:
            quote = section_text[lo:hi]
            supported += 1
            if len(claims) >= MAX_CLAIMS:
                gaps["claim_limit"] += 1
                continue
            start, end = section["start"] + lo, section["start"] + hi
            refs = source_refs(segments, start, end, text)
            claim = dict(
                id=ident("K", extraction["id"], signature(), start, end),
                kind="claim",
                epistemic_status="extracted_claim",
                review_status="automatic_unreviewed",
                document_version_id=document["document_version_id"],
                extraction_id=extraction["id"],
                artifact_sha=extraction["artifact_sha"],
                canonical_start=start,
                canonical_end=end,
                quote=quote,
                concepts=query_concepts(quote),
                sources=refs,
                context_sources=source_refs(
                    segments, max(section["start"], start - 500), min(section["end"], end + 500), text
                ),
                discourse_status="surrounding_discourse_unresolved",
                locator=knowledge_readings.compact_locator(locator),
                source_reading=knowledge_readings.reference(document, section, start, end),
                modality_source=section.get("modality", "native"),
                original_blob_sha=document.get("blob"),
                **parsed,
                confidence={
                    "transcription": {
                        "raw": confidence,
                        "calibrated_probability": None,
                        "probability_target": "transcription_fidelity",
                        "calibration_status": "unvalidated" if confidence else "unavailable",
                    },
                    "claim_extraction": {"calibrated_probability": None, "method": VERSION},
                    "entity_match": {"calibrated_probability": None, "status": "literal_surface_only"},
                    "claim_truth": {"calibrated_probability": None, "status": "unknown"},
                },
                usage="candidate_only; inspect original evidence and qualifications",
            )
            claims.append(claim)
    return {
        "signature": signature(),
        "extraction_id": extraction["id"],
        "artifact_sha": extraction["artifact_sha"],
        "claims": claims,
        "observations": observed,
        "uncertainties": uncertainties,
        "coverage": {
            "supported_clauses": supported,
            "emitted_claims": len(claims),
            "emitted_observations": len(observed),
            "gaps": dict(gaps),
        },
    }


def enrich_document(store, document, previous=None):
    """Reuse only the exact extraction and implementation; old snapshots stay frozen."""
    old = (previous or {}).get("knowledge", {})
    if old.get("signature") == signature() and previous.get("extraction_id") == document["extraction_id"]:
        result = read_knowledge(store, old["blob"])
        if result["extraction_id"] != document["extraction_id"] or result["signature"] != signature():
            raise ValueError("Knowledge cache identity mismatch")
        return old
    result = extract_document(store, document)
    return {"signature": signature(), "blob": put_knowledge(store, result), "coverage": result["coverage"]}


def graph_for(documents, read_artifact, storage=None):
    """Linear-size typed graph, with separate interpretation and observation nodes."""
    nodes = storage.nodes if storage is not None else {}
    edges = storage.edges if storage is not None else []
    groups = defaultdict(list)

    def edge(source, relation, target):
        edges.append(
            dict(
                id=ident("KE", source, relation, target),
                kind="edge",
                from_node=source,
                relation=relation,
                to_node=target,
            )
        )

    def concept_node(value):
        key = ident("KC", value)
        nodes[key] = dict(
            id=key,
            kind="concept",
            key=value,
            status="shared normalized vocabulary; not shared event or entity identity",
        )
        return key

    for doc in documents:
        artifact = read_artifact(doc)
        if not artifact["claims"] and not artifact.get("observations") and not artifact.get("uncertainties"):
            continue
        nodes[doc["document_version_id"]] = dict(
            id=doc["document_version_id"],
            kind="source",
            source_path=doc["path"],
            extraction_id=doc["extraction_id"],
        )
        for uncertainty in artifact.get("uncertainties", []):
            nodes[uncertainty["id"]] = dict(uncertainty, source_path=doc["path"])
            edge(uncertainty["id"], "UNCERTAIN_READING_OF", doc["document_version_id"])
        for original in artifact.get("observations", []):
            observed = dict(original, source_path=doc["path"])
            nodes[observed["id"]] = observed
            edge(observed["id"], "OBSERVED_IN", doc["document_version_id"])
            for concept in observed["concepts"]:
                key = concept_node(concept)
                edge(
                    observed["id"],
                    "POSSIBLE_READING"
                    if observed["normalization_status"] == "ambiguous"
                    else "LEXICAL_MAPPING",
                    key,
                )
        for original in artifact["claims"]:
            claim = dict(original, source_path=doc["path"])
            entity = ident("KM", claim["entity_key"])
            claim["entity_id"] = entity
            nodes[entity] = dict(
                id=entity,
                kind="entity",
                key=claim["entity_key"],
                identity_status="unresolved_literal_surface",
            )
            edge(claim["id"], "ABOUT_LITERAL_SURFACE", entity)
            edge(claim["id"], "EXTRACTED_FROM", doc["document_version_id"])
            for concept in claim.get("concepts", []):
                edge(claim["id"], "USES_VOCABULARY", concept_node(concept))
            if claim["applicable_on"]:
                edge(claim["id"], "STATED_EVENT_DAY", concept_node("date:" + claim["applicable_on"]))
            for method, key in (
                ("same_source_bytes", claim["original_blob_sha"]),
                ("repeated_wording", " ".join(claim["quote"].split())),
            ):
                if key:
                    groups[(method, key)].append(claim["id"])
            if (
                claim["modality"] == "asserted"
                and not claim["condition"]
                and claim["applicable_on"]
                and claim["attribution"] == "source_document"
            ):
                groups[
                    (
                        "potential_conflict",
                        dump(
                            [
                                claim["entity_key"],
                                claim["predicate"],
                                claim["applicable_on"],
                                claim["actor_surface"],
                            ]
                        ),
                    )
                ].append(claim["id"])
            nodes[claim["id"]] = claim
    for (method, key), members in sorted(groups.items()):
        if len(members) < 2 or (
            method == "potential_conflict" and len({nodes[key]["polarity"] for key in members}) < 2
        ):
            continue
        group_id = ident("KG", method, key)
        node = dict(
            id=group_id,
            kind="conflict" if method == "potential_conflict" else "dependence",
            method=method,
            claim_count=len(members),
            status="potential; identity and scope unresolved"
            if method == "potential_conflict"
            else "shared source signal; independence unknown",
        )
        nodes[group_id] = node
        for member in members:
            claim = nodes[member]
            claim.setdefault("groups", []).append(group_id)
            nodes[claim["id"]] = claim
            edge(
                claim["id"],
                "POTENTIAL_CONFLICT_MEMBER" if method == "potential_conflict" else "SHARED_SOURCE_SIGNAL",
                group_id,
            )
    del groups
    # A small explainable rule over normalized calendar concepts, not events.
    # Store adjacent known days only; transitive traversal supplies longer order
    # proofs without quadratic day-pair expansion or inferred event occurrence.
    days = sorted(
        (node["key"][5:], node["id"])
        for node in nodes.values()
        if node["kind"] == "concept" and re.fullmatch(r"date:\d{4}-\d{2}-\d{2}", node["key"])
    )
    for (earlier, source), (later, target) in zip(days, days[1:]):
        calendar_edge = knowledge_links.relation(source, "CALENDAR_PRECEDES", target)
        calendar_edge.update(
            epistemic_status="logical_derivation",
            rule_id="iso-calendar-order",
            rule_version="1",
            premises=[source, target],
            proof={"earlier_day": earlier, "later_day": later},
            conclusion_scope="calendar ordering only; not a statement that either event occurred",
        )
        edges.append(calendar_edge)
    graph_gaps = knowledge_links.enrich(nodes, edges)
    return {
        "graph_gaps": graph_gaps,
        "nodes": storage.rows("nodes")
        if storage is not None
        else sorted(nodes.values(), key=lambda n: n["id"]),
        "edges": storage.rows("edges") if storage is not None else sorted(edges, key=lambda e: e["id"]),
    }


def freeze(store, documents, previous=None):
    input_sha = sha(
        dump(
            {
                "signature": signature(),
                "documents": sorted(
                    (
                        d["document_version_id"],
                        d["extraction_id"],
                        d["path"],
                        d.get("blob"),
                        d["knowledge"]["blob"],
                    )
                    for d in documents
                ),
            }
        )
    )
    if previous and previous.get("input_sha") == input_sha and previous.get("signature") == signature():
        retained = read_knowledge(store, previous["blob"])
        if retained["signature"] != signature() or retained["coverage"] != previous["coverage"]:
            raise ValueError("Knowledge graph reuse identity mismatch")
        for kind in ("nodes", "edges"):
            for _ in retained[kind]:
                pass
        return previous
    gaps = Counter()

    def read_artifact(doc):
        artifact = read_knowledge(store, doc["knowledge"]["blob"])
        gaps.update(artifact["coverage"]["gaps"])
        return artifact

    with DiskGraph() as storage:
        graph = graph_for(documents, read_artifact, storage)
        gaps.update(graph.get("graph_gaps", {}))
        result = dict(
            signature=signature(),
            **graph,
            limitations=LIMITATIONS,
            coverage={
                "documents": len(documents),
                "gaps": dict(gaps),
                "node_counts": dict(Counter(n["kind"] for n in graph["nodes"])),
            },
        )
        return {
            "signature": signature(),
            "blob": put_graph(store, result),
            "coverage": result["coverage"],
            "input_sha": input_sha,
        }


def load(store, snapshot):
    manifest = store.manifest(snapshot)
    metadata = manifest.get("knowledge")
    if metadata is None:
        return None
    graph = read_knowledge(store, metadata["blob"])
    if graph["signature"] != metadata["signature"] or graph["coverage"] != metadata["coverage"]:
        raise ValueError("Knowledge graph identity mismatch")
    return graph


def project(graph, snapshot=None):
    """Add distinguishable semantic candidates to the app's existing graph view."""
    if graph is None:
        return [], []
    nodes = []
    for record in graph["nodes"]:
        if record["kind"] == "source":
            continue  # Source IDs are the existing document nodes.
        label = record.get("quote") or record.get("key") or record.get("method") or record["id"]
        nodes.append(
            {
                "id": record["id"],
                "kind": "knowledge_" + record["kind"],
                "label": label[:160],
                "document_id": record.get("document_version_id"),
                "epistemic_status": record.get("epistemic_status", record.get("status", "candidate")),
                "summary": {
                    key: record[key]
                    for key in (
                        "polarity",
                        "modality",
                        "applicable_on",
                        "identity_status",
                        "normalization_status",
                        "interpretation_status",
                        "category",
                    )
                    if key in record
                },
                "details": {
                    "tool": "knowledge_query",
                    "snapshot_id": snapshot,
                    "kind": record["kind"],
                    "group_id": record["id"],
                },
            }
        )
    edges = [
        {
            "id": edge["id"],
            "source": edge["from_node"],
            "target": edge["to_node"],
            "type": edge["relation"],
            "layer": "knowledge",
            "epistemic_status": edge.get("epistemic_status", "candidate_relationship"),
            "details": {
                "tool": "knowledge_query",
                "snapshot_id": snapshot,
                "kind": "edge",
                "group_id": edge["id"],
            },
        }
        for edge in graph["edges"]
    ]
    return nodes, edges


def verify(store, snapshot):
    """Check preserved evidence and graph structure; re-extract only matching rules."""
    manifest = store.manifest(snapshot)
    graph = load(store, snapshot)
    if graph is None:
        return
    with DiskGraph() as storage:
        for node in graph["nodes"]:
            storage.nodes[node["id"]] = node
        _verify_graph(store, manifest, graph, storage.nodes)


def _verify_graph(store, manifest, graph, records):
    graph_claims = {
        node["id"] for node in graph["nodes"] if node["kind"] in {"claim", "observation", "uncertainty"}
    }
    verified_claims = set()
    for doc in manifest["documents"]:
        data = read_knowledge(store, doc["knowledge"]["blob"])
        if data["signature"] != graph["signature"] or data["extraction_id"] != doc["extraction_id"]:
            raise ValueError("Knowledge document identity mismatch")
        extraction = store.one("SELECT * FROM extractions WHERE id=?", (doc["extraction_id"],))
        if data["artifact_sha"] != extraction["artifact_sha"]:
            raise ValueError("Knowledge artifact identity mismatch")
        text = json.loads(store.get(extraction["artifact_sha"]))["text"]
        segments = store.rows(
            "SELECT * FROM segments WHERE extraction_id=? ORDER BY ordinal", (extraction["id"],)
        )
        for claim in data["claims"] + data.get("observations", []) + data.get("uncertainties", []):
            if (
                claim["document_version_id"] != doc["document_version_id"]
                or claim["extraction_id"] != doc["extraction_id"]
                or claim["artifact_sha"] != extraction["artifact_sha"]
                or claim["original_blob_sha"] != doc["blob"]
                or claim["id"] in verified_claims
            ):
                raise ValueError("Knowledge claim identity mismatch")
            retained = records.get(claim["id"], {})
            if (
                any(retained.get(key) != value for key, value in claim.items())
                or retained.get("source_path") != doc["path"]
            ):
                raise ValueError("Knowledge graph claim mismatch")
            verified_claims.add(claim["id"])
            a, b = claim["canonical_start"], claim["canonical_end"]
            if not 0 <= a < b <= len(text) or claim["quote"] != text[a:b]:
                raise ValueError("Knowledge quotation mismatch")
            if source_refs(segments, a, b, text) != claim["sources"]:
                raise ValueError("Knowledge references mismatch")
            for reference in claim.get("context_sources", []):
                segment = next((s for s in segments if s["id"] == reference["segment_id"]), None)
                if (
                    segment is None
                    or segment["extraction_id"] != reference["extraction_id"]
                    or segment["text"][reference["start"] : reference["end"]] != reference["quote"]
                ):
                    raise ValueError("Knowledge context references mismatch")
        if data["signature"] == signature() and data != extract_document(store, doc, use_cache=False):
            raise ValueError("Knowledge derivation mismatch")
    if set(graph_claims) != verified_claims:
        raise ValueError("Knowledge graph claim inventory mismatch")
    ids = {node["id"] for node in graph["nodes"]}
    if len(ids) != len(graph["nodes"]) or any(
        e["from_node"] not in ids or e["to_node"] not in ids for e in graph["edges"]
    ):
        raise ValueError("Knowledge graph endpoint mismatch")
    if graph["signature"] == signature():
        with DiskGraph() as expected_storage:
            expected = graph_for(
                manifest["documents"],
                lambda doc: read_knowledge(store, doc["knowledge"]["blob"]),
                expected_storage,
            )
            from itertools import zip_longest

            missing = object()
            if any(
                a != b
                for kind in ("nodes", "edges")
                for a, b in zip_longest(graph[kind], expected[kind], fillvalue=missing)
            ):
                raise ValueError("Knowledge graph derivation mismatch")


def query(
    api,
    snapshot_id,
    *,
    kind="claim",
    entity=None,
    predicate=None,
    applicable_on=None,
    valid_at=None,
    group_id=None,
    concept=None,
    text=None,
    segment_id=None,
    cursor=None,
    limit=100,
):
    """Exhaustive stored records under explicit filters; no semantic completeness claim."""
    snapshot = api.store.snapshot(snapshot_id)
    if kind not in {
        "claim",
        "evidence_set",
        "observation",
        "concept",
        "entity",
        "conflict",
        "dependence",
        "source",
        "edge",
        "identity",
        "uncertainty",
    }:
        raise ValueError("Invalid knowledge kind")
    evidence_set = kind == "evidence_set"
    if evidence_set and (entity is None or any(v is not None for v in (group_id, concept, text, segment_id))):
        raise ValueError(
            "Evidence sets require an entity; only predicate and requested date may narrow context"
        )
    if predicate is not None and predicate not in set(PREDICATES.values()) | semantics.PREDICATES:
        raise ValueError("Invalid knowledge predicate")
    if applicable_on is not None:
        dates = date_readings(applicable_on) if isinstance(applicable_on, str) else []
        if len(dates) != 1:
            raise ValueError("Expected an unambiguous complete English/German calendar date")
        applicable_on = dates[0]
    if valid_at is not None:
        dates = date_readings(valid_at) if isinstance(valid_at, str) else []
        if len(dates) != 1:
            raise ValueError("Expected an unambiguous validity date")
        valid_at = dates[0]
    if kind not in {"claim", "evidence_set"} and any(
        v is not None for v in (entity, predicate, applicable_on, valid_at)
    ):
        raise ValueError("Entity, predicate and date filters require claim kind")
    if any(v is not None and (not isinstance(v, str) or not v or len(v) > 200) for v in (entity, group_id)):
        raise ValueError("Invalid knowledge filter")
    if any(
        v is not None and (not isinstance(v, str) or not v or len(v) > 2000)
        for v in (concept, text, segment_id)
    ):
        raise ValueError("Invalid knowledge search filter")
    if segment_id is not None and kind not in {"claim", "observation", "uncertainty", "edge"}:
        raise ValueError("Segment filter requires claim, observation, uncertainty or edge kind")
    if entity is not None and ":" in entity:
        namespace, value = entity.split(":", 1)
        entity = ENTITY_TYPES.get(namespace.casefold(), namespace) + ":" + value
    graph = load(api.store, snapshot["id"])
    normalized_text_concepts = query_concepts(text) if text is not None else None
    scope = dict(
        kind="knowledge",
        record_kind=kind,
        entity=entity,
        predicate=predicate,
        applicable_on=applicable_on,
        valid_at=valid_at,
        group_id=group_id,
        concept=concept,
        text=text,
        segment_id=segment_id,
        text_filter="any recognized EN/DE vocabulary concept; casefold literal if none",
        query_normalizer_sha=signature()["modules"]["knowledge_language.py"],
        normalized_text_concepts=normalized_text_concepts,
        knowledge=graph["signature"] if graph else None,
    )
    rows = []
    role_counts = Counter()
    if graph:
        record_kind = "claim" if evidence_set else kind
        rows = (n for n in graph["edges" if kind == "edge" else "nodes"] if n["kind"] == record_kind)
        if record_kind == "claim":
            rows = (
                n
                for n in rows
                if (
                    entity is None
                    or n["entity_key"] == entity
                    or (evidence_set and n.get("object_key") == entity and n["predicate"] == "supersession")
                )
                and (
                    predicate is None
                    or n["predicate"] == predicate
                    or (evidence_set and n["predicate"] == "supersession")
                )
                and (evidence_set or applicable_on is None or n["applicable_on"] == applicable_on)
                and (
                    evidence_set
                    or valid_at is None
                    or semantics.temporal_relation(n, valid_at)
                    in {"within_stated_validity", "boundary_unresolved"}
                )
            )
        if evidence_set:
            groups = {
                node["id"]: node for node in graph["nodes"] if node["kind"] in {"conflict", "dependence"}
            }
            enriched = []
            for claim in rows:
                roles = [claim["polarity"] + "_statement", "modality_" + claim["modality"]]
                if claim["condition"]:
                    roles.append("qualified")
                if claim["attribution"] != "source_document":
                    roles.append("attribution_unresolved")
                day = claim["applicable_on"]
                roles.append(
                    "time_unknown_or_ambiguous"
                    if day is None
                    else "other_event_day"
                    if applicable_on and day != applicable_on
                    else "dated_statement"
                )
                membership = [groups[key] for key in claim.get("groups", [])]
                roles.extend(sorted({g["kind"] for g in membership}))
                role_counts.update(roles)
                relation = (
                    (
                        "unknown"
                        if day is None
                        else "same"
                        if day == applicable_on
                        else "before"
                        if day < applicable_on
                        else "after"
                    )
                    if applicable_on
                    else "not_requested"
                )
                enriched.append(
                    dict(
                        claim,
                        evidence_roles=roles,
                        related_groups=membership,
                        event_day_relation=relation,
                        validity_relation=semantics.temporal_relation(claim, valid_at or applicable_on)
                        if valid_at or applicable_on
                        else "not_requested",
                    )
                )
            rows = enriched
        if group_id is not None:
            rows = (
                n
                for n in rows
                if group_id in n.get("groups", []) or n.get("to_node") == group_id or n["id"] == group_id
            )

        def concepts_for(node):
            values = set(node.get("concepts", []))
            if node["kind"] == "concept":
                values.add(node["key"])
            return values

        if concept is not None:
            rows = (node for node in rows if concept in concepts_for(node))
        if text is not None:
            concepts = set(normalized_text_concepts)
            rows = (
                node
                for node in rows
                if (
                    concepts & concepts_for(node)
                    if concepts
                    else text.casefold() in (node.get("quote", "") + node.get("key", "")).casefold()
                )
            )
        if segment_id is not None:
            if kind == "edge":
                rows = _segment_edges(graph, rows, segment_id)
            else:
                rows = (
                    node for node in rows if any(ref["segment_id"] == segment_id for ref in node["sources"])
                )
    result = api.page(snapshot["id"], scope, rows, cursor, limit, presorted=True)
    result.update(
        knowledge_status="available" if graph else "unavailable_reingest_required",
        recorded_at=snapshot["created_at"],
        knowledge_blob=api.store.manifest(snapshot["id"]).get("knowledge", {}).get("blob"),
        coverage=graph["coverage"] if graph else None,
        limitations=graph["limitations"] if graph else LIMITATIONS,
        contract="all stored matches across pages; not all meanings in the sources",
        confidence_policy="unknown is null; relevance, decoder scores and truth are separate",
        supported_languages=["en", "de"],
    )
    if graph is None:
        result["total"] = None
    if evidence_set:
        result["evidence_set"] = {
            "roles": dict(role_counts),
            "requested_event_day": applicable_on,
            "requested_validity_day": valid_at,
            "temporal_policy": "retain every stored event day and unknown/ambiguous dates; roles distinguish them",
            "identity_policy": "literal entity key only; unrelated real entities may share it",
            "support_policy": "no source independence or truth inferred; inspect each source and shared-source group",
        }
    return result


def _segment_edges(graph, rows, segment_id):
    seeds = {
        node["id"]
        for node in graph["nodes"]
        if any(ref["segment_id"] == segment_id for ref in node.get("sources", []))
    }
    targets = {edge["to_node"] for edge in graph["edges"] if edge["from_node"] in seeds}
    selected = [
        edge
        for edge in rows
        if edge["from_node"] in seeds or edge["to_node"] in targets or edge["to_node"] in seeds
    ]
    endpoints = {edge[key] for edge in selected for key in ("from_node", "to_node")}
    sources = {node["id"]: node.get("sources", []) for node in graph["nodes"] if node["id"] in endpoints}
    for edge in selected:
        yield dict(
            edge,
            related_sources=[ref for key in (edge["from_node"], edge["to_node"]) for ref in sources[key]],
        )


def annotate_segments(store, snapshot, segments):
    """Bounded interpretation metadata alongside original text, including in app prompts."""
    graph = load(store, snapshot)
    if graph is None:
        return
    with DiskGraph() as storage:
        for record in graph["nodes"]:
            storage.nodes[record["id"]] = record
        _annotate_segments(graph, snapshot, segments, storage.nodes)


def _annotate_segments(graph, snapshot, segments, records):
    record_segments = {
        key: {ref["segment_id"] for ref in row.get("sources", [])} for key, row in records.items()
    }
    members = defaultdict(set)
    superseded = {link["to_node"] for link in graph["edges"] if link["relation"] == "STATES_SUPERSESSION_OF"}
    for link in graph["edges"]:
        if link["relation"] in {
            "IDENTITY_COMPARISON",
            "POSSIBLE_REPRODUCTION_MEMBER",
            "SHARED_PASSAGE_SIGNAL",
            "POTENTIAL_CONFLICT_MEMBER",
            "ABOUT_LITERAL_SURFACE",
            "STATES_SUPERSESSION_OF",
        }:
            members[link["to_node"]].update(record_segments.get(link["from_node"], set()))
    related = defaultdict(set)
    for key, ids in members.items():
        kind = records[key]["kind"]
        if kind == "entity" and key not in superseded:
            continue
        ordered = sorted(ids)
        for source in ordered:
            related[source].update([target for target in ordered[:65] if target != source][:64])
            if len(ordered) > 65:
                segments[source]["knowledge_related_omitted"] = (
                    segments[source].get("knowledge_related_omitted", 0) + len(ordered) - 65
                )
    for link in graph["edges"]:
        if link["relation"] == "POSSIBLE_ACTOR_RECORD":
            for source in record_segments.get(link["from_node"], ()):
                related[source].update(record_segments.get(link["to_node"], ()))
    for sid, targets in related.items():
        ordered = sorted(targets - {sid})
        segments[sid]["knowledge_related"] = ordered[:64]
        segments[sid]["knowledge_related_omitted"] = segments[sid].get("knowledge_related_omitted", 0) + max(
            0, len(ordered) - 64
        )
        segments[sid]["knowledge_related_policy"] = (
            "typed candidate context; identity, copying direction and truth unresolved"
        )
        segments[sid]["knowledge_related_omission_unit"] = (
            "relationship memberships, may overlap across groups"
        )
    groups = {n["id"]: n for n in graph["nodes"] if n["kind"] in {"conflict", "dependence"}}
    by_segment, observed_segments = defaultdict(list), defaultdict(list)
    for claim in graph["nodes"]:
        if claim["kind"] == "uncertainty":
            for ref in claim["sources"]:
                if ref["segment_id"] not in segments:
                    raise ValueError("Knowledge uncertainty outside snapshot sources")
                annotation = segments[ref["segment_id"]].setdefault(
                    "knowledge_uncertainty",
                    {
                        "items": [],
                        "total": 0,
                        "remaining": 0,
                        "continuation": {
                            "tool": "knowledge_query",
                            "snapshot_id": snapshot,
                            "kind": "uncertainty",
                            "segment_id": ref["segment_id"],
                        },
                    },
                )
                annotation["total"] += 1
                item = {
                    "id": claim["id"],
                    "confidence": {
                        key: claim["confidence"].get(key)
                        for key in (
                            "score",
                            "withheld",
                            "reasons",
                            "calibrated_probability",
                            "calibration_status",
                        )
                    },
                    "uncertain_span_count": len(claim["confidence"].get("uncertain_spans", [])),
                    "continuation": {
                        "tool": "knowledge_query",
                        "snapshot_id": snapshot,
                        "kind": "uncertainty",
                        "group_id": claim["id"],
                    },
                }
                if len(annotation["items"]) < 8 and len(dump(annotation["items"] + [item]).encode()) <= 4000:
                    annotation["items"].append(item)
                annotation["remaining"] = annotation["total"] - len(annotation["items"])
        if claim["kind"] in {"claim", "observation"}:
            for ref in claim["sources"]:
                target = by_segment if claim["kind"] == "claim" else observed_segments
                target[ref["segment_id"]].append(claim["id"])
    for sid in sorted(set(by_segment) | set(observed_segments)):
        claims = [records[key] for key in by_segment[sid]]
        observations = [records[key] for key in observed_segments[sid]]
        if sid not in segments:
            raise ValueError("Knowledge claim outside snapshot sources")
        items = []
        for claim in claims:
            item = {
                k: claim[k]
                for k in (
                    "id",
                    "entity_key",
                    "predicate",
                    "polarity",
                    "modality",
                    "condition",
                    "applicable_on",
                    "actor_surface",
                    "attribution",
                    "confidence",
                    "sources",
                )
            }
            item["groups"] = [groups[g] for g in claim.get("groups", [])]
            item.update(
                {
                    key: claim[key]
                    for key in (
                        "language",
                        "extraction_rule",
                        "negation_scope",
                        "applicable_date_surface",
                        "applicable_date_candidates",
                        "entity_type_surface",
                        "validity",
                        "temporal_anchor",
                        "participants",
                        "amount",
                        "event_type",
                        "object_key",
                        "evidence_role",
                        "revision_state",
                        "speaker_surface",
                        "context_sources",
                        "discourse_status",
                        "source_reading",
                    )
                    if key in claim
                }
            )
            if len(items) >= 4 or len(dump(items + [item]).encode()) > 4000:
                break
            items.append(item)
        segments[sid]["knowledge"] = {
            "epistemic_status": "unreviewed_interpretations; identity and truth unresolved",
            "items": items,
            "total": len(claims),
            "remaining": len(claims) - len(items),
            "signature": graph["signature"],
            "continuation": {
                "tool": "knowledge_query",
                "snapshot_id": snapshot,
                "kind": "claim",
                "segment_id": sid,
            },
        }
        claim_index = []
        for claim in claims:
            entry = {
                key: claim[key]
                for key in ("entity_key", "predicate", "polarity", "modality", "applicable_on", "attribution")
            }
            entry["qualified"] = bool(claim["condition"])
            if entry in claim_index:
                continue
            if len(claim_index) >= 64 or len(dump(claim_index + [entry]).encode()) > 8000:
                break
            claim_index.append(entry)
        segments[sid]["knowledge_claim_index"] = claim_index
        segments[sid]["knowledge_claim_index_complete"] = all(
            {
                **{
                    key: c[key]
                    for key in (
                        "entity_key",
                        "predicate",
                        "polarity",
                        "modality",
                        "applicable_on",
                        "attribution",
                    )
                },
                "qualified": bool(c["condition"]),
            }
            in claim_index
            for c in claims
        )
        observed = []
        for observation in observations:
            item = {
                key: value
                for key, value in observation.items()
                if key
                not in {
                    "original_blob_sha",
                    "source_path",
                    "artifact_sha",
                    "extraction_id",
                    "document_version_id",
                }
            }
            if len(observed) >= 8 or len(dump(observed + [item]).encode()) > 4000:
                break
            observed.append(item)
        segments[sid]["knowledge"].update(
            observations=observed,
            observation_total=len(observed_segments[sid]),
            observations_remaining=len(observed_segments[sid]) - len(observed),
            observations_continuation={
                "tool": "knowledge_query",
                "snapshot_id": snapshot,
                "kind": "observation",
                "segment_id": sid,
            },
        )
        concepts = sorted({key for record in [*observations, *claims] for key in record.get("concepts", [])})
        segments[sid]["knowledge_concepts"] = concepts[:256]
        segments[sid]["knowledge_concepts_remaining"] = max(0, len(concepts) - 256)


def discovery_hint(manifest, snapshot, segments):
    """Direct readers to qualifications and contrary candidates in the full set."""
    if "knowledge" not in manifest:
        return {"status": "unavailable_reingest_required", "tool": "knowledge_query"}
    entities = sorted(
        {n["entity_key"] for segment in segments for n in segment.get("knowledge", {}).get("items", [])}
    )
    return {
        "status": "available",
        "tool": "knowledge_query",
        "snapshot_id": snapshot,
        "entities": entities[:20],
        "remaining_entities": max(0, len(entities) - 20),
        "scope": "bounded hints from retained passage annotations; enumerate entity kind for all stored surfaces",
        "instruction": "Query evidence_set kind by entity to include conditions, other/unknown dates and potential conflicts. "
        "Follow every next_cursor; groups are candidates, not confirmed identities or facts.",
    }
