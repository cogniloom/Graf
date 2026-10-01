import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/svelte";
import Graph from "./Graph.svelte";
import type { CosmographConfig } from "@cosmograph/cosmograph";

const state = vi.hoisted(() => ({
  config: {} as CosmographConfig,
  updates: [] as CosmographConfig[],
  pause: vi.fn(),
  destroy: vi.fn(async () => {}),
}));
vi.mock("@duckdb/duckdb-wasm", () => ({
  VoidLogger: class {},
  AsyncDuckDB: class {
    async instantiate() {}
    async connect() {
      return { insertArrowTable: async () => {}, close: async () => {} };
    }
    async terminate() {}
  },
}));
vi.mock("@cosmograph/cosmograph", () => ({
  Cosmograph: class {
    constructor(_host: unknown, config: CosmographConfig) {
      state.config = config;
    }
    stats = { pointsCount: 1001 };
    async dataUploaded() {}
    async getConfig() {
      return state.config;
    }
    async setConfig(config: CosmographConfig) {
      state.config = config;
      state.updates.push(config);
    }
    fitView() {}
    pause = state.pause;
    destroy = state.destroy;
  },
}));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  state.updates = [];
});

it("preserves data and callbacks across display toggles and pauses hidden graphs", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal(
    "Worker",
    class {
      terminate() {}
    },
  );
  const onSelectEdge = vi.fn();
  const { unmount } = render(Graph, {
    data: {
      nodes: Array.from({ length: 1001 }, (_, i) => ({
        id: `n${i}`,
        label: `Record ${i}`,
        kind: "document",
        document_id: `n${i}`,
      })),
      edges: [
        { id: "dangling", source: "outside", target: "n0", type: "reference" },
        { id: "valid", source: "n0", target: "n1", type: "reference" },
      ],
      total_nodes: 1001,
      total_edges: 2,
      truncated: false,
      snapshot_id: "test",
    },
    onSelectEdge,
  });
  await vi.waitFor(() =>
    expect(screen.getByRole("button", { name: "Fit graph" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  expect(state.config.pixelRatio).toBe(1);
  expect(state.config.showLabels).toBe(false);
  const callback = state.config.onLinkClick!;
  // Exercise the callback that was handed to the renderer, including filtered edges.
  (callback as (index: number) => void)(0);
  expect(onSelectEdge).toHaveBeenCalledWith("valid");
  await fireEvent.click(screen.getByRole("button", { name: "Labels" }));
  await fireEvent.click(screen.getByRole("button", { name: "Connections" }));
  await vi.waitFor(() => expect(state.updates).toHaveLength(2));
  expect(state.config).toMatchObject({
    points: "graf_points",
    links: "graf_links",
    showLabels: true,
    renderLinks: false,
    pixelRatio: 1,
    onLinkClick: callback,
  });
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  await fireEvent(document, new Event("visibilitychange"));
  expect(state.pause).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Resume motion" })).toBeTruthy();
  unmount();
  await vi.waitFor(() => expect(state.destroy).toHaveBeenCalled());
});
