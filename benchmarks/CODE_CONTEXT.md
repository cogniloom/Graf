# Experimental code evidence packets

This experiment asks whether improving Graf's evidence response helps a low-reasoning answering model. The feature is opt-in; adoption and default routing remain a separate decision. It does not generate an answer or consult benchmark gold during retrieval.

## Language support and roadmap

**Currently optimized for Python only.** Function extraction, enclosing conditions, field accesses and static caller/callee relationships use Python's AST. For other languages, the reply preserves the original retrieved passages but does not add this structural enrichment. This limitation applies to the optional code enrichment, not to Graf's general passage retrieval.

The measured improvement comes from one Python repository and ten development questions. It does not establish an accuracy improvement on other Python repositories or languages.

Future work is to add language-specific parsers and relationship resolvers feeding the same source-backed node representation. Each language should be validated on unseen repositories before claiming an accuracy improvement. Additional language support is planned, not implemented; no language order or delivery date is committed.

## Current opt-in implementation: anchored relationship context

`Runtime.retrieve(..., code_context=True)` and `discover --code-context` select `evidencekg.hybrid.code_relationships.build_relationship_context`. Default retrieval is unchanged. The current `python-relationships-anchored-v2` reply preserves every original retrieved passage, in order, before optional enrichment. It selects functions/constants that overlap those exact passage intervals, completes their source when space permits, and adds direct static callers/callees. It does not globally rerank replacement nodes or expand second-hop neighbours. The existing reranker parameter remains accepted for compatibility but is not invoked by this builder.

Nodes include exact source references, enclosing conditions, return/error statements and mapping-key reads/writes/deletes. Separate implementation groups are explicitly labelled without asserting a primary implementation. Unknown or ambiguous dynamic calls remain unresolved. Caller source can show how a returned value is consumed; this is not full data-flow analysis.

The default limit is 110,000 serialized UTF-8 bytes. Original passages are mandatory for every language and parse outcome; insufficient room for mandatory evidence raises an error. Optional complete nodes have explicit omission counts. Construction uses only the verified immutable snapshot, question and original retrieval. No generative calls or benchmark answers enter selection. Prepared hybrid configuration requires refresh after implementation changes.

The [previous relationship experiment](results/2026-09-27-code-relationships/REPORT.md) scored 7/20 versus 10/20 for original replies; its selection could amplify a wrong starting node. Its frozen artifacts remain unchanged. The anchored follow-up uses the same recovered byte-identical original `luna_graf.py`, unchanged `native.py`, questions, source snapshot, gold and pass/fail grading. The historical `code_context_eval.py` replacement harness below is not used for either relationship experiment.

The completed [anchored follow-up](results/2026-09-27-code-anchored/REPORT.md) scored 11/20 versus 7/20 for fresh original controls. Both executions improved, but per-question regressions remain; the feature stays opt-in.

## Historical first implementation

`evidencekg.hybrid.code_context.build_code_context` consumes the full verified immutable segment inventory and the ordinary hybrid result. It reconstructs Python files, parses their AST without executing them, ranks function/block evidence using lexical terms, symbol names and the existing local cross-encoder, and expands resolvable imported/local calls plus scoped exact-identifier matches. These links are navigation hints, not a runtime call graph.

The response separates implementation/test/example evidence, preserves enclosing branch source, and attaches exact segment-relative spans. Multi-span headers and bodies remain separate so they cannot be mistaken for contiguous quotes. Ambiguous or rebound imports are conservatively unresolved. Skipped Python files and unsupported languages retain original passages where the budget permits. Omitted units/passages are counted; bounded retrieval is never presented as exhaustive coverage.

The serialized evidence view is capped at 45,000 UTF-8 bytes by default. Source text is never summarized or rewritten. It includes guidance to check conditions, resolve missing evidence with the model's native tools, and cite exact source text.

`Runtime.retrieve(..., code_context=True)` returns the additional `code_context` view and preserves the ordinary `segments` field. Its cache identity is distinct. The normal API call retains the existing behavior. The opt-in CLI emits only the enriched view, matching the benchmark treatment rather than duplicating both evidence payloads. This experimental switch is exposed through the CLI, not yet through the application's MCP/API or UI:

```sh
evidencekg --state STATE discover 'QUESTION' --code-context
```

As with any hybrid implementation change, existing prepared configuration hashes become stale. Run `prepare-hybrid` against the chosen workspace before use. The evaluation uses an isolated copy of the benchmark index, not an existing user workspace.

## Paired model evaluation

`benchmarks/code_context_eval.py` freezes both prompts, original source bytes, original gold, local preparation times, protocol and receipt hashes. It runs adjacent pairs in randomized order, with two repetitions per question by default. Both arms use GPT-6-luna low, identical native tools, answer schema and base prompt; only the supplied evidence context differs. Anonymous GPT-6-astra medium judging follows in randomized order.

The comparison uses the original ten development questions and the same public 192-file source snapshot. It measures regression behavior on known questions, not generalization to a new repository or unseen question set. The original rubric is preserved, including its known imperfections. Reports separate citation validity, abstention, automated semantic grading, and strict conjunction; failures and unknown usage are retained.

```sh
PYTHONPATH=evidencekg/src python -m benchmarks.code_context_eval prepare OUTPUT \
  --frozen ORIGINAL_GRAF_RUN --baseline ORIGINAL_NATIVE_RUN \
  --contexts PREBUILT_CONTEXTS --repetitions 2
PYTHONPATH=evidencekg/src python -m benchmarks.code_context_eval run OUTPUT \
  --auth-home EXISTING_SUBSCRIPTION_HOME
```

Each prebuilt context file is named `code-XX.json` and contains `context` and measured enrichment `seconds`. Retain the builder hash and local-model identity alongside it. `run` requires an existing ChatGPT subscription login, creates a separate home, and has no API-key fallback. Calls with unknown outcomes are not retried automatically. Inputs and original results are not modified; report snapshots are append-only.

Answer tokens and judge tokens are separate. The end-to-end estimate adds historical original retrieval time, measured enrichment time for the treatment, and current answering time. It is not a fresh end-to-end randomized latency measurement. Shared caches and provider load are not controlled.

## Accuracy-first follow-up

`evidencekg.hybrid.accuracy_context.build_accuracy_context` is a second experimental builder, available as a Python function. It is not selected by the existing CLI flag or runtime. It keeps the original retrieval and the first prototype's source spans, merges overlapping intervals within each immutable document, and adds complete Python functions intersecting that evidence. Decorators, later assignments, exception handlers, and nested functions are retained when the expansion fits. Enclosing class/guard source and earlier handler/case precedence are included atomically with each expansion. Relative module imports such as `from . import parsers` now participate in the conservative navigation resolver. Each output region is contiguous source with exact segment references; separate regions must not be concatenated into one quotation.

The default ceiling is 240,000 serialized UTF-8 bytes. If the initial evidence cannot fit, construction fails rather than silently discarding passages. Optional complete-function expansions have explicit omission counts. This preserves previously retrieved evidence, but does not guarantee that it is sufficient, relevant, or correctly understood by the answering model. Unsupported languages and unparseable Python retain their selected original text without function expansion. Runtime configuration fingerprints cover this new module as well, so existing prepared hybrid configurations require the usual explicit preparation after adding it.

The follow-up uses fresh baseline calls, the same unchanged harness and gold, and two repetitions per run: exploratory seed 20260928 and final reviewed seed 20260929. All inputs and builder files are frozen before model calls. Reference-excerpt coverage is computed afterward as a diagnostic and is never an input to retrieval. These remain development questions, not held-out evidence of generalization.

The completed [accuracy-first follow-up report](results/2026-09-27-accuracy-context/REPORT.md) records 7/20 original versus 11/20 final strict passes, with improved source coverage but remaining answer regressions. The larger view remains experimental.
