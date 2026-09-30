<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { api, type GraphData, type Node, type Edge } from "./api";
  import Graph from "./Graph.svelte";
  let graph: (GraphData & { note: string }) | null = null,
    selected: Node | Edge | null = null,
    knowledge: Record<string, unknown> | null = null,
    detailError = "",
    detailLoading = false,
    selectionVersion = 0,
    error = "",
    filter = "",
    version = 0,
    alive = true;
  let host: HTMLDivElement;
  onMount(() => {
    void load();
  });
  onDestroy(() => {
    alive = false;
    version++;
    selectionVersion++;
  });
  async function load() {
    const v = ++version;
    error = "";
    selected = null;
    knowledge = null;
    detailError = "";
    detailLoading = false;
    selectionVersion++;
    try {
      const result = await api<GraphData & { note: string }>(
        "/investigation-graph",
      );
      if (alive && v === version) graph = result;
    } catch (e) {
      if (alive && v === version) {
        graph = null;
        error = (e as Error).message;
      }
    }
  }
  async function select(record: Node | Edge | undefined) {
    const current = ++selectionVersion;
    selected = record ?? null;
    knowledge = null;
    detailError = "";
    detailLoading = false;
    if (!record || !("details" in record) || !record.details) return;
    const detail = record.details;
    detailLoading = true;
    try {
      const params = new URLSearchParams({
        kind: detail.kind,
        group_id: detail.group_id,
        limit: "1",
      });
      const response = await api<{
        snapshot_id: string;
        items: Record<string, unknown>[];
      }>(`/knowledge?${params}`);
      if (
        response.snapshot_id !== detail.snapshot_id ||
        response.items[0]?.id !== record.id
      )
        throw new Error(
          "The collection changed. Refresh the graph to read the current evidence.",
        );
      if (alive && current === selectionVersion) knowledge = response.items[0];
    } catch (e) {
      if (alive && current === selectionVersion)
        detailError = (e as Error).message;
    } finally {
      if (alive && current === selectionVersion) detailLoading = false;
    }
  }
</script>

<section class="workspace-graph-page">
  <header class="page-heading">
    <p class="breadcrumb">Workspace / Graph</p>
    <div class="heading-row">
      <div>
        <h1>The connected record</h1>
        <p>
          Documents, investigations, and outputs. Explore how the evidence fits
          together. Automatic knowledge is shown as source-bound candidates.
        </p>
      </div>
      <button on:click={load}>Refresh graph</button>
    </div>
  </header>
  {#if error}<p class="notice error" role="alert">{error}</p>{/if}
  {#if graph && graph.nodes.length === 0}<div class="empty">
      <h2>No graph records available yet</h2>
      <p>{graph.note}</p>
      <p>
        You can use Sources, Settings, and Sessions while your collection is
        being prepared.
      </p>
    </div>
  {:else if graph}<div class="graph-workbench">
      <div class="graph-stage" bind:this={host}>
        <div class="graph-stage-heading">
          <span>Workspace graph</span><button
            on:click={() => {
              void (
                document.fullscreenElement
                  ? document.exitFullscreen()
                  : host.requestFullscreen()
              ).catch((e) => (error = e.message));
            }}>Toggle full screen</button
          >
        </div>
        {#key graph}<Graph
            data={graph}
            onSelect={(id) =>
              void select(
                graph?.nodes.find((n) => n.id === id || n.document_id === id),
              )}
            onSelectEdge={(id) =>
              void select(graph?.edges.find((e) => e.id === id))}
          />{/key}
      </div>
      <aside class="graph-records">
        <h2>Record explorer</h2>
        <input
          aria-label="Filter graph records"
          placeholder="Filter nodes…"
          bind:value={filter}
        />
        <div class="graph-node-list">
          {#each graph.nodes.filter((n) => `${n.label} ${n.kind}`
              .toLowerCase()
              .includes(filter.toLowerCase())) as n}<button
              class:chosen={selected === n}
              on:click={() => void select(n)}
              ><small>{n.kind.replaceAll("_", " ")}</small><span
                >{n.label.startsWith("/")
                  ? n.label.split("/").pop()
                  : n.label}</span
              ></button
            >{/each}
        </div>
      </aside>
    </div>
    <p class="muted graph-note">{graph.note}</p>
    {#if graph.truncated}<p class="notice">
        This view is bounded or the current collection is incomplete. Counts
        describe loaded records.
      </p>{/if}{#if selected}<section class="selected-record">
        <h2>Selected record</h2>
        {#if "details" in selected && selected.details}
          <p class="notice">
            Automatically extracted or derived from sources. Identity,
            interpretation, and real-world truth may remain uncertain.
          </p>
          {#if detailLoading}<p role="status">
              Loading source-bound knowledge…
            </p>{/if}
          {#if detailError}<p class="notice error" role="alert">
              {detailError}
            </p>{/if}
          {#if knowledge}
            {#if typeof knowledge.quote === "string"}<blockquote>
                {knowledge.quote}
              </blockquote>{/if}
            {#if typeof knowledge.source_path === "string"}<p>
                Source: {knowledge.source_path}
              </p>{/if}
            <pre>{JSON.stringify(knowledge, null, 2)}</pre>
          {:else}<pre>{JSON.stringify(selected, null, 2)}</pre>{/if}
        {:else}<pre>{JSON.stringify(selected, null, 2)}</pre>{/if}
      </section>{/if}
    <details>
      <summary>Accessible relationship list ({graph.edges.length})</summary>
      <div class="record-list">
        {#each graph.edges as e}<button on:click={() => void select(e)}
            >{e.type} · {e.id}</button
          >{/each}
      </div>
    </details>{:else if !error}<div class="empty" role="status">
      Loading workspace graph…
    </div>{/if}
</section>
