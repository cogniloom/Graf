import { useEffect, useState, useRef } from "react";
import { api, Source, Job } from "./api";
import { Plus, RefreshCw, Trash2, Copy } from "lucide-react";
function Confirm({
  children,
  onCancel,
}: {
  children: React.ReactNode;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="confirm"
      onCancel={onCancel}
      aria-labelledby="remove-title"
    >
      {children}
    </dialog>
  );
}
export function Sources({
  tick,
  mutate,
  busy,
  mutationError,
}: {
  tick: number;
  mutate: (path: string, init: RequestInit) => Promise<boolean>;
  busy: boolean;
  mutationError?: string;
}) {
  const [data, setData] = useState<{
    items: Source[];
    allowed_roots: string[];
  }>();
  const [error, setError] = useState("");
  const [path, setPath] = useState("");
  const [remove, setRemove] = useState<Source | null>(null);
  useEffect(() => {
    let alive = true;
    api<{ items: Source[]; allowed_roots: string[] }>("/sources")
      .then((x) => {
        if (alive) {
          setData(x);
          setError("");
        }
      })
      .catch((e) => {
        if (alive) {
          setData(undefined);
          setError(e.message);
        }
      });
    return () => {
      alive = false;
    };
  }, [tick]);
  return (
    <section className="page">
      <h2>Your sources</h2>
      <p className="muted">
        Add a file or directory from an allowed location. Originals stay
        untouched.
      </p>
      <form
        className="add-source"
        onSubmit={async (e) => {
          e.preventDefault();
          if (
            await mutate("/sources", {
              method: "POST",
              body: JSON.stringify({ path }),
            })
          )
            setPath("");
        }}
      >
        <label htmlFor="source-path">File or directory path</label>
        <div className="row">
          <input
            id="source-path"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/absolute/path/to/source"
            required
          />
          <button className="primary" disabled={busy || !path.trim()}>
            <Plus size={17} />
            Add source
          </button>
        </div>
      </form>
      <details>
        <summary>Allowed locations</summary>
        {data?.allowed_roots.map((x) => (
          <code className="block" key={x}>
            {x}
          </code>
        ))}
        {data?.allowed_roots.length === 0 && (
          <p>
            No locations configured. Update the local workspace configuration.
          </p>
        )}
      </details>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Loading sources…</p>}
      {data?.items.length === 0 && (
        <div className="empty">
          <h3>Start with your first source</h3>
          <p>
            Choose an allowed file or directory above to build your local
            evidence workspace.
          </p>
        </div>
      )}
      <div className="source-table-wrap">
        <table className="source-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Type</th>
              <th>Status</th>
              <th>Files</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((s) => (
              <tr key={s.id}>
                <td>
                  <strong>{s.path}</strong>
                  {s.error && <p className="error">{s.error}</p>}
                </td>
                <td>{s.kind}</td>
                <td>
                  <span className={`source-status ${s.status}`}>
                    {s.status}
                  </span>
                </td>
                <td>{s.file_count}</td>
                <td>
                  <div className="row">
                    <button
                      disabled={busy}
                      onClick={() =>
                        void mutate(
                          `/sources/${encodeURIComponent(s.id)}/rescan`,
                          { method: "POST" },
                        )
                      }
                      aria-label={`Rescan ${s.path}`}
                    >
                      <RefreshCw size={16} />
                      Rescan
                    </button>
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={s.enabled}
                        disabled={busy}
                        onChange={(e) =>
                          void mutate(`/sources/${encodeURIComponent(s.id)}`, {
                            method: "PATCH",
                            body: JSON.stringify({ enabled: e.target.checked }),
                          })
                        }
                      />
                      Enabled
                    </label>
                    <button
                      className="remove-source"
                      disabled={busy}
                      onClick={() => setRemove(s)}
                      aria-label={`Remove ${s.path}`}
                    >
                      <Trash2 size={16} />
                      Remove
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {remove && (
        <Confirm onCancel={() => setRemove(null)}>
          <h3 id="remove-title">Remove this source?</h3>
          {mutationError && (
            <p className="error" role="alert">
              {mutationError}
            </p>
          )}
          <p>{remove.path}</p>
          <p>
            This removes it from current evidence. Original files are preserved;
            historical audit evidence is retained.
          </p>
          <div className="row">
            <button autoFocus onClick={() => setRemove(null)}>
              Cancel
            </button>
            <button
              className="danger"
              disabled={busy}
              onClick={async () => {
                if (
                  await mutate(`/sources/${encodeURIComponent(remove.id)}`, {
                    method: "DELETE",
                  })
                )
                  setRemove(null);
              }}
            >
              Remove source
            </button>
          </div>
        </Confirm>
      )}
    </section>
  );
}
export function Activity({ tick }: { tick: number }) {
  const [jobs, setJobs] = useState<Job[]>();
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    api<{ items: Job[] }>("/jobs")
      .then((x) => {
        if (alive) {
          setJobs(x.items);
          setError("");
        }
      })
      .catch((e) => {
        if (alive) {
          setJobs(undefined);
          setError(e.message);
        }
      });
    return () => {
      alive = false;
    };
  }, [tick]);
  return (
    <section className="page">
      <h2>Workspace activity</h2>
      <p className="muted">
        Actual processing stages and results from your local workspace.
      </p>
      {error && <p role="alert">{error}</p>}
      {!jobs && !error && <p>Loading activity…</p>}
      {jobs?.length === 0 && (
        <div className="empty">No jobs yet. Add a source to get started.</div>
      )}
      {jobs?.map((j) => (
        <article className="source-row" key={j.id}>
          <div>
            <h3>{j.phase || "Queued"}</h3>
            <p>{j.state}</p>
            <small className="muted">
              {j.started_at
                ? new Date(j.started_at).toLocaleString()
                : "Not started"}
              {j.finished_at
                ? ` → ${new Date(j.finished_at).toLocaleString()}`
                : ""}
            </small>
            {j.progress != null && (
              <pre>
                {typeof j.progress === "string"
                  ? j.progress
                  : JSON.stringify(j.progress, null, 2)}
              </pre>
            )}
            {j.error && <p className="error">{j.error}</p>}
          </div>
          <code>{j.id}</code>
        </article>
      ))}
    </section>
  );
}
export function Settings({ codex = false }: { codex?: boolean }) {
  const [settings, setSettings] = useState<Record<string, unknown>>();
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    api<Record<string, unknown>>("/settings")
      .then(setSettings)
      .catch((e) => setError(e.message));
  }, []);
  const instructions =
    typeof settings?.codex_instructions === "string"
      ? settings.codex_instructions
      : "";
  return (
    <section className="page">
      <h2>{codex ? "Use Docworm in Codex" : "Workspace settings"}</h2>
      <p className="muted">Your local workspace, connected on your terms.</p>
      {error && <p role="alert">{error}</p>}
      {!settings && !error && <p>Loading configuration…</p>}
      <h3>Codex connection</h3>
      <p className="muted">
        Codex may send selected evidence to its configured model provider.
        Review its settings before querying private documents.
      </p>
      {instructions ? (
        <>
          <pre className="instructions">{instructions}</pre>
          <button
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(instructions);
                setCopied(true);
              } catch {
                setError(
                  "Clipboard unavailable. Select and copy the instructions above.",
                );
              }
            }}
          >
            <Copy size={16} />
            {copied ? "Copied" : "Copy instructions"}
          </button>
        </>
      ) : (
        settings && (
          <p>
            Connection instructions are not available from this server. Check
            the local installation documentation.
          </p>
        )
      )}
      {!codex && settings && (
        <>
          <h3>Safe configuration</h3>
          <dl className="settings">
            {Object.entries(settings)
              .filter(([k]) => k !== "codex_instructions")
              .map(([k, v]) => (
                <div key={k}>
                  <dt>{k.replaceAll("_", " ")}</dt>
                  <dd>
                    <pre>
                      {typeof v === "string" ? v : JSON.stringify(v, null, 2)}
                    </pre>
                  </dd>
                </div>
              ))}
          </dl>
          <h3>Graph library</h3>
          <p>
            Cosmograph by cosmograph-org · Non-commercial use under CC BY-NC
            4.0.
          </p>
          <a href="/cosmograph-license.txt" target="_blank" rel="noreferrer">
            Read bundled license and attribution
          </a>
        </>
      )}
    </section>
  );
}
