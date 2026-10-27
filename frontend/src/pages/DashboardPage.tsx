import { useCallback, useEffect, useState } from "react";
import { dashboardInsights, type DashKpi, type DashboardInsights } from "../api/client";
import type { EntityType } from "../api/types";
import { useIntentRefresh, useWorkspace } from "../workspace";

const money = (n: number) => (Math.abs(n) >= 1e7 ? `₹${(n / 1e7).toFixed(2)} Cr` : Math.abs(n) >= 1e5 ? `₹${(n / 1e5).toFixed(2)} L` : `₹${Math.round(n).toLocaleString()}`);
const short = (n: number) => (n >= 1e5 ? money(n) : Math.round(n).toLocaleString());
const PALETTE = ["#38bdf8", "#a78bfa", "#34d399", "#fbbf24", "#f472b6", "#60a5fa", "#fb923c", "#94a3b8"];
const TONE_DOT: Record<string, string> = { good: "bg-emerald-400", bad: "bg-red-400", warn: "bg-amber-400", info: "bg-sky-400" };
const SEV: Record<string, string> = { critical: "bg-red-500", high: "bg-amber-500", medium: "bg-sky-500", low: "bg-slate-500" };

function value(k: DashKpi) {
  if (k.value === null || k.value === undefined) return "—";
  return k.format === "money" ? money(k.value) : k.format === "pct" ? `${k.value}%` : k.format === "days" ? `${k.value} d` : k.value.toLocaleString();
}

function Delta({ k }: { k: DashKpi }) {
  if (k.delta === null) return null;
  const good = k.lower_is_better ? k.delta <= 0 : k.delta >= 0;
  return (
    <span className={`rounded-full px-1.5 py-0.5 text-[11px] font-semibold ${good ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>
      {k.delta >= 0 ? "↑" : "↓"} {Math.abs(k.delta)}
      {k.delta_points ? " pts" : "%"}
    </span>
  );
}

function Sparkline({ values }: { values: number[] }) {
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${28 - (v / max) * 24}`).join(" ");
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="mt-3 h-8 w-full">
      <polyline points={`0,30 ${pts} 100,30`} fill="url(#spark)" stroke="none" />
      <polyline points={pts} fill="none" stroke="#38bdf8" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <defs>
        <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#38bdf8" stopOpacity="0.25" />
          <stop offset="1" stopColor="#38bdf8" stopOpacity="0" />
        </linearGradient>
      </defs>
    </svg>
  );
}

/** The headline number: bigger, with its trend. */
function HeroKpi({ k, spark }: { k: DashKpi; spark: number[] }) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-gradient-to-br from-slate-900 to-sky-950/40 p-5 sm:col-span-2 lg:col-span-1 lg:row-span-2">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">{k.label}</p>
      <div className="mt-2 flex flex-wrap items-baseline gap-2">
        <span className="text-3xl font-bold tracking-tight text-white sm:text-4xl">{value(k)}</span>
        <Delta k={k} />
      </div>
      <p className="mt-1 text-xs text-slate-500">{k.hint}</p>
      {spark.length > 1 && <Sparkline values={spark} />}
    </div>
  );
}

function Kpi({ k }: { k: DashKpi }) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-3.5">
      <p className="truncate text-[11px] font-semibold uppercase tracking-wider text-slate-500">{k.label}</p>
      <div className="mt-1 flex flex-wrap items-baseline gap-1.5">
        <span className="text-xl font-bold text-slate-100">{value(k)}</span>
        <Delta k={k} />
      </div>
      <p className="mt-0.5 truncate text-[11px] text-slate-500" title={k.hint}>
        {k.hint}
      </p>
    </div>
  );
}

function Card({ title, sub, action, children, className = "" }: { title: string; sub?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-2xl border border-slate-800 bg-slate-900/50 p-4 sm:p-5 ${className}`}>
      <div className="mb-4 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
          {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h2 className="px-1 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-500">{title}</h2>
      {children}
    </div>
  );
}

function MrrChart({ data }: { data: DashboardInsights["mrr_trend"] }) {
  const max = Math.max(...data.map((d) => d.mrr), 1);
  const last = data[data.length - 1];
  return (
    <div>
      <div className="flex h-40 items-end gap-1.5 sm:h-48 sm:gap-3">
        {data.map((d, i) => (
          <div key={d.month} className="group flex h-full flex-1 flex-col items-center justify-end gap-1" title={`${d.month}: ${money(d.mrr)}${d.new ? ` · new ${money(d.new)}` : ""}`}>
            <span className={`text-[10px] text-slate-400 ${i === data.length - 1 ? "" : "hidden sm:group-hover:block"}`}>{short(d.mrr)}</span>
            <div className="relative w-full overflow-hidden rounded-t-md bg-slate-800" style={{ height: `${Math.max(3, (d.mrr / max) * 85)}%` }}>
              <div className={`absolute inset-0 bg-gradient-to-t ${i === data.length - 1 ? "from-sky-500 to-sky-300" : "from-sky-700 to-sky-500"}`} />
              {d.new > 0 && <div className="absolute inset-x-0 top-0 bg-emerald-400" style={{ height: `${(d.new / Math.max(d.mrr, 1)) * 100}%` }} />}
            </div>
            <span className={`text-[10px] text-slate-500 ${i % 2 && data.length > 6 ? "hidden sm:block" : ""}`}>{d.month.split(" ")[0]}</span>
          </div>
        ))}
      </div>
      {last && (
        <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
          <span>
            <span className="text-sky-400">■</span> recurring revenue
          </span>
          <span>
            <span className="text-emerald-400">■</span> new that month
          </span>
        </p>
      )}
    </div>
  );
}

function Funnel({ data }: { data: DashboardInsights["funnel"] }) {
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="space-y-2.5">
      {data.map((d, i) => (
        <div key={d.label}>
          <div className="mb-1 flex items-center gap-2 text-xs">
            <span className="min-w-0 flex-1 truncate text-slate-300">{d.label}</span>
            <span className="font-semibold text-slate-100">{d.count}</span>
            {d.conversion !== null && (
              <span className={`w-12 text-right text-[11px] ${d.conversion < 50 ? "text-amber-400" : "text-emerald-400"}`}>{d.conversion}%</span>
            )}
          </div>
          <div className="h-2 rounded-full bg-slate-800/80">
            <div className="h-2 rounded-full" style={{ width: `${Math.max(3, (d.count / max) * 100)}%`, background: PALETTE[i % PALETTE.length] }} />
          </div>
        </div>
      ))}
      {data.length === 0 && <p className="text-xs text-slate-500">No leads yet.</p>}
    </div>
  );
}

function Donut({ data, empty }: { data: { name: string; mrr: number; share: number }[]; empty: string }) {
  const r = 40;
  const c = 2 * Math.PI * r;
  const offsets = data.map((_, i) => data.slice(0, i).reduce((sum, d) => sum + (d.share / 100) * c, 0));
  if (data.length === 0) return <p className="text-xs text-slate-500">{empty}</p>;
  const top = data.slice(0, 5);
  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-center">
      <div className="relative h-28 w-28 shrink-0">
        <svg viewBox="0 0 100 100" className="h-28 w-28 -rotate-90">
          <circle cx="50" cy="50" r={r} fill="none" stroke="#1e293b" strokeWidth="14" />
          {data.map((d, i) => {
            const len = (d.share / 100) * c;
            return <circle key={d.name} cx="50" cy="50" r={r} fill="none" stroke={PALETTE[i % PALETTE.length]} strokeWidth="14" strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offsets[i]} />;
          })}
        </svg>
        <span className="absolute inset-0 flex flex-col items-center justify-center text-center">
          <span className="text-sm font-bold text-slate-100">{data[0].share}%</span>
          <span className="max-w-[70px] truncate text-[9px] text-slate-500">{data[0].name}</span>
        </span>
      </div>
      <div className="w-full min-w-0 space-y-1.5 text-xs">
        {top.map((d, i) => (
          <p key={d.name} className="flex items-center gap-2">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: PALETTE[i % PALETTE.length] }} />
            <span className="min-w-0 flex-1 truncate text-slate-300">{d.name}</span>
            <span className="shrink-0 text-slate-400">{money(d.mrr)}</span>
          </p>
        ))}
        {data.length > top.length && <p className="pl-4 text-[11px] text-slate-500">+ {data.length - top.length} more</p>}
      </div>
    </div>
  );
}

function Activity({ data }: { data: DashboardInsights["activity"] }) {
  const max = Math.max(...data.map((d) => d.executed + d.failed + d.review), 1);
  return (
    <div>
      <div className="flex h-24 items-end gap-px sm:gap-0.5">
        {data.map((d) => (
          <div key={d.date} className="flex h-full flex-1 flex-col justify-end" title={`${d.date}: ${d.executed} done, ${d.review} for review, ${d.failed} failed`}>
            {d.failed > 0 && <div className="bg-red-500/80" style={{ height: `${(d.failed / max) * 100}%` }} />}
            {d.review > 0 && <div className="bg-amber-400/80" style={{ height: `${(d.review / max) * 100}%` }} />}
            <div className="rounded-t-sm bg-sky-500/80" style={{ height: `${Math.max(1, (d.executed / max) * 100)}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap justify-between gap-2 text-[10px] text-slate-500">
        <span>{data[0]?.date.slice(5)}</span>
        <span className="flex gap-3">
          <span>
            <span className="text-sky-400">■</span> done
          </span>
          <span>
            <span className="text-amber-400">■</span> review
          </span>
          <span>
            <span className="text-red-400">■</span> failed
          </span>
        </span>
        <span>{data[data.length - 1]?.date.slice(5)}</span>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const ws = useWorkspace();
  const [days, setDays] = useState(30);
  const [d, setD] = useState<DashboardInsights | null>(null);
  const [error, setError] = useState("");
  const [allInsights, setAllInsights] = useState(false);

  const load = useCallback(() => {
    dashboardInsights(days)
      .then((r) => (setD(r), setError("")))
      .catch(() => setError("Could not load the dashboard."));
  }, [days]);
  useEffect(load, [load]);
  useIntentRefresh(load);

  if (error) return <p className="text-sm text-red-400">{error}</p>;
  if (!d)
    return (
      <div className="page space-y-4">
        <div className="h-10 w-48 animate-pulse rounded-lg bg-slate-800/60" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-2xl bg-slate-900/60" />
          ))}
        </div>
        <div className="h-64 animate-pulse rounded-2xl bg-slate-900/60" />
      </div>
    );

  const spark = d.mrr_trend.map((m) => m.mrr);
  const stageMax = Math.max(...d.pipeline_by_stage.map((s) => s.count), 1);
  const scopeLabel = { own: "your records", team: "your team's records", department: "your department", department_tree: "your departments", org: "the whole organization" }[d.scope] ?? d.scope;
  const open = (entity: string, id: number) => {
    if (["party", "lead", "opportunity", "quote", "order"].includes(entity)) ws.openRecord(entity as EntityType, id);
  };
  const hero = d.kpis.find((k) => k.key === "mrr") ?? d.kpis[0];
  const rest = d.kpis.filter((k) => k !== hero);
  const insights = allInsights ? d.insights : d.insights.slice(0, 3);
  const attentionTotal = d.attention.reduce((n, a) => n + a.count, 0);
  const openTickets = Object.values(d.tickets_by_severity).reduce((n, x) => n + x, 0);

  return (
    <div className="page space-y-6 sm:space-y-8">
      {/* ---------------- header */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold text-slate-100">Dashboard</h1>
          <p className="mt-1 text-xs text-slate-400 sm:text-sm">
            {scopeLabel} · updated {new Date(d.as_of).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </p>
        </div>
        <div className="flex rounded-lg border border-slate-700 bg-slate-900/60 p-0.5">
          {[7, 30, 90].map((n) => (
            <button key={n} type="button" onClick={() => setDays(n)} className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${days === n ? "bg-sky-600 text-white" : "text-slate-400 hover:text-slate-200"}`}>
              {n}d
            </button>
          ))}
        </div>
      </div>

      {/* ---------------- the numbers, then what they mean */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {hero && <HeroKpi k={hero} spark={spark} />}
        <div className="grid grid-cols-2 gap-3 sm:col-span-2 lg:col-span-3 lg:grid-cols-3">
          {rest.map((k) => (
            <Kpi key={k.key} k={k} />
          ))}
        </div>
      </div>

      {d.insights.length > 0 && (
        <section className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4 sm:p-5">
          <div className="mb-2 flex items-center gap-2">
            <h3 className="flex-1 text-sm font-semibold text-slate-100">✨ What stands out</h3>
            {d.insights.length > 3 && (
              <button type="button" onClick={() => setAllInsights(!allInsights)} className="text-xs text-sky-400 hover:text-sky-300">
                {allInsights ? "Show less" : `All ${d.insights.length}`}
              </button>
            )}
          </div>
          <ul className="divide-y divide-slate-800/80">
            {insights.map((i) => (
              <li key={i.text} className="flex items-start gap-3 py-2.5 text-sm leading-relaxed text-slate-300">
                <span className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${TONE_DOT[i.tone] ?? "bg-slate-500"}`} />
                <span>{i.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ---------------- revenue */}
      <Group title="Revenue">
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Monthly recurring revenue" sub="Live services by the month they started" className="lg:col-span-2">
            <MrrChart data={d.mrr_trend} />
          </Card>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1">
            <Card title="By segment">
              <Donut data={d.revenue_by_segment} empty="No live services yet." />
            </Card>
            <Card title="Top products">
              <Donut data={d.revenue_by_product} empty="No live services yet." />
            </Card>
          </div>
        </div>
      </Group>

      {/* ---------------- sales */}
      <Group title="Sales">
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Lead to cash" sub="How many reach each step · % from the step before">
            <Funnel data={d.funnel} />
          </Card>
          <Card title="Pipeline by stage" sub="Opportunities and quoted monthly value">
            <div className="space-y-2.5">
              {d.pipeline_by_stage.map((s) => (
                <div key={s.stage} className="text-xs">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-slate-300">{s.label}</span>
                    <span className="font-semibold text-slate-100">{s.count}</span>
                    <span className="w-20 text-right text-slate-500">{money(s.value)}</span>
                  </div>
                  <div className="h-2 rounded-full bg-slate-800/80">
                    <div className={`h-2 rounded-full ${s.stage === "lost" || s.stage === "feasibility_failed" ? "bg-red-400" : s.stage === "won" ? "bg-emerald-400" : "bg-violet-400"}`} style={{ width: `${Math.max(3, (s.count / stageMax) * 100)}%` }} />
                  </div>
                </div>
              ))}
              {d.pipeline_by_stage.length === 0 && <p className="text-xs text-slate-500">No opportunities yet.</p>}
            </div>
          </Card>
        </div>
      </Group>

      {/* ---------------- operations */}
      <Group title="Operations">
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Needs attention" sub={attentionTotal ? `${attentionTotal} waiting too long` : "Waiting too long"} className="lg:col-span-2">
            {d.attention.length === 0 && <p className="text-sm text-emerald-400">✓ Nothing is stuck.</p>}
            <div className="divide-y divide-slate-800">
              {d.attention.map((a) => (
                <div key={a.title} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex items-center gap-2 text-sm">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${SEV[a.severity]}`} />
                    <span className="min-w-0 flex-1 truncate font-medium text-slate-200">{a.title}</span>
                    <span className="rounded-full bg-slate-800 px-2 text-xs text-slate-300">{a.count}</span>
                    {a.intent && ws.canRun(a.intent) && (
                      <button type="button" onClick={() => ws.runIntent(a.intent as string)} className="shrink-0 rounded-md bg-sky-600/20 px-2 py-0.5 text-xs font-semibold text-sky-300 hover:bg-sky-600/30">
                        Act
                      </button>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5 pl-4">
                    {a.records.slice(0, 6).map((r) => (
                      <button
                        key={`${r.id}-${r.label}`}
                        type="button"
                        onClick={() => (a.entity_type === "product" ? ws.openProductModeller?.(r.label) : open(a.entity_type, r.id))}
                        className="max-w-full truncate rounded-md border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:border-sky-500"
                      >
                        {r.label}
                        {r.age_days !== null && <span className="text-slate-500"> · {Math.round(r.age_days)}d</span>}
                      </button>
                    ))}
                    {a.records.length > 6 && <span className="py-0.5 text-[11px] text-slate-500">+ {a.records.length - 6}</span>}
                  </div>
                </div>
              ))}
            </div>
          </Card>
          <Card title="Service health" sub={`${openTickets} open ticket${openTickets === 1 ? "" : "s"} · ${d.open_work_orders} field job${d.open_work_orders === 1 ? "" : "s"}`}>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(d.tickets_by_severity).map(([sev, n]) => (
                <div key={sev} className="rounded-xl bg-slate-950/60 px-3 py-2.5">
                  <p className={`text-xl font-bold ${sev === "critical" && n ? "text-red-400" : sev === "high" && n ? "text-amber-400" : "text-slate-200"}`}>{n}</p>
                  <p className="flex items-center gap-1.5 text-[11px] capitalize text-slate-500">
                    <span className={`h-1.5 w-1.5 rounded-full ${SEV[sev] ?? "bg-slate-500"}`} />
                    {sev}
                  </p>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </Group>

      {/* ---------------- people */}
      <Group title="Team & usage">
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Team" sub="Live revenue they brought in">
            <div className="space-y-2">
              {d.leaderboard.slice(0, 6).map((r, i) => (
                <div key={r.user} className="flex items-center gap-3 text-sm">
                  <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${i === 0 ? "bg-amber-400/20 text-amber-300" : "bg-slate-800 text-slate-400"}`}>{i + 1}</span>
                  <span className="min-w-0 flex-1 truncate text-slate-200">{r.user}</span>
                  <span className="text-right">
                    <span className="block text-xs font-semibold text-slate-200">{money(r.mrr)}</span>
                    <span className="block text-[10px] text-slate-500">{r.intents} actions</span>
                  </span>
                </div>
              ))}
              {d.leaderboard.length === 0 && <p className="text-xs text-slate-500">Nobody on the board yet.</p>}
            </div>
          </Card>
          <Card title="What people asked the CRM to do" sub={`Last ${Math.min(d.period_days, 30)} days`} className="lg:col-span-2">
            <Activity data={d.activity} />
            {d.top_intents.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {d.top_intents.filter((t) => ws.canRun(t.code)).slice(0, 8).map((t) => (
                  <button key={t.code} type="button" onClick={() => ws.runIntent(t.code)} className="rounded-full border border-slate-700 px-2.5 py-1 text-[11px] text-slate-300 hover:border-sky-500" title="Run it">
                    {t.label} <span className="text-slate-500">{t.count}</span>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>
      </Group>
    </div>
  );
}
