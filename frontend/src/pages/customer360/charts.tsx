import { useId, useState } from "react";
import { formatCell, formatValue, monthLabel, seriesColor, statusTone, TONE_COLORS, type Fmt } from "./format";

/** Small, dependency-free SVG charts for the Customer 360 page. Every chart
 * scales to its card (viewBox), animates in, and explains itself on hover. */

export function StatusChip({ value }: { value: unknown }) {
  const tone = statusTone(value);
  const color = TONE_COLORS[tone] || "var(--c-muted)";
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold capitalize"
      style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {formatCell(value, "text")}
    </span>
  );
}


// ---------------------------------------------------------------- KPI

/** A headline number. `accent` is kept for callers; the value alone carries
 * the meaning (no decorative bar that reads like progress). */
export function KpiValue({ value, fmt, unit, count }: { value: number; fmt: Fmt; unit?: string; count?: number; accent: string }) {
  return (
    <div className="c360-rise">
      <div className="text-[26px] font-bold leading-tight tracking-tight tabular-nums" style={{ color: "var(--c-text)" }}>
        {formatValue(value, fmt, true, unit)}
      </div>
      {count !== undefined && (
        <div className="mt-1 text-xs" style={{ color: "var(--c-muted)" }}>
          from {count} record{count === 1 ? "" : "s"}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- trend (line / area / bars)

export function TrendChart({
  points,
  type,
  fmt,
  accent,
}: {
  points: { label: string; value: number }[];
  type: "line" | "area" | "bar";
  fmt: Fmt;
  accent: string;
}) {
  const gid = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);
  if (points.length === 0) return <Empty />;
  const W = 320;
  const H = 150;
  const padL = 8;
  const padR = 8;
  const padT = 14;
  const padB = 22;
  const max = Math.max(...points.map((p) => p.value), 1) * 1.12;
  const step = (W - padL - padR) / Math.max(points.length - (type === "bar" ? 0 : 1), 1);
  const x = (i: number) => padL + (type === "bar" ? step * i + step / 2 : step * i);
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = `${line} L${x(points.length - 1).toFixed(1)},${H - padB} L${x(0).toFixed(1)},${H - padB} Z`;
  const shown = hover ?? points.length - 1;
  const first = points[0].value;
  const last = points[points.length - 1].value;
  const delta = first ? ((last - first) / first) * 100 : 0;

  return (
    <div>
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-xl font-bold" style={{ color: "var(--c-text)" }}>
          {formatValue(points[shown].value, fmt, true)}
        </span>
        <span className="text-xs" style={{ color: "var(--c-muted)" }}>
          {monthLabel(points[shown].label)}
        </span>
        {hover === null && points.length > 1 && (
          <span className={`ml-auto text-xs font-semibold ${delta >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
            {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(0)}% over {points.length} mo
          </span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full overflow-visible" onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id={`g${gid}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={accent} stopOpacity="0.45" />
            <stop offset="100%" stopColor={accent} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={padL} x2={W - padR} y1={padT + (H - padT - padB) * f} y2={padT + (H - padT - padB) * f} stroke="var(--c-border)" strokeDasharray="3 4" />
        ))}
        {type === "bar" ? (
          points.map((p, i) => {
            const bw = Math.min(step * 0.62, 26);
            return (
              <rect
                key={p.label}
                className="c360-bar"
                style={{ animationDelay: `${i * 35}ms` }}
                x={x(i) - bw / 2}
                y={y(p.value)}
                width={bw}
                height={Math.max(H - padB - y(p.value), 1)}
                rx={4}
                fill={accent}
                opacity={hover === null || hover === i ? 1 : 0.4}
              />
            );
          })
        ) : (
          <>
            {type === "area" && <path d={area} fill={`url(#g${gid})`} className="c360-fade" />}
            <path d={line} fill="none" stroke={accent} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" className="c360-draw" pathLength={1} />
            {points.map((p, i) => (
              <circle key={p.label} cx={x(i)} cy={y(p.value)} r={hover === i ? 5 : 2.5} fill={hover === i ? accent : "var(--c-bg)"} stroke={accent} strokeWidth={2} />
            ))}
          </>
        )}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={padT} y2={H - padB} stroke={accent} strokeOpacity={0.35} />}
        {points.map((p, i) =>
          i % Math.ceil(points.length / 6) === 0 || i === points.length - 1 ? (
            <text key={p.label} x={x(i)} y={H - 6} textAnchor="middle" fontSize="9" fill="var(--c-muted)">
              {monthLabel(p.label).split(" ")[0]}
            </text>
          ) : null
        )}
        {points.map((p, i) => (
          <rect key={`h${p.label}`} x={x(i) - step / 2} y={0} width={step} height={H} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------- categories

export function BarList({ items, fmt, accent }: { items: { label: string; value: number }[]; fmt: Fmt; accent: string }) {
  if (items.length === 0) return <Empty />;
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <div className="space-y-2.5">
      {items.map((item, i) => {
        const color = seriesColor(item.label, i, accent);
        return (
          <div key={item.label} className="group">
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="truncate capitalize" style={{ color: "var(--c-text)" }}>
                {item.label.replace(/_/g, " ")}
              </span>
              <span className="font-semibold tabular-nums" style={{ color: "var(--c-muted)" }}>
                {formatValue(item.value, fmt, true)}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full" style={{ background: "color-mix(in srgb, var(--c-muted) 14%, transparent)" }}>
              <div
                className="c360-grow h-full rounded-full transition-all group-hover:brightness-125"
                style={{ width: `${(item.value / max) * 100}%`, background: color, animationDelay: `${i * 60}ms` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function Donut({ items, fmt, accent }: { items: { label: string; value: number }[]; fmt: Fmt; accent: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const total = items.reduce((s, i) => s + i.value, 0);
  if (items.length === 0 || total === 0) return <Empty />;
  const R = 42;
  const C = 2 * Math.PI * R;
  const lengths = items.map((item) => (item.value / total) * C);
  const starts = lengths.map((_, i) => lengths.slice(0, i).reduce((s, l) => s + l, 0));
  const shown = hover !== null ? items[hover] : null;
  return (
    <div className="flex flex-wrap items-center justify-center gap-4">
      <svg viewBox="0 0 110 110" className="h-32 w-32 shrink-0">
        <g transform="rotate(-90 55 55)">
          <circle cx="55" cy="55" r={R} fill="none" stroke="color-mix(in srgb, var(--c-muted) 14%, transparent)" strokeWidth="13" />
          {items.map((item, i) => (
            <circle
              key={item.label}
              cx="55"
              cy="55"
              r={R}
              fill="none"
              stroke={seriesColor(item.label, i, accent)}
              strokeWidth={hover === i ? 16 : 13}
              strokeDasharray={`${Math.max(lengths[i] - 1.5, 0.5)} ${C}`}
              strokeDashoffset={-starts[i]}
              className="c360-fade cursor-pointer transition-all"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}
        </g>
        <text x="55" y="56" textAnchor="middle" fontSize="15" fontWeight="800" fill="var(--c-text)">
          {formatValue(shown ? shown.value : total, fmt, true)}
        </text>
        <text x="55" y="69" textAnchor="middle" fontSize="8" fill="var(--c-muted)">
          {shown ? `${Math.round((shown.value / total) * 100)}% ${shown.label.replace(/_/g, " ")}`.slice(0, 22) : "total"}
        </text>
      </svg>
      <ul className="min-w-[8rem] flex-1 space-y-1.5 text-xs">
        {items.map((item, i) => (
          <li
            key={item.label}
            className="flex items-center gap-2"
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            style={{ opacity: hover === null || hover === i ? 1 : 0.45 }}
          >
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: seriesColor(item.label, i, accent) }} />
            <span className="truncate capitalize" style={{ color: "var(--c-text)" }}>
              {item.label.replace(/_/g, " ")}
            </span>
            <span className="ml-auto tabular-nums" style={{ color: "var(--c-muted)" }}>
              {formatValue(item.value, fmt, true)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- gauge

export function Gauge({ value, max, fmt, accent }: { value: number; max: number; fmt: Fmt; accent: string }) {
  const ratio = Math.max(0, Math.min(1, value / (max || 1)));
  const R = 50;
  const half = Math.PI * R;
  const color = fmt === "percent" && max === 100 ? (value >= 99.5 ? "#34d399" : value >= 98 ? "#fbbf24" : "#f87171") : accent;
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 120 70" className="w-full max-w-[220px]">
        <path d="M10,62 A50,50 0 0 1 110,62" fill="none" stroke="color-mix(in srgb, var(--c-muted) 16%, transparent)" strokeWidth="11" strokeLinecap="round" />
        <path
          d="M10,62 A50,50 0 0 1 110,62"
          fill="none"
          stroke={color}
          strokeWidth="11"
          strokeLinecap="round"
          strokeDasharray={`${half * ratio} ${half}`}
          className="c360-gauge"
        />
        <text x="60" y="56" textAnchor="middle" fontSize="17" fontWeight="800" fill="var(--c-text)">
          {formatValue(value, fmt, true)}
        </text>
      </svg>
      <div className="-mt-1 text-[11px]" style={{ color: "var(--c-muted)" }}>
        of {formatValue(max, fmt, true)}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- table

export function DataTable({
  columns,
  rows,
  total,
}: {
  columns: { key: string; label: string; type: string }[];
  rows: Record<string, unknown>[];
  total: number;
}) {
  if (rows.length === 0) return <Empty />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr style={{ color: "var(--c-muted)" }}>
            {columns.map((c) => (
              <th key={c.key} className={`whitespace-nowrap pb-2 pr-4 font-semibold last:pr-0 ${c.type === "money" || c.type === "number" || c.type === "percent" ? "text-right" : ""}`}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="c360-rise border-t" style={{ borderColor: "var(--c-border)", animationDelay: `${i * 40}ms` }}>
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`whitespace-nowrap py-2 pr-4 last:pr-0 ${c.type === "money" || c.type === "number" || c.type === "percent" ? "text-right tabular-nums" : ""}`}
                  style={{ color: "var(--c-text)" }}
                >
                  {c.type === "status" ? <StatusChip value={row[c.key]} /> : formatCell(row[c.key], c.type)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {total > rows.length && (
        <p className="mt-2 text-[11px]" style={{ color: "var(--c-muted)" }}>
          Showing {rows.length} of {total}
        </p>
      )}
    </div>
  );
}

export function Empty({ text = "Nothing here yet for this customer." }: { text?: string }) {
  return (
    <div className="flex h-24 items-center justify-center rounded-lg border border-dashed text-xs" style={{ borderColor: "var(--c-border)", color: "var(--c-muted)" }}>
      {text}
    </div>
  );
}
