"""Build readable tables from the verified comparison export; no inferred winners."""

import argparse
import json
from pathlib import Path

NAMES = {"graf": "Graf", "trustgraph": "TrustGraph", "lightrag": "LightRAG",
         "cognee": "Cognee", "graphiti": "Graphiti"}


def number(value, precision=2):
    return "—" if value is None else f"{value:,.{precision}f}"


def total(usage):
    return None if usage is None else usage["input_tokens"] + usage["output_tokens"]


def build(root):
    rows = json.loads((root / "summary.json").read_text())
    rows.sort(key=lambda row: list(NAMES).index(row["system"]))
    first = rows[0]
    primary = ["| System | Indexing interval ↓ | Index LLM tokens ↓ | Native context median ↓ | Context + answer median ↓ | Mean query tokens ↓ | Strict passes ↑ |",
               "|---|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        quality = f"{row['strict_passes']}/{row['planned_queries']}" if row["graded_queries"] == row["planned_queries"] else f"{row['graded_queries']}/{row['planned_queries']} graded"
        suffix = "" if row["status"] == "complete" else " (incomplete)"
        primary.append(f"| **{NAMES[row['system']]}**{suffix} | {number(row['ingest_seconds'])} s | {number(total(row['ingest_usage']), 0)} | {number(row['median_retrieval_seconds'])} s | {number(row['median_end_to_end_seconds'])} s | {number(row['mean_query_tokens'], 0)} | {quality} |")
    title = "# Graf vs. TrustGraph, LightRAG, Cognee and Graphiti\n\n"
    intro = (f"Measured local comparison · 27 September 2026 · **GPT-6-luna, high reasoning**. "
             f"All systems receive the same **{first['corpus_files']} fictional operational documents "
             f"({first['source_bytes']:,} UTF-8 bytes)** and {first['planned_queries']} questions. "
             "One answering pass and two repeated retrieval passes; CPU execution and a shared workstation.\n\n")
    caveat = ("**Scope:** this is a small configuration comparison with authored questions and same-model automated grading; "
              "human review is pending. Different local embeddings, native context formats and the common Codex "
              "subscription transport affect results. These are not native provider API timings, large-corpus "
              "scalability results, dollar costs or a universal product ranking.\n\n"
              "**TrustGraph:** its native GraphRAG service includes synthesis before the common final answer. "
              "Both are counted here; ordinary TrustGraph use need not generate two answers. Its indexing interval "
              "includes a 30-second quiescence check. LightRAG's indexing start overlapped two already-submitted "
              "TrustGraph smoke calls, with 2.799 seconds of recorded gateway queue wait. Query runs were sequential.\n\n")
    details = ["## Latency, repeated queries and reliability", "",
               "Repeated measurements use the exact same questions. They measure warm/cache behavior, not independent accuracy trials. p95 is nearest-rank and is unstable with this small question set.", "",
               "| System | First-pass context p95 | Repeated context median | Repeated context p95 | Repeated queries | Completed responses / planned | Unattempted | Usage accounting |",
               "|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        details.append(f"| {NAMES[r['system']]} | {number(r['p95_retrieval_seconds'])} s | {number(r['median_warm_retrieval_seconds'], 3)} s | {number(r['p95_warm_retrieval_seconds'], 3)} s | {r['warm_retrieval_queries']} | {r['answered_queries']}/{r['planned_queries']} | {r['unattempted_queries']} | {r['token_usage_status']} |")
    details += ["", "Latency distributions contain answered queries only. Failed/unknown operations and unattempted questions remain explicit in the per-query data; they are never zero-duration successes.", "",
                "## Generative usage by stage", "",
                "Cached input is part of input, not an additional token category. Totals are input + output. Local embedding/reranking computation is reflected in elapsed time and partial memory measurements, not generative tokens. No subscription-token-to-dollar conversion is made.", "",
                "| System | Stage | Calls | Input | Cached input | Output | Total |", "|---|---|---:|---:|---:|---:|---:|"]
    for r in rows:
        for stage, usage, calls in [("Index", r["ingest_usage"], (r["ingest_usage"] or {}).get("calls")),
                                    ("Initial queries + answers", r["query_usage"], r["query_calls"]),
                                    ("Repeated retrieval", r["warm_retrieval_usage"], r["warm_retrieval_usage"]["calls"]),
                                    ("Automated grading (separate)", r["grading_usage"], r["grading_calls"])]:
            u = usage or {}
            details.append(f"| {NAMES[r['system']]} | {stage} | {number(calls, 0)} | {number(u.get('input_tokens'), 0)} | {number(u.get('cached_input_tokens'), 0)} | {number(u.get('output_tokens'), 0)} | {number(total(usage), 0)} |")
    details += ["", "## Automated quality breakdown", "",
                "Each column is a separate check, not an additive score. Judge correctness alone does not require the complete, citation-supported answer demanded by a strict pass. These are same-model judgments against authored references, with human adjudication pending.", "",
                "Completeness judgments can demand reference details beyond a concise direct answer. For example, Graf's doc-10 answer gives the requested delivery date and accepted-pump count correctly with valid citations, but the judge rejects completeness for omitting the total received and quarantined count. All original grades remain included, without post-result exclusions; strict passes should not be read as a human-adjudicated accuracy rate.", "",
                "| System | Judge: correct | Judge: complete | Judge: supported | Valid abstention behavior | Strict passes |",
                "|---|---:|---:|---:|---:|---:|"]
    for r in rows:
        scores = [json.loads(p.read_text()) for p in sorted(Path(r["run_path"]).glob("grading/query-*/score.json"))]
        counts = [sum(s["judge"][key] for s in scores) for key in ("correct", "complete", "supported")]
        counts += [sum(s["abstention_valid"] for s in scores), sum(s["strict_pass"] for s in scores)]
        values = [f"{count}/{len(scores)}" if scores else "Not graded" for count in counts]
        details.append(f"| {NAMES[r['system']]} | " + " | ".join(values) + " |")
    details += ["", "## Designated source evidence delivered", "",
                "Exact reference quotations must be present in the supplied context and bound to their original source through native retrieval provenance. This measures delivery of the authored reference evidence, not all relevant evidence or semantic completeness. Only answered queries are evaluated here; other outcomes remain explicit in the per-query file.", "",
                "| System | Queries with every required quotation | Required quotations delivered |",
                "|---|---:|---:|"]
    for r in rows:
        cases = f"{r['complete_evidence_queries']}/{r['evidence_evaluated_queries']}" if r['evidence_evaluated_queries'] else "Not measured"
        quotes = f"{r['designated_evidence_hits']}/{r['designated_evidence_total']}" if r['designated_evidence_total'] else "Not measured"
        details.append(f"| {NAMES[r['system']]} | {cases} | {quotes} |")
    details += ["", "## Context size and partial resource measurements", "",
                "| System | Mean supplied context characters | Truncated contexts | Valid citations / answered | Query tokens per strict pass | Adapter peak RSS | Adapter workdir |",
                "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        run = Path(r["run_path"])
        storage = json.loads((run / "storage.json").read_text())["adapter_workdir_bytes"] if (run / "storage.json").exists() else None
        memory = r["adapter_tree_peak_rss_bytes"]
        details.append(f"| {NAMES[r['system']]} | {number(r['mean_context_characters'], 0)} | {r['truncated_contexts']} | {r['valid_citations']}/{r['answered_queries']} | {number(r['query_tokens_per_strict_pass'], 0)} | {number(memory / 1024**2 if memory is not None else None, 1)} MiB | {number(storage / 1024**2 if storage is not None else None, 2)} MiB |")
    details += ["", "RSS is the sampled adapter process tree only: it excludes database containers, the shared inference/embedding service, remote inference and GPU memory. Workdir size excludes database volumes, Python packages and shared model weights, but includes any source copies. These are diagnostics, not total product footprints: each architecture places different work outside the measured process.", "",
                "## What was tested", "",
                "- Graf at repository revision `a899af2590c89a79cd6f626083a7fc928b04e2ac`, actual hybrid runtime with pinned BGE-M3 and BGE reranker on CPU, isolated PostgreSQL.",
                "- LightRAG 1.5.7, native mix graph/vector retrieval and local storage.",
                "- Cognee 1.6.1, native cognify + GRAPH_COMPLETION context, Ladybug/LanceDB/SQLite.",
                "- Graphiti 0.30.2, native text episodes and hybrid edge RRF, Neo4j 5.26.12.",
                "- TrustGraph 2.9.11, actual broker/database/extraction/GraphRAG stack and private Unix-socket model transport.",
                "- Competitor embeddings: pinned local all-MiniLM-L6-v2, 384 dimensions, CPU. Graf uses its own larger pinned product models. This does not isolate the graph contribution or hold embedding model size constant.", "",
                "Final context representations differ: Graf supplies original passages; LightRAG supplies source chunks plus its native data JSON, which can repeat those chunks; Cognee supplies source chunks plus native graph context; Graphiti supplies facts plus linked original episodes; TrustGraph supplies native synthesis plus linked original documents. Final-answer token totals include these adapter representation choices and are not the products' default answer-prompt token costs.", "",
                "All generation requests use GPT-6-luna/high via the existing ChatGPT subscription, including native extraction/query transforms and grading. CLI receipts do not independently attest the served model identity. Temperature/top-p/native token caps and provider-side schema enforcement are unavailable in the experimental CLI transport. Model-client retries and model fallback are disabled; Cognee retains its native bounded JSON-validation correction, with all completed-output attempts counted. Failed/unknown operations are retained.", "",
                "Indexing starts at the ingest request; process launch and startup work overlapping earlier receipt/intent setup are unmeasured. Prior package/container setup and shared model-weight downloads are excluded; native tokenizer downloads during adapter initialization remain included. The workstation is shared, CUDA was unavailable, and provider/OS caches were not flushed.", "",
                "Host: AMD Ryzen 9 5950X, 16 physical / 32 logical CPUs, 31.24 GiB RAM. Adapter environments set OMP_NUM_THREADS=4 and MKL_NUM_THREADS=4; this is not a CPU quota. The retained host snapshot had substantial pre-existing swap usage. These measurements are not an isolated hardware capacity test.", "",
                "## Audit and reproduction", "",
                "[Methodology and commands](../../competitors/README.md) · [Machine-readable summary](summary.json) · [Every planned query](per-query.csv) · [Answers and judge rationales](answers.json) · [PNG chart](comparison.png) · [SVG chart](comparison.svg)", "",
                "Raw prompts, responses, CLI events, native results, failures and source hashes are retained locally under `.evidencekg-benchmarks/competitors/`. Curated files alone are not a full raw-evidence distribution. Recorded harness versions for early runs are preserved under its `frozen-code/` directory with exact matching hashes. The independent harness review and focused recheck resolved seven accounting/quality-gate findings; this is not independent human adjudication of the answers."]
    if (root / "failed-attempts.json").exists():
        attempts = json.loads((root / "failed-attempts.json").read_text())
        details += ["", "## Retained full-corpus integration failures", "",
                    "These attempts are separate from the final configuration measurements above. Their known usage remains visible; they are not scored as product accuracy failures.", ""]
        for attempt in attempts:
            details.append(f"- {attempt['system']}: {attempt['description']} Known usage: {attempt['calls']} calls, {number(total(attempt['usage']), 0)} total tokens ({number(attempt['usage']['input_tokens'], 0)} input, {number(attempt['usage']['cached_input_tokens'], 0)} cached input, {number(attempt['usage']['output_tokens'], 0)} output).")
        details += ["", "[Failure accounting and original run identities](failed-attempts.json). All originally submitted model requests settled before the fresh configuration ran; the failed index was not reused."]
    report = title + intro + caveat + "![Graf compared with four graph systems](comparison.png)\n\n" + "\n".join(primary) + "\n\n" + "\n".join(details) + "\n"
    (root / "REPORT.md").write_text(report)
    answers = []
    for r in rows:
        run = Path(r["run_path"])
        dataset = Path(json.loads((run / "run.json").read_text())["dataset"])
        cases = {c["id"]: c for c in json.loads((dataset / "cases.json").read_text())}
        for folder in sorted(run.glob("query-*")):
            if (folder / "answer.json").exists():
                score = run / "grading" / folder.name / "score.json"
                grading = json.loads(score.read_text()) if score.exists() else None
                answers.append({"system": r["system"], "trial": folder.name,
                                "reference": cases[folder.name.split("-", 2)[2]],
                                "answer": json.loads((folder / "answer.json").read_text()),
                                "native_citation_aliases": grading["native_citation_aliases"] if grading else {},
                                "strict_pass": grading["strict_pass"] if grading else None,
                                "citations_valid": grading["citations_valid"] if grading else None,
                                "abstention_valid": grading["abstention_valid"] if grading else None,
                                "grading": grading["judge"] if grading else None})
    (root / "answers.json").write_text(json.dumps(answers, indent=2, ensure_ascii=False) + "\n")
    (root / "readme-table.md").write_text("\n".join(primary) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    build(parser.parse_args().results)
