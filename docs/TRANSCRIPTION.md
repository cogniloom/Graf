# Local speech transcription and confidence

Graf can transcribe the first audio stream of supported audio/video files using a
local faster-whisper model, on CPU with INT8 inference. No remote inference,
subscription/API billing, model download, translation, speaker identification or
visual interpretation takes place during ingestion. Original media remains the
source of truth. Every transcript is `partial`, marked `asr`, and shown as
**Automatic transcript — unverified** in the viewer and extracted text.

## Confidence does not establish correctness

Whisper can produce incorrect words, including confident mistakes. Graf's score
is **not a percentage probability of correctness** and has not been calibrated
on your recordings, languages, dialects or noise conditions. Do not treat a high
score as verified evidence. Listen to the original when a detail matters.

For each segment, the score is the minimum of the model's weakest word
probability and `exp(avg_logprob)`. Graf labels it:

- **High:** score at least 0.9, with valid alignment and no rejection flag.
- **Medium:** score at least the configured threshold (default 0.8), below 0.9.
- **Low / withheld:** below that threshold, no-speech probability above 0.35,
  compression ratio above 2.4 (repetitive output), or missing/invalid confidence
  or word alignment. Invalid segment timestamps reject the transcript.

A single weak word withholds the entire sentence-sized model segment, preserving
negation/context instead of silently deleting a doubtful word. The main index
contains an explicit unclear-speech placeholder, **not the guessed wording**.
This conservative policy may withhold correct speech too. Voice activity detection
can miss speech; an empty result is not proof of silence. Gaps and warnings remain
visible. Confidence badges persist even when passage boundaries split the text.

The `speech-transcription.json` extraction artifact retains all returned candidate
words, timings, scores and rejection reasons for deliberate review. It is not
indexed as source prose. Retrieve it through the existing original-region
artifact tool; candidate text remains unverified. Accepted words and word scores
are also included in source locators. Model hashes, runtime versions and the
confidence policy are bound to the extraction; model changes invalidate cached
extractions and a model change during inference rejects the result.

Decoder settings use speech detection, zero temperature, no previous-text prompt,
no supplied hints and `task=transcribe`. These controls reduce some failure modes;
they cannot guarantee a correct transcript. Algorithm/API reference:
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), including
[the decoder implementation](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py).

Uncertain intervals receive a bounded alternate decode using the same loaded
local model: at most 30 seconds in total, ten seconds per interval. Alternative
words remain in the audit artifact and do not replace the primary transcript or
release withheld wording. These are dependent readings of one recording, not
independent corroboration. Low-score negation/number diagnostics and original
timestamps are preserved; retry failures leave the primary uncertainty intact.

An optional reference calibration can annotate transcription fidelity for an
explicit matching model, language and input domain. It keeps decoder scores
separate and never changes withholding, speaker identity or claim truth.
Missing/mismatched reference evidence leaves probability unknown. See the
[calibration workflow and input contract](AUTOMATIC_KNOWLEDGE.md) for fitting a
local isotonic model with separate source-family validation. No calibration
accuracy on real user recordings is established by synthetic regression tests.

## Enable

Supply an existing local CTranslate2 Whisper model directory with `model.bin`,
`config.json`, `tokenizer.json` and its vocabulary files. A Hugging Face snapshot
cache directory is supported; no weights are downloaded automatically. FFmpeg
must be installed. Missing models, dependencies or an audio stream produce an
explicit transcription gap, while readable metadata is retained.

For a new Graf installation:

```sh
./graf install --transcription-model /absolute/path/to/local-whisper --transcription-language en
```

The normal installer/update installs the Python speech runtime. A custom
`--runtime` must already include it. For development or a manually managed runtime:

```sh
uv sync --project evidencekg --extra app --extra transcription
```

For an existing installation, add these fields to its private `app.json`, restart
Graf and request a source rescan/rebuild. No existing installation is reconfigured
by this code change:

```json
{
  "transcription_model": "/absolute/path/to/local-whisper",
  "transcription_language": "en",
  "transcription_min_confidence": 0.8,
  "transcription_max_seconds": 600
}
```

Omit the language or use `null` for model detection; specify it when known.
The same keys are accepted by EvidenceKG's `init --config` and `configure` tools.
A null model keeps transcription disabled, with a visible gap for media.
The threshold accepts 0.5–1.0; reducing it admits more potentially wrong text.
Duration is bounded (default ten minutes, configurable up to one hour). Recordings
beyond the limit are rejected without indexing a misleading partial transcript.
The existing parser memory/CPU/timeout limits and network denial remain in force;
large models or slow recordings may exceed them and remain gaps. Longer material
can be split into source recordings before ingestion. Only the first audio stream
is processed; additional tracks are unreviewed.

## Verification boundary

Run deterministic confidence/configuration tests with:

```sh
uv run --project evidencekg --extra app --extra transcription python -m pytest evidencekg/tests/test_transcription.py -q
```

Set `EVIDENCEKG_TEST_SPEECH_MODEL` to existing local weights to enable the real
speech/silence/video test; it requires `espeak` and `ffmpeg`. No test downloads
models. Synthetic fixtures verify behavior, not recognition accuracy on your
files. Hosted PostgreSQL publication, private recordings, multiple speakers,
other languages and model-wide accuracy require separate verification.


Local implementation checks on 28 September 2026: 30 speech tests passed with the
cached faster-whisper-small model, including actual speech, silence and video
inference; a float-WAV check also passed. Real SQLite ingestion/search and artifact
integrity passed at an explicitly lower 0.5 threshold. The default 0.8 policy
withheld the tested public/synthetic speech rather than exposing weak words.
These examples demonstrate abstention, not calibrated confidence or an accuracy rate.
The broader run passed 562 tests with 46 skips and four previously reproduced
inherited OCR/MCP cases excluded. The dashboard passed 24 tests plus build,
typecheck and formatting. Desktop/mobile browser checks used synthetic API data;
independent review remains unavailable after Orca worker readiness failures.
