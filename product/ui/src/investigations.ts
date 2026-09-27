export type Run = {
  id: string;
  session_id: string;
  parent_id: string | null;
  state: string;
  created_at: string;
  updated_at: string;
  prompt: string;
  model: string;
  effort: string;
  partial: boolean;
};
export type Artifact = {
  id: string;
  kind?: string;
  name?: string;
  media_type?: string;
  sha256: string;
  size: number;
  deleted: boolean;
};
export type Evidence = {
  id: string;
  path: string;
  status: string;
  passages: number;
  supplied: number;
  cited: number;
  artifact_id: string | null;
};
export type Detail = Run & {
  result: {
    answer?: string;
    citations?: {
      segment_id: string;
      quote: string;
      valid: boolean;
      document_id: string;
    }[];
  };
  error: { message: string } | null;
  artifacts: Artifact[];
  evidence: Evidence[];
  activity: {
    id: string;
    kind: string;
    value: unknown;
    display_text?: string;
    observed_at?: string;
    stream?: string;
  }[];
  activity_truncated: boolean;
  events: unknown[];
  limitations: string[];
  snapshot: Record<string, unknown>;
  coverage: {
    supplied_passages: number;
    cited_passages: number;
    total_documents: number;
    omitted_for_budget: string[];
  };
};
export type ErasurePreview = {
  preview_hash: string;
  confirmation: string;
  artifact_ids: string[];
  affected_runs: string[];
  external_obligations: string[];
  active_runs: string[];
};
export const active = (state: string) =>
  ["preparing", "queued", "running", "cancelling"].includes(state);
export const date = (value: string) => new Date(value).toLocaleString();
