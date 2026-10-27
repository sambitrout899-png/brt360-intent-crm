import { useCallback, useState } from "react";

/** Server timestamps are naive UTC. */
export const when = (iso: string | null | undefined): Date | null => (iso ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`) : null);

export const fmtTime = (iso: string | null | undefined) => when(iso)?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) ?? "—";

export const fmtDay = (iso: string | null | undefined) => when(iso)?.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) ?? "—";

export const fmtWhen = (iso: string | null | undefined) => (iso ? `${fmtDay(iso)} · ${fmtTime(iso)}` : "—");

export const fmtDate = (d: string | null | undefined) => (d ? new Date(`${d.slice(0, 10)}T00:00:00`).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "—");

export const money = (n: number) => `₹${(Math.round(n * 100) / 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/** The browser's offset as "UTC+05:30" - works on any server, tz database or not. */
export const browserOffset = () => {
  const m = -new Date().getTimezoneOffset();
  const sign = m >= 0 ? "+" : "-";
  const a = Math.abs(m);
  return `UTC${sign}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
};

export function relative(iso: string | null | undefined): string {
  const d = when(iso);
  if (!d) return "";
  const mins = Math.round((d.getTime() - Date.now()) / 60000);
  const abs = Math.abs(mins);
  const text = abs < 60 ? `${abs}m` : abs < 48 * 60 ? `${Math.round(abs / 60)}h` : `${Math.round(abs / 1440)}d`;
  return mins >= 0 ? `in ${text}` : `${text} ago`;
}

export const STATUS: Record<string, { label: string; cls: string; dot: string }> = {
  unscheduled: { label: "Unscheduled", cls: "bg-slate-500/15 text-slate-300", dot: "#94a3b8" },
  scheduled: { label: "Scheduled", cls: "bg-sky-500/15 text-sky-300", dot: "#38bdf8" },
  en_route: { label: "En route", cls: "bg-indigo-500/15 text-indigo-300", dot: "#818cf8" },
  on_site: { label: "On site", cls: "bg-amber-500/15 text-amber-300", dot: "#fbbf24" },
  complete: { label: "Done", cls: "bg-emerald-500/15 text-emerald-300", dot: "#34d399" },
  cancelled: { label: "Cancelled", cls: "bg-slate-500/10 text-slate-500", dot: "#475569" },
  open: { label: "Open", cls: "bg-sky-500/15 text-sky-300", dot: "#38bdf8" },
  diagnosing: { label: "Diagnosing", cls: "bg-violet-500/15 text-violet-300", dot: "#a78bfa" },
  resolved: { label: "Resolved", cls: "bg-emerald-500/15 text-emerald-300", dot: "#34d399" },
  closed: { label: "Closed", cls: "bg-slate-500/15 text-slate-400", dot: "#64748b" },
};

export const PRIORITY_COLOR: Record<number, string> = { 1: "#f87171", 2: "#fb923c", 3: "#38bdf8", 4: "#94a3b8" };

/** Run one change at a time; remember the server's refusal to show it. */
export function useAction(after?: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const run = useCallback(
    async <T,>(fn: () => Promise<T>, success?: string): Promise<T | undefined> => {
      setBusy(true);
      setError(null);
      setDone(null);
      try {
        const out = await fn();
        if (success) setDone(success);
        after?.();
        return out;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [after]
  );
  return { busy, error, done, run, setError, setDone };
}

export const CATEGORY_LABEL: Record<string, string> = {
  installation: "Installation",
  repair: "Repair",
  preventive_maintenance: "Preventive maintenance",
  inspection: "Inspection",
  other: "Other",
};

/** Local "YYYY-MM-DDTHH:mm" for an <input type="datetime-local"> from a UTC timestamp, and back. */
export const toLocalInput = (iso: string | null | undefined) => {
  const d = when(iso);
  if (!d) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

export const fromLocalInput = (v: string) => (v ? new Date(v).toISOString().slice(0, 19) : null);
