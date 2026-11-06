import { useContext, useState } from "react";
import type { Fmt } from "../../api/analytics";
import type { AssistantInsight } from "../../api/types";
import { ENTITY_TYPES } from "../../api/types";
import type { EntityType } from "../../api/types";
import { WorkspaceContext } from "../../workspace";
import { BarChart, LineChart } from "../analytics/Charts";
import { fmtValue } from "../analytics/format";

/** An answer from Ask anything's analyst (backend analytics/answers.py): the sentence, the figure, a chart,
 * the table behind it, the records it is about, how it was read, and the questions to ask next. */

const KIND: Record<string, { icon: string; label: string; tone: string }> = {
  data: { icon: "📊", label: "From your data", tone: "from-sky-500/15 to-sky-500/0 border-sky-500/30 text-sky-300" },
  kpi: { icon: "📈", label: "KPI", tone: "from-emerald-500/15 to-emerald-500/0 border-emerald-500/30 text-emerald-300" },
  prediction: { icon: "🔮", label: "Prediction", tone: "from-violet-500/15 to-violet-500/0 border-violet-500/30 text-violet-300" },
  record: { icon: "🗂️", label: "Record", tone: "from-amber-500/15 to-amber-500/0 border-amber-500/30 text-amber-300" },
  mywork: { icon: "📥", label: "Your work", tone: "from-teal-500/15 to-teal-500/0 border-teal-500/30 text-teal-300" },
  knowledge: { icon: "📚", label: "Telecom knowledge", tone: "from-indigo-500/15 to-indigo-500/0 border-indigo-500/30 text-indigo-300" },
  ai: { icon: "✨", label: "AI analyst", tone: "from-fuchsia-500/15 to-fuchsia-500/0 border-fuchsia-500/30 text-fuchsia-300" },
  hello: { icon: "👋", label: "Hello", tone: "from-sky-500/15 to-sky-500/0 border-sky-500/30 text-sky-300" },
};

const asFmt = (f?: string | null): Fmt => (f === "money" || f === "pct" || f === "days" || f === "hours" ? f : undefined);

function cell(v: unknown, fmt?: string | null): string {
  if (v === null || v === undefined || v === "") return "-";
  if (typeof v === "number") return fmtValue(v, asFmt(fmt));
  if (typeof v === "boolean") return v ? "Yes" : "No";
  const s = String(v);
  return /^\d{4}-\d{2}-\d{2}T/.test(s) ? s.slice(0, 16).replace("T", " ") : s.replace(/_/g, " ");
}

export default function InsightAnswer({ insight, onAsk }: { insight: AssistantInsight; onAsk: (question: string) => void }) {
  const ws = useContext(WorkspaceContext);
  const [rows, setRows] = useState(12);
  const kind = KIND[insight.kind] ?? KIND.data;
  const chart = insight.chart && insight.chart.labels.length > 1 ? insight.chart : null;
  const openable = (t: string): t is EntityType => (ENTITY_TYPES as readonly string[]).includes(t);

  return (
    <div className="space-y-4">
      <article className={`overflow-hidden rounded-2xl border bg-gradient-to-br ${kind.tone} bg-slate-900/60`}>
        <div className="flex flex-wrap items-start gap-4 p-5">
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em]">
              <span className="text-base leading-none">{kind.icon}</span>
              {kind.label}
              <span className="font-normal normal-case tracking-normal text-slate-500">· {insight.title}</span>
            </p>
            <p className="mt-2 text-[17px] leading-relaxed text-slate-100">{insight.answer}</p>
          </div>
          {insight.value && (
            <div className="min-w-[10rem] rounded-xl border border-slate-700/70 bg-slate-950/50 px-4 py-3 text-right">
              <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{insight.value.label}</p>
              <p className="mt-1 text-3xl font-bold tracking-tight text-slate-50">{insight.value.display}</p>
            </div>
          )}
        </div>
        {chart && (
          <div className="border-t border-slate-800/80 bg-slate-950/30 px-4 pb-3 pt-4">
            {chart.kind === "line" ? (
              <LineChart labels={chart.labels} series={chart.series} fmt={asFmt(chart.fmt)} height={220} />
            ) : (
              <BarChart labels={chart.labels} series={chart.series} fmt={asFmt(chart.fmt)} height={chart.labels.length > 8 ? 300 : 230}
                horizontal={chart.labels.length > 6 || chart.labels.some((l) => l.length > 14)} showLabels />
            )}
          </div>
        )}
      </article>

      {insight.records && insight.records.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {insight.records.slice(0, 12).map((r) => {
            const canOpen = ws && openable(r.type);
            return (
              <button key={`${r.type}-${r.id}`} type="button" disabled={!canOpen}
                onClick={() => canOpen && ws.openRecord(r.type as EntityType, r.id)}
                className="group flex max-w-xs items-center gap-2 rounded-lg border border-slate-700/80 bg-slate-900/60 px-3 py-1.5 text-left text-xs enabled:hover:border-sky-500/60 disabled:cursor-default">
                <span className="truncate font-semibold text-slate-200 group-enabled:group-hover:text-sky-300">{r.title}</span>
                {r.subtitle && <span className="shrink-0 text-slate-500">{r.subtitle}</span>}
                {canOpen && <span className="text-slate-600 group-hover:text-sky-400">↗</span>}
              </button>
            );
          })}
        </div>
      )}

      {insight.table && insight.table.rows.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-slate-800">
          <div className="max-h-[420px] overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-900 text-[11px] uppercase tracking-wide text-slate-500">
                <tr>{insight.table.columns.map((c) => <th key={c.key} className="whitespace-nowrap px-3 py-2 font-semibold">{c.label}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {insight.table.rows.slice(0, rows).map((r, i) => (
                  <tr key={i} className="hover:bg-slate-800/40">
                    {insight.table!.columns.map((c) => (
                      <td key={c.key} className={`whitespace-nowrap px-3 py-1.5 ${typeof r[c.key] === "number" ? "text-right tabular-nums text-slate-200" : "text-slate-300"}`}>
                        {cell(r[c.key], c.fmt)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {insight.table.rows.length > rows && (
            <button type="button" onClick={() => setRows(rows + 50)} className="w-full border-t border-slate-800 py-2 text-xs font-semibold text-sky-300 hover:bg-slate-800/40">
              Show more ({insight.table.rows.length - rows} more)
            </button>
          )}
        </div>
      )}

      {(insight.facts.length > 0 || insight.source) && (
        <details className="rounded-lg border border-slate-800 bg-slate-900/30 px-3 py-2 text-xs text-slate-400">
          <summary className="cursor-pointer select-none font-semibold text-slate-500">How I worked it out · {insight.source}</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">{insight.facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </details>
      )}

      {insight.follow_ups.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-slate-500">Ask next:</span>
          {insight.follow_ups.map((q) => (
            <button key={q} type="button" onClick={() => onAsk(q)}
              className="rounded-full border border-slate-700 bg-slate-900/50 px-3 py-1 text-xs text-slate-300 hover:border-sky-500 hover:text-sky-200">
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
