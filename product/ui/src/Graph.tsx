import { useEffect, useRef, useState } from "react";
import type { Cosmograph } from "@cosmograph/cosmograph";
import { Maximize, Pause, Play, Tags, Info } from "lucide-react";
import { GraphData } from "./api";
import wasmUrl from "@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url";
import workerUrl from "@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url";
export function Graph({
  data,
  onSelect,
}: {
  data: GraphData;
  onSelect: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const graph = useRef<Cosmograph>();
  const pendingUpdate = useRef(Promise.resolve());
  const select = useRef(onSelect);
  select.current = onSelect;
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [paused, setPaused] = useState(
    () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [labels, setLabels] = useState(true);
  useEffect(() => {
    let disposed = false;
    let cleanup: (() => Promise<void>) | undefined;
    setLoaded(false);
    setError("");
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
          n.kind === "document"
            ? "#9867ff"
            : n.kind === "passage"
              ? "#b18aff"
              : "#46cdd0",
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
          }),
          { name: "graf_links" },
        );
      if (disposed) {
        return;
      }
      const instance = new Cosmograph(
        host.current!,
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
          backgroundColor: "#070f1c",
          // Avoid degree/count aggregation paths that invoke Arrow's dynamic null checker.
          linkColorStrategy: "single",
          linkWidthStrategy: "single",
          linkDefaultColor: "#6b91b3",
          linkDefaultWidth: 1.3,
          pointColorStrategy: "direct",
          pointSizeStrategy: "direct",
          pointLabelColor: "#e4eaff",
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
              setPaused(true);
            }
          },
          enableSimulation: !matchMedia("(prefers-reduced-motion: reduce)")
            .matches,
          onClick: (index) => {
            if (index !== undefined) {
              const n = data.nodes[index];
              if (n?.document_id) select.current(n.document_id);
              else if (n?.kind === "document") select.current(n.id);
            }
          },
          onLabelClick: (index) => {
            const n = data.nodes[index];
            if (n?.document_id) select.current(n.document_id);
            else if (n?.kind === "document") select.current(n.id);
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
      graph.current = instance;
      setLoaded(true);
      instance.fitView(0);
    })().catch(async (e) => {
      if (!disposed)
        setError(
          `Graph unavailable: ${e.message}. Use the document list to inspect sources.`,
        );
      await cleanup?.().catch(() => {});
      cleanup = undefined;
    });
    return () => {
      disposed = true;
      graph.current = undefined;
      void initialize
        .then(() => pendingUpdate.current)
        .then(() => cleanup?.())
        .catch(() => {
          /* Teardown can race browser context loss. */
        });
    };
  }, [data]);
  return (
    <div className="graph-wrap">
      <div className="graph-toolbar">
        <button
          disabled={!loaded}
          onClick={() => graph.current?.fitView(paused ? 0 : 250)}
        >
          <Maximize size={16} />
          Fit graph
        </button>
        <button
          disabled={!loaded}
          onClick={() => {
            if (paused) graph.current?.unpause();
            else graph.current?.pause();
            setPaused(!paused);
          }}
        >
          {paused ? <Play size={16} /> : <Pause size={16} />}{" "}
          {paused ? "Resume motion" : "Pause motion"}
        </button>
        <button
          aria-pressed={labels}
          disabled={!loaded}
          onClick={() => {
            const instance = graph.current;
            if (instance)
              pendingUpdate.current = instance
                .setConfig({ showLabels: !labels })
                .catch((e) => {
                  if (graph.current === instance)
                    setError(`Label update failed: ${e.message}`);
                });
            setLabels(!labels);
          }}
        >
          <Tags size={16} />
          Labels <span className={labels ? "switch on" : "switch"} />
        </button>
      </div>
      <div
        className="graph-canvas"
        ref={host}
        aria-label="Evidence graph; use the Documents view for keyboard access"
      />
      {(!loaded || error) && (
        <div className="graph-notice" role="status">
          {error || "Loading local graph engine…"}
        </div>
      )}
      <div className="graph-footer">
        <span className="legend">
          {Array.from(new Set(data.nodes.map((n) => n.kind))).map((kind) => (
            <span key={kind}>
              <i
                style={{
                  background:
                    kind === "document"
                      ? "#9867ff"
                      : kind === "passage"
                        ? "#b18aff"
                        : "#46cdd0",
                }}
              />
              {kind === "document"
                ? "Documents"
                : kind === "passage"
                  ? "Passages"
                  : kind === "reference"
                    ? "References"
                    : kind.replaceAll("_", " ")}
            </span>
          ))}
        </span>
        <span>
          <Info size={15} />
          Layout proximity is not evidence.
        </span>
      </div>
      <div className="graph-count">
        {data.nodes.length} of {data.total_nodes} nodes · {data.edges.length} of{" "}
        {data.total_edges} connections{data.truncated ? " · Bounded view" : ""}
      </div>
    </div>
  );
}
