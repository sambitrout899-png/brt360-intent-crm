import { download, num, tr } from "./api";
import type { Dashboard } from "./api";
import { useLoad } from "./hooks";
import { Badge, Card, Note, Stat, Table, btnGhost } from "./ui";

/* Session outcome dashboard (ANL-001..008): funnel, knowledge and confidence
   pre vs post, gain bands, objectives, significance, flags and feedback. */

function Bar({ value, max = 100, tone = "bg-sky-500" }: { value: number | null | undefined; max?: number; tone?: string }) {
  const pct = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, (100 * value) / max));
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function PrePost({ title, d, max, unit }: { title: string; d: NonNullable<Dashboard["knowledge"]>; max: number; unit: string }) {
  return (
    <div className="space-y-2 rounded-xl border border-slate-800 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</p>
      {(["pre", "post"] as const).map((p) => (
        <div key={p} className="grid grid-cols-[48px_1fr_64px] items-center gap-2 text-sm">
          <span className="text-slate-400">{p === "pre" ? "Before" : "After"}</span>
          <Bar value={d[p].mean} max={max} tone={p === "pre" ? "bg-slate-500" : "bg-sky-500"} />
          <span className="text-right tabular-nums">{num(d[p].mean, unit)}</span>
        </div>
      ))}
      <p className="text-xs text-slate-400">
        Change: <b className={d.change !== null && d.change < 0 ? "text-red-300" : "text-emerald-300"}>{d.change === null ? "—" : `${d.change > 0 ? "+" : ""}${d.change}`}</b> · n = {d.post.n}
      </p>
    </div>
  );
}

export default function SessionResults({ sessionId, canRecompute }: { sessionId: number; canRecompute?: boolean }) {
  const { data: d, error, reload } = useLoad(() => tr.dashboard(sessionId), [sessionId]);
  if (error) return <Note>{error}</Note>;
  if (!d) return <p className="animate-pulse text-sm text-slate-500">Computing outcomes…</p>;
  const exportAs = (fmt: string) => download(`/api/training/analytics/export?format=${fmt}&session_id=${sessionId}`, `session-${sessionId}.${fmt}`).catch(() => undefined);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-500">
          Paired participants n = {d.paired_n} · post-only {d.post_only} · late pre excluded {d.late_pre_excluded}
        </span>
        <span className="ml-auto flex flex-wrap gap-2">
          {canRecompute && (
            <button type="button" className={btnGhost} onClick={() => tr.recompute(sessionId).then(reload)}>
              ↻ Recompute
            </button>
          )}
          {["csv", "xlsx", "pdf"].map((f) => (
            <button key={f} type="button" className={btnGhost} onClick={() => exportAs(f)}>
              ⬇ {f.toUpperCase()}
            </button>
          ))}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Invited" value={d.funnel.invited} />
        <Stat label="Pre done" value={d.funnel.pre_done} />
        <Stat label="Attended" value={d.funnel.attended} />
        <Stat label="Post done" value={d.funnel.post_done} />
      </div>
      {!d.template_based && <Note kind="info">{d.note}</Note>}
      {d.knowledge && d.confidence && (
        <>
          <div className="grid gap-3 md:grid-cols-3">
            <PrePost title="Knowledge (paired items)" d={d.knowledge} max={100} unit="%" />
            <PrePost title="Confidence (1-5)" d={d.confidence} max={5} unit="" />
            <div className="space-y-2 rounded-xl border border-slate-800 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Normalized gain</p>
              <p className="text-2xl font-bold tabular-nums">{num(d.gain?.mean)}</p>
              <div className="flex flex-wrap gap-1">
                {Object.entries(d.gain?.bands ?? {}).map(([band, count]) => (
                  <Badge key={band} value={band === "High" ? "attended" : band === "Decline" ? "cancelled" : band === "Medium" ? "scheduled" : "draft"}>
                    {band}: {count}
                  </Badge>
                ))}
              </div>
              <p className="text-xs text-slate-400">Outcome rate: {num(d.outcome_rate, "%")} (all measured objectives ≥ pass mark {d.pass_mark}%)</p>
            </div>
          </div>
          {d.significance && (
            <div className="flex flex-wrap gap-3 text-xs text-slate-400">
              {Object.entries(d.significance).map(([k, s]) => (
                <span key={k} className="rounded-lg border border-slate-800 px-2 py-1">
                  {k}: {s ? `${s.label} (${s.test}, p = ${s.p_value}, d = ${s.cohens_d ?? "—"}, n = ${s.n})` : "not enough pairs (n < 5)"}
                </span>
              ))}
            </div>
          )}
          {!!d.flags?.length && (
            <div className="space-y-1">
              {d.flags.map((f, i) => (
                <Note key={i} kind="info">
                  ⚑ <b>{f.flag}</b>
                  {f.objective ? ` (${f.objective})` : ""}: {f.detail}
                </Note>
              ))}
            </div>
          )}
          <Card title="Learning objectives">
            <Table
              head={["Objective", "Before", "After", "Change", "Confidence", "Achieved"]}
              rows={(d.objectives ?? []).map((o) => [
                <span key="o">
                  <b className="font-mono text-xs">{o.code}</b> {o.text}
                </span>,
                o.measured ? num(o.pre, "%") : <i key="n" className="text-slate-500">not measured</i>,
                num(o.post, "%"),
                num(o.change),
                o.confidence_pre === null ? "—" : `${o.confidence_pre} → ${num(o.confidence_post)}`,
                o.achieved === null ? "—" : `${o.achieved} / ${o.achieved_of}`,
              ])}
            />
          </Card>
        </>
      )}
      {d.feedback && (
        <Card title={`Feedback${d.feedback.anonymous ? " (anonymous)" : ""} · ${d.feedback.n} responses${d.feedback.rating ? ` · ${d.feedback.rating.toFixed(2)} / 5` : ""}`}>
          {d.feedback.suppressed ? (
            <Note kind="info">{d.feedback.message}</Note>
          ) : (
            <div className="space-y-3">
              {(d.feedback.questions ?? []).map((q) => (
                <div key={q.item_key} className="space-y-1 text-sm">
                  <p className="font-medium text-slate-200">
                    {q.text} <span className="text-xs text-slate-500">n = {q.n}</span>
                  </p>
                  {q.mean !== undefined && <p className="text-slate-300">Average {num(q.mean)}{q.nps !== undefined ? ` · NPS ${q.nps}` : ""}</p>}
                  {q.distribution?.map((x) => (
                    <div key={x.label} className="grid grid-cols-[1fr_120px_32px] items-center gap-2 text-xs">
                      <span className="truncate text-slate-400">{x.label}</span>
                      <Bar value={x.count} max={Math.max(1, q.n)} />
                      <span className="text-right">{x.count}</span>
                    </div>
                  ))}
                  {q.comments?.map((c, i) => (
                    <blockquote key={i} className="border-l-2 border-slate-700 pl-2 text-xs text-slate-300">
                      {c}
                    </blockquote>
                  ))}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
      {d.participants && (
        <Card title="Participants (paired)">
          <Table
            head={["Participant", "Knowledge", "Change", "Confidence", "Change", "Gain"]}
            rows={d.participants.map((p) => [
              p.name,
              `${num(p.k_pre, "%")} → ${num(p.k_post, "%")}`,
              num(p.dk),
              `${num(p.c_pre)} → ${num(p.c_post)}`,
              num(p.dc),
              p.band ? `${num(p.g)} ${p.band}` : "excluded",
            ])}
          />
        </Card>
      )}
      {d.aggregate_only && <Note kind="info">You see aggregated results only.</Note>}
    </div>
  );
}
