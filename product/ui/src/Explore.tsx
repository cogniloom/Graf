import { useEffect, useState, useRef } from "react";
import { FileText, X, Network, List } from "lucide-react";
import { api, Doc, Detail, GraphData, Status, isReady } from "./api";
import { Graph } from "./Graph";
import { Relationship } from "./Relationship";
export function Explore({
  status,
  query,
  onSources,
}: {
  status: Status;
  query: string;
  onSources: () => void;
}) {
  const ready = isReady(status);
  const inspectorTitle = useRef<HTMLHeadingElement>(null);
  const [view, setView] = useState<"graph" | "list">("graph");
  const [graph, setGraph] = useState<GraphData>();
  const [docs, setDocs] = useState<{ items: Doc[]; total: number }>();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string>();
  const [detail, setDetail] = useState<Detail>();
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [passageOffset, setPassageOffset] = useState(0);
  useEffect(() => {
    setOffset(0);
  }, [query]);
  useEffect(() => {
    let alive = true;
    setGraph(undefined);
    setDocs(undefined);
    setDetail(undefined);
    setSelected(undefined);
    setError("");
    if (ready)
      api<GraphData>("/graph?limit=2000")
        .then((x) => {
          if (alive) setGraph(x);
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    return () => {
      alive = false;
    };
  }, [ready, status.revision, status.snapshot_id]);
  useEffect(() => {
    let alive = true;
    setDocs(undefined);
    if (ready)
      api<{ items: Doc[]; total: number }>(
        `/documents?query=${encodeURIComponent(query)}&offset=${offset}&limit=50`,
      )
        .then((x) => {
          if (alive) setDocs(x);
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    return () => {
      alive = false;
    };
  }, [ready, status.revision, query, offset]);
  useEffect(() => {
    let alive = true;
    setDetail(undefined);
    setDetailError("");
    if (selected && ready)
      api<Detail>(
        `/documents/${encodeURIComponent(selected)}?offset=${passageOffset}&limit=25`,
      )
        .then((x) => {
          if (alive) setDetail(x);
        })
        .catch((e) => {
          if (alive) setDetailError(e.message);
        });
    return () => {
      alive = false;
    };
  }, [selected, ready, status.revision, passageOffset]);
  useEffect(() => {
    setPassageOffset(0);
  }, [selected]);
  useEffect(() => {
    if (detail) inspectorTitle.current?.focus();
  }, [detail]);
  if (!ready)
    return (
      <div className="empty">
        <Network size={48} />
        <h2>
          {status.state === "empty"
            ? "Your evidence starts here"
            : status.state === "blocked"
              ? "Workspace needs attention"
              : "Preparing your evidence"}
        </h2>
        <p>
          {status.message ||
            "Add sources to explore documents and their connections."}
        </p>
        <p className="muted">{status.phase}</p>
        <button className="primary" onClick={onSources}>
          {status.state === "empty"
            ? "Add your first source"
            : "Manage sources"}
        </button>
      </div>
    );
  return (
    <>
      <div className="view-tabs" aria-label="Explore views">
        <button
          aria-pressed={view === "graph" && !query}
          onClick={() => setView("graph")}
        >
          <Network size={16} />
          Graph
        </button>
        <button
          aria-pressed={view === "list" || !!query}
          onClick={() => setView("list")}
        >
          <List size={16} />
          Documents{docs ? ` (${docs.total})` : ""}
        </button>
        <span className="muted">
          Snapshot {status.snapshot_id || "unavailable"}
        </span>
      </div>
      <div className={`explore ${selected ? "with-inspector" : ""}`}>
        <section className="explore-main">
          {error && (
            <p className="notice" role="alert">
              {error}
            </p>
          )}
          {view === "graph" && !query ? (
            graph ? (
              graph.nodes.length ? (
                <Graph data={graph} onSelect={setSelected} />
              ) : (
                <div className="empty">
                  <h2>No graph nodes in this snapshot</h2>
                  <p>
                    Inspect the available documents or check source activity.
                  </p>
                </div>
              )
            ) : (
              !error && (
                <div className="empty" role="status">
                  Loading evidence graph…
                </div>
              )
            )
          ) : (
            <div className="documents">
              <p className="muted document-search-help">
                Filter document names and paths here. For questions about the
                evidence, choose Open in Codex.
              </p>
              {!docs && !error && <p role="status">Loading documents…</p>}
              {docs?.items.length === 0 && (
                <div className="empty">No documents match your search.</div>
              )}
              {docs?.items.map((d) => (
                <button
                  className="document-row"
                  key={d.id}
                  onClick={() => setSelected(d.id)}
                >
                  <FileText size={23} />
                  <span>
                    <strong>{d.path.split("/").pop()}</strong>
                    <small>{d.path}</small>
                    <small>
                      {d.status} · {d.passage_count} passages
                      {d.warnings?.length
                        ? ` · ${d.warnings.length} warnings`
                        : ""}
                    </small>
                  </span>
                </button>
              ))}
              {docs && (
                <div className="pagination">
                  <button
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - 50))}
                  >
                    Previous
                  </button>
                  <span>
                    {docs.total
                      ? `${offset + 1}–${Math.min(offset + 50, docs.total)} of ${docs.total}`
                      : "0 documents"}
                  </span>
                  <button
                    disabled={offset + 50 >= docs.total}
                    onClick={() => setOffset(offset + 50)}
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
        {selected && (
          <aside className="inspector" aria-label="Source details">
            <div className="row between">
              <h2 ref={inspectorTitle} tabIndex={-1}>
                Source details
              </h2>
              <button
                aria-label="Close source details"
                onClick={() => setSelected(undefined)}
              >
                <X size={18} />
              </button>
            </div>
            {detailError && <p role="alert">{detailError}</p>}
            {!detail && !detailError && <p role="status">Loading source…</p>}
            {detail && (
              <>
                <div className="detail-title">
                  <span className="file-icon">
                    <FileText />
                  </span>
                  <div>
                    <h3>{detail.path.split("/").pop()}</h3>
                    <small>
                      {detail.status} ·{" "}
                      {detail.passage_count ?? detail.passages.length} passages
                    </small>
                  </div>
                </div>
                <dl>
                  <dt>Source file</dt>
                  <dd>{detail.path}</dd>
                  <dt>Document ID</dt>
                  <dd>{detail.id}</dd>
                </dl>
                {detail.warnings?.map((w, i) => (
                  <p className="warning" key={i}>
                    {w}
                  </p>
                ))}
                <h3>Passages ({detail.passages.length} shown)</h3>
                {detail.passages.map((p) => (
                  <article className="passage" key={p.id}>
                    <blockquote>{p.text}</blockquote>
                    <details>
                      <summary>Source locator</summary>
                      <pre>{JSON.stringify(p.locators, null, 2)}</pre>
                    </details>
                  </article>
                ))}
                <div className="pagination">
                  <button
                    disabled={passageOffset === 0}
                    onClick={() =>
                      setPassageOffset(Math.max(0, passageOffset - 25))
                    }
                  >
                    Previous passages
                  </button>
                  <button
                    disabled={detail.next_offset == null}
                    onClick={() => setPassageOffset(detail.next_offset!)}
                  >
                    Next passages
                  </button>
                </div>
                {(detail.truncated || detail.next_cursor) && (
                  <p className="warning">
                    This preview is bounded. More passages are available through
                    the evidence tools.
                  </p>
                )}
                <h3>
                  Recorded relationships ({detail.relationships.length} shown
                  {detail.relationships_total !== undefined
                    ? ` of ${detail.relationships_total}`
                    : ""}
                  )
                </h3>
                {detail.relationships_truncated && (
                  <p className="warning">
                    Relationship preview is bounded. Use the evidence tools for
                    the remaining records.
                  </p>
                )}
                <p className="muted">
                  These records come from the source index; layout distance does
                  not establish a relationship.
                </p>
                {detail.relationships.length ? (
                  detail.relationships.map((r, i) => (
                    <Relationship
                      key={i}
                      record={r}
                      documentId={detail.id}
                      names={
                        new Map([
                          ...(graph?.nodes ?? []).map(
                            (n) =>
                              [n.document_id || n.id, n.label] as [
                                string,
                                string,
                              ],
                          ),
                          ...(docs?.items ?? []).map(
                            (d) => [d.id, d.path] as [string, string],
                          ),
                        ])
                      }
                      onSelect={setSelected}
                    />
                  ))
                ) : (
                  <p className="muted">
                    No relationships returned for this document.
                  </p>
                )}
              </>
            )}
          </aside>
        )}
      </div>
    </>
  );
}
