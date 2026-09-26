# Measured results

[Read the measured pilot report](2026-09-26/REPORT.md), [view the chart](2026-09-26/comparison.png), or [inspect all 52 original trials](2026-09-26/per-case.csv).

Both arms requested GPT-6-astra at medium effort. Graf used its actual hybrid runtime; the baseline used controlled literal file search and reading without a graph.

| Valid paired workload | Mean tokens: baseline → Graf | Median seconds: baseline → Graf | Automated passes: baseline → Graf |
|---|---:|---:|---:|
| 1,000 fictional operational documents; 15 questions | 57,567 → 16,340 | 30.53 → 13.91 | 15/15 → 15/15 |
| 189 real source/doc files, 31,039 text lines; 9 questions | 80,823 → 64,613 | 44.99 → 41.17 | 5/9 → 8/9 |

The separate 10,000-document hybrid retrieval probe delivered all 16 designated evidence sets, with 1.97-second median retrieval and 18.34-second p95 including first-query model loading. This is retrieval coverage, not answer accuracy.

**Pilot limitations:** one repetition, authored questions, same-model automated grading and two symmetric rubric exclusions discovered during grading. All original results are retained. The code speed direction reverses when the excluded pair is included; do not claim a general code-speed advantage. The report includes setup costs, grading costs, uncached tokens, failures and original-denominator sensitivity. Human review and a fresh held-out run remain necessary for broad marketing claims.

The temporary PostgreSQL database was removed after measurements, with its recorded identity and absence verified. Raw evidence and frozen pilot implementation are retained in the ignored `.evidencekg-benchmarks/` directory. No results were published externally.

To rebuild this exact pilot's curated report from retained local evidence, run from the checkout root:

```sh
PYTHONPATH=evidencekg/src /path/to/ml-enabled/python benchmarks/export_pilot.py
uv run --no-project --with matplotlib python benchmarks/render_pilot.py benchmarks/results/2026-09-26
```

The exporter explicitly loads `.evidencekg-benchmarks/protocol-v2/`, verifies original receipts and recomputes the citation/abstention gates. Current corpus definitions correct the two rubric defects; new datasets are a new experiment and must not overwrite the old ones. The raw evidence directory is needed to rebuild; the checked-in summaries/chart alone are not a full raw-artifact distribution.
