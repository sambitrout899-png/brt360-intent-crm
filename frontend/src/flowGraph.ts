import type { FlowGraph } from "./api/types";

const NODE_LABELS: Record<string, string> = {
  start: "Start", end: "End", condition: "Condition", ai_decision: "AI Decision",
  set_field: "Set Field", transition: "Change State", assign: "Assign",
  notify: "Notify", reject: "Reject", action: "Run an action",
};

/** Older generated flows may omit presentation metadata. Preserve their logic. */
export function editableFlowGraph(graph: FlowGraph): FlowGraph {
  return {
    ...graph,
    nodes: graph.nodes.map((node, index) => ({
      ...node,
      label: typeof node.label === "string" ? node.label : NODE_LABELS[node.type] ?? node.type,
      x: Number.isFinite(node.x) ? node.x : 80,
      y: Number.isFinite(node.y) ? node.y : 40 + index * 120,
      config: node.config ?? {},
    })),
    edges: graph.edges.map((edge) => ({ ...edge, label: edge.label ?? "" })),
  };
}
