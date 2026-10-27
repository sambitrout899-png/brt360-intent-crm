/**
 * Analytics & Reports - Explore (build any report, drill to the records),
 * Dashboards, the telecom scorecard and Predictions (forecasts and machine
 * learning). Backend: app/analytics, /api/analytics.
 */
import { useEffect, useState } from "react";
import { analyticsCatalog, listReports } from "../api/analytics";
import type { ACatalog, SavedReport } from "../api/analytics";
import Dashboards from "./analytics/Dashboards";
import Explorer from "./analytics/Explorer";
import Predictions from "./analytics/Predictions";
import Scorecard from "./analytics/Scorecard";
import { input } from "./analytics/ui";

const TABS = [
  ["scorecard", "Telecom KPIs", "The operator scorecard"],
  ["explore", "Explore", "Slice and dice any data"],
  ["dashboards", "Dashboards", "Your saved views"],
  ["predict", "Predictions", "Forecasts and machine learning"],
] as const;
type Tab = (typeof TABS)[number][0];
const TAB_KEY = "brt360.analytics.tab";

export default function AnalyticsPage() {
  const [tab, setTab] = useState<Tab>(() => {
    try {
      return (localStorage.getItem(TAB_KEY) as Tab) || "scorecard";
    } catch {
      return "scorecard";
    }
  });
  const [catalog, setCatalog] = useState<ACatalog | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<SavedReport | null>(null);
  const [reports, setReports] = useState<SavedReport[]>([]);

  useEffect(() => {
    analyticsCatalog().then(setCatalog).catch((e) => setErr(String(e.message ?? e)));
    listReports("report").then(setReports).catch(() => setReports([]));
  }, []);
  const go = (t: Tab) => {
    setTab(t);
    try {
      localStorage.setItem(TAB_KEY, t);
    } catch {
      /* private window - the tab is remembered for this visit only */
    }
  };

  return (
    <div className="page space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">Analytics & Reports</h1>
          <p className="mt-1 text-sm text-slate-400">Slice any record down to the last field, chart it your way, and see what's coming next.</p>
        </div>
        {tab === "explore" && reports.length > 0 && (
          <select value={open?.id ?? ""} className={`${input} py-2`} onChange={(e) => setOpen(reports.find((r) => r.id === Number(e.target.value)) ?? null)}>
            <option value="">Open a saved report…</option>
            {reports.map((r) => <option key={r.id} value={r.id}>{r.name}{r.mine ? "" : " (shared)"}</option>)}
          </select>
        )}
      </div>
      <div className="flex flex-wrap gap-1 border-b border-slate-800">
        {TABS.map(([k, label, hint]) => (
          <button key={k} type="button" onClick={() => go(k)} title={hint}
            className={`-mb-px border-b-2 px-4 py-2.5 text-sm font-semibold transition ${tab === k ? "border-sky-400 text-sky-200" : "border-transparent text-slate-400 hover:text-slate-100"}`}>
            {label}
          </button>
        ))}
      </div>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {!catalog && !err && <p className="text-sm text-slate-500">Loading the reporting model…</p>}
      {catalog && tab === "explore" && (
        <Explorer catalog={catalog} open={open} onSaved={(r) => setReports((rs) => [r, ...rs.filter((x) => x.id !== r.id)])} />
      )}
      {catalog && tab === "dashboards" && <Dashboards catalog={catalog} onEdit={(r) => { setOpen(r); go("explore"); }} />}
      {tab === "scorecard" && <Scorecard />}
      {catalog && tab === "predict" && <Predictions catalog={catalog} />}
    </div>
  );
}
