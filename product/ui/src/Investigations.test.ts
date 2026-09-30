import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/svelte";
import Investigations from "./Investigations.svelte";
import { Status } from "./api";

const status = {
  state: "ready",
  revision: 1,
  published_revision: 1,
  counts: {},
  phase: "idle",
} as Status;
const run = {
  id: "run-one",
  session_id: "session-one",
  parent_id: null,
  state: "completed",
  created_at: "2026-09-27T10:00:00Z",
  updated_at: "2026-09-27T10:01:00Z",
  prompt: "Who approved the invoice?",
  model: "gpt-6-astra",
  effort: "medium",
  partial: false,
};
const detail = {
  ...run,
  result: {
    answer: "No approval was recorded.",
    citations: [
      {
        segment_id: "seg-1",
        quote: "not approved",
        valid: true,
        document_id: "doc-1",
      },
    ],
  },
  error: null,
  artifacts: [
    {
      id: "art-1",
      kind: "generated_document",
      name: "report.md",
      sha256: "a".repeat(64),
      size: 20,
      deleted: false,
    },
  ],
  evidence: [
    {
      id: "doc-1",
      path: "invoice.txt",
      status: "ready",
      passages: 2,
      supplied: 1,
      cited: 1,
      artifact_id: "art-1",
    },
  ],
  activity: [],
  events: [],
  limitations: ["No private internal reasoning"],
  snapshot: { snapshot_id: "snap-1" },
  coverage: {
    supplied_passages: 1,
    cited_passages: 1,
    total_documents: 1,
    omitted_for_budget: [],
  },
};
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  history.replaceState(null, "", "/");
  fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/graph?"))
      return new Response(
        JSON.stringify({
          nodes: [],
          edges: [],
          total_nodes: 0,
          total_edges: 0,
          truncated: false,
          snapshot_id: "test",
        }),
      );
    if (url === "/api/investigations")
      return new Response(
        JSON.stringify(options?.method === "POST" ? run : { items: [run] }),
      );
    if (url.endsWith("/preview"))
      return new Response(
        JSON.stringify({
          text: "<script>alert('untrusted')</script>",
          truncated: false,
          artifact: detail.artifacts[0],
        }),
      );
    if (url.endsWith("/erasure-preview"))
      return new Response(
        JSON.stringify({
          preview_hash: "f".repeat(64),
          confirmation: "ERASE run-one",
          artifact_ids: ["art-1"],
          affected_runs: ["run-one"],
          external_obligations: ["External packages remain"],
          active_runs: [],
        }),
      );
    return new Response(JSON.stringify(detail));
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("requires explicit incomplete-collection consent and records it when creating a run", async () => {
  render(Investigations, { status: { ...status, state: "updating" }, tick: 0 });
  await fireEvent.input(
    screen.getByLabelText("What would you like to investigate?"),
    { target: { value: "Check the invoice" } },
  );
  const start = screen.getByRole("button", { name: "Start investigation" });
  expect((start as HTMLButtonElement).disabled).toBe(true);
  await fireEvent.click(
    screen.getByLabelText("I accept the incomplete collection for this run"),
  );
  expect((start as HTMLButtonElement).disabled).toBe(false);
  await fireEvent.click(start);
  await screen.findByText("No approval was recorded.");
  const body = JSON.parse(
    fetchMock.mock.calls.find(([, o]) => o?.method === "POST")![1].body,
  );
  expect(body.allow_partial).toBe(true);
  expect(body.prompt).toBe("Check the invoice");
  expect(body.request_id).toBeTruthy();
});

it("shows source coverage separately from citations and renders generated HTML as text", async () => {
  history.replaceState(null, "", "/?run=run-one");
  render(Investigations, { status, tick: 0 });
  await screen.findByText("No approval was recorded.");
  await fireEvent.click(screen.getByRole("button", { name: "Evidence" }));
  await screen.findByText(/1 documents in the snapshot/);
  await fireEvent.click(screen.getByRole("button", { name: "invoice.txt" }));
  expect(
    await screen.findByText("<script>alert('untrusted')</script>"),
  ).toBeTruthy();
  expect(document.querySelector(".artifact-preview script")).toBeNull();
});

it("requires an impact preview and exact confirmation before erasure", async () => {
  history.replaceState(null, "", "/?run=run-one");
  render(Investigations, { status, tick: 0 });
  await screen.findByText("No approval was recorded.");
  await fireEvent.click(screen.getByRole("button", { name: "Outputs" }));
  await fireEvent.click(screen.getByLabelText("Select for erasure"));
  await fireEvent.input(screen.getByLabelText("Authority and reason"), {
    target: { value: "Synthetic deletion instruction" },
  });
  await fireEvent.click(
    screen.getByRole("button", { name: "Preview erasure impact" }),
  );
  await screen.findByText("External packages remain");
  const erase = screen.getByRole("button", { name: "Authorize erasure" });
  expect((erase as HTMLButtonElement).disabled).toBe(true);
  await fireEvent.input(screen.getByLabelText("Type ERASE run-one"), {
    target: { value: "ERASE run-one" },
  });
  expect((erase as HTMLButtonElement).disabled).toBe(false);
  await fireEvent.click(erase);
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/erase"))).toBe(
      true,
    ),
  );
});

it("surfaces start failures and preserves the entered question", async () => {
  fetchMock.mockImplementation(
    async (_url: string, options?: RequestInit) =>
      new Response(
        JSON.stringify(
          options?.method === "POST"
            ? { detail: "No snapshot is available" }
            : { items: [] },
        ),
        { status: options?.method === "POST" ? 409 : 200 },
      ),
  );
  render(Investigations, { status, tick: 0 });
  await fireEvent.input(
    screen.getByLabelText("What would you like to investigate?"),
    { target: { value: "Keep my prompt" } },
  );
  await fireEvent.click(
    screen.getByRole("button", { name: "Start investigation" }),
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "No snapshot is available",
  );
  expect(
    (
      screen.getByLabelText(
        "What would you like to investigate?",
      ) as HTMLTextAreaElement
    ).value,
  ).toBe("Keep my prompt");
});

it("discards an artifact response after switching to a different session", async () => {
  let resolvePreview: (value: Response) => void = () => {};
  const second = {
    ...detail,
    id: "run-two",
    session_id: "session-two",
    prompt: "A different question?",
    result: { answer: "Second run answer", citations: [] },
  };
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/investigations")
      return new Response(JSON.stringify({ items: [run, second] }));
    if (url.includes("/graph?"))
      return new Response(JSON.stringify({ nodes: [], edges: [] }));
    if (url.endsWith("/preview"))
      return new Promise<Response>((r) => (resolvePreview = r));
    return new Response(
      JSON.stringify(url.endsWith("/run-two") ? second : detail),
    );
  });
  history.replaceState(null, "", "/?run=run-one");
  render(Investigations, { status, tick: 0 });
  await screen.findByText("No approval was recorded.");
  await fireEvent.click(
    screen.getByRole("button", { name: /Inspect original/ }),
  );
  await fireEvent.click(
    screen.getByRole("button", { name: /A different question/ }),
  );
  await screen.findByText("Second run answer");
  resolvePreview(
    new Response(
      JSON.stringify({
        text: "STALE PRIVATE PAYLOAD",
        truncated: false,
        artifact: detail.artifacts[0],
      }),
    ),
  );
  await waitFor(() =>
    expect(screen.queryByText("STALE PRIVATE PAYLOAD")).toBeNull(),
  );
});

it("clears an open preview when another client erases the selected run", async () => {
  let erased = false;
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/investigations")
      return new Response(JSON.stringify({ items: [run] }));
    if (url.includes("/graph?"))
      return new Response(JSON.stringify({ nodes: [], edges: [] }));
    if (url.endsWith("/preview"))
      return new Response(
        JSON.stringify({
          text: "PAYLOAD TO ERASE",
          truncated: false,
          artifact: detail.artifacts[0],
        }),
      );
    return new Response(
      JSON.stringify(
        erased
          ? {
              ...detail,
              state: "erased",
              result: {},
              artifacts: [],
              evidence: [],
            }
          : detail,
      ),
    );
  });
  history.replaceState(null, "", "/?run=run-one");
  const { rerender } = render(Investigations, { status, tick: 0 });
  await screen.findByText("No approval was recorded.");
  await fireEvent.click(
    screen.getByRole("button", { name: /Inspect original/ }),
  );
  await screen.findByText("PAYLOAD TO ERASE");
  erased = true;
  await rerender({ status, tick: 1 });
  await screen.findByText("Payloads erased");
  expect(screen.queryByText("PAYLOAD TO ERASE")).toBeNull();
  expect(screen.queryByText("No approval was recorded.")).toBeNull();
});

it("does not reload graph data on an unchanged status poll", async () => {
  history.replaceState(null, "", "/?run=run-one");
  const { rerender } = render(Investigations, { status, tick: 0 });
  await screen.findByText("No approval was recorded.");
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(([url]) => url.includes("/graph?")),
    ).toHaveLength(1),
  );
  await rerender({ status, tick: 1 });
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(([url]) => url === "/api/investigations"),
    ).toHaveLength(2),
  );
  expect(
    fetchMock.mock.calls.filter(([url]) => url.includes("/graph?")),
  ).toHaveLength(1);
});

it("discards an erasure impact response if the reason changed while it was pending", async () => {
  let resolveImpact: (v: Response) => void = () => {};
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/investigations")
      return new Response(JSON.stringify({ items: [run] }));
    if (url.includes("/graph?"))
      return new Response(JSON.stringify({ nodes: [], edges: [] }));
    if (url.endsWith("/erasure-preview"))
      return new Promise<Response>((r) => (resolveImpact = r));
    return new Response(JSON.stringify(detail));
  });
  history.replaceState(null, "", "/?run=run-one");
  render(Investigations, { status, tick: 0 });
  await screen.findByText("No approval was recorded.");
  await fireEvent.click(screen.getByRole("button", { name: "Outputs" }));
  await fireEvent.click(screen.getByLabelText("Select for erasure"));
  await fireEvent.input(screen.getByLabelText("Authority and reason"), {
    target: { value: "Original reason" },
  });
  await fireEvent.click(
    screen.getByRole("button", { name: "Preview erasure impact" }),
  );
  await fireEvent.input(screen.getByLabelText("Authority and reason"), {
    target: { value: "Changed reason" },
  });
  resolveImpact(
    new Response(
      JSON.stringify({
        preview_hash: "a".repeat(64),
        confirmation: "ERASE run-one",
        artifact_ids: ["art-1"],
        affected_runs: ["run-one"],
        external_obligations: [],
        active_runs: [],
      }),
    ),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Preview erasure impact",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  expect(
    screen.queryByRole("button", { name: "Authorize erasure" }),
  ).toBeNull();
});

it("accepts a slow saved-session list across ingestion polling ticks", async () => {
  let resolve!: (response: Response) => void;
  const fetch = vi.fn(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  vi.stubGlobal("fetch", fetch);
  history.replaceState(null, "", "/");
  const props = {
    status: { ...status, state: "updating", revision: 2 },
    tick: 0,
  };
  const app = render(Investigations, props);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  await app.rerender({ ...props, tick: 1 });
  await app.rerender({ ...props, tick: 2 });
  expect(fetch).toHaveBeenCalledTimes(1);
  resolve(new Response(JSON.stringify({ items: [run] })));
  await screen.findByText(run.prompt);
});

it("sends uploaded files with the prompt and shows their filenames", async () => {
  render(Investigations, { status, tick: 0 });
  await fireEvent.input(
    screen.getByLabelText("What would you like to investigate?"),
    {
      target: { value: "Read memo.txt" },
    },
  );
  const input = screen.getByLabelText("Attach files");
  const file = new File(["BLUE-742"], "memo.txt", { type: "text/plain" });
  await fireEvent.change(input, { target: { files: [file] } });
  expect(screen.getByRole("button", { name: "Remove memo.txt" })).toBeTruthy();
  await fireEvent.click(
    screen.getByRole("button", { name: /Start investigation/ }),
  );
  await waitFor(() => {
    const request = fetchMock.mock.calls.find(
      ([url, options]) =>
        url === "/api/investigations" && options?.method === "POST",
    );
    expect(request).toBeTruthy();
    const body = JSON.parse(String(request![1]!.body));
    expect(body.prompt).toBe("Read memo.txt");
    expect(body.attachments).toEqual([
      { name: "memo.txt", data: btoa("BLUE-742") },
    ]);
  });
});

it("offers clarification choices and keeps free-text answers in a linked followup", async () => {
  history.replaceState(null, "", "/?run=run-one");
  const base = fetchMock.getMockImplementation()! as (
    url: string,
    options?: RequestInit,
  ) => Promise<Response>;
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === "/api/investigations/run-one")
      return new Response(
        JSON.stringify({
          ...detail,
          state: "awaiting_input",
          result: {
            answer: "Choose a format",
            questions: [
              {
                id: "format",
                question: "Which format?",
                options: ["Short", "Long"],
              },
            ],
          },
        }),
      );
    return base(url, options);
  });
  render(Investigations, { status, tick: 0 });
  await screen.findByRole("heading", { name: "Codex needs your input" });
  await fireEvent.click(screen.getByRole("button", { name: "Short" }));
  expect(
    (screen.getByLabelText("Your answer") as HTMLTextAreaElement).value,
  ).toBe("Short");
  await fireEvent.input(screen.getByLabelText("Your answer"), {
    target: { value: "Short, in English" },
  });
  await fireEvent.click(
    screen.getByRole("button", { name: "Continue with answers" }),
  );
  expect(
    (
      screen.getByLabelText(
        "What would you like to investigate?",
      ) as HTMLTextAreaElement
    ).value,
  ).toContain("Short, in English");
  await fireEvent.click(
    screen.getByRole("button", { name: /Start investigation/ }),
  );
  await waitFor(() => {
    const request = fetchMock.mock.calls.find(
      ([url, options]) =>
        url === "/api/investigations" && options?.method === "POST",
    );
    expect(JSON.parse(String(request![1]!.body)).parent_id).toBe("run-one");
  });
});
