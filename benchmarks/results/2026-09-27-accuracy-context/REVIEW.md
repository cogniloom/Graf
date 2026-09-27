# Independent accuracy-context review

Reviewed 2026-09-27 for dispatch `ctx_a859b7985f4e`, task `task_ee97cf803d8f`. Read-only review; only this report was written. No implementation changes, commits, memory writes, or external model benchmark calls.

## Verdict

The lead fixed both reproduced P2 context defects, and the final narrow recheck passes: enclosing guard/class identity and earlier exception/match branch precedence are preserved. No unresolved substantive defect remains in that recheck; two test-file import-order lint findings remain. The already-running v1 benchmark must retain its frozen inputs and builder; four of ten intermediate repaired v2 contexts differ, requiring the separate final experiment the coordinator has now planned.

Exact source reconstruction, original-passage retention, final output budget enforcement, and the examined deterministic behavior passed the checks below. This does not establish semantic completeness or generalization.

## P2 — Original-only function expansion omits enclosing guard/class source

Location: `evidencekg/src/evidencekg/hybrid/accuracy_context.py:85-94` in reviewed SHA-256 `91c6edab7e9763eb277ad60c47ea7b30a481f282bf4eaae27d91a39e11b246a3`.

The AST function inventory keeps only decorator/function start and end offsets. When the original retrieval seeds a function whose body did not fit the frozen enrichment view, the expansion adds that function without its enclosing module-level condition, class declaration, or branch header. The preserved original and enriched seeds cannot supply context that neither selected. This matters directly for an accuracy-oriented view: a function under `if TYPE_CHECKING` appears without that condition; a method loses the class/base identity needed to distinguish implementations. `complete_function_expansions=1` and `omitted_function_expansions=0` do not expose this missing context. The generic non-exhaustive warning and native file access do not repair the emitted evidence context.

Reproduce from repository root, without changing source files:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=evidencekg/src:evidencekg/tests evidencekg/.venv/bin/python - <<'PY'
from evidencekg.hybrid.accuracy_context import build_accuracy_context
from test_code_context import snapshot
from evidencekg.db import dump

for prefix in ['if TYPE_CHECKING:\n',
               'class NeverUsed(Base):\n    enabled = False\n']:
    source = prefix + '    def target():\n        return "' + 'x' * 50000 + '"\n'
    segments = snapshot({'a.py': source}, size=1000)
    packet = {'segments': list(segments.values())[1:2]}
    result = build_accuracy_context(segments, 'target', packet)
    text = '\n'.join(r['text'] for e in result['evidence'] for r in e['regions'])
    print(prefix.strip(), prefix.strip() in text,
          '    def target():' in text,
          result['limitations']['complete_function_expansions'],
          result['limitations']['omitted_function_expansions'],
          len(dump(result).encode()))
PY
```

Observed for both cases: enclosing prefix absent, complete function present, one accepted expansion, zero omitted expansions, 57,762 bytes. The large literal is a valid deterministic fixture that prevents the frozen 45KB enrichment from selecting the return unit; the new 240KB view accepts the function.

Requested fix: track enclosing AST context independently of frozen enrichment selection and atomically budget the function plus required enclosing source. Preserve class/decorator identity and conditional/exception branch headers as separately cited source spans; if they cannot fit, reject that expansion and report the omission while retaining all original seeds. Do not modify the frozen `code_context.py`. Add regressions for original-only seeds under conditions/classes and exception/else branches, including a budget that fits the function but not its required ancestors. The current five new tests do not cover these cases.

Coordinator response `msg_70ed823d2f2d`: confirmed the risk and retained lead fix ownership. At the lead's explicit recheck request, examined the first fix, SHA-256 `3cc2db8826e33be61313bfbc6ac89b4ea5f6cc3036f48cc0940bee9dc5af6490`: original `if TYPE_CHECKING` and class declaration/base identity cases now pass. Class attribute values are still outside a header-only expansion's scope. The focused suite then passed **40 tests in 11.10s**. A 15KB guard plus 50KB function with a 59KB output budget correctly rejected the expansion, preserved the original seed, and counted the omission. Exact source-reference reconstruction passed the narrow reproduction variants.

## P2 follow-up — Preserve prior exception/case headers when showing a later branch

In the first fix's `_functions`, the `ast.Match` loop adds only the match header plus the selected case, and the exception loop adds only the try header plus the selected handler. Prior alternatives determine whether that later branch is reached. Reuse the reproduction above with these prefixes (eight spaces of function indentation for `match`, four for `try`):

```python
'try:\n    attempt()\nexcept Exception:\n    pass\nexcept ValueError:\n'
'match value:\n    case _ if blocked:\n        pass\n    case 42:\n'
```

The first output begins `try:\n\nexcept ValueError:\n    def target():` and omits `except Exception:` entirely, although it catches the ValueError first. The second begins `match value:\n\n    case 42:\n        def target():` and omits `case _ if blocked:`, hiding the earlier guarded match. Both report zero omitted expansions. Exact emitted text remains valid; the loss is semantic context. Sent to coordinator as `msg_4dad6e334afe`. Requested correction: atomically include preceding handler/case headers, or explicitly expose unresolved branch precedence. Do not concatenate discontiguous headers into a verbatim source quote.

**Closed in final narrow recheck.** Coordinator message `msg_e1b48a9765d7` requested recheck of complete-prefix retention for earlier handlers/cases and else/finally. Final `accuracy_context.py` SHA-256 `76ae7e2e50e81ed7ca45ff3f8a410f144e38af67774fa1fb787defc0fd4fd99b` passes all four exact guard/class/exception/match reproductions, with full required prefixes and exact region reconstruction. A large earlier try-body prefix that would exceed 59KB with the function is atomically rejected, preserving the original seed and counting an omission. Final focused suite: **44 passed in 11.13s**. This is a targeted correction check, not proof that every conceivable Python control-flow construct or runtime effect is covered.

## Coordinator-requested scope extension

Message `msg_589a93b4685c` explicitly requested review of the lead's small `code_context.py` relative-module-import fix; the reviewer made no edits to it. Its frozen archived copy remains preserved. The diff removes the `node.module` truthiness filter, builds the package prefix for `from . import parsers`, and skips empty prefixes. Independent checks passed direct relative imports, aliased parent imports, package initializer imports, and parameter shadowing; no actionable defect found in that small change. This changes a second treatment component and must be bound to the final experiment's provenance.

Final reviewed `code_context.py` SHA-256: `1bb68d529669fe13987c02b9f1b555436f466bb1e6ed41ba1e0c582529478e9d`.

Independently compared all ten `/tmp/graf-code-accuracy-v1/contexts` and `/tmp/graf-code-accuracy-v2/contexts` context objects, excluding timing: **code-04, code-05, code-06, code-09 differ**, six others match. Coordinator states v1 will remain exploratory and a separate final paired run will use rebuilt reviewed packets; no equivalence claim is warranted.

## Verification performed

- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=evidencekg/src:evidencekg/tests evidencekg/.venv/bin/python -m pytest -q -p no:cacheprovider evidencekg/tests/test_accuracy_context.py evidencekg/tests/test_code_context.py benchmarks/test_code_context_eval.py`: **35 passed in 11.07s**.
- Independent in-memory probes: equal serialized output with reversed segment-map insertion order; unchanged input objects; rejected budgets `True`, `9999`, `1000001`, and `10.0`; rejected changed source hash, changed packet text, and invalid source offset. CRLF and non-ASCII text were included in this probe.
- Independently read the frozen source SQLite database with `mode=ro&immutable=1`, after asserting no WAL existed. For all ten prepared `/tmp/graf-code-accuracy-v1/contexts/code-*.json` files: **225 regions / 348 references** passed segment hash, document identity, offset bounds, contiguous absolute offsets, and exact text reconstruction checks. All **96 original packet passages** had complete interval coverage by their original segment IDs. Snapshot IDs matched. This checks the v1 prepared artifacts, not a future repaired builder.
- Canonical serialized sizes ranged from **72,199 to 123,177 bytes**, below the default 240,000-byte ceiling. The existing tests exercise fail-closed seed overflow and counted expansion omissions. The final render recomputes evidence after rejected tentative expansions, so no rejected source leaked into the final view in the reviewed control flow.
- At coordinator request `msg_1855e2fbeac7`, repeated the entire provenance/interval check against **all ten final CUDA contexts** in `/tmp/graf-code-accuracy-final/contexts`: **225 regions / 349 references / 96 fully retained original passages**, all exact hashes, document identities, contiguous offsets, text reconstruction, snapshot IDs, and byte ceilings passed. Final sizes: **72,199–123,414 bytes**. Both archived final builder copies equal the reviewed source hashes above. The reviewer made no CUDA/model calls; these are independent checks of the prepared artifacts. The coordinator reported 49 focused tests; independently executed evidence in this report remains the 44-test scope stated above.
- `/tmp/graf-code-accuracy-paired` passed the harness's read-only `verify()`. Its full contract equaled the previous experiment's recorded contract, with ten cases, two repetitions, and forty scheduled answers. Seven answer receipts existed when inspected; this was an in-progress check, not a final result audit.
- Frozen `code_context.py` SHA-256: `9658667f95feee1317ede24317dbce8d71f8cf3c76dabc3f778f01d0bc4580dd`; harness `code_context_eval.py`: `e216f72c8ac3550536dbca294eddc5b096846581d16a2bcaf2a4386d1a3c96cc`; `native.py`: `f7a88f3d089a0e62197cce6726c0a5462e4fee9c550337c2309fd0aa6ed56527`. The v1 frozen builder copies matched working files when checked.
- Final `git diff --check` passed. `.venv/bin/ruff check --no-cache src/evidencekg/hybrid/accuracy_context.py src/evidencekg/hybrid/code_context.py tests/test_accuracy_context.py tests/test_code_context.py` from `evidencekg/` failed only with **I001 import ordering at line 1 of both test files**. Move the local test-helper import into Ruff's appropriate group and separate third-party/local package imports as its diagnostic requests. Lead owns this mechanical correction; no source edits were made by the reviewer.

## Experiment controls and methodology limitations

The inspected plan preserves the requested original ten development questions, original baseline retrieval contexts, rubric, schemas, Luna-low answering, Astra-medium anonymous grading, randomized adjacent paired schedule, and two repetitions. The current protocol matches the prior one, and the immutable input inventory verifies. Subscription enforcement checks ChatGPT login, forces ChatGPT authentication, removes API-key environment variables, and uses an isolated Codex home. I did not make a provider call or claim provider-attested model identity.

Action before final attribution: retain a supplemental provenance manifest binding the new context hashes to both builder hashes, the preparation script, verified snapshot identity, local reranker identity/configuration, and relevant library/device versions. In the prebuilt-context route, the harness seal includes generated contexts but **does not include the copied builder files**; the files currently live outside its sealed input inventory. This need not change the frozen harness. If the lead fixes the builder, compare semantic context objects or exact generated prompt bytes, excluding timing-only fields, across all ten cases and retain the comparison. A changed packet cannot inherit the running v1 experiment's outcome.

The public-source receipt reports **192/192** frozen files matching public `cogniloom/Graf` commit `de5064b252cfbbaeb8e4882ff963c84c3d7b4c98`. This review inspected that receipt and verified the local frozen inventories through the harness; it did not repeat the earlier network archive verification. It covers this source corpus, not arbitrary future sources.

Report the following limits alongside any results:

- These questions were already used to diagnose failures and design the follow-up. Two repetitions of ten development cases are twenty observations per arm, not twenty independent unseen tasks. This is a regression/development experiment, not evidence of generalization.
- Original baseline versus additive view jointly changes source coverage, packet size, grouping, and reading instructions. It can estimate the aggregate treatment effect; it cannot isolate the causal benefit of full-function expansion from more evidence or guidance. There is no matched third arm comparing the prior enriched view in this new run.
- Anonymous judging conceals the explicit arm label, but a single automated judge and known imperfect original rubric are not independent ground truth. The judge receives cited and rubric-selected full source documents, not the complete 192-file corpus, so contrary evidence in other files may be absent. Preserve mechanical quote validity, factual correctness, completeness, supportedness, and strict conjunction separately, including per-case regressions.
- Final denominators must include every scheduled answer and judge, with failed/unknown outcomes retained and no automatic retries. An incomplete live run must not be described as a completed forty-answer/forty-judge evaluation.
- Source-only working directories and prompt restrictions do not provide OS-level read confinement. Existing native-tool receipts require inspection before claiming compliance. Both arms have native file tools; increased context may change their tool-use behavior.
- Requested model/effort identities are not provider-attested served identities. Shared provider/host caches and load are uncontrolled. Historical baseline retrieval time plus current preparation/answer time is an estimate, not a fresh randomized end-to-end latency measurement.
- The byte ceiling bounds the returned canonical JSON, not token count, process memory, AST work, or model cost. The larger packet must be evaluated for token usage and failures as well as answer quality. Function containment is not runtime reachability or complete dependency analysis.

No production/default adoption, completed external benchmark results, or exhaustive semantic coverage is asserted by this review. Repaired-builder verification comprises the targeted source/reproduction checks, focused suite, and final prepared packet provenance checks above; full-source semantic sufficiency remains a model-evaluation question.


## Lead integration resolution

After reviewer settlement, the lead reproduced both I001 findings from the package directory and applied the mechanical import ordering fixes. Package-directory no-cache Ruff now passes, and all 49 focused tests passed again in 11.18s. The initial root-directory check resolved imports differently; the reviewer finding was valid. No builder source or frozen model input changed in this correction. Both reviewed builder hashes still equal the final preparation manifest. The reviewer did not audit final external-model scores; the lead verified every receipt and exported all outcomes.
