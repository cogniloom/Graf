# Independent relationship source review

Status: **review closed: all seven substantive findings passed exact rechecks after lead fixes; no unresolved substantive finding in the reviewed behavior**. Reviewer owns only this report; lead owns implementation and regression fixes. No model, benchmark, provider, or corpus source execution calls were made. Baseline focused check: `PYTHONDONTWRITEBYTECODE=1 evidencekg/.venv/bin/python -m pytest evidencekg/tests/test_code_relationships.py -q -p no:cacheprovider` — **7 passed**. Final affected regression gate: **50 passed in 11.02s**.

Review covers `evidencekg/src/evidencekg/hybrid/code_relationships.py`, its tests, and `Runtime.retrieve(code_context=True)` integration. Dependencies were inspected to trace the changed builder's behavior. Implementation is concurrently being edited, so references below use function names rather than unstable line numbers.

## Original substantive findings (all resolved by exact recheck)

1. **P1: Cross-implementation call edges mix unrelated source trees.** `_edges` indexes only `module.symbol`; `_module` strips the prefix before `/src/`. With caller `one/src/pkg/a.py` importing `.b.parse` and only `two/src/pkg/b.py` present, it asserts the first implementation calls the second one's `parse`. A single candidate globally does not establish that the import belongs to that implementation. This contradicts the output's explicit instruction to keep implementations separate. Repro `cross_family` below returns `run -> parse`; expected no edge unless source-root/import correspondence is established.

2. **P1: Rebound/dynamic names are asserted as resolved function relationships.** `_index`'s shadowing handling and `_edges`'s unconditional `self.` expansion are insufficient for the new graph. `conditional_rebind` assigns `parse = replacement` under a module guard yet yields `run -> parse` pointing to the original function. `static_self` treats an arbitrary parameter named `self` in a static method as the enclosing class instance. `rebound_self` does the same after `self = other`. These contradict the advertised conservative resolution rule. With uncertain binding, omit the edge or explicitly distinguish an unresolved candidate rather than asserting this callee.

3. **P2: Definition-time expressions are reported as calls from the function body.** `_nodes` reuses `_index` call sets that walk decorators, defaults, and annotations as children of the function. Both `@decorate()` above `run` and `run(value=parse())` yield a `run` call edge, although those expressions execute when the function is defined. Source navigation must distinguish definition-time references from body call relationships; runtime-path reasoning otherwise starts from a false premise.

4. **P2: Guarded and nested functions silently lose ordinary body calls.** `_nodes` enumerates all functions, while `_index` stops at small outer function/guard units; associating calls by `(doc, symbol)` therefore leaves the inner function's call list empty. Both `if enabled: def run(): return parse()` and `outer` containing `def run(): return parse()` produce no `run -> parse` link. Collect function body calls in their own lexical scope instead of copying presentation-unit aggregates. This is an actual lost relevant path, not an inherently dynamic call.

5. **P1: Selecting one Python node drops original retrieved evidence in other languages or malformed Python.** `build_relationship_context` adds `original_passages` only when `selected` is empty. Given retrieved `broken.py`, `app.js`, and an irrelevant valid `valid.py`, the result includes only `valid.py`; JavaScript has no omission entry, and malformed Python has a skip reason but no retained source. The CLI prints the new view alone, so preservation of the outer runtime packet does not protect its consumer. Retain relevant original passages not represented by selected source spans, with exact references and explicit budget behavior.

6. **P2: Successfully parsed deep source crashes instead of becoming an explicit skipped-source result.** A ~2.2 KB valid function with a return expression containing 1,100 additions parses successfully, then recursive traversal raises `RecursionError`. Only `ast.parse` is guarded; `_index`, `_nodes.walk`, and `_local_walk` also recurse. One such file aborts the entire optional retrieval view. Bound traversal or catch document-level traversal failure and preserve the original evidence with a diagnostic.

7. **P2: `limitations.omitted_nodes` reports zero despite hard selection-cap omissions.** It counts only rejected add attempts. For 25 tiny same-family matching functions, the current selection cap returns six functions while reporting zero omitted nodes. Count all indexed but unselected nodes (or rename the existing field precisely and expose total/selected/omitted counts). This is materially misleading coverage metadata in the new bounded reply.

## Exact reproductions

Run from repository root. These use only synthetic snapshot text, with no filesystem source reads or source execution:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=evidencekg/src:evidencekg/tests evidencekg/.venv/bin/python - <<'PY'
from test_code_context import snapshot
from evidencekg.hybrid.code_context import _documents
from evidencekg.hybrid.code_relationships import _nodes, _edges, build_relationship_context

cases = {
    'cross_family': {
        'one/src/pkg/a.py': 'from .b import parse\ndef run():\n    return parse()\n',
        'two/src/pkg/b.py': 'def parse():\n    return 1\n',
    },
    'conditional_rebind': {'pkg/a.py': 'def parse():\n    return 1\nif flag:\n    parse = replacement\ndef run():\n    return parse()\n'},
    'static_self': {'pkg/a.py': 'class C:\n    def parse(self):\n        return 1\n    @staticmethod\n    def run(self):\n        return self.parse()\n'},
    'rebound_self': {'pkg/a.py': 'class C:\n    def parse(self):\n        return 1\n    def run(self, other):\n        self = other\n        return self.parse()\n'},
    'decorator': {'pkg/a.py': 'def decorate():\n    return wrapper\n@decorate()\ndef run():\n    return 1\n'},
    'default': {'pkg/a.py': 'def parse():\n    return 1\ndef run(value=parse()):\n    return value\n'},
    'guarded': {'pkg/a.py': 'def parse():\n    return 1\nif enabled:\n    def run():\n        return parse()\n'},
    'nested': {'pkg/a.py': 'def parse():\n    return 1\ndef outer():\n    def run():\n        return parse()\n    return run\n'},
}
for label, files in cases.items():
    nodes, _ = _nodes(_documents(snapshot(files)))
    print(label, [(nodes[i]['symbol'], nodes[j]['symbol']) for i, j in sorted(_edges(nodes))])

s = snapshot({'broken.py': 'def nope(:\n', 'app.js': 'function target() {}\n',
              'valid.py': 'def irrelevant():\n    return 1\n'})
r = build_relationship_context(s, 'target nope', dict(segments=list(s.values())))
print('mixed', [n['path'] for g in r['implementations'] for n in g['nodes']],
      r.get('original_passages'), r['limitations'])

s = snapshot({'x.py': 'def target():\n    return ' + '+'.join(['1'] * 1100) + '\n'}, size=10000)
try:
    build_relationship_context(s, 'target', dict(segments=list(s.values())))
    print('deep: returned')
except Exception as exc:
    print('deep:', type(exc).__name__, str(exc))

s = snapshot({'pkg/a.py': ''.join(f'def target_{i}():\n    return {i}\n' for i in range(25))}, size=10000)
r = build_relationship_context(s, 'target', dict(segments=list(s.values())))
print('coverage:', sum(len(g['nodes']) for g in r['implementations']), 'of 25', r['limitations'])
PY
```

Observed initial output:

```text
cross_family [('run', 'parse')]
conditional_rebind [('run', 'parse')]
static_self [('C.run', 'C.parse')]
rebound_self [('C.run', 'C.parse')]
decorator [('run', 'decorate')]
default [('run', 'parse')]
guarded []
nested []
mixed ['valid.py'] None; skipped_python contains broken.py, omitted_nodes=0
deep: RecursionError maximum recursion depth exceeded
coverage: 6 of 25; omitted_nodes=0
```

## Positive evidence and limits

- All seven preexisting relationship tests passed, including exact quote reconstruction across segments, Unicode physical-line handling, packet-source drift rejection, and duplicate-module ambiguity.
- Runtime defaults remain opt-in: normal identity is unchanged, `code_context=True` gets a distinct identity, and implementation hashes are part of the prepared configuration, so no stale-version cache finding was established.
- Budget overflow is explicitly rejected rather than returning oversize data. A non-Python original of 9,000 bytes with a 10,000-byte budget raises due to metadata overhead; this behavior is recorded as a limit, not a silent-truncation finding.
- No paid/model benchmark was run, so this review establishes neither an improved original benchmark pass score nor live retrieval/model correctness.
- Canonical Hindsight recall returned bank-not-found; no memory was written or treated as proof.

## Recheck status

First narrow recheck after the lead's call-extraction and original-passage fixes:

```text
cross_family []
conditional_rebind []
static_self []
rebound_self []
decorator []
default []
guarded [('run', 'parse')]
nested [('outer.run', 'parse')]
mixed: valid.py node plus original_passages for broken.py and app.js, with exact refs
deep: RecursionError maximum recursion depth exceeded
coverage: 6 of 25; omitted_nodes=0
```

Findings 1–5 are resolved for the reported exact reproductions. Findings 6–7 remain open and were escalated again. The original findings above are retained as review history; only unresolved items govern the current status.

Affected regression command: `PYTHONDONTWRITEBYTECODE=1 evidencekg/.venv/bin/python -m pytest evidencekg/tests/test_code_relationships.py evidencekg/tests/test_code_context.py evidencekg/tests/test_accuracy_context.py -q -p no:cacheprovider` — **44 passed in 10.84s**. This does not supersede the two failing exact reproductions.

### Final narrow recheck

All earlier fixes remain confirmed by the same report reproduction block. Findings 6–7 are now also resolved:

```text
deep: returned
deep fallback: original quote reconstructs exactly from source_refs; output <= 65000 bytes
skipped_python: [{path: x.py, reason: Python AST nesting limit}]
coverage: 6 of 25; omitted_nodes=19; budget_omitted_nodes=0
```

Lead added iterative AST depth preflight (limit 200), before recursive extraction, and separate total/budget omission counts. The same affected regression command above now reports **50 passed in 11.02s**. `git diff --check` also passed. These are local deterministic review results, not live model/provider or benchmark-score evidence. The inherently bounded/static navigation limitations and explicit budget rejection remain intentional.

Final reviewed SHA-256 identities:

```text
4b3d090fdbf98586c4bfabc26569ee87d4506060f5db951520e6f3811eb13ac5  evidencekg/src/evidencekg/hybrid/code_relationships.py
cd15e64a631b7c99e9a0786d2d024a9569cb1faac4bd44127974f604d784a085  evidencekg/src/evidencekg/hybrid/runtime.py
51ba0e8d079ebce473feae2bbaa5f9768f109ad4e5b2939fc19ab6ab4b115b58  evidencekg/tests/test_code_relationships.py
```

Review ownership was preserved: only `.swarm/relationship-source-review.md` was authored by this worker; no implementation, benchmark, previous report, memory, commit, or remote mutation was made.
