import { it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/svelte";
import Graph from "./Graph.svelte";
const { instantiate, terminate } = vi.hoisted(() => ({
  instantiate: vi.fn(() => new Promise<void>(() => {})),
  terminate: vi.fn(async () => {}),
}));
vi.mock("@duckdb/duckdb-wasm", () => ({
  VoidLogger: class {},
  AsyncDuckDB: class {
    instantiate = instantiate;
    terminate = terminate;
  },
}));
vi.mock("@cosmograph/cosmograph", () => ({ Cosmograph: class {} }));
vi.mock("apache-arrow", () => ({ tableFromJSON: vi.fn() }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("bounds worker startup stalls and offers the document list", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal(
    "Worker",
    class {
      terminate() {}
    },
  );
  render(Graph, {
    data: {
      nodes: [{ id: "d1", document_id: "d1", label: "Test", kind: "document" }],
      edges: [],
      total_nodes: 1,
      total_edges: 0,
      truncated: false,
      snapshot_id: "test",
    },
    onSelect: () => {},
  });
  await vi.waitFor(() => expect(instantiate).toHaveBeenCalled());
  await vi.advanceTimersByTimeAsync(15001);
  expect(
    screen.getByText(/Graph unavailable: Local graph engine did not start/),
  ).toBeTruthy();
  expect(screen.getByText(/Use the record list/)).toBeTruthy();
  expect(terminate).toHaveBeenCalled();
});
