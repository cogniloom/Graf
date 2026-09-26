import jsonschema


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


STR = {"type": "string"}
TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
REF = obj(
    {
        "segment_id": STR,
        "extraction_id": STR,
        "start": {"type": "integer", "minimum": 0},
        "end": {"type": "integer", "minimum": 0},
        "quote": STR,
    }
)
REFS = {"type": "array", "items": REF, "minItems": 1}
FINDING = obj(
    {
        "assertion": TEXT,
        "epistemic_status": {
            "type": "string",
            "enum": ["observation", "allegation", "interpretation", "uncertain"],
        },
        "sources": REFS,
        "uncertainty": STR,
    }
)
PROPOSAL = obj(
    {"relationship": TEXT, "subject": STR, "object": STR, "explanation": TEXT, "evidence_refs": REFS}
)
RESULT_SCHEMA = obj(
    {
        "task_id": STR,
        "input_sha": STR,
        "findings": {"type": "array", "items": FINDING},
        "interpretations": {"type": "array", "items": PROPOSAL},
        "unresolved_questions": {
            "type": "array",
            "items": {**TEXT, "maxLength": 4000},
            "maxItems": 100,
            "uniqueItems": True,
        },
        "uncertainty": STR,
        "modality_reviewed": {"type": "boolean"},
    }
)


def validate_refs(refs, segments):
    for ref in refs:
        REF_VALIDATOR.validate(ref)
        seg = segments.get(ref["segment_id"])
        if not seg or ref["extraction_id"] != seg["extraction_id"]:
            raise ValueError("Evidence is not in this task")
        lo, hi = ref["start"], ref["end"]
        if type(lo) is not int or type(hi) is not int or not 0 <= lo < hi <= len(seg["text"]):
            raise ValueError("Invalid citation span")
        if seg["text"][lo:hi] != ref["quote"]:
            raise ValueError("Fabricated or altered quotation")


def validate_result(result, task, payload):
    jsonschema.Draft202012Validator(schema_for_payload(payload)).validate(result)
    if result["task_id"] != task["id"] or result["input_sha"] != task["input_manifest_sha"]:
        raise ValueError("Wrong task/input identity")
    segments = {s["id"]: s for s in payload["segments"]}
    if payload.get("assurance_version"):
        validate_assurance(result, payload, segments)
    for finding in result["findings"]:
        validate_refs(finding["sources"], segments)
        if (
            finding["epistemic_status"] == "interpretation"
            and payload["kind"] in {"connection", "candidate_connection", "semantic_candidate"}
            and {r["segment_id"] for r in finding["sources"]} != set(segments)
        ):
            raise ValueError("Joint interpretation must cite all supplied premises")
    for proposal in result["interpretations"]:
        validate_refs(proposal["evidence_refs"], segments)
        if payload["kind"] in {"connection", "candidate_connection", "semantic_candidate"} and len(
            {r["segment_id"] for r in proposal["evidence_refs"]}
        ) < len(segments):
            raise ValueError("Joint interpretation must cite all supplied premises")
    if result["modality_reviewed"]:
        raise ValueError("Text-only task cannot certify visual review")


REF_VALIDATOR = jsonschema.Draft202012Validator(REF)
RESULT_VALIDATOR = jsonschema.Draft202012Validator(RESULT_SCHEMA)

# Separate schemas preserve frozen legacy runs and strict provider output contracts.
STRINGS = {"type": "array", "items": TEXT, "maxItems": 64, "uniqueItems": True}
OBSERVATION = obj(
    {
        "actors": STRINGS,
        "action_event": TEXT,
        "dates": {
            "type": "array",
            "items": obj(
                {
                    "value": TEXT,
                    "role": {"enum": ["event", "document", "uncertain"], "type": "string"},
                    "sources": REFS,
                }
            ),
            "maxItems": 64,
        },
        "obligations": STRINGS,
        "conditions": STRINGS,
        "negations": STRINGS,
        "issue_keys": STRINGS,
        "event_keys": STRINGS,
        "semantic_query_terms": STRINGS,
        "categories": STRINGS,
        "sources": REFS,
        "uncertainty": STR,
        "epistemic_status": {"type": "string", "enum": ["attributed_interpretation"]},
    }
)
CHECK = obj({"marker_id": TEXT, "assessment": TEXT, "sources": REFS})
ASSESSMENT = obj(
    {
        "target_id": TEXT,
        "verdict": {"type": "string", "enum": ["supported", "unsupported", "uncertain", "disagreement"]},
        "impact": {"type": "string", "enum": ["low", "high"]},
        "explanation": TEXT,
        "sources": REFS,
    }
)
ENHANCED_KINDS = (
    "source",
    "critical_wording",
    "connection",
    "candidate_connection",
    "reconsideration",
    "modality_gap",
    "inventory_gap",
    "semantic_candidate",
    "issue_reconsideration",
    "source_reconciliation",
    "premise_challenge",
)


def enhanced_schema(kind):
    properties = dict(RESULT_SCHEMA["properties"])
    properties.update(
        observations={"type": "array", "items": OBSERVATION, "maxItems": 64},
        wording_checks={"type": "array", "items": CHECK},
        assessments={"type": "array", "items": ASSESSMENT},
    )
    if kind != "critical_wording":
        properties["wording_checks"]["maxItems"] = 0
    if kind not in {"source_reconciliation", "premise_challenge"}:
        properties["assessments"]["maxItems"] = 0
    if kind == "premise_challenge":
        for field in ("findings", "interpretations", "observations"):
            properties[field] = {**properties[field], "maxItems": 0}
    return obj(properties)


STAGE_SCHEMAS = {kind: enhanced_schema(kind) for kind in ENHANCED_KINDS}


def schema_for_payload(payload):
    if payload.get("assurance_version"):
        return STAGE_SCHEMAS[payload["kind"]]
    return RESULT_SCHEMA


def validate_assurance(result, payload, segments):
    for observation in result["observations"]:
        validate_refs(observation["sources"], segments)
        for date in observation["dates"]:
            validate_refs(date["sources"], segments)
    expected = {m["id"]: m for m in payload["descriptor"].get("risk_markers", [])}
    seen = set()
    for check in result["wording_checks"]:
        validate_refs(check["sources"], segments)
        mid = check["marker_id"]
        if mid not in expected or mid in seen:
            raise ValueError("Unknown or duplicate critical wording marker")
        marker = expected[mid]
        if not any(
            r["segment_id"] == marker["segment_id"]
            and r["start"] <= marker["start"]
            and r["end"] >= marker["end"]
            for r in check["sources"]
        ):
            raise ValueError("Wording checklist must cite the marker")
        seen.add(mid)
    if seen != set(expected):
        raise ValueError("Missing critical wording checklist coverage")
    targets = {t["id"]: t for t in payload["descriptor"].get("targets", [])}
    seen = set()
    for assessment in result["assessments"]:
        validate_refs(assessment["sources"], segments)
        tid = assessment["target_id"]
        if tid not in targets or tid in seen:
            raise ValueError("Unknown or duplicate assessment target")
        required = set(targets[tid].get("premise_segment_ids", segments))
        if {r["segment_id"] for r in assessment["sources"]} != required:
            raise ValueError("Challenge/reconciliation must cite all source premises")
        seen.add(tid)
    if seen != set(targets):
        raise ValueError("Missing challenge/reconciliation assessment")
