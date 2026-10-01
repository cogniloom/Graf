export type EvidenceCitation = {
  segment_id: string;
  quote: string;
  valid?: boolean;
  document_id?: string;
};
export type Conclusion = {
  id: string;
  text: string;
  status: "supported" | "inference" | "conflicting" | "unresolved";
  supporting: EvidenceCitation[];
  contrary: EvidenceCitation[];
  assumptions: string[];
  gaps: string[];
};
export type Review = {
  id: string;
  target_kind: string;
  target_id: string;
  decision: string;
  reason: string;
  correction: string;
  actor: string;
  time: string;
  ledger_seq: number;
  ledger_hash: string;
  supersedes: string | null;
  current: boolean;
  effective: boolean;
};
export type ReviewTarget = {
  target_kind: string;
  target_id: string;
  record: Record<string, unknown>;
  sources: {
    id?: string;
    segment_id?: string;
    text?: string;
    quote?: string;
    source_path?: string;
  }[];
};
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
    conclusions?: Conclusion[];
    evidence_contract?: {
      version: string;
      validation: string;
      semantic_support?: string;
    };
    questions?: {
      id: string;
      question: string;
      options: string[];
      kind?: string;
      reason?: string;
    }[];
    citations?: {
      segment_id: string;
      quote: string;
      valid: boolean;
      document_id: string;
    }[];
  };
  reviews?: Review[];
  supplied_reviews?: Review[];
  supplied_reviews_omitted?: number;
  guidance?: {
    messages?: { kind: string; message: string; action: string }[];
    document_gaps?: { path: string; status: string; warnings?: string[] }[];
    document_gaps_total?: number;
    document_gaps_total_is_lower_bound?: boolean;
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
