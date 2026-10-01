import { expect, it } from "vitest";
import type { GraphData } from "./api";
import { indexGraphLinks, isLargeGraph } from "./graph-data";

it("keeps endpoint indices and click identity aligned when dangling edges are omitted", () => {
  const data: GraphData = {
    nodes: ["a", "b"].map((id) => ({
      id,
      label: id,
      kind: "document",
      document_id: id,
    })),
    edges: [
      { id: "missing", source: "a", target: "outside", type: "test" },
      { id: "backwards", source: "b", target: "a", type: "test" },
      { id: "self", source: "a", target: "a", type: "test" },
    ],
    total_nodes: 2,
    total_edges: 3,
    truncated: true,
    snapshot_id: "test",
  };
  expect(indexGraphLinks(data)).toEqual([
    { edge: data.edges[1], sourceIndex: 1, targetIndex: 0 },
    { edge: data.edges[2], sourceIndex: 0, targetIndex: 0 },
  ]);
  expect(data.edges).toHaveLength(3);
  expect(isLargeGraph(data)).toBe(false);
});

it("reads node identifiers only once at the workspace graph limit", () => {
  let reads = 0;
  const data: GraphData = {
    nodes: Array.from({ length: 5000 }, (_, i) => ({
      get id() {
        reads++;
        return `n${i}`;
      },
      label: `Record ${i}`,
      kind: "document",
      document_id: null,
    })),
    edges: Array.from({ length: 20000 }, (_, i) => ({
      id: `e${i}`,
      source: `n${i % 5000}`,
      target: `n${(i + 1) % 5000}`,
      type: "test",
    })),
    total_nodes: 5000,
    total_edges: 20000,
    truncated: false,
    snapshot_id: "large",
  };
  expect(indexGraphLinks(data)).toHaveLength(20000);
  expect(reads).toBe(5000);
  expect(isLargeGraph(data)).toBe(true);
});
