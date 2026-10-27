import type { MapNode } from "../../api/functionMapTypes";

export const normalize = (value: string) => value.toLocaleLowerCase().normalize("NFKD").replace(/\p{M}/gu, "").replace(/[^\p{L}\p{N}]+/gu, " ").trim();

export function matchesFunctionPoint(node: MapNode, query: string): boolean {
  const text = normalize([node.code, node.label, node.default_label, node.description, node.lane, node.target,
    ...(node.phrases ?? []), ...(node.examples ?? [])].join(" "));
  return normalize(query).split(/\s+/).every((term) => text.includes(term));
}
