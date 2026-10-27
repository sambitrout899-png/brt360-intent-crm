/**
 * Analytics charts - hand-drawn SVG, no chart library. Every chart is
 * responsive (measured width), has hover tooltips, and reports a click on a
 * mark (onSelect) so the page can drill down to the records behind it.
 *
 * Shape: categories (labels) x series ({name, values}), except Heatmap
 * (rows x columns matrix) and Scatter (points).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { Fmt } from "../../api/analytics";
import { fmtValue, palette, ticks, truncate } from "./format";

export interface Series {
  name: string;
  values: (number | null)[];
  kind?: "bar" | "line";
  dashed?: boolean;
}
export interface Band {
  lower: (number | null)[];
  upper: (number | null)[];
}
type Select = (label: string, series?: string) => void;

const AXIS = "#64748b";
const GRID = "#1e293b";

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function Tip({ tip }: { tip: { x: number; y: number; lines: string[] } | null }) {
  if (!tip) return null;
  return (
    <div
      className="pointer-events-none absolute z-10 max-w-[260px] rounded-lg border border-slate-700 bg-slate-950/95 px-2.5 py-1.5 text-[11px] text-slate-200 shadow-xl"
      style={{ left: tip.x, top: tip.y, transform: "translate(-50%, calc(-100% - 10px))" }}
    >
      {tip.lines.map((l, i) => (
        <div key={i} className={i === 0 ? "font-semibold text-white" : ""}>
          {l}
        </div>
      ))}
    </div>
  );
}

export function Legend({ names, colors, hidden, onToggle }: { names: string[]; colors: string[]; hidden?: Set<string>; onToggle?: (n: string) => void }) {
  if (names.length < 2) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
      {names.map((n, i) => (
        <button key={n} type="button" onClick={() => onToggle?.(n)} className={`flex items-center gap-1.5 ${hidden?.has(n) ? "opacity-40" : ""}`}>
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: colors[i % colors.length] }} />
          {truncate(n, 28)}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- bars (vertical, horizontal, stacked, 100%, combo)

export function BarChart({
  labels, series, fmt, height = 300, mode = "grouped", horizontal = false, paletteName, onSelect, showLabels = false, fmt2,
}: {
  labels: string[]; series: Series[]; fmt?: Fmt; height?: number; mode?: "grouped" | "stacked" | "stacked100"; horizontal?: boolean;
  paletteName?: string; onSelect?: Select; showLabels?: boolean; fmt2?: Fmt;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const colors = palette(paletteName);
  const shown = series.filter((s) => !hidden.has(s.name));
  const bars = shown.filter((s) => s.kind !== "line");
  const lines = shown.filter((s) => s.kind === "line");
  const stacked = mode !== "grouped";
  const totals = labels.map((_, i) => bars.reduce((a, s) => a + Math.max(0, s.values[i] ?? 0), 0));
  const values = (si: number, i: number) => {
    const v = bars[si].values[i] ?? 0;
    return mode === "stacked100" ? (totals[i] ? (100 * Math.max(0, v)) / totals[i] : 0) : v;
  };
  const maxV = mode === "stacked100" ? 100 : stacked ? Math.max(0, ...totals) : Math.max(0, ...bars.flatMap((s) => s.values.map((v) => v ?? 0)));
  const minV = stacked ? 0 : Math.min(0, ...bars.flatMap((s) => s.values.map((v) => v ?? 0)));
  const yt = ticks(minV, maxV || 1);
  const lo = yt[0], hi = yt[yt.length - 1];
  const lineMax = Math.max(1, ...lines.flatMap((s) => s.values.map((v) => v ?? 0)));
  const lt = ticks(0, lineMax);
  const longest = Math.max(...labels.map((l) => l.length), 4);
  const padL = horizontal ? Math.min(170, 12 + longest * 6.2) : 56;
  const padR = lines.length ? 56 : 16;
  // labels turn when they don't fit under their bar
  const rotate = !horizontal && (labels.length > 8 || longest * 6.2 > ((width - padL - padR) / Math.max(1, labels.length)) * 0.95);
  const padB = horizontal ? 24 : rotate ? 64 : 30;
  const h = horizontal ? Math.max(height, labels.length * 26 + 40) : height;
  const plotW = width - padL - padR, plotH = h - 12 - padB;
  const n = Math.max(1, labels.length);
  const band = (horizontal ? plotH : plotW) / n;
  const inner = band * 0.72;
  const bw = stacked ? inner : inner / Math.max(1, bars.length);
  const scale = (v: number) => ((v - lo) / (hi - lo || 1)) * (horizontal ? plotW : plotH);
  const fmtTick = (v: number) => (mode === "stacked100" ? `${v}%` : fmtValue(v, fmt, true));
  const every = Math.ceil(n / Math.max(1, Math.floor((horizontal ? plotH : plotW) / (horizontal ? 18 : 40))));

  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setTip(null)}>
      <svg width={width} height={h} className="block">
        {yt.map((t) => {
          const p = scale(t);
          return horizontal ? (
            <g key={t}>
              <line x1={padL + p} x2={padL + p} y1={12} y2={12 + plotH} stroke={GRID} />
              <text x={padL + p} y={h - 6} fontSize={10} fill={AXIS} textAnchor="middle">{fmtTick(t)}</text>
            </g>
          ) : (
            <g key={t}>
              <line x1={padL} x2={padL + plotW} y1={12 + plotH - p} y2={12 + plotH - p} stroke={GRID} />
              <text x={padL - 6} y={12 + plotH - p + 3} fontSize={10} fill={AXIS} textAnchor="end">{fmtTick(t)}</text>
            </g>
          );
        })}
        {lines.length > 0 && !horizontal && lt.map((t) => (
          <text key={`r${t}`} x={width - padR + 6} y={12 + plotH - (t / lt[lt.length - 1]) * plotH + 3} fontSize={10} fill={AXIS}>{fmtValue(t, fmt2, true)}</text>
        ))}
        {labels.map((label, i) => {
          let acc = 0;
          const start = i * band + (band - inner) / 2;
          return (
            <g key={`${label}-${i}`}>
              {bars.map((s, si) => {
                const v = values(si, i);
                const len = Math.abs(scale(v) - scale(stacked ? 0 : Math.max(lo, 0)));
                const base = stacked ? scale(acc) : scale(Math.max(lo, 0));
                const color = colors[series.indexOf(s) % colors.length];
                const off = stacked ? 0 : si * bw;
                const neg = v < 0;
                const rect = horizontal
                  ? { x: padL + (neg ? base - len : base), y: 12 + start + off, width: Math.max(0.5, len), height: Math.max(1, bw - 2) }
                  : { x: padL + start + off, y: 12 + plotH - (neg ? base : base + len), width: Math.max(1, bw - 2), height: Math.max(0.5, len) };
                if (stacked) acc += v;
                const raw = s.values[i];
                return (
                  <g key={s.name}>
                    <rect {...rect} rx={2} fill={color} className="cursor-pointer transition-opacity hover:opacity-80"
                      onClick={() => onSelect?.(label, bars.length > 1 ? s.name : undefined)}
                      onMouseMove={(e) => {
                        const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                        setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [label, `${s.name}: ${fmtValue(raw, fmt)}`,
                          ...(mode === "stacked100" ? [`${v.toFixed(1)}% of ${label}`] : [])] });
                      }} />
                    {showLabels && len > 14 && (
                      <text x={horizontal ? rect.x + rect.width + 4 : rect.x + rect.width / 2} y={horizontal ? rect.y + rect.height / 2 + 3 : rect.y - 3}
                        fontSize={10} fill="#cbd5e1" textAnchor={horizontal ? "start" : "middle"}>{fmtValue(raw, fmt, true)}</text>
                    )}
                  </g>
                );
              })}
              {i % every === 0 && (horizontal ? (
                <text x={padL - 6} y={12 + start + inner / 2 + 3} fontSize={11} fill="#94a3b8" textAnchor="end">{truncate(label, Math.floor(padL / 6.2))}</text>
              ) : (
                <text x={padL + start + inner / 2} y={12 + plotH + 14} fontSize={10} fill="#94a3b8" textAnchor={padB > 30 ? "end" : "middle"}
                  transform={padB > 30 ? `rotate(-35 ${padL + start + inner / 2} ${12 + plotH + 14})` : undefined}>{truncate(label, 18)}</text>
              ))}
            </g>
          );
        })}
        {!horizontal && lines.map((s) => {
          const color = colors[series.indexOf(s) % colors.length];
          const top = lt[lt.length - 1] || 1;
          const pts = s.values.map((v, i) => (v === null ? null : [padL + i * band + band / 2, 12 + plotH - ((v ?? 0) / top) * plotH] as const));
          return (
            <g key={s.name}>
              <polyline fill="none" stroke={color} strokeWidth={2.5} points={pts.filter(Boolean).map((p) => p!.join(",")).join(" ")} />
              {pts.map((p, i) => p && <circle key={i} cx={p[0]} cy={p[1]} r={3.5} fill={color} />)}
            </g>
          );
        })}
      </svg>
      <Legend names={series.map((s) => s.name)} colors={colors} hidden={hidden}
        onToggle={(nm) => setHidden((h0) => { const nx = new Set(h0); if (nx.has(nm)) nx.delete(nm); else nx.add(nm); return nx; })} />
      <Tip tip={tip} />
    </div>
  );
}

// ---------------------------------------------------------------- lines and areas (with forecast band, anomalies)

export function LineChart({
  labels, series, fmt, height = 280, area = false, band, splitAt, marks, paletteName, onSelect,
}: {
  labels: string[]; series: Series[]; fmt?: Fmt; height?: number; area?: boolean; band?: Band; splitAt?: number;
  marks?: { index: number; kind: "spike" | "drop"; text: string }[]; paletteName?: string; onSelect?: Select;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const colors = palette(paletteName);
  const shown = series.filter((s) => !hidden.has(s.name));
  const all = [...shown.flatMap((s) => s.values), ...(band ? [...band.lower, ...band.upper] : [])].filter((v): v is number => v !== null && v !== undefined);
  const yt = ticks(Math.min(0, ...all), Math.max(1, ...all));
  const lo = yt[0], hi = yt[yt.length - 1];
  const padL = 58, padR = 14, padB = labels.length > 10 ? 52 : 28;
  const plotW = width - padL - padR, plotH = height - 12 - padB;
  const n = labels.length;
  const x = (i: number) => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => 12 + plotH - ((v - lo) / (hi - lo || 1)) * plotH;
  const every = Math.ceil(n / Math.max(1, Math.floor(plotW / 56)));
  const path = (vals: (number | null)[]) => {
    let d = "";
    vals.forEach((v, i) => {
      if (v === null || v === undefined) return;
      d += `${d ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    });
    return d;
  };
  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} className="block"
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const i = Math.round(((e.clientX - r.left - padL) / (plotW || 1)) * (n - 1));
          setHover(i >= 0 && i < n ? i : null);
        }}
        onClick={() => hover !== null && onSelect?.(labels[hover])}>
        <defs>
          {shown.map((s) => (
            <linearGradient key={s.name} id={`g-${s.name.replace(/\W/g, "")}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={colors[series.indexOf(s) % colors.length]} stopOpacity={0.35} />
              <stop offset="1" stopColor={colors[series.indexOf(s) % colors.length]} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        {yt.map((t) => (
          <g key={t}>
            <line x1={padL} x2={padL + plotW} y1={y(t)} y2={y(t)} stroke={GRID} />
            <text x={padL - 6} y={y(t) + 3} fontSize={10} fill={AXIS} textAnchor="end">{fmtValue(t, fmt, true)}</text>
          </g>
        ))}
        {splitAt !== undefined && splitAt < n && (
          <g>
            <rect x={x(splitAt)} y={12} width={Math.max(0, padL + plotW - x(splitAt))} height={plotH} fill="#0ea5e9" opacity={0.05} />
            <text x={x(splitAt) + 4} y={22} fontSize={10} fill="#7dd3fc">forecast →</text>
          </g>
        )}
        {band && (
          <path fill="#38bdf8" opacity={0.15}
            d={`${path(band.upper)} ${band.lower.map((v, i) => [v, i] as const).filter(([v]) => v !== null).reverse()
              .map(([v, i]) => `L${x(i).toFixed(1)},${y(v as number).toFixed(1)}`).join(" ")} Z`} />
        )}
        {shown.map((s) => {
          const color = colors[series.indexOf(s) % colors.length];
          const d = path(s.values);
          const firstI = s.values.findIndex((v) => v !== null && v !== undefined);
          const lastI = s.values.length - 1 - [...s.values].reverse().findIndex((v) => v !== null && v !== undefined);
          return (
            <g key={s.name}>
              {area && d && <path d={`${d} L${x(lastI)},${y(Math.max(lo, 0))} L${x(firstI)},${y(Math.max(lo, 0))} Z`} fill={`url(#g-${s.name.replace(/\W/g, "")})`} />}
              <path d={d} fill="none" stroke={color} strokeWidth={2.25} strokeDasharray={s.dashed ? "6 4" : undefined} strokeLinejoin="round" />
              {n <= 40 && s.values.map((v, i) => v !== null && v !== undefined && <circle key={i} cx={x(i)} cy={y(v)} r={hover === i ? 4.5 : 2.5} fill={color} />)}
            </g>
          );
        })}
        {marks?.map((m) => {
          const v = shown[0]?.values[m.index];
          if (v === null || v === undefined) return null;
          return (
            <g key={m.index}>
              <circle cx={x(m.index)} cy={y(v)} r={8} fill="none" stroke={m.kind === "spike" ? "#f87171" : "#fbbf24"} strokeWidth={2} />
              <title>{m.text}</title>
            </g>
          );
        })}
        {labels.map((l, i) => i % every === 0 && (
          <text key={i} x={x(i)} y={12 + plotH + 14} fontSize={10} fill="#94a3b8" textAnchor={padB > 30 ? "end" : "middle"}
            transform={padB > 30 ? `rotate(-35 ${x(i)} ${12 + plotH + 14})` : undefined}>{truncate(l, 14)}</text>
        ))}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={12} y2={12 + plotH} stroke="#475569" strokeDasharray="3 3" />}
      </svg>
      <Legend names={series.map((s) => s.name)} colors={colors} hidden={hidden}
        onToggle={(nm) => setHidden((h0) => { const nx = new Set(h0); if (nx.has(nm)) nx.delete(nm); else nx.add(nm); return nx; })} />
      {hover !== null && (
        <Tip tip={{ x: x(hover), y: 16, lines: [labels[hover], ...shown.map((s) => `${s.name}: ${fmtValue(s.values[hover], fmt)}`),
          ...(band && band.lower[hover] !== null ? [`range: ${fmtValue(band.lower[hover], fmt)} – ${fmtValue(band.upper[hover], fmt)}`] : [])] }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- donut / pie

export function DonutChart({ labels, values, fmt, paletteName, onSelect, pie = false, size = 220 }: {
  labels: string[]; values: (number | null)[]; fmt?: Fmt; paletteName?: string; onSelect?: Select; pie?: boolean; size?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const colors = palette(paletteName);
  const items = labels.map((l, i) => ({ l, v: Math.max(0, values[i] ?? 0), i })).filter((d) => d.v > 0);
  const total = items.reduce((a, d) => a + d.v, 0);
  const r = size / 2 - 6, ir = pie ? 0 : r * 0.62, c = size / 2;
  const starts = items.map((_, k) => -Math.PI / 2 + items.slice(0, k).reduce((a, x) => a + (total ? (x.v / total) * Math.PI * 2 : 0), 0));
  const arcs = items.map((d, k) => {
    const a0 = starts[k], a1 = a0 + (total ? (d.v / total) * Math.PI * 2 : 0);
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (rad: number, a: number) => `${c + rad * Math.cos(a)},${c + rad * Math.sin(a)}`;
    const full = a1 - a0 >= Math.PI * 2 - 1e-6;
    const path = full
      ? `M${c - r},${c} A${r},${r} 0 1 1 ${c + r},${c} A${r},${r} 0 1 1 ${c - r},${c}` + (ir ? ` M${c - ir},${c} A${ir},${ir} 0 1 0 ${c + ir},${c} A${ir},${ir} 0 1 0 ${c - ir},${c}` : "")
      : `M${p(r, a0)} A${r},${r} 0 ${large} 1 ${p(r, a1)} ` + (ir ? `L${p(ir, a1)} A${ir},${ir} 0 ${large} 0 ${p(ir, a0)} Z` : `L${c},${c} Z`);
    return { ...d, path };
  });
  const focus = active !== null ? items.find((d) => d.i === active) : null;
  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
        {arcs.map((a) => (
          <path key={a.i} d={a.path} fill={colors[a.i % colors.length]} fillRule="evenodd" stroke="#0b1220" strokeWidth={1.5}
            opacity={active === null || active === a.i ? 1 : 0.45} className="cursor-pointer transition-opacity"
            onMouseEnter={() => setActive(a.i)} onMouseLeave={() => setActive(null)} onClick={() => onSelect?.(a.l)} />
        ))}
        {!pie && (
          <>
            <text x={c} y={c - 4} textAnchor="middle" fontSize={focus ? 12 : 11} fill="#94a3b8">{focus ? truncate(focus.l, 18) : "Total"}</text>
            <text x={c} y={c + 16} textAnchor="middle" fontSize={17} fontWeight={700} fill="#f1f5f9">{fmtValue(focus ? focus.v : total, fmt, true)}</text>
            {focus && <text x={c} y={c + 32} textAnchor="middle" fontSize={11} fill="#7dd3fc">{((100 * focus.v) / (total || 1)).toFixed(1)}%</text>}
          </>
        )}
      </svg>
      <ul className="min-w-0 flex-1 space-y-1 text-xs">
        {items.slice(0, 12).map((d) => (
          <li key={d.i} className={`flex cursor-pointer items-center gap-2 rounded px-1.5 py-0.5 ${active === d.i ? "bg-slate-800" : ""}`}
            onMouseEnter={() => setActive(d.i)} onMouseLeave={() => setActive(null)} onClick={() => onSelect?.(d.l)}>
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colors[d.i % colors.length] }} />
            <span className="min-w-0 flex-1 truncate text-slate-300" title={d.l}>{d.l}</span>
            <span className="tabular-nums text-slate-400">{fmtValue(d.v, fmt, true)}</span>
            <span className="w-11 text-right tabular-nums text-slate-500">{((100 * d.v) / (total || 1)).toFixed(1)}%</span>
          </li>
        ))}
        {items.length > 12 && <li className="px-1.5 text-slate-500">+ {items.length - 12} more</li>}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- heatmap

export function Heatmap({ rows, cols, matrix, fmt, onSelect }: {
  rows: string[]; cols: string[]; matrix: (number | null)[][]; fmt?: Fmt; onSelect?: (row: string, col: string) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const vals = matrix.flat().filter((v): v is number => v !== null);
  const max = Math.max(1e-9, ...vals), min = Math.min(0, ...vals);
  const padL = Math.min(150, 14 + Math.max(...rows.map((r) => r.length), 3) * 6.2), padT = 12, padB = 46;
  const cw = Math.max(8, (width - padL - 8) / Math.max(1, cols.length));
  const ch = Math.max(16, Math.min(30, 300 / Math.max(1, rows.length)));
  const h = padT + rows.length * ch + padB;
  const color = (v: number | null) => {
    if (v === null) return "#0f172a";
    const t = (v - min) / (max - min || 1);
    return `rgba(56, 189, 248, ${0.08 + 0.92 * t})`;
  };
  const every = Math.ceil(cols.length / Math.max(1, Math.floor((width - padL) / 34)));
  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setTip(null)}>
      <svg width={width} height={h}>
        {rows.map((r, ri) => (
          <g key={r}>
            <text x={padL - 6} y={padT + ri * ch + ch / 2 + 3} fontSize={10} fill="#94a3b8" textAnchor="end">{truncate(r, Math.floor(padL / 6.2))}</text>
            {cols.map((c, ci) => (
              <rect key={c} x={padL + ci * cw} y={padT + ri * ch} width={cw - 1.5} height={ch - 1.5} rx={2} fill={color(matrix[ri][ci])} className="cursor-pointer"
                onClick={() => onSelect?.(r, c)}
                onMouseMove={(e) => {
                  const b = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                  setTip({ x: e.clientX - b.left, y: e.clientY - b.top, lines: [`${r} · ${c}`, fmtValue(matrix[ri][ci], fmt)] });
                }} />
            ))}
          </g>
        ))}
        {cols.map((c, ci) => ci % every === 0 && (
          <text key={c} x={padL + ci * cw + cw / 2} y={padT + rows.length * ch + 12} fontSize={10} fill="#94a3b8" textAnchor="end"
            transform={`rotate(-40 ${padL + ci * cw + cw / 2} ${padT + rows.length * ch + 12})`}>{truncate(c, 12)}</text>
        ))}
      </svg>
      <Tip tip={tip} />
    </div>
  );
}

// ---------------------------------------------------------------- scatter

export function Scatter({ points, xLabel, yLabel, xFmt, yFmt, onSelect }: {
  points: { x: number; y: number; label: string }[]; xLabel: string; yLabel: string; xFmt?: Fmt; yFmt?: Fmt; onSelect?: Select;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const height = 300, padL = 58, padB = 40, padR = 14;
  const xt = ticks(Math.min(0, ...points.map((p) => p.x)), Math.max(1, ...points.map((p) => p.x)));
  const yt = ticks(Math.min(0, ...points.map((p) => p.y)), Math.max(1, ...points.map((p) => p.y)));
  const plotW = width - padL - padR, plotH = height - 12 - padB;
  const sx = (v: number) => padL + ((v - xt[0]) / (xt[xt.length - 1] - xt[0] || 1)) * plotW;
  const sy = (v: number) => 12 + plotH - ((v - yt[0]) / (yt[yt.length - 1] - yt[0] || 1)) * plotH;
  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setTip(null)}>
      <svg width={width} height={height}>
        {yt.map((t) => <g key={`y${t}`}><line x1={padL} x2={padL + plotW} y1={sy(t)} y2={sy(t)} stroke={GRID} /><text x={padL - 6} y={sy(t) + 3} fontSize={10} fill={AXIS} textAnchor="end">{fmtValue(t, yFmt, true)}</text></g>)}
        {xt.map((t) => <text key={`x${t}`} x={sx(t)} y={12 + plotH + 14} fontSize={10} fill={AXIS} textAnchor="middle">{fmtValue(t, xFmt, true)}</text>)}
        <text x={padL + plotW / 2} y={height - 4} fontSize={11} fill="#94a3b8" textAnchor="middle">{xLabel}</text>
        <text x={12} y={12 + plotH / 2} fontSize={11} fill="#94a3b8" textAnchor="middle" transform={`rotate(-90 12 ${12 + plotH / 2})`}>{yLabel}</text>
        {points.map((p, i) => (
          <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={5} fill="#38bdf8" fillOpacity={0.7} stroke="#0ea5e9" className="cursor-pointer"
            onClick={() => onSelect?.(p.label)}
            onMouseMove={(e) => {
              const b = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
              setTip({ x: e.clientX - b.left, y: e.clientY - b.top, lines: [p.label, `${xLabel}: ${fmtValue(p.x, xFmt)}`, `${yLabel}: ${fmtValue(p.y, yFmt)}`] });
            }} />
        ))}
      </svg>
      <Tip tip={tip} />
    </div>
  );
}

// ---------------------------------------------------------------- funnel

export function Funnel({ labels, values, fmt, paletteName, onSelect }: { labels: string[]; values: (number | null)[]; fmt?: Fmt; paletteName?: string; onSelect?: Select }) {
  const colors = palette(paletteName);
  const max = Math.max(1, ...values.map((v) => v ?? 0));
  return (
    <div className="space-y-1.5">
      {labels.map((l, i) => {
        const v = values[i] ?? 0;
        const prev = i > 0 ? values[i - 1] ?? 0 : null;
        return (
          <button key={l} type="button" onClick={() => onSelect?.(l)} className="group flex w-full items-center gap-3 text-left">
            <span className="w-28 shrink-0 truncate text-xs text-slate-400" title={l}>{l}</span>
            <span className="relative h-8 flex-1">
              <span className="absolute inset-y-0 left-1/2 -translate-x-1/2 rounded-md transition-opacity group-hover:opacity-80"
                style={{ width: `${Math.max(2, (100 * v) / max)}%`, background: colors[i % colors.length] }} />
              <span className="absolute inset-0 flex items-center justify-center text-xs font-semibold text-slate-950">{fmtValue(v, fmt, true)}</span>
            </span>
            <span className="w-14 shrink-0 text-right text-[11px] text-slate-500">{prev ? `${((100 * v) / prev).toFixed(0)}%` : ""}</span>
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- treemap (squarified)

export function Treemap({ labels, values, fmt, paletteName, onSelect, height = 300 }: {
  labels: string[]; values: (number | null)[]; fmt?: Fmt; paletteName?: string; onSelect?: Select; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const colors = palette(paletteName);
  const rects = useMemo(() => {
    const items = labels.map((l, i) => ({ l, v: Math.max(0, values[i] ?? 0), i })).filter((d) => d.v > 0).sort((a, b) => b.v - a.v);
    const total = items.reduce((a, d) => a + d.v, 0) || 1;
    const out: { l: string; v: number; i: number; x: number; y: number; w: number; h: number }[] = [];
    let x = 0, y = 0, w = width, h = height;
    let rest = items.map((d) => ({ ...d, area: (d.v / total) * width * height }));
    while (rest.length) {
      const short = Math.min(w, h);
      let row: typeof rest = [];
      let worst = Infinity;
      for (const d of rest) {
        const next = [...row, d];
        const s = next.reduce((a, r) => a + r.area, 0);
        const wr = Math.max(...next.map((r) => Math.max((short * short * r.area) / (s * s), (s * s) / (short * short * r.area))));
        if (row.length && wr > worst) break;
        row = next;
        worst = wr;
      }
      const s = row.reduce((a, r) => a + r.area, 0);
      const thick = s / short;
      let off = 0;
      for (const r of row) {
        const len = r.area / thick;
        out.push(w >= h ? { ...r, x, y: y + off, w: thick, h: len } : { ...r, x: x + off, y, w: len, h: thick });
        off += len;
      }
      if (w >= h) { x += thick; w -= thick; } else { y += thick; h -= thick; }
      rest = rest.slice(row.length);
    }
    return out;
  }, [labels, values, width, height]);
  return (
    <div ref={ref} className="w-full">
      <svg width={width} height={height}>
        {rects.map((r) => (
          <g key={r.i} className="cursor-pointer" onClick={() => onSelect?.(r.l)}>
            <rect x={r.x + 1} y={r.y + 1} width={Math.max(0, r.w - 2)} height={Math.max(0, r.h - 2)} rx={4} fill={colors[r.i % colors.length]} opacity={0.85}>
              <title>{`${r.l}: ${fmtValue(r.v, fmt)}`}</title>
            </rect>
            {r.w > 60 && r.h > 32 && (
              <>
                <text x={r.x + 8} y={r.y + 18} fontSize={11} fontWeight={600} fill="#0b1220">{truncate(r.l, Math.floor(r.w / 7))}</text>
                <text x={r.x + 8} y={r.y + 32} fontSize={10} fill="#0b1220">{fmtValue(r.v, fmt, true)}</text>
              </>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------- KPI and sparkline

export function Sparkline({ values, color = "#38bdf8" }: { values: number[]; color?: string }) {
  if (values.length < 2) return null;
  const max = Math.max(...values), min = Math.min(...values);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${28 - ((v - min) / (max - min || 1)) * 24}`).join(" ");
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="mt-2 h-7 w-full">
      <polyline points={pts} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
