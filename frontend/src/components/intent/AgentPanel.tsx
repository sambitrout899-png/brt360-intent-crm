import { useEffect, useRef, useState } from "react";
import { agentAnnotate, agentPlan, agentRun } from "../../api/client";
import type { Conflict } from "../../api/conflictTypes";
import type { AgentPlan, AgentRun, AgentStep, IntentDef } from "../../api/types";
import { notifyIntentExecuted } from "../../workspace";
import BuildFromScratch from "./BuildFromScratch";
import ConflictsPanel from "./ConflictsPanel";
import { ImpactDetails } from "./ImpactPreview";
import IntentForm from "./IntentForm";
import { isEmpty } from "./intentUi";

const EXAMPLES = [
  "Add prospect Globex, ops@globex.com, then generate a lead for them interested in Fiber 800, then approve the lead",
  "Approve quote 5, then generate the proposal, then record customer approval of the proposal",
  "Approve order 12, then complete the installation for its work order",
];

const nice = (key: string) => key.replace(/_id$/, "").replace(/_/g, " ");
const show = (v: unknown) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));
const STEP_TONE: Record<string, string> = {
  done: "border-emerald-500/40 bg-emerald-500/5",
  needs_review: "border-amber-500/40 bg-amber-500/5",
  failed: "border-red-500/40 bg-red-500/5",
  skipped: "border-amber-500/40 bg-amber-500/5",
};

/** The agent: one goal in plain words, carried out as a chain of the
 * user's own actions. It plans (rules first, then the tenant's AI model),
 * passes what each step creates to the next, rehearses the whole chain on
 * live data with nothing kept, and only then runs it - all steps together,
 * or none. */
export default function AgentPanel({
  intents,
  bare = false,
  initialGoal = "",
  initialPlan,
  onRun,
  autonomous = false,
}: {
  intents: IntentDef[];
  bare?: boolean;
  /** a goal (and its plan) handed over - e.g. by the Process Builder */
  initialGoal?: string;
  initialPlan?: AgentPlan;
  /** told after each rehearsal or run - for the request journal */
  onRun?: (run: AgentRun) => void;
  autonomous?: boolean;
}) {
  const [goal, setGoal] = useState(initialGoal);
  const [steps, setSteps] = useState<AgentStep[]>(initialPlan?.steps ?? []);
  const [planner, setPlanner] = useState<AgentPlan["planner"] | null>(initialPlan?.planner ?? null);
  const [notes, setNotes] = useState<string[]>(initialPlan?.notes ?? []);
  const [outcome, setOutcome] = useState<{ key: string; run: AgentRun } | null>(null);
  const [busy, setBusy] = useState<"" | "plan" | "rehearse" | "execute">("");
  const [error, setError] = useState("");
  // the pre-run scan of the plan (conflicts it touches); a rehearsal or run brings a fresh one
  const [planScan, setPlanScan] = useState<{ conflicts: Conflict[]; can_settle: boolean }>({
    conflicts: initialPlan?.conflicts ?? [],
    can_settle: !!initialPlan?.can_settle,
  });
  // the input pop-up: the step that needs details, and the run to carry on with afterwards
  const [asking, setAsking] = useState<{ n: number; then: "rehearse" | "execute"; note?: string } | null>(null);
  // a step nothing covers, being built from scratch
  const [building, setBuilding] = useState<number | null>(null);
  const autoStarted = useRef(false);
  const repairsUsed = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!autonomous || !initialPlan || autoStarted.current) return;
    autoStarted.current = true;
    void go("rehearse", initialPlan.steps);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stepsIn = (list: AgentStep[] = steps) => list.map((s) => ({ intent_type: s.intent_type, entities: s.entities, text: s.text }));
  const planKey = JSON.stringify(stepsIn());
  const rehearsed = outcome?.key === planKey && outcome.run.status === "rehearsed";
  const run = outcome?.key === planKey ? outcome.run : null;
  const canSettle = planScan.can_settle || !!run?.can_settle;
  const conflicts = run?.conflicts ?? planScan.conflicts;
  const unsettled = conflicts.filter((c) => !c.resolved && c.severity !== "low").length;

  async function act<T>(what: typeof busy, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(what);
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy("");
    }
  }

  async function makePlan(text = goal) {
    if (text.trim().length < 3) return;
    const plan = await act("plan", () => agentPlan(text.trim()));
    if (plan) {
      setSteps(plan.steps);
      setPlanner(plan.planner);
      setNotes(plan.notes ?? []);
      setPlanScan({ conflicts: plan.conflicts ?? [], can_settle: !!plan.can_settle });
      setOutcome(null);
    }
  }

  async function reannotate(list: AgentStep[]) {
    setSteps(list);
    const fresh = await act("plan", () => agentAnnotate(stepsIn(list)));
    if (fresh) setSteps(fresh);
  }

  // details only the user can give: required, missing, and no earlier step supplies them
  const ownAsk = (s: AgentStep) => s.needs.filter((x) => x.from_step === null && !x.field.includes(" or ")).map((x) => x.field);

  /** Rehearse or run. A step that still needs details from the user opens the input pop-up first
   * (skipAsk: the user chose to carry on anyway); a run that pauses for input opens it too. */
  async function go(mode: "rehearse" | "execute", list: AgentStep[] = steps, skipAsk = false) {
    if (!mounted.current) return;
    if (!skipAsk) {
      const first = list.find((s) => s.intent_type && ownAsk(s).some((f) => isEmpty(s.entities[f])));
      if (first) {
        setAsking({ n: first.n, then: mode });
        return;
      }
    }
    const key = JSON.stringify(stepsIn(list));
    const result = await act(mode, () => agentRun(goal, stepsIn(list), mode));
    if (result) {
      setOutcome({ key, run: result });
      if (result.status === "done") notifyIntentExecuted();
      onRun?.(result);
      if (result.status === "paused" && result.stopped?.kind === "needs_input") {
        setAsking({ n: result.stopped.step, then: mode, note: result.stopped.message });
      }
      if (!mounted.current) return;
      if (autonomous && mode === "rehearse" && result.status === "rehearsed") {
        await go("execute", list);
      } else if (autonomous && mode === "rehearse" && result.stopped?.repairs?.length === 1 && repairsUsed.current < 2) {
        repairsUsed.current += 1;
        const repair = result.stopped.repairs[0];
        const current = stepsIn(list);
        const at = Math.max(0, repair.at - 1);
        const fresh = await act("plan", () => agentAnnotate([...current.slice(0, at), ...repair.insert, ...current.slice(at)]));
        if (fresh) {
          setSteps(fresh);
          await go("rehearse", fresh);
        }
      }
    }
  }

  /** The pop-up's values go into the step; the run carries on (asking the next step that needs something). */
  function applyValues(n: number, entities: Record<string, unknown>, complete: boolean) {
    const step = steps.find((s) => s.n === n);
    const supplied = new Set((step?.needs ?? []).filter((x) => x.from_step !== null).map((x) => x.field));
    // what an earlier step makes is passed on when the steps run - a guess here must not replace it
    const kept = Object.fromEntries(Object.entries(entities).filter(([k]) => !supplied.has(k) || !isEmpty(step?.entities[k])));
    const list = steps.map((s) => (s.n === n ? { ...s, entities: kept } : s));
    const then = asking?.then;
    setSteps(list);
    setAsking(null);
    if (then) go(then, list, !complete);
  }

  /** The user accepted a repair: its steps go in before the blocked one, and the plan is rehearsed again. */
  async function applyRepair(repair: NonNullable<NonNullable<AgentRun["stopped"]>["repairs"]>[number]) {
    const current = stepsIn();
    const at = Math.max(0, repair.at - 1);
    const list = [...current.slice(0, at), ...repair.insert, ...current.slice(at)];
    const fresh = await act("plan", () => agentAnnotate(list));
    if (!fresh) return;
    setSteps(fresh);
    const key = JSON.stringify(stepsIn(fresh));
    const result = await act("rehearse", () => agentRun(goal, stepsIn(fresh), "rehearse"));
    if (result) setOutcome({ key, run: result });
  }

  // fields the user has to give: missing with no earlier step to supply them, or what a paused step asked for
  const askFor = (s: AgentStep) => {
    const asked = run?.stopped?.step === s.n ? run.stopped.missing_fields ?? [] : [];
    return [...new Set([...ownAsk(s), ...asked])].filter((f) => !f.includes(" or ") && isEmpty(s.entities[f]));
  };

  return (
    <section className={bare ? "space-y-5" : "space-y-4 rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/50 via-slate-900 to-slate-950 p-5 shadow-xl"}>
      {!bare && (
      <div className="flex flex-wrap items-start gap-3">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-violet-600/30 text-2xl">🤖</span>
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-bold text-slate-100">Agent - give me a goal, I'll do the steps</h2>
          <p className="text-sm text-slate-400">
            I plan it with your own actions, pass what each step creates to the next, rehearse the whole chain on your live data with nothing kept - and run it only when you say so. All steps happen together, or none.
          </p>
        </div>
      </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          makePlan();
        }}
        className="space-y-2"
      >
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          rows={bare ? 3 : 2}
          placeholder="e.g. Add prospect Globex, ops@globex.com, then generate a lead for them interested in Fiber 800, then approve the lead"
          className={`w-full resize-none rounded-xl border border-slate-700 bg-slate-950/80 text-slate-100 placeholder:text-slate-600 focus:border-violet-500 focus:outline-none ${bare ? "px-4 py-3 text-[15px] leading-relaxed" : "px-3 py-2 text-sm"}`}
        />
        <div className={`flex flex-wrap items-center gap-2 ${bare ? "pt-1" : ""}`}>
          {bare && <span className="mr-1 text-xs font-medium text-slate-500">Try:</span>}
          {EXAMPLES.map((x) => (
            <button
              key={x}
              type="button"
              onClick={() => {
                setGoal(x);
                makePlan(x);
              }}
              className="max-w-xs truncate rounded-full border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 hover:border-violet-500 hover:text-slate-200"
              title={x}
            >
              {x}
            </button>
          ))}
          <button
            type="submit"
            disabled={goal.trim().length < 3 || busy !== ""}
            className="ml-auto rounded-lg bg-violet-600 px-4 py-2 text-sm font-bold text-white hover:bg-violet-500 disabled:opacity-40"
          >
            {busy === "plan" ? "Planning…" : "Plan it"}
          </button>
        </div>
      </form>

      {steps.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-bold uppercase tracking-wide text-slate-500">
            The plan · {steps.length} step{steps.length > 1 ? "s" : ""} · planned by {planner === "ai" ? "your AI model" : planner === "memory" ? "memory - the plan that worked last time" : "the built-in rules"}
          </p>
          {notes.length > 0 && (
            <ul className="space-y-1 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs text-slate-400">
              {notes.map((n) => (
                <li key={n}>ℹ️ {n}</li>
              ))}
            </ul>
          )}
          {steps.map((s) => {
            const done = run?.steps.find((r) => r.n === s.n);
            return (
              <div key={s.n} className={`rounded-xl border p-3 ${done ? STEP_TONE[done.status] ?? "border-slate-700" : "border-slate-800 bg-slate-950/50"}`}>
                <div className="flex flex-wrap items-start gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-800 text-xs font-bold text-slate-300">{s.n}</span>
                  <span className="text-xl">{s.icon}</span>
                  <div className="min-w-0 flex-1">
                    {s.intent_type ? (
                      <p className="text-sm font-semibold text-slate-100">{s.label}</p>
                    ) : (
                      <select
                        value=""
                        onChange={(e) => reannotate(steps.map((x) => (x.n === s.n ? { ...x, intent_type: e.target.value, entities: {} } : x)))}
                        className="rounded-lg border border-amber-500/50 bg-slate-950 px-2 py-1 text-sm text-amber-200"
                      >
                        <option value="">Choose the action for this step…</option>
                        {intents.map((i) => (
                          <option key={i.code} value={i.code}>
                            {i.icon} {i.label}
                          </option>
                        ))}
                      </select>
                    )}
                    <p className="text-xs text-slate-500">“{s.text}”</p>
                    {Object.values(s.entities).some((v) => v !== "" && v !== null) && (
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {Object.entries(s.entities).filter(([, v]) => v !== "" && v !== null).map(([k, v]) => (
                          <span key={k} className="rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
                            {nice(k)}: <span className="text-slate-100">{show(v)}</span>
                          </span>
                        ))}
                      </div>
                    )}
                    {s.needs.filter((x) => x.from_step !== null).map((x) => (
                      <p key={x.field} className="mt-1 text-[11px] text-violet-300">
                        ↳ {nice(x.field)} comes from step {x.from_step === 0 ? "the record you're on" : x.from_step}
                      </p>
                    ))}
                    {s.intent_type && askFor(s).length > 0 && (
                      <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/[0.07] px-2.5 py-1.5 text-xs text-red-200">
                        <span>Needs from you: {askFor(s).map(nice).join(", ")}</span>
                        <button
                          type="button"
                          onClick={() => setAsking({ n: s.n, then: "rehearse" })}
                          className="ml-auto rounded-md bg-red-500/20 px-2 py-0.5 font-bold text-red-100 hover:bg-red-500/30"
                        >
                          ✎ Fill in
                        </button>
                      </div>
                    )}
                    {s.intent_type && askFor(s).length === 0 && (
                      <button type="button" onClick={() => setAsking({ n: s.n, then: "rehearse" })} className="mt-1.5 text-[11px] text-slate-500 hover:text-sky-300">
                        ✎ Review the details
                      </button>
                    )}
                    {!s.intent_type && (
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                        {canSettle ? (
                          <button
                            type="button"
                            onClick={() => setBuilding(s.n)}
                            className="rounded-md border border-emerald-500/50 bg-emerald-500/10 px-2.5 py-1 font-semibold text-emerald-200 hover:bg-emerald-500/20"
                          >
                            🏗️ Nothing does this yet - build it as a new action
                          </button>
                        ) : (
                          <span className="text-slate-500">Nothing does this yet - an administrator can have it built from scratch.</span>
                        )}
                      </div>
                    )}
                    {done && <p className="mt-1.5 text-xs text-slate-300">{done.status === "done" ? "✓ " : "⚠ "}{done.summary}</p>}
                    {done?.filled_from_memory && done.filled_from_memory.length > 0 && (
                      <p className="text-[11px] text-violet-300">Passed on: {done.filled_from_memory.map(nice).join(", ")}</p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => reannotate(steps.filter((x) => x.n !== s.n))}
                    className="text-xs text-slate-500 hover:text-red-400"
                    title="Drop this step"
                  >
                    ✕
                  </button>
                </div>
              </div>
            );
          })}

          {run && (
            <div className={`rounded-xl border p-3 text-sm ${run.status === "paused" ? "border-amber-500/40 bg-amber-500/10 text-amber-100" : "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"}`}>
              <p className="font-semibold">
                {run.status === "done" ? "✅ " : run.status === "rehearsed" ? "🔮 " : "⏸ "}
                {run.message}
              </p>
              {run.status === "paused" && run.stopped?.repairs?.map((r) => (
                <div key={r.id} className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="text-xs text-amber-200/80">I'd add before step {r.at}:</span>
                  {r.insert.map((s, i) => (
                    <span key={i} className="rounded-md bg-slate-900/80 px-2 py-0.5 text-xs text-slate-200">
                      {i + 1}. {intents.find((x) => x.code === s.intent_type)?.label ?? s.text}
                    </span>
                  ))}
                  <button
                    type="button"
                    onClick={() => applyRepair(r)}
                    disabled={busy !== ""}
                    className="ml-auto rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-bold text-slate-950 hover:bg-amber-400 disabled:opacity-40"
                  >
                    {busy === "plan" || busy === "rehearse" ? "Adding…" : "Yes - add them and rehearse again"}
                  </button>
                </div>
              ))}
            </div>
          )}
          {run && run.status !== "paused" && (
            <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/40 p-3">
              <ImpactDetails impact={run.impact} />
            </div>
          )}

          <ConflictsPanel
            conflicts={conflicts}
            canSettle={canSettle}
            title={run ? "Conflicts this run touches" : "Pre-run scan - conflicts this plan touches"}
            onChanged={() => go("rehearse")}
            onDropStep={(n) => reannotate(steps.filter((x) => x.n !== n))}
          />

          <div className="flex flex-wrap items-center justify-end gap-3 pt-1">
            {unsettled > 0 && (
              <span className="text-xs text-amber-300">
                ⚖️ {unsettled} conflict{unsettled > 1 ? "s" : ""} not settled - it runs in the current order{canSettle ? "" : " (an administrator can set the priority)"}.
              </span>
            )}
            {!rehearsed && run?.status !== "done" && <span className="text-xs text-slate-500">Rehearse first - then you can run it.</span>}
            <button
              type="button"
              onClick={() => go("rehearse")}
              disabled={busy !== ""}
              className="rounded-lg border border-violet-500/50 bg-violet-500/10 px-4 py-2 text-sm font-semibold text-violet-200 hover:bg-violet-500/20 disabled:opacity-40"
            >
              {busy === "rehearse" ? "Rehearsing…" : "🔮 Rehearse the plan"}
            </button>
            <button
              type="button"
              onClick={() => go("execute")}
              disabled={!rehearsed || busy !== ""}
              className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-bold text-white shadow-lg shadow-emerald-900/40 hover:bg-emerald-500 disabled:opacity-40"
            >
              {busy === "execute" ? "Working…" : "▶ Run all steps"}
            </button>
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* the input pop-up: the step's form, prefilled - defaults, history, the customer, best guesses */}
      {asking && (() => {
        const s = steps.find((x) => x.n === asking.n);
        if (!s?.intent_type) return null;
        return (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setAsking(null)}>
            <div className="max-h-[92vh] w-full max-w-3xl space-y-3 overflow-y-auto" onClick={(e) => e.stopPropagation()}>
              <div className="rounded-xl border border-violet-500/30 bg-slate-900 px-4 py-3 text-sm text-slate-200">
                <b>Step {s.n} of {steps.length}</b> - {asking.note ?? "a few details before I go on. I filled in what I could; anything marked red is still needed."}
                <span className="block text-xs text-slate-400">
                  {asking.then === "execute" ? "Then the run carries on." : "Then the rehearsal carries on - nothing is kept."}
                  {s.needs.some((x) => x.from_step !== null) &&
                    ` ${s.needs.filter((x) => x.from_step !== null).map((x) => nice(x.field)).join(", ")} come${s.needs.filter((x) => x.from_step !== null).length > 1 ? "" : "s"} from an earlier step.`}
                </span>
              </div>
              <IntentForm
                key={`${s.n}-${s.intent_type}`}
                code={s.intent_type}
                initialEntities={s.entities}
                text={s.text}
                supplied={s.needs.filter((x) => x.from_step !== null).map((x) => x.field)}
                onDone={() => setAsking(null)}
                onCancel={() => setAsking(null)}
                onUse={(entities, complete) => applyValues(s.n, entities, complete)}
              />
            </div>
          </div>
        );
      })()}

      {building !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setBuilding(null)}>
          <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <BuildFromScratch
              request={steps.find((x) => x.n === building)?.text ?? ""}
              onCancel={() => setBuilding(null)}
              onBuilt={(built) => {
                const n = building;
                setBuilding(null);
                reannotate(steps.map((x) => (x.n === n ? { ...x, intent_type: built.code, entities: {} } : x)));
              }}
            />
          </div>
        </div>
      )}
    </section>
  );
}
