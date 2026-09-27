# ChatGPT web benchmark: start here

Start with **Google Drive + the 1,000-document archive (16 questions)**. Optionally run **GitHub + the code export (10 questions)**. Each is a separate workload. The document archive is fictional; the code comes from a frozen repository revision. This pack contains current corrected questions, not the historical pilot's exact frozen artifacts.

## Put only sources in the connected collection

- `UPLOAD-documents.zip`: 1,000 individual source files for a dedicated Drive folder, or a dedicated GitHub repository.
- `UPLOAD-code.zip`: exported source files for a dedicated GitHub repository (or a separate Drive condition).
- `prompts/`: one complete copy-paste prompt per question; keep these locally.
- `LOCAL-ONLY/`: reference answers, questions, source hashes and pack identity. **Never upload this folder or connect it to the answering ChatGPT account.**
- `results/`: prefilled run log and one answer file per question. Return these after testing.

Extract the relevant ZIP locally. Upload the **extracted files**, preserving directories, not the ZIP itself. Do not upload the entire pack or the original Graf repository: it contains benchmark definitions and published results. Use a dedicated source-only collection. Keep answer keys, prompts, results and this guide outside all connected collections used by the answering session. Restrict the connection to the source collection where your account permits.

The GitHub export preserves source text and paths but appends `.txt` to source formats (for example `api.py.txt`), matching the existing benchmark's export. It is an evidence collection, not a runnable repository. Use an empty repository without the original Git history, copy only the extracted sources into its default branch, then record the resulting remote commit. Do not update it during the experiment. A prompt naming a commit is not proof the connector honored it; check cited contents against this frozen copy afterwards.

For Drive, upload the extracted folder tree and record its folder URL, upload completion time and file count. Keep names and content unchanged. A successful upload alone does not establish connector searchability. If conversion to Google Docs or another format is necessary, stop and record that change as a new condition; retain original-to-remote file mappings and verify the converted text. Do not silently merge the archive into a single document: that changes retrieval granularity.

## Connect and check access

In ChatGPT on the web, install/connect the Google Drive or GitHub integration available in your account, then select that integration in a new chat. Current official guidance describes connecting services through plugins and choosing the model/reasoning level in the composer: [Plugins](https://learn.chatgpt.com/docs/plugins), [ChatGPT on the web](https://learn.chatgpt.com/docs/web) (checked 2026-09-26). Exact availability, permissions and supported formats must be checked in your account.

Run `PREFLIGHT.txt` in a disposable chat. For documents, use `operations/000/record_00000.txt` (a distractor), then also try a `.md` distractor such as `operations/000/record_00001.md`. For code, use `evidencekg/pyproject.toml.txt`. Compare returned lines against the local sources. Check both direct opening and searching by the filename. Wait for any displayed sync/indexing completion before starting. Sample access does not prove every file was indexed; record coverage limitations.

Save the preflight response and setup duration in `results/setup.md`. If the connection cannot read these formats, report the problem before scored runs. Direct chat uploads or pasted source excerpts are separate conditions, not substitutes inside a connector run.

## Run the questions

1. Choose the displayed model, reasoning level and Chat/Work mode once. Record their exact UI labels and your plan in the run log. Prefer a matching model/effort to the Graf pilot if available, but do not assume UI and CLI names establish identical backends. If unavailable, use your chosen web model consistently and label it as a different system.
2. Use a fresh chat for **each** question, outside projects with accumulated context. Disable reference to saved memory/chat history and custom instructions for the experiment where possible; record any settings you cannot control. Do not use a chat in which an answer key has been discussed.
3. Follow `results/run-log.csv` order, filtering to the workload you chose. This is one repetition (a pilot). For three repetitions, decide before starting, copy the full schedule and create new answer files for r2/r3; do not choose only failed or successful questions to repeat.
4. Open that question's file in `prompts/`. Replace only the SOURCE placeholder with the dedicated folder link or repository and frozen commit. Select the intended connection and paste the whole prompt. Save the exact submitted prompt with its response.
5. Measure wall time yourself from pressing Send until the final answer completes. Predeclare a 10-minute limit per question; stop and retain the partial output if it expires. This user-facing latency includes connector and UI delays. Record UTC timestamps and elapsed seconds; missing timing stays blank.
6. Save the complete unedited response, clickable citation destinations, visible source/tool activity, and any error message in the named answer file. A screenshot or chat link can supplement the text; do not rely solely on a link that another account may not access.
7. Mark status `completed`, `access_error`, `timeout`, `rate_limited`, `interrupted`, or `not_run`. If it asks for clarification, retain that response as the first attempt; do not coach it toward a correct answer. Record any follow-up separately. Do not regenerate or silently replace an answer.

Keep all 16 document questions or all 10 code questions in the chosen workload's denominator, including errors and missing trials. If rate-limited, retain the attempt, pause and record the gap. Any later retry gets a new attempt ID and retains the original. Do not enable paid overage or API billing for this exercise.

To compare **Drive vs GitHub retrieval**, put the SAME document ZIP contents on both services, use identical questions/model/settings, maintain separate result logs and alternate which connector goes first by question. Comparing Drive documents with GitHub code does not isolate a connector effect. Do not mix ordinary chat and deep-research runs in one condition.

## Send back

Send the entire `results/` folder (ZIP is fine), plus `LOCAL-ONLY/manifest.json` and any differences from the instructions. Keep the gold files locally for the evaluator; never put them into the answering session. We can then check answers against the frozen sources and reference rubric.

We will evaluate correctness, completeness, exact-quote validity, whether evidence supports each claim, and appropriate uncertainty/abstention. Alternative valid evidence is acceptable after review; matching a designated quote alone is not semantic correctness. Access failures remain distinct from justified abstention. Report per-question outcomes, strict supported answers / all scheduled questions, and latency with its measured denominator. No post-hoc exclusions without a documented rubric defect and symmetric treatment.

Web token consumption, internal reasoning and hidden retrieval calls remain unknown unless the product exposes actual receipts. Do not use the model's guesses or output word counts as usage measurements. This is a native ChatGPT-plus-connector system comparison, not an isolated graph experiment or an exact reproduction of the controlled CLI agent. Historical pilot comparisons are descriptive until corpus bytes, questions, rubrics and settings are reconciled; a fresh matched Graf run would be needed for a controlled comparison. These are authored development questions, not a new held-out test.

## Recreate locally

From the repository root, with Python 3.12+ (standard library only; no model or database calls):

```sh
python3 -m benchmarks.prepare_web .evidencekg-benchmarks/chatgpt-web --count 1000
```

The output directory must not exist. Use a different directory for another pack. `LOCAL-ONLY/manifest.json` records source hashes, current commit and generator identities. Preparation performs no uploads, commits, pushes or external writes.
