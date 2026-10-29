/**
 * Explore: build any report by dragging fields onto shelves -
 * dimensions (with date grains and number bins), values (aggregations and
 * table calculations), filters - pick a chart, drill into any mark, save it,
 * export it, or just ask in plain English.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { askAnalytics, exportCsv, runQuery, saveReport } from "../../api/analytics";
import type { ACatalog, AEntity, AField, ChartType, Dim, Drill, Filter, Meas, QueryResult, ReportSpec, SavedReport } from "../../api/analytics";
import { PALETTES } from "../../components/analytics/format";
import ReportView, { autoChart } from "../../components/analytics/ReportView";
import { Card, ExplainButton, NarrativeList, RecordsDrawer, btn, btnPrimary, input } from "./ui";

const KIND_ICON: Record<string, string> = { dimension: "Abc", number: "#", date: "📅", bool: "✓" };
const KIND_TINT: Record<string, string> = { dimension: "text-sky-300", number: "text-emerald-300", date: "text-violet-300", bool: "text-amber-300" };
const AGG_LABEL: Record<string, string> = { count: "Count", count_distinct: "Count distinct", sum: "Sum", avg: "Average", min: "Min", max: "Max",
  median: "Median", p90: "90th percentile", stddev: "Std deviation" };
const CALC_LABEL: Record<string, string> = { "": "No calculation", pct_total: "% of total", running: "Running total", diff: "Change vs previous",
  pct_change: "% change vs previous", moving_avg: "3-period moving avg", rank: "Rank" };
const CHARTS: { key: ChartType; label: string; icon: string }[] = [
  { key: "bar", label: "Bars", icon: "▮▮" }, { key: "hbar", label: "Horizontal", icon: "☰" }, { key: "stacked", label: "Stacked", icon: "▥" },
  { key: "stacked100", label: "100% stacked", icon: "▤" }, { key: "line", label: "Line", icon: "⟋" }, { key: "area", label: "Area", icon: "◿" },
  { key: "combo", label: "Bar + line", icon: "▮⟋" }, { key: "donut", label: "Donut", icon: "◎" }, { key: "pie", label: "Pie", icon: "◔" },
  { key: "treemap", label: "Treemap", icon: "▦" }, { key: "heatmap", label: "Heatmap", icon: "▩" }, { key: "scatter", label: "Scatter", icon: "⁘" },
  { key: "funnel", label: "Funnel", icon: "⏷" }, { key: "pivot", label: "Pivot", icon: "⊞" }, { key: "table", label: "Table", icon: "≣" },
  { key: "kpi", label: "KPI", icon: "42" },
];
const OPS: Record<string, [string, string][]> = {
  dimension: [["in", "is any of"], ["not_in", "is none of"], ["eq", "equals"], ["neq", "not equal"], ["contains", "contains"], ["starts", "starts with"],
    ["is_blank", "is blank"], ["not_blank", "is not blank"]],
  number: [["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"], ["eq", "="], ["neq", "≠"], ["between", "between"], ["is_blank", "is blank"], ["not_blank", "not blank"]],
  date: [["last_n_days", "in the last N days"], ["this_month", "this month"], ["last_month", "last month"], ["this_quarter", "this quarter"],
    ["this_year", "this year"], ["last_year", "last year"], ["between", "between"], ["before", "before"], ["after", "after"], ["is_blank", "is blank"]],
  bool: [["is_true", "is yes"], ["is_false", "is no"]],
};
const NO_VALUE = new Set(["is_blank", "not_blank", "is_true", "is_false", "this_month", "last_month", "this_quarter", "this_year", "last_year", "today"]);

export const DEFAULT_SPEC: ReportSpec = {
  entity: "order", dimensions: [{ field: "created_at", grain: "month" }], measures: [{ agg: "count" }, { field: "monthly_price", agg: "sum" }],
  filters: [{ field: "created_at", op: "last_n_days", value: 365 }], chart: "combo", title: "Orders booked per month",
};

function facts(result: QueryResult | null): string[] {
  return (result?.narrative ?? []).map((n) => n.text);
}

export default function Explorer({ catalog, open, onSaved }: { catalog: ACatalog; open?: SavedReport | null; onSaved?: (r: SavedReport) => void }) {
  const [spec, setSpec] = useState<ReportSpec>(open?.spec?.entity ? (open.spec as ReportSpec) : DEFAULT_SPEC);
  const [saved, setSaved] = useState<SavedReport | null>(open ?? null);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [search, setSearch] = useState("");
  const [question, setQuestion] = useState("");
  const [understood, setUnderstood] = useState<{ notes: string[]; by: string } | null>(null);
  const [drill, setDrill] = useState<Drill[] | null>(null);
  const [showTable, setShowTable] = useState(false);
  const [msg, setMsg] = useState("");
  const skipRun = useRef(false);

  useEffect(() => {
    if (open?.spec?.entity) {
      setSpec(open.spec as ReportSpec);
      setSaved(open);
    }
  }, [open]);

  const entity: AEntity | undefined = catalog.entities.find((e) => e.key === spec.entity);
  const fields = useMemo(() => new Map((entity?.fields ?? []).map((f) => [f.key, f])), [entity]);
  const fieldOf = (k?: string) => (k ? fields.get(k) : undefined);

  // run on every change (debounced) - the report is always live
  useEffect(() => {
    if (skipRun.current) {
      skipRun.current = false;
      return;
    }
    const t = setTimeout(() => {
      setBusy(true);
      setErr("");
      runQuery(spec).then(setResult).catch((e) => setErr(cleanErr(e))).finally(() => setBusy(false));
    }, 350);
    return () => clearTimeout(t);
  }, [spec]);

  const set = (patch: Partial<ReportSpec>) => setSpec((s) => ({ ...s, ...patch }));
  const addDim = (f: AField) => {
    if (spec.dimensions.some((d) => d.field === f.key && !d.grain)) return;
    set({ dimensions: [...spec.dimensions, f.kind === "date" ? { field: f.key, grain: "month" } : { field: f.key }].slice(-3) });
  };
  const addMeasure = (f: AField | null) =>
    set({ measures: [...spec.measures, f ? { field: f.key, agg: f.kind === "number" ? "sum" : "count_distinct" } : { agg: "count" }] });
  const addFilter = (f: AField) =>
    set({ filters: [...spec.filters, { field: f.key, op: f.kind === "date" ? "last_n_days" : f.kind === "number" ? "gt" : f.kind === "bool" ? "is_true" : f.values?.length ? "in" : "contains",
      value: f.kind === "date" ? 90 : f.values?.length ? [] : "" }] });
  const add = (f: AField) => (f.kind === "number" ? addMeasure(f) : addDim(f));

  const changeEntity = (key: string) => {
    const e = catalog.entities.find((x) => x.key === key);
    setSaved(null);
    setSpec({ entity: key, dimensions: e ? [{ field: e.date, grain: "month" }] : [], measures: [{ agg: "count" }], filters: [], chart: undefined,
      title: e ? `${e.label} per month` : "" });
  };

  const onDrop = (shelf: "dims" | "measures" | "filters") => (ev: React.DragEvent) => {
    ev.preventDefault();
    const f = fields.get(ev.dataTransfer.getData("text/field"));
    if (!f) return;
    if (shelf === "dims") addDim(f);
    else if (shelf === "measures") addMeasure(f);
    else addFilter(f);
  };

  const ask = () => {
    if (question.trim().length < 3) return;
    setBusy(true);
    setErr("");
    askAnalytics(question.trim())
      .then((r) => {
        skipRun.current = true;
        setSaved(null);
        setSpec({ ...r.spec, dimensions: r.spec.dimensions ?? [], measures: r.spec.measures ?? [{ agg: "count" }], filters: r.spec.filters ?? [] });
        setResult(r.result);
        setUnderstood({ notes: r.understood, by: r.by });
      })
      .catch((e) => setErr(cleanErr(e)))
      .finally(() => setBusy(false));
  };

  const doSave = (asNew: boolean) => {
    const name = (spec.title || "").trim() || window.prompt("Name this report") || "";
    if (!name) return;
    saveReport({ id: asNew ? undefined : saved?.can_edit ? saved.id : undefined, kind: "report", name, spec: { ...spec, title: name } })
      .then((r) => {
        setSaved(r);
        setMsg(`Saved “${r.name}”`);
        onSaved?.(r);
        setTimeout(() => setMsg(""), 2500);
      })
      .catch((e) => setErr(cleanErr(e)));
  };

  const groups = useMemo(() => {
    const q = search.trim().toLowerCase();
    const out = new Map<string, AField[]>();
    for (const f of entity?.fields ?? []) {
      if (q && !f.label.toLowerCase().includes(q) && !f.key.toLowerCase().includes(q)) continue;
      out.set(f.group, [...(out.get(f.group) ?? []), f]);
    }
    return [...out.entries()];
  }, [entity, search]);

  const chart = spec.chart ?? (result ? autoChart(result) : "bar");
  const measureKeys = result?.columns.filter((c) => c.role === "measure") ?? [];

  return (
    <div className="grid gap-4 xl:grid-cols-[290px_minmax(0,1fr)]">
      {/* ------------ the data pane ------------ */}
      <aside className="min-w-0 rounded-2xl border border-slate-800 bg-slate-900/50 p-3 xl:sticky xl:top-20 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto">
        <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Data</label>
        <select value={spec.entity} onChange={(e) => changeEntity(e.target.value)} className={`${input} mt-1 w-full text-sm`}>
          {catalog.entities.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
        </select>
        {entity && <p className="mt-1 text-[11px] text-slate-500">{entity.description}</p>}
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={`Search ${entity?.fields.length ?? 0} fields…`} className={`${input} mt-3 w-full`} />
        <button type="button" className="mt-2 w-full rounded-md border border-dashed border-slate-700 px-2 py-1 text-left text-xs text-emerald-300 hover:border-emerald-500"
          onClick={() => addMeasure(null)}># Number of records</button>
        <div className="mt-2 space-y-3">
          {groups.map(([g, fs]) => (
            <div key={g}>
              <p className="px-1 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">{g}</p>
              <ul className="mt-1 space-y-0.5">
                {fs.map((f) => (
                  <li key={f.key} draggable onDragStart={(e) => e.dataTransfer.setData("text/field", f.key)}
                    className="group flex cursor-grab items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-slate-300 hover:bg-slate-800/70">
                    <span className={`w-6 shrink-0 text-center text-[10px] font-bold ${KIND_TINT[f.kind]}`}>{KIND_ICON[f.kind]}</span>
                    <button type="button" onClick={() => add(f)} className="min-w-0 flex-1 truncate text-left" title={`${f.label} - click to add, or drag onto a shelf`}>
                      {f.label}
                      {f.custom && <span className="ml-1 text-[9px] text-fuchsia-300">custom</span>}
                      {f.computed && <span className="ml-1 text-[9px] text-amber-300">calc</span>}
                    </button>
                    <span className="hidden shrink-0 gap-1 group-hover:flex">
                      {f.kind !== "number" && <button type="button" title="Add as a dimension" onClick={() => addDim(f)} className="text-sky-400 hover:text-white">⊞</button>}
                      <button type="button" title="Add as a value" onClick={() => addMeasure(f)} className="text-emerald-400 hover:text-white">Σ</button>
                      <button type="button" title="Filter by it" onClick={() => addFilter(f)} className="text-amber-400 hover:text-white">⧩</button>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </aside>

      <div className="min-w-0 space-y-4">
        {/* ------------ ask ------------ */}
        <div className="rounded-2xl border border-sky-500/30 bg-gradient-to-r from-sky-950/40 to-violet-950/30 p-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()}
              placeholder="Ask in plain English - “MRR by segment per month for B2B”, “top 10 customers by outstanding invoices”, “tickets by category and severity as a heatmap”"
              className={`${input} flex-1 py-2 text-sm`} />
            <button type="button" className={btnPrimary} onClick={ask} disabled={busy}>✨ Ask</button>
          </div>
          {understood && (
            <p className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
              {understood.notes.map((n, i) => <span key={i} className="rounded-full bg-slate-800 px-2 py-0.5 text-slate-300">{n}</span>)}
              <span className="text-slate-500">· read by {understood.by}</span>
            </p>
          )}
        </div>

        {/* ------------ shelves ------------ */}
        <div className="grid gap-2 lg:grid-cols-3">
          <Shelf title="Rows / columns" hint="Drop dimensions - the 2nd splits the 1st (pivot)" onDrop={onDrop("dims")}>
            {spec.dimensions.map((d, i) => (
              <DimChip key={`${d.field}-${i}`} d={d} f={fieldOf(d.field)} grains={catalog.grains}
                onChange={(nd) => set({ dimensions: spec.dimensions.map((x, j) => (j === i ? nd : x)) })}
                onRemove={() => set({ dimensions: spec.dimensions.filter((_, j) => j !== i) })}
                onSwap={i > 0 ? () => set({ dimensions: [spec.dimensions[i], ...spec.dimensions.filter((_, j) => j !== i)] }) : undefined} />
            ))}
          </Shelf>
          <Shelf title="Values" hint="Drop numbers - sum, average, count…" onDrop={onDrop("measures")}>
            {spec.measures.map((m, i) => (
              <MeasureChip key={i} m={m} f={fieldOf(m.field)} aggs={catalog.aggregations}
                onChange={(nm) => set({ measures: spec.measures.map((x, j) => (j === i ? nm : x)) })}
                onRemove={() => set({ measures: spec.measures.filter((_, j) => j !== i) })} />
            ))}
          </Shelf>
          <Shelf title="Filters" hint="Drop any field to filter" onDrop={onDrop("filters")}>
            {spec.filters.map((fl, i) => (
              <FilterChip key={i} fl={fl} f={fieldOf(fl.field)}
                onChange={(nf) => set({ filters: spec.filters.map((x, j) => (j === i ? nf : x)) })}
                onRemove={() => set({ filters: spec.filters.filter((_, j) => j !== i) })} />
            ))}
          </Shelf>
        </div>

        {/* ------------ chart options ------------ */}
        <div className="flex flex-wrap items-center gap-1.5 rounded-2xl border border-slate-800 bg-slate-900/40 p-2">
          {CHARTS.map((c) => (
            <button key={c.key} type="button" title={c.label} onClick={() => set({ chart: c.key })}
              className={`flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-semibold ${chart === c.key ? "bg-sky-500/20 text-sky-200 ring-1 ring-sky-500/50" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"}`}>
              <span className="font-mono text-[10px]">{c.icon}</span>
              <span className="hidden 2xl:inline">{c.label}</span>
            </button>
          ))}
          <span className="mx-1 h-5 w-px bg-slate-700" />
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            Top
            <input type="number" min={0} value={spec.top?.n ?? ""} placeholder="all" className={`${input} w-14 py-1`}
              onChange={(e) => set({ top: e.target.value ? { n: Number(e.target.value), other: spec.top?.other ?? false } : null })} />
          </label>
          {spec.top?.n ? (
            <label className="flex items-center gap-1 text-[11px] text-slate-400">
              <input type="checkbox" checked={!!spec.top.other} onChange={(e) => set({ top: { ...spec.top!, other: e.target.checked } })} /> group the rest as Other
            </label>
          ) : null}
          <select className={`${input} py-1`} value={spec.sort?.[0] ? `${spec.sort[0].key}|${spec.sort[0].dir}` : ""}
            onChange={(e) => set({ sort: e.target.value ? [{ key: e.target.value.split("|")[0], dir: e.target.value.split("|")[1] as "asc" | "desc" }] : [] })}>
            <option value="">Auto sort</option>
            {measureKeys.map((m) => <option key={`${m.key}d`} value={`${m.key}|desc`}>{m.label} ↓</option>)}
            {measureKeys.map((m) => <option key={`${m.key}a`} value={`${m.key}|asc`}>{m.label} ↑</option>)}
            {result?.columns.filter((c) => c.role === "dimension").map((d) => <option key={d.key} value={`${d.key}|asc`}>{d.label} A→Z</option>)}
          </select>
          <select className={`${input} py-1`} value={spec.palette ?? "brt"} onChange={(e) => set({ palette: e.target.value })}>
            {Object.keys(PALETTES).map((p) => <option key={p} value={p}>{p} colours</option>)}
          </select>
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            <input type="checkbox" checked={!!spec.options?.labels} onChange={(e) => set({ options: { ...spec.options, labels: e.target.checked } })} /> data labels
          </label>
        </div>

        {/* ------------ the report ------------ */}
        <Card
          title={<input value={spec.title ?? ""} onChange={(e) => setSpec((s) => ({ ...s, title: e.target.value }))} placeholder="Untitled report"
            className="w-full bg-transparent text-base font-semibold text-white outline-none placeholder:text-slate-600" />}
          sub={result ? `${result.entity_label} · ${result.scanned.toLocaleString()} records · ${result.groups.toLocaleString()} groups${busy ? " · updating…" : ""}` : busy ? "Running…" : ""}
          action={
            <div className="flex flex-wrap justify-end gap-1.5">
              {msg && <span className="self-center text-xs text-emerald-300">{msg}</span>}
              <button type="button" className={btnPrimary} onClick={() => doSave(false)}>{saved?.can_edit ? "Save" : "Save report"}</button>
              {saved && <button type="button" className={btn} onClick={() => doSave(true)}>Save as new</button>}
              <button type="button" className={btn} onClick={() => setShowTable((v) => !v)}>{showTable ? "Hide data" : "Show data"}</button>
              <button type="button" className={btn} onClick={() => exportCsv("query", spec, spec.title || "report").catch((e) => setErr(cleanErr(e)))}>⬇ CSV</button>
              <button type="button" className={btn} onClick={() => setDrill([])}>Records</button>
            </div>
          }
        >
          {err && <p className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">{err}</p>}
          {result && <ReportView spec={{ ...spec, chart }} result={result} onDrill={setDrill} />}
          {result?.truncated && <p className="mt-2 text-[11px] text-amber-300">Only the newest 200,000 records were read - add a date filter for the full picture.</p>}
          {result && showTable && chart !== "table" && <div className="mt-4"><ReportView spec={{ chart: "table" }} result={result} onDrill={setDrill} compact /></div>}
          {result && (
            <div className="mt-4 grid gap-4 border-t border-slate-800 pt-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <div>
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">What it shows</p>
                <NarrativeList items={result.narrative} />
                <p className="mt-2 text-[11px] text-slate-500">Click any bar, slice, point or cell to see the records behind it.</p>
              </div>
              <ExplainButton title={spec.title || "Report"} facts={facts(result)} table={result.rows.slice(0, 25)} />
            </div>
          )}
        </Card>
      </div>
      {drill && (
        <RecordsDrawer entity={spec.entity} filters={spec.filters} drill={drill} title={spec.title || entity?.label || "Report"} onClose={() => setDrill(null)} />
      )}
    </div>
  );
}

function cleanErr(e: unknown): string {
  const s = String((e as Error)?.message ?? e);
  const m = s.match(/"detail"\s*:\s*"([^"]+)"/);
  return m ? m[1] : s;
}

function Shelf({ title, hint, onDrop, children }: { title: string; hint: string; onDrop: (e: React.DragEvent) => void; children: React.ReactNode }) {
  const [over, setOver] = useState(false);
  return (
    <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={(e) => { setOver(false); onDrop(e); }}
      className={`min-h-[72px] rounded-2xl border p-2 transition ${over ? "border-sky-400 bg-sky-500/10" : "border-slate-800 bg-slate-900/40"}`}>
      <p className="px-1 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">{title}</p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">{children}</div>
      {!Array.isArray(children) || !(children as unknown[]).length ? <p className="px-1 text-[11px] text-slate-600">{hint}</p> : null}
    </div>
  );
}

const chip = "flex items-center gap-1 rounded-lg border px-1.5 py-1 text-[11px]";
const mini = "rounded bg-slate-950 px-1 py-0.5 text-[11px] text-slate-200 outline-none";

function DimChip({ d, f, grains, onChange, onRemove, onSwap }: { d: Dim; f?: AField; grains: string[]; onChange: (d: Dim) => void; onRemove: () => void; onSwap?: () => void }) {
  return (
    <span className={`${chip} border-sky-500/40 bg-sky-500/10 text-sky-100`}>
      {onSwap && <button type="button" title="Make it the first" onClick={onSwap} className="text-sky-400">⇤</button>}
      <span className="max-w-[150px] truncate">{f?.label ?? d.field}</span>
      {f?.kind === "date" && (
        <select value={d.grain ?? "month"} onChange={(e) => onChange({ ...d, grain: e.target.value })} className={mini}>
          {grains.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      )}
      {f?.kind === "number" && (
        <select value={d.bins ?? ""} onChange={(e) => onChange({ ...d, bins: e.target.value ? Number(e.target.value) : undefined })} className={mini}>
          <option value="">exact</option>
          {[5, 10, 20].map((b) => <option key={b} value={b}>{b} bins</option>)}
        </select>
      )}
      <button type="button" onClick={onRemove} className="text-sky-300 hover:text-white">×</button>
    </span>
  );
}

function MeasureChip({ m, f, aggs, onChange, onRemove }: { m: Meas; f?: AField; aggs: string[]; onChange: (m: Meas) => void; onRemove: () => void }) {
  const allowed = !m.field ? ["count"] : f?.kind === "number" || f?.kind === "bool" ? aggs : ["count", "count_distinct"];
  return (
    <span className={`${chip} border-emerald-500/40 bg-emerald-500/10 text-emerald-100`}>
      <select value={m.agg} onChange={(e) => onChange({ ...m, agg: e.target.value })} className={`${mini} max-w-[92px]`}>
        {allowed.map((a) => <option key={a} value={a}>{AGG_LABEL[a] ?? a}</option>)}
      </select>
      <span className="max-w-[140px] truncate">{m.field ? f?.label ?? m.field : "records"}</span>
      <select value={m.calc ?? ""} onChange={(e) => onChange({ ...m, calc: e.target.value || undefined })}
        className={`${mini} ${m.calc ? "max-w-[110px]" : "w-7"}`} title="Table calculation">
        {Object.entries(CALC_LABEL).map(([k, v]) => <option key={k} value={k}>{k ? v : "ƒ"}</option>)}
      </select>
      <button type="button" onClick={onRemove} className="text-emerald-300 hover:text-white">×</button>
    </span>
  );
}

function FilterChip({ fl, f, onChange, onRemove }: { fl: Filter; f?: AField; onChange: (f: Filter) => void; onRemove: () => void }) {
  const kind = f?.kind ?? "dimension";
  const ops = OPS[kind] ?? OPS.dimension;
  const values = Array.isArray(fl.value) ? (fl.value as string[]) : [];
  return (
    <span className={`${chip} flex-wrap border-amber-500/40 bg-amber-500/10 text-amber-100`}>
      <span className="max-w-[130px] truncate">{f?.label ?? fl.field}</span>
      <select value={fl.op} onChange={(e) => onChange({ ...fl, op: e.target.value, value: e.target.value === "between" ? ["", ""] : e.target.value === "in" || e.target.value === "not_in" ? [] : fl.value })}
        className={`${mini} max-w-[118px]`}>
        {ops.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      {!NO_VALUE.has(fl.op) && (fl.op === "in" || fl.op === "not_in") && f?.values?.length ? (
        <details className="relative">
          <summary className={`${mini} cursor-pointer list-none`}>{values.length ? `${values.length} chosen` : "choose…"}</summary>
          <div className="absolute z-20 mt-1 max-h-56 w-48 overflow-auto rounded-lg border border-slate-700 bg-slate-950 p-1.5 shadow-xl">
            {f.values.map((v) => (
              <label key={v} className="flex items-center gap-1.5 px-1 py-0.5 text-[11px] text-slate-200">
                <input type="checkbox" checked={values.includes(v)} onChange={(e) => onChange({ ...fl, value: e.target.checked ? [...values, v] : values.filter((x) => x !== v) })} />
                {v}
              </label>
            ))}
          </div>
        </details>
      ) : !NO_VALUE.has(fl.op) && fl.op === "between" ? (
        <>
          <input type={kind === "date" ? "date" : "number"} value={String((fl.value as string[])?.[0] ?? "")} className={`${mini} w-28`}
            onChange={(e) => onChange({ ...fl, value: [e.target.value, (fl.value as string[])?.[1] ?? ""] })} />
          <span>–</span>
          <input type={kind === "date" ? "date" : "number"} value={String((fl.value as string[])?.[1] ?? "")} className={`${mini} w-28`}
            onChange={(e) => onChange({ ...fl, value: [(fl.value as string[])?.[0] ?? "", e.target.value] })} />
        </>
      ) : !NO_VALUE.has(fl.op) ? (
        <input type={kind === "number" || fl.op === "last_n_days" ? "number" : kind === "date" ? "date" : "text"} value={String(fl.value ?? "")}
          className={`${mini} ${kind === "date" && fl.op !== "last_n_days" ? "w-28" : "w-16"}`}
          onChange={(e) => onChange({ ...fl, value: kind === "number" || fl.op === "last_n_days" ? Number(e.target.value) : e.target.value })} />
      ) : null}
      <button type="button" onClick={onRemove} className="text-amber-300 hover:text-white">×</button>
    </span>
  );
}
