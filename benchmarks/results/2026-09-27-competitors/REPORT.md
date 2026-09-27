# Graf vs. TrustGraph, LightRAG, Cognee and Graphiti

Measured local comparison · 27 September 2026 · **GPT-6-luna, high reasoning**. All systems receive the same **30 fictional operational documents (14,184 UTF-8 bytes)** and 16 questions. One answering pass and two repeated retrieval passes; CPU execution and a shared workstation.

**Scope:** this is a small configuration comparison with authored questions and same-model automated grading; human review is pending. Different local embeddings, native context formats and the common Codex subscription transport affect results. These are not native provider API timings, large-corpus scalability results, dollar costs or a universal product ranking.

**TrustGraph:** its native GraphRAG service includes synthesis before the common final answer. Both are counted here; ordinary TrustGraph use need not generate two answers. Its indexing interval includes a 30-second quiescence check. LightRAG's indexing start overlapped two already-submitted TrustGraph smoke calls, with 2.799 seconds of recorded gateway queue wait. Query runs were sequential.

![Graf compared with four graph systems](comparison.png)

| System | Indexing interval ↓ | Index LLM tokens ↓ | Native context median ↓ | Context + answer median ↓ | Mean query tokens ↓ | Strict passes ↑ |
|---|---:|---:|---:|---:|---:|---:|
| **Graf** | 23.25 s | 0 | 49.43 s | 56.49 s | 14,331 | 7/16 |
| **TrustGraph** | 927.50 s | 795,560 | 15.88 s | 23.24 s | 40,745 | 4/16 |
| **LightRAG** | 1,439.17 s | 951,249 | 4.44 s | 12.58 s | 36,593 | 7/16 |
| **Cognee** | 1,095.10 s | 859,680 | 0.25 s | 8.32 s | 14,900 | 6/16 |
| **Graphiti** | 1,411.64 s | 2,203,364 | 0.06 s | 7.40 s | 14,242 | 6/16 |

## Latency, repeated queries and reliability

Repeated measurements use the exact same questions. They measure warm/cache behavior, not independent accuracy trials. p95 is nearest-rank and is unstable with this small question set.

| System | First-pass context p95 | Repeated context median | Repeated context p95 | Repeated queries | Completed responses / planned | Unattempted | Usage accounting |
|---|---:|---:|---:|---:|---:|---:|---|
| Graf | 60.51 s | 0.020 s | 0.023 s | 32 | 16/16 | 0 | receipted |
| TrustGraph | 23.32 s | 15.115 s | 24.713 s | 32 | 16/16 | 0 | receipted |
| LightRAG | 8.67 s | 0.030 s | 0.036 s | 32 | 16/16 | 0 | receipted |
| Cognee | 1.28 s | 0.226 s | 0.510 s | 32 | 16/16 | 0 | receipted |
| Graphiti | 0.47 s | 0.055 s | 0.059 s | 32 | 16/16 | 0 | receipted |

Latency distributions contain answered queries only. Failed/unknown operations and unattempted questions remain explicit in the per-query data; they are never zero-duration successes.

## Generative usage by stage

Cached input is part of input, not an additional token category. Totals are input + output. Local embedding/reranking computation is reflected in elapsed time and partial memory measurements, not generative tokens. No subscription-token-to-dollar conversion is made.

| System | Stage | Calls | Input | Cached input | Output | Total |
|---|---|---:|---:|---:|---:|---:|
| Graf | Index | 0 | 0 | 0 | 0 | 0 |
| Graf | Initial queries + answers | 16 | 226,529 | 89,600 | 2,760 | 229,289 |
| Graf | Repeated retrieval | 0 | 0 | 0 | 0 | 0 |
| Graf | Automated grading (separate) | 16 | 207,919 | 134,400 | 3,741 | 211,660 |
| TrustGraph | Index | 60 | 766,886 | 537,600 | 28,674 | 795,560 |
| TrustGraph | Initial queries + answers | 48 | 645,744 | 430,080 | 6,175 | 651,919 |
| TrustGraph | Repeated retrieval | 64 | 864,179 | 555,520 | 5,783 | 869,962 |
| TrustGraph | Automated grading (separate) | 16 | 207,525 | 143,360 | 4,747 | 212,272 |
| LightRAG | Index | 60 | 893,614 | 474,880 | 57,635 | 951,249 |
| LightRAG | Initial queries + answers | 32 | 581,344 | 286,720 | 4,148 | 585,492 |
| LightRAG | Repeated retrieval | 0 | 0 | 0 | 0 | 0 |
| LightRAG | Automated grading (separate) | 16 | 207,952 | 134,400 | 4,601 | 212,553 |
| Cognee | Index | 62 | 820,721 | 555,520 | 38,959 | 859,680 |
| Cognee | Initial queries + answers | 16 | 235,308 | 134,400 | 3,085 | 238,393 |
| Cognee | Repeated retrieval | 0 | 0 | 0 | 0 | 0 |
| Cognee | Automated grading (separate) | 16 | 207,886 | 143,360 | 5,697 | 213,583 |
| Graphiti | Index | 151 | 2,168,629 | 1,358,080 | 34,735 | 2,203,364 |
| Graphiti | Initial queries + answers | 16 | 224,743 | 143,360 | 3,122 | 227,865 |
| Graphiti | Repeated retrieval | 0 | 0 | 0 | 0 | 0 |
| Graphiti | Automated grading (separate) | 16 | 207,167 | 143,360 | 3,516 | 210,683 |

## Automated quality breakdown

Each column is a separate check, not an additive score. Judge correctness alone does not require the complete, citation-supported answer demanded by a strict pass. These are same-model judgments against authored references, with human adjudication pending.

Completeness judgments can demand reference details beyond a concise direct answer. For example, Graf's doc-10 answer gives the requested delivery date and accepted-pump count correctly with valid citations, but the judge rejects completeness for omitting the total received and quarantined count. All original grades remain included, without post-result exclusions; strict passes should not be read as a human-adjudicated accuracy rate.

| System | Judge: correct | Judge: complete | Judge: supported | Valid abstention behavior | Strict passes |
|---|---:|---:|---:|---:|---:|
| Graf | 15/16 | 7/16 | 14/16 | 14/16 | 7/16 |
| TrustGraph | 16/16 | 6/16 | 12/16 | 14/16 | 4/16 |
| LightRAG | 15/16 | 8/16 | 15/16 | 16/16 | 7/16 |
| Cognee | 14/16 | 6/16 | 14/16 | 12/16 | 6/16 |
| Graphiti | 13/16 | 7/16 | 13/16 | 11/16 | 6/16 |

## Designated source evidence delivered

Exact reference quotations must be present in the supplied context and bound to their original source through native retrieval provenance. This measures delivery of the authored reference evidence, not all relevant evidence or semantic completeness. Only answered queries are evaluated here; other outcomes remain explicit in the per-query file.

| System | Queries with every required quotation | Required quotations delivered |
|---|---:|---:|
| Graf | 16/16 | 29/29 |
| TrustGraph | 13/16 | 25/29 |
| LightRAG | 15/16 | 28/29 |
| Cognee | 14/16 | 26/29 |
| Graphiti | 10/16 | 22/29 |

## Context size and partial resource measurements

| System | Mean supplied context characters | Truncated contexts | Valid citations / answered | Query tokens per strict pass | Adapter peak RSS | Adapter workdir |
|---|---:|---:|---:|---:|---:|---:|
| Graf | 6,183 | 0 | 16/16 | 32,756 | 3,818.7 MiB | 0.92 MiB |
| TrustGraph | 3,242 | 0 | 16/16 | 162,980 | 48.6 MiB | 0.01 MiB |
| LightRAG | 31,776 | 0 | 15/16 | 83,642 | 205.6 MiB | 6.90 MiB |
| Cognee | 8,081 | 0 | 15/16 | 39,732 | 1,073.8 MiB | 13.82 MiB |
| Graphiti | 5,658 | 0 | 14/16 | 37,978 | 113.3 MiB | 0.01 MiB |

RSS is the sampled adapter process tree only: it excludes database containers, the shared inference/embedding service, remote inference and GPU memory. Workdir size excludes database volumes, Python packages and shared model weights, but includes any source copies. These are diagnostics, not total product footprints: each architecture places different work outside the measured process.

## What was tested

- Graf at repository revision `a899af2590c89a79cd6f626083a7fc928b04e2ac`, actual hybrid runtime with pinned BGE-M3 and BGE reranker on CPU, isolated PostgreSQL.
- LightRAG 1.5.7, native mix graph/vector retrieval and local storage.
- Cognee 1.6.1, native cognify + GRAPH_COMPLETION context, Ladybug/LanceDB/SQLite.
- Graphiti 0.30.2, native text episodes and hybrid edge RRF, Neo4j 5.26.12.
- TrustGraph 2.9.11, actual broker/database/extraction/GraphRAG stack and private Unix-socket model transport.
- Competitor embeddings: pinned local all-MiniLM-L6-v2, 384 dimensions, CPU. Graf uses its own larger pinned product models. This does not isolate the graph contribution or hold embedding model size constant.

Final context representations differ: Graf supplies original passages; LightRAG supplies source chunks plus its native data JSON, which can repeat those chunks; Cognee supplies source chunks plus native graph context; Graphiti supplies facts plus linked original episodes; TrustGraph supplies native synthesis plus linked original documents. Final-answer token totals include these adapter representation choices and are not the products' default answer-prompt token costs.

All generation requests use GPT-6-luna/high via the existing ChatGPT subscription, including native extraction/query transforms and grading. CLI receipts do not independently attest the served model identity. Temperature/top-p/native token caps and provider-side schema enforcement are unavailable in the experimental CLI transport. Model-client retries and model fallback are disabled; Cognee retains its native bounded JSON-validation correction, with all completed-output attempts counted. Failed/unknown operations are retained.

Indexing starts at the ingest request; process launch and startup work overlapping earlier receipt/intent setup are unmeasured. Prior package/container setup and shared model-weight downloads are excluded; native tokenizer downloads during adapter initialization remain included. The workstation is shared, CUDA was unavailable, and provider/OS caches were not flushed.

Host: AMD Ryzen 9 5950X, 16 physical / 32 logical CPUs, 31.24 GiB RAM. Adapter environments set OMP_NUM_THREADS=4 and MKL_NUM_THREADS=4; this is not a CPU quota. The retained host snapshot had substantial pre-existing swap usage. These measurements are not an isolated hardware capacity test.

## Audit and reproduction

[Methodology and commands](../../competitors/README.md) · [Machine-readable summary](summary.json) · [Every planned query](per-query.csv) · [Answers and judge rationales](answers.json) · [PNG chart](comparison.png) · [SVG chart](comparison.svg)

Raw prompts, responses, CLI events, native results, failures and source hashes are retained locally under `.evidencekg-benchmarks/competitors/`. Curated files alone are not a full raw-evidence distribution. Recorded harness versions for early runs are preserved under its `frozen-code/` directory with exact matching hashes. The independent harness review and focused recheck resolved seven accounting/quality-gate findings; this is not independent human adjudication of the answers.

## Retained full-corpus integration failures

These attempts are separate from the final configuration measurements above. Their known usage remains visible; they are not scored as product accuracy failures.

- Cognee: Native ingestion failed after concurrent requests exceeded client timeout; all submitted model calls now receipted. Some responses completed after the client exited. Index remains failed and is never reused. Known usage: 61 calls, 838,852 total tokens (797,893 input, 537,600 cached input, 40,959 output).
- Cognee: Native ingestion failed on invalid schema JSON in a completed summary. All submitted model calls now receipted; index remains failed and is never reused. Known usage: 7 calls, 94,891 total tokens (90,952 input, 62,720 cached input, 3,939 output).
- Cognee: One-attempt native prompted-JSON ingestion halted on a completed summary containing an unescaped control character. All model requests receipted; failed index not reused. Known usage: 35 calls, 487,271 total tokens (463,270 input, 304,640 cached input, 24,001 output).

[Failure accounting and original run identities](failed-attempts.json). All originally submitted model requests settled before the fresh configuration ran; the failed index was not reused.
