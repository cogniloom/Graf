# File formats

Graf preserves original bytes. Extracted content is a searchable representation;
`partial`, `failed`, and `unsupported` remain visible in coverage. Reprocessing
creates a new extraction version; older snapshots remain immutable.

| Family | Extensions / detection | Useful representation and limits |
|---|---|---|
| Text and code | TXT, MD, RST, LOG, JSON/JSONL/NDJSON, XML, YAML, TOML, INI, source code, SRT/VTT, VCF; unknown suffixes with valid printable UTF-8 or BOM-marked UTF-16 | Text, whitespace preserved. Invalid text encodings are flagged. Binary files are not guessed as prose. |
| Tables | CSV, TSV | Quoted/multiline fields preserved in JSON row arrays with row locators. CSV uses commas; TSV uses tabs. |
| Web | HTML, HTM, XHTML | Visible static text; script/style/template content excluded. No scripts or remote resources run. |
| Calendar | ICS | Unfolded properties, including timezone and recurrence rules. No recurrence expansion or timezone interpretation. |
| Word | DOCX, DOCM, DOTX, DOTM, DOCT; DOC, DOT | Modern XML paragraphs/tables/comments/revisions and embedded objects. Legacy files use antiword. Layout/revision semantics remain unreviewed. Some very short or encrypted legacy files are unreadable by antiword. |
| Spreadsheets | XLSX, XLSM, XLTX, XLTM, XLST; XLS, XLT | Sheet/cell locators, values and modern formula text. No formula calculation or macros. Legacy cached values may be stale. Charts and embedded objects remain unreviewed. |
| Slides / open documents / books | PPTX, PPTM, POTX, ODT, ODS, ODP, EPUB, RTF | Text and part locators. Layout, embedded objects and repeated OpenDocument cells are not reconstructed. Legacy PPT and XLSB are not yet supported. |
| Archives | ZIP, RAR, 7Z/7ZIP, TAR, GZ/GZIP, BZ2/BZIP/BZIP2, XZ, TGZ, TBZ/TBZ2, TXZ; TAR.GZ, TAR.BZ2, TAR.BZIP | Members become individually searchable child documents with parent/part provenance. Compression layers remain in the ancestry. Duplicate names have distinct part IDs. |
| Compressed metafiles | EMZ | Decompressed EMF bytes retained as a child. EMF rendering/OCR is not implemented; an explicit visual gap remains. |
| macOS metadata | AppleDouble content signature, often `._` sidecars | Validated entry IDs, offsets and lengths; original metadata bytes retained. Resource/Finder semantics remain unreviewed. These sidecars do not contain the separate document data fork. |
| Audio / video | WAV, MP3, FLAC, OGG/OGA, OPUS, M4A/M4B, AAC, AIFF/AIF, WMA, MP4, MOV, MKV, WEBM, AVI and other listed media suffixes | Local tags, duration, codecs and stream metadata where readable. Optional offline speech transcription with timestamps, explicit unverified labels, decoder confidence and conservative abstention. See [speech transcription](TRANSCRIPTION.md). Visual understanding and speaker identification are not implemented. |
| Existing readers | PDF, EML, PNG/JPEG/TIFF/BMP/WebP and Pillow-readable GIF/ICO/PPM/PGM/PBM | PDF native text/annotations, attachments, email bodies/attachments, local OCR. Visual gaps remain. |

Content signatures recognize Office containers, archives, PDFs, common image and
media containers even without an extension. `xlst` and `doct` are accepted input
aliases; their actual contents must be a readable spreadsheet/Word format.
Renaming an unreadable file does not make it readable.

The knowledge layer additionally retains modern Word heading/style and table
coordinates, separates inserted/deleted revisions, and qualifies comments,
notes and unaccepted changes. It does not resolve Word revision acceptance or
full layout semantics. Email quoting/forward markers and HTML blockquotes keep
structural attribution; unmarked or ambiguous attribution remains unresolved.
Modern spreadsheets retain number formats, formulas and available cached values;
first-row header candidates remain unconfirmed. PDF text-run matrices/font sizes
are bounded parser estimates, not verified page coordinates or reading order.
See [automatic knowledge](AUTOMATIC_KNOWLEDGE.md) for exact limits.

When normal XLSX extraction fails, bounded streaming XML recovery can retain
complete cell records as `partial` evidence. It preserves raw values, formula
attributes, caches, coordinates and style references; number formats and displayed
dates/currency are explicitly uninterpreted. Recovery visits smaller sheets first
and caps emitted cell JSON at 2 MB; warnings identify unread cells and sheets.
It never repairs or overwrites the original workbook. AppleDouble metadata
inventory follows the header/entry layout in [RFC 1740](https://www.rfc-editor.org/rfc/rfc1740).

## Runtime dependencies

Python readers are pinned in `evidencekg/uv.lock`. On Debian/Ubuntu the optional
system readers are provided by `libarchive13` (RAR/7z), `antiword` (DOC/DOT), and
`ffmpeg` (ffprobe media metadata). Existing PDF/OCR prerequisites remain
`poppler-utils`, `tesseract-ocr`, language packs and `libseccomp2`. Release CI
installs these readers. Existing installations need the corresponding packages;
missing tools produce extraction gaps, never automatic downloads or billing.

Archive paths are never extracted onto the filesystem. Links, devices, absolute
paths and traversal names are rejected. Attachment count and aggregate-byte
budgets apply separately to each filesystem original and are shared by all its
nested descendants; unrelated originals cannot exhaust each other's budget.
Per-member, aggregate, count, depth,
process-memory and timeout limits apply. Encrypted/damaged members are recorded
as gaps. Incomplete container enumeration makes the source denominator unknown.
Original bytes are retained for recovery; passwords are not requested or guessed.

These deterministic readers do not run macros or fetch remote content. Binary
readers run under the existing network-denied parser worker. That worker is not
a complete filesystem sandbox against native parser vulnerabilities.

## Verification (28 September 2026)

- `test_file_formats.py`: **48 passed**, using the real isolated parser, including
  generated legacy DOC/XLS files, real 7z, encrypted ZIP rejection, bounded
  expansion, duplicate members, links/traversal, extensionless detection,
  deterministic media metadata, nested ingestion/search and retained-byte verification.
  Legacy Word checks used antiword from a temporary extracted distribution package.
- An upstream libarchive RAR fixture was also checked locally: regular files were
  recovered and its symbolic link was rejected. That downloaded fixture is not bundled.
- Broader Python/product suite: **533 passed, 45 skipped, 1 failed, 3 deselected**.
  The remaining discovery SDK timeout and the three separately run acceptance
  failures (OCR assertion, two SDK initialization timeouts) all reproduced on
  unchanged `4f4a5fa` with the same Python environment. No inherited failures were
  changed. Skips include opt-in PostgreSQL/model checks and are not passes.
- Scoped Ruff lint/format checks, `git diff --check` and frozen offline dependency
  synchronization passed. CI configuration was updated but not run on GitHub.
- Both supervised Orca worker launches failed at agent readiness before task
  delivery and were released. Independent review remains unverified. No browser,
  hosted deployment or semantic-accuracy claim follows from these local parser
  checks. Later speech-model verification is documented in [transcription](TRANSCRIPTION.md).
