<script lang="ts">
  import { onDestroy } from "svelte";
  import ProcessingIssues from "./ProcessingIssues.svelte";
  import SourcePicker from "./SourcePicker.svelte";
  import JobProgress from "./JobProgress.svelte";
  import { phaseLabel } from "./progress";
  let picker: "file" | "directory" | null = null;
  import { api, type Source, type Job, type Status } from "./api";
  export let view: string;
  export let tick: number;
  export let status: Status;
  export let refresh: () => Promise<void>;
  export let logout: () => Promise<void>;
  let sources: { items: Source[] } | null = null;
  let jobs: Job[] | null = null,
    settings: Record<string, unknown> | null = null;
  let path = "",
    error = "",
    loadError = "",
    message = "",
    busy = false,
    remove: Source | null = null,
    confirmDialog: HTMLDialogElement;
  let pendingPage: string | null = null;
  let version = 0,
    alive = true;
  onDestroy(() => {
    alive = false;
    version++;
  });
  $: load(view, tick);
  $: if (remove && confirmDialog && !confirmDialog.open)
    confirmDialog.showModal();
  async function load(page: string, _tick: number, force = false) {
    if (!force && pendingPage === page) return;
    pendingPage = page;
    const v = ++version;
    try {
      if (page === "Sources") {
        const result = await api<NonNullable<typeof sources>>("/sources");
        if (alive && v === version) {
          sources = result;
          loadError = "";
        }
      } else if (page === "Activity") {
        const result = await api<{ items: Job[] }>("/jobs");
        if (alive && v === version) {
          jobs = result.items;
          loadError = "";
        }
      } else {
        const result = await api<Record<string, unknown>>("/settings");
        if (alive && v === version) {
          settings = result;
          loadError = "";
        }
      }
    } catch (e) {
      if (alive && v === version) loadError = (e as Error).message;
    } finally {
      if (v === version) pendingPage = null;
    }
  }
  async function mutate(endpoint: string, method: string, body?: unknown) {
    busy = true;
    error = "";
    message = "";
    try {
      await api(endpoint, {
        method,
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      void refresh();
      await load(view, tick, true);
      message = "Workspace updated.";
      return true;
    } catch (e) {
      error = (e as Error).message;
      return false;
    } finally {
      busy = false;
    }
  }
</script>

<section class="management-page">
  <header class="page-heading">
    <p class="breadcrumb">Workspace / {view}</p>
    <h1>
      {view === "Sources"
        ? "Your sources"
        : view === "Activity"
          ? "Workspace activity"
          : "Workspace settings"}
    </h1>
    <p>
      {view === "Sources"
        ? "The documents behind your research. Add files or directories; Graf keeps the originals untouched."
        : view === "Activity"
          ? "Follow each processing stage, from source discovery to a published collection."
          : "A local workspace, connected on your terms."}
    </p>
  </header>
  {#if loadError}<p class="notice error" role="alert">{loadError}</p>{/if}
  {#if error}<p class="notice error" role="alert">
      {error}
    </p>{/if}{#if message}<p role="status" class="notice">{message}</p>{/if}
  {#if view === "Sources"}
    <div class="source-overview">
      <div>
        <span>COLLECTION</span><strong
          >{status.counts.documents ?? 0}<small>documents</small></strong
        >
      </div>
      <div>
        <span>EXTRACTION</span><strong
          >{status.counts.passages ?? 0}<small>passages</small></strong
        >
      </div>
      <div>
        <span>READINESS</span><strong class="readiness-word"
          >{status.state.replaceAll("_", " ")}<small>{status.phase}</small
          ></strong
        >
      </div>
    </div>
    <ProcessingIssues {status} />
    <form
      class="add-source"
      on:submit|preventDefault={async () => {
        if (await mutate("/sources", "POST", { path })) path = "";
      }}
    >
      <label for="source-path">File or directory path</label>
      <div class="row">
        <input
          id="source-path"
          required
          bind:value={path}
          placeholder="/absolute/path/to/source"
        /><button class="primary" disabled={busy || !path.trim()}
          ><span aria-hidden="true">＋</span> Add source</button
        >
      </div>
    </form>
    <div class="row">
      <button disabled={busy} on:click={() => (picker = "directory")}
        >Choose folder…</button
      >
      <button disabled={busy} on:click={() => (picker = "file")}
        >Choose file…</button
      >
    </div>
    {#if picker}
      <SourcePicker
        kind={picker}
        close={() => (picker = null)}
        select={(selected) => {
          path = selected;
          picker = null;
        }}
      />
    {/if}
    <div class="section-heading">
      <h2>Registered sources</h2>
      <span>{sources?.items.length ?? "—"}</span>
    </div>
    {#if !sources && !error}<p role="status">Loading sources…</p>{/if}
    {#if sources?.items.length === 0}<div class="empty">
        <h3>Start with your first source</h3>
        <p>Add a file or directory above to build your evidence workspace.</p>
      </div>{/if}
    <div class="table-scroll">
      <table class="source-table">
        <thead
          ><tr
            ><th>Source</th><th>Type</th><th>Status</th><th>Files</th><th
              >Controls</th
            ></tr
          ></thead
        ><tbody
          >{#each sources?.items || [] as s}<tr
              ><td
                ><strong>{s.path.split("/").pop()}</strong><small class="path"
                  >{s.path}</small
                >{#if s.error}<p class="error">{s.error}</p>{/if}</td
              ><td>{s.kind}</td><td
                ><span class="source-status"
                  ><i class="state-dot"></i>{s.status}</span
                ></td
              ><td>{s.file_count}</td><td
                ><div class="row">
                  <button
                    disabled={busy}
                    aria-label={`Rescan ${s.path}`}
                    on:click={() =>
                      mutate(
                        `/sources/${encodeURIComponent(s.id)}/rescan`,
                        "POST",
                      )}>Rescan</button
                  ><label class="check"
                    ><input
                      type="checkbox"
                      checked={s.enabled}
                      disabled={busy}
                      on:change={(e) =>
                        mutate(
                          `/sources/${encodeURIComponent(s.id)}`,
                          "PATCH",
                          { enabled: e.currentTarget.checked },
                        )}
                    />Enabled</label
                  ><button
                    class="text-button"
                    aria-label={`Remove ${s.path}`}
                    disabled={busy}
                    on:click={() => (remove = s)}>Remove</button
                  >
                </div></td
              ></tr
            >{/each}</tbody
        >
      </table>
    </div>
    <div class="source-note">
      <span>↳</span>
      <p>
        Removing a source changes the current collection. Historical session
        evidence is retained until explicitly erased through its audit workflow.
      </p>
    </div>
    {#if remove}<dialog
        bind:this={confirmDialog}
        on:cancel={() => (remove = null)}
        aria-labelledby="remove-title"
      >
        <h2 id="remove-title">Remove this source?</h2>
        <p>{remove.path}</p>
        <p>
          This removes it from current evidence. Original files are preserved;
          historical audit evidence is retained.
        </p>
        {#if error}<p role="alert">{error}</p>{/if}
        <div class="row">
          <button on:click={() => (remove = null)}>Cancel</button><button
            class="danger"
            disabled={busy}
            on:click={async () => {
              if (
                await mutate(
                  `/sources/${encodeURIComponent(remove!.id)}`,
                  "DELETE",
                )
              )
                remove = null;
            }}>Remove source</button
          >
        </div>
      </dialog>{/if}
  {:else if view === "Activity"}
    {#each jobs || [] as j}<article class="job-row">
        <div class="job-heading">
          <h2>{phaseLabel(j.phase || "queued")}</h2>
          <span class="status-chip">{j.state.replaceAll("_", " ")}</span>
        </div>
        <p class="muted">
          {j.started_at
            ? new Date(j.started_at).toLocaleString()
            : "Not started"}{j.finished_at
            ? ` → ${new Date(j.finished_at).toLocaleString()}`
            : ""}
        </p>
        <JobProgress job={j} phase={j.phase} />
        <details class="job-details">
          <summary>Processing details</summary>
          <p>Job <code>{j.id}</code></p>
          {#if j.attempts}<p>Attempt {j.attempts}</p>{/if}
        </details>
        {#if j.error}<p class="error">
            {j.error}
          </p>{/if}
      </article>{:else}<p class="empty">
        {jobs
          ? "No jobs yet. Add a source to get started."
          : "Loading activity…"}
      </p>{/each}
  {:else}
    <section class="settings-section">
      <h2>Codex connection</h2>
      <p>Codex may send selected evidence to its configured model provider.</p>
      {#if typeof settings?.codex_instructions === "string"}<pre>{settings.codex_instructions}</pre>
        <button
          on:click={async () => {
            try {
              await navigator.clipboard.writeText(
                String(settings?.codex_instructions),
              );
              message = "Instructions copied.";
            } catch {
              error =
                "Clipboard unavailable. Select and copy the instructions above.";
            }
          }}>Copy instructions</button
        >{:else}<p class="muted">
          {settings
            ? "Connection instructions are not available from this server."
            : "Loading configuration…"}
        </p>{/if}
    </section>
    {#if settings}<section class="settings-section">
        <h2>Configuration</h2>
        <dl class="settings">
          {#each Object.entries(settings).filter(([k]) => k !== "codex_instructions") as [k, v]}<div
            >
              <dt>{k.replaceAll("_", " ")}</dt>
              <dd>
                <pre>{typeof v === "string"
                    ? v
                    : JSON.stringify(v, null, 2)}</pre>
              </dd>
            </div>{/each}
        </dl>
      </section>{/if}
    <section class="settings-section">
      <h2>Workspace controls</h2>
      <div class="row">
        <button disabled={busy} on:click={() => mutate("/rebuild", "POST")}
          >Rebuild evidence index</button
        ><button on:click={logout}>End session</button>
      </div>
    </section>
    <section class="settings-section">
      <h2>Graph library</h2>
      <p>
        Cosmograph by cosmograph-org · Non-commercial use under CC BY-NC 4.0.
      </p>
      <a href="/cosmograph-license.txt" target="_blank" rel="noreferrer"
        >Bundled license and attribution ↗</a
      >
    </section>
  {/if}
</section>

<style>
  .job-row .error {
    overflow-wrap: anywhere;
  }
</style>
