<script lang="ts">
  import { api, isReady, type Status, type GraphData } from "./api";
  import {
    active,
    date,
    type Run,
    type Detail,
    type Artifact,
    type ErasurePreview,
  } from "./investigations";
  import Graph from "./Graph.svelte";
  export let status: Status;
  export let tick: number;
  let runs: Run[] = [];
  let selected: string | null = new URLSearchParams(location.search).get("run");
  let detail: Detail | null = null;
  let tab = "Results",
    filter = "",
    prompt = "",
    model = "gpt-6-astra",
    effort = "medium";
  let partial = false,
    parent: string | null = null,
    busy = false,
    error = "",
    refresh = 0;
  let requestId = crypto.randomUUID();
  let preview: { text: string; truncated: boolean; artifact: Artifact } | null =
    null;
  let annotation = "",
    withdrawalReason = "",
    revisionText = "";
  let graph: GraphData | null = null,
    graphSelection: unknown = null,
    mode = "supplied",
    graphHost: HTMLDivElement;
  let comparison: Detail | null = null,
    comparisonId = "";
  let eraseIds: string[] = [],
    reason = "",
    erasePreview: ErasurePreview | null = null,
    confirmation = "";
  let generation = 0,
    graphGeneration = 0,
    compareGeneration = 0,
    mounted = true;
  let previewVersion = 0;
  import { onDestroy } from "svelte";
  onDestroy(() => {
    mounted = false;
    generation++;
    graphGeneration++;
    compareGeneration++;
  });
  $: restricted = !!detail && ["deleting", "erased"].includes(detail.state);
  $: load(tick, refresh, selected);
  $: graphState = detail?.state;
  $: loadGraph(selected, tab, mode, refresh, graphState);
  $: heading = detail?.prompt.includes("?")
    ? detail.prompt.slice(0, detail.prompt.indexOf("?") + 1)
    : (detail?.prompt || "").slice(0, 120);
  $: loadComparison(comparisonId, selected, tick);
  function clearSensitive() {
    previewVersion++;
    preview = null;
    graph = null;
    graphSelection = null;
    comparison = null;
    comparisonId = "";
    revisionText = "";
    annotation = "";
    withdrawalReason = "";
    eraseIds = [];
    erasePreview = null;
    reason = "";
    confirmation = "";
  }
  function select(id: string | null) {
    generation++;
    graphGeneration++;
    compareGeneration++;
    selected = id;
    detail = null;
    clearSensitive();
    error = "";
    tab = "Results";
    const url = new URL(location.href);
    if (id) url.searchParams.set("run", id);
    else url.searchParams.delete("run");
    history.replaceState(null, "", url);
  }
  async function load(_tick: number, _refresh: number, id: string | null) {
    const version = ++generation;
    try {
      const [list, value] = await Promise.all([
        api<{ items: Run[] }>("/investigations"),
        id ? api<Detail>(`/investigations/${id}`) : Promise.resolve(null),
      ]);
      if (!mounted || version !== generation) return;
      runs = list.items;
      detail = value;
      if (value && ["deleting", "erased"].includes(value.state))
        clearSensitive();
      else if (
        preview &&
        !value?.artifacts.some(
          (a) => a.id === preview?.artifact.id && !a.deleted,
        )
      )
        preview = null;
    } catch (e) {
      if (mounted && version === generation) {
        detail = null;
        clearSensitive();
        error = (e as Error).message;
      }
    }
  }
  async function loadGraph(
    id: string | null,
    view: string,
    highlight: string,
    _refresh: number,
    state: string | undefined,
  ) {
    const version = ++graphGeneration;
    graph = null;
    if (
      !id ||
      !["Graph", "Results"].includes(view) ||
      !state ||
      ["deleting", "erased"].includes(state)
    )
      return;
    try {
      const v = await api<GraphData>(
        `/investigations/${id}/graph?mode=${highlight}`,
      );
      if (mounted && version === graphGeneration) graph = v;
    } catch (e) {
      if (mounted && version === graphGeneration) error = (e as Error).message;
    }
  }
  async function loadComparison(
    id: string,
    _selected: string | null,
    _tick: number,
  ) {
    const v = ++compareGeneration;
    if (!id) {
      comparison = null;
      return;
    }
    try {
      const d = await api<Detail>(`/investigations/${id}`);
      if (mounted && v === compareGeneration)
        comparison = ["erased", "deleting"].includes(d.state) ? null : d;
    } catch {
      if (mounted && v === compareGeneration) comparison = null;
    }
  }
  async function action(fn: () => Promise<void>) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      await fn();
      refresh++;
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }
  const post = (path: string, body?: unknown) =>
    api(path, {
      method: "POST",
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  function submit() {
    void action(async () => {
      const run = await api<Run>("/investigations", {
        method: "POST",
        body: JSON.stringify({
          prompt,
          allow_partial: partial,
          parent_id: parent,
          request_id: requestId,
          model,
          effort,
        }),
      });
      requestId = crypto.randomUUID();
      prompt = "";
      parent = null;
      select(run.id);
    });
  }
  async function showArtifact(id: string) {
    const owner = selected;
    const version = ++previewVersion;
    await action(async () => {
      const v = await api<NonNullable<typeof preview>>(
        `/investigations/${owner}/artifacts/${id}/preview`,
      );
      if (
        mounted &&
        selected === owner &&
        version === previewVersion &&
        detail?.artifacts.some((a) => a.id === id && !a.deleted) &&
        !restricted
      ) {
        preview = v;
        revisionText = v.text;
      }
    });
  }
  function download(blob: Blob, name: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function downloadPackage() {
    void action(async () => {
      const r = await fetch(`/api/investigations/${selected}/package`, {
        method: "POST",
        credentials: "include",
      });
      if (!r.ok)
        throw new Error((await r.json()).detail || "Package export failed");
      download(await r.blob(), `graf-${selected}.zip`);
    });
  }
  function followup() {
    parent = selected;
    select(null);
  }
  function inspectNode(id: string) {
    const doc = detail?.evidence.find((d) => d.id === id);
    if (doc?.artifact_id) void showArtifact(doc.artifact_id);
    else if (detail?.artifacts.some((a) => a.id === id && !a.deleted))
      void showArtifact(id);
    else graphSelection = graph?.nodes.find((n) => n.id === id);
  }
</script>

<div class="research-workbench">
  <aside class="session-list" aria-label="Sessions">
    <div class="pane-heading">
      <h2>Sessions</h2>
      <button
        class="square-button"
        aria-label="New session"
        on:click={() => {
          parent = null;
          select(null);
        }}>＋</button
      >
    </div>
    <input
      class="session-search"
      aria-label="Find a session"
      placeholder="Find a session…"
      bind:value={filter}
    />
    <p class="list-label">RESEARCH HISTORY <span>{runs.length}</span></p>
    <div class="session-items">
      {#each runs.filter((r) => `${r.prompt} ${r.id} ${r.state}`
          .toLowerCase()
          .includes(filter.toLowerCase())) as run}
        <button
          class="session-item"
          class:selected={selected === run.id}
          on:click={() => select(run.id)}
          aria-current={selected === run.id ? "page" : undefined}
        >
          <strong>{run.prompt}</strong><span
            ><i class="state-dot" class:running={active(run.state)}
            ></i>{run.state} · {date(run.created_at)}</span
          >
        </button>
      {:else}<p class="muted empty-small">
          {runs.length
            ? "No matching sessions."
            : "Your research history will appear here."}
        </p>{/each}
    </div>
    <div class="pane-foot">
      A record of every question.<br />Evidence connected across sessions.
    </div>
  </aside>
  <section class="research-main" aria-label="Investigation">
    {#if error}<div role="alert" class="notice error">{error}</div>{/if}
    {#if !selected}
      <div class="compose-page">
        <p class="breadcrumb">
          Research / {parent ? "Follow-up" : "New session"}
        </p>
        <h1>Start with a question.<br /><em>Follow the evidence.</em></h1>
        <p class="intro">
          Bring your documents into focus. Ask Graf to investigate a question,
          trace a connection, or prepare a report with sources.
        </p>
        <form on:submit|preventDefault={submit} class="composer">
          <label for="research-prompt"
            >What would you like to investigate?</label
          >
          <textarea
            id="research-prompt"
            required
            maxlength="16000"
            rows="6"
            bind:value={prompt}
            on:input={() => (requestId = crypto.randomUUID())}
            placeholder="What does the record tell us about…"
          ></textarea>
          <div class="composer-options">
            <label
              >Model<select
                bind:value={model}
                on:change={() => (requestId = crypto.randomUUID())}
                ><option>gpt-6-astra</option><option>gpt-6-sol</option><option
                  >gpt-6-luna</option
                ></select
              ></label
            ><label
              >Reasoning effort<select
                bind:value={effort}
                on:change={() => (requestId = crypto.randomUUID())}
                ><option value="low">Low</option><option value="medium"
                  >Medium</option
                ><option value="high">High</option></select
              ></label
            ><button
              class="primary"
              disabled={busy ||
                !prompt.trim() ||
                (!isReady(status) && !partial)}
              >Start investigation <span aria-hidden="true">↗</span></button
            >
          </div>
        </form>
        {#if parent}<p class="muted">
            Follow-up to run <code>{parent}</code>. This run records its own
            evidence snapshot.
          </p>{/if}
        {#if !isReady(status)}<div class="notice">
            <strong>Indexing is incomplete</strong>
            <p>
              Starting now uses an available, consistent snapshot. Evidence may
              be missing or outdated. If no snapshot exists, Graf will ask you
              to wait.
            </p>
            <label class="check"
              ><input
                type="checkbox"
                bind:checked={partial}
                on:change={() => (requestId = crypto.randomUUID())}
              /> I accept the incomplete collection for this run</label
            >
          </div>{/if}
        <div class="compose-bottom">
          <span class="rule-number">01</span>
          <div>
            <h3>Your sources. A traceable answer.</h3>
            <p>
              Graf retains the supplied evidence, observable activity,
              citations, and generated documents together.
            </p>
          </div>
        </div>
        <p class="privacy-note">
          Codex uses your configured subscription login and may send supplied
          evidence to its provider. The workspace and retained history live
          locally.
        </p>
      </div>
    {:else if !detail}<div class="empty" role="status">
        Loading investigation…
      </div>
    {:else}
      <header class="research-heading">
        <p class="breadcrumb">Research / Session {detail.id.slice(0, 8)}</p>
        <div class="heading-row">
          <h1>
            {heading}{detail.prompt.length > 120 && !detail.prompt.includes("?")
              ? "…"
              : ""}
          </h1>
          <button
            class="export-button"
            disabled={busy || restricted}
            on:click={downloadPackage}
            ><span aria-hidden="true">↓</span> Download evidence package</button
          >
        </div>
        <p class="muted">
          <i class="state-dot" class:running={active(detail.state)}
          ></i>{detail.state} · {date(
            detail.created_at,
          )}{#if active(detail.state)}
            <button
              disabled={busy}
              on:click={() =>
                action(async () => {
                  await post(`/investigations/${selected}/cancel`);
                })}>Cancel run</button
            >{/if}
        </p>
      </header>
      {#if detail.partial}<div class="notice">
          This run used an incomplete collection. Snapshot: {String(
            detail.snapshot.snapshot_id || "unavailable",
          )} · Retrieval: {String(detail.snapshot.retrieval || "unavailable")}
        </div>{/if}
      {#if detail.error}<div class="notice error" role="alert">
          {detail.error.message}
        </div>{/if}
      <nav class="investigation-tabs" aria-label="Investigation views">
        {#each ["Results", "Activity", "Evidence", "Outputs", "Graph", "History"] as t}<button
            aria-current={tab === t ? "page" : undefined}
            on:click={() => {
              tab = t;
              preview = null;
            }}>{t}</button
          >{/each}
      </nav>
      <div class="session-body" class:wide={tab === "Graph"}>
        <div class="session-content">
          {#if restricted}<div class="empty">
              <h2>
                {detail.state === "erased"
                  ? "Payloads erased"
                  : "Erasure in progress"}
              </h2>
              <p>
                The attributed audit trail remains available. Sensitive content
                is unavailable.
              </p>
            </div>{/if}
          {#if tab === "Results"}
            <details class="initial-prompt">
              <summary>Initial prompt</summary>
              <p>{detail.prompt}</p>
            </details>
            <article class="result-document">
              <p class="list-label">RESEARCH RESULT</p>
              <div class="answer-text">
                {detail.result.answer ||
                  (active(detail.state)
                    ? "The investigation is in progress. Open Activity to follow observable output."
                    : "No final answer is available.")}
              </div>
            </article>
            {#if detail.result.citations?.length}<h3 class="section-heading">
                Citations <span>{detail.result.citations.length}</span>
              </h3>{/if}
            {#each detail.result.citations || [] as c, i}<article
                class="citation"
              >
                <span class="citation-number">{i + 1}</span>
                <div>
                  <blockquote>{c.quote}</blockquote>
                  <p class="muted">
                    {c.valid
                      ? "Exact quotation verified"
                      : "Quotation validation failed"} ·
                    <code>{c.segment_id}</code>
                  </p>
                  {#if detail.evidence.find((d) => d.id === c.document_id)?.artifact_id}<button
                      class="text-button"
                      on:click={() =>
                        showArtifact(
                          detail!.evidence.find((d) => d.id === c.document_id)!
                            .artifact_id!,
                        )}>Inspect original ↗</button
                    >{/if}
                </div>
              </article>{/each}
            {#if !restricted}<details class="review-note">
                <summary>Add a human review note</summary>
                <form
                  on:submit|preventDefault={() =>
                    action(async () => {
                      await post(`/investigations/${selected}/annotations`, {
                        text: annotation,
                      });
                      annotation = "";
                    })}
                >
                  <label
                    >Human review note<textarea
                      required
                      maxlength="16000"
                      bind:value={annotation}
                    ></textarea></label
                  ><button disabled={busy || !annotation.trim()}
                    >Save attributed note</button
                  >
                </form>
              </details>{/if}
            <details>
              <summary>Coverage and verification limits</summary>
              <ul>
                {#each detail.limitations as l}<li>{l}</li>{/each}
              </ul>
            </details>
          {:else if tab === "Activity"}
            <h2>Observable activity</h2>
            <p class="muted">
              Captured input and output, with observation timestamps.
            </p>
            {#if detail.activity_truncated}<p class="notice">
                Showing the latest activity window. Download the package for the
                retained record.
              </p>{/if}{#each detail.activity as a}<details class="event-row">
                <summary
                  ><time>{a.observed_at ? date(a.observed_at) : a.kind}</time
                  ><span>{a.stream || a.id}</span></summary
                >
                <pre>{a.display_text ?? JSON.stringify(a.value, null, 2)}</pre>
              </details>{:else}<p class="empty">
                No provider activity captured yet.
              </p>{/each}
          {:else if tab === "Evidence"}
            <h2>The evidence record</h2>
            <p>
              {detail.coverage.total_documents} documents in the snapshot · {detail
                .coverage.supplied_passages} passages supplied · {detail
                .coverage.cited_passages} passages cited
            </p>
            <p class="muted">
              Not supplied does not mean irrelevant. Exact quotation checks do
              not establish semantic support.
            </p>
            {#if detail.coverage.omitted_for_budget.length}<p class="notice">
                {detail.coverage.omitted_for_budget.length} selected passages were
                omitted to fit the input budget.
              </p>{/if}
            <div class="table-scroll">
              <table>
                <thead
                  ><tr
                    ><th>Document</th><th>Passages</th><th>Supplied</th><th
                      >Cited</th
                    ><th>Extraction</th></tr
                  ></thead
                ><tbody
                  >{#each detail.evidence as d}<tr
                      ><td
                        ><button
                          class="text-button"
                          title={d.path}
                          disabled={!d.artifact_id || restricted}
                          on:click={() => showArtifact(d.artifact_id!)}
                          >{d.path.split("/").pop()}</button
                        ><small class="path">{d.path}</small></td
                      ><td>{d.passages}</td><td>{d.supplied}</td><td
                        >{d.cited}</td
                      ><td>{d.status}</td></tr
                    >{/each}</tbody
                >
              </table>
            </div>
          {:else if tab === "Outputs"}
            <h2>Files & retained records</h2>
            <p class="muted">
              Originals, deliverables, revisions, and captured activity remain
              distinguishable.
            </p>
            {#each detail.artifacts as a}<article class="artifact-row">
                <div>
                  <strong>{a.deleted ? "Erased artifact" : a.name}</strong
                  ><small
                    >{a.kind || "Retained deletion record"} · {a.size.toLocaleString()}
                    bytes</small
                  ><code>{a.sha256}</code>
                </div>
                {#if !a.deleted && !restricted}<div class="row">
                    <button on:click={() => showArtifact(a.id)}>Preview</button
                    ><a
                      class="button"
                      href={`/api/investigations/${selected}/artifacts/${a.id}`}
                      download>Download</a
                    ><label class="check"
                      ><input
                        type="checkbox"
                        checked={eraseIds.includes(a.id)}
                        on:change={(e) => {
                          eraseIds = e.currentTarget.checked
                            ? [...eraseIds, a.id]
                            : eraseIds.filter((k) => k !== a.id);
                          erasePreview = null;
                          confirmation = "";
                        }}
                      />Select for erasure</label
                    >
                  </div>{/if}
              </article>{/each}
            {#if eraseIds.length && !restricted}<section class="erasure-panel">
                <h3>Accountable erasure</h3>
                <p>
                  Erasure is irreversible for Graf-managed payloads. Preview the
                  dependent artifacts and affected follow-ups before
                  authorizing.
                </p>
                <label
                  >Authority and reason<textarea
                    bind:value={reason}
                    on:input={() => {
                      erasePreview = null;
                      confirmation = "";
                    }}
                  ></textarea></label
                ><button
                  disabled={busy || !reason.trim()}
                  on:click={() =>
                    action(async () => {
                      const owner = selected;
                      const requestedIds = [...eraseIds];
                      const requestedReason = reason;
                      const version = previewVersion;
                      const value = await api<ErasurePreview>(
                        `/investigations/${owner}/erasure-preview`,
                        {
                          method: "POST",
                          body: JSON.stringify({
                            artifact_ids: eraseIds,
                            reason,
                          }),
                        },
                      );
                      if (
                        mounted &&
                        owner === selected &&
                        version === previewVersion &&
                        !restricted &&
                        requestedReason === reason &&
                        JSON.stringify(requestedIds) ===
                          JSON.stringify(eraseIds)
                      )
                        erasePreview = value;
                    })}>Preview erasure impact</button
                >{#if erasePreview}<p>
                    {erasePreview.artifact_ids.length} artifacts across {erasePreview
                      .affected_runs.length} runs are affected.
                  </p>
                  <ul>
                    {#each erasePreview.external_obligations as o}<li>
                        {o}
                      </li>{/each}
                  </ul>
                  <label
                    >Type {erasePreview.confirmation}<input
                      bind:value={confirmation}
                    /></label
                  ><button
                    class="danger"
                    disabled={busy ||
                      confirmation !== erasePreview.confirmation}
                    on:click={() =>
                      action(async () => {
                        await post(`/investigations/${selected}/erase`, {
                          artifact_ids: eraseIds,
                          reason,
                          preview_hash: erasePreview!.preview_hash,
                          confirmation,
                        });
                        clearSensitive();
                      })}>Authorize erasure</button
                  >{/if}
              </section>{/if}
          {:else if tab === "Graph" && !restricted}
            <div class="session-graph" bind:this={graphHost}>
              <div class="row between">
                <label
                  >Highlight <select bind:value={mode}
                    ><option value="supplied">Supplied evidence</option><option
                      value="cited">Cited evidence</option
                    ></select
                  ></label
                ><button
                  on:click={() => {
                    void (
                      document.fullscreenElement
                        ? document.exitFullscreen()
                        : graphHost.requestFullscreen()
                    ).catch((e) => (error = e.message));
                  }}>Toggle full screen</button
                >
              </div>
              <p class="muted">
                Colored documents were {mode}; gray nodes provide context. Edges
                record relationships, not inferred agent traversal.
              </p>
              {#if graph}{#key graph}<Graph
                    data={graph}
                    onSelect={inspectNode}
                    onSelectEdge={(id) =>
                      (graphSelection = graph?.edges.find((e) => e.id === id))}
                  />{/key}
                <details>
                  <summary>Inspect nodes ({graph.nodes.length})</summary>
                  <div class="record-list">
                    {#each graph.nodes as n}<button
                        on:click={() => inspectNode(n.document_id || n.id)}
                        >{n.label} · {n.kind}</button
                      >{/each}
                  </div>
                </details>
                <details>
                  <summary>Inspect relationships ({graph.edges.length})</summary
                  >
                  <div class="record-list">
                    {#each graph.edges as e}<button
                        on:click={() => (graphSelection = e)}
                        >{e.type} · {e.id}</button
                      >{/each}
                  </div>
                </details>
                {#if graphSelection}<pre>{JSON.stringify(
                      graphSelection,
                      null,
                      2,
                    )}</pre>{/if}{:else}<p role="status">
                  Loading session graph…
                </p>{/if}
            </div>
          {:else if tab === "History"}
            <h2>History & integrity</h2>
            <details>
              <summary>Recorded ledger events</summary>
              <pre>{JSON.stringify(detail.events, null, 2)}</pre>
            </details>
            <button
              disabled={busy}
              on:click={() =>
                action(async () => {
                  const checkpoint = await api("/integrity/checkpoint");
                  download(
                    new Blob([JSON.stringify(checkpoint, null, 2)], {
                      type: "application/json",
                    }),
                    "graf-checkpoint.json",
                  );
                })}>Download signed workspace checkpoint</button
            >
            <p class="muted">
              Preserve the checkpoint and signing-key fingerprint independently.
              Local signatures do not establish external identity or trusted
              time.
            </p>
            {#if !restricted}<h3>Compare with another run</h3>
              <select
                aria-label="Compare run"
                bind:value={comparisonId}
                on:change={() => (comparison = null)}
                ><option value="">Choose a run</option
                >{#each runs.filter((r) => r.id !== selected) as r}<option
                    value={r.id}>{r.prompt.slice(0, 80)} · {r.id}</option
                  >{/each}</select
              >{#if comparison}<div class="comparison">
                  <div>
                    <h4>This run</h4>
                    <p>
                      {String(detail.snapshot.snapshot_id)} · {detail.coverage
                        .supplied_passages} supplied passages
                    </p>
                    <pre>{detail.result.answer}</pre>
                  </div>
                  <div>
                    <h4>Compared run</h4>
                    <p>
                      {String(comparison.snapshot.snapshot_id)} · {comparison
                        .coverage.supplied_passages} supplied passages
                    </p>
                    <pre>{comparison.result.answer}</pre>
                  </div>
                </div>{/if}
              <form
                class="withdrawal"
                on:submit|preventDefault={() =>
                  action(async () => {
                    await post(`/investigations/${selected}/withdraw`, {
                      text: withdrawalReason,
                    });
                    withdrawalReason = "";
                  })}
              >
                <h3>Withdraw this run</h3>
                <p class="muted">
                  Keep the record and attribute the reason. Withdrawn runs
                  cannot be used for new follow-ups.
                </p>
                <label
                  >Withdrawal reason<textarea
                    bind:value={withdrawalReason}
                    required
                  ></textarea></label
                ><button
                  disabled={busy ||
                    !withdrawalReason.trim() ||
                    active(detail.state) ||
                    detail.state === "withdrawn"}>Record withdrawal</button
                >
              </form>{/if}
          {/if}
          {#if preview && !restricted}<aside class="artifact-preview">
              <div class="row between">
                <h3>{preview.artifact.name}</h3>
                <button on:click={() => (preview = null)}>Close preview</button>
              </div>
              {#if preview.truncated}<p class="notice">
                  Preview truncated; download the complete artifact.
                </p>{/if}
              <pre>{preview.text}</pre>
              {#if ["generated_document", "human_revision"].includes(preview.artifact.kind || "") && ["text/plain", "text/markdown", "text/csv", "text/html", "application/json"].includes(preview.artifact.media_type || "") && !preview.truncated}<form
                  on:submit|preventDefault={() =>
                    action(async () => {
                      await post(
                        `/investigations/${selected}/artifacts/${preview!.artifact.id}/revise`,
                        { text: revisionText },
                      );
                      preview = null;
                    })}
                >
                  <label
                    >Save a human revision<textarea
                      required
                      maxlength="16000"
                      bind:value={revisionText}
                    ></textarea></label
                  >
                  <p class="muted">
                    The original remains retained. Your revision is attributed
                    to the authenticated local owner.
                  </p>
                  <button
                    disabled={busy ||
                      !revisionText.trim() ||
                      revisionText.length > 16000}>Save new version</button
                  >
                </form>{/if}
            </aside>{/if}
        </div>
        {#if tab !== "Graph"}<aside class="session-record">
            <h2>Session record</h2>
            <dl>
              <dt>Session ID</dt>
              <dd><code>{detail.session_id}</code></dd>
              <dt>Run ID</dt>
              <dd><code>{detail.id}</code></dd>
              <dt>Started</dt>
              <dd>{date(detail.created_at)}</dd>
              <dt>Last action</dt>
              <dd>{date(detail.updated_at)}</dd>
              <dt>Requested model</dt>
              <dd>{detail.model} / {detail.effort}</dd>
              <dt>Status</dt>
              <dd>
                <i class="state-dot" class:running={active(detail.state)}
                ></i>{detail.state}
              </dd>
            </dl>
            <div class="record-stats">
              <button on:click={() => (tab = "Results")}
                ><strong>{detail.result.citations?.length || 0}</strong>
                citations <span aria-hidden="true">↗</span></button
              ><button on:click={() => (tab = "Evidence")}
                ><strong>{detail.coverage.total_documents}</strong> source files
                <span aria-hidden="true">↗</span></button
              ><button on:click={() => (tab = "Outputs")}
                ><strong
                  >{detail.artifacts.filter((a) => !a.deleted).length}</strong
                >
                retained artifacts <span aria-hidden="true">↗</span></button
              >
            </div>
            <h3>Connections</h3>
            {#if graph?.nodes.length && tab === "Results" && !restricted}
              <div class="mini-graph">
                {#key graph}<Graph
                    data={graph}
                    compact
                    onSelect={inspectNode}
                    onSelectEdge={(id) => {
                      graphSelection = graph?.edges.find((e) => e.id === id);
                      tab = "Graph";
                    }}
                  />{/key}
              </div>
            {/if}
            <p class="muted">
              Inspect the evidence supplied to this run and the relationships
              recorded in Graf.
            </p>
            <button
              class="text-button"
              disabled={restricted}
              on:click={() => (tab = "Graph")}>Explore session graph →</button
            >
            <h3>Source index</h3>
            <ol class="source-index">
              {#each detail.evidence
                .filter((d) => d.cited > 0)
                .slice(0, 8) as d}<li>
                  <button
                    disabled={!d.artifact_id || restricted}
                    on:click={() => showArtifact(d.artifact_id!)}
                    >{d.path.split("/").pop()}<small
                      >{d.cited} cited passages</small
                    ></button
                  >
                </li>{/each}
            </ol>
          </aside>{/if}
      </div>
      {#if !active(detail.state) && !restricted}<footer class="followup-bar">
          <span>Continue the investigation</span><button
            disabled={detail.state === "withdrawn"}
            on:click={followup}
            >Follow up <span aria-hidden="true">↗</span></button
          >
        </footer>{/if}
    {/if}
  </section>
</div>
