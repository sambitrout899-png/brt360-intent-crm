import { useEffect } from "react";
import { PRIORITY_COLOR, STATUS, fmtWhen, relative } from "./style";
import type { Sla } from "./types";

export const BTN = "inline-flex items-center justify-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 transition hover:border-sky-500 hover:text-sky-300 disabled:cursor-not-allowed disabled:opacity-40";
export const BTN_PRIMARY = "inline-flex items-center justify-center gap-1 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40";
export const BTN_GO = "inline-flex items-center justify-center gap-1 rounded-lg bg-emerald-600/20 px-3 py-1.5 text-xs font-semibold text-emerald-300 transition hover:bg-emerald-600/30 disabled:cursor-not-allowed disabled:opacity-40";
export const BTN_WARN = "inline-flex items-center justify-center gap-1 rounded-lg bg-amber-500/15 px-3 py-1.5 text-xs font-semibold text-amber-300 transition hover:bg-amber-500/25 disabled:cursor-not-allowed disabled:opacity-40";
export const BTN_DANGER = "inline-flex items-center justify-center gap-1 rounded-lg border border-red-500/30 px-3 py-1.5 text-xs font-semibold text-red-300 transition hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-40";
export const INPUT = "w-full min-w-0 rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none";
export const CARD = "min-w-0 rounded-2xl border border-slate-800 bg-slate-900/50";

export function Stat({ label, value, sub, tone, onClick }: { label: string; value: string | number; sub?: string; tone?: string; onClick?: () => void }) {
  const body = (
    <>
      <p className="truncate text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1 truncate text-xl font-bold sm:text-2xl ${tone ?? "text-slate-100"}`}>{value}</p>
      {sub && <p className="truncate text-[11px] text-slate-500">{sub}</p>}
    </>
  );
  return onClick ? (
    <button type="button" onClick={onClick} className="min-w-0 rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-3.5 text-left transition hover:border-slate-600">
      {body}
    </button>
  ) : (
    <div className="min-w-0 rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-3.5">{body}</div>
  );
}

export function Section({ title, count, action, children, className = "" }: { title: string; count?: number; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`space-y-3 ${className}`}>
      <div className="flex flex-wrap items-center gap-2 px-1">
        <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
        {count !== undefined && <span className="rounded-full bg-slate-800 px-2 text-xs text-slate-300">{count}</span>}
        <span className="flex-1" />
        {action}
      </div>
      {children}
    </section>
  );
}

export function Panel({ title, action, children, className = "" }: { title?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={`${CARD} p-4 ${className}`}>
      {(title || action) && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {title && <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">{title}</h3>}
          <span className="flex-1" />
          {action}
        </div>
      )}
      {children}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-2xl border border-dashed border-slate-800 px-4 py-8 text-center text-sm text-slate-500">{children}</div>;
}

export function Field({ label, children, hint, className = "" }: { label: string; children: React.ReactNode; hint?: string; className?: string }) {
  return (
    <label className={`block min-w-0 space-y-1 ${className}`}>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

export function StatusPill({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status.replace(/_/g, " "), cls: "bg-slate-500/15 text-slate-300", dot: "#94a3b8" };
  return <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ${s.cls}`}>{s.label}</span>;
}

export function PriorityTag({ priority, label }: { priority: number; label?: string }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 text-[11px] font-semibold text-slate-400">
      <span className="h-2 w-2 rounded-full" style={{ background: PRIORITY_COLOR[priority] ?? "#94a3b8" }} />
      {label ?? ["", "ASAP", "High", "Medium", "Low"][priority] ?? "Medium"}
    </span>
  );
}

const SLA_META: Record<Sla, { label: string; cls: string }> = {
  breached: { label: "SLA breached", cls: "bg-red-500/15 text-red-300" },
  at_risk: { label: "SLA at risk", cls: "bg-amber-500/15 text-amber-300" },
  on_track: { label: "On track", cls: "bg-emerald-500/10 text-emerald-300" },
  none: { label: "Not verified", cls: "bg-slate-500/15 text-slate-400" },
  met: { label: "Done", cls: "bg-slate-500/15 text-slate-400" },
};
export function SlaBadge({ sla, committed }: { sla: Sla; committed?: string | null }) {
  const m = SLA_META[sla];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${m.cls}`} title={committed ? `Committed ${fmtWhen(committed)}` : undefined}>
      {m.label}
      {committed && sla !== "met" && sla !== "none" && <span className="font-normal opacity-80">· {sla === "breached" ? relative(committed) : relative(committed)}</span>}
    </span>
  );
}

export function Chip({ children, tone = "slate" }: { children: React.ReactNode; tone?: "slate" | "sky" | "emerald" | "amber" | "red" | "violet" }) {
  const cls = {
    slate: "bg-slate-800 text-slate-300",
    sky: "bg-sky-500/10 text-sky-300",
    emerald: "bg-emerald-500/10 text-emerald-300",
    amber: "bg-amber-500/10 text-amber-300",
    red: "bg-red-500/10 text-red-300",
    violet: "bg-violet-500/10 text-violet-300",
  }[tone];
  return <span className={`inline-flex max-w-full items-center truncate rounded-md px-1.5 py-0.5 text-[11px] ${cls}`}>{children}</span>;
}

/** A right-hand panel on a desktop, full screen on a phone. */
export function Drawer({ open, onClose, title, subtitle, children, wide }: { open: boolean; onClose: () => void; title: React.ReactNode; subtitle?: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-slate-950/70 backdrop-blur-[1px]" onClick={onClose} />
      <aside className={`relative flex h-full w-full flex-col border-l border-slate-800 bg-slate-950 shadow-2xl ${wide ? "sm:max-w-3xl" : "sm:max-w-xl"}`}>
        <header className="flex items-start gap-3 border-b border-slate-800 px-4 py-3 sm:px-5">
          <div className="min-w-0 flex-1">
            <div className="truncate text-base font-bold text-slate-100">{title}</div>
            {subtitle && <div className="mt-0.5 text-xs text-slate-400">{subtitle}</div>}
          </div>
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-700 px-2.5 py-1 text-sm text-slate-300 hover:border-slate-500">
            ✕
          </button>
        </header>
        <div className="thin-scroll flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-5">{children}</div>
      </aside>
    </div>
  );
}

export function Notice({ text, tone = "red", onClose }: { text: string | null; tone?: "red" | "emerald" | "amber"; onClose?: () => void }) {
  if (!text) return null;
  const cls = { red: "border-red-500/30 bg-red-500/10 text-red-200", emerald: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200", amber: "border-amber-500/30 bg-amber-500/10 text-amber-200" }[tone];
  return (
    <div className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-sm ${cls}`}>
      <span className="min-w-0 flex-1">{text}</span>
      {onClose && (
        <button type="button" className="text-xs opacity-70 hover:opacity-100" onClick={onClose}>
          ✕
        </button>
      )}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string; count?: number }[] }) {
  return (
    <div className="thin-scroll -mx-1 flex gap-1 overflow-x-auto px-1 pb-1">
      {items.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={`shrink-0 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${value === t.id ? "bg-slate-100 text-slate-900" : "text-slate-400 hover:bg-slate-800/70 hover:text-slate-200"}`}
        >
          {t.label}
          {t.count !== undefined && t.count > 0 && <span className={`ml-1.5 rounded-full px-1.5 text-[10px] ${value === t.id ? "bg-slate-900/10" : "bg-slate-800"}`}>{t.count}</span>}
        </button>
      ))}
    </div>
  );
}
