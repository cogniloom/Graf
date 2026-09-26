# Third-party software and models

Original Docworm code is MIT licensed. Dependencies retain their own terms. The dependency lockfiles are the authoritative version inventory; the release archive also includes collected frontend license notices.

| Component | Role | License/source |
|---|---|---|
| Cosmograph 2.5.1 | Graph visualization | CC BY-NC 4.0 for non-commercial use; [licensing](https://cosmograph.app/licensing/), [source](https://github.com/cosmograph-org/cosmograph) |
| PostgreSQL 17 | Durable storage | [PostgreSQL License](https://www.postgresql.org/about/licence/) |
| BAAI BGE-M3 | Local embeddings | MIT; [model card](https://huggingface.co/BAAI/bge-m3) |
| BAAI bge-reranker-v2-m3 | Local reranking | Apache 2.0; [model card](https://huggingface.co/BAAI/bge-reranker-v2-m3) |
| DuckDB / DuckDB WASM | Local graph data engine | MIT; [source](https://github.com/duckdb/duckdb-wasm) |
| React | Dashboard | MIT; [source](https://github.com/facebook/react) |
| FastAPI / Uvicorn | Local API | MIT / BSD-3-Clause; project package notices |
| PyTorch / Transformers | Local model execution | BSD-style / Apache 2.0; project package notices |

Cosmograph attribution: Cosmograph, developed by Cosmograph contributors. The integration configures and styles the library; it does not claim authorship of it. CC BY-NC 4.0 terms are available at https://creativecommons.org/licenses/by-nc/4.0/. A commercial user must obtain appropriate Cosmograph rights or replace that component. The fact that Docworm is distributed without charge does not authorize every downstream use.

Model weights are downloaded separately at pinned commits, not bundled into the release. Their licenses and model cards remain available with the upstream repositories. Review all applicable licenses before redistribution.
