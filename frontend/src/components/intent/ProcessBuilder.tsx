import { useEffect, useRef, useState } from "react";
import { processBuild, processDesign, processRecipes } from "../../api/client";
import type { IntentDef, ProcessBlueprint, ProcessBuild, ProcessInput, ProcessQuestion } from "../../api/types";
import { notifyIntentExecuted } from "../../workspace";
import AgentPanel from "./AgentPanel";

const EXAMPLES = [
  "Build a B2C process - customer -> identity verification -> raise order -> installation -> service activation on network -> connectivity testing -> acceptance -> start billing -> installed asset",
  "Set up a discount approval process. Quotes with discount above 20% need approval from the Regional Sales Head, then the Finance Controller. Once approved, notify the account manager that the quote is cleared. On the quote page add a mandatory field Discount reason.",
  "Orders need CPMO approval before they become activating. When an order becomes active, notify the account manager that the service is live.",
  "Assign all new leads to Inside Sales round robin. Leads need Sales Director approval before they are qualified.",
];

const ENGINE_TONE: Record<string, string> = {
  approval: "border-emerald-500/30 bg-emerald-500/[0.04]",
  flow: "border-violet-500/30 bg-violet-500/[0.04]",
  page: "border-sky-500/30 bg-sky-500/[0.04]",
  assignment: "border-amber-500/30 bg-amber-500/[0.04]",
};

/** "Set up a process" in plain words: the builder splits it across the
 * engines (approvals, Flow Studio, Page Designer, assignment rules), stops to
 * ask what it can't tell, rehearses the whole build with nothing kept, and
 * builds it - all or nothing - only on the administrator's consent. What no
 * engine can do comes back as an engineering change, evaluated. */
export default function ProcessBuilder({
  initialText = "",
  intents,
  autoDesign = false,
  onFinished,
  autonomous = false,
  initialSteps,
}: {
  initialText?: string;
  /** only these pieces - what a vanilla process lacks (process fit) - instead of reading the whole text again */
  initialSteps?: { kind: string; text: string }[];
  intents: IntentDef[];
  /** start designing initialText at once (opened from Ask anything) */
  autoDesign?: boolean;
  /** told when a build finishes - for the request journal */
  onFinished?: (status: string, summary: string) => void;
  autonomous?: boolean;
}) {
  const [recipes, setRecipes] = useState<{ id: number; name: string; request: string }[]>([]);
  const [memoryError, setMemoryError] = useState("");
  useEffect(() => {
    let active = true;
    processRecipes().then((items) => { if (active) setRecipes(items); })
      .catch(() => { if (active) setMemoryError("Process memory could not be loaded."); });
    return () => { active = false; };
  }, []);
  const started = useRef(false);
  useEffect(() => {
    if (!autoDesign || started.current || initialText.trim().length < 5) return;
    started.current = true;
    void Promise.resolve().then(() => design(initialText));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, for the text it was opened with
  }, []);
  const [text, setText] = useState(initialText);
  const [blueprint, setBlueprint] = useState<ProcessBlueprint | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [rehearsal, setRehearsal] = useState<ProcessBuild | null>(null);
  const [built, setBuilt] = useState<ProcessBuild | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState<"" | "design" | "rehearse" | "build">("");
  const [error, setError] = useState("");
  // decisions taken from my own suggestions - shown, and changeable
  const [decided, setDecided] = useState<{ prompt: string; answer: string }[]>([]);
  const [reviewing, setReviewing] = useState(false);
  const autoBuilt = useRef(new Set<string>());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!autonomous || busy || !blueprint?.ready || blueprint.engineering.length || built) return;
    const key = JSON.stringify({ blueprint, answers });
    if (autoBuilt.current.has(key)) return;
    autoBuilt.current.add(key);
    void run("execute");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autonomous, busy, blueprint, answers, built]);

  const input = (bp: ProcessBlueprint | null, a: Record<string, string>): ProcessInput => ({
    request: text.trim(),
    name: bp?.name,
    steps: bp?.steps,
    answers: { ...bp?.answers, ...a },
    graphs: Object.fromEntries((bp?.components ?? []).filter((c) => c.kind === "flow").map((c) => [c.id, c.spec.graph])),
  });

  function reset() {
    setBlueprint(null);
    setAnswers({});
    setDraft({});
    setDecided([]);
    setReviewing(false);
    setRehearsal(null);
    setBuilt(null);
    setConsent(false);
    setError("");
  }

  async function design(request = text, given?: Record<string, string>, keep?: ProcessBlueprint | null, auto = true) {
    if (request.trim().length < 5) return;
    setBusy("design");
    setError("");
    setRehearsal(null);
    setConsent(false);
    try {
      let a = { ...(given ?? {}) };
      const first = initialSteps?.length && request === initialText ? { steps: initialSteps } : {};
      let bp = await processDesign(keep ? { ...input(keep, a), request: request.trim(), graphs: {} } : { request: request.trim(), answers: a, ...first });
      a = { ...bp.answers, ...a };
      // Decisions I can make myself (every one with a suggested answer) are made - only the rest are asked.
      const made: { prompt: string; answer: string }[] = given ? [...decided] : [];
      for (let round = 0; round < 4; round++) {
        const sure = bp.questions.filter((q) => q.default && !(q.id in a));
        if (!auto || sure.length === 0 || sure.length !== bp.questions.length) break;
        for (const q of sure) {
          a = { ...a, [q.id]: q.default as string };
          made.push({ prompt: q.prompt, answer: q.options.find((o) => o.value === q.default)?.label ?? String(q.default) });
        }
        bp = await processDesign({ ...input(bp, a), request: request.trim(), graphs: {} });
      }
      setDecided(made);
      setBlueprint(bp);
      setAnswers(a);
      // what still needs the user: the suggestion (if any) pre-selected
      setDraft(Object.fromEntries(bp.questions.filter((q) => q.default).map((q) => [q.id, q.default as string])));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not design that process");
    } finally {
      setBusy("");
    }
  }

  async function run(mode: "rehearse" | "execute") {
    if (!blueprint) return;
    setBusy(mode === "rehearse" ? "rehearse" : "build");
    setError("");
    try {
      // one consent: the build is rehearsed first (nothing kept) and only goes ahead when that passes
      const check = rehearsal?.status === "rehearsed" ? rehearsal : await processBuild(input(blueprint, answers), "rehearse");
      setRehearsal(check);
      if (check.status !== "rehearsed") {
        setError(check.message ?? "The rehearsal didn't pass - nothing was built.");
        return;
      }
      if (mode === "rehearse") return;
      if (!mounted.current) return;
      const out = await processBuild(input(blueprint, answers), "execute");
      setBuilt(out);
      if (out.memory_id) {
        setRecipes((items) => [{ id: out.memory_id!, name: out.blueprint.name, request: text.trim() }, ...items.filter((r) => r.request !== text.trim())]);
      }
      if (out.status === "built") notifyIntentExecuted();
      if (out.status === "failed" || out.status === "nothing") setError(out.message ?? "Nothing was built.");
      onFinished?.(out.status, out.status === "built" ? `${out.blueprint.name}: ${out.made.length} piece(s) built` : out.message ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "The build did not go through");
    } finally {
      setBusy("");
    }
  }

  const open = blueprint?.questions ?? [];
  const answeredAll = open.every((q) => (draft[q.id] ?? "").trim());

  return (
    <div className="space-y-6">
      {/* ---------- the request ---------- */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          reset();
          design();
        }}
        className="space-y-3"
      >
        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            if (blueprint) reset();
          }}
          rows={4}
          placeholder="Describe the process - who approves what, what should happen when, which fields a page needs… e.g. Quotes with discount above 20% need approval from the Regional Sales Head, then the Finance Controller. Once approved, notify the account manager."
          className="w-full resize-y rounded-xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-[15px] leading-relaxed text-slate-100 placeholder:text-slate-600 focus:border-emerald-500 focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-xs font-medium text-slate-500">Try:</span>
          {EXAMPLES.map((x, i) => (
            <button
              key={x}
              type="button"
              onClick={() => {
                setText(x);
                reset();
                design(x);
              }}
              title={x}
              className="max-w-xs truncate rounded-full border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 hover:border-emerald-500 hover:text-slate-200"
            >
              {["B2C customer journey", "Discount approvals", "Order control + go-live notice", "Lead routing + qualification"][i]}
            </button>
          ))}
          <button
            type="submit"
            disabled={text.trim().length < 5 || busy !== ""}
            className="ml-auto rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 hover:bg-emerald-500 disabled:opacity-40"
          >
            {busy === "design" ? "Designing…" : blueprint ? "Design again" : "Design the process"}
          </button>
        </div>
      </form>
      {memoryError && <p className="text-sm text-amber-300">{memoryError}</p>}
      {recipes.length > 0 && (
        <label className="block text-sm text-slate-400">
          Reuse a verified process
          <select value="" disabled={busy !== ""} className="ml-3 rounded-lg border border-slate-700 bg-slate-950 p-2"
            onChange={(e) => {
              const recipe = recipes.find((r) => r.id === Number(e.target.value));
              if (recipe) { setText(recipe.request); reset(); design(recipe.request); }
            }}>
            <option value="">Choose from process memory</option>
            {recipes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </label>
      )}

      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</p>}

      {/* ---------- a chain of things to do: the agent runs it ---------- */}
      {blueprint?.mode === "actions" && blueprint.actions && (
        <section className="space-y-5 rounded-2xl border border-violet-500/30 bg-violet-500/[0.04] p-5 sm:p-6">
          <div className="flex items-start gap-3">
            <span className="text-2xl leading-none">🤖</span>
            <div>
              <h4 className="text-[15px] font-semibold text-violet-100">This is a sequence of actions - I'll carry it out step by step</h4>
              <p className="mt-1 text-sm text-slate-400">
                Nothing needs configuring: each part is an action you can run. I've planned them in order and wired what each step creates into
                the next. Rehearse to see the result with nothing kept - if a step of your sales process is missing, I'll offer to add it.
              </p>
            </div>
          </div>
          <AgentPanel key={blueprint.request} bare autonomous={autonomous} intents={intents} initialGoal={blueprint.request} initialPlan={blueprint.actions} />
        </section>
      )}

      {blueprint && blueprint.mode !== "actions" && (
        <div className="space-y-6">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h3 className="text-lg font-semibold text-slate-50">{blueprint.name}</h3>
            <span className="text-xs text-slate-500">
              {blueprint.components.length} piece{blueprint.components.length === 1 ? "" : "s"} across the engines
              {blueprint.planner === "ai" ? " · understood by your AI model, checked against your setup" : ""}
              {blueprint.memory ? " · recalled from process memory and checked against your current setup" : ""}
            </span>
          </div>

          {/* ---------- how I reasoned it out, and what already runs ---------- */}
          {((blueprint.reasoning?.length ?? 0) > 0 || (blueprint.recommendations?.length ?? 0) > 0) && (
            <section className="space-y-3 rounded-2xl border border-sky-500/25 bg-sky-500/[0.04] p-5 sm:p-6">
              <h4 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-sky-300/80">How I reasoned it out</h4>
              <ul className="space-y-2 text-[13px] leading-relaxed text-slate-300">
                {blueprint.reasoning?.map((r) => (
                  <li key={r} className="flex gap-2">
                    <span className="text-sky-400">›</span>
                    <span>{r}</span>
                  </li>
                ))}
                {blueprint.recommendations?.map((r) => (
                  <li key={r} className="flex gap-2 font-medium text-emerald-300">
                    <span>✓</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* ---------- decisions I made from my own suggestions ---------- */}
          {decided.length > 0 && open.length === 0 && (
            <section className="rounded-2xl border border-slate-800 bg-slate-900/40 p-5 sm:p-6">
              <div className="flex flex-wrap items-center gap-3">
                <h4 className="min-w-0 flex-1 text-[15px] font-semibold text-slate-100">
                  ✓ I made {decided.length === 1 ? "one decision" : `${decided.length} decisions`} for you
                </h4>
                <button onClick={() => setReviewing((r) => !r)} className="text-xs font-semibold text-slate-400 hover:text-slate-200">
                  {reviewing ? "Hide" : "See them"}
                </button>
                <button
                  onClick={() => design(text, {}, null, false)}
                  disabled={busy !== ""}
                  className="rounded-lg border border-slate-700 px-3 py-1 text-xs font-semibold text-slate-300 hover:border-amber-500/60 hover:text-amber-200 disabled:opacity-40"
                >
                  Change them
                </button>
              </div>
              {reviewing && (
                <ul className="mt-3 space-y-2 text-[13px]">
                  {decided.map((d) => (
                    <li key={d.prompt} className="rounded-lg bg-slate-950/50 px-3 py-2">
                      <span className="text-slate-400">{d.prompt}</span>
                      <span className="mt-0.5 block font-medium text-emerald-300">→ {d.answer}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {/* ---------- decisions (the break in the loop) ---------- */}
          {open.length > 0 && (
            <section className="space-y-4 rounded-2xl border border-amber-500/30 bg-amber-500/[0.05] p-5 sm:p-6">
              <div>
                <h4 className="text-[15px] font-semibold text-amber-100">I need {open.length === 1 ? "one decision" : `${open.length} decisions`} before I can build it</h4>
                <p className="mt-1 text-xs text-amber-200/70">My suggestion is selected - change anything, then continue.</p>
              </div>
              {open.map((q) => (
                <Question key={q.id} q={q} value={draft[q.id] ?? ""} onChange={(v) => setDraft((d) => ({ ...d, [q.id]: v }))} />
              ))}
              <div className="flex justify-end">
                <button
                  onClick={() => design(text, { ...answers, ...draft }, blueprint)}
                  disabled={!answeredAll || busy !== ""}
                  className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-40"
                >
                  {busy === "design" ? "Working it out…" : "Continue with these answers"}
                </button>
              </div>
            </section>
          )}

          {/* ---------- what gets built ---------- */}
          {blueprint.components.length > 0 && (
            <section className="space-y-3">
              <h4 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">What I'll set up</h4>
              <div className="grid gap-4 lg:grid-cols-2">
                {blueprint.components.map((c) => {
                  const check = built?.checks.find((k) => k.component === c.id);
                  const made = (built ?? rehearsal)?.made.find((m) => m.component === c.id);
                  return (
                    <article key={c.id} className={`rounded-xl border p-4 sm:p-5 ${ENGINE_TONE[c.kind] ?? "border-slate-800"}`}>
                      <div className="flex items-start gap-3">
                        <span className="text-xl leading-none">{c.icon}</span>
                        <div className="min-w-0 flex-1">
                          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">{c.engine}</p>
                          <p className="mt-0.5 text-sm font-semibold text-slate-100">{c.title}</p>
                        </div>
                        {check && (
                          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${check.ok ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>
                            {check.ok ? "✓ live" : "⚠ check"}
                          </span>
                        )}
                      </div>
                      <ul className="mt-3 space-y-1 pl-8 text-[13px] text-slate-300">
                        {c.lines.map((l) => (
                          <li key={l} className="list-disc marker:text-slate-600">{l}</li>
                        ))}
                      </ul>
                      {c.warnings.map((w) => (
                        <p key={w} className="mt-2 pl-8 text-xs text-amber-300">⚠ {w}</p>
                      ))}
                      <p className="mt-3 pl-8 text-[11px] italic text-slate-500">“{c.text}”</p>
                      {check && <p className={`mt-2 pl-8 text-xs ${check.ok ? "text-emerald-300" : "text-amber-300"}`}>{check.message}</p>}
                      {made && built?.status === "built" && <p className="mt-1 pl-8 text-[11px] text-slate-500">Find it in {made.where}</p>}
                    </article>
                  );
                })}
              </div>
            </section>
          )}

          {/* ---------- engineering changes ---------- */}
          {blueprint.engineering.length > 0 && (
            <section className="space-y-3">
              <h4 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Needs a core engineering change - not built</h4>
              {blueprint.engineering.map((e) => (
                <article key={e.id} className="rounded-xl border border-orange-500/30 bg-orange-500/[0.05] p-4 sm:p-5">
                  <div className="flex flex-wrap items-start gap-3">
                    <span className="text-xl leading-none">🛠️</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-orange-100">{e.need}</p>
                      <p className="mt-0.5 text-[11px] italic text-slate-500">“{e.text}”</p>
                    </div>
                    <span className="rounded-full bg-orange-500/15 px-2 py-0.5 text-[11px] font-semibold text-orange-300" title="Rough size: S = days, M = 1-3 weeks, L = a project">
                      effort {e.effort}
                    </span>
                  </div>
                  <p className="mt-3 pl-8 text-[13px] leading-relaxed text-slate-300">{e.why}</p>
                  {e.workaround && <p className="mt-2 pl-8 text-[13px] text-slate-400"><span className="font-semibold text-slate-300">Meanwhile:</span> {e.workaround}</p>}
                  <div className="mt-3 pl-8">
                    <button
                      onClick={() => {
                        try {
                          void navigator.clipboard.writeText(`Change request - ${e.need}\nAsked for: ${e.text}\nWhy it needs engineering: ${e.why}\nWorkaround meanwhile: ${e.workaround}\nEstimated effort: ${e.effort}`);
                        } catch {
                          /* clipboard blocked - the text is on screen */
                        }
                      }}
                      className="rounded-lg border border-orange-500/40 px-3 py-1 text-xs font-semibold text-orange-200 hover:bg-orange-500/10"
                    >
                      Copy as a change request
                    </button>
                  </div>
                </article>
              ))}
            </section>
          )}

          {blueprint.skipped.length > 0 && <p className="text-xs text-slate-500">Left out: {blueprint.skipped.map((s) => `“${s}”`).join(", ")}</p>}

          {/* ---------- rehearse, consent, build ---------- */}
          {blueprint.ready && !built && (
            <section className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/50 p-5 sm:p-6">
              {!rehearsal || rehearsal.status === "rehearsed" ? (
                <>
                  <div>
                    {rehearsal ? (
                      <>
                        <p className="text-sm font-semibold text-violet-200">Preview passed - nothing was kept.</p>
                        {rehearsal.impact?.headline && <p className="mt-1 text-sm text-slate-300">{rehearsal.impact.headline}</p>}
                      </>
                    ) : (
                      <p className="text-sm text-slate-400">
                        One consent does it: I rehearse the whole build on your live setup first, and switch it on only if that passes - fields, pages,
                        flows and approvals, created together or not at all.{" "}
                        <button onClick={() => run("rehearse")} disabled={busy !== ""} className="font-semibold text-violet-300 hover:text-violet-200 disabled:opacity-40">
                          {busy === "rehearse" ? "Previewing…" : "Preview first"}
                        </button>
                      </p>
                    )}
                    {blueprint.new_positions.length > 0 && (
                      <p className="mt-1 text-xs text-slate-500">New positions: {blueprint.new_positions.join(", ")} - add their people in Org Structure; until then anyone allowed to approve can decide.</p>
                    )}
                  </div>
                  <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-700 bg-slate-950/50 p-4 text-sm text-slate-200">
                    <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5 h-4 w-4 accent-emerald-500" />
                    <span>
                      I consent to building <b>{blueprint.name}</b> and switching it on now - {blueprint.components.length} piece
                      {blueprint.components.length === 1 ? "" : "s"}, created together or not at all.
                    </span>
                  </label>
                  <div className="flex justify-end">
                    <button
                      onClick={() => run("execute")}
                      disabled={!consent || busy !== ""}
                      className="rounded-lg bg-emerald-600 px-5 py-2.5 text-sm font-bold text-white shadow-lg shadow-emerald-900/40 hover:bg-emerald-500 disabled:opacity-40"
                    >
                      {busy === "build" ? "Building…" : "▶ Build it and switch it on"}
                    </button>
                  </div>
                </>
              ) : (
                <p className="text-sm text-amber-300">{rehearsal.message ?? "The rehearsal needs another look."}</p>
              )}
            </section>
          )}

          {built?.status === "built" && (
            <section className="rounded-2xl border border-emerald-500/40 bg-emerald-500/10 p-5 sm:p-6">
              <p className="text-[15px] font-semibold text-emerald-100">
                ✓ {blueprint.name} is built and live - {built.checks.filter((c) => c.ok).length} of {built.checks.length} pieces verified.
              </p>
              <p className="mt-1 text-sm text-emerald-200/80">Verification results are shown above. Every piece can be edited in its own studio.</p>
              <p className="mt-1 text-sm text-emerald-200/80">{built.memory_id ? "Saved in your tenant's process memory for future designs." : "Not saved to process memory because verification is incomplete."}</p>
              <button onClick={() => { setText(""); reset(); }} className="mt-4 rounded-lg bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-white">
                Build another process
              </button>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function Question({ q, value, onChange }: { q: ProcessQuestion; value: string; onChange: (v: string) => void }) {
  const isOption = q.options.some((o) => o.value === value);
  return (
    <fieldset className="space-y-2 rounded-xl border border-amber-500/20 bg-slate-950/40 p-4">
      <legend className="sr-only">{q.prompt}</legend>
      <p className="text-sm font-medium text-slate-100">{q.prompt}</p>
      {q.hint && <p className="text-xs text-slate-500">{q.hint}</p>}
      <div className="grid gap-1.5 sm:grid-cols-2">
        {q.options.map((o) => (
          <label key={o.value} className={`flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-[13px] transition ${value === o.value ? "border-amber-400/60 bg-amber-500/10 text-amber-50" : "border-slate-800 text-slate-300 hover:border-slate-700"}`}>
            <input type="radio" name={q.id} checked={value === o.value} onChange={() => onChange(o.value)} className="accent-amber-500" />
            {o.label}
          </label>
        ))}
      </div>
      {q.allow_text && (
        <input
          value={isOption ? "" : value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={q.options.length ? "…or type your own answer" : "Your answer"}
          className="h-9 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-amber-500"
        />
      )}
    </fieldset>
  );
}
