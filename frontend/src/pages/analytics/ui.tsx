import { useEffect, useState } from "react";
import { drillRecords, explainReport, exportCsv } from "../../api/analytics";
import type { Drill, Explanation, Filter, Narrative, RecordsResult } from "../../api/analytics";
import { ENTITY_TYPES } from "../../api/types";
import type { EntityType } from "../../api/types";
import { fmtValue } from "../../components/analytics/format";
import { useWorkspace } from "../../workspace";

export const btn = "rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:border-slate-500 hover:text-white disabled:opacity-50";
export const btnPrimary = "rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50";
export const input = "rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none";

export function Card({ title, sub, action, children, className = "" }: { title?: React.ReactNode; sub?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-2xl border border-slate-800 bg-slate-900/50 p-4 sm:p-5 ${className}`}>
      {(title || action) && (
        <div className="mb-3 flex items-start gap-2">
          <div className="min-w-0 flex-1">
            {title && <h3 className="truncate text-sm font-semibold text-slate-100">{title}</h3>}
            {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

const TONE: Record<string, string> = { good: "bg-emerald-400", bad: "bg-red-400", warn: "bg-amber-400", info: "bg-sky-400" };

export function NarrativeList({ items }: { items?: Narrative[] }) {
  if (!items?.length) return null;
  return (
    <ul className="space-y-1.5">
      {items.map((n, i) => (
        <li key={i} className="flex gap-2 text-xs text-slate-300">
          <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${TONE[n.tone] ?? TONE.info}`} />
          <span>{n.text}</span>
        </li>
      ))}
    </ul>
  );
}

/** "Explain with AI": the tenant's model as an analyst, or the computed facts. */
export function ExplainButton({ title, facts, table }: { title: string; facts: string[]; table?: Record<string, unknown>[] }) {
  const [out, setOut] = useState<Explanation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  return (
    <div>
      <button type="button" className={btn} disabled={busy || !facts.length} onClick={() => {
        setBusy(true);
        setErr("");
        explainReport(title, facts, table).then(setOut).catch((e) => setErr(String(e.message ?? e))).finally(() => setBusy(false));
      }}>
        {busy ? "Thinking…" : "✨ Explain with AI"}
      </button>
      {err && <p className="mt-2 text-xs text-red-400">{err}</p>}
      {out && (
        <div className="mt-3 rounded-xl border border-violet-500/30 bg-violet-500/5 p-3 text-xs">
          <p className="font-semibold text-violet-100">{out.headline}</p>
          {out.insights.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-slate-300">{out.insights.map((x, i) => <li key={i}>{x}</li>)}</ul>}
          {out.actions.length > 0 && (
            <>
              <p className="mt-2 font-semibold text-emerald-300">Recommended actions</p>
              <ul className="mt-1 list-disc space-y-1 pl-4 text-slate-300">{out.actions.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </>
          )}
          {out.risks.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-amber-200">{out.risks.map((x, i) => <li key={i}>{x}</li>)}</ul>}
          <p className="mt-2 text-[10px] text-slate-500">{out.by === "rules" ? out.note : `Written by ${out.by}`}</p>
        </div>
      )}
    </div>
  );
}

/** The records behind a report or a clicked cell - the most granular level. */
export function RecordsDrawer({ entity, filters, drill, title, onClose }: { entity: string; filters: Filter[]; drill: Drill[]; title: string; onClose: () => void }) {
  const ws = useWorkspace();
  const [data, setData] = useState<RecordsResult | null>(null);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<{ key: string; dir: string } | undefined>(undefined);
  const [err, setErr] = useState("");
  const limit = 50;
  useEffect(() => {
    let live = true;
    drillRecords({ entity, filters, drill, sort, offset, limit }).then((d) => live && setData(d)).catch((e) => live && setErr(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [entity, filters, drill, sort, offset]);
  const openable = data?.record_type && (ENTITY_TYPES as readonly string[]).includes(data.record_type);
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <div className="flex h-full w-full max-w-5xl flex-col border-l border-slate-800 bg-[#0a101d] shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3 border-b border-slate-800 p-4">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Records behind</p>
            <h3 className="truncate text-base font-semibold text-white">{title}</h3>
            <p className="mt-0.5 text-xs text-slate-500">
              {drill.map((d) => `${d.field}${d.grain ? ` (${d.grain})` : ""} = ${String(d.value)}`).join(" · ") || "every record in the report"}
              {data ? ` · ${data.total.toLocaleString()} records` : ""}
            </p>
          </div>
          <button type="button" className={btn} onClick={() => exportCsv("records", { entity, filters, drill, columns: data?.columns.map((c) => c.key) }, `${entity}-records`)}>
            ⬇ CSV
          </button>
          <button type="button" className={btn} onClick={onClose}>Close</button>
        </div>
        {err && <p className="p-4 text-sm text-red-400">{err}</p>}
        <div className="flex-1 overflow-auto">
          {data && (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-slate-900">
                <tr>
                  {openable && <th className="w-8" />}
                  {data.columns.map((c) => (
                    <th key={c.key} onClick={() => setSort((s) => ({ key: c.key, dir: s?.key === c.key && s.dir === "desc" ? "asc" : "desc" }))}
                      className="cursor-pointer whitespace-nowrap px-2.5 py-2 text-left font-semibold text-slate-300 hover:text-white">
                      {c.label} {sort?.key === c.key ? (sort.dir === "desc" ? "↓" : "↑") : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r, i) => (
                  <tr key={i} className="border-t border-slate-800/70 hover:bg-slate-800/40">
                    {openable && (
                      <td className="px-2">
                        <button type="button" title="Open the record" className="text-sky-400 hover:text-sky-200"
                          onClick={() => ws.openRecord(data.record_type as EntityType, Number(r.__id))}>↗</button>
                      </td>
                    )}
                    {data.columns.map((c) => (
                      <td key={c.key} className={`whitespace-nowrap px-2.5 py-1.5 ${c.kind === "number" ? "text-right tabular-nums text-slate-100" : "text-slate-300"}`}>
                        {c.kind === "number" ? fmtValue(r[c.key] as number, c.fmt) : c.kind === "bool" ? (r[c.key] ? "Yes" : "No") :
                          c.kind === "date" && r[c.key] ? String(r[c.key]).replace("T", " ").slice(0, 16) : String(r[c.key] ?? "—")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {data && data.total > limit && (
          <div className="flex items-center justify-between border-t border-slate-800 p-3 text-xs text-slate-400">
            <span>{offset + 1}–{Math.min(offset + limit, data.total)} of {data.total.toLocaleString()}</span>
            <span className="flex gap-2">
              <button type="button" className={btn} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹ Prev</button>
              <button type="button" className={btn} disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>Next ›</button>
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
