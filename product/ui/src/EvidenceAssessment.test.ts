import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import EvidenceAssessment from "./EvidenceAssessment.svelte";
import type { Detail } from "./investigations";
const detail = {
  id: "run-one",
  state: "completed",
  result: {
    conclusions: [
      {
        id: "c1",
        text: "Accounts conflict.",
        status: "conflicting",
        supporting: [{ segment_id: "s1", quote: "approved" }],
        contrary: [{ segment_id: "s2", quote: "not approved" }],
        assumptions: [],
        gaps: ["Identity is unresolved"],
      },
    ],
  },
  reviews: [],
  guidance: {
    messages: [
      {
        kind: "scope",
        message: "Selected evidence only",
        action: "Inspect missing records",
      },
    ],
  },
} as unknown as Detail;
const item = {
  target_kind: "knowledge",
  target_id: "claim1",
  record: { condition: "only after inspection" },
  sources: [
    {
      id: "s1",
      text: "Approved only after inspection.",
      source_path: "approval.txt",
    },
  ],
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows both accounts, gaps and actionable coverage", () => {
  render(EvidenceAssessment, { detail });
  expect(screen.getByText("Supporting passages")).toBeTruthy();
  expect(screen.getByText("Contrary passages")).toBeTruthy();
  expect(screen.getByText("Identity is unresolved")).toBeTruthy();
  expect(screen.getByText("Inspect missing records")).toBeTruthy();
});
it("requires reason and correction and records a ledger review", async () => {
  const fetcher = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(
        JSON.stringify(
          init?.method === "POST"
            ? { ledger_seq: 42 }
            : { items: [item], total: 1, next_offset: null },
        ),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  const onSaved = vi.fn();
  render(EvidenceAssessment, { detail, onSaved });
  await fireEvent.click(screen.getByText("Load interpretations"));
  await fireEvent.click(
    await screen.findByText(
      "Extracted interpretation: Approved only after inspection.",
    ),
  );
  const save = screen.getByRole("button", {
    name: "Record review in ledger",
  }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  await fireEvent.input(screen.getByLabelText("Reason for this decision"), {
    target: { value: "Condition matters" },
  });
  expect(save.disabled).toBe(true);
  await fireEvent.input(screen.getByLabelText("Correction or clarification"), {
    target: { value: "Inspection has not occurred" },
  });
  await fireEvent.click(save);
  await screen.findByText("Review recorded in ledger entry 42.");
  const call = fetcher.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string)).toEqual({
    target_kind: "knowledge",
    target_id: "claim1",
    decision: "clarify",
    reason: "Condition matters",
    correction: "Inspection has not occurred",
    supersedes: null,
  });
  expect(onSaved).toHaveBeenCalledOnce();
});
it("discards delayed evidence after erasure", async () => {
  let finish!: (r: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const view = render(EvidenceAssessment, { detail });
  await fireEvent.click(screen.getByText("Load interpretations"));
  await view.rerender({ detail: { ...detail, state: "deleting" } });
  finish(
    new Response(
      JSON.stringify({ items: [item], total: 1, next_offset: null }),
    ),
  );
  await Promise.resolve();
  expect(
    screen.queryByText(
      "Extracted interpretation: Approved only after inspection.",
    ),
  ).toBeNull();
  expect(
    screen.queryByRole("region", { name: "Evidence assessment" }),
  ).toBeNull();
});
it("shows failed ledger writes without claiming success", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? new Response(
            JSON.stringify({
              detail: "Review changed; reload before superseding it",
            }),
            { status: 400 },
          )
        : new Response(
            JSON.stringify({ items: [item], total: 1, next_offset: null }),
          ),
    ),
  );
  render(EvidenceAssessment, { detail });
  await fireEvent.click(screen.getByText("Load interpretations"));
  await fireEvent.click(
    await screen.findByText(
      "Extracted interpretation: Approved only after inspection.",
    ),
  );
  await fireEvent.change(screen.getByLabelText("Review decision"), {
    target: { value: "reject" },
  });
  await fireEvent.input(screen.getByLabelText("Reason for this decision"), {
    target: { value: "Wrong attribution" },
  });
  await fireEvent.click(
    screen.getByRole("button", { name: "Record review in ledger" }),
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Review changed",
  );
  expect(screen.queryByRole("status")).toBeNull();
});

it("does not silently supersede a review that changes while editing", async () => {
  const fetcher = vi.fn(
    async () =>
      new Response(
        JSON.stringify({ items: [item], total: 1, next_offset: null }),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = render(EvidenceAssessment, { detail });
  await fireEvent.click(screen.getByText("Load interpretations"));
  await fireEvent.click(
    await screen.findByText(
      "Extracted interpretation: Approved only after inspection.",
    ),
  );
  await fireEvent.change(screen.getByLabelText("Review decision"), {
    target: { value: "reject" },
  });
  await fireEvent.input(screen.getByLabelText("Reason for this decision"), {
    target: { value: "My assessment" },
  });
  await view.rerender({
    detail: {
      ...detail,
      reviews: [
        {
          id: "newer",
          current: true,
          effective: true,
          target_kind: "knowledge",
          target_id: "claim1",
          decision: "confirm",
          actor: "another-local-review",
          time: "2026-09-30",
          reason: "Inspected",
          correction: "",
          ledger_seq: 20,
          ledger_hash: "hash",
          supersedes: null,
        },
      ],
    },
  });
  expect((await screen.findByRole("alert")).textContent).toContain(
    "changed while you were editing",
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Record review in ledger",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});

it("labels uncertain gap totals and retains attachment warnings", () => {
  render(EvidenceAssessment, {
    detail: {
      ...detail,
      guidance: {
        document_gaps: [{ path: "uploaded.pdf", status: "partial", warnings: ["Visual content needs review"] }],
        document_gaps_total: 101,
        document_gaps_total_is_lower_bound: true,
      },
    },
  });
  expect(screen.getByText("Documents needing attention (at least 101)")).toBeTruthy();
  expect(screen.getByText("uploaded.pdf")).toBeTruthy();
  expect(screen.getByText("Visual content needs review")).toBeTruthy();
});
