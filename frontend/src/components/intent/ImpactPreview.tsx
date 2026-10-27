import type { ReactNode } from "react";
import type { IntentResponse, RehearsalImpact } from "../../api/types";

const show = (v: unknown) => (v === null || v === undefined || v === "" ? "-" : typeof v === "number" ? v.toLocaleString() : String(v));
const state = (s: string | null) => (s ? s.replace(/_/g, " ") : "new");
const by = (source: string) => (source.startsWith("flow") ? "automation" : source.startsWith("rule") ? "assignment rule" : source.startsWith("intent") ? "this action" : source);

function Section({ icon, title, count, children }: { icon: string; title: string; count: number; children: ReactNode }) {
  if (!count) return null;
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
      <p className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-400">
        {icon} {title} <span className="ml-1 rounded-full bg-slate-800 px-1.5 text-slate-300">{count}</span>
      </p>
      <ul className="space-y-1.5 text-sm text-slate-200">{children}</ul>
    </div>
  );
}

/** The rehearsal of an intent: everything it would do, worked out by
 * actually running it and taking it all back (POST /api/intents/rehearse). */
export default function ImpactPreview({
  response,
  onRun,
  onClose,
  running,
}: {
  response: IntentResponse;
  onRun: () => void;
  onClose: () => void;
  running: boolean;
}) {
  const impact = response.impact;
  if (!impact) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
        <p className="font-semibold">🔮 The rehearsal stopped before anything would happen.</p>
        <p className="mt-1 text-amber-200/80">{response.summary || response.rationale}</p>
        <button type="button" onClick={onClose} className="mt-2 text-xs font-semibold text-amber-100 underline">
          Close
        </button>
      </div>
    );
  }
  return (
    <div className="space-y-3 rounded-2xl border border-violet-500/40 bg-gradient-to-b from-violet-950/40 to-slate-950 p-4">
      <div className="flex flex-wrap items-start gap-3">
        <span className="text-2xl">🔮</span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-bold uppercase tracking-wide text-violet-300">Rehearsed on your live data - nothing was kept</p>
          <p className="mt-1 text-base font-semibold text-slate-100">{impact.headline}</p>
        </div>
        <span className="rounded-full border border-violet-400/40 px-2.5 py-1 text-xs font-semibold text-violet-200" title="Records this action touches">
          Blast radius: {impact.blast_radius}
        </span>
      </div>

      <ImpactDetails impact={impact} />

      <div className="flex flex-wrap items-center justify-end gap-3 border-t border-slate-800 pt-3">
        <button type="button" onClick={onClose} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300 hover:border-slate-500">
          Not now
        </button>
        <button
          type="button"
          onClick={onRun}
          disabled={running}
          className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-bold text-white shadow-lg shadow-emerald-900/40 hover:bg-emerald-500 disabled:opacity-40"
        >
          {running ? "Working…" : "Looks right - run it for real"}
        </button>
      </div>
    </div>
  );
}

/** The consequences, section by section - shared with the agent's plan rehearsal. */
export function ImpactDetails({ impact }: { impact: RehearsalImpact }) {
  return (
    <>
      {impact.warnings.map((w) => (
        <p key={w} className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
          ⚠ {w}
        </p>
      ))}

      <div className="grid gap-3 md:grid-cols-2">
        <Section icon="🔀" title="States that move" count={impact.state_moves.length}>
          {impact.state_moves.map((m, i) => (
            <li key={i}>
              <span className="font-semibold">{m.record}</span>: {state(m.from)} → <span className="text-emerald-300">{state(m.to)}</span>
              <span className="text-xs text-slate-500"> · by {by(m.by)}</span>
            </li>
          ))}
        </Section>
        <Section icon="👥" title="Who gets the work" count={impact.assignments.length}>
          {impact.assignments.map((a, i) => (
            <li key={i}>
              <span className="font-semibold">{a.record}</span> → <span className="text-sky-300">{a.to}</span>
              <span className="text-xs text-slate-500"> · by {by(a.by)}</span>
            </li>
          ))}
        </Section>
        <Section icon="✅" title="Approvals" count={impact.approvals.length}>
          {impact.approvals.map((a, i) => (
            <li key={i}>
              <span className="font-semibold">{a.record}</span> - {a.status}
              {a.tiers.length > 0 && <span className="text-slate-400"> · waits for {a.tiers.join(" → ")}</span>}
            </li>
          ))}
        </Section>
        <Section icon="⚙️" title="Automations that fire" count={impact.flows.length}>
          {impact.flows.map((f, i) => (
            <li key={i}>
              {f.name} <span className="text-xs text-slate-500">· {f.steps} steps</span>
            </li>
          ))}
        </Section>
        <Section icon="🔔" title="Who is told" count={impact.notifications.length}>
          {impact.notifications.map((n, i) => (
            <li key={i}>
              <span className="font-semibold">{n.audience}</span>
              {n.channel && <span className="text-xs text-slate-500"> ({n.channel})</span>}: <span className="text-slate-300">{n.message}</span>
            </li>
          ))}
        </Section>
        <Section icon="₹" title="Money that changes" count={impact.money.length}>
          {impact.money.map((m, i) => (
            <li key={i}>
              {m.record} · {m.field}: {show(m.from)} → <span className="text-emerald-300">{show(m.to)}</span>
            </li>
          ))}
        </Section>
        <Section icon="✨" title="New records" count={impact.records_created.length}>
          {impact.records_created.map((r, i) => (
            <li key={i}>{r.label.replace(/ #\d+/, "")}</li>
          ))}
        </Section>
        <Section icon="✏️" title="Records that change" count={impact.records_changed.length}>
          {impact.records_changed.map((r, i) => (
            <li key={i}>
              <span className="font-semibold">{r.label}</span>
              <span className="text-slate-400"> · {r.fields.map((f) => `${f.field}: ${show(f.from)} → ${show(f.to)}`).join(", ")}</span>
            </li>
          ))}
        </Section>
        <Section icon="🗑" title="Records removed" count={impact.records_deleted.length}>
          {impact.records_deleted.map((r, i) => (
            <li key={i}>{r.label}</li>
          ))}
        </Section>
      </div>
    </>
  );
}
