import { useState } from "react";
import { fmt, num, tr } from "./api";
import type { Insights, Overview } from "./api";
import { ActivityChart, DailyBars, Donut, Funnel, PrePost, RankBars, RatingBars, Ring } from "./charts";
import { useLoad } from "./hooks";
import { LEARNING_PAGES } from "./learningMeta";
import { PALETTE } from "./palette";
import { Note } from "./ui";

/* The Learning home: a dashboard of how learning is going across everything
   this person may see - headline insights, trends, the participation funnel,
   completion against the BRD targets, learning impact, the best programs and
   facilitators, feedback, and what needs attention. */

type Open = (page: string) => void;

function Panel({ title, hint, children, className = "", accent = PALETTE.sky }: { title: string; hint?: string; children: React.ReactNode; className?: string; accent?: string }) {
  return (
    <section className={`relative overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/60 p-4 ${className}`}>
      <div className="absolute inset-x-0 top-0 h-0.5" style={{ background: `linear-gradient(90deg, ${accent}, transparent)` }} />
      <div className="mb-3 flex items-baseline gap-2">
        <h3 className="text-sm font-bold text-slate-100">{title}</h3>
        {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

function Kpi({ label, value, sub, from, to, icon, onClick }: { label: string; value: React.ReactNode; sub?: string; from: string; to: string; icon: string; onClick?: () => void }) {
  return (
    <button type="button" onClick={onClick} disabled={!onClick}
      className="group relative overflow-hidden rounded-2xl p-[1px] text-left transition enabled:hover:-translate-y-0.5 disabled:cursor-default"
      style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}>
      <div className="h-full rounded-2xl bg-slate-950/85 p-3.5">
        <div className="flex items-start justify-between gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</p>
          <span className="flex h-7 w-7 items-center justify-center rounded-lg text-sm" style={{ background: `${from}26` }} aria-hidden>
            {icon}
          </span>
        </div>
        <p className="mt-1 bg-clip-text text-2xl font-extrabold tabular-nums text-transparent" style={{ backgroundImage: `linear-gradient(90deg, ${from}, ${to})` }}>
          {value}
        </p>
        {sub && <p className="mt-0.5 text-[11px] text-slate-500">{sub}</p>}
      </div>
    </button>
  );
}

function headlines(i: Insights, o: Overview): { tone: "good" | "warn" | "info"; text: string }[] {
  const out: { tone: "good" | "warn" | "info"; text: string }[] = [];
  const k = i.impact;
  if (k && k.n && k.knowledge.pre !== null && k.knowledge.post !== null) {
    const d = k.knowledge.post - k.knowledge.pre;
    out.push({ tone: d > 0 ? "good" : "warn", text: `Knowledge ${d >= 0 ? "up" : "down"} ${Math.abs(d).toFixed(1)} pts (${k.knowledge.pre}% → ${k.knowledge.post}%) across ${k.n} participant${k.n === 1 ? "" : "s"}.` });
    const high = k.bands.High + k.bands.Medium;
    if (k.n >= 3) out.push({ tone: high / k.n >= 0.5 ? "good" : "warn", text: `${Math.round((100 * high) / k.n)}% reached a medium or high learning gain.` });
  }
  const post = i.completion.post.rate;
  if (post !== null) out.push({ tone: post >= i.targets.post ? "good" : "warn", text: `Post-assessment completion is ${post}% - ${post >= i.targets.post ? "above" : "below"} the ${i.targets.post}% target.` });
  const pre = i.completion.pre.rate;
  if (pre !== null && pre < i.targets.pre) out.push({ tone: "warn", text: `Pre-assessment completion is ${pre}% against an ${i.targets.pre}% target - reminders go out automatically; resend links from a session if needed.` });
  if (i.attendance_rate !== null) out.push({ tone: i.attendance_rate >= 85 ? "good" : "info", text: `${i.attendance_rate}% of registered participants turned up.` });
  if (i.programs?.[0]?.knowledge_change) out.push({ tone: "info", text: `Strongest program: ${i.programs[0].program} (+${i.programs[0].knowledge_change} pts).` });
  if (o.sessions.next_7_days) out.push({ tone: "info", text: `${o.sessions.next_7_days} session${o.sessions.next_7_days === 1 ? "" : "s"} in the next 7 days${o.sessions.waitlisted ? `, ${o.sessions.waitlisted} people on waitlists` : ""}.` });
  return out.slice(0, 4);
}

const TONE = { good: "border-emerald-500/30 bg-emerald-500/10 text-emerald-100", warn: "border-amber-500/30 bg-amber-500/10 text-amber-100", info: "border-sky-500/30 bg-sky-500/10 text-sky-100" };
const ICON = { good: "▲", warn: "!", info: "•" };

export function LearningHome({ pages, onOpen }: { pages: string[]; onOpen?: Open }) {
  const [today] = useState(() => new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }));
  const ov = useLoad(() => tr.overview(), []);
  const ins = useLoad(() => tr.insights(), []);
  if (ov.error || ins.error) return <Note>{ov.error || ins.error}</Note>;
  const o = ov.data;
  const i = ins.data;
  if (!o || !i) return <p className="animate-pulse text-sm text-slate-500">Building your dashboard…</p>;
  const go = (page: string) => (pages.includes(page) && onOpen ? () => onOpen(page) : undefined);
  const lines = headlines(i, o);
  const regStatus = i.registration_status;
  const sessionStatus = i.session_status;
  return (
    <div className="space-y-5">
      {/* ---------- hero ---------- */}
      <section className="relative overflow-hidden rounded-3xl border border-slate-800 p-5"
        style={{ background: "radial-gradient(1200px 300px at 0% 0%, rgba(56,189,248,0.18), transparent), radial-gradient(800px 300px at 100% 0%, rgba(167,139,250,0.18), transparent), radial-gradient(600px 240px at 50% 120%, rgba(52,211,153,0.14), transparent), #0b1222" }}>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-sky-300">{today}</p>
            <h2 className="mt-1 text-2xl font-extrabold text-white">How learning is going</h2>
            <p className="mt-0.5 text-sm text-slate-300">Across the sessions you can see · you are {o.roles.join(" + ")}</p>
          </div>
          {o.sessions.upcoming[0] && (
            <div className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-right">
              <p className="text-[11px] uppercase tracking-wide text-slate-400">Next session</p>
              <p className="text-sm font-semibold text-white">{o.sessions.upcoming[0].title}</p>
              <p className="text-xs text-slate-300">{fmt(o.sessions.upcoming[0].starts_at)} · {o.sessions.upcoming[0].registered}/{o.sessions.upcoming[0].capacity} seats</p>
            </div>
          )}
        </div>
        {lines.length > 0 && (
          <ul className="mt-4 grid gap-2 md:grid-cols-2">
            {lines.map((l, n) => (
              <li key={n} className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-sm ${TONE[l.tone]}`}>
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/10 text-[11px] font-bold">{ICON[l.tone]}</span>
                {l.text}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ---------- KPIs ---------- */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Next 7 days" value={o.sessions.next_7_days} sub={`${o.sessions.in_progress} in progress now`} from={PALETTE.sky} to={PALETTE.indigo} icon="📅" onClick={go("training_sessions")} />
        <Kpi label="Attendance" value={num(i.attendance_rate, "%")} sub="of registered turned up" from={PALETTE.emerald} to={PALETTE.cyan} icon="✅" onClick={go("training_sessions")} />
        <Kpi label="Post completion" value={num(i.completion.post.rate, "%")} sub={`${i.completion.post.done} of ${i.completion.post.invited} · target ${i.targets.post}%`} from={PALETTE.violet} to={PALETTE.sky} icon="📝" onClick={go("training_sessions")} />
        {i.impact ? (
          <Kpi label="Knowledge change" value={i.impact.knowledge.pre !== null && i.impact.knowledge.post !== null ? `${i.impact.knowledge.post - i.impact.knowledge.pre >= 0 ? "+" : ""}${(i.impact.knowledge.post - i.impact.knowledge.pre).toFixed(1)}` : "—"}
            sub={`pts · gain ${num(i.impact.gain)} · n ${i.impact.n}`} from={PALETTE.amber} to={PALETTE.orange} icon="📈" onClick={go("training_outcomes")} />
        ) : (
          <Kpi label="My open assessments" value={o.mine.open_assessments.length} sub="waiting for you" from={PALETTE.amber} to={PALETTE.orange} icon="📝" onClick={go("training_mine")} />
        )}
      </div>

      {/* ---------- trend + funnel ---------- */}
      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Activity" hint="registrations and attendance by week · 8 weeks back, 3 ahead" className="xl:col-span-2" accent={PALETTE.sky}>
          <ActivityChart weeks={i.activity} />
        </Panel>
        <Panel title="Participation funnel" hint="% = conversion from the step above" accent={PALETTE.violet}>
          <Funnel steps={i.funnel} />
          <div className="mt-4 flex flex-wrap gap-2 text-[11px] text-slate-400">
            {Object.entries(i.sources).map(([k, v]) => (
              <span key={k} className="rounded-full bg-slate-800 px-2 py-0.5">
                {k === "self" ? "Self-registered" : k === "bulk" ? "Bulk upload" : "Nominated"}: <b className="text-slate-200">{v}</b>
              </span>
            ))}
          </div>
        </Panel>
      </div>

      {/* ---------- completion + impact ---------- */}
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Completion against targets" hint="BRD targets: pre ≥ 80%, post ≥ 70%" accent={PALETTE.emerald}>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Ring label="Pre-assessment" value={i.completion.pre.rate} target={i.targets.pre} color={PALETTE.sky} />
            <Ring label="Post-assessment" value={i.completion.post.rate} target={i.targets.post} color={PALETTE.violet} />
            <Ring label="Feedback" value={i.completion.feedback.rate} color={PALETTE.amber} sub={`${i.completion.feedback.done} responses`} />
            <Ring label="Attendance" value={i.attendance_rate} color={PALETTE.emerald} sub="present or late" />
          </div>
        </Panel>
        {i.impact ? (
          <Panel title="Learning impact" hint={`${i.impact.n} paired participants`} accent={PALETTE.amber}>
            {i.impact.n ? (
              <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
                <div className="space-y-4">
                  <PrePost label="Knowledge" pre={i.impact.knowledge.pre} post={i.impact.knowledge.post} max={100} unit="%" mark={i.impact.pass_mark} />
                  <PrePost label="Confidence (1-5)" pre={i.impact.confidence.pre} post={i.impact.confidence.post} max={5} />
                  <p className="text-[11px] text-slate-500">The white tick is the pass mark ({i.impact.pass_mark}%).</p>
                </div>
                <Donut size={116} centre={num(i.impact.gain)} caption="avg gain"
                  data={[
                    { label: "High (≥ 0.7)", value: i.impact.bands.High, color: PALETTE.emerald },
                    { label: "Medium", value: i.impact.bands.Medium, color: PALETTE.sky },
                    { label: "Low (< 0.3)", value: i.impact.bands.Low, color: PALETTE.amber },
                    { label: "Decline", value: i.impact.bands.Decline, color: PALETTE.rose },
                  ]} />
              </div>
            ) : (
              <p className="text-sm text-slate-500">Results appear once participants finish both the pre and the post assessment.</p>
            )}
          </Panel>
        ) : (
          <Panel title="My learning" accent={PALETTE.amber}>
            <div className="grid grid-cols-3 gap-3 text-center">
              {[["Upcoming", o.mine.upcoming, PALETTE.sky], ["Completed", o.mine.completed, PALETTE.emerald], ["To do", o.mine.open_assessments.length, PALETTE.amber]].map(([l, v, c]) => (
                <div key={l as string} className="rounded-xl bg-slate-950/60 p-3">
                  <p className="text-2xl font-extrabold" style={{ color: c as string }}>{v as number}</p>
                  <p className="text-[11px] text-slate-400">{l as string}</p>
                </div>
              ))}
            </div>
            <ul className="mt-3 space-y-1.5">
              {o.mine.open_assessments.map((a, n) => (
                <li key={n} className="flex items-center gap-2 text-sm">
                  <span className="min-w-0 flex-1 truncate text-slate-300">{a.session} · {a.phase}</span>
                  {a.link && <a href={a.link} className="text-xs font-semibold text-sky-300">Start →</a>}
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>

      {/* ---------- programs, facilitators, feedback ---------- */}
      {i.analyst && (
        <div className="grid gap-4 xl:grid-cols-3">
          <Panel title="Programs by knowledge gain" hint="mean change, points" accent={PALETTE.violet}>
            <RankBars rows={(i.programs ?? []).map((p) => ({ label: p.program, value: p.knowledge_change, note: `n ${p.n}` }))} unit=" pts" />
          </Panel>
          {i.facilitators ? (
            <Panel title="Facilitators" hint="sessions delivered · knowledge change · rating" accent={PALETTE.cyan}>
              {i.facilitators.length ? (
                <ul className="space-y-2">
                  {i.facilitators.map((f, n) => (
                    <li key={f.name} className="flex items-center gap-3">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold text-slate-950"
                        style={{ background: [PALETTE.sky, PALETTE.violet, PALETTE.emerald, PALETTE.amber, PALETTE.rose, PALETTE.cyan][n % 6] }}>
                        {f.name.split(" ").map((w) => w[0]).slice(0, 2).join("")}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-slate-100">{f.name}</span>
                        <span className="text-[11px] text-slate-500">{f.sessions} delivered</span>
                      </span>
                      <span className="text-right">
                        <span className="block text-sm font-bold tabular-nums text-emerald-300">{f.knowledge_change === null ? "—" : `+${f.knowledge_change}`}</span>
                        <span className="text-[11px] text-amber-300">{f.rating === null ? "no rating" : `★ ${f.rating}`}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-slate-500">No completed sessions yet.</p>
              )}
            </Panel>
          ) : (
            <Panel title="My sessions" accent={PALETTE.cyan}>
              <p className="text-3xl font-extrabold text-cyan-300">{o.sessions.i_facilitate}</p>
              <p className="text-xs text-slate-400">sessions you facilitate that are still running or coming up</p>
            </Panel>
          )}
          <Panel title="Participant feedback" hint={i.feedback ? `${i.feedback.n} ratings` : undefined} accent={PALETTE.amber}>
            {i.feedback && i.feedback.n ? (
              <>
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-extrabold text-amber-300">{i.feedback.rating}</span>
                  <span className="text-sm text-slate-400">/ 5 average</span>
                  <span className="ml-auto text-lg tracking-tight text-amber-300" aria-hidden>
                    {"★".repeat(Math.round(i.feedback.rating ?? 0))}
                    <span className="text-slate-700">{"★".repeat(5 - Math.round(i.feedback.rating ?? 0))}</span>
                  </span>
                </div>
                <div className="mt-3">
                  <RatingBars counts={i.feedback.distribution} />
                </div>
              </>
            ) : (
              <p className="text-sm text-slate-500">No feedback yet (anonymous results show once a session has enough responses).</p>
            )}
          </Panel>
        </div>
      )}

      {/* ---------- status mix ---------- */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Panel title="Registrations" accent={PALETTE.sky}>
          <Donut data={[
            { label: "Registered", value: regStatus.registered, color: PALETTE.sky },
            { label: "Attended", value: regStatus.attended, color: PALETTE.emerald },
            { label: "Waitlisted", value: regStatus.waitlisted, color: PALETTE.amber },
            { label: "Nominated", value: regStatus.nominated, color: PALETTE.indigo },
            { label: "No-show", value: regStatus.no_show, color: PALETTE.rose },
            { label: "Cancelled", value: regStatus.cancelled, color: PALETTE.slate },
          ]} />
        </Panel>
        <Panel title="Sessions" accent={PALETTE.violet}>
          <Donut data={[
            { label: "Scheduled", value: sessionStatus.scheduled, color: PALETTE.sky },
            { label: "In progress", value: sessionStatus.in_progress, color: PALETTE.amber },
            { label: "Completed", value: sessionStatus.completed, color: PALETTE.violet },
            { label: "Closed", value: sessionStatus.closed, color: PALETTE.emerald },
            { label: "Draft", value: sessionStatus.draft, color: PALETTE.indigo },
            { label: "Cancelled", value: sessionStatus.cancelled, color: PALETTE.rose },
          ]} />
        </Panel>
        {i.certificates && (
          <Panel title="Facilitator certificates" accent={PALETTE.emerald}>
            <Donut data={[
              { label: "Valid", value: i.certificates.Valid, color: PALETTE.emerald },
              { label: "Expiring", value: i.certificates.Expiring, color: PALETTE.amber },
              { label: "Expired", value: i.certificates.Expired, color: PALETTE.rose },
              { label: "Revoked", value: i.certificates.Revoked, color: PALETTE.slate },
            ]} />
          </Panel>
        )}
        {i.messages && (
          <Panel title="Messages · 14 days" hint="sent · skipped (already done) · failed" accent={PALETTE.cyan}>
            <DailyBars days={i.messages} />
            <p className="mt-2 text-[11px] text-slate-500">
              {i.messages.reduce((s, d) => s + d.sent, 0)} sent · {i.messages.reduce((s, d) => s + d.skipped, 0)} skipped · {i.messages.reduce((s, d) => s + d.failed, 0)} failed
            </p>
          </Panel>
        )}
      </div>

      {/* ---------- attention + upcoming ---------- */}
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Needs attention" accent={PALETTE.rose}>
          {o.alerts.length ? (
            <ul className="space-y-2">
              {o.alerts.map((a, n) => (
                <li key={n} className={`flex items-start gap-2 rounded-lg border-l-4 bg-slate-950/50 px-3 py-2 text-sm ${a.tone === "warn" ? "border-amber-400" : "border-sky-400"}`}>
                  <span className="flex-1 text-slate-300">{a.text}</span>
                  {a.link ? (
                    <a className="text-xs font-semibold text-sky-300" href={a.link}>Open →</a>
                  ) : (
                    go(a.page) && <button type="button" className="text-xs font-semibold text-sky-300" onClick={go(a.page)}>Go →</button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">✓ Nothing needs your attention.</p>
          )}
        </Panel>
        <Panel title="Coming up" accent={PALETTE.sky}>
          {o.sessions.upcoming.length ? (
            <ul className="space-y-2.5">
              {o.sessions.upcoming.map((s) => {
                const pct = s.capacity ? Math.round((100 * s.registered) / s.capacity) : 0;
                const d = new Date(s.starts_at);
                return (
                  <li key={s.id} className="flex items-center gap-3">
                    <span className="flex w-12 shrink-0 flex-col items-center rounded-lg bg-gradient-to-b from-sky-500/30 to-violet-500/20 py-1">
                      <span className="text-[10px] uppercase text-sky-200">{d.toLocaleDateString(undefined, { month: "short" })}</span>
                      <span className="text-lg font-extrabold leading-none text-white">{d.getDate()}</span>
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-slate-100">
                        {s.title}
                        {s.facilitating && <span className="ml-1 rounded bg-cyan-500/20 px-1 text-[10px] text-cyan-200">you facilitate</span>}
                      </span>
                      <span className="mt-1 block h-1.5 rounded-full bg-slate-800">
                        <span className="block h-1.5 rounded-full" style={{ width: `${Math.min(100, pct)}%`, background: pct >= 90 ? PALETTE.amber : `linear-gradient(90deg, ${PALETTE.sky}, ${PALETTE.violet})` }} />
                      </span>
                    </span>
                    <span className="w-16 text-right text-xs tabular-nums text-slate-400">{s.registered}/{s.capacity}</span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">No upcoming sessions.</p>
          )}
        </Panel>
      </div>

      {/* ---------- pages ---------- */}
      <div className="flex flex-wrap gap-2">
        {LEARNING_PAGES.filter((p) => pages.includes(p.id)).map((p) => (
          <button key={p.id} type="button" onClick={go(p.id)} title={p.blurb}
            className="rounded-full border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-sky-500 hover:text-white">
            {p.label} →
          </button>
        ))}
      </div>
    </div>
  );
}
