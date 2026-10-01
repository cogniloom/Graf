<script lang="ts">
  import { onDestroy } from "svelte";
  import Relationship from "./Relationship.svelte";
  import SpeechConfidence from "./SpeechConfidence.svelte";
  import SourceReadings from "./SourceReadings.svelte";
  import { api, isReady, type Status, type Doc, type Detail } from "./api";
  export let status: Status;
  export let query: string;
  export let onSources: () => void;
  let docs: { items: Doc[]; total: number } | null = null,
    selected: string | null = null,
    detail: Detail | null = null;
  let offset = 0,
    passageOffset = 0,
    error = "",
    detailError = "",
    version = 0,
    detailVersion = 0,
    alive = true;
  let reviewReading = false;
  let reviewButton: HTMLButtonElement;
  function closeReading() {
    reviewReading = false;
    reviewButton?.focus();
  }
  $: if (!ready || !selected) reviewReading = false;
  onDestroy(() => {
    alive = false;
    version++;
    detailVersion++;
  });
  $: ready = isReady(status);
  $: reset(query);
  $: load(ready, status.revision, query, offset);
  $: inspect(ready, status.revision, selected, passageOffset);
  function reset(_query: string) {
    offset = 0;
    selected = null;
  }
  async function load(ok: boolean, _revision: number, q: string, page: number) {
    const v = ++version;
    docs = null;
    detail = null;
    error = "";
    if (!ok) return;
    try {
      const d = await api<NonNullable<typeof docs>>(
        `/documents?query=${encodeURIComponent(q)}&offset=${page}&limit=50`,
      );
      if (alive && v === version) docs = d;
    } catch (e) {
      if (alive && v === version) error = (e as Error).message;
    }
  }
  function select(id: string) {
    reviewReading = false;
    selected = id;
    passageOffset = 0;
  }
  async function inspect(
    ok: boolean,
    _revision: number,
    id: string | null,
    page: number,
  ) {
    const v = ++detailVersion;
    detail = null;
    detailError = "";
    if (!ok || !id) return;
    try {
      const d = await api<Detail>(
        `/documents/${encodeURIComponent(id)}?offset=${page}&limit=25`,
      );
      if (alive && v === detailVersion) detail = d;
    } catch (e) {
      if (alive && v === detailVersion) detailError = (e as Error).message;
    }
  }
</script>

{#if reviewReading && selected && ready}
  {#key `${status.revision}:${selected}`}
    <SourceReadings documentId={selected} onClose={closeReading} />
  {/key}
{/if}

<section class="management-page">
  <header class="page-heading">
    <p class="breadcrumb">Workspace / Documents</p>
    <h1>Find a source</h1>
    <p>
      Filter document names and paths. Start a research session to ask questions
      about their contents.
    </p>
  </header>
  {#if !ready}<div class="empty">
      <h2>
        {status.state === "empty"
          ? "Your evidence starts here"
          : status.state === "blocked"
            ? "Workspace needs attention"
            : "Preparing your evidence"}
      </h2>
      <p>{status.message}</p>
      <button on:click={onSources}>Manage sources</button>
    </div>{:else}
    {#if error}<p role="alert" class="notice error">{error}</p>{/if}
    <div class="document-workbench">
      <div>
        {#each docs?.items || [] as d}<button
            class="document-row"
            on:click={() => select(d.id)}
            ><strong>{d.path.split("/").pop()}</strong><small>{d.path}</small
            ><small
              >{d.status} · {d.passage_count} passages{d.warnings?.length
                ? ` · ${d.warnings.length} warnings`
                : ""}</small
            ></button
          >{:else}<p class="empty">
            {docs ? "No documents match your search." : "Loading documents…"}
          </p>{/each}{#if docs}<div class="row pagination">
            <button
              disabled={!offset}
              on:click={() => (offset = Math.max(0, offset - 50))}
              >Previous</button
            ><span
              >{docs.total
                ? `${offset + 1}–${Math.min(offset + 50, docs.total)} of ${docs.total}`
                : "0 documents"}</span
            ><button
              disabled={offset + 50 >= docs.total}
              on:click={() => (offset += 50)}>Next</button
            >
          </div>{/if}
      </div>
      {#if selected}<aside class="source-inspector" aria-label="Source details">
          <div class="row between">
            <h2>Source details</h2>
            <button
              aria-label="Close source details"
              on:click={() => (selected = null)}>×</button
            >
          </div>
          {#if detailError}<p role="alert">{detailError}</p>{/if}{#if detail}<h3
            >
              {detail.path.split("/").pop()}
            </h3>
            <p class="path">{detail.path}</p>
            <button
              bind:this={reviewButton}
              on:click={() => (reviewReading = true)}
              >Review PDF / image reading</button
            >
            <code>{detail.id}</code>{#each detail.warnings || [] as w}<p
                class="notice"
              >
                {w}
              </p>{/each}
            <h3>Passages ({detail.passages.length} shown)</h3>
            {#each detail.passages as p}<article class="passage">
                <SpeechConfidence locators={p.locators} />
                <blockquote>{p.text}</blockquote>
                <details>
                  <summary>Source locator</summary>
                  <pre>{JSON.stringify(p.locators, null, 2)}</pre>
                </details>
              </article>{/each}
            <div class="row">
              <button
                disabled={!passageOffset}
                on:click={() =>
                  (passageOffset = Math.max(0, passageOffset - 25))}
                >Previous passages</button
              ><button
                disabled={detail.next_offset == null}
                on:click={() => (passageOffset = detail!.next_offset!)}
                >Next passages</button
              >
            </div>
            {#if detail.truncated || detail.next_cursor}<p class="notice">
                This preview is bounded. More passages are available through the
                evidence tools.
              </p>{/if}
            <h3>
              Recorded relationships ({detail.relationships.length} shown{detail.relationships_total !==
              undefined
                ? ` of ${detail.relationships_total}`
                : ""})
            </h3>
            {#if detail.relationships_truncated}<p class="notice">
                Relationship preview is bounded. Use the evidence tools for the
                remaining records.
              </p>{/if}{#each detail.relationships as r}
              <Relationship
                record={r}
                documentId={detail.id}
                names={new Map((docs?.items || []).map((d) => [d.id, d.path]))}
                onSelect={select}
              />
            {:else}<p>
                No relationships returned.
              </p>{/each}{:else if !detailError}<p role="status">
              Loading source…
            </p>{/if}
        </aside>{/if}
    </div>{/if}
</section>
