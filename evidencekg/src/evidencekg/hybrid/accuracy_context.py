"""Experimental additive source view: retain seeds and expand complete functions.

No gold answers, filesystem reads, model-generated summaries, or source execution.
Budget exhaustion is explicit; original and enriched evidence may never be dropped.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict

from evidencekg.db import dump
from evidencekg.hybrid.code_context import _documents, _references, _role, build_code_context


def _merge(spans):
    result = []
    for start, end in sorted(set(spans)):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _functions(tree, offsets):
    """Whole functions plus exact enclosing suite headers, including guards."""
    result = []

    def walk(node, parents=()):
        line = getattr(node, "lineno", None)
        if isinstance(node, ast.match_case):
            line = node.pattern.lineno
        if line is None:
            return
        decorators = getattr(node, "decorator_list", [])
        start = offsets[min([line] + [d.lineno for d in decorators]) - 1]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append((start, offsets[node.end_lineno], parents))
        body = getattr(node, "body", [])
        if not isinstance(body, list) or not body:
            if isinstance(node, ast.Match):
                for case in node.cases:
                    # Earlier cases can shadow this one. Keep their exact source,
                    # rather than displaying the selected pattern in isolation.
                    prefix = (start, offsets[case.body[0].lineno - 1])
                    walk(case, parents + (prefix,))
            return
        header = (start, offsets[body[0].lineno - 1])
        for child in body:
            walk(child, parents + (header,))
        for handler in getattr(node, "handlers", []):
            # Earlier handlers have precedence over later matching handlers.
            h = (start, offsets[handler.body[0].lineno - 1])
            for child in handler.body:
                walk(child, parents + (header, h))
        for field in ("orelse", "finalbody"):
            branch = getattr(node, field, [])
            if branch:
                h = (start, offsets[branch[0].lineno - 1])
                for child in branch:
                    walk(child, parents + (header, h))

    for node in tree.body:
        walk(node)
    return result


def build_accuracy_context(segments, question, packet, *, max_bytes=240000, reranker=None):
    """Return verified contiguous regions, retaining the complete original retrieval.

    Ranking is the existing experimental code view. Expansion adds any complete
    Python function intersecting selected evidence, including decorators and all
    branches. It is lexical containment, not proof of runtime reachability.
    """
    if type(max_bytes) is not int or not 10000 <= max_bytes <= 1000000:
        raise ValueError("Accuracy context budget must be 10000..1000000 bytes")
    enriched = build_code_context(segments, question, packet, reranker=reranker)
    documents = _documents(segments)
    spans = defaultdict(list)
    priority = []

    def seed(doc, start, end):
        if start < end:
            spans[doc].append((start, end))
            priority.append((doc, start, end))

    for part in packet["segments"]:
        seed(part["document_version_id"], part["char_start"], part["char_end"])
    for item in enriched["evidence"]:
        for region in [item] + item.get("enclosing_source", []):
            for ref in region["source_refs"]:
                part = segments[ref["segment_id"]]
                seed(part["document_version_id"], part["char_start"] + ref["start"],
                     part["char_start"] + ref["end"])
    spans = {doc: _merge(values) for doc, values in spans.items()}
    result = dict(version="python-accuracy-context-v1", snapshot_id=packet.get("snapshot_id"),
                  scope="Original retrieval plus navigation evidence and enclosing functions; not exhaustive.",
                  reading_instructions=enriched["reading_instructions"] + [
                      "Each region is contiguous source. Later assignments and exception branches can override earlier values.",
                      "Use the implementation named in the question; similar names in other packages can have different behavior.",
                  ], evidence=[], navigation=enriched["navigation"], limitations=dict(
                      original_passages_retained=len(packet["segments"]),
                      complete_function_expansions=0, omitted_function_expansions=0,
                      enrichment=enriched["limitations"]))

    def render():
        result["evidence"] = [dict(path=documents[doc]["path"], document_version_id=doc,
            role=_role(documents[doc]["path"]), regions=[dict(
                text=documents[doc]["text"][lo:hi], source_refs=_references(documents[doc], lo, hi))
                for lo, hi in ranges]) for doc, ranges in spans.items()]
        return len(dump(result).encode())

    if render() > max_bytes - 1000:
        raise ValueError("Accuracy budget cannot retain all original and enriched evidence")
    functions = {}
    for doc in spans:
        source = documents[doc]
        if not source["path"].removesuffix(".txt").endswith(".py") or len(source["text"].encode()) > 1000000:
            continue
        try:
            tree = ast.parse(source["text"])
        except (SyntaxError, ValueError, RecursionError):
            continue
        offsets = [0]
        for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source["text"]):
            offsets.append(offsets[-1] + len(line))
        functions[doc] = _functions(tree, offsets)
    candidates = list(dict.fromkeys((doc, lo, hi, parents) for doc, start, end in priority
        for lo, hi, parents in functions.get(doc, []) if lo < end and hi > start))
    for doc, lo, hi, parents in candidates:
        additions = [(lo, hi)] + [(start, end) for start, end in parents if start < end]
        if all(any(start <= a and end >= b for start, end in spans[doc]) for a, b in additions):
            continue
        previous = spans[doc]
        spans[doc] = _merge(previous + additions)
        if render() > max_bytes - 1000:
            spans[doc] = previous
            result["limitations"]["omitted_function_expansions"] += 1
        else:
            result["limitations"]["complete_function_expansions"] += 1
    if render() > max_bytes:
        raise ValueError("Accuracy context metadata exceeds budget")
    return result
