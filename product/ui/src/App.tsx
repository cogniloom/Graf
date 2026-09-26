import { useCallback, useEffect, useState, useRef } from "react";
import {
  Network,
  FileText,
  Clock3,
  Settings as SettingsIcon,
  HardDrive,
  Search,
  Plus,
  Code2,
  RefreshCw,
  LogOut,
} from "lucide-react";
import { api, ApiError, bootstrap, Status } from "./api";
import { Explore } from "./Explore";
import { Sources, Activity, Settings } from "./Management";
let bootstrapPromise: Promise<void> | undefined;
export function App() {
  const [tab, setTab] = useState("Explore");
  const [status, setStatus] = useState<Status | null>(null);
  const [tick, setTick] = useState(0);
  const [error, setError] = useState("");
  const [auth, setAuth] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [query, setQuery] = useState("");
  const refreshVersion = useRef(0);
  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    try {
      const next = await api<Status>("/status");
      if (version !== refreshVersion.current) return;
      setStatus(next);
      setAuth(false);
      setError("");
      setTick((t) => t + 1);
    } catch (e) {
      if (version !== refreshVersion.current) return;
      setStatus(null);
      setError((e as Error).message);
      setAuth(e instanceof ApiError && e.status === 401);
    } finally {
      if (version === refreshVersion.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    let alive = true;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll(current: number) {
      if (!alive || current !== generation) return;
      await refresh();
      if (alive && current === generation)
        timer = setTimeout(() => void poll(current), 3000);
    }
    function connect() {
      const current = ++generation;
      clearTimeout(timer);
      refreshVersion.current++;
      setLoading(true);
      const hasToken = new URLSearchParams(location.hash.slice(1)).has("token");
      const exchange = hasToken
        ? (bootstrapPromise = bootstrap())
        : (bootstrapPromise ??= bootstrap());
      exchange
        .then(() => {
          if (alive && current === generation) void poll(current);
        })
        .catch((e) => {
          if (alive && current === generation) {
            setStatus(null);
            setAuth(true);
            setError(e.message);
            setLoading(false);
          }
        });
    }
    function onHashChange() {
      if (new URLSearchParams(location.hash.slice(1)).has("token")) connect();
    }
    window.addEventListener("hashchange", onHashChange);
    connect();
    return () => {
      alive = false;
      generation++;
      clearTimeout(timer);
      window.removeEventListener("hashchange", onHashChange);
    };
  }, [refresh]);
  async function mutate(path: string, init: RequestInit) {
    refreshVersion.current++;
    setBusy(true);
    setMessage("");
    setMutationError("");
    setStatus((s) => (s ? { ...s, state: "updating" } : null));
    try {
      const result = await api<{ message?: string }>(path, init);
      setMessage(result?.message || "Request accepted by the local server.");
      await refresh();
      return true;
    } catch (e) {
      setMutationError((e as Error).message);
      await refresh();
      setMutationError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  const titles: Record<string, string> = {
    Explore: "Explore your evidence",
    Sources: "Manage your sources",
    Activity: "Follow the progress",
    Settings: "Your workspace settings",
    Codex: "Connect your evidence",
  };
  return (
    <div className="app-shell">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <Network size={33} />
          <span>Graf</span>
        </div>
        <div className="workspace">
          <label>Workspace</label>
          <div>{status?.workspace_name || "Local workspace"}</div>
        </div>
        <nav aria-label="Main navigation">
          {[
            [Network, "Explore"],
            [FileText, "Sources"],
            [Clock3, "Activity"],
            [SettingsIcon, "Settings"],
          ].map(([Icon, name]) => {
            const I = Icon as typeof Network;
            return (
              <button
                key={String(name)}
                aria-current={tab === name ? "page" : undefined}
                className={tab === name ? "active" : ""}
                onClick={() => setTab(String(name))}
              >
                <I size={22} />
                {String(name)}
              </button>
            );
          })}
        </nav>
        <div className="local">
          <HardDrive size={20} />
          <div>
            Local workspace<small>Indexed on your machine.</small>
          </div>
          <i className={status ? "connected" : ""} />
        </div>
      </aside>
      <main id="main">
        <header>
          <div className="header-top">
            <div>
              <h1>{titles[tab]}</h1>
              <p>
                {tab === "Sources"
                  ? "Choose what belongs in this workspace."
                  : "Follow connections back to their sources."}
              </p>
            </div>
            <div className="row">
              <button className="primary" onClick={() => setTab("Sources")}>
                <Plus size={20} />
                Add sources
              </button>
              <button onClick={() => setTab("Codex")}>
                <Code2 size={20} />
                Open in Codex
              </button>
            </div>
          </div>
          <div className="header-bottom">
            <label className="search">
              <Search size={22} />
              <input
                aria-label="Search document names"
                placeholder="Search document names"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setTab("Explore");
                }}
              />
            </label>
            <div className={`readiness ${status?.state || ""}`} role="status">
              <i />
              <div>
                <strong>
                  {loading
                    ? "Connecting…"
                    : auth
                      ? "Authentication required"
                      : status
                        ? status.state.replaceAll("_", " ")
                        : "Offline"}
                </strong>
                <small>
                  {status
                    ? `${status.counts.documents ?? 0} documents · ${status.counts.passages ?? 0} passages · ${status.counts.connections ?? 0} connections`
                    : "Local server connection"}
                </small>
              </div>
            </div>
          </div>
        </header>
        {status && status.state !== "ready" && (
          <div className="phase-strip">
            <span>{status.phase}</span>
            <span>{status.message}</span>
            {status.counts.gaps > 0 && (
              <strong>{status.counts.gaps} gaps</strong>
            )}
          </div>
        )}
        {message && (
          <div className="notice success" role="status">
            {message}
          </div>
        )}
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        {mutationError && (
          <div className="notice error" role="alert">
            {mutationError}
          </div>
        )}
        {loading ? (
          <div className="empty" role="status">
            Connecting to your local workspace…
          </div>
        ) : auth ? (
          <div className="empty">
            <h2>Open your workspace securely</h2>
            <p>
              Run ./graf open to open the dashboard and establish a local
              session.
            </p>
            <button onClick={() => void refresh()}>Check connection</button>
          </div>
        ) : !status ? (
          <div className="empty">
            <h2>Local server unavailable</h2>
            <p>Check that Graf is running, then reconnect.</p>
            <button onClick={() => void refresh()}>
              <RefreshCw size={16} />
              Retry connection
            </button>
          </div>
        ) : tab === "Explore" ? (
          <Explore
            status={busy ? { ...status, state: "updating" } : status}
            query={query}
            onSources={() => setTab("Sources")}
          />
        ) : tab === "Sources" ? (
          <Sources
            tick={tick}
            mutate={mutate}
            busy={busy}
            mutationError={mutationError}
          />
        ) : tab === "Activity" ? (
          <Activity tick={tick} />
        ) : (
          <>
            <Settings codex={tab === "Codex"} />
            {tab === "Settings" && (
              <div className="settings-actions">
                <button
                  disabled={busy}
                  onClick={() => void mutate("/rebuild", { method: "POST" })}
                >
                  <RefreshCw size={16} />
                  Rebuild evidence index
                </button>
                <button
                  onClick={async () => {
                    try {
                      await api("/logout", { method: "POST" });
                      setStatus(null);
                      setAuth(true);
                    } catch (e) {
                      setError((e as Error).message);
                    }
                  }}
                >
                  <LogOut size={16} />
                  End session
                </button>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
