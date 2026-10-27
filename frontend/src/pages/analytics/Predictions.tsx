/**
 * Predictions: forecasts of any measure, churn risk (with drivers and CLV),
 * deal win probability, behavioural segments, next best offer and usage
 * alerts - each says which model made it and how well it tested.
 */
import { useEffect, useState } from "react";
import { predictChurn, predictNextOffer, predictSegments, predictUsageAlerts, predictWin, runForecast } from "../../api/analytics";
import type { ACatalog, ChurnResult, ForecastResult, OfferResult, SegmentsResult, UsageAlerts, WinResult } from "../../api/analytics";
import { LineChart } from "../../components/analytics/Charts";
import { fmtValue } from "../../components/analytics/format";
import type { EntityType } from "../../api/types";
import { useWorkspace } from "../../workspace";
import { Card, ExplainButton, btn, btnPrimary, input } from "./ui";

const TABS = [
  ["forecast", "📈 Forecast"], ["churn", "⚠ Churn risk & CLV"], ["win", "🎯 Deal win probability"], ["segments", "🧩 Customer segments"],
  ["offer", "🎁 Next best offer"], ["usage", "📶 Usage & fraud alerts"],
] as const;
type Tab = (typeof TABS)[number][0];

function useLoad<T>(fn: () => Promise<T>): { data: T | null; err: string; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    setErr("");
    fn().then((d) => live && setData(d)).catch((e) => live && setErr(String(e.message ?? e)));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n]);
  return { data, err, reload: () => setN((x) => x + 1) };
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/40 px-3.5 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-bold text-white">{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

function ModelNote({ method, metrics }: { method: string; metrics?: Record<string, number | null> }) {
  const m = metrics ?? {};
  const auc = m.auc_holdout ?? m.auc_train;
  return (
    <span className="text-[11px] text-slate-500">
      <span className="text-slate-400">Model:</span> {method}
      {m.trained_on !== undefined && ` · learned from ${m.trained_on} records (${m.positives ?? 0} positive)`}
      {auc !== null && auc !== undefined && ` · AUC ${auc}${m.auc_holdout ? " on held-out data" : " in-sample"}`}
      {m.accuracy !== undefined && m.accuracy !== null && ` · accuracy ${Math.round(100 * m.accuracy)}%`}
    </span>
  );
}

/** A probability as a bar - red when high is bad (churn), green when high is good (winning a deal). */
function ProbBar({ p, highIsGood = false }: { p: number; highIsGood?: boolean }) {
  const [hi, lo] = highIsGood ? ["bg-emerald-500", "bg-red-500"] : ["bg-red-500", "bg-emerald-500"];
  const color = p >= 0.5 ? hi : p >= 0.25 ? "bg-amber-400" : lo;
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-800"><span className={`block h-full ${color}`} style={{ width: `${Math.round(p * 100)}%` }} /></span>
      <span className="tabular-nums text-slate-200">{Math.round(p * 100)}%</span>
    </span>
  );
}

export default function Predictions({ catalog }: { catalog: ACatalog }) {
  const [tab, setTab] = useState<Tab>("forecast");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5">
        {TABS.map(([k, l]) => (
          <button key={k} type="button" onClick={() => setTab(k)}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${tab === k ? "bg-violet-500/20 text-violet-100 ring-1 ring-violet-500/50" : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"}`}>
            {l}
          </button>
        ))}
      </div>
      {tab === "forecast" && <Forecast catalog={catalog} />}
      {tab === "churn" && <Churn />}
      {tab === "win" && <Win />}
      {tab === "segments" && <Segments />}
      {tab === "offer" && <Offers />}
      {tab === "usage" && <Usage />}
    </div>
  );
}

// ---------------------------------------------------------------- forecast

function Forecast({ catalog }: { catalog: ACatalog }) {
  const [preset, setPreset] = useState(catalog.presets[0]?.key ?? "orders");
  const [custom, setCustom] = useState({ entity: "order", field: "", agg: "count" });
  const [grain, setGrain] = useState("");
  const [horizon, setHorizon] = useState(6);
  const [data, setData] = useState<ForecastResult | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const entity = catalog.entities.find((e) => e.key === custom.entity);
  const run = () => {
    setBusy(true);
    setErr("");
    const body = preset === "custom"
      ? { entity: custom.entity, measure: custom.field ? { field: custom.field, agg: custom.agg === "count" ? "sum" : custom.agg } : { agg: "count" }, grain: grain || "month", horizon }
      : { preset, grain: grain || undefined, horizon };
    runForecast(body).then(setData).catch((e) => setErr(String(e.message ?? e))).finally(() => setBusy(false));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(run, []);
  const labels = data ? [...data.history.map((h) => h.period), ...data.forecast.map((f) => f.period)] : [];
  const n = data?.history.length ?? 0;
  const facts = data ? [
    `${data.label}: next ${data.grain} forecast ${fmtValue(data.next, data.fmt)}; ${data.forecast.length} ${data.grain}s total ${fmtValue(data.horizon_total, data.fmt)}`,
    `Trend ${data.trend.direction} (${data.trend.pct_of_mean ?? 0}% of the average per ${data.grain})`,
    data.accuracy.mape !== undefined && data.accuracy.mape !== null ? `Back-test error (MAPE) ${data.accuracy.mape}% on the last ${data.accuracy.holdout} ${data.grain}s` : "",
    ...data.anomalies.map((a) => `Unusual ${a.kind} in ${a.period}: ${fmtValue(a.value, data.fmt)} vs typical ${fmtValue(a.expected, data.fmt)}`),
  ].filter(Boolean) : [];
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-slate-400">What to forecast
            <select value={preset} onChange={(e) => setPreset(e.target.value)} className={`${input} mt-1 block min-w-[220px]`}>
              {catalog.presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
              <option value="custom">Custom measure…</option>
            </select>
          </label>
          {preset === "custom" && (
            <>
              <label className="text-[11px] text-slate-400">Of
                <select value={custom.entity} onChange={(e) => setCustom({ entity: e.target.value, field: "", agg: "count" })} className={`${input} mt-1 block`}>
                  {catalog.entities.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
                </select>
              </label>
              <label className="text-[11px] text-slate-400">Measure
                <select value={custom.field} onChange={(e) => setCustom({ ...custom, field: e.target.value })} className={`${input} mt-1 block`}>
                  <option value="">Number of records</option>
                  {entity?.fields.filter((f) => f.kind === "number" && f.key !== "id").map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                </select>
              </label>
              {custom.field && (
                <label className="text-[11px] text-slate-400">As
                  <select value={custom.agg} onChange={(e) => setCustom({ ...custom, agg: e.target.value })} className={`${input} mt-1 block`}>
                    {["sum", "avg", "max"].map((a) => <option key={a} value={a}>{a}</option>)}
                  </select>
                </label>
              )}
            </>
          )}
          <label className="text-[11px] text-slate-400">By
            <select value={grain} onChange={(e) => setGrain(e.target.value)} className={`${input} mt-1 block`}>
              <option value="">default</option>
              {["day", "week", "month", "quarter"].map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </label>
          <label className="text-[11px] text-slate-400">Periods ahead
            <input type="number" min={1} max={36} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} className={`${input} mt-1 block w-20`} />
          </label>
          <button type="button" className={btnPrimary} onClick={run} disabled={busy}>{busy ? "Forecasting…" : "Forecast"}</button>
        </div>
      </Card>
      {err && <p className="text-xs text-red-400">{err}</p>}
      {data?.note && <Card><p className="text-sm text-slate-400">{data.note}</p></Card>}
      {data && !data.note && (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
            <Stat label={`Next ${data.grain}`} value={fmtValue(data.next, data.fmt, true)} hint={data.forecast[0] ? `range ${fmtValue(data.forecast[0].lower, data.fmt, true)} – ${fmtValue(data.forecast[0].upper, data.fmt, true)}` : ""} />
            <Stat label={`Next ${data.forecast.length} ${data.grain}s`} value={fmtValue(data.horizon_total, data.fmt, true)} />
            <Stat label="Trend" value={`${data.trend.direction === "up" ? "↑" : data.trend.direction === "down" ? "↓" : "→"} ${data.trend.pct_of_mean ?? 0}%`} hint={`per ${data.grain}, R² ${data.trend.r2}`} />
            <Stat label="Back-test error" value={data.accuracy.mape !== undefined && data.accuracy.mape !== null ? `${data.accuracy.mape}%` : "—"}
              hint={data.accuracy.holdout ? `MAPE on the last ${data.accuracy.holdout} ${data.grain}s${data.accuracy.skill_vs_naive !== null && data.accuracy.skill_vs_naive !== undefined ? ` · ${data.accuracy.skill_vs_naive}% better than naive` : ""}` : "needs 8+ periods"} />
            <Stat label="Confidence" value={data.confidence} hint={`${data.points} periods of history`} />
          </div>
          <Card title={data.label} sub={`${data.method}${data.partial_period ? ` · ${data.partial_period} is still under way - forecast, not learned from` : ""}`}>
            <LineChart labels={labels} height={320} fmt={data.fmt} splitAt={n}
              series={[
                { name: "Actual", values: [...data.history.map((h) => h.value), ...data.forecast.map(() => null)] },
                { name: "Forecast", values: [...data.history.map((_, i) => (i === n - 1 ? data.history[i].value : null)), ...data.forecast.map((f) => f.value)], dashed: true },
                { name: "Model fit", values: [...data.history.map((h) => h.fitted), ...data.forecast.map(() => null)], dashed: true },
              ]}
              band={{ lower: [...data.history.map(() => null), ...data.forecast.map((f) => f.lower)], upper: [...data.history.map(() => null), ...data.forecast.map((f) => f.upper)] }}
              marks={data.anomalies.map((a) => ({ index: data.history.findIndex((h) => h.period === a.period), kind: a.kind, text: `${a.kind} - ${fmtValue(a.value, data.fmt)} vs ${fmtValue(a.expected, data.fmt)}` })).filter((m) => m.index >= 0)} />
            <div className="mt-3 grid gap-4 lg:grid-cols-2">
              <div className="text-xs text-slate-400">
                {data.anomalies.length ? (
                  <ul className="space-y-1">{data.anomalies.map((a) => (
                    <li key={a.period}><span className={a.kind === "spike" ? "text-red-300" : "text-amber-300"}>● {a.kind}</span> {a.period}: {fmtValue(a.value, data.fmt)} vs a typical {fmtValue(a.expected, data.fmt)} (z {a.z})</li>
                  ))}</ul>
                ) : <p>No unusual periods in the history.</p>}
                <p className="mt-2 text-[11px] text-slate-500">The shaded band is the 95% range; it widens the further ahead it looks.</p>
              </div>
              <ExplainButton title={`Forecast: ${data.label}`} facts={facts} />
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- churn

function Churn() {
  const ws = useWorkspace();
  const { data, err } = useLoad<ChurnResult>(predictChurn);
  const [band, setBand] = useState("High");
  if (err) return <p className="text-xs text-red-400">{err}</p>;
  if (!data) return <p className="text-sm text-slate-500">Training the churn model on your customers…</p>;
  const rows = data.customers.filter((c) => !band || c.band === band);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
        <Stat label="High risk" value={String(data.summary.high)} hint={`of ${data.summary.customers} live customers`} />
        <Stat label="Medium risk" value={String(data.summary.medium)} />
        <Stat label="MRR at risk" value={fmtValue(data.summary.mrr_at_risk, "money", true)} hint="high + medium risk" />
        <Stat label="Expected MRR loss" value={fmtValue(data.summary.expected_mrr_loss, "money", true)} hint="Σ MRR × churn probability" />
        <Stat label="Customer lifetime value" value={fmtValue(data.summary.total_clv, "money", true)} hint="expected revenue of the live base" />
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Card title="Customers most likely to leave" sub={<ModelNote method={data.method} metrics={data.metrics} />}
          action={<select value={band} onChange={(e) => setBand(e.target.value)} className={input}>
            <option value="High">High risk</option><option value="Medium">Medium risk</option><option value="Low">Low risk</option><option value="">Everyone</option>
          </select>}>
          <div className="max-h-[520px] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-slate-900 text-left text-slate-400">
                <tr><th className="px-2 py-2">Customer</th><th className="px-2">Churn risk</th><th className="px-2">Why</th><th className="px-2 text-right">MRR</th><th className="px-2 text-right">CLV</th><th className="px-2 text-right">Tenure</th></tr>
              </thead>
              <tbody>
                {rows.slice(0, 200).map((c) => (
                  <tr key={c.party_id} className="border-t border-slate-800/70 hover:bg-slate-800/40">
                    <td className="px-2 py-1.5"><button type="button" className="text-left font-medium text-sky-300 hover:underline" onClick={() => ws.openRecord("party" as EntityType, c.party_id)}>{c.name}</button>
                      <span className="block text-[10px] text-slate-500">{c.segment ?? ""}</span></td>
                    <td className="px-2"><ProbBar p={c.probability} /></td>
                    <td className="px-2 text-slate-300">{c.drivers.join(" · ") || "—"}</td>
                    <td className="px-2 text-right tabular-nums">{fmtValue(c.mrr, "money", true)}</td>
                    <td className="px-2 text-right tabular-nums">{fmtValue(c.clv, "money", true)}</td>
                    <td className="px-2 text-right tabular-nums text-slate-400">{c.tenure_months} mo</td>
                  </tr>
                ))}
                {!rows.length && <tr><td colSpan={6} className="px-2 py-6 text-center text-slate-500">No customers in this band.</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
        <div className="space-y-4">
          <Card title="What drives churn" sub="The conditions that raise the risk, and how much each weighs">
            <ul className="space-y-2">
              {data.importance.filter((f) => f.share > 0).slice(0, 10).map((f) => (
                <li key={f.feature} className="text-xs">
                  <div className="flex justify-between text-slate-300"><span>{f.label}</span><span className="text-slate-500">{f.share}%</span></div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-800"><div className="h-full bg-red-400" style={{ width: `${Math.min(100, f.share * 3)}%` }} /></div>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[10px] text-slate-500">Learned from your history - a pattern in your data, not necessarily the cause.</p>
          </Card>
          <Card><ExplainButton title="Churn risk" facts={[
            `${data.summary.high} live customers at high churn risk and ${data.summary.medium} at medium risk, out of ${data.summary.customers}`,
            `MRR at risk ${fmtValue(data.summary.mrr_at_risk, "money")}; expected MRR loss ${fmtValue(data.summary.expected_mrr_loss, "money")}`,
            `Top churn drivers: ${data.importance.slice(0, 4).map((f) => f.label).join(", ")}`,
            ...data.customers.slice(0, 5).map((c) => `${c.name} (${c.segment ?? "-"}, MRR ${fmtValue(c.mrr, "money")}) risk ${Math.round(100 * c.probability)}%: ${c.drivers.join(", ")}`),
          ]} /></Card>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- deals

function Win() {
  const ws = useWorkspace();
  const { data, err } = useLoad<WinResult>(predictWin);
  if (err) return <p className="text-xs text-red-400">{err}</p>;
  if (!data) return <p className="text-sm text-slate-500">Scoring the open pipeline…</p>;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Stat label="Open opportunities" value={String(data.summary.open)} />
        <Stat label="Pipeline" value={fmtValue(data.summary.pipeline, "money", true)} />
        <Stat label="Weighted pipeline" value={fmtValue(data.summary.weighted_pipeline, "money", true)} hint="Σ value × win probability" />
        <Stat label="Expected wins" value={String(data.summary.expected_wins)} hint={data.metrics.historic_win_rate !== null ? `historic win rate ${data.metrics.historic_win_rate}%` : ""} />
      </div>
      <Card title="Open deals by expected value" sub={<ModelNote method={data.method} metrics={data.metrics} />}>
        <div className="max-h-[520px] overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-900 text-left text-slate-400">
              <tr><th className="px-2 py-2">Opportunity</th><th className="px-2">Stage</th><th className="px-2">Win chance</th><th className="px-2 text-right">Value</th><th className="px-2 text-right">Expected</th><th className="px-2">Why</th></tr>
            </thead>
            <tbody>
              {data.opportunities.map((o) => (
                <tr key={o.opportunity_id} className="border-t border-slate-800/70 hover:bg-slate-800/40">
                  <td className="px-2 py-1.5"><button type="button" className="text-left font-medium text-sky-300 hover:underline" onClick={() => ws.openRecord("opportunity" as EntityType, o.opportunity_id)}>{o.name}</button>
                    <span className="block text-[10px] text-slate-500">{o.customer ?? ""} · {o.age_days} days open</span></td>
                  <td className="px-2 text-slate-300">{o.stage.replace(/_/g, " ")}</td>
                  <td className="px-2"><ProbBar p={o.probability} highIsGood /></td>
                  <td className="px-2 text-right tabular-nums">{fmtValue(o.value, "money", true)}</td>
                  <td className="px-2 text-right font-semibold tabular-nums text-emerald-300">{fmtValue(o.expected_value, "money", true)}</td>
                  <td className="px-2 text-slate-400">{o.drivers.join(" · ")}</td>
                </tr>
              ))}
              {!data.opportunities.length && <tr><td colSpan={6} className="px-2 py-6 text-center text-slate-500">No open opportunities.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- segments

const SEG_LABEL: Record<string, string> = { mrr: "MRR", tenure_months: "Tenure (mo)", services: "Services", tickets_180: "Tickets (6 mo)", usage_30: "Usage (30 d)", outstanding: "Outstanding" };

function Segments() {
  const ws = useWorkspace();
  const { data, err } = useLoad<SegmentsResult>(predictSegments);
  if (err) return <p className="text-xs text-red-400">{err}</p>;
  if (!data) return <p className="text-sm text-slate-500">Clustering customers…</p>;
  if (!data.segments.length) return <Card><p className="text-sm text-slate-400">{data.note}</p></Card>;
  return (
    <div className="space-y-3">
      <p className="text-[11px] text-slate-500">{data.method} · {data.customers} live customers · silhouette {data.silhouette}</p>
      <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {data.segments.map((s) => (
          <Card key={s.id} title={s.name} sub={`${s.size} customers (${s.share}%) · MRR ${fmtValue(s.mrr, "money", true)}`}>
            <div className="grid grid-cols-3 gap-2 text-[11px]">
              {Object.entries(s.averages).map(([k, v]) => (
                <div key={k} className="rounded-lg bg-slate-950/50 px-2 py-1.5">
                  <p className="text-slate-500">{SEG_LABEL[k] ?? k}</p>
                  <p className={`font-semibold ${s.profile[k] > 0.5 ? "text-sky-300" : s.profile[k] < -0.5 ? "text-slate-500" : "text-slate-200"}`}>
                    {fmtValue(v, ["mrr", "usage_30", "outstanding"].includes(k) ? "money" : undefined, true)}
                  </p>
                </div>
              ))}
            </div>
            <p className="mt-3 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-2 text-xs text-emerald-100">▶ {s.action}</p>
            <details className="mt-2 text-xs">
              <summary className="cursor-pointer text-slate-400">Customers</summary>
              <ul className="mt-1 max-h-40 space-y-0.5 overflow-auto">
                {s.members.map((m) => (
                  <li key={m.party_id} className="flex justify-between">
                    <button type="button" className="text-sky-300 hover:underline" onClick={() => ws.openRecord("party" as EntityType, m.party_id)}>{m.name}</button>
                    <span className="text-slate-500">{fmtValue(m.mrr, "money", true)}</span>
                  </li>
                ))}
              </ul>
            </details>
          </Card>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- next best offer

function Offers() {
  const ws = useWorkspace();
  const { data, err } = useLoad<OfferResult>(predictNextOffer);
  if (err) return <p className="text-xs text-red-400">{err}</p>;
  if (!data) return <p className="text-sm text-slate-500">Mining what customers buy together…</p>;
  if (data.note) return <Card><p className="text-sm text-slate-400">{data.note}</p></Card>;
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
      <Card title="Recommended next product per customer" sub={data.method}>
        <div className="max-h-[520px] overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-900 text-left text-slate-400"><tr><th className="px-2 py-2">Customer</th><th className="px-2">Has</th><th className="px-2">Offer</th><th className="px-2">Why</th></tr></thead>
            <tbody>
              {data.recommendations.map((r) => (
                <tr key={r.party_id} className="border-t border-slate-800/70">
                  <td className="px-2 py-1.5"><button type="button" className="text-sky-300 hover:underline" onClick={() => ws.openRecord("party" as EntityType, r.party_id)}>{r.name}</button></td>
                  <td className="px-2 text-slate-400">{r.owns.join(", ")}</td>
                  <td className="px-2 font-semibold text-emerald-300">{r.offer}</td>
                  <td className="px-2 text-slate-400">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Products bought together" sub="If a customer has A, how often they also have B">
        <ul className="space-y-2 text-xs">
          {data.rules.map((r, i) => (
            <li key={i} className="rounded-lg bg-slate-950/50 p-2">
              <span className="text-slate-300">{r.if}</span> <span className="text-slate-500">→</span> <span className="font-semibold text-emerald-300">{r.then}</span>
              <span className="block text-[11px] text-slate-500">confidence {Math.round(100 * r.confidence)}% · lift {r.lift} · {r.customers} customers</span>
            </li>
          ))}
          {!data.rules.length && <li className="text-slate-500">No strong pairs yet - recommendations use segment popularity.</li>}
        </ul>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- usage alerts

function Usage() {
  const ws = useWorkspace();
  const { data, err, reload } = useLoad<UsageAlerts>(predictUsageAlerts);
  if (err) return <p className="text-xs text-red-400">{err}</p>;
  if (!data) return <p className="text-sm text-slate-500">Checking every customer's usage against their baseline…</p>;
  return (
    <Card title="Usage spikes this week" sub={`${data.method} · ${data.customers_checked} customers checked`} action={<button type="button" className={btn} onClick={reload}>Re-check</button>}>
      <table className="w-full text-xs">
        <thead className="text-left text-slate-400"><tr><th className="px-2 py-2">Customer</th><th className="px-2">Risk</th><th className="px-2 text-right">Daily now</th><th className="px-2 text-right">Usual</th><th className="px-2 text-right">Increase</th><th className="px-2">Signals</th></tr></thead>
        <tbody>
          {data.alerts.map((a) => (
            <tr key={a.party_id} className="border-t border-slate-800/70">
              <td className="px-2 py-1.5"><button type="button" className="text-sky-300 hover:underline" onClick={() => ws.openRecord("party" as EntityType, a.party_id)}>{a.name}</button></td>
              <td className="px-2"><span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${a.risk === "Possible fraud" ? "bg-red-500/20 text-red-200" : "bg-amber-500/20 text-amber-200"}`}>{a.risk}</span></td>
              <td className="px-2 text-right tabular-nums">{fmtValue(a.recent_daily, "money")}</td>
              <td className="px-2 text-right tabular-nums text-slate-400">{fmtValue(a.baseline_daily, "money")}</td>
              <td className="px-2 text-right tabular-nums text-red-300">{a.increase_pct !== null ? `+${a.increase_pct}%` : "new"}</td>
              <td className="px-2 text-slate-400">{a.reasons.join(" · ")}</td>
            </tr>
          ))}
          {!data.alerts.length && <tr><td colSpan={6} className="px-2 py-6 text-center text-slate-500">No unusual usage this week.</td></tr>}
        </tbody>
      </table>
    </Card>
  );
}
