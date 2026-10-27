import { useCallback, useEffect, useState } from "react";
import { agentAnnotate, assistantJournal, assistantOutcome, assistantUnderstand } from "../../api/client";
import type { AgentRun, AssistantJournalEntry, AssistantUnderstanding, IntentDef } from "../../api/types";
import AgentPanel from "./AgentPanel";
import BuildFromScratch from "./BuildFromScratch";
import ProcessBuilder from "./ProcessBuilder";
import ProcessFitReport from "./ProcessFitReport";

const EXAMPLES: [string, string][] = [
  ["Do one thing", "Add prospect Globex Ltd"],
  ["A chain", 'Create a new Prospect "Globex" with address "5 MG Road" - add a lead for a product "Fiber 500 Bundle" - auto qualify the lead - auto configure with default settings - send it for presales check - auto feasibility pass - generate a quote'],
  ["Set up a process", "Orders need CPMO approval before they become activating. When an order becomes active, show a message box saying the service is live"],
  ["A journey", "Build a B2C process - customer -> identity verification -> raise order -> installation -> service activation -> acceptance -> start billing"],
  ["Design a page", "On the quote page add a mandatory field Discount reason"],
  ["Describe your process", "We sell fibre to enterprise customers. Presales checks feasibility for every opportunity. Every opportunity must have a site contact phone and a site survey date. Quotes with discount above 15% need approval from the CFO. The customer signs an NDA before we share the proposal."],
];

const ROUTE_TONE: Record<AssistantUnderstanding["route"], { chip: string; icon: string }> = {
  answer: { chip: "bg-teal-500/15 text-teal-300", icon: "💬" },
  action: { chip: "bg-sky-500/15 text-sky-300", icon: "✨" },
  chain: { chip: "bg-violet-500/15 text-violet-300", icon: "🤖" },
  process: { chip: "bg-emerald-500/15 text-emerald-300", icon: "🏗️" },
  unknown: { chip: "bg-amber-500/15 text-amber-300", icon: "❔" },
};

function ago(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

/** Ask anything: one box for every request. It is understood and sent to the
 * platform's own pillars - one action (its form prefilled), a chain of actions
 * (the agent: plan, rehearse, run), or a process to set up (the process
 * builder: Flow Studio, Page Designer, custom fields, approvals) - and every
 * request is recorded in the journal with its outcome. */
export default function AssistantPanel({
  intents,
  onAction,
}: {
  intents: IntentDef[];
  /** one action: open its form, prefilled (the journal id records its outcome) */
  onAction: (code: string, entities: Record<string, unknown>, text: string, journalId: number) => void;
}) {
  const [text, setText] = useState("");
  const [understood, setUnderstood] = useState<AssistantUnderstanding | null>(null);
  const [journal, setJournal] = useState<AssistantJournalEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [autonomous, setAutonomous] = useState(false);

  const loadJournal = useCallback(() => {
    assistantJournal().then(setJournal).catch(() => setJournal([]));
  }, []);
  useEffect(loadJournal, [loadJournal]);

  async function ask(request = text) {
    if (request.trim().length < 2 || busy || understood) return;
    setBusy(true);
    setError("");
    setUnderstood(null);
    try {
      const out = await assistantUnderstand(request.trim(), undefined, autonomous);
      if (autonomous && out.route === "action" && out.action) {
        const steps = await agentAnnotate([out.action]);
        setUnderstood({ ...out, route: "chain", label: "Autonomous action", plan: {
          goal: out.request, planner: "rules", steps, notes: [],
        } });
      } else {
        setUnderstood(out);
        if (out.route === "action" && out.action) onAction(out.action.intent_type, out.action.entities, out.action.text, out.journal_id);
      }
      loadJournal();
    } catch (e) {
      setError(e instanceof Error ? e.message : "I couldn't work that out");
    } finally {
      setBusy(false);
    }
  }

  function record(status: string, summary: string) {
    if (!understood) return;
    assistantOutcome(understood.journal_id, status, summary).then(loadJournal).catch(() => undefined);
  }

  const tone = understood ? ROUTE_TONE[understood.route] : null;

  return (
    <div className="space-y-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask();
        }}
        className="space-y-3"
      >
        <label className="flex items-start gap-2 rounded-lg border border-violet-500/30 bg-violet-500/5 p-3 text-sm text-slate-200">
          <input type="checkbox" checked={autonomous} disabled={busy || understood !== null}
            onChange={(event) => setAutonomous(event.target.checked)} className="mt-1 accent-violet-500" />
          <span>Autonomous mode
            <span className="block text-xs text-slate-400">Run validated actions and build ready processes automatically. Ask for missing information; keep role permissions and approvals.</span>
          </span>
        </label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              ask();
            }
          }}
          rows={3}
          placeholder="Ask for anything in plain English - do something, a chain of steps, a process or journey to set up, a page or field change…"
          className="w-full resize-y rounded-xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-[15px] leading-relaxed text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-xs font-medium text-slate-500">Try:</span>
          {EXAMPLES.map(([label, example]) => (
            <button
              key={label}
              type="button"
              title={example}
              onClick={() => {
                setText(example);
                ask(example);
              }}
              className="rounded-full border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 hover:border-sky-500 hover:text-slate-200"
            >
              {label}
            </button>
          ))}
          <button
            type="submit"
            disabled={text.trim().length < 2 || busy || understood !== null}
            className="ml-auto rounded-lg bg-sky-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-sky-950/40 hover:bg-sky-500 disabled:opacity-40"
          >
            {busy ? "Understanding…" : "Go"}
          </button>
        </div>
      </form>

      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</p>}

      {understood && tone && (
        <section className="space-y-5">
          <button type="button" onClick={() => setUnderstood(null)}
            className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300">
            Start another request (stop further automatic steps)
          </button>
          <div className="flex flex-wrap items-start gap-3 rounded-xl border border-slate-800 bg-slate-900/50 px-4 py-3">
            <span className="text-xl leading-none">{tone.icon}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-200">
                <span className={`mr-2 rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone.chip}`}>{understood.label}</span>
                {understood.memory && <span className="mr-2 rounded-full bg-slate-800 px-2 py-0.5 text-[11px] font-semibold text-slate-300">🧠 from memory</span>}
                {understood.why}
              </p>
            </div>
          </div>

          {understood.route === "chain" && understood.plan && (
            <AgentPanel
              key={understood.journal_id}
              bare
              autonomous={autonomous}
              intents={intents}
              initialGoal={understood.request}
              initialPlan={understood.plan}
              onRun={(run: AgentRun) => {
                if (run.status === "done" || run.mode === "execute") record(run.status, run.message);
              }}
            />
          )}
          {understood.fit && (
            <ProcessFitReport fit={understood.fit} intents={intents} canBuild={!understood.needs_admin} onBuilt={(summary) => record("built", summary)} />
          )}
          {understood.route === "process" && !understood.needs_admin && (!understood.fit || understood.fit.journey || understood.fit.build_steps.length > 0) && (
            <ProcessBuilder key={understood.journal_id} intents={intents} initialText={understood.request} autoDesign autonomous={autonomous} onFinished={record}
              initialSteps={understood.fit && !understood.fit.journey ? understood.fit.build_steps : undefined} />
          )}
          {understood.route === "unknown" && (understood.build_offer || understood.can_build) && (
            <BuildFromScratch
              key={understood.journal_id}
              request={understood.request}
              draft={understood.build_offer}
              autonomous={autonomous}
              journalId={understood.journal_id}
              onBuilt={(built) => {
                loadJournal();
                // straight into the new action's form, with what was said filled in
                onAction(built.code, {}, understood.request, understood.journal_id);
              }}
            />
          )}
          {understood.route === "action" && <p className="text-sm text-slate-400">Opening “{understood.action?.label}” with what I could fill in…</p>}
          {understood.route === "answer" && (understood.answers ?? []).length > 0 && (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {(understood.answers ?? []).map((a, i) => (
                <article key={a.intent_type} className={`flex flex-col rounded-xl border p-4 ${i === 0 ? "border-teal-500/40 bg-teal-500/[0.05]" : "border-slate-800 bg-slate-900/40"}`}>
                  <div className="flex items-start gap-3">
                    <span className="text-xl leading-none">{a.icon}</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-slate-100">
                        <span className="mr-1.5 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-sky-300">{a.intent_type}</span>
                        {a.label}
                      </p>
                      <p className="mt-1 text-xs leading-relaxed text-slate-400">{a.description}</p>
                    </div>
                  </div>
                  <button
                    onClick={() => onAction(a.intent_type, a.entities, a.text, understood.journal_id)}
                    className={`mt-3 self-start rounded-lg px-3 py-1.5 text-xs font-semibold ${i === 0 ? "bg-teal-600 text-white hover:bg-teal-500" : "border border-slate-700 text-slate-300 hover:border-teal-500/60"}`}
                  >
                    Do it now →
                  </button>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {/* ---------- the journal ---------- */}
      {journal.length > 0 && (
        <section className="space-y-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Your requests</h4>
          <ul className="divide-y divide-slate-800 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/30">
            {journal.slice(0, 8).map((j) => {
              const t = ROUTE_TONE[(j.route ?? "unknown") as AssistantUnderstanding["route"]] ?? ROUTE_TONE.unknown;
              const ok = j.outcome && ["done", "built", "executed"].includes(j.outcome.status);
              return (
                <li key={j.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <span className="text-base leading-none">{t.icon}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-slate-200" title={j.request}>
                      {j.request}
                    </span>
                    <span className="block text-[11px] text-slate-500">
                      {j.label} · {ago(j.at)}
                      {j.memory ? " · from memory" : ""}
                      {j.outcome ? (
                        <span className={ok ? "text-emerald-400" : "text-amber-400"}> · {ok ? "✓" : "⚠"} {j.outcome.summary || j.outcome.status}</span>
                      ) : (
                        <span> · not run yet</span>
                      )}
                    </span>
                  </span>
                  <button
                    onClick={() => {
                      setText(j.request);
                      ask(j.request);
                    }}
                    className="text-xs font-semibold text-sky-400 opacity-80 hover:opacity-100"
                  >
                    Again →
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
