/* Small shared pieces for the training workspace (dark slate, like the rest of BRT 360). */

export const btn = "inline-flex min-h-9 items-center justify-center gap-1 rounded-lg px-3 py-1.5 text-sm font-semibold disabled:opacity-40";
export const btnPrimary = `${btn} bg-sky-600 text-white hover:bg-sky-500`;
export const btnGhost = `${btn} border border-slate-700 text-slate-200 hover:border-sky-500`;
export const btnDanger = `${btn} border border-red-500/50 text-red-300 hover:bg-red-500/10`;
export const field = "w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none";
export const label = "block space-y-1 text-xs font-medium text-slate-400";

export function Card({ title, actions, children, className = "" }: { title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-slate-800 bg-slate-900/50 p-4 ${className}`}>
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {title && <h3 className="min-w-0 flex-1 text-sm font-bold text-slate-100">{title}</h3>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

const TONES: Record<string, string> = {
  draft: "bg-slate-700/60 text-slate-200",
  scheduled: "bg-sky-500/15 text-sky-300",
  published: "bg-emerald-500/15 text-emerald-300",
  in_progress: "bg-amber-500/15 text-amber-300",
  completed: "bg-violet-500/15 text-violet-300",
  closed: "bg-slate-600/40 text-slate-300",
  retired: "bg-slate-600/40 text-slate-400",
  cancelled: "bg-red-500/15 text-red-300",
  registered: "bg-sky-500/15 text-sky-300",
  nominated: "bg-slate-700/60 text-slate-200",
  waitlisted: "bg-amber-500/15 text-amber-300",
  attended: "bg-emerald-500/15 text-emerald-300",
  no_show: "bg-red-500/15 text-red-300",
  Valid: "bg-emerald-500/15 text-emerald-300",
  Expiring: "bg-amber-500/15 text-amber-300",
  Expired: "bg-red-500/15 text-red-300",
  Revoked: "bg-red-500/15 text-red-300",
  sent: "bg-emerald-500/15 text-emerald-300",
  clicked: "bg-emerald-500/15 text-emerald-300",
  opened: "bg-emerald-500/15 text-emerald-300",
  failed: "bg-red-500/15 text-red-300",
  skipped: "bg-slate-700/60 text-slate-300",
  queued: "bg-amber-500/15 text-amber-300",
  open: "bg-emerald-500/15 text-emerald-300",
};

export function Badge({ value, children }: { value: string; children?: React.ReactNode }) {
  return <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold ${TONES[value] ?? "bg-slate-700/60 text-slate-200"}`}>{children ?? value.replace(/_/g, " ")}</span>;
}

export function Note({ kind = "error", children }: { kind?: "error" | "ok" | "info"; children: React.ReactNode }) {
  if (!children) return null;
  const tone =
    kind === "error" ? "border-red-500/40 bg-red-500/10 text-red-200" : kind === "ok" ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200" : "border-slate-700 bg-slate-900 text-slate-300";
  return (
    <p role={kind === "error" ? "alert" : "status"} className={`rounded-lg border px-3 py-2 text-sm ${tone}`}>
      {children}
    </p>
  );
}

export function Stat({ label: name, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{name}</p>
      <p className="mt-1 text-xl font-bold tabular-nums text-slate-100">{value}</p>
      {hint && <p className="text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

export function Table({ head, rows, empty = "Nothing yet." }: { head: React.ReactNode[]; rows: React.ReactNode[][]; empty?: string }) {
  if (!rows.length) return <p className="text-sm text-slate-500">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-left text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-[11px] uppercase tracking-wide text-slate-500">
            {head.map((h, i) => (
              <th key={i} className="px-2 py-2 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-800/60 last:border-0">
              {r.map((c, j) => (
                <td key={j} className="px-2 py-2 align-top text-slate-200">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
