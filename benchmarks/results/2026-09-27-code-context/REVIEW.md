# Independent code-context review

Reviewer: task_f6471cd4c140 / ctx_0fa1730c17a8, 2026-09-27.
Scope: `evidencekg/src/evidencekg/hybrid/code_context.py`, runtime and CLI opt-in integration. Read-only except this report; no inference, commits, source-snapshot mutations, or benchmark-harness edits.

Status: complete. The lead fixed all six reproduced findings; original exact reproductions, four local-scope variants, and four module-scope variants now pass. No unresolved substantive defect remains among these checked cases; this is not a claim of exhaustive Python semantic correctness.

## Additional scope finding resolved during final recheck

**P1, resolved — Module-scope rebinding created a false cross-module dependency.** In the pre-fix import collector and scope handling, imports were collected without accounting for module-level replacement bindings. Each source below yielded `process` call `pkg.safe.validate`, though that binding was no longer statically established:

```python
from pkg.safe import validate
validate = replacement
def process():
    return validate()
```

Replace the assignment with `def validate(): return False`, or with `if flag: from pkg.other import validate`, for the two additional reproduced cases. Smallest fix: collect module-scope import-name rebindings and suppress dependency resolution for those names; avoid claiming a particular implementation when module-level control flow leaves the target ambiguous. The lead implemented this conservative suppression, including duplicate imports; all four variants now return no imported dependency. This is the same scope-binding issue as finding 2, not a request for dynamic call-graph resolution.

## Reproduced findings, now resolved by the lead

1. **P1 — Split suites lose branch identity and exception bodies.** `_index.walk`, initially lines 129–140, flattened only direct `ast.stmt` children. In a >6500-character `if/else`, the second statement in `else` had enclosing headers `def choose(flag):` and `if flag:` with no `else:`. A similarly large `try` omitted all handler bodies because `ExceptHandler` is not an `ast.stmt`. This can reverse apparent conditions and hide recovery paths. Smallest fix: iterate explicit body/orelse/handlers/finalbody suites, carrying every relevant suite header to every child; keep unsupported compounds intact and expose omissions.

2. **P1 — Shadowed calls bind to the wrong imported implementation.** With `from pkg.safe import validate; def process(validate): return validate()`, the view included `pkg.safe.validate` as `static dependency of process`. `_index.walk` applied file-level imports without scope binding. Smallest fix: abstain on ambiguous/shadowed names, including parameters, assignments, nested definitions/imports, exception and pattern bindings; do not traverse nested scopes as if their calls belonged to the outer scope.

3. **P1 — Nonphysical Unicode separators corrupt AST source association.** `message = "hello\u2028world"\ndef target():\n    return 42\n` parses correctly, but `str.splitlines(keepends=True)` treats U+2028 as a newline while Python's AST does not. The unit labelled `target` contains `world"\ndef target():\n` and omits its return. References still reconstruct those wrong slices exactly, so provenance tests alone miss this defect. Smallest fix: derive offsets from Python physical LF/CRLF/CR boundaries, preserving raw text.

4. **P2 — Retrieved skipped Python evidence disappears.** A valid `pkg/valid.py` plus retrieved `pkg/invalid.py` containing `def broken(:` produces no original passage for the invalid file because the fallback checks `not units` globally. The skipped-file limitation is visible, but previously retrieved evidence is lost. Smallest fix: retain original passages from every skipped/unindexed document within budget and count any resulting omissions.

5. **P2 — Half-budget seed reservation can return empty evidence despite available space.** At `max_bytes=6000`, a sole target function containing a roughly 2200-byte docstring is rejected by the half-budget seed phase and never retried, leaving a roughly 926-byte result with `evidence=[]`. Its complete item fits the full evidence allowance. Smallest fix: retry deferred top seeds against the full allowance after expansion, or remove the reserve when no competing facets/dependencies exist.

6. **P2 — Original-passage budget omissions are uncounted.** A 5009-character non-Python retrieved passage at `max_bytes=4000` is dropped while `budget_omissions=0` and `omitted_units=0`. Smallest fix: record retained/dropped original-passage counts or IDs separately from indexed AST units.

## Focused reproduction inputs

Run with `PYTHONDONTWRITEBYTECODE=1 evidencekg/.venv/bin/python` and imports:

```python
from evidencekg.hybrid.code_context import _documents, _index, build_code_context
from evidencekg.db import sha

def segment(path, text):
    return dict(id=path, document_version_id=path, ordinal=0,
                char_start=0, char_end=len(text), source_path=path,
                text=text, text_sha=sha(text))

def context(rows, question, retrieved=None, budget=45000):
    return build_code_context({s['id']: s for s in rows}, question,
        {'snapshot_id': 'S', 'segments': rows if retrieved is None else retrieved},
        max_bytes=budget)

branch = ('def choose(flag):\n    if flag:\n        first = 1\n'
          + '        # padding\n' * 420
          + '    else:\n        fallback = 2\n        target = 3\n')
handler = ('def parse():\n    try:\n        start = 1\n'
           + '        # padding\n' * 420
           + '    except ValueError:\n        recovery = 2\n')
# Inspect _index(_documents(...)) units and parent spans for target/recovery.

shadow = [segment('pkg/a.py',
    'from pkg.safe import validate\ndef process(validate):\n    return validate()\n'),
    segment('pkg/safe.py', 'def validate():\n    return True\n')]
# context(shadow, 'process', shadow[:1]): inspect dependency reasons.

unicode_source = 'message = "hello\u2028world"\ndef target():\n    return 42\n'
skipped = [segment('pkg/valid.py', 'def works():\n    return True\n'),
           segment('pkg/invalid.py', 'def broken(:\n    pass\n')]
# context(skipped, 'broken', skipped[1:]): inspect original passage retention.
large_seed = segment('pkg/a.py',
    'def target():\n    """' + 'details ' * 270 + '"""\n    return True\n')
# context([large_seed], 'target', budget=6000): expected nonempty evidence.
large_doc = segment('notes.md', 'relevant ' + 'x' * 5000)
# context([large_doc], 'relevant', budget=4000): inspect omission accounting.
```

## Verification and boundaries

- Verified 192 frozen code documents and 360 original segments through `ReadOnlyStore` / `load_sources`. SQLite recovery occurs only in a disposable copy; original packet files were read-only.
- Built all ten frozen code-question views with the deterministic lexical path, no reranker/inference. All outputs stayed within 45,000 serialized UTF-8 bytes; 371 emitted body/header spans reconstructed exactly from original segment IDs and offsets with matching hashes. Inputs remained unchanged. This proves these sampled byte bindings, not correct scope, conditions, retrieval recall, answer quality, or semantic completeness.
- Initial runtime diff preserves the default call signature behavior and adds `code_context=True` to the cache identity only for opt-in requests. Configuration already binds all hybrid module bytes, so code changes require fresh preparation; an old prepared configuration is expected to reject after this update.
- `PYTHONDONTWRITEBYTECODE=1 evidencekg/.venv/bin/python -m pytest -p no:cacheprovider evidencekg/tests/test_hybrid_default.py -q`: 4 passed, 1 failed because local environment lacks `psycopg`, before reconnect-test behavior executes. No dependency installation or database mutation was attempted.
- Reused the existing `/home/wenga/src/docworm/evidencekg/.venv/bin/python` with `PYTHONPATH=evidencekg/src` and `PYTHONDONTWRITEBYTECODE=1`; `-m pytest -p no:cacheprovider evidencekg/tests/test_code_context.py evidencekg/tests/test_hybrid_default.py -q` passed **19 tests** after fixes. This resolves the local test dependency gap without changing the workspace environment.
- Final run of that same focused command after module-scope fixes: **22 passed in 0.26 seconds**. Final module SHA-256: `9658667f95feee1317ede24317dbce8d71f8cf3c76dabc3f778f01d0bc4580dd`.
- Independently exercised `Runtime.retrieve` against an in-memory ledger and deterministic fake ranker: default miss, enriched miss, default hit, enriched hit; exactly two cache keys, unchanged original segments, unchanged default cache identity, opt-in identity containing `code_context: true`, and rejection of nonboolean opt-in values. This is synthetic integration evidence, not PostgreSQL or reranker-inference evidence.
- Exact recheck assertions passed for else-child headers, except bodies, parameter shadowing, local imports, local definitions, exception bindings, nested function parameters, U+2028 source association, skipped Python originals, top-seed budget retry, and original-passage omission counts.
- Final frozen replay at module SHA-256 `9658667f95feee1317ede24317dbce8d71f8cf3c76dabc3f778f01d0bc4580dd`: ten views, 359 exact reconstructed spans, output byte sizes `[44486, 44577, 43824, 43510, 44786, 43410, 44545, 44082, 44096, 44380]`; module bytes remained unchanged during this run. This supersedes the initial 371-span count for the reviewed final revision.
- Inspected runtime SHA-256 `9f7beaf90ec6f4d1a5ce9c74acf4fec57cd73b98cd43658f5f1a1eda4127f0ad` and CLI SHA-256 `aa29908edbae5fc5a104e5beb816a537b457c2b4a4807b64275ff2591778b253`; `git diff --check` passed.
- Builder validates packet membership/equality and segment text hashes/contiguity; full snapshot/tail/artifact verification is an upstream precondition fulfilled by `load_sources`. It does not independently authenticate an arbitrary caller-supplied snapshot ID or prove inventory completeness.
- Live PostgreSQL, local cross-encoder inference, provider/model behavior, hosted CLI operation, and benchmark semantic outcomes remain outside this review's evidence.
