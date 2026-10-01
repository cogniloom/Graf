# Source processing and progress

Graf checks source contents, copies a verified snapshot, extracts documents, and
builds the search index before publishing a collection. Progress percentages refer
to the named stage, not to the entire process. Preparation reports copied bytes
and verified files, including updates while copying a large file. Checking sources
shows the current file and number checked with an indeterminate bar because a
complete inventory is not yet known. Queued jobs show their waiting state; if an
older scan is stopping, its progress is explicitly labelled as belonging to that
older scan. Real document changes still supersede an outdated build, which exits
at a throttled staging checkpoint instead of finishing a whole directory first.

Directory scans exclude the import helper's reserved operational files in a
`metadata` directory: `graf-import.log`, `graf-import-status.json`,
`graf-import.lock`, and `graf-document-verification.json`, including their `.tmp`
atomic-write files. Updating these files cannot trigger another evidence build.
Ordinary logs, other metadata, and directories with these names remain included;
symlinks remain rejected. Explicitly selecting one of these files still treats
it as evidence. No original is changed, and the selected source paths are kept.

Status responses contain progress summaries. Internal partial-publication source
inventories remain stored for recovery, but are not repeatedly transferred to the
browser with progress polling.

After a graceful restart, interrupted work is queued automatically and the
workspace immediately reports recovery as updating. Source checks then report
live scan progress; old interruption messages are cleared rather than presented
as errors requiring user action. Genuine failed jobs retain their failure state.
