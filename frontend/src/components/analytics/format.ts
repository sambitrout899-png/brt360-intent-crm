import type { Fmt } from "../../api/analytics";

export const PALETTES: Record<string, string[]> = {
  brt: ["#38bdf8", "#a78bfa", "#34d399", "#fbbf24", "#f472b6", "#60a5fa", "#fb923c", "#94a3b8", "#2dd4bf", "#f87171"],
  ocean: ["#0ea5e9", "#06b6d4", "#14b8a6", "#22c55e", "#3b82f6", "#6366f1", "#8b5cf6", "#0284c7", "#0891b2", "#0d9488"],
  sunset: ["#f97316", "#f43f5e", "#eab308", "#ec4899", "#ef4444", "#f59e0b", "#d946ef", "#fb7185", "#fdba74", "#a855f7"],
  mono: ["#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b", "#475569", "#38bdf8", "#7dd3fc", "#bae6fd", "#334155", "#1e293b"],
  telecom: ["#e60000", "#38bdf8", "#fbbf24", "#34d399", "#a78bfa", "#f472b6", "#fb923c", "#94a3b8", "#2dd4bf", "#60a5fa"],
};

export function palette(name?: string): string[] {
  return PALETTES[name ?? "brt"] ?? PALETTES.brt;
}

/** A value as people read it: ₹ in lakh / crore, %, days. */
export function fmtValue(v: unknown, fmt?: Fmt, compact = false): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v !== "number") return String(v);
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (fmt === "money") {
    if (abs >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
    if (abs >= 1e5) return `₹${(v / 1e5).toFixed(compact ? 1 : 2)} L`;
    if (compact && abs >= 1e3) return `₹${(v / 1e3).toFixed(1)}k`;
    return `₹${v.toLocaleString(undefined, { maximumFractionDigits: abs < 100 ? 2 : 0 })}`;
  }
  if (fmt === "pct") return `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  if (fmt === "days") return `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })} d`;
  if (fmt === "hours") return `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })} h`;
  if (fmt === "ms") return `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} ms`;
  if (fmt === "gb")return `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })} GB`;
  if (compact) {
    if (abs >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
    if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (abs >= 1e4) return `${(v / 1e3).toFixed(1)}k`;
  }
  return v.toLocaleString(undefined, { maximumFractionDigits: Number.isInteger(v) ? 0 : 2 });
}

/** Round axis ticks covering [min, max]. */
export function ticks(min: number, max: number, count = 5): number[] {
  if (min === max) {
    max = min + 1;
    min = Math.min(0, min);
  }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(Math.abs(raw) || 1));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw) ?? raw;
  const start = Math.floor(min / step) * step;
  const out: number[] = [];
  for (let t = start; t <= max + step * 0.5 && out.length < 20; t += step) out.push(Math.round(t * 1e6) / 1e6);
  return out;
}

export const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
