# Benchmark verification — 26 September 2026

## Implementation checks

- **49 benchmark tests passed**, including actual mechanical graph ingestion/discovery, mandatory initial Graf context delivery, corpus determinism and byte preservation, all reference quotes, path boundaries, token accounting, invalid-citation rejection despite a positive judge, missing-trial rejection, raw-event tampering, fabricated aggregate usage, and blinded review export.
- Ruff passed for the benchmark Python files.
- Independent read-only review found trial pairing, citation enforcement, artifact validation and denominator/report-label defects. These were corrected and covered with focused checks. Review used supervised Orca workers; both workers settled and were released. No production source code was changed.
- The actual Codex adapter preflight succeeded with the existing ChatGPT subscription while requesting `gpt-6-astra`, medium effort. This establishes successful invocation and structured usage output, not independent attestation of the server's model identity.

## Existing suite limits

The broader combined run completed with **374 passed, 37 skipped, 4 failed**. The four failures were:

1. `test_acceptance.py::test_pdf_docx_image_adapters`: the expected OCR text was absent.
2. `test_acceptance.py::test_sdk_stdio_real_tools`: MCP initialization timed out.
3. `test_acceptance.py::test_sdk_enumerates_1201_sources_and_memberships`: MCP initialization timed out.
4. `test_discovery_interface.py::test_discover_official_sdk_read_only`: MCP initialization timed out.

All four reproduced in a clean archive of exact base commit `00be47ad82e0fd126f571062dafd979b60e3a9bf`. They also reproduced in this checkout's freshly synchronized locked environment, alongside all 47 passing benchmark tests. `git diff HEAD -- evidencekg` was empty. These are existing/environment-dependent failures, not evidence of a benchmark regression; they remain unresolved and are not counted as passes. No test was weakened or skipped to hide them.

Raw focused verification output is retained locally in `/tmp/graf-benchmark-base-failures.txt` and `/tmp/graf-benchmark-local-checks.txt`. Opt-in native-model/PostgreSQL groups skipped by the broader suite do not establish full hybrid-product validation.

## Measurement scope

The document corpus is fictional UTF-8 operational text. The code corpus is an exact text export of one real repository revision. Neither establishes PDF/OCR/attachment fidelity, million-line code performance, private-customer accuracy, or population-wide generalization. Mechanical retrieval is an explicit compatibility backend; the live measurements below use the separately prepared actual hybrid product runtime.

The live pilots use one repetition and six response steps per trial, with actual token receipts. The default reproduction recipe uses three repetitions and eight steps; it is a larger experiment, not an exact rerun of the pilot. Corpus export, scale probes and tests also ran on the shared host during parts of the document pilot. Timings therefore describe the observed shared-host run, not an isolated hardware performance certification.

## Completed live measurements

- 52/52 answering trials completed with actual usage receipts; 52/52 separate anonymous automated grades completed. Export revalidated raw artifacts and recomputed citation/abstention gates against the original sources.
- Two gold defects were discovered during grading (doc-16 answerability and code-03 process-wait wording). Both paired arms are excluded from headline metrics, leaving 15 document pairs and 9 code pairs. Original trials/grades remain unchanged. The current builder corrects the defects; exact old implementation is preserved in `.evidencekg-benchmarks/protocol-v2/`.
- The actual hybrid GPU/PostgreSQL engine completed all 16 retrieval queries over 10,000 short documents with zero result-cache hits and full designated-reference coverage. This does not establish final-answer accuracy at that scale.
- Both measurement processes exited successfully. The isolated database was dropped only after verifying its recorded name/OID and zero active connections; absence was checked afterward. Existing databases and credentials were unchanged.
- The report and PNG/SVG charts were generated from measured data; the PNG was visually inspected and clipped labels corrected. No external publication or Git commit/push occurred.
