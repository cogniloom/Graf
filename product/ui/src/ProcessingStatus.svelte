<script lang="ts">
  import type { Status } from "./api";
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
  $: phase = job?.phase ?? status.phase;
  $: working =
    job?.state === "running" || (!job && status.state === "updating");
  $: empty = status.state === "empty";
  $: blocked = !working && status.state === "blocked";
  $: title = empty
    ? "Bring your sources into Graf"
    : status.state === "ready_with_gaps"
      ? "Your collection is ready, with some gaps"
      : jobProgress(job, phase).title;
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
    {#if working}<p class="processing-description">
        Your workspace stays available while we prepare your documents.
      </p>
    {:else}<p class="processing-description">{status.message}</p>{/if}
    <JobProgress {job} {phase} />
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
