/**
 * A report drawn: the query result as the chart its spec asks for (or the
 * one that fits), a pivot crosstab, a sortable table or KPI tiles. A click
 * on any mark or cell reports the drill (the dimension values under it).
 */
import { useMemo, useState } from "react";
import type { ACol, ChartType, Drill, QueryResult, ReportSpec } from "../../api/analytics";
import { BarChart, DonutChart, Funnel, Heatmap, LineChart, Scatter, Treemap } from "./Charts";
import type { Series } from "./Charts";
import { fmtValue } from "./format";

export function autoChart(result: QueryResult): ChartType {
  const dims = result.columns.filter((c) => c.role === "dimension");
  const ms = result.columns.filter((c) => c.role === "measure");
  if (!dims.length) return "kpi";
  if (dims.length === 1 && ms.length === 2 && !dims[0].grain) return "bar";
  if (dims[0].grain && !["weekday", "hour"].includes(dims[0].grain)) return dims.length === 2 ? "line" : ms.length > 1 ? "combo" : "area";
  if (dims.length === 2) return "stacked";
  return result.rows.length > 12 ? "hbar" : "bar";
}

const label = (v: unknown) => (v === null || v === undefined ? "(blank)" : String(v));

export default function ReportView({ spec, result, onDrill, height = 300, compact = false }: {
  spec: Partial<ReportSpec>; result: QueryResult; onDrill?: (drill: Drill[]) => void; height?: number; compact?: boolean;
}) {
  const chart = (spec.chart as ChartType) || autoChart(result);
  const dims = result.columns.filter((c) => c.role === "dimension");
  const ms = result.columns.filter((c) => c.role === "measure");
  const d0 = dims[0], d1 = dims[1], m0 = ms[0];
  const pal = spec.palette;
  const drill = (a: string, b?: string) => {
    if (!onDrill || !d0) return;
    const out: Drill[] = [{ field: d0.field!, grain: d0.grain, value: a, edges: d0.edges }];
    if (d1 && b !== undefined) out.push({ field: d1.field!, grain: d1.grain, value: b, edges: d1.edges });
    onDrill(out);
  };

  const shaped = useMemo(() => {
    if (!d0) return null;
    if (d1 && m0) {
      const rowsL: string[] = [], colsL: string[] = [];
      for (const r of result.rows) {
        const a = label(r[d0.key]), b = label(r[d1.key]);
        if (!rowsL.includes(a)) rowsL.push(a);
        if (!colsL.includes(b)) colsL.push(b);
      }
      const cell = new Map(result.rows.map((r) => [`${label(r[d0.key])}\u0000${label(r[d1.key])}`, r[m0.key] as number | null]));
      const matrix = rowsL.map((a) => colsL.map((b) => cell.get(`${a}\u0000${b}`) ?? null));
      const series: Series[] = colsL.map((b, j) => ({ name: b, values: rowsL.map((_, i) => matrix[i][j]) }));
      return { labels: rowsL, series, matrix, cols: colsL };
    }
    const labels = result.rows.map((r) => label(r[d0.key]));
    const series: Series[] = ms.map((m, i) => ({ name: m.label, values: result.rows.map((r) => (r[m.key] as number | null) ?? null),
      kind: chart === "combo" && i > 0 ? "line" : "bar" }));
    return { labels, series, matrix: null, cols: [] as string[] };
  }, [result, d0, d1, m0, ms, chart]);

  if (!result.rows.length) return <p className="py-10 text-center text-sm text-slate-500">No records match - loosen the filters.</p>;

  if (chart === "kpi" || !d0 || !shaped) {
    return (
      <div className={`grid gap-3 ${ms.length > 1 ? "sm:grid-cols-2 lg:grid-cols-3" : ""}`}>
        {ms.map((m) => (
          <div key={m.key} className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
            <p className="truncate text-[11px] font-semibold uppercase tracking-wider text-slate-500">{m.label}</p>
            <p className={`mt-1 font-bold text-white ${compact ? "text-2xl" : "text-3xl"}`}>
              {fmtValue(d0 ? result.totals[m.key] : (result.rows[0]?.[m.key] as number), m.fmt)}
            </p>
            <p className="mt-1 text-[11px] text-slate-500">{result.scanned.toLocaleString()} records</p>
          </div>
        ))}
      </div>
    );
  }
  const fmt = m0?.fmt;
  switch (chart) {
    case "line":
    case "area":
      return <LineChart labels={shaped.labels} series={shaped.series} fmt={fmt} area={chart === "area"}
        height={height} paletteName={pal} onSelect={(l) => drill(l)} />;
    case "donut":
    case "pie":
      return <DonutChart labels={shaped.labels} values={shaped.series[0]?.values ?? []} fmt={fmt} pie={chart === "pie"} paletteName={pal}
        size={compact ? 180 : 220} onSelect={(l) => drill(l)} />;
    case "heatmap":
      return shaped.matrix ? <Heatmap rows={shaped.labels} cols={shaped.cols} matrix={shaped.matrix} fmt={fmt} onSelect={(a, b) => drill(a, b)} />
        : <BarChart labels={shaped.labels} series={shaped.series} fmt={fmt} height={height} paletteName={pal} onSelect={(l) => drill(l)} />;
    case "scatter":
      if (ms.length >= 2) {
        return <Scatter points={result.rows.map((r) => ({ x: Number(r[ms[0].key] ?? 0), y: Number(r[ms[1].key] ?? 0), label: label(r[d0.key]) }))}
          xLabel={ms[0].label} yLabel={ms[1].label} xFmt={ms[0].fmt} yFmt={ms[1].fmt} onSelect={(l) => drill(l)} />;
      }
      return <p className="text-sm text-slate-500">A scatter plot needs two values (e.g. MRR and tickets per customer).</p>;
    case "funnel":
      return <Funnel labels={shaped.labels} values={shaped.series[0]?.values ?? []} fmt={fmt} paletteName={pal} onSelect={(l) => drill(l)} />;
    case "treemap":
      return <Treemap labels={shaped.labels} values={shaped.series[0]?.values ?? []} fmt={fmt} paletteName={pal} height={height} onSelect={(l) => drill(l)} />;
    case "table":
      return <DataTable result={result} onDrill={onDrill} compact={compact} />;
    case "pivot":
      return shaped.matrix ? <Pivot rows={shaped.labels} cols={shaped.cols} matrix={shaped.matrix} m={m0} d0={d0} d1={d1} onSelect={(a, b) => drill(a, b)} />
        : <DataTable result={result} onDrill={onDrill} compact={compact} />;
    default:
      return <BarChart labels={shaped.labels} series={shaped.series} fmt={fmt} height={height} paletteName={pal} showLabels={spec.options?.labels}
        horizontal={chart === "hbar"} mode={chart === "stacked" ? "stacked" : chart === "stacked100" ? "stacked100" : "grouped"}
        fmt2={ms[1]?.fmt} onSelect={(l, s) => drill(l, d1 ? s : undefined)} />;
  }
}

function Pivot({ rows, cols, matrix, m, d0, d1, onSelect }: {
  rows: string[]; cols: string[]; matrix: (number | null)[][]; m: ACol; d0: ACol; d1: ACol; onSelect: (a: string, b: string) => void;
}) {
  const additive = !m.calc && ["count", "sum", undefined].includes(m.agg);
  const max = Math.max(1e-9, ...matrix.flat().map((v) => v ?? 0));
  const rowT = matrix.map((r) => r.reduce<number>((a, v) => a + (v ?? 0), 0));
  const colT = cols.map((_, j) => matrix.reduce((a, r) => a + (r[j] ?? 0), 0));
  return (
    <div className="max-h-[480px] overflow-auto rounded-lg border border-slate-800">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-900">
          <tr>
            <th className="px-2 py-2 text-left font-semibold text-slate-400">{d0.label} ↓ / {d1.label} →</th>
            {cols.map((c) => <th key={c} className="px-2 py-2 text-right font-semibold text-slate-300">{c}</th>)}
            {additive && <th className="px-2 py-2 text-right font-semibold text-sky-300">Total</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r} className="border-t border-slate-800/70">
              <td className="px-2 py-1.5 font-medium text-slate-300">{r}</td>
              {cols.map((c, j) => (
                <td key={c} onClick={() => onSelect(r, c)} className="cursor-pointer px-2 py-1.5 text-right tabular-nums text-slate-100 hover:outline hover:outline-1 hover:outline-sky-500"
                  style={{ background: matrix[i][j] ? `rgba(56,189,248,${0.06 + (0.4 * (matrix[i][j] ?? 0)) / max})` : undefined }}>
                  {fmtValue(matrix[i][j], m.fmt, true)}
                </td>
              ))}
              {additive && <td className="px-2 py-1.5 text-right font-semibold tabular-nums text-sky-200">{fmtValue(rowT[i], m.fmt, true)}</td>}
            </tr>
          ))}
          {additive && (
            <tr className="border-t-2 border-slate-700 bg-slate-900/60">
              <td className="px-2 py-1.5 font-semibold text-sky-300">Total</td>
              {colT.map((v, j) => <td key={j} className="px-2 py-1.5 text-right font-semibold tabular-nums text-sky-200">{fmtValue(v, m.fmt, true)}</td>)}
              <td className="px-2 py-1.5 text-right font-bold tabular-nums text-white">{fmtValue(rowT.reduce((a, v) => a + v, 0), m.fmt, true)}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function DataTable({ result, onDrill, compact }: { result: QueryResult; onDrill?: (d: Drill[]) => void; compact?: boolean }) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const rows = useMemo(() => {
    if (!sort) return result.rows;
    return [...result.rows].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      if (typeof x === "number" && typeof y === "number") return (x - y) * sort.dir;
      return String(x ?? "").localeCompare(String(y ?? ""), undefined, { numeric: true }) * sort.dir;
    });
  }, [result.rows, sort]);
  const dims = result.columns.filter((c) => c.role === "dimension");
  return (
    <div className={`overflow-auto rounded-lg border border-slate-800 ${compact ? "max-h-72" : "max-h-[480px]"}`}>
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-900">
          <tr>
            {result.columns.map((c) => (
              <th key={c.key} onClick={() => setSort((s) => ({ key: c.key, dir: s?.key === c.key && s.dir === -1 ? 1 : -1 }))}
                className={`cursor-pointer select-none px-2.5 py-2 font-semibold text-slate-300 hover:text-white ${c.role === "measure" ? "text-right" : "text-left"}`}>
                {c.label} {sort?.key === c.key ? (sort.dir === -1 ? "↓" : "↑") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={`border-t border-slate-800/70 ${onDrill && dims.length ? "cursor-pointer hover:bg-slate-800/50" : ""}`}
              onClick={() => onDrill && dims.length && onDrill(dims.map((d) => ({ field: d.field!, grain: d.grain, value: label(r[d.key]), edges: d.edges })))}>
              {result.columns.map((c) => (
                <td key={c.key} className={`px-2.5 py-1.5 ${c.role === "measure" ? "text-right tabular-nums text-slate-100" : "text-slate-300"}`}>
                  {c.role === "measure" ? fmtValue(r[c.key] as number, c.fmt) : label(r[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {dims.length > 0 && (
          <tfoot className="sticky bottom-0 bg-slate-900">
            <tr className="border-t-2 border-slate-700">
              {result.columns.map((c, i) => (
                <td key={c.key} className={`px-2.5 py-1.5 font-semibold ${c.role === "measure" ? "text-right tabular-nums text-sky-200" : "text-sky-300"}`}>
                  {c.role === "measure" ? (c.calc ? "" : fmtValue(result.totals[c.key], c.fmt)) : i === 0 ? "Total" : ""}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
