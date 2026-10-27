/**
 * Dashboards: saved reports laid out on a grid. A dashboard-wide period
 * narrows every tile to the same window; every tile drills to its records
 * and opens in Explore for editing.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { deleteReport, listReports, runQuery, saveReport, starterDashboard } from "../../api/analytics";
import type { ACatalog, Drill, Filter, QueryResult, ReportSpec, SavedReport } from "../../api/analytics";
import ReportView from "../../components/analytics/ReportView";
import { Card, RecordsDrawer, btn, btnPrimary, input } from "./ui";

type Tile = { report_id: number; w: number };
const PERIODS: [string, string][] = [["", "All time"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["180", "Last 6 months"], ["365", "Last 12 months"]];

export default function Dashboards({ catalog, onEdit }: { catalog: ACatalog; onEdit: (r: SavedReport) => void }) {
  const [dashboards, setDashboards] = useState<SavedReport[]>([]);
  const [reports, setReports] = useState<SavedReport[]>([]);
  const [current, setCurrent] = useState<SavedReport | null>(null);
  const [editing, setEditing] = useState(false);
  const [period, setPeriod] = useState("");
  const [err, setErr] = useState("");
  const [loaded, setLoaded] = useState(false);

  const load = useCallback((pick?: number) => {
    Promise.all([listReports("dashboard"), listReports("report")])
      .then(([d, r]) => {
        setDashboards(d);
        setReports(r);
        setCurrent((c) => d.find((x) => x.id === (pick ?? c?.id)) ?? d[0] ?? null);
        setLoaded(true);
      })
      .catch((e) => setErr(String(e.message ?? e)));
  }, []);
  useEffect(() => load(), [load]);

  const tiles: Tile[] = (current?.spec.tiles as Tile[] | undefined) ?? [];
  const cols = current?.spec.columns ?? 3;
  const setTiles = (next: Tile[]) => current && setCurrent({ ...current, spec: { ...current.spec, tiles: next } });

  const save = () => {
    if (!current) return;
    saveReport({ id: current.can_edit ? current.id : undefined, kind: "dashboard", name: current.name, description: current.description, spec: current.spec })
      .then((d) => {
        setEditing(false);
        load(d.id);
      })
      .catch((e) => setErr(String(e.message ?? e)));
  };
  const create = () => {
    const name = window.prompt("Name the new dashboard");
    if (!name) return;
    saveReport({ kind: "dashboard", name, spec: { tiles: [], columns: 3 } as unknown as ReportSpec }).then((d) => {
      load(d.id);
      setEditing(true);
    });
  };

  if (!loaded) return err ? <p className="text-sm text-red-400">{err}</p> : <p className="text-sm text-slate-500">Loading dashboards…</p>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select value={current?.id ?? ""} onChange={(e) => setCurrent(dashboards.find((d) => d.id === Number(e.target.value)) ?? null)} className={`${input} min-w-[240px] py-2 text-sm`}>
          {!dashboards.length && <option value="">No dashboards yet</option>}
          {dashboards.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        <select value={period} onChange={(e) => setPeriod(e.target.value)} className={`${input} py-2`} title="Applies to every tile, on each report's main date">
          {PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <span className="flex-1" />
        {current?.can_edit && !editing && <button type="button" className={btn} onClick={() => setEditing(true)}>✎ Edit layout</button>}
        {editing && <button type="button" className={btnPrimary} onClick={save}>Save layout</button>}
        {editing && <button type="button" className={btn} onClick={() => { setEditing(false); load(); }}>Cancel</button>}
        <button type="button" className={btn} onClick={create}>+ New dashboard</button>
        <button type="button" className={btn} onClick={() => starterDashboard().then((d) => load(d.id)).catch((e) => setErr(String(e.message ?? e)))}>
          ✨ Telecom starter dashboard
        </button>
        {current?.can_edit && (
          <button type="button" className={`${btn} hover:border-red-500 hover:text-red-300`} onClick={() => {
            if (window.confirm(`Delete the dashboard “${current.name}”? Its reports stay.`)) deleteReport(current.id).then(() => { setCurrent(null); load(); });
          }}>Delete</button>
        )}
      </div>
      {err && <p className="text-xs text-red-400">{err}</p>}
      {!current && (
        <Card>
          <p className="text-sm text-slate-300">No dashboards yet. Start from the telecom executive dashboard - revenue, pipeline, products, assurance, ageing and usage - and change it as you like, or build reports in Explore and lay them out on a new dashboard.</p>
          <button type="button" className={`${btnPrimary} mt-3`} onClick={() => starterDashboard().then((d) => load(d.id))}>✨ Create the telecom starter dashboard</button>
        </Card>
      )}
      {current && editing && (
        <Card title="Add a report" sub="Saved reports from Explore">
          <div className="flex flex-wrap gap-2">
            {reports.filter((r) => !tiles.some((t) => t.report_id === r.id)).map((r) => (
              <button key={r.id} type="button" className={btn} onClick={() => setTiles([...tiles, { report_id: r.id, w: 1 }])}>+ {r.name}</button>
            ))}
            {!reports.length && <p className="text-xs text-slate-500">Save a report in Explore first.</p>}
          </div>
          <label className="mt-3 flex items-center gap-2 text-xs text-slate-400">
            Columns
            <select value={cols} onChange={(e) => setCurrent({ ...current, spec: { ...current.spec, columns: Number(e.target.value) } })} className={input}>
              {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </Card>
      )}
      {current && (
        <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(min(100%, ${cols >= 3 ? 340 : 420}px), 1fr))` }}>
          {tiles.map((t, i) => {
            const r = reports.find((x) => x.id === t.report_id);
            return (
              <div key={`${t.report_id}-${i}`} className="min-w-0" style={{ gridColumn: `span ${Math.min(t.w, cols)} / span ${Math.min(t.w, cols)}` }}>
                {r ? (
                  <TileView report={r} period={period} catalog={catalog} onEdit={() => onEdit(r)}
                    tools={editing ? (
                      <span className="flex gap-1">
                        <button type="button" className={btn} title="Move left" disabled={i === 0} onClick={() => { const n = [...tiles]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; setTiles(n); }}>←</button>
                        <button type="button" className={btn} title="Move right" disabled={i === tiles.length - 1} onClick={() => { const n = [...tiles]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; setTiles(n); }}>→</button>
                        <button type="button" className={btn} title="Width" onClick={() => setTiles(tiles.map((x, j) => (j === i ? { ...x, w: (x.w % 3) + 1 } : x)))}>↔ {t.w}</button>
                        <button type="button" className={btn} title="Remove" onClick={() => setTiles(tiles.filter((_, j) => j !== i))}>×</button>
                      </span>
                    ) : null} />
                ) : (
                  <Card><p className="text-xs text-slate-500">This report was deleted or isn't shared with you.</p></Card>
                )}
              </div>
            );
          })}
          {!tiles.length && <Card><p className="text-sm text-slate-400">This dashboard is empty - choose “Edit layout” and add saved reports.</p></Card>}
        </div>
      )}
    </div>
  );
}

function TileView({ report, period, catalog, onEdit, tools }: { report: SavedReport; period: string; catalog: ACatalog; onEdit: () => void; tools: React.ReactNode }) {
  const [result, setResult] = useState<QueryResult | null>(null);
  const [err, setErr] = useState("");
  const [drill, setDrill] = useState<Drill[] | null>(null);
  const spec = report.spec as ReportSpec;
  const filters: Filter[] = useMemo(() => {
    const date = catalog.entities.find((e) => e.key === spec.entity)?.date;
    const base = (spec.filters ?? []).filter((f) => !(period && date && f.field === date && ["last_n_days", "this_month", "this_year", "between"].includes(f.op)));
    return period && date ? [...base, { field: date, op: "last_n_days", value: Number(period) }] : base;
  }, [spec, period, catalog]);
  useEffect(() => {
    let live = true;
    setErr("");
    runQuery({ ...spec, dimensions: spec.dimensions ?? [], measures: spec.measures ?? [], filters })
      .then((r) => live && setResult(r))
      .catch((e) => live && setErr(String(e.message ?? e).slice(0, 200)));
    return () => {
      live = false;
    };
  }, [spec, filters]);
  return (
    <Card title={report.name} sub={result ? `${result.entity_label} · ${result.scanned.toLocaleString()} records` : "Loading…"}
      action={tools ?? <button type="button" className={btn} onClick={onEdit}>Open in Explore</button>}>
      {err && <p className="text-xs text-red-400">{err}</p>}
      {result && <ReportView spec={spec} result={result} onDrill={setDrill} height={240} compact />}
      {drill && <RecordsDrawer entity={spec.entity} filters={filters} drill={drill} title={report.name} onClose={() => setDrill(null)} />}
    </Card>
  );
}
