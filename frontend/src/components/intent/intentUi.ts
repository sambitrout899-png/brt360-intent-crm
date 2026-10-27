import { useState } from "react";
import type { ValueOrigin } from "../../api/types";

/** Accent per business category - cards, chips and headers share it. */
export const CATEGORY_STYLES: Record<string, { ring: string; text: string; bg: string; dot: string }> = {
  sales: { ring: "border-sky-500/40", text: "text-sky-300", bg: "bg-sky-500/10", dot: "bg-sky-400" },
  customers: { ring: "border-violet-500/40", text: "text-violet-300", bg: "bg-violet-500/10", dot: "bg-violet-400" },
  orders: { ring: "border-emerald-500/40", text: "text-emerald-300", bg: "bg-emerald-500/10", dot: "bg-emerald-400" },
  service: { ring: "border-amber-500/40", text: "text-amber-300", bg: "bg-amber-500/10", dot: "bg-amber-400" },
  billing: { ring: "border-rose-500/40", text: "text-rose-300", bg: "bg-rose-500/10", dot: "bg-rose-400" },
  products: { ring: "border-pink-500/40", text: "text-pink-300", bg: "bg-pink-500/10", dot: "bg-pink-400" },
  records: { ring: "border-slate-500/40", text: "text-slate-300", bg: "bg-slate-500/10", dot: "bg-slate-400" },
  workspace: { ring: "border-indigo-500/40", text: "text-indigo-300", bg: "bg-indigo-500/10", dot: "bg-indigo-400" },
  custom: { ring: "border-fuchsia-500/40", text: "text-fuchsia-300", bg: "bg-fuchsia-500/10", dot: "bg-fuchsia-400" },
};

export function categoryStyle(category: string) {
  return CATEGORY_STYLES[category] ?? CATEGORY_STYLES.records;
}

/** Where a prefilled value came from, as a small badge. */
export const ORIGIN_BADGES: Record<ValueOrigin, { icon: string; label: string; cls: string }> = {
  you: { icon: "✎", label: "you", cls: "border-slate-600 text-slate-400" },
  understood: { icon: "✨", label: "understood", cls: "border-violet-500/40 bg-violet-500/10 text-violet-300" },
  context: { icon: "📌", label: "from this record", cls: "border-sky-500/40 bg-sky-500/10 text-sky-300" },
  linked: { icon: "🔗", label: "from the chosen record", cls: "border-sky-500/40 bg-sky-500/10 text-sky-300" },
  default: { icon: "⚙", label: "default", cls: "border-slate-600 text-slate-400" },
  usual: { icon: "↺", label: "your usual", cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300" },
  guess: { icon: "🤖", label: "best guess", cls: "border-amber-500/40 bg-amber-500/10 text-amber-300" },
  account: { icon: "🏢", label: "this customer's usual", cls: "border-teal-500/40 bg-teal-500/10 text-teal-300" },
  business: { icon: "📊", label: "your business's usual", cls: "border-indigo-500/40 bg-indigo-500/10 text-indigo-300" },
};

export const INPUT_CLS =
  "w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-sky-500 focus:ring-1 focus:ring-sky-500/40";

export function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0) ||
    (typeof value === "object" && !Array.isArray(value) && Object.keys(value as object).length === 0);
}

export function prettyState(state: string | null | undefined): string {
  return (state ?? "").replace(/_/g, " ");
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).replace(/_/g, " ");
}

const TECH_KEY = "brt360_intent_tech_view";

/** "Explain results" - a plain-language note on why an action ran the way
 * it did (never codes or internals). Off by default; remembered per browser. */
export function useTechView(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState<boolean>(() => {
    try {
      return localStorage.getItem(TECH_KEY) === "1";
    } catch {
      return false;
    }
  });
  return [
    on,
    (next: boolean) => {
      setOn(next);
      try {
        localStorage.setItem(TECH_KEY, next ? "1" : "0");
      } catch {
        // per-session only
      }
    },
  ];
}
