# Original Luna-low benchmark execution review

Read-only investigation, 2026-09-27. Only this report was written; no model calls, benchmark changes, retrieval calls, database access, or old experiment mutations were performed.

## Finding and provenance

The original runner is `benchmarks/luna_graf.py`, formerly in `/home/wenga/orca/workspaces/Graf/Benchmark2`. It is absent from the current tracked benchmark files, all three current worktrees' benchmark directories, and Git history reachable with `git log --all -- benchmarks/luna_graf.py`.

Its exact creation command survives in:

`/home/wenga/.codex/sessions/2026/09/26/rollout-2026-09-26T20-37-59-01a0df02-85bc-7f13-a91b-af4712c58ee8.jsonl`, line **2813**.

The same session records a subsequent Ruff import fix and the actual execution command. Parsing that creation command and applying the recorded Ruff fix **in memory through stdin** produced:

```text
Before Ruff: 6aa03028d3cd51201352c5e7da75b5488bde27579587eb343877b6507c822b81
After Ruff:  8beb7b31db663228e53117fa424ee2508e8bf7d3c407c75c51605f6c8ae6c90c
```

The latter exactly matches `treatment_implementation_sha256` in `/tmp/graf-luna-low-20260927/run.json`. This recovers the original implementation, not an approximation or replacement runner.

Current `benchmarks/native.py` and `benchmarks/corpora.py` exactly match that run's frozen implementation hashes:

```text
native.py  f7a88f3d089a0e62197cce6726c0a5462e4fee9c550337c2309fd0aa6ed56527
corpora.py 02cf4fc3f6632b003720af9720d9d47d1c8dfafc1b0319c62308a9bd11c3e908
run.json   cb6cbe49b3ed60556cdc017680efa6a25fb051c74edb95063104688a7b038402
code gold  69268af954e461b6c30450e6aae2dff62a3aab7be406b73d5c60829b78c9489d
```

Executed existing `native.verify` with bytecode writes disabled against the frozen root: **PASS**, covering all 1,000 document files, 192 code files, both gold files, and both implementation files. All 26 retrieval records' question strings match their corresponding frozen gold questions. No secrets or authentication files were opened.

## Exact original execution command

Recorded historical command, **do not rerun against the old completed root**:

```sh
PYTHONPATH=.:evidencekg/src /home/wenga/src/docworm/evidencekg/.venv/bin/python -m benchmarks.luna_graf execute /tmp/graf-luna-low-20260927 --home /tmp/graf-luna-low-codex-home
```

For a newly staged experiment, after restoring the byte-identical original runner, the corresponding command is:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:evidencekg/src /home/wenga/src/docworm/evidencekg/.venv/bin/python -m benchmarks.luna_graf execute "$NEW_RUN" --home "$BENCHMARK_AUTH_HOME"
```

`NEW_RUN` must name a new prepared artifact directory; `BENCHMARK_AUTH_HOME` must be the lead's designated subscription-auth benchmark home. These are deliberate prerequisites, not paths created or auth verified by this review. The historical auth home exists, but its authentication validity was not checked. The original command itself handles answering, original judging, original reporting, and completion recording.

## Existing integration point: change response content only

The recovered `execute(root, home)`:

1. Verifies its own SHA against `run.json` and requires `preparation-complete.json` to contain `retrievals == 26`.
2. Saves the existing `native.call`, wraps only answering, and looks up `retrieval-{case_id}.json` using each scheduled trial.
3. Appends exactly `\nGRAF RETRIEVED SOURCE PASSAGES:\n` followed by `json.dumps(retrieval_record["context"], ensure_ascii=False)` to the unchanged native prompt.
4. Calls the existing `native.run`, restores `native.call` in `finally`, then invokes unchanged `native.grade` and `native.report`.

Therefore the intended treatment belongs in the new run's retrieval records' **`context`** field. The full `packet` field is retained evidence but is not injected into the answer prompt. Do not patch the native prompt, call, schemas, token reconciliation, timers, grader, citation/abstention gates, or reference answers. Do not use `code_context_eval.py` as a substitute.

The original native call measures subprocess wall time and reconciles root/child session usage; the existing report uses those receipts. Original retrieval `seconds` is measured separately. `/tmp/report_luna_graf.py` demonstrates the original export formula: end-to-end seconds = answer seconds + retrieval seconds, total tokens = input + output. New retrieval measurements must be real; copied old retrieval times would not measure the changed implementation. Existing grader is GPT-6-astra medium, timeout 1200 seconds, and strict pass requires citation and abstention gates plus correct, complete, supported. Preserve known rubric defects; this investigation proposes no corrections.

## Source-code-only execution scope

The original runner has no `--kind` filter. Existing `native.run`, `native.grade`, and `native.report` all obey `run.json["schedule"]`. For the requested code-only scope, a **new** run manifest can select the original ten code rows, retaining their order, identities, questions, gold and model settings. This is an explicit workload subset; disclose it rather than presenting the new manifest as identical to the original 26-row manifest. Do not edit the old run manifest or source corpus.

The original code schedule is:

```text
code-03 trial-0048
code-06 trial-0068
code-09 trial-0099
code-02 trial-0148
code-01 trial-0251
code-10 trial-0460
code-08 trial-0525
code-05 trial-0607
code-04 trial-0660
code-07 trial-0796
```

Keep both frozen source collections, both original gold files, catalog, and all 26 preparation records when staging so unchanged verification and the original 26-retrieval preparation assertion remain truthful. Change only the code retrieval context and associated real retrieval evidence/measurement in that new preparation. Retain the exact implementation hashes and treatment hash. Add truthful separate provenance for the new Graf implementation and changed response records; an unchanged old treatment description alone would misdescribe the new response content. Do not copy trial folders, `completed.json`, or generated score/summary files into the new execution root. Existing receipts cause native answering/grading to skip; preexisting completion files collide with exclusive writes.

Do not invoke original `prepare`: it creates the hard-coded database `graf_luna_low_20260927`, reindexes and retrieves using the old response construction. It is unnecessary for frozen-source reuse and unsuitable as a fresh-run staging command.

## Deterministic restoration recipe for the lead

This recipe is supplied for the lead, **not executed here**. It creates only the recovered original runner and refuses overwrite. The in-memory extraction and resulting hash were actually verified in this review.

```python
import hashlib, json, re, subprocess
from pathlib import Path

session = Path('/home/wenga/.codex/sessions/2026/09/26/rollout-2026-09-26T20-37-59-01a0df02-85bc-7f13-a91b-af4712c58ee8.jsonl')
matches = []
for line in session.open():
    text = json.loads(line).get('payload', {}).get('input', '')
    if 'cat > benchmarks/luna_graf.py' in text:
        command = json.loads(re.search(r'cmd:("(?:\\.|[^"\\])*")', text).group(1))
        matches.append(command.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0] + '\n')
assert len(matches) == 1
result = subprocess.run([
    '/home/wenga/src/docworm/evidencekg/.venv/bin/ruff', 'check', '--no-cache',
    '--config', 'evidencekg/pyproject.toml', '--fix',
    '--stdin-filename', 'benchmarks/luna_graf.py', '-'
], input=matches[0], text=True, capture_output=True, check=True)
data = result.stdout.encode()
assert hashlib.sha256(data).hexdigest() == '8beb7b31db663228e53117fa424ee2508e8bf7d3c407c75c51605f6c8ae6c90c'
with Path('benchmarks/luna_graf.py').open('xb') as target:
    target.write(data)
```

## Remaining boundaries

This is provenance and execution-path review, not a benchmark result. The lead owns restoration, new artifact staging, retrieval implementation, actual timing, subscription authentication and live execution. No new benchmark runner or scoring logic is needed. Canonical Hindsight recall returned 404; local memory only supplied general historical benchmark context, with current hashes and behavior independently verified from artifacts/source. Old experiments and existing dirty work were preserved.
