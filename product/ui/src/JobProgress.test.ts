import { expect, it } from "vitest";
import { render, screen } from "@testing-library/svelte";
import JobProgress from "./JobProgress.svelte";
import ProcessingStatus from "./ProcessingStatus.svelte";
import type { Job, Status } from "./api";
const job = (progress: unknown, phase = "ingesting") =>
  ({ state: "running", phase, progress }) as Job;

it("reports actual source completion while keeping failures and embedded documents visible", () => {
  render(JobProgress, {
    job: job({
      processed_files: 25,
      total_files: 100,
      processed_documents: 31,
      failed_documents: 2,
      partial_documents: 1,
    }),
  });
  const bar = screen.getByRole("progressbar") as HTMLProgressElement;
  expect(bar.value).toBe(25);
  expect(bar.max).toBe(100);
  expect(screen.getByText("25%")).toBeTruthy();
  expect(screen.getByText("2 failed")).toBeTruthy();
  expect(
    screen.getByText("31 document records processed, including attachments."),
  ).toBeTruthy();
});
it("never treats the legacy staged-file count as completed ingestion", () => {
  render(JobProgress, { job: job({ files: 8594 }) });
  expect(screen.getByRole("progressbar").hasAttribute("value")).toBe(false);
  expect(screen.queryByText("100%")).toBeNull();
  expect(
    screen.getByText(/completed-file count is not available/),
  ).toBeTruthy();
});
it("labels 100 percent file completion as extraction, not overall readiness", () => {
  render(JobProgress, {
    job: job({ processed_files: 10, total_files: 10, stage: "finalizing" }),
  });
  expect(screen.getByText("100%")).toBeTruthy();
  expect(screen.getByText(/before building the search index/)).toBeTruthy();
});
it("uses an indeterminate bar for indexing even when file counts are complete", () => {
  render(JobProgress, {
    job: job({ processed_files: 10, total_files: 10 }, "preparing"),
    phase: "preparing",
  });
  expect(screen.getByRole("progressbar").hasAttribute("value")).toBe(false);
  expect(screen.getByText(/Search indexing is still in progress/)).toBeTruthy();
});
it.each([
  { processed_files: 11, total_files: 10 },
  { processed_files: -1, total_files: 10 },
  { processed_files: 0, total_files: 0 },
])(
  "does not invent a percentage for invalid or empty totals: %j",
  (progress) => {
    render(JobProgress, { job: job(progress) });
    expect(screen.getByRole("progressbar").hasAttribute("value")).toBe(false);
  },
);
it("does not expose progress from a superseded source revision", () => {
  render(ProcessingStatus, {
    status: {
      state: "updating",
      phase: "ingesting",
      revision: 6,
      counts: {},
      jobs: [
        { ...job({ processed_files: 99, total_files: 100 }), revision: 5 },
      ],
    } as Status,
    onActivity: () => {},
    onSources: () => {},
  });
  expect(screen.queryByText("99%")).toBeNull();
  expect(screen.getByRole("progressbar").hasAttribute("value")).toBe(false);
});

it("retains file totals and unsupported outcomes after extraction finishes", () => {
  render(JobProgress, {
    job: {
      ...job({
        processed_files: 10,
        total_files: 10,
        processed_documents: 12,
        unsupported_documents: 2,
      }),
      state: "succeeded",
      phase: "complete",
    },
    phase: "complete",
  });
  expect(screen.getByText("10 of 10 source files processed.")).toBeTruthy();
  expect(screen.getByText("2 unsupported")).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
});
it("keeps the gap count visible for an older completed job", () => {
  render(ProcessingStatus, {
    status: {
      workspace_name: "Test",
      published_revision: 6,
      snapshot_id: "snapshot",
      message: "Some documents need review.",
      models_ready: true,
      device: "cpu",
      state: "ready_with_gaps",
      phase: "idle",
      revision: 6,
      counts: { gaps: 4 },
    } as Status,
    onActivity: () => {},
    onSources: () => {},
  });
  expect(screen.getByText(/4 reported gaps/)).toBeTruthy();
});

it("shows measured GPU window progress and keeps finalization separate", async () => {
  const job = {
    state: "running",
    phase: "preparing",
    progress: {
      processed_files: 8,
      total_files: 8,
      indexing_stage: "embedding",
      indexing_completed: 2,
      indexing_total: 4,
      indexing_device: "cuda",
      indexing_device_name: "Test GPU",
    },
  } as Job;
  const { rerender } = render(JobProgress, { job, phase: "preparing" });
  expect(screen.getByText("50%")).toBeTruthy();
  expect(screen.getByText(/2 of 4 search windows encoded/)).toBeTruthy();
  expect(screen.getByText(/Processing on GPU · Test GPU/)).toBeTruthy();
  expect(screen.getByRole("progressbar").getAttribute("value")).toBe("2");
  await rerender({
    job: {
      ...job,
      progress: {
        ...(job.progress as Record<string, unknown>),
        indexing_completed: 4,
      },
    },
    phase: "preparing",
  });
  expect(screen.getByText(/still needs to be saved and verified/)).toBeTruthy();
  await rerender({
    job: {
      ...job,
      progress: { indexing_stage: "saving_index", indexing_device: "cpu" },
    },
    phase: "preparing",
  });
  expect(screen.getByText("Saving search index")).toBeTruthy();
  expect(screen.queryByText("100%")).toBeNull();
  expect(screen.getByRole("progressbar").hasAttribute("value")).toBe(false);
  expect(screen.getByText(/Processing on CPU/)).toBeTruthy();
});

it("does not invent window progress for invalid counters or a failed job", () => {
  render(JobProgress, {
    job: {
      state: "failed",
      phase: "preparing",
      progress: {
        indexing_stage: "embedding",
        indexing_completed: 5,
        indexing_total: 4,
        processed_files: 8,
        total_files: 8,
      },
    } as Job,
    phase: "preparing",
  });
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.getByText("Stopped")).toBeTruthy();
  expect(screen.queryByText("100%")).toBeNull();
});
