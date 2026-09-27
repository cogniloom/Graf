<script lang="ts">
  import { onMount } from "svelte";
  import type { Cosmograph } from "@cosmograph/cosmograph";
  import type { GraphData } from "./api";
  import wasmUrl from "@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url";
  import workerUrl from "@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url";
  export let data: GraphData;
  export let compact = false;
  export let onSelect: (id: string) => void = () => {};
  export let onSelectEdge: (id: string) => void = () => {};
  let host: HTMLDivElement;
  let graph: Cosmograph | undefined;
  let pendingUpdate = Promise.resolve();
  let error = "";
  let loaded = false;
  let labels = !compact;
  let paused = matchMedia("(prefers-reduced-motion: reduce)").matches;
  onMount(() => {
    let disposed = false;
    let cleanup: (() => Promise<void>) | undefined;
    loaded = false;
    error = "";
    const initialize = (async () => {
      const [{ Cosmograph }, duckdb, arrow] = await Promise.all([
        import("@cosmograph/cosmograph"),
        import("@duckdb/duckdb-wasm"),
        import("apache-arrow"),
      ]);
      if (disposed) return;
      const worker = new Worker(workerUrl);
      const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
      cleanup = async () => {
        await db.terminate();
        worker.terminate();
      };
      // CSP or worker startup failures may leave DuckDB's initialization promise pending.
      // Bound that wait and surface a usable document-list alternative.
      let startupTimer: ReturnType<typeof setTimeout>;
      try {
        await Promise.race([
          db.instantiate(wasmUrl),
          new Promise<never>((_, reject) => {
            startupTimer = setTimeout(
              () =>
                reject(
                  new Error(
                    "Local graph engine did not start within 15 seconds. Check WebAssembly support and the local server policy",
                  ),
                ),
              15000,
            );
          }),
        ]);
      } finally {
        clearTimeout(startupTimer!);
      }
      if (disposed) {
        return;
      }
      const connection = await db.connect();
      const points = data.nodes.map((n, index) => ({
        ...n,
        label:
          n.kind === "document" && n.label.startsWith("/")
            ? n.label.split("/").at(-1) || n.label
            : n.label,
        index,
        color:
          n.highlighted === false
            ? "#c5c6c1"
            : n.kind === "document"
              ? "#a64c2e"
              : n.kind === "passage"
                ? "#cf9378"
                : "#567968",
        size: n.kind === "document" ? 9 : 4,
      }));
      const links = data.edges.map((e) => ({
        ...e,
        sourceIndex: data.nodes.findIndex((n) => n.id === e.source),
        targetIndex: data.nodes.findIndex((n) => n.id === e.target),
      }));
      // Arrow's tableFromJSON uses new Function for null-sentinel checks.
      // These graph columns are explicitly non-null, so typed builders avoid
      // dynamic code generation while preserving the strict page CSP.
      const strings = (values: string[]) => {
        const builder = arrow.makeBuilder({
          type: new arrow.Utf8(),
          nullValues: [],
        });
        for (const value of values) builder.append(value);
        return builder.finish().toVector();
      };
      const numbers = (values: number[]) => {
        const builder = arrow.makeBuilder({
          type: new arrow.Float64(),
          nullValues: [],
        });
        for (const value of values) builder.append(value);
        return builder.finish().toVector();
      };
      await connection.insertArrowTable(
        new arrow.Table({
          id: strings(points.map((n) => n.id)),
          label: strings(points.map((n) => n.label)),
          index: numbers(points.map((n) => n.index)),
          color: strings(points.map((n) => n.color)),
          size: numbers(points.map((n) => n.size)),
        }),
        { name: "graf_points" },
      );
      if (links.length)
        await connection.insertArrowTable(
          new arrow.Table({
            source: strings(links.map((e) => e.source)),
            target: strings(links.map((e) => e.target)),
            sourceIndex: numbers(links.map((e) => e.sourceIndex)),
            targetIndex: numbers(links.map((e) => e.targetIndex)),
            color: strings(
              links.map((e) =>
                e.highlighted === false ? "#d4d4ce" : "#a1aaa1",
              ),
            ),
          }),
          { name: "graf_links" },
        );
      if (disposed) {
        return;
      }
      const instance = new Cosmograph(
        host,
        {
          points: "graf_points",
          links: links.length ? "graf_links" : undefined,
          pointIdBy: "id",
          pointIndexBy: "index",
          pointLabelBy: "label",
          pointColorBy: "color",
          pointColorByFn: (value: unknown) => String(value),
          pointSizeBy: "size",
          linkSourceBy: "source",
          linkTargetBy: "target",
          linkSourceIndexBy: "sourceIndex",
          linkTargetIndexBy: "targetIndex",
          backgroundColor: "#f7f6f2",
          // Avoid degree/count aggregation paths that invoke Arrow's dynamic null checker.
          linkColorStrategy: "direct",
          linkColorBy: "color",
          linkColorByFn: (value: unknown) => String(value),
          linkWidthStrategy: "single",
          linkDefaultColor: "#a1aaa1",
          linkDefaultWidth: 1.3,
          pointColorStrategy: "direct",
          pointSizeStrategy: "direct",
          pointLabelColor: "#353a37",
          showLabels: !compact,
          showDynamicLabels: false,
          showTopLabels: false,
          showLabelsFor: data.nodes
            .filter((n) => n.kind === "document")
            .slice(0, 100)
            .map((n) => n.id),
          showHoveredPointLabel: true,
          pointLabelClassName: "evidence-label",
          pointLabelFontSize: 14,
          pointSizeByFn: (value: unknown) => Number(value) * 2.5,
          linkWidth: 1.3,
          fitViewOnInit: true,
          fitViewDelay: 1500,
          fitViewPadding: 0.2,
          simulationDecay: 2000,
          onSimulationEnd: () => {
            if (!disposed) {
              instance.fitView(0);
              paused = true;
            }
          },
          enableSimulation: !matchMedia("(prefers-reduced-motion: reduce)")
            .matches,
          onClick: (index) => {
            if (index !== undefined) {
              const n = data.nodes[index];
              if (n?.document_id) onSelect(n.document_id);
              else if (n) onSelect(n.id);
            }
          },
          onLabelClick: (index) => {
            const n = data.nodes[index];
            if (n?.document_id) onSelect(n.document_id);
            else if (n) onSelect(n.id);
          },
          onLinkClick: (index) => {
            if (index !== undefined && data.edges[index])
              onSelectEdge?.(data.edges[index].id);
          },
        },
        { duckdb: db, connection },
      );
      cleanup = async () => {
        await instance.destroy();
        await connection.close();
        await db.terminate();
        worker.terminate();
      };
      await instance.dataUploaded();
      if (disposed) {
        return;
      }
      if (instance.stats.pointsCount !== data.nodes.length)
        throw new Error("Graph engine did not load the expected node count");
      graph = instance;
      loaded = true;
      instance.fitView(0);
    })().catch(async (e) => {
      if (!disposed)
        error = `Graph unavailable: ${e.message}. Use the record list to inspect sources.`;
      await cleanup?.().catch(() => {});
      cleanup = undefined;
    });
    return () => {
      disposed = true;
      graph = undefined;
      void initialize
        .then(() => pendingUpdate)
        .then(() => cleanup?.())
        .catch(() => {
          /* Teardown can race browser context loss. */
        });
    };
  });
  function toggleLabels() {
    if (!graph) return;
    labels = !labels;
    pendingUpdate = graph.setConfig({ showLabels: labels }).catch((e) => {
      error = e.message;
    });
  }
</script>

<div class="graph-wrap">
  <div class="graph-toolbar">
    <button disabled={!loaded} on:click={() => graph?.fitView(paused ? 0 : 250)}
      >Fit graph</button
    >
    <button
      disabled={!loaded}
      on:click={() => {
        if (paused) graph?.unpause();
        else graph?.pause();
        paused = !paused;
      }}>{paused ? "Resume motion" : "Pause motion"}</button
    >
    <button disabled={!loaded} aria-pressed={labels} on:click={toggleLabels}
      >Labels</button
    >
  </div>
  <div
    class="graph-canvas"
    bind:this={host}
    aria-label="Evidence graph; use the record lists for keyboard access"
  ></div>
  {#if !loaded || error}<div class="graph-notice" role="status">
      {error || "Loading local graph engine…"}
    </div>{/if}
  <div class="graph-footer">
    <span class="legend"
      >{#each [...new Set(data.nodes.map((n) => n.kind))] as kind}<span
          ><i
            class:document={kind === "document"}
            class:passage={kind === "passage"}
          ></i>{kind.replaceAll("_", " ")}</span
        >{/each}</span
    ><span>Layout proximity is not evidence.</span>
  </div>
  <div class="graph-count">
    {data.nodes.length} of {data.total_nodes} nodes · {data.edges.length} of {data.total_edges}
    connections{data.truncated ? " · Bounded view" : ""}
  </div>
</div>
