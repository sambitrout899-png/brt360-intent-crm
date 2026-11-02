import type { ReactNode } from "react";
import type { Invoice, SubStatus } from "./types";

export const INPUT = "w-full min-w-0 rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none disabled:opacity-50";
export const BTN = "inline-flex items-center justify-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-sky-500 hover:text-sky-300 disabled:cursor-not-allowed disabled:opacity-40";
export const BTN_PRIMARY = "inline-flex items-center justify-center gap-1 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40";
export const BTN_DANGER = "inline-flex items-center justify-center gap-1 rounded-lg border border-red-500/40 px-3 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-40";

export function money(amount: number | null | undefined, currency: string, digits = 0): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return "—";
  try {
    return new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", { style: "currency", currency, maximumFractionDigits: digits, minimumFractionDigits: digits }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(digits)}`;
  }
}

export function num(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M` : n >= 10_000 ? `${Math.round(n / 1000)}k` : n.toLocaleString();
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

const STATUS: Record<SubStatus | "none", { label: string; cls: string }> = {
  trial: { label: "Trial", cls: "bg-violet-500/15 text-violet-300 border-violet-500/30" },
  active: { label: "Active", cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  past_due: { label: "Past due", cls: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  suspended: { label: "Suspended", cls: "bg-red-500/15 text-red-300 border-red-500/30" },
  cancelled: { label: "Cancelled", cls: "bg-slate-700/40 text-slate-300 border-slate-600" },
  expired: { label: "Expired", cls: "bg-slate-700/40 text-slate-400 border-slate-600" },
  none: { label: "No subscription", cls: "bg-slate-800 text-slate-400 border-slate-700" },
};

export function StatusBadge({ status }: { status: SubStatus | null | undefined }) {
  const s = STATUS[status ?? "none"];
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${s.cls}`}>{s.label}</span>;
}

export function InvoiceBadge({ status }: { status: Invoice["status"] }) {
  const cls = { paid: "text-emerald-300 bg-emerald-500/10", issued: "text-sky-300 bg-sky-500/10", overdue: "text-red-300 bg-red-500/10", void: "text-slate-500 bg-slate-800", draft: "text-slate-300 bg-slate-800" }[status];
  return <span className={`rounded px-2 py-0.5 text-[11px] font-semibold capitalize ${cls}`}>{status}</span>;
}

export function Card({ title, action, children, className = "" }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-2xl border border-slate-800 bg-slate-900/50 p-4 ${className}`}>
      {(title || action) && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {title && <h3 className="text-sm font-semibold text-slate-100">{title}</h3>}
          {action && <div className="ml-auto flex flex-wrap gap-2">{action}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub, tone = "text-slate-100" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="min-w-0 rounded-2xl border border-slate-800 bg-slate-900/50 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1 truncate text-xl font-bold ${tone}`}>{value}</p>
      {sub && <p className="mt-0.5 truncate text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

/** A used / limit bar: amber from 80%, red at or over the limit. */
export function Meter({ used, limit }: { used: number | null; limit: number }) {
  if (used === null || used === undefined) return <p className="text-xs text-slate-500">{num(limit)} included · usage not measured</p>;
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 100;
  const tone = pct >= 100 ? "bg-red-500" : pct >= 80 ? "bg-amber-400" : "bg-sky-500";
  return (
    <div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-800">
        <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-1 text-xs text-slate-400">
        {num(used)} of {num(limit)} <span className="text-slate-600">({Math.round(pct)}%)</span>
      </p>
    </div>
  );
}

export function Notice({ text, tone = "red", onClose }: { text: string | null; tone?: "red" | "emerald"; onClose: () => void }) {
  if (!text) return null;
  const cls = tone === "red" ? "border-red-500/30 bg-red-500/10 text-red-300" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${cls}`}>
      <span className="min-w-0 flex-1">{text}</span>
      <button type="button" onClick={onClose} className="text-xs opacity-70 hover:opacity-100">✕</button>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block min-w-0 space-y-1">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

export const KIND_LABEL: Record<string, string> = { core: "Core", service: "Service", add_on: "Add-on" };
export const UNIT_LABEL: Record<string, string> = { user: "per user", org: "per organization", pack: "per pack" };
export const CYCLE_LABEL: Record<string, string> = { monthly: "Monthly", quarterly: "Quarterly", annual: "Annual" };
