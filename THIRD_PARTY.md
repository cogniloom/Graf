# Third-party software and models

Original Graf code is MIT licensed. Dependencies retain their own terms. The dependency lockfiles are the authoritative version inventory; the release archive also includes collected frontend license notices.

| Component | Role | License/source |
|---|---|---|
| Cosmograph 2.5.1 | Graph visualization | CC BY-NC 4.0 for non-commercial use; [licensing](https://cosmograph.app/licensing/), [source](https://github.com/cosmograph-org/cosmograph) |
| LadybugDB 0.21.0 | Native immutable graph storage and traversal | MIT; installed package notices; [source](https://github.com/LadybugDB/ladybug-python) |
| PostgreSQL 17 | Durable storage | [PostgreSQL License](https://www.postgresql.org/about/licence/) |
| BAAI BGE-M3 | Local embeddings | MIT; [model card](https://huggingface.co/BAAI/bge-m3) |
| BAAI bge-reranker-v2-m3 | Local reranking | Apache 2.0; [model card](https://huggingface.co/BAAI/bge-reranker-v2-m3) |
| DuckDB / DuckDB WASM | Local graph data engine | MIT; [source](https://github.com/duckdb/duckdb-wasm) |
| React | Dashboard | MIT; [source](https://github.com/facebook/react) |
| FastAPI / Uvicorn | Local API | MIT / BSD-3-Clause; project package notices |
| openpyxl / xlrd / olefile | Office readers | MIT / BSD / BSD; installed package notices |
| striprtf / defusedxml | RTF and safe XML readers | BSD-3-Clause / PSF; installed package notices |
| libarchive-c / libarchive | Streaming archive readers | CC0 / BSD; package and system-library notices |
| faster-whisper / CTranslate2 / Silero VAD | Optional offline speech recognition and activity detection | MIT; installed package notices; Whisper model weights are supplied separately |
| spaCy 3.8.16 / en_core_web_sm 3.8.0 / de_core_news_sm 3.8.0 | Optional local English/German CNN name and syntax candidates | MIT; installed package/model notices; models supplied as pinned wheels by the `knowledge-nlp` extra |
| Lingua 2.2.0 | Optional relative English/German language hints | Apache 2.0; installed package LICENSE; only English and German detectors are enabled |
| Mutagen | Media tags | GPL-2.0-or-later; installed package notices |
| antiword / FFmpeg | Optional legacy Word / media probing | System packages retain their own licenses; FFmpeg terms depend on build configuration |
| PyTorch / Transformers | Local model execution | BSD-style / Apache 2.0; project package notices |

Cosmograph attribution: Cosmograph, developed by Cosmograph contributors. The integration configures and styles the library; it does not claim authorship of it. CC BY-NC 4.0 terms are available at https://creativecommons.org/licenses/by-nc/4.0/. A commercial user must obtain appropriate Cosmograph rights or replace that component. The fact that Graf is distributed without charge does not authorize every downstream use.

Retrieval model weights are downloaded separately at pinned commits, not bundled into the release. The optional knowledge CNN models use release wheels pinned with hashes in `evidencekg/uv.lock`; they are not downloaded during ingestion. Model licenses and cards remain available with their upstream repositories. Review all applicable licenses before redistribution.
