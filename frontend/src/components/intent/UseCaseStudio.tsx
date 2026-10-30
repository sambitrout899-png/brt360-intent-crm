import { useEffect, useMemo, useState } from "react";
import type { IntentDef } from "../../api/types";
import {
  LIST_FIELDS,
  commitUseCases,
  deleteUseCaseSet,
  downloadUseCaseTests,
  generateUseCaseTests,
  getUseCaseSet,
  previewUseCases,
  saveUseCaseSet,
  type Answers,
  type UseCase,
  type UseCaseCommit,
  type UseCaseDesign,
  type UseCaseQuestion,
  type UseCaseSet,
  type UseCaseStatus,
} from "../../api/useCases";

const STATUS: Record<UseCaseStatus, { label: string; tone: string }> = {
  brt: { label: "In BRT", tone: "bg-emerald-500/15 text-emerald-300" },
  in_use: { label: "In use", tone: "bg-emerald-500/15 text-emerald-300" },
  delta: { label: "Delta", tone: "bg-sky-500/15 text-sky-300" },
  configure: { label: "Customization", tone: "bg-violet-500/15 text-violet-300" },
  new: { label: "New requirement", tone: "bg-amber-500/15 text-amber-300" },
  gap: { label: "Process gap", tone: "bg-orange-500/15 text-orange-300" },
};
const BUILD_FIELDS = new Set(["business_rules", "data", "notifications", "assignment", "integrations"]);
const SET_STATUS: Record<UseCaseSet["status"], string> = {
  draft: "Draft - being reviewed",
  committed: "Committed to a draft map version",
  live: "Live - its map version is active",
  covered: "Committed - BRT's processes already do it all",
};

function Chip({ status, title }: { status: UseCaseStatus; title?: string }) {
  const s = STATUS[status];
  return <span title={title} className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${s.tone}`}>{s.label}</span>;
}

/** Functional use cases, reviewed and implemented: every step and rule as it
 * maps on the Functional Map (already in BRT, a delta on its process, a
 * customization, a new requirement, a process gap) - edited here and mapped
 * again on save, committed into a map version, then turned into manual and
 * automation test cases. */
export default function UseCaseStudio({ setId, initial, intents, processes, onClose }: {
  setId: number;
  initial?: UseCaseSet | null;
  intents: IntentDef[];
  processes: { key: string; label: string }[];
  onClose?: () => void;
}) {
  const [set, setSet] = useState<UseCaseSet | null>(initial ?? null);
  const [cases, setCases] = useState<UseCase[]>(initial?.use_cases ?? []);
  const [name, setName] = useState(initial?.name ?? "");
  const [dirty, setDirty] = useState(false);
  const [open, setOpen] = useState(0);
  const [busy, setBusy] = useState<"" | "load" | "save" | "preview" | "commit" | "tests" | "download">(initial ? "" : "load");
  const [error, setError] = useState("");
  const [designs, setDesigns] = useState<UseCaseDesign[] | null>(null);
  const [questions, setQuestions] = useState<UseCaseQuestion[]>([]);
  const [answers, setAnswers] = useState<Answers>({});
  const [result, setResult] = useState<UseCaseCommit | null>(null);
  const [showTests, setShowTests] = useState(false);

  function take(s: UseCaseSet) {
    setSet(s);
    setCases(s.use_cases);
    setName(s.name);
    setDirty(false);
  }

  useEffect(() => {
    if (initial) return;
    let live = true;
    getUseCaseSet(setId).then((s) => live && take(s)).catch((e) => live && setError(e instanceof Error ? e.message : "Couldn't load the use cases"))
      .finally(() => live && setBusy(""));
    return () => { live = false; };
  }, [setId, initial]);

  async function job<T>(kind: typeof busy, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(kind);
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through");
      return undefined;
    } finally {
      setBusy("");
    }
  }

  function edit(i: number, change: (c: UseCase) => UseCase) {
    setCases((cs) => cs.map((c, j) => (j === i ? change(c) : c)));
    setDirty(true);
    setDesigns(null);
    setQuestions([]);
  }

  async function save() {
    const s = await job("save", () => saveUseCaseSet(setId, name.trim() || "Use cases", cases));
    if (s) { take(s); setResult(null); }
  }

  async function preview() {
    const out = await job("preview", () => previewUseCases(setId, answers));
    if (!out) return;
    setDesigns(out.designs);
    const open = out.designs.flatMap((d) => d.questions.map((q) => ({ ...q, group: d.group, process: d.process })));
    setQuestions(open);
    setAnswers((a) => {
      const next = { ...a };
      for (const q of open) if (q.default && !next[q.group]?.[q.id]) next[q.group] = { ...next[q.group], [q.id]: q.default };
      return next;
    });
  }

  async function commit(activate: boolean) {
    const out = await job("commit", () => commitUseCases(setId, answers, activate));
    if (!out) return;
    take(out.set);
    if (!out.committed) {
      setQuestions(out.questions);
      return;
    }
    setResult(out);
    setQuestions([]);
  }

  async function tests() {
    const s = await job("tests", () => generateUseCaseTests(setId));
    if (s) { take(s); setShowTests(true); }
  }

  const intentLabel = useMemo(() => Object.fromEntries(intents.map((i) => [i.code, i.label])), [intents]);
  const sec = "space-y-3 rounded-2xl border border-slate-800 bg-slate-900/40 p-4 sm:p-5";
  const h = "text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500";
  const input = "w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none";

  if (!set) {
    return (
      <section className={sec}>
        <p className="text-sm text-slate-300">{busy ? "Loading the use cases…" : error}</p>
        {onClose && <button onClick={onClose} className="text-xs text-sky-400">Back</button>}
      </section>
    );
  }
  const sum = set.summary;
  const answeredAll = questions.every((q) => (answers[q.group]?.[q.id] ?? "").trim());

  return (
    <div className="space-y-4">
      {/* ---------- the set ---------- */}
      <section className="rounded-2xl border border-emerald-500/30 bg-emerald-500/[0.04] p-4 sm:p-5">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className={h}>📋 Use cases - a delta on the Functional Map</p>
            <input value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }}
              className="mt-1 w-full bg-transparent text-lg font-semibold text-slate-50 focus:outline-none" />
            <p className="mt-1 text-xs text-slate-500">
              {set.filename} · {set.read_by === "template" ? "read from the use case template" : set.read_by === "ai" ? "restructured by your AI model" : "read sentence by sentence (no AI model set)"} ·{" "}
              <span className={set.status === "draft" ? "text-amber-300" : "text-emerald-300"}>{SET_STATUS[set.status]}</span>
              {set.examples_dropped ? ` · the template's example was ignored` : ""}
            </p>
          </div>
          <div className="flex gap-2">
            {onClose && <button onClick={onClose} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:border-slate-500">Close</button>}
            <button
              onClick={async () => { if (window.confirm(`Delete “${set.name}”? A map version it was committed into stays.`)) { await job("save", () => deleteUseCaseSet(setId)); onClose?.(); } }}
              className="rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10">
              Delete
            </button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-slate-800 px-2.5 py-1 text-slate-300">{sum.use_cases} use case{sum.use_cases === 1 ? "" : "s"}</span>
          <span className="rounded-full bg-emerald-500/15 px-2.5 py-1 text-emerald-300">{sum.covered} already in BRT</span>
          <span className="rounded-full bg-sky-500/15 px-2.5 py-1 text-sky-300">{sum.delta} delta step{sum.delta === 1 ? "" : "s"} · {sum.links} new hand-off{sum.links === 1 ? "" : "s"}</span>
          <span className="rounded-full bg-violet-500/15 px-2.5 py-1 text-violet-300">{sum.configure} customization{sum.configure === 1 ? "" : "s"}</span>
          <span className="rounded-full bg-amber-500/15 px-2.5 py-1 text-amber-300">{sum.new} new requirement{sum.new === 1 ? "" : "s"}</span>
          <span className="rounded-full bg-orange-500/15 px-2.5 py-1 text-orange-300">{sum.gap} process gap{sum.gap === 1 ? "" : "s"}</span>
        </div>
      </section>

      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-300">{error}</p>}

      {/* ---------- new requirements and process gaps ---------- */}
      {set.gaps.length > 0 && (
        <section className="space-y-2 rounded-2xl border border-orange-500/30 bg-orange-500/[0.05] p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="flex-1 text-sm font-semibold text-orange-100">
              Outside what BRT 360 does today - {set.gaps.length} new requirement{set.gaps.length === 1 ? "" : "s"} / process gap{set.gaps.length === 1 ? "" : "s"}
            </h4>
            <button
              onClick={() => { try { void navigator.clipboard.writeText(set.gaps.map((g) => `[${g.kind}] ${g.use_case} ${g.title}: ${g.text}\n  ${g.how}`).join("\n")); } catch { /* the list is on screen */ } }}
              className="rounded-lg border border-orange-500/40 px-3 py-1 text-xs font-semibold text-orange-200 hover:bg-orange-500/10">
              Copy as change requests
            </button>
          </div>
          <ul className="space-y-1 text-[13px]">
            {set.gaps.map((g, i) => (
              <li key={i} className="flex gap-2 text-slate-300">
                <Chip status={g.kind === "new requirement" ? "new" : "gap"} />
                <span><span className="font-mono text-[11px] text-slate-500">{g.use_case}</span> {g.text} <span className="text-slate-500">- {g.how}</span></span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ---------- the use cases, editable ---------- */}
      {cases.map((c, i) => (
        <section key={i} className={sec}>
          <button onClick={() => setOpen(open === i ? -1 : i)} className="flex w-full items-center gap-2 text-left">
            <span className="font-mono text-xs text-slate-500">{c.id}</span>
            <span className="flex-1 font-semibold text-slate-100">{c.title}</span>
            {c.mapping && (
              <span className="text-xs text-slate-500">
                {c.mapping.process.mode === "append" ? `extends ${c.mapping.process.label}` : `new process “${c.mapping.process.label}”`}
                {c.mapping.process.inferred ? " (inferred from its steps)" : ""}
              </span>
            )}
            <span className="text-slate-500">{open === i ? "▾" : "▸"}</span>
          </button>
          {open === i && (
            <div className="space-y-4 pt-2">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs text-slate-400">Title
                  <input value={c.title} onChange={(e) => edit(i, (x) => ({ ...x, title: e.target.value }))} className={input} />
                </label>
                <label className="text-xs text-slate-400">Business function (Functional Map process, or “New process”)
                  <input list="uc-processes" value={c.business_function} onChange={(e) => edit(i, (x) => ({ ...x, business_function: e.target.value }))} className={input} />
                </label>
                <label className="text-xs text-slate-400">Actor
                  <input value={c.actor} onChange={(e) => edit(i, (x) => ({ ...x, actor: e.target.value }))} className={input} />
                </label>
                <label className="text-xs text-slate-400">Trigger
                  <input value={c.trigger} onChange={(e) => edit(i, (x) => ({ ...x, trigger: e.target.value }))} className={input} />
                </label>
              </div>

              <div>
                <p className={h}>Main flow - each step mapped to an action of the map</p>
                <ol className="mt-2 space-y-2">
                  {c.main_flow.map((s, n) => {
                    const m = dirty ? undefined : c.mapping?.steps[n];
                    return (
                      <li key={n} className="rounded-lg bg-slate-950/50 p-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="w-5 text-right font-mono text-xs text-slate-500">{n + 1}</span>
                          <input value={s.text} onChange={(e) => edit(i, (x) => ({ ...x, main_flow: x.main_flow.map((y, k) => (k === n ? { ...y, text: e.target.value } : y)) }))}
                            className={`${input} min-w-[12rem] flex-1`} />
                          <select
                            value={s.pinned ? (s.intent ?? "") : "auto"}
                            onChange={(e) => edit(i, (x) => ({ ...x, main_flow: x.main_flow.map((y, k) => (k === n ? (e.target.value === "auto" ? { ...y, intent: null, pinned: false } : { ...y, intent: e.target.value || null, pinned: true }) : y)) }))}
                            className="max-w-[16rem] rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200"
                            title="Which action does this step - matched automatically, or chosen by you">
                            <option value="auto">Match automatically{m?.intent ? ` (${m.label})` : ""}</option>
                            <option value="">None - a new requirement</option>
                            {intents.map((it) => <option key={it.code} value={it.code}>{it.label} ({it.code})</option>)}
                          </select>
                          {m && <Chip status={m.status} title={m.how} />}
                          <button onClick={() => edit(i, (x) => ({ ...x, main_flow: x.main_flow.filter((_, k) => k !== n) }))} className="text-xs text-slate-500 hover:text-red-300">✕</button>
                        </div>
                        {m && <p className="mt-1 pl-7 text-[11px] text-slate-500">{m.intent && s.pinned ? `${intentLabel[m.intent] ?? m.intent} - your choice. ` : ""}{m.how}</p>}
                      </li>
                    );
                  })}
                </ol>
                <button onClick={() => edit(i, (x) => ({ ...x, main_flow: [...x.main_flow, { text: "", intent: null, pinned: false }] }))}
                  className="mt-2 text-xs font-semibold text-emerald-300 hover:text-emerald-200">+ Add a step</button>
                {!dirty && (c.mapping?.links.length ?? 0) > 0 && (
                  <p className="mt-2 text-xs text-sky-300">New hand-offs on the map: {c.mapping!.links.map((l) => `${l.from_label} → ${l.to_label}`).join(" · ")}</p>
                )}
              </div>

              <div className="grid gap-3 lg:grid-cols-2">
                {LIST_FIELDS.map(([field, label]) => (
                  <label key={field} className="text-xs text-slate-400">
                    {label} <span className="text-slate-600">- one per line</span>
                    <textarea rows={Math.max(2, c[field].length + 1)} value={c[field].join("\n")}
                      onChange={(e) => edit(i, (x) => ({ ...x, [field]: e.target.value.split("\n") }))}
                      className={`${input} resize-y`} />
                    {!dirty && BUILD_FIELDS.has(field) && c.mapping?.lines.filter((l) => l.field === field).map((l, k) => (
                      <span key={k} className="mt-1 flex items-start gap-1.5 text-[11px] text-slate-500"><Chip status={l.status} /> {l.text.slice(0, 60)}{l.text.length > 60 ? "…" : ""} - {l.how}</span>
                    ))}
                  </label>
                ))}
              </div>
              <div className="flex justify-end">
                <button onClick={() => { if (window.confirm(`Remove ${c.id}?`)) { edit(i, (x) => x); setCases((cs) => cs.filter((_, j) => j !== i)); } }}
                  className="text-xs text-slate-500 hover:text-red-300">Remove this use case</button>
              </div>
            </div>
          )}
        </section>
      ))}
      <datalist id="uc-processes">
        <option value="New process" />
        {processes.map((p) => <option key={p.key} value={p.label} />)}
      </datalist>

      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          onClick={() => {
            const n = cases.length + 1;
            setCases((cs) => [...cs, { id: `UC-${String(n).padStart(2, "0")}`, title: "New use case", business_function: "", actor: "", trigger: "", main_flow: [{ text: "", intent: null, pinned: false }],
              preconditions: [], alternate_flows: [], business_rules: [], data: [], notifications: [], assignment: [], integrations: [], postconditions: [], acceptance: [] }]);
            setOpen(cases.length);
            setDirty(true);
          }}
          className="mr-auto text-xs font-semibold text-emerald-300 hover:text-emerald-200">
          + Add a use case
        </button>
        {dirty && <span className="text-xs text-amber-300">Changed - save to map it again</span>}
        <button onClick={save} disabled={!dirty || busy !== ""}
          className="rounded-lg bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-white disabled:opacity-40">
          {busy === "save" ? "Mapping…" : "Save and map again"}
        </button>
      </div>

      {/* ---------- commit ---------- */}
      <section className={sec}>
        <p className={h}>Commit - apply the use cases through the Functional Map</p>
        <p className="text-sm text-slate-400">
          The mapped actions are placed in their process and linked, the customizations (approvals, automations, fields, assignment) and the new
          actions are built - all in one draft version of the map, built when that version goes live. Process gaps are not built.
        </p>
        <button onClick={preview} disabled={dirty || busy !== ""} className="text-sm font-semibold text-sky-300 hover:text-sky-200 disabled:opacity-40">
          {busy === "preview" ? "Working it out…" : "▶ Preview what commit builds"}
        </button>
        {designs?.map((d) => (
          <article key={d.group} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3 text-[13px] text-slate-300">
            <p className="font-semibold text-slate-100">
              {d.mode === "append" ? `Extends “${d.process}”` : `New process “${d.process}”`} {!d.changes && <span className="text-emerald-300">- nothing to change, BRT already does it</span>}
            </p>
            {d.steps.length > 0 && <p className="mt-1 text-xs text-slate-400">Steps: {d.steps.map((s) => s.label).join(" → ")}</p>}
            {d.links.length > 0 && <p className="text-xs text-sky-300">Hand-offs: {d.links.map((l) => `${l.from_label} → ${l.to_label}`).join(" · ")}</p>}
            {d.components.map((c, k) => (
              <div key={k} className="mt-2">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-violet-300">{c.engine}</span> <span className="text-slate-100">{c.title}</span>
                <ul className="list-disc pl-5 text-xs text-slate-400">{c.lines.map((l) => <li key={l}>{l}</li>)}</ul>
                {c.warnings.map((w) => <p key={w} className="text-xs text-amber-300">⚠ {w}</p>)}
              </div>
            ))}
            {d.assumptions.length > 0 && <p className="mt-2 text-xs text-slate-400">Decided for you: {d.assumptions.join(" · ")}</p>}
            {d.notes.map((n) => <p key={n} className="text-xs text-amber-200/80">{n}</p>)}
          </article>
        ))}
        {questions.length > 0 && (
          <div className="space-y-2 rounded-xl border border-amber-500/30 bg-amber-500/[0.05] p-3">
            <p className="text-sm font-semibold text-amber-100">{questions.length === 1 ? "One decision" : `${questions.length} decisions`} before it can be committed</p>
            {questions.map((q) => (
              <label key={`${q.group}-${q.id}`} className="block text-sm text-slate-200">
                {q.process ? <span className="text-slate-500">{q.process}: </span> : null}{q.prompt}
                {q.options.length > 0 ? (
                  <select value={answers[q.group]?.[q.id] ?? ""} onChange={(e) => setAnswers((a) => ({ ...a, [q.group]: { ...a[q.group], [q.id]: e.target.value } }))} className={`mt-1 block ${input}`}>
                    <option value="">Choose…</option>
                    {q.options.map((o, k) => <option key={o} value={q.values?.[k] ?? o}>{o}</option>)}
                  </select>
                ) : (
                  <input value={answers[q.group]?.[q.id] ?? ""} onChange={(e) => setAnswers((a) => ({ ...a, [q.group]: { ...a[q.group], [q.id]: e.target.value } }))} className={`mt-1 block ${input}`} />
                )}
              </label>
            ))}
          </div>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button onClick={() => commit(false)} disabled={dirty || busy !== "" || !answeredAll}
            className="rounded-lg border border-slate-600 px-4 py-2.5 text-sm font-semibold text-slate-200 hover:border-slate-400 disabled:opacity-40"
            title="Into a draft map version - activate it later from Admin → Functional Map">
            {busy === "commit" ? "Committing…" : "Commit to a draft map version"}
          </button>
          <button onClick={() => commit(true)} disabled={dirty || busy !== "" || !answeredAll}
            className="rounded-lg bg-emerald-600 px-5 py-2.5 text-sm font-bold text-white hover:bg-emerald-500 disabled:opacity-40"
            title="The organization's root administrator only, outside a workspace">
            Commit and activate now
          </button>
        </div>
        {result?.committed && (
          <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-100">
            {result.status === "covered"
              ? "✓ Committed - BRT's processes already do everything these use cases ask; nothing needed changing."
              : result.status === "live"
                ? `✓ Live - map version ${result.version?.label ?? result.version?.number} “${result.version?.name}” is active and everything in it is built.`
                : `✓ In draft map version ${result.version?.label ?? result.version?.number} “${result.version?.name}”${result.version?.workspace ? ` in your workspace “${result.version.workspace}”` : ""} - nothing is built until it goes live (Admin → Functional Map → simulate, then Activate…).`}
            {result.activation_error && <p className="mt-1 text-amber-300">It wasn't activated: {result.activation_error}</p>}
            {(result.assumptions?.length ?? 0) > 0 && <p className="mt-1 text-xs text-emerald-200/80">Decided for you: {result.assumptions!.join(" · ")}</p>}
          </div>
        )}
      </section>

      {/* ---------- test cases ---------- */}
      <section className={sec}>
        <div className="flex flex-wrap items-center gap-2">
          <p className={`${h} flex-1`}>Test cases - manual and automation</p>
          <button onClick={tests} disabled={set.status === "draft" || busy !== ""}
            title={set.status === "draft" ? "Commit the use cases first - the tests are written for what was implemented" : ""}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-40">
            {busy === "tests" ? "Writing…" : set.tests.count ? "Generate again" : "Generate test cases"}
          </button>
        </div>
        {set.status === "draft" && <p className="text-xs text-slate-500">Available once the use cases are committed.</p>}
        {set.tests.stale && <p className="text-xs text-amber-300">The use cases changed after these were written - commit and generate them again.</p>}
        {set.test_cases.length > 0 && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-slate-400">
                {set.test_cases.length} test cases · {set.test_cases.filter((t) => t.automation === "automated").length} automated · {set.test_cases.filter((t) => t.automation === "scaffolded").length} scaffolded for automation · generated {set.tests.generated_at?.replace("T", " ")}
              </span>
              {(["manual.docx", "manual.csv", "automation.py"] as const).map((k) => (
                <button key={k} disabled={busy !== ""} onClick={() => job("download", () => downloadUseCaseTests(set, k))}
                  className="rounded-lg border border-slate-600 px-2.5 py-1 font-semibold text-slate-200 hover:border-sky-500">
                  ⬇ {k === "manual.docx" ? "Manual (Word)" : k === "manual.csv" ? "Manual (CSV / Excel)" : "Automation (pytest)"}
                </button>
              ))}
              <button onClick={() => setShowTests((v) => !v)} className="ml-auto text-slate-400 hover:text-slate-200">{showTests ? "Hide" : "Show"} them</button>
            </div>
            {showTests && (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs text-slate-300">
                  <thead className="text-slate-500">
                    <tr><th className="py-1 pr-2">ID</th><th className="pr-2">Title</th><th className="pr-2">Type</th><th className="pr-2">Priority</th><th className="pr-2">Steps → expected</th><th>Run</th></tr>
                  </thead>
                  <tbody>
                    {set.test_cases.map((t) => (
                      <tr key={t.id} className="border-t border-slate-800 align-top">
                        <td className="py-1.5 pr-2 font-mono text-slate-500">{t.id}</td>
                        <td className="pr-2 text-slate-100">{t.title}{t.data && <span className="block text-slate-500">{t.data}</span>}</td>
                        <td className="pr-2">{t.kind}</td>
                        <td className="pr-2">{t.priority}</td>
                        <td className="pr-2">
                          <ol className="list-decimal pl-4">{t.steps.map((s, k) => <li key={k}>{s.action} <span className="text-slate-500">→ {s.expected}</span></li>)}</ol>
                          <span className="text-emerald-300/80">Expected: {t.expected}</span>
                        </td>
                        <td className={t.automation === "manual" ? "text-slate-500" : "text-sky-300"}>{t.automation}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
