import type { FunctionMap, MapEdge, MapNode } from "../../api/functionMapTypes";

export const W = 196; // a function point's card
export const H = 52;
export const GUTTER = 150; // lane names
export const LANE_PAD = 18;
export const GRID = 10;
export const COLLAPSED_LANE = 34;
export const PROCESS_COLORS = ["#38bdf8", "#34d399", "#a78bfa", "#f472b6", "#fbbf24", "#fb7185", "#22d3ee", "#a3e635", "#f97316", "#e879f9", "#60a5fa", "#94a3b8"];

export type Band = FunctionMap["lanes"][number] & { top: number; height: number; collapsed: boolean; count: number };
export type Box = { x0: number; y0: number; x1: number; y1: number };

export const snap = (v: number) => Math.round(v / GRID) * GRID;
export const edgeKey = (e: Pick<MapEdge, "source" | "target" | "kind">) => `${e.source}>${e.target}>${e.kind}`;

/** The lanes stacked top to bottom - each as tall as its lowest card, or a
 * thin strip when collapsed. */
export function laneBands(lanes: FunctionMap["lanes"], nodes: MapNode[], collapsed: Set<string>): Band[] {
  let top = 0;
  return lanes.map((lane) => {
    const mine = nodes.filter((n) => n.lane === lane.key);
    const shut = collapsed.has(lane.key);
    const deepest = Math.max(0, ...mine.map((n) => n.dy + H));
    const height = shut ? COLLAPSED_LANE : Math.max(84, deepest + LANE_PAD);
    const band = { ...lane, top, height, collapsed: shut, count: mine.length };
    top += height;
    return band;
  });
}

export function path(sx: number, sy: number, tx: number, ty: number): string {
  if (tx > sx + 24) {
    const dx = Math.max(40, (tx - sx) / 2);
    return `M${sx},${sy} C${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`;
  }
  // backwards or in the same column: swing out and come back in
  const out = 70;
  return `M${sx},${sy} C${sx + out},${sy + 30} ${tx - out},${ty - 30} ${tx},${ty}`;
}

/** The middle of a link drawn by path() - where its condition is written. */
export function midpoint(sx: number, sy: number, tx: number, ty: number): { x: number; y: number } {
  const [c1x, c1y, c2x, c2y] =
    tx > sx + 24 ? [sx + Math.max(40, (tx - sx) / 2), sy, tx - Math.max(40, (tx - sx) / 2), ty] : [sx + 70, sy + 30, tx - 70, ty - 30];
  return { x: (sx + 3 * c1x + 3 * c2x + tx) / 8, y: (sy + 3 * c1y + 3 * c2y + ty) / 8 };
}

export const OPERATORS: { key: string; label: string; short: string; noValue?: boolean }[] = [
  { key: "eq", label: "is", short: "=" },
  { key: "ne", label: "is not", short: "≠" },
  { key: "in", label: "is one of", short: "in" },
  { key: "contains", label: "contains", short: "has" },
  { key: "gt", label: "is more than", short: ">" },
  { key: "gte", label: "is at least", short: "≥" },
  { key: "lt", label: "is less than", short: "<" },
  { key: "lte", label: "is at most", short: "≤" },
  { key: "not_empty", label: "is filled in", short: "is set", noValue: true },
  { key: "empty", label: "is empty", short: "is empty", noValue: true },
];

/** "decision = reject", "otherwise" - the short form written on the map. */
export function conditionText(when?: MapEdge["when"]): string {
  if (!when) return "";
  if (when.operator === "otherwise") return "otherwise";
  const op = OPERATORS.find((o) => o.key === when.operator);
  // entities.x / result.x / record.x read as "x"; customer.x as "customer x"; parent.opportunity.x as "opportunity x"
  const parts = (when.field ?? "").split(".");
  const key = (parts[0] === "customer" ? parts : parts.slice(1)).join(" ").replace(/_/g, " ");
  return `${key} ${op?.short ?? when.operator}${op?.noValue ? "" : ` ${when.value ?? ""}`}`.trim();
}

/** Tidy up: each business area keeps its lane; inside it the cards line up
 * in columns by how far along the hand-offs they come (a step sits right of
 * the steps before it), ordered to keep links straight. Cards with no
 * hand-offs go in a neat grid after the flow. */
export function autoArrange(nodes: MapNode[], edges: MapEdge[], laneOrder: string[]): MapNode[] {
  const COL = W + 70;
  const ROW = H + 16;
  const TOP = 14;
  const codes = new Set(nodes.map((n) => n.code));
  const links = edges.filter((e) => codes.has(e.source) && codes.has(e.target) && e.source !== e.target && (e.kind === "next" || e.active !== false));
  const out = new Map<string, string[]>();
  const preds = new Map<string, string[]>();
  for (const e of links) {
    if (!out.has(e.source)) out.set(e.source, []);
    if (!preds.has(e.target)) preds.set(e.target, []);
    out.get(e.source)!.push(e.target);
    preds.get(e.target)!.push(e.source);
  }
  const linked = new Set(links.flatMap((e) => [e.source, e.target]));

  // depth-first order that ignores the links closing a loop
  const seen = new Map<string, 1 | 2>();
  const back = new Set<string>();
  const order: string[] = [];
  const visit = (c: string) => {
    seen.set(c, 1);
    for (const t of out.get(c) ?? []) {
      const s = seen.get(t);
      if (s === 1) back.add(`${c}>${t}`);
      else if (!s) visit(t);
    }
    seen.set(c, 2);
    order.push(c);
  };
  const byX = [...nodes].sort((a, b) => a.x - b.x);
  for (const n of byX) if (linked.has(n.code) && !preds.has(n.code)) visit(n.code);
  for (const n of byX) if (linked.has(n.code) && !seen.has(n.code)) visit(n.code);
  order.reverse();

  const rank = new Map<string, number>();
  for (const c of order) {
    const r = rank.get(c) ?? 0;
    rank.set(c, r);
    for (const t of out.get(c) ?? []) if (!back.has(`${c}>${t}`)) rank.set(t, Math.max(rank.get(t) ?? 0, r + 1));
  }

  const lanePos = new Map(laneOrder.map((k, i) => [k, i]));
  const placed = new Map<string, { x: number; dy: number; order: number }>();
  const maxRank = Math.max(0, ...rank.values());
  const lanes = [...new Set(nodes.map((n) => n.lane))];
  // column by column, so each card can line up with the cards that lead to it
  for (let r = 0; r <= maxRank; r++) {
    for (const lane of lanes) {
      const col = nodes.filter((n) => n.lane === lane && rank.get(n.code) === r);
      const weight = (n: MapNode) => {
        const from = (preds.get(n.code) ?? []).map((p) => placed.get(p)?.order).filter((v): v is number => v !== undefined);
        return from.length ? from.reduce((s, v) => s + v, 0) / from.length : Number.MAX_SAFE_INTEGER;
      };
      col
        .sort((a, b) => weight(a) - weight(b) || a.dy - b.dy || a.label.localeCompare(b.label))
        .forEach((n, i) => placed.set(n.code, { x: r * COL, dy: TOP + i * ROW, order: (lanePos.get(lane) ?? 0) * 1000 + i }));
    }
  }
  for (const lane of lanes) {
    const loose = nodes.filter((n) => n.lane === lane && !linked.has(n.code)).sort((a, b) => a.label.localeCompare(b.label));
    const start = Math.max(-1, ...nodes.filter((n) => n.lane === lane && linked.has(n.code)).map((n) => rank.get(n.code) ?? 0)) + 1;
    loose.forEach((n, i) => placed.set(n.code, { x: (start + (i % 3)) * COL, dy: TOP + Math.floor(i / 3) * ROW, order: 0 }));
  }
  return nodes.map((n) => {
    const p = placed.get(n.code);
    return p ? { ...n, x: p.x, dy: p.dy } : n;
  });
}
