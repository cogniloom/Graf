import type { Job } from "./api";

const labels: Record<string, string> = {
  queued: "Waiting to start",
  recovered: "Resuming processing",
  inventory: "Checking source files",
  staging: "Preparing source files",
  ingesting: "Extracting documents",
  preparing: "Building search index",
  complete: "Processing complete",
  idle: "Collection ready",
  interrupted: "Processing interrupted",
  failed: "Processing needs attention",
  superseded: "Replaced by a newer scan",
};
export const phaseLabel = (phase: string) =>
  labels[phase] ?? phase.replaceAll("_", " ");
const count = (value: unknown): number | null =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;

export function jobProgress(
  job: Job | null | undefined,
  phase = job?.phase ?? "queued",
) {
  const raw = job?.progress;
  const p =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  const indexing = phase === "preparing";
  const stages: Record<string, string> = {
    loading_sources: "Loading extracted passages",
    verifying_sources: "Verifying source references",
    importing_snapshot: "Preparing search storage",
    loading_model: "Loading search model",
    waiting_for_index: "Waiting for the search index",
    windowing: "Preparing search windows",
    embedding: "Encoding search windows",
    saving_index: "Saving search index",
    verifying_index: "Verifying search index",
    finalizing: "Finalizing search index",
  };
  const indexingStage =
    typeof p.indexing_stage === "string" ? p.indexing_stage : "";
  const indexCompleted = count(p.indexing_completed);
  const indexTotal = count(p.indexing_total);
  const indexMeasured =
    indexing &&
    indexingStage === "embedding" &&
    indexCompleted !== null &&
    indexTotal !== null &&
    indexTotal > 0 &&
    indexCompleted <= indexTotal;
  const device =
    p.indexing_device === "cuda"
      ? `GPU${typeof p.indexing_device_name === "string" ? ` · ${p.indexing_device_name}` : ""}`
      : p.indexing_device === "cpu"
        ? "CPU"
        : null;
  const staged = phase === "staging";
  const completed = count(staged ? p.files : p.processed_files);
  const total = count(p.total_files);
  const finalizing = phase === "ingesting" && p.stage === "finalizing";
  const countable = ["staging", "ingesting"].includes(phase);
  const measured =
    countable &&
    completed !== null &&
    total !== null &&
    total > 0 &&
    completed <= total;
  const active = job?.state === "running" || (!job && countable);
  return {
    indexing,
    indexingTitle: stages[indexingStage] ?? "Building search index",
    indexingStage,
    indexCompleted,
    indexTotal,
    indexMeasured,
    indexPercent: indexMeasured
      ? Math.floor((indexCompleted! / indexTotal!) * 100)
      : null,
    indexedPassages: count(p.indexed_passages),
    totalPassages: count(p.total_passages),
    device,
    title: finalizing ? "Finalizing extraction" : phaseLabel(phase),
    completed,
    total,
    staged,
    finalizing,
    measured,
    active,
    // File completion measures extraction only, never overall publication readiness.
    percent: measured ? Math.floor((completed! / total!) * 100) : null,
    documents:
      count(p.processed_documents) ??
      (["preparing", "complete"].includes(phase) ? count(p.documents) : null),
    failed: count(p.failed_documents) ?? 0,
    partial: count(p.partial_documents) ?? 0,
    unsupported: count(p.unsupported_documents) ?? 0,
    preparedFiles: count(p.files),
  };
}
