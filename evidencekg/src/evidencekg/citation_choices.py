"""Resolve model-selected immutable citation IDs, without repairing quotations."""

import json

from .db import dump, ident
from .validation import REF, validate_refs


class SelectedCitationResult(dict):
    def __init__(self, resolved, raw):
        super().__init__(resolved)
        self.raw_citation_output = raw


def choices(payload):
    result = {}
    segments = {s["id"]: s for s in payload.get("segments", [])}
    for segment in segments.values():
        for span in segment.get("citation_spans", []) + segment.get("citation_extra_spans", []):
            ref = {**span, "segment_id": segment["id"], "extraction_id": segment["extraction_id"]}
            cid = ref.pop("citation_id")
            validate_refs([ref], segments)
            if cid != ident("Q", segment["id"], ref["start"], ref["end"]) or cid in result:
                raise ValueError("Invalid or duplicate citation choice")
            result[cid] = ref
    return result


def selection_schema(schema, catalog):
    schema = json.loads(dump(schema))

    def visit(node):
        if isinstance(node, dict):
            if set(node.get("properties", {})) == set(REF["properties"]):
                node.clear()
                node["$ref"] = "#/$defs/citation_choice"
                return
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    schema.setdefault("$defs", {})["citation_choice"] = {
        "type": "object",
        "properties": {"citation_id": {"type": "string", "enum": sorted(catalog)}},
        "required": ["citation_id"],
        "additionalProperties": False,
    }
    return schema


def resolve(raw, payload):
    catalog = choices(payload)

    def visit(node):
        if isinstance(node, dict):
            if "citation_id" in node:
                if set(node) != {"citation_id"} or node["citation_id"] not in catalog:
                    raise ValueError("Unknown or malformed citation choice")
                return dict(catalog[node["citation_id"]])
            return {key: visit(value) for key, value in node.items()}
        if isinstance(node, list):
            return [visit(value) for value in node]
        return node

    return visit(raw)
