<script lang="ts">
  import { onDestroy } from "svelte";
  import { api, ApiError, isReady, type Doc, type Status } from "./api";
  export let status: Status;
  let opened = false,
    kind = "unsupported",
    offset = 0,
    version = 0;
  let result: { items: Doc[]; total: number } | null = null;
  let error = "";
  let request: AbortController | null = null;
  onDestroy(() => {
    version++;
    request?.abort();
  });
  $: ready = isReady(status);
  $: revision = status.revision;
  $: load(opened && ready, revision, kind, offset);
  async function load(
    enabled: boolean,
    _revision: number,
    filter: string,
    page: number,
  ) {
    const v = ++version;
    request?.abort();
    request = null;
    result = null;
    error = "";
    if (!enabled) return;
    const controller = new AbortController();
    request = controller;
    try {
      const response = await api<NonNullable<typeof result>>(
        `/document-issues?kind=${filter}&offset=${page}&limit=50`,
        { signal: controller.signal },
      );
      if (v === version) result = response;
    } catch (e) {
      if (v === version)
        error =
          e instanceof ApiError && e.status === 404
            ? "This report requires the updated Graf backend. Restart Graf after the current scan finishes, then reopen this report."
            : (e as Error).message;
    } finally {
      if (request === controller) request = null;
    }
  }
</script>

<section class="issues" aria-label="Processing issues">
  <h2>Processing issues</h2>
  <p>
    Review unsupported files, incomplete extraction, and processing failures,
    including attachments. Originals are unchanged.
  </p>
  {#if !ready}
    <p class="notice">
      The file list becomes available when this collection finishes processing
      successfully. Counts during the scan are provisional.
    </p>
  {:else}
    <button aria-expanded={opened} on:click={() => (opened = !opened)}
      >{opened ? "Hide processing issues" : "Review processing issues"}</button
    >
    {#if opened}
      <label for="issue-kind">Show</label>
      <select id="issue-kind" bind:value={kind} on:change={() => (offset = 0)}>
        <option value="unsupported">Unsupported files</option>
        <option value="partial">Partial extraction</option>
        <option value="failed">Failed extraction</option>
        <option value="all">All processing issues</option>
      </select>
      {#if error}<p class="notice error" role="alert">{error}</p>
        <button on:click={() => load(opened && ready, revision, kind, offset)}
          >Retry loading issues</button
        >
      {:else if !result}<p role="status">
          Verifying source files and loading processing issues. Large
          collections can take several minutes. You can hide this report to
          cancel waiting.
        </p>
      {:else}
        <p role="status">
          {result.total} matching document{result.total === 1 ? "" : "s"}
        </p>
        {#each result.items as doc (doc.id)}
          <article class="issue">
            <strong>{doc.path}</strong>
            <p>{doc.status.replaceAll("_", " ")}</p>
            {#if doc.warnings.length}<ul>
                {#each doc.warnings as warning}<li>{warning}</li>{/each}
              </ul>
            {:else}<p>
                {doc.status === "unsupported"
                  ? "No supported extractor is available for this file type."
                  : "No further explanation was recorded for this document."}
              </p>{/if}
          </article>
        {:else}<p>No documents match this filter.</p>{/each}
        <div class="row pagination">
          <button
            disabled={offset === 0}
            on:click={() => (offset = Math.max(0, offset - 50))}
            >Previous issues</button
          >
          <span
            >{result.total
              ? `${offset + 1}–${Math.min(offset + 50, result.total)} of ${result.total}`
              : "0 documents"}</span
          >
          <button
            disabled={offset + 50 >= result.total}
            on:click={() => (offset += 50)}>Next issues</button
          >
        </div>
      {/if}
    {/if}
  {/if}
</section>

<style>
  .issues {
    margin: 1.5rem 0;
    padding: 1.25rem;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface);
  }
  h2 {
    margin-top: 0;
  }
  label {
    display: block;
    margin-top: 1rem;
  }
  .issue {
    border-top: 1px solid var(--line);
    padding: 1rem 0;
    overflow-wrap: anywhere;
  }
  .issue p {
    margin: 0.4rem 0;
  }
  select {
    margin: 0.5rem 0;
  }
  .pagination {
    flex-wrap: wrap;
  }
</style>
