export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    signal: init.signal ?? AbortSignal.timeout(15000),
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init.headers },
  }).catch((error: Error) => {
    if (error.name === "TimeoutError")
      throw new Error(
        "Graf took too long to respond. Your request may still be running. Check research history; retrying an unchanged prompt will reuse the same request.",
      );
    throw error;
  });
  const body = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof body?.detail === "string"
        ? body.detail
        : body?.detail?.message ||
          body?.message ||
          `Request failed (${response.status})`,
    );
  return body as T;
}
export async function bootstrap() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const token = fragment.get("token");
  if (token) {
    history.replaceState(null, "", location.pathname + location.search);
    await api("/session", { method: "POST", body: JSON.stringify({ token }) });
  }
}
export type Status = {
  workspace_name: string;
  knowledge_restart_required?: boolean;
  jobs?: (Job & { revision: number })[];
  state: string;
  phase: string;
  revision: number;
  published_revision: number;
  snapshot_id: string | null;
  counts: Record<string, number>;
  message: string;
  models_ready: boolean;
  device: string;
};
export const isReady = (s: Status | null) =>
  !!s &&
  ["ready", "ready_with_gaps"].includes(s.state) &&
  s.revision === s.published_revision;
export type Source = {
  id: string;
  path: string;
  kind: string;
  enabled: boolean;
  status: string;
  file_count: number;
  error: string | null;
};
export type Job = {
  attempts?: number;
  revision?: number;
  id: string;
  state: string;
  phase: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  progress: unknown;
};
export type Doc = {
  id: string;
  path: string;
  status: string;
  warnings: string[];
  passage_count: number;
};
export type Detail = Doc & {
  passages: { id: string; text: string; locators: unknown }[];
  relationships: unknown[];
  relationships_total?: number;
  relationships_truncated?: boolean;
  next_cursor?: string | null;
  next_offset?: number | null;
  truncated?: boolean;
};
export type Node = {
  id: string;
  label: string;
  kind: string;
  document_id: string | null;
  highlighted?: boolean;
  epistemic_status?: string;
  details?: {
    tool: "knowledge_query";
    snapshot_id: string;
    kind: string;
    group_id: string;
  };
};
export type Edge = {
  id: string;
  source: string;
  target: string;
  type: string;
  highlighted?: boolean;
  details?: {
    tool: "knowledge_query";
    snapshot_id: string;
    kind: string;
    group_id: string;
  };
};
export type GraphData = {
  nodes: Node[];
  edges: Edge[];
  total_nodes: number;
  total_edges: number;
  truncated: boolean;
  snapshot_id: string;
};
