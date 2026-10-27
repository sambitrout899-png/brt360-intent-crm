/** Telecom KPIs: the operator's scorecard - revenue, churn, sales, fulfilment, assurance, billing, usage, fraud. */
import { useEffect, useState } from "react";
import { telecomScorecard } from "../../api/analytics";
import type { Kpi, Scorecard as Data, SeriesChart } from "../../api/analytics";
import { BarChart, DonutChart, Funnel, LineChart } from "../../components/analytics/Charts";
import { fmtValue } from "../../components/analytics/format";
import { Card, ExplainButton, NarrativeList, btn, input } from "./ui";

function KpiTile({ k }: { k: Kpi }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/40 px-3.5 py-3">
      <p className="truncate text-[11px] font-semibold uppercase tracking-wider text-slate-500" title={k.label}>{k.label}</p>
      <p className="mt-1 text-xl font-bold text-slate-50">{fmtValue(k.value, k.fmt, true)}</p>
      <p className="mt-0.5 truncate text-[11px] text-slate-500" title={k.hint}>{k.hint || " "}</p>
    </div>
  );
}

export function ChartOf({ c, height = 240 }: { c: SeriesChart; height?: number }) {
  if (c.kind === "donut") return <DonutChart labels={c.labels} values={c.series[0]?.values ?? []} fmt={c.fmt} size={180} />;
  if (c.kind === "funnel") return <Funnel labels={c.labels} values={c.series[0]?.values ?? []} fmt={c.fmt} />;
  if (c.kind === "line" || c.kind === "area") return <LineChart labels={c.labels} series={c.series} fmt={c.fmt} area={c.kind === "area"} height={height} />;
  return <BarChart labels={c.labels} series={c.series} fmt={c.fmt} height={height} horizontal={c.kind === "hbar"} mode={c.kind === "stacked" ? "stacked" : "grouped"} />;
}

export default function Scorecard() {
  const [data, setData] = useState<Data | null>(null);
  const [months, setMonths] = useState(12);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    setBusy(true);
    telecomScorecard(months).then(setData).catch((e) => setErr(String(e.message ?? e))).finally(() => setBusy(false));
  }, [months]);
  if (err) return <p className="text-sm text-red-400">{err}</p>;
  if (!data) return <p className="text-sm text-slate-500">Computing the scorecard…</p>;
  const facts = [
    ...data.notes.map((n) => n.text),
    ...data.groups.flatMap((g) => g.kpis.filter((k) => k.value !== null).map((k) => `${g.title} - ${k.label}: ${fmtValue(k.value, k.fmt)}${k.hint ? ` (${k.hint})` : ""}`)),
  ];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs text-slate-500">As of {data.generated_at.replace("T", " ")} UTC{busy ? " · refreshing…" : ""}</p>
        <span className="flex-1" />
        <select value={months} onChange={(e) => setMonths(Number(e.target.value))} className={input}>
          {[6, 12, 18, 24, 36].map((m) => <option key={m} value={m}>Trends over {m} months</option>)}
        </select>
        <button type="button" className={btn} onClick={() => window.print()}>🖨 Print / PDF</button>
      </div>
      {(data.notes.length > 0 || facts.length > 0) && (
        <Card title="Headlines" action={<ExplainButton title="Telecom scorecard" facts={facts} />}>
          <NarrativeList items={data.notes.length ? data.notes : [{ tone: "info", text: "Nothing unusual stands out - every KPI below is within its normal range." }]} />
        </Card>
      )}
      {data.groups.map((g) => (
        <div key={g.title} className="space-y-2">
          <h2 className="px-1 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-500">{g.title}</h2>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">{g.kpis.map((k) => <KpiTile key={k.key} k={k} />)}</div>
        </div>
      ))}
      <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {data.charts.map((c) => (
          <Card key={c.key} title={c.title} className={c.key === "mrr_trend" ? "lg:col-span-2" : ""}>
            <ChartOf c={c} height={c.key === "mrr_trend" ? 280 : 240} />
          </Card>
        ))}
      </div>
    </div>
  );
}
