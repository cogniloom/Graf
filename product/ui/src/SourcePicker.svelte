<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { api } from "./api";
  export let kind: "file" | "directory";
  export let select: (path: string) => void;
  export let close: () => void;
  type Listing = {
    path: string;
    parent: string | null;
    items: { name: string; path: string; kind: string }[];
    total: number;
  };
  let dialog: HTMLDialogElement;
  let listing: Listing | null = null;
  let location = "",
    error = "",
    loading = false,
    offset = 0;
  let request = 0;
  onMount(() => {
    dialog.showModal();
    browse();
  });
  onDestroy(() => {
    request++;
  });
  async function browse(path?: string, page = 0) {
    const current = ++request;
    loading = true;
    error = "";
    try {
      const query = new URLSearchParams({ offset: String(page) });
      if (path) query.set("path", path);
      const result = await api<Listing>(`/filesystem?${query}`);
      if (current !== request) return;
      listing = result;
      location = result.path;
      offset = page;
    } catch (e) {
      if (current === request) error = (e as Error).message;
    } finally {
      if (current === request) loading = false;
    }
  }
</script>

<dialog bind:this={dialog} on:close={close} aria-labelledby="picker-title">
  <h2 id="picker-title">Choose a {kind === "directory" ? "folder" : "file"}</h2>
  <p>Browse this computer. Your originals stay in place.</p>
  <form on:submit|preventDefault={() => browse(location)}>
    <label for="browse-location">Folder location</label>
    <div class="row">
      <input id="browse-location" bind:value={location} required />
      <button disabled={loading}>Open</button>
    </div>
  </form>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  {#if loading}<p role="status">Loading folder…</p>{/if}
  {#if listing}
    <p class="path">{listing.path}</p>
    <button
      disabled={loading || !listing.parent}
      on:click={() => browse(listing?.parent ?? undefined)}
      >Up one folder</button
    >
    <ul aria-label="Files and folders" aria-busy={loading}>
      {#each listing.items.filter((item) => kind === "file" || item.kind === "directory") as item}
        <li>
          <button
            disabled={loading}
            on:click={() =>
              item.kind === "directory" ? browse(item.path) : select(item.path)}
          >
            <span aria-hidden="true"
              >{item.kind === "directory" ? "▸" : "◇"}</span
            >
            {item.name}{item.kind === "directory" ? "/" : ""}
          </button>
        </li>
      {:else}<li class="muted">
          {kind === "directory"
            ? "No subfolders on this page. You can select this folder."
            : "This folder is empty."}
        </li>{/each}
    </ul>
    {#if listing.total > 200}<div class="row">
        <button
          disabled={loading || offset === 0}
          on:click={() => browse(listing?.path, offset - 200)}>Previous</button
        >
        <span
          >{offset + 1}–{Math.min(offset + 200, listing.total)} of {listing.total}</span
        >
        <button
          disabled={loading || offset + 200 >= listing.total}
          on:click={() => browse(listing?.path, offset + 200)}>Next</button
        >
      </div>{/if}
  {/if}
  <div class="row actions">
    <button on:click={close}>Cancel</button>
    {#if kind === "directory"}<button
        class="primary"
        disabled={loading || !listing || !!error}
        on:click={() => listing && select(listing.path)}
        >Select this folder</button
      >{/if}
  </div>
</dialog>

<style>
  dialog {
    width: min(640px, calc(100vw - 32px));
    max-height: calc(100dvh - 32px);
    overflow: auto;
  }
  input {
    min-width: 0;
    flex: 1;
  }
  .path {
    overflow-wrap: anywhere;
  }
  ul {
    list-style: none;
    padding: 0;
    max-height: 40dvh;
    overflow: auto;
  }
  li button {
    width: 100%;
    text-align: left;
    overflow-wrap: anywhere;
    margin-bottom: 4px;
  }
  .actions {
    justify-content: flex-end;
    margin-top: 16px;
  }
</style>
