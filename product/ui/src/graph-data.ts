import type { GraphData } from "./api";

// Resolve endpoints once: scanning every node for every edge is quadratic.
// Keep the edge itself alongside its indices so clicks still identify the
// correct record if an edge outside the loaded node set is omitted.
export function indexGraphLinks(data: GraphData) {
  const indices = new Map(data.nodes.map((node, index) => [node.id, index]));
  return data.edges.flatMap((edge) => {
    const sourceIndex = indices.get(edge.source);
    const targetIndex = indices.get(edge.target);
    return sourceIndex === undefined || targetIndex === undefined
      ? []
      : [{ edge, sourceIndex, targetIndex }];
  });
}

export function isLargeGraph(data: GraphData) {
  return data.nodes.length > 1000 || data.edges.length > 5000;
}
