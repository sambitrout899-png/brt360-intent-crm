/**
 * Platform health: Grafana's "BRT 360 - API" and "BRT 360 - Services" dashboards,
 * drawn here (app/analytics/platform_health.py asks Prometheus). The operator's
 * admins only - the numbers cover the whole server and every organization.
 */
import { useEffect, useState } from "react";
import { platformHealth } from "../../api/analytics";
import type { HealthKpi, PlatformHealth as Data } from "../../api/analytics";
import { LineChart } from "../../components/analytics/Charts";
import { fmtValue } from "../../components/analytics/format";
import { Card, NarrativeList, input } from "./ui";

const TONE: Record<string, { dot: string; word: string }> = {
  good: { dot: "bg-emerald-400", word: "OK" },
  warn: { dot: "bg-amber-400", word: "Watch" },
  bad: { dot: "bg-red-400", word: "Act" },
  info: { dot: "bg-slate-500", word: "" },
};

function HealthTile({ k }: { k: HealthKpi }) {
  const t = TONE[k.tone] ?? TONE.info;
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/40 px-3.5 py-3">
      <p className="flex items-center gap-1.5 truncate text-[11px] font-semibold uppercase tracking-wider text-slate-500" title={k.label}>
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${t.dot}`} />
        <span className="truncate">{k.label}</span>
        {t.word && <span className="ml-auto text-[10px] normal-case tracking-normal text-slate-400">{t.word}</span>}
      </p>
      <p className="mt-1 text-xl font-bold text-slate-50">{fmtValue(k.value, k.fmt, true)}</p>
      <p className="mt-0.5 truncate text-[11px] text-slate-500" title={k.hint}>{k.hint || " "}</p>
    </div>
  );
}

const th = "whitespace-nowrap px-2.5 py-2 text-left font-semibold text-slate-300";
const td = "whitespace-nowrap px-2.5 py-1.5 text-slate-300";
const num = "whitespace-nowrap px-2.5 py-1.5 text-right tabular-nums text-slate-100";

function Table({ head, rows, empty }: { head: string[]; rows: React.ReactNode[][]; empty: string }) {
  if (!rows.length) return <p className="text-xs text-slate-500">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead><tr>{head.map((h, i) => <th key={h} className={`${th} ${i > 0 ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-slate-800/70">{r.map((c, j) => <td key={j} className={j === 0 ? td : num}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PlatformHealth() {
  const [hours, setHours] = useState(6);
  const [data, setData] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    let live = true;
    const load = (background: boolean) => {
      setBusy(true);
      platformHealth(hours, background)
        .then((d) => live && (setData(d), setErr("")))
        .catch((e) => live && setErr(String(e.message ?? e)))
        .finally(() => live && setBusy(false));
    };
    load(false);
    const timer = window.setInterval(() => document.visibilityState === "visible" && load(true), 60_000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [hours]);

  if (err) return <p className="text-sm text-red-400">{err}</p>;
  if (!data) return <p className="text-sm text-slate-500">Asking the monitoring…</p>;
  if (!data.available) {
    return (
      <Card title="Monitoring isn't available here">
        <p className="text-xs text-slate-400">{data.reason}</p>
      </Card>
    );
  }
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs text-slate-500">
          The whole server, every organization · as of {data.generated_at.replace("T", " ")} UTC · refreshes every minute{busy ? " · refreshing…" : ""}
        </p>
        <span className="flex-1" />
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))} className={input}>
          {[[1, "Last hour"], [6, "Last 6 hours"], [24, "Last 24 hours"], [72, "Last 3 days"], [168, "Last 7 days"]].map(([h, l]) => (
            <option key={h} value={h}>{l}</option>
          ))}
        </select>
      </div>
      <Card title="Headlines">
        <NarrativeList items={data.notes.length ? data.notes : [{ tone: "good", text: "Everything is up and within its normal range." }]} />
      </Card>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">{data.kpis.map((k) => <HealthTile key={k.key} k={k} />)}</div>

      <Card title="Services" sub="Up = Prometheus reaches it now; requests over 24 hours; p95 and server errors over the last hour">
        <Table head={["Service", "Status", "Requests, 24 h", "p95", "Server errors"]} empty="No service has reported yet."
          rows={data.services.map((s) => [
            s.name,
            <span className="inline-flex items-center gap-1.5">
              <span className={`h-1.5 w-1.5 rounded-full ${s.up ? "bg-emerald-400" : s.up === false ? "bg-red-400" : "bg-slate-500"}`} />
              {s.up ? "Up" : s.up === false ? "Down" : "—"}
            </span>,
            fmtValue(s.requests_24h, null, true),
            fmtValue(s.p95_ms, "ms"),
            fmtValue(s.error_pct, "pct"),
          ])} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {data.charts.map((c) => (
          <Card key={c.key} title={c.title}>
            <LineChart labels={c.labels} series={c.series} fmt={c.fmt} height={240} />
          </Card>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Routes answering with server errors (5xx), last hour">
          <Table head={["Route", "Status", "Count"]} empty="None - no request failed with a server error in the last hour."
            rows={data.failing_routes.map((r) => [`${r.method} ${r.route}`, r.status, fmtValue(r.count)])} />
        </Card>
        <Card title="Busiest routes in core, last 24 hours" sub="What a further split of core would move">
          <Table head={["Route", "Requests"]} empty="No traffic recorded yet." rows={data.busiest_routes.map((r) => [r.route, fmtValue(r.count)])} />
        </Card>
        <Card title="AI tokens per organization, last 24 hours">
          <Table head={["Organization", "Tokens"]} empty="No AI calls in the last 24 hours." rows={data.ai_tokens.map((r) => [r.tenant, fmtValue(r.tokens)])} />
        </Card>
      </div>
    </div>
  );
}
