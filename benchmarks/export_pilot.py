"""Rebuild the dated pilot report from preserved, verified local v2 artifacts."""

import csv
import json
import statistics
import sys
from pathlib import Path

base = Path(".evidencekg-benchmarks").resolve()
sys.path.insert(0, str(base / "protocol-v2"))
from benchmarks.analyze import analyze  # noqa: E402 -- intentionally use frozen pilot code
from benchmarks.runner import FileTools, load_trials, report, validate_citations  # noqa: E402

assert Path(report.__code__.co_filename).resolve().is_relative_to(base / "protocol-v2")
out = Path("benchmarks/results/2026-09-26")
out.mkdir(parents=True, exist_ok=True)
collected = {}
records = []
for label, folder in [("documents", "docs-v2"), ("code", "code-v2")]:
    run = base / (folder + "-hybrid-run")
    summary = report(run)
    quality = analyze(run)
    assert quality["complete_measured_and_graded"]
    config = json.loads((run / "run.json").read_text())
    dataset = Path(config["dataset"])
    cases = {c["id"]: c for c in json.loads((dataset / "cases.json").read_text())}
    tools = FileTools(dataset / "sources")
    paths, rows = load_trials(run, config)
    scores = {
        s["trial"]: s for p in (run / "grading").glob("score-*.json") for s in [json.loads(p.read_text())]
    }
    for path, row in zip(paths, rows):
        score = scores[path.parent.name]
        checks = validate_citations(row["answer"], tools)
        case = cases[row["case_id"]]
        citation_gate = checks["citation_count"] == checks["valid_citation_count"] and (
            checks["citation_count"] > 0 or not case["answerable"]
        )
        abstention_gate = row["answer"]["abstain"] is not case["answerable"]
        expected = (
            citation_gate
            and abstention_gate
            and all(score["grade"][k] for k in ["correct", "complete", "supported"])
        )
        assert (
            score["pass"] == expected
            and score["citation_gate"] == citation_gate
            and score["abstention_gate"] == abstention_gate
        )
        records.append(
            {
                "workload": label,
                "case_id": row["case_id"],
                "question": case["question"],
                "category": row["category"],
                "arm": row["arm"],
                "strict_pass": score["pass"],
                "calls": row["calls"],
                "seconds": row["seconds"],
                **row["usage"],
                "uncached_input_tokens": row["usage"]["input_tokens"] - row["usage"]["cached_input_tokens"],
                "valid_citations": row["valid_citation_count"],
                "citations": row["citation_count"],
            }
        )
    for suffix, data in [("summary", summary), ("quality", quality)]:
        (out / f"{label}-{suffix}.json").write_text(json.dumps(data, indent=2) + "\n")
    preparation = json.loads((dataset / "hybrid-preparation.json").read_text())
    collected[label] = {"summary": summary, "quality": quality, "config": config, "preparation": preparation}
with (out / "per-case.csv").open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(sorted(records, key=lambda r: (r["workload"], r["case_id"], r["arm"])))
# Exclude the discovered invalid gold item symmetrically; preserve original summaries.
primary = {}
for label in ["documents", "code"]:
    primary[label] = {"arms": {}}
    for arm in ["files", "graf"]:
        subset = [
            r
            for r in records
            if r["workload"] == label and r["arm"] == arm and r["case_id"] not in {"doc-16", "code-03"}
        ]
        primary[label]["arms"][arm] = {
            "scheduled": len(subset),
            "strict_passes": sum(r["strict_pass"] for r in subset),
            "answer_accuracy": sum(r["strict_pass"] for r in subset) / len(subset),
            "mean_input_tokens": statistics.mean(r["input_tokens"] for r in subset),
            "mean_output_tokens": statistics.mean(r["output_tokens"] for r in subset),
            "mean_cached_input_tokens": statistics.mean(r["cached_input_tokens"] for r in subset),
            "median_seconds": statistics.median(r["seconds"] for r in subset),
        }
    (out / f"{label}-primary.json").write_text(json.dumps(primary[label], indent=2) + "\n")
lines = [
    "# Graf benchmark: measured pilot",
    "",
    "These are observed results from the actual hybrid retrieval engine and real GPT-6-astra subscription calls. The baseline uses a controlled file-search/read agent. Both arms requested the same model, medium effort and a six-response limit. One repetition was run per question. The headline document comparison includes 15 valid question pairs; one defective document rubric item and one code rubric item were excluded symmetrically after grading exposed them; code headlines use 9 valid pairs.",
    "",
    "| Workload | Arm | Mean total model tokens | Mean uncached input | Median elapsed | Strict automated passes |",
    "|---|---|---:|---:|---:|---:|",
]
for label in ["documents", "code"]:
    item = collected[label]
    for arm, title in [("files", "File search"), ("graf", "Graf + file tools")]:
        a = primary[label]["arms"][arm]
        tokens = a["mean_input_tokens"] + a["mean_output_tokens"]
        lines.append(
            f"| {label.title()} | {title} | {tokens:,.0f} | {a['mean_input_tokens'] - a['mean_cached_input_tokens']:,.0f} | {a['median_seconds']:.2f} s | {a['strict_passes']}/{a['scheduled']} |"
        )
lines += [
    "",
    "Total model tokens include cached input and output. They are actual CLI usage, not character-based estimates or dollar charges. Elapsed time includes retrieval, tool work, process startup and model/network wait. Accuracy is strict automated judgment, with independent mechanical citation and abstention gates; it is not human-certified.",
    "",
    "## Sensitivity: all original trials",
    "",
    "No original trials were discarded from the evidence. Before symmetric rubric exclusions, documents averaged 56,608 vs 16,351 tokens and 30.53 vs 14.09 seconds median (file search vs Graf). Code averaged 78,949 vs 63,653 tokens and 43.85 vs 45.73 seconds median. Original strict scores were 15/16 vs 15/16 documents and 5/10 vs 9/10 code; the incorrect gold makes those accuracy denominators unsuitable for headline claims. The code speed direction changes after exclusion, so no broad code-speed claim is warranted.",
    "",
    "## What was tested",
    "",
    "- **Documents:** 1,000 fictional short operational records, 9,946 text lines and 548,291 bytes; 16 questions spanning amendments, conflicting policies, reconciliation, chronology, ambiguous identities, multilingual evidence and missing facts.",
    "- **Code:** 189 real source/configuration/documentation files at commit `00be47ad82e0fd126f571062dafd979b60e3a9bf`, with 31,039 physical text lines and 1,398,358 bytes; 10 questions about cross-module behavior and failure paths. This is not 31,039 executable lines or a million-line repository.",
    "- **Engine:** actual Graf hybrid Runtime with pinned local BGE-M3 embeddings, local reranking, graph/structural context and an isolated PostgreSQL database. This measures the whole retrieval stack, not the causal contribution of graph edges alone.",
    "- **Machine:** AMD Ryzen 9 5950X CPU, NVIDIA RTX 4060 Ti 16 GB, Linux; shared host. Model weights already existed. Answering runs were sequential across corpora; the later large-corpus GPU probe did not compete with answering.",
    "- **Protocol:** Graf retrieves the original question before the first model response. Both arms retain file tools. This is a controlled JSON tool loop, not an unrestricted native Codex terminal session.",
    "",
    "## Why Graf helped in this pilot",
    "",
    "Graf supplies relevant passages before the first model response, which can remove repeated search/read/model turns. This was useful on the short operational-document tasks. The code latency result is sensitive to the rubric exclusion: across all 10 original questions, Graf used fewer tokens but had a higher median latency (45.73 vs 43.85 seconds); across the 9 valid pairs, its median is lower (41.17 vs 44.99 seconds). This is not robust evidence of a general code-speed advantage. Retrieval and local reranking have a cost, and broad code investigation may still need several source reads. These observations support a workload-specific benefit, not an automatic advantage on every repository.",
    "",
    "## Setup and evaluation costs",
    "",
    "| Workload | Ingestion | Hybrid preparation | Model download |",
    "|---|---:|---:|---:|",
]
for label in ["documents", "code"]:
    item = collected[label]
    lines.append(
        f"| {label.title()} | {item['config']['ingestion_seconds']:.2f} s | {item['preparation']['total_preparation_seconds']:.2f} s | 0 s; weights reused |"
    )
lines += [
    "",
    "These setup costs are additional to answer latency. Indexing/preparation made zero generative-model calls but consumed local compute. First queries include lazy model loading. Database provisioning and initial software/model installation are not measured in these setup totals.",
    "",
]
for label in ["documents", "code"]:
    item = collected[label]
    g = item["summary"]["grading"]
    lines.append(
        f"- {label.title()} grading: {g['calls']} separate judge calls, {g['input_tokens']:,} input tokens, {g['output_tokens']:,} output tokens and {g['seconds']:.1f} summed seconds. These are excluded from answering usage."
    )
lines += [
    "",
    "## Quality-conditioned comparisons",
    "",
    "Positive savings below mean file-search usage minus Graf usage. These subsets include only paired questions where both arms passed; overall pass counts above keep the full denominator. The small, authored sample does not support population-wide claims.",
    "",
    "| Workload | Both-pass pairs | Mean seconds saved on both-pass subset | Mean total tokens saved on both-pass subset |",
    "|---|---:|---:|---:|",
]
for label in ["documents", "code"]:
    bycase = {}
    for r in records:
        if r["workload"] == label and r["case_id"] not in {"doc-16", "code-03"}:
            bycase.setdefault(r["case_id"], {})[r["arm"]] = r
    pairs = [p for p in bycase.values() if all(r["strict_pass"] for r in p.values())]
    t = statistics.mean(p["files"]["seconds"] - p["graf"]["seconds"] for p in pairs) if pairs else None
    n = (
        statistics.mean(
            p["files"]["input_tokens"]
            + p["files"]["output_tokens"]
            - p["graf"]["input_tokens"]
            - p["graf"]["output_tokens"]
            for p in pairs
        )
        if pairs
        else None
    )
    lines.append(
        f"| {label.title()} | {len(pairs)} | {'unscored' if t is None else f'{t:.2f} s'} | {'unscored' if n is None else f'{n:,.0f}'} |"
    )
lines += [
    "",
    "The original JSON analyses retain question-cluster bootstrap intervals and category-level results for all original questions, including the defective items; they are retained as audit artifacts, not the corrected headline comparison. With one repetition and hand-authored tasks, these are descriptive pilot statistics, not a universal speed guarantee.",
    "",
    "## Scale and limitations",
    "",
]
scale_path = base / "docs10000-v2-hybrid-scale/summary.json"
if scale_path.exists():
    scale = json.loads(scale_path.read_text())
    (out / "hybrid-10000-scale.json").write_text(json.dumps(scale, indent=2) + "\n")
    lines += [
        f"The real hybrid engine retrieved all 16 original questions against **{scale['files']:,} short documents / {scale['lines']:,} lines**. Ingestion took **{scale['ingestion_seconds']:.2f} s**, hybrid preparation **{scale['hybrid_preparation_seconds']:.2f} s**, median retrieval **{scale['median_seconds']:.2f} s** and p95 **{scale['p95_seconds']:.2f} s**. It delivered **{scale['complete_designated_sets']}/{scale['query_count']} complete designated reference sets**, with **{scale['cache_hits']} cached results**. This scale probe made no generative calls and measures retrieval coverage, **not final-answer accuracy at 10,000 documents**.",
        "",
    ]
lines += [
    "The earlier tool-availability calibration was stopped after 28 completed trials because the model never invoked Graf. Its receipts and exact source snapshot are retained and excluded from comparison. An older mechanical/lexical scale probe was stopped after 861.6 seconds without complete aggregates; it is not a measurement of the current hybrid engine.",
    "",
    "The documents are fictional text records, not real customer PDFs or OCR workloads. Source code was exported losslessly to supported text formats; no AST or call-graph analysis was added. Provider/OS caches were not flushed, the effective server model identity is not independently attested by CLI events, and same-model automated judging can make correlated mistakes. Human review packets are available locally. Use any marketing wording only with the tested workload, baseline, sample size and automated-grading qualification; these data do not establish general customer accuracy or a blanket speed/cost advantage.",
    "",
    "## Rubric correction",
    "",
    "Question doc-16 asks whether RP-9 establishes financial-ledger retention. The source explicitly excludes ledgers, so “No” is an answer supported by the evidence. The authored rubric incorrectly set answerable=false, which mechanically penalized a correct non-abstaining answer. This was discovered during grading, before selecting headline results. The code-03 reference also incorrectly says p.wait() reaps the entire process group; it waits for the parser child after a group kill. Both arms of doc-16 and code-03 are excluded from the primary comparison; all 52 original trials and original grades remain in the CSV/raw summaries. This post-run exclusion and the small pilot require a fresh validation run before broad marketing claims. The corpus builder is corrected for subsequent datasets: doc-16 is answerable and code-03 accurately describes waiting for the parser child.",
    "",
    "![Measured comparison](comparison.png)",
    "",
    "## Reproduce and audit",
    "",
    "- [Runner and commands](../../README.md)",
    "- [Methodology](../../METHODOLOGY.md)",
    "- [All per-case measurements](per-case.csv)",
    "- [Documents: raw summary](documents-summary.json) and [quality-conditioned analysis](documents-quality.json)",
    "- [Code: raw summary](code-summary.json) and [quality-conditioned analysis](code-quality.json)",
    "- [Verification and existing failures](../VERIFICATION.md)",
    "- Raw prompts, tool packets, provider events, seals, judge calls and blinded review packets are retained under `.evidencekg-benchmarks/` in this checkout. Nothing was published externally.",
    "",
]
(out / "environment.json").write_text((base / "v2-environment.json").read_text())
(out / "REPORT.md").write_text("\n".join(lines))
print(json.dumps({label: collected[label]["summary"]["arms"] for label in collected}, indent=2))
