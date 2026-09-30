import { afterEach, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import WorkspaceGraph from "./WorkspaceGraph.svelte";

afterEach(() => vi.unstubAllGlobals());
vi.mock("@duckdb/duckdb-wasm", () => ({
  VoidLogger: class {},
  AsyncDuckDB: class {
    async instantiate() {
      throw new Error("Graph rendering is covered separately");
    }
    async terminate() {}
  },
}));
vi.mock("@cosmograph/cosmograph", () => ({ Cosmograph: class {} }));
vi.mock("apache-arrow", () => ({}));

const candidate = {
  id: "K1",
  label: "Die Bestellung 1847 wurde genehmigt.",
  kind: "knowledge_claim",
  document_id: "D1",
  details: {
    tool: "knowledge_query",
    snapshot_id: "N1",
    kind: "claim",
    group_id: "K1",
  },
};
function graphFixture() {
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal(
    "Worker",
    class {
      terminate() {}
    },
  );
  return {
    nodes: [candidate],
    edges: [],
    note: "Current collection.",
    truncated: false,
    snapshot_id: "workspace-history",
  };
}

it("loads complete candidate evidence on selection with visible uncertainty", async () => {
  const graph = graphFixture();
  const fetch = vi.fn(
    async (path: string) =>
      new Response(
        JSON.stringify(
          path.includes("/knowledge?")
            ? {
                snapshot_id: "N1",
                items: [
                  {
                    id: "K1",
                    quote:
                      "Die Bestellung 1847 wurde genehmigt, nur nach Prüfung.",
                    source_path: "/fixture/approval.txt",
                    confidence: { claim_truth: null },
                  },
                ],
              }
            : graph,
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(WorkspaceGraph);
  await fireEvent.click(
    await screen.findByRole("button", { name: /knowledge claim/ }),
  );
  await screen.findByText(
    "Die Bestellung 1847 wurde genehmigt, nur nach Prüfung.",
    { selector: "blockquote" },
  );
  expect(
    screen.getByText(/real-world truth may remain uncertain/),
  ).toBeTruthy();
  expect(screen.getByText("Source: /fixture/approval.txt")).toBeTruthy();
  expect(
    fetch.mock.calls.some(
      ([url]) => url === "/api/knowledge?kind=claim&group_id=K1&limit=1",
    ),
  ).toBe(true);
});

it("rejects details from a different snapshot", async () => {
  const graph = graphFixture();
  let resolve: (response: Response) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      path.includes("/knowledge?")
        ? new Promise<Response>((done) => {
            resolve = done;
          })
        : Promise.resolve(new Response(JSON.stringify(graph))),
    ),
  );
  render(WorkspaceGraph);
  await fireEvent.click(
    await screen.findByRole("button", { name: /knowledge claim/ }),
  );
  expect(screen.getByText("Loading source-bound knowledge…")).toBeTruthy();
  resolve(
    new Response(
      JSON.stringify({
        snapshot_id: "N2",
        items: [{ id: "K1", quote: "Must not be shown" }],
      }),
    ),
  );
  await screen.findByRole("alert");
  expect(screen.queryByText("Must not be shown")).toBeNull();
  expect(screen.getByText(/The collection changed/)).toBeTruthy();
});

it("ignores a late detail response after refreshing the graph", async () => {
  const graph = graphFixture();
  let resolve: (response: Response) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      path.includes("/knowledge?")
        ? new Promise<Response>((done) => {
            resolve = done;
          })
        : Promise.resolve(new Response(JSON.stringify(graph))),
    ),
  );
  render(WorkspaceGraph);
  await fireEvent.click(
    await screen.findByRole("button", { name: /knowledge claim/ }),
  );
  await fireEvent.click(screen.getByRole("button", { name: "Refresh graph" }));
  resolve(
    new Response(
      JSON.stringify({
        snapshot_id: "N1",
        items: [{ id: "K1", quote: "Obsolete selected evidence" }],
      }),
    ),
  );
  await screen.findByRole("button", { name: /knowledge claim/ });
  expect(screen.queryByText("Obsolete selected evidence")).toBeNull();
  expect(screen.queryByText("Loading source-bound knowledge…")).toBeNull();
});
it("shows an empty collection without starting the graph engine during ingestion", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            nodes: [],
            edges: [],
            note: "Current collection is incomplete.",
            truncated: true,
          }),
        ),
    ),
  );
  render(WorkspaceGraph);
  await screen.findByText("No graph records available yet");
  expect(screen.queryByText("Loading local graph engine…")).toBeNull();
  expect(screen.queryByRole("button", { name: "Fit graph" })).toBeNull();
});
