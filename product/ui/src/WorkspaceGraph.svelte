<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { api, type GraphData } from "./api";
  import Graph from "./Graph.svelte";
  let graph: (GraphData & { note: string }) | null = null,
    selected: unknown = null,
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
  });
  async function load() {
    const v = ++version;
    error = "";
    selected = null;
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
</script>

<section class="workspace-graph-page">
  <header class="page-heading">
    <p class="breadcrumb">Workspace / Graph</p>
    <div class="heading-row">
      <div>
        <h1>The connected record</h1>
        <p>
          Documents, investigations, and outputs. Explore how the evidence fits
          together.
        </p>
      </div>
      <button on:click={load}>Refresh graph</button>
    </div>
  </header>
  {#if error}<p class="notice error" role="alert">{error}</p>{/if}
  {#if graph}<div class="graph-workbench">
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
              (selected = graph?.nodes.find(
                (n) => n.id === id || n.document_id === id,
              ))}
            onSelectEdge={(id) =>
              (selected = graph?.edges.find((e) => e.id === id))}
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
              on:click={() => (selected = n)}
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
        <pre>{JSON.stringify(selected, null, 2)}</pre>
      </section>{/if}
    <details>
      <summary>Accessible relationship list ({graph.edges.length})</summary>
      <div class="record-list">
        {#each graph.edges as e}<button on:click={() => (selected = e)}
            >{e.type} · {e.id}</button
          >{/each}
      </div>
    </details>{:else if !error}<div class="empty" role="status">
      Loading workspace graph…
    </div>{/if}
</section>
