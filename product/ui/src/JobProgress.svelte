<script lang="ts">
  import type { Job } from "./api";
  import { jobProgress } from "./progress";
  export let job: Job | null | undefined = undefined;
  export let phase = job?.phase ?? "queued";
  $: progress = jobProgress(job, phase);
  const number = (n: number) => n.toLocaleString();
</script>

<div class="job-progress">
  {#if progress.indexing}
    <div class="progress-caption">
      <span>{progress.indexingTitle}</span>
      <span class="progress-percent"
        >{progress.indexMeasured
          ? `${progress.indexPercent}%`
          : progress.active
            ? "In progress"
            : "Stopped"}</span
      >
    </div>
    {#if progress.indexMeasured}
      <progress
        class="progress-track"
        max={progress.indexTotal!}
        value={progress.indexCompleted!}
        aria-label="Search window encoding"
      ></progress>
      <p class="progress-detail">
        {number(progress.indexCompleted!)} of {number(progress.indexTotal!)} search
        windows encoded.
      </p>
      {#if progress.indexPercent === 100}<p class="progress-detail">
          Encoding is complete. The search index still needs to be saved and
          verified.
        </p>{/if}
    {:else if progress.active}
      <progress class="progress-track" aria-label={progress.indexingTitle}
      ></progress>
    {/if}
    {#if progress.indexingStage === "windowing" && progress.indexedPassages !== null && progress.totalPassages !== null && progress.indexedPassages <= progress.totalPassages}
      <p class="progress-detail">
        {number(progress.indexedPassages)} of {number(progress.totalPassages)} passages
        prepared.
      </p>
    {/if}
    {#if progress.device}<p class="progress-detail">
        Processing on {progress.device}.
      </p>{/if}
    <p class="progress-detail">
      Document extraction is complete. Search indexing {progress.active
        ? "is still in progress"
        : "has stopped"}.
    </p>
  {:else if progress.measured}
    <div class="progress-caption">
      <span
        ><strong>{number(progress.completed!)}</strong> of {number(
          progress.total!,
        )} source files {progress.staged ? "prepared" : "processed"}</span
      >
      <span class="progress-percent">{progress.percent}%</span>
    </div>
    <progress
      class="progress-track"
      max={progress.total!}
      value={progress.completed!}
      aria-label={progress.staged
        ? "Source preparation"
        : "Source file extraction"}
    ></progress>
    {#if progress.finalizing}<p class="progress-detail">
        File processing is complete. Finalizing the extraction before building
        the search index.
      </p>
    {:else if !progress.staged && progress.percent === 100}<p
        class="progress-detail"
      >
        File processing is complete. The collection is still being prepared.
      </p>{/if}
  {:else if progress.active}
    <div class="progress-caption">
      <span
        >{phase === "preparing"
          ? "Preparing documents for search"
          : "Processing in the background"}</span
      ><span class="progress-percent">In progress</span>
    </div>
    <progress class="progress-track" aria-label={progress.title}></progress>
    {#if phase === "ingesting"}<p class="progress-detail">
        {progress.preparedFiles !== null
          ? `${number(progress.preparedFiles)} files prepared. `
          : ""}A completed-file count is not available for this scan yet.
      </p>
    {:else if phase === "preparing"}<p class="progress-detail">
        Document extraction is complete. Search indexing is still in progress.
      </p>{/if}
  {/if}
  {#if !progress.measured && progress.completed !== null && progress.total !== null && progress.completed <= progress.total}
    <p class="progress-detail">
      {number(progress.completed)} of {number(progress.total)} source files processed.
    </p>
  {/if}
  {#if progress.documents !== null && progress.documents !== progress.completed}<p
      class="progress-detail"
    >
      {number(progress.documents)} document records processed, including attachments.
    </p>{/if}
  {#if progress.failed || progress.partial || progress.unsupported}<div
      class="progress-outcomes"
      aria-label="Document processing outcomes"
    >
      {#if progress.failed}<span class="outcome-failed"
          >{number(progress.failed)} failed</span
        >{/if}
      {#if progress.partial}<span>{number(progress.partial)} partial</span>{/if}
      {#if progress.unsupported}<span
          >{number(progress.unsupported)} unsupported</span
        >{/if}
    </div>{/if}
</div>
