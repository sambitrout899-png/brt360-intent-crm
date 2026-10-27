/* Small dependency-free SVG charts for the Learning home. Every chart has a
   text equivalent (title / aria-label) and works on the dark theme. */
import { PALETTE } from "./palette";

/** A donut with a centre figure and a legend. */
export function Donut({ data, centre, caption, size = 132 }: { data: { label: string; value: number; color: string }[]; centre?: React.ReactNode; caption?: string; size?: number }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const r = 42;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className="flex items-center gap-4">
      <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={data.map((d) => `${d.label} ${d.value}`).join(", ")} className="shrink-0">
        <circle cx="50" cy="50" r={r} fill="none" stroke="#1e293b" strokeWidth="12" />
        {total > 0 &&
          data
            .filter((d) => d.value > 0)
            .map((d) => {
              const len = (d.value / total) * c;
              const el = (
                <circle key={d.label} cx="50" cy="50" r={r} fill="none" stroke={d.color} strokeWidth="12" strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset}
                  transform="rotate(-90 50 50)" strokeLinecap="butt">
                  <title>{`${d.label}: ${d.value}`}</title>
                </circle>
              );
              offset += len;
              return el;
            })}
        <text x="50" y="48" textAnchor="middle" className="fill-slate-100" fontSize="16" fontWeight="700">
          {centre ?? total}
        </text>
        {caption && (
          <text x="50" y="62" textAnchor="middle" className="fill-slate-400" fontSize="8">
            {caption}
          </text>
        )}
      </svg>
      <ul className="min-w-0 space-y-1 text-xs">
        {data.map((d) => (
          <li key={d.label} className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: d.color }} />
            <span className="truncate text-slate-300">{d.label}</span>
            <span className="ml-auto pl-2 tabular-nums font-semibold text-slate-100">{d.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A progress ring against a target. */
export function Ring({ value, target, label, color, sub }: { value: number | null; target?: number; label: string; color: string; sub?: string }) {
  const r = 40;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, value ?? 0));
  const met = target !== undefined && value !== null && value >= target;
  const t = target !== undefined ? (target / 100) * 360 - 90 : 0;
  return (
    <div className="flex flex-col items-center gap-1 text-center">
      <svg viewBox="0 0 100 100" width="104" height="104" role="img" aria-label={`${label} ${value ?? "no data"}%${target ? `, target ${target}%` : ""}`}>
        <circle cx="50" cy="50" r={r} fill="none" stroke="#1e293b" strokeWidth="10" />
        <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round" strokeDasharray={`${(v / 100) * c} ${c}`} transform="rotate(-90 50 50)" />
        {target !== undefined && (
          <line x1={50 + 33 * Math.cos((t * Math.PI) / 180)} y1={50 + 33 * Math.sin((t * Math.PI) / 180)} x2={50 + 47 * Math.cos((t * Math.PI) / 180)}
            y2={50 + 47 * Math.sin((t * Math.PI) / 180)} stroke="#f8fafc" strokeWidth="2" />
        )}
        <text x="50" y="55" textAnchor="middle" className="fill-slate-50" fontSize="18" fontWeight="700">
          {value === null ? "—" : `${Math.round(value)}%`}
        </text>
      </svg>
      <p className="text-xs font-semibold text-slate-200">{label}</p>
      <p className={`text-[11px] ${target === undefined ? "text-slate-500" : met ? "text-emerald-300" : "text-amber-300"}`}>
        {sub ?? (target !== undefined ? (value === null ? `target ${target}%` : met ? `✓ above ${target}% target` : `below ${target}% target`) : "")}
      </p>
    </div>
  );
}

/** Horizontal funnel with step-to-step conversion. */
export function Funnel({ steps }: { steps: { step: string; value: number }[] }) {
  const max = Math.max(1, ...steps.map((s) => s.value));
  const colors = [PALETTE.sky, PALETTE.indigo, PALETTE.violet, PALETTE.emerald];
  return (
    <div className="space-y-2">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].value : null;
        return (
          <div key={s.step} className="grid grid-cols-[84px_1fr_auto] items-center gap-2 text-sm">
            <span className="text-xs text-slate-400">{s.step}</span>
            <div className="h-7 overflow-hidden rounded-md bg-slate-800/70">
              <div className="flex h-full items-center rounded-md px-2 text-xs font-semibold text-slate-950 transition-all"
                style={{ width: `${Math.max(6, (100 * s.value) / max)}%`, background: `linear-gradient(90deg, ${colors[i % 4]}, ${colors[(i + 1) % 4]})` }}>
                {s.value}
              </div>
            </div>
            <span className="w-12 text-right text-[11px] tabular-nums text-slate-500">{prev ? `${Math.round((100 * s.value) / prev)}%` : ""}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Weekly columns (sessions) with registered / attended stacked bars and a "this week" marker. */
export function ActivityChart({ weeks }: { weeks: { week: string; sessions: number; registered: number; attended: number; future: boolean }[] }) {
  const max = Math.max(1, ...weeks.map((w) => w.registered));
  const H = 120;
  const bw = 100 / weeks.length;
  const nowIndex = weeks.findIndex((w) => w.future) - 1;
  return (
    <div>
      <svg viewBox={`0 0 100 ${H + 18}`} preserveAspectRatio="none" className="h-44 w-full" role="img"
        aria-label={weeks.map((w) => `week of ${w.week}: ${w.sessions} sessions, ${w.registered} registered, ${w.attended} attended`).join("; ")}>
        <defs>
          <linearGradient id="reg" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={PALETTE.sky} stopOpacity="0.9" />
            <stop offset="1" stopColor={PALETTE.indigo} stopOpacity="0.5" />
          </linearGradient>
          <linearGradient id="att" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={PALETTE.emerald} />
            <stop offset="1" stopColor={PALETTE.cyan} stopOpacity="0.7" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1="0" x2="100" y1={H * f} y2={H * f} stroke="#1e293b" strokeWidth="0.3" />
        ))}
        {nowIndex >= 0 && <rect x={nowIndex * bw} y="0" width={bw} height={H} fill="#38bdf8" opacity="0.06" />}
        {weeks.map((w, i) => {
          const rh = (w.registered / max) * (H - 8);
          const ah = (w.attended / max) * (H - 8);
          return (
            <g key={w.week} opacity={w.future ? 0.55 : 1}>
              <title>{`Week of ${w.week}: ${w.sessions} session(s), ${w.registered} registered, ${w.attended} attended`}</title>
              <rect x={i * bw + bw * 0.18} y={H - rh} width={bw * 0.64} height={rh} rx="0.8" fill="url(#reg)" />
              <rect x={i * bw + bw * 0.32} y={H - ah} width={bw * 0.36} height={ah} rx="0.6" fill="url(#att)" />
              {w.sessions > 0 && <circle cx={i * bw + bw / 2} cy={Math.max(4, H - rh - 4)} r="1.6" fill={PALETTE.amber} />}
            </g>
          );
        })}
      </svg>
      <div className="mt-1 grid text-[10px] text-slate-500" style={{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }}>
        {weeks.map((w, i) => (
          <span key={w.week} className={`text-center ${i === nowIndex ? "font-semibold text-sky-300" : ""}`}>
            {i === nowIndex ? "now" : new Date(w.week).toLocaleDateString(undefined, { day: "numeric", month: "short" })}
          </span>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-slate-400">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: PALETTE.sky }} />Registered</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: PALETTE.emerald }} />Attended</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: PALETTE.amber }} />Has sessions</span>
        <span>Faded = weeks ahead</span>
      </div>
    </div>
  );
}

/** Before -> after on one scale (a dumbbell). */
export function PrePost({ label, pre, post, max, unit = "", mark }: { label: string; pre: number | null; post: number | null; max: number; unit?: string; mark?: number }) {
  const x = (v: number) => `${(100 * v) / max}%`;
  const up = pre !== null && post !== null && post >= pre;
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-semibold text-slate-200">{label}</span>
        <span className={`tabular-nums font-bold ${up ? "text-emerald-300" : "text-rose-300"}`}>
          {pre === null || post === null ? "—" : `${post - pre >= 0 ? "+" : ""}${(post - pre).toFixed(1)}${unit}`}
        </span>
      </div>
      <div className="relative h-3 rounded-full bg-slate-800">
        {mark !== undefined && <div className="absolute top-[-3px] h-[18px] w-px bg-slate-400" style={{ left: x(mark) }} title={`Pass mark ${mark}${unit}`} />}
        {pre !== null && post !== null && (
          <div className="absolute top-0 h-3 rounded-full" style={{ left: x(Math.min(pre, post)), width: `${(100 * Math.abs(post - pre)) / max}%`, background: `linear-gradient(90deg, ${PALETTE.slate}, ${up ? PALETTE.emerald : PALETTE.rose})` }} />
        )}
        {pre !== null && <div className="absolute top-[-2px] h-4 w-4 -translate-x-1/2 rounded-full border-2 border-slate-900 bg-slate-400" style={{ left: x(pre) }} title={`Before ${pre}${unit}`} />}
        {post !== null && <div className="absolute top-[-2px] h-4 w-4 -translate-x-1/2 rounded-full border-2 border-slate-900" style={{ left: x(post), background: up ? PALETTE.emerald : PALETTE.rose }} title={`After ${post}${unit}`} />}
      </div>
      <div className="flex justify-between text-[11px] text-slate-500">
        <span>before {pre ?? "—"}{unit}</span>
        <span>after {post ?? "—"}{unit}</span>
      </div>
    </div>
  );
}

/** Ranked horizontal bars. */
export function RankBars({ rows, unit = "", color = PALETTE.violet }: { rows: { label: string; value: number | null; note?: string }[]; unit?: string; color?: string }) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.value ?? 0)));
  if (!rows.length) return <p className="text-sm text-slate-500">No results yet.</p>;
  return (
    <ul className="space-y-2.5">
      {rows.map((r, i) => (
        <li key={r.label} className="space-y-1">
          <div className="flex items-baseline gap-2 text-xs">
            <span className="w-4 text-slate-500">{i + 1}</span>
            <span className="min-w-0 flex-1 truncate text-slate-200">{r.label}</span>
            {r.note && <span className="text-[11px] text-slate-500">{r.note}</span>}
            <span className="tabular-nums font-semibold text-slate-100">{r.value === null ? "—" : `${r.value > 0 ? "+" : ""}${r.value}${unit}`}</span>
          </div>
          <div className="ml-6 h-2 rounded-full bg-slate-800">
            <div className="h-2 rounded-full" style={{ width: `${(100 * Math.abs(r.value ?? 0)) / max}%`, background: (r.value ?? 0) < 0 ? PALETTE.rose : `linear-gradient(90deg, ${color}, ${PALETTE.sky})` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** 14-day stacked columns for messages. */
export function DailyBars({ days }: { days: { day: string; sent: number; skipped: number; failed: number }[] }) {
  const max = Math.max(1, ...days.map((d) => d.sent + d.skipped + d.failed));
  return (
    <div>
      <div className="flex h-24 items-end gap-1" role="img" aria-label={days.map((d) => `${d.day}: ${d.sent} sent, ${d.skipped} skipped, ${d.failed} failed`).join("; ")}>
        {days.map((d) => (
          <div key={d.day} className="flex h-full flex-1 flex-col justify-end" title={`${d.day}: ${d.sent} sent, ${d.skipped} skipped (already done), ${d.failed} failed`}>
            <div style={{ height: `${(100 * d.failed) / max}%`, background: PALETTE.rose }} className="rounded-t-sm" />
            <div style={{ height: `${(100 * d.skipped) / max}%`, background: PALETTE.amber, opacity: 0.8 }} />
            <div style={{ height: `${(100 * d.sent) / max}%`, background: `linear-gradient(180deg, ${PALETTE.cyan}, ${PALETTE.sky})` }} className="rounded-b-sm" />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>{days[0] && new Date(days[0].day).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
        <span>today</span>
      </div>
    </div>
  );
}

/** Five rating buckets as a mini histogram. */
export function RatingBars({ counts }: { counts: number[] }) {
  const max = Math.max(1, ...counts);
  const labels = ["1-2", "2-3", "3-4", "4-4.5", "4.5-5"];
  const colors = [PALETTE.rose, PALETTE.orange, PALETTE.amber, PALETTE.cyan, PALETTE.emerald];
  return (
    <div className="flex h-20 items-end gap-2">
      {counts.map((c, i) => (
        <div key={labels[i]} className="flex flex-1 flex-col items-center gap-1" title={`${labels[i]}: ${c}`}>
          <div className="w-full rounded-t-md" style={{ height: `${Math.max(4, (56 * c) / max)}px`, background: colors[i] }} />
          <span className="text-[10px] text-slate-500">{labels[i]}</span>
        </div>
      ))}
    </div>
  );
}
