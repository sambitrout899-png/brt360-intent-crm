import type { C360Layout, C360Widget, C360WidgetType } from "../../api/c360Types";

export function newId(prefix: string): string {
  return `${prefix}${Math.random().toString(16).slice(2, 10)}`;
}

export function move<T>(list: T[], index: number, delta: number): T[] {
  const to = index + delta;
  if (to < 0 || to >= list.length) return list;
  const copy = [...list];
  const [item] = copy.splice(index, 1);
  copy.splice(to, 0, item);
  return copy;
}

/** Which chart types can draw a widget's data - mirrors the backend agent. */
export function compatibleTypes(w: C360Widget): C360WidgetType[] {
  if (w.type === "records" || w.type === "insights") return [w.type];
  if (w.type === "table") return ["table"];
  if (w.type === "kpi" || w.type === "gauge") return ["kpi", "gauge"];
  if (w.metric.x) return ["area", "line", "bar"];
  return ["bar", "donut", "table"];
}

export const TYPE_LABELS: Record<C360WidgetType, string> = {
  kpi: "Number",
  gauge: "Gauge",
  line: "Line",
  area: "Area",
  bar: "Bars",
  donut: "Donut",
  table: "Table",
  records: "Workbench",
  insights: "AI insights",
};

export const TYPE_ICONS: Record<C360WidgetType, string> = {
  kpi: "🔢",
  gauge: "⏲",
  line: "📈",
  area: "🏔",
  bar: "📊",
  donut: "🍩",
  table: "📋",
  records: "🗂",
  insights: "✨",
};

export const WIDTH_LABELS: Record<string, string> = { third: "⅓", half: "½", two_thirds: "⅔", full: "Full" };

export function sameLayout(a: C360Layout | null, b: C360Layout | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
