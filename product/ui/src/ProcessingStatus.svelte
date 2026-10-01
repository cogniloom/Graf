<script lang="ts">
  import type { Status, Job } from "./api";
  import { jobProgress } from "./progress";
  import JobProgress from "./JobProgress.svelte";
  import Icon from "./Icon.svelte";
  export let status: Status;
  export let onActivity: () => void;
  export let onSources: () => void;
  $: job = status.jobs?.find(
    (entry) =>
      entry.revision === status.revision && entry.state !== "superseded",
  );
  $: previous =
    job?.state === "queued"
      ? status.jobs?.find(
          (entry) =>
            entry.state === "running" && entry.revision !== status.revision,
        )
      : undefined;
  $: scanJob =
    status.state === "updating" &&
    status.source_scan &&
    job?.state === "queued" &&
    !previous
      ? ({
          id: "source-check",
          state: "running",
          phase: "inventory",
          progress: status.source_scan,
          started_at: null,
          finished_at: null,
          error: null,
        } satisfies Job)
      : undefined;
  $: displayJob = previous ?? scanJob ?? job;
  $: phase = displayJob?.phase ?? status.phase;
  $: working =
    displayJob?.state === "running" ||
    (!displayJob && status.state === "updating");
  $: empty = status.state === "empty";
  $: blocked = !working && status.state === "blocked";
  $: title = empty
    ? "Bring your sources into Graf"
    : status.state === "ready_with_gaps"
      ? "Your collection is ready, with some gaps"
      : previous
        ? "Switching to updated sources"
        : jobProgress(displayJob, phase).title;
</script>

<section
  class="processing-banner"
  class:attention={blocked || status.state === "ready_with_gaps"}
  aria-label="Collection progress"
>
  <div class="processing-icon">
    <Icon name={empty ? "Sources" : "Activity"} />
  </div>
  <div class="processing-content">
    <div class="processing-heading">
      <h2>{title}</h2>
      <span class="status-chip"
        >{working
          ? "Background task"
          : empty
            ? "Get started"
            : blocked
              ? "Needs attention"
              : status.state.replaceAll("_", " ")}</span
      >
    </div>
    {#if previous}<p class="processing-description">
        The latest snapshot is queued while the earlier scan stops. Progress
        below belongs to that earlier scan (revision {previous.revision}), not
        the new snapshot.
      </p>
    {:else if scanJob}<p class="processing-description">
        Checking the selected sources for changes. The snapshot build starts
        after this check.
      </p>
    {:else if working}<p class="processing-description">
        Your workspace stays available while we prepare your documents.
      </p>
    {:else}<p class="processing-description">{status.message}</p>{/if}
    <JobProgress job={displayJob} {phase} />
    {#if status.counts.gaps > 0}<p class="progress-detail">
        {status.counts.gaps.toLocaleString()} reported gaps in this collection. Review
        Sources and Activity for details.
      </p>{/if}
  </div>
  <button class="processing-link" on:click={empty ? onSources : onActivity}
    >{empty ? "Add sources" : "View activity"}<span aria-hidden="true">↗</span
    ></button
  >
</section>
