import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { cancelRun, download, generateSuite, getRun, getSuite, listRuns, listSuites, openReport, startRun, toggleCase } from "../api/systemTests";
import type { CaseResult, RunSummary, SuiteSummary, TestCaseRow } from "../api/systemTests";

/** Admin -> Test Suite -> System tests: cases generated from this tenant's own system (every intent, custom record
 *  type, flow and page rule), run in the background through the real platform - each case rolled back, nothing kept -
 *  with step-by-step evidence and a report to share or print. */

const BTN = "rounded-lg border border-slate-700 px-3 py-1.5 text-sm font-semibold text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-50";
const PRIMARY = "rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50";
const CHIP = "rounded-full border px-2.5 py-1 text-xs";
const STATUS: Record<string, string> = {
  passed: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10", failed: "text-red-400 border-red-500/40 bg-red-500/10",
  error: "text-fuchsia-400 border-fuchsia-500/40 bg-fuchsia-500/10", skipped: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  running: "text-sky-400 border-sky-500/40 bg-sky-500/10", queued: "text-slate-300 border-slate-600 bg-slate-800", cancelled: "text-slate-400 border-slate-600 bg-slate-800",
};
const KIND: Record<string, string> = {
  understanding: "Plain English", required_fields: "Required fields", end_to_end: "End to end", lifecycle: "Lifecycle", mandatory: "Mandatory",
  type_check: "Type check", rule: "Validation rule", lookup: "Lookup", relationship: "Relationship", flow: "Flow", page_rule: "Page rule",
};

const Badge = ({ status }: { status: string }) => <span className={`rounded-md border px-2 py-0.5 text-[11px] font-bold ${STATUS[status] ?? ""}`}>{status}</span>;
const json = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));

export default function SystemTestsPanel() {
  const [suites, setSuites] = useState<SuiteSummary[]>([]);
  const [suite, setSuite] = useState<(SuiteSummary & { cases: TestCaseRow[] }) | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"run" | "cases">("run");

  const load = useCallback(async (keepSuite?: number) => {
    try {
      const [s, r] = await Promise.all([listSuites(), listRuns()]);
      setSuites(s);
      setRuns(r);
      const pick = keepSuite ?? s[0]?.id;
      if (pick) setSuite(await getSuite(pick));
      if (runId === null && r[0]) setRunId(r[0].id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the test suite.");
    }
  }, [runId]);
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function generate() {
    setBusy(true);
    setError("");
    try {
      const made = await generateSuite();
      await load(made.id);
      setTab("cases");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate the suite.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-slate-700 bg-slate-900/40 p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-bold text-slate-100">System tests - generated from your own system</h3>
          <p className="mt-1 text-sm text-slate-400">
            Test cases for everything this tenant has: every intent (understood in plain English, refused without its required fields,
            run end to end on your data), every custom record type (lifecycle, mandatory fields, validation rules, lookups, relationships),
            every switched-on flow and page rule. Runs go through the real platform in the background, as you, and every case is rolled
            back - nothing is kept - with step-by-step evidence and a report you can share or print.
          </p>
        </div>
        <button onClick={generate} disabled={busy} className={PRIMARY}>{busy ? "Generating…" : suite ? "Generate again" : "Generate the test suite"}</button>
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {suites.length > 1 && (
        <select value={suite?.id ?? ""} onChange={async (e) => setSuite(await getSuite(Number(e.target.value)))} aria-label="Suite"
          className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
          {suites.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.case_count} cases</option>)}
        </select>
      )}
      {suite && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {[["Test cases", suite.case_count], ["Intents", suite.scope.intents ?? 0], ["Record types", suite.scope.record_types?.length ?? 0],
              ["Flows", suite.scope.flows ?? 0], ["Page rules", suite.scope.page_rules ?? 0]].map(([label, value]) => (
              <div key={String(label)} className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
                <span className="block text-[11px] uppercase tracking-wide text-slate-500">{label}</span>
                <span className="text-lg font-bold text-slate-100">{value}</span>
              </div>
            ))}
          </div>
          <div className="flex gap-1 text-sm" role="tablist">
            {(["run", "cases"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                className={`rounded-md px-3 py-1 ${tab === t ? "bg-slate-800 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>
                {t === "run" ? "Runs & evidence" : `Test cases · ${suite.active}/${suite.case_count}`}
              </button>
            ))}
          </div>
          {tab === "cases" && <Cases suite={suite} onChanged={() => load(suite.id)} />}
          {tab === "run" && (
            <Runs suite={suite} runs={runs} runId={runId} onPick={setRunId}
              onStarted={async (r) => { setRunId(r.id); setRuns(await listRuns()); }} onRefresh={async () => setRuns(await listRuns())} />
          )}
        </>
      )}
      {!suite && !error && <p className="text-sm text-slate-500">No suite yet - generate one; it reads your intents, record types, flows and rules.</p>}
    </section>
  );
}

function Cases({ suite, onChanged }: { suite: SuiteSummary & { cases: TestCaseRow[] }; onChanged: () => void }) {
  const [area, setArea] = useState("");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const areas = useMemo(() => Object.keys(suite.scope.by_area ?? {}).sort(), [suite]);
  const shown = suite.cases.filter((c) => (!area || c.area === area) && (!kind || c.kind === kind) && (!q || c.title.toLowerCase().includes(q.toLowerCase())));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select value={area} onChange={(e) => setArea(e.target.value)} aria-label="Area" className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
          <option value="">Every area</option>
          {areas.map((a) => <option key={a} value={a}>{a} · {suite.scope.by_area?.[a]}</option>)}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind" className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
          <option value="">Every kind</option>
          {Object.entries(suite.scope.by_kind ?? {}).map(([k, n]) => <option key={k} value={k}>{KIND[k] ?? k} · {n}</option>)}
        </select>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200" />
        <button className={BTN} onClick={() => download(`/api/system-tests/suites/${suite.id}/cases.xlsx`, `test-cases-${suite.id}.xlsx`)}>⬇ Test cases (Excel)</button>
        <button className={BTN} onClick={() => download(`/api/system-tests/suites/${suite.id}/cases.csv`, `test-cases-${suite.id}.csv`)}>CSV</button>
      </div>
      <div className="max-h-[32rem] overflow-y-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-slate-900 text-[10px] uppercase tracking-wide text-slate-500">
            <tr><th className="px-3 py-2">Case</th><th className="px-3 py-2">Area</th><th className="px-3 py-2">Kind</th><th className="px-3 py-2">Title</th><th className="px-3 py-2">On</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {shown.slice(0, 400).map((c) => (
              <Fragment key={c.id}>
                <tr onClick={() => setOpen(open === c.id ? null : c.id)} className={`cursor-pointer hover:bg-slate-900/60 ${c.is_active ? "" : "opacity-50"}`}>
                  <td className="px-3 py-1.5 font-mono text-sky-300">{c.code}</td>
                  <td className="px-3 py-1.5 text-slate-400">{c.area}</td>
                  <td className="px-3 py-1.5 text-slate-400">{KIND[c.kind] ?? c.kind}</td>
                  <td className="px-3 py-1.5 text-slate-200">{c.title}</td>
                  <td className="px-3 py-1.5" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={c.is_active} aria-label={`Run ${c.code}`} onChange={async (e) => { await toggleCase(c.id, e.target.checked); onChanged(); }} />
                  </td>
                </tr>
                {open === c.id && (
                  <tr><td colSpan={5} className="bg-slate-950/60 px-4 py-2 text-slate-300">
                    <ol className="list-decimal space-y-0.5 pl-5">{c.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
                    <p className="mt-1 text-slate-400"><b>Expected:</b> {c.expected}</p>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {shown.length > 400 && <p className="px-3 py-2 text-xs text-slate-500">Showing 400 of {shown.length} - narrow it down.</p>}
      </div>
    </div>
  );
}

function Runs({ suite, runs, runId, onPick, onStarted, onRefresh }: {
  suite: SuiteSummary; runs: RunSummary[]; runId: number | null; onPick: (id: number) => void; onStarted: (r: RunSummary) => void; onRefresh: () => void;
}) {
  const areas = useMemo(() => Object.keys(suite.scope.by_area ?? {}).sort(), [suite]);
  const kinds = Object.keys(suite.scope.by_kind ?? {});
  const [pickAreas, setPickAreas] = useState<string[]>([]);
  const [pickKinds, setPickKinds] = useState<string[]>([]);
  const [useAi, setUseAi] = useState(false);
  const [error, setError] = useState("");
  const toggle = (list: string[], v: string, set: (x: string[]) => void) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  async function start() {
    setError("");
    try {
      onStarted(await startRun(suite.id, { areas: pickAreas, kinds: pickKinds, use_ai: useAi }));
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^\d+ [^:]*: /, "") : "Could not start the run.");
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
        <p className="text-xs text-slate-400">Run everything, or only some areas and kinds. It runs in the background - you can leave this page.</p>
        <div className="flex flex-wrap gap-1.5">
          {kinds.map((k) => (
            <button key={k} onClick={() => toggle(pickKinds, k, setPickKinds)} className={`${CHIP} ${pickKinds.includes(k) ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400"}`}>
              {KIND[k] ?? k} · {suite.scope.by_kind?.[k]}
            </button>
          ))}
        </div>
        <div className="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto">
          {areas.map((a) => (
            <button key={a} onClick={() => toggle(pickAreas, a, setPickAreas)} className={`${CHIP} ${pickAreas.includes(a) ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400"}`}>{a}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-slate-400">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> Plain-English cases use our AI model (slower; exactly what people get)
          </label>
          <span className="flex-1" />
          <button onClick={start} className={PRIMARY}>▶ Run {pickAreas.length || pickKinds.length ? "the selection" : "all"} in the background</button>
        </div>
        {error && <p className="text-sm text-red-400">{error}</p>}
      </div>
      <div className="grid gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <div className="space-y-1.5">
          {runs.map((r) => (
            <button key={r.id} onClick={() => onPick(r.id)}
              className={`block w-full rounded-lg border px-3 py-2 text-left text-xs ${r.id === runId ? "border-sky-500 bg-sky-500/10" : "border-slate-800 hover:border-slate-600"}`}>
              <span className="flex items-center gap-2"><span className="font-mono text-slate-200">{r.number}</span><Badge status={r.status} /></span>
              <span className="text-slate-500">{r.passed}✔ {r.failed}✘ {r.skipped}⏭ of {r.total} · {new Date(r.created_at).toLocaleString()}</span>
            </button>
          ))}
          {runs.length === 0 && <p className="text-xs text-slate-500">No runs yet.</p>}
        </div>
        {runId !== null ? <RunView key={runId} runId={runId} onRefresh={onRefresh} /> : <p className="text-sm text-slate-500">Start a run to see its evidence here.</p>}
      </div>
    </div>
  );
}

function RunView({ runId, onRefresh }: { runId: number; onRefresh: () => void }) {
  const [run, setRun] = useState<(RunSummary & { results: CaseResult[] }) | null>(null);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const r = await getRun(runId);
        if (stop) return;
        setRun(r);
        if (r.status === "queued" || r.status === "running") timer = window.setTimeout(tick, 2500);
        else onRefresh();
      } catch {
        timer = window.setTimeout(tick, 5000);
      }
    };
    void tick();
    return () => { stop = true; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  if (!run) return <p className="text-sm text-slate-500">Loading…</p>;
  const pct = run.total ? Math.round((run.done / run.total) * 100) : 0;
  const live = run.status === "queued" || run.status === "running";
  const order: Record<string, number> = { failed: 0, error: 1, skipped: 2, passed: 3 };
  const shown = run.results.filter((r) => !filter || r.status === filter).sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));
  return (
    <div className="min-w-0 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-slate-200">{run.number}</span>
        <Badge status={run.status} />
        <span className="text-xs text-slate-500">{run.done}/{run.total}{run.seconds != null ? ` · ${run.seconds}s` : ""}</span>
        <span className="flex-1" />
        {live && <button onClick={async () => setRun({ ...run, ...(await cancelRun(run.id)) })} className={BTN}>Cancel</button>}
        <button onClick={() => download(`/api/system-tests/runs/${run.id}/report.pdf`, `${run.number}-evidence.pdf`)} className={PRIMARY} disabled={run.done === 0}>📄 PDF report</button>
        <button onClick={() => download(`/api/system-tests/runs/${run.id}/report.pdf?all_steps=true`, `${run.number}-evidence-full.pdf`)} className={BTN} disabled={run.done === 0}
          title="Every case with its steps - passed ones too">PDF (every step)</button>
        <button onClick={() => download(`/api/system-tests/runs/${run.id}/results.xlsx`, `${run.number}-results.xlsx`)} className={BTN} disabled={run.done === 0}>⬇ Excel</button>
        <button onClick={() => openReport(run.id)} className={BTN} disabled={run.done === 0}>View in browser</button>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-800" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full ${run.failed + run.errors ? "bg-red-500" : "bg-emerald-500"} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex flex-wrap gap-1.5">
        {([["", `All · ${run.done}`], ["failed", `Failed · ${run.failed}`], ["error", `Errors · ${run.errors}`], ["skipped", `Skipped · ${run.skipped}`], ["passed", `Passed · ${run.passed}`]] as const).map(([v, l]) => (
          <button key={v} onClick={() => setFilter(v)} className={`${CHIP} ${filter === v ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400"}`}>{l}</button>
        ))}
      </div>
      <div className="max-h-[36rem] space-y-1.5 overflow-y-auto">
        {shown.map((r, i) => (
          <div key={`${r.case.id}-${i}`} className="rounded-lg border border-slate-800">
            <button onClick={() => setOpen(open === i ? null : i)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs">
              <Badge status={r.status} />
              <span className="font-mono text-sky-300">{r.case.code}</span>
              <span className="min-w-0 flex-1 truncate text-slate-200">{r.case.title}</span>
              <span className="text-slate-500">{r.duration_ms} ms</span>
            </button>
            {open === i && (
              <div className="space-y-2 border-t border-slate-800 px-3 py-2 text-xs">
                <p className="text-slate-400"><b className="text-slate-300">Expected:</b> {r.case.expected}<br /><b className="text-slate-300">Result:</b> {r.message}</p>
                {r.evidence.map((s) => (
                  <div key={s.step} className={`border-l-2 pl-3 ${s.status === "passed" ? "border-emerald-500" : s.status === "skipped" ? "border-amber-500" : "border-red-500"}`}>
                    <p className="text-slate-200">Step {s.step}: {s.say} <span className="text-slate-500">({s.status}{s.ms != null ? `, ${s.ms} ms` : ""})</span></p>
                    {s.checks && s.checks.length > 0 && (
                      <ul className="mt-1 space-y-0.5">
                        {s.checks.map((c, j) => (
                          <li key={j} className={c.passed ? "text-emerald-400" : "text-red-400"}>
                            {c.passed ? "✔" : "✘"} {c.check}: expected <code>{json(c.expected)}</code> - got <code>{json(c.actual)}</code>
                          </li>
                        ))}
                      </ul>
                    )}
                    {(s.note || s.error) && <p className="text-amber-300">{s.note || s.error}</p>}
                    <details className="mt-1">
                      <summary className="cursor-pointer text-slate-500">Request, response and what it would have written</summary>
                      <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-950 p-2 text-[11px] text-slate-400">{json({ request: s.request, response: s.response, writes: s.writes })}</pre>
                    </details>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {live && <p className="text-xs text-slate-500">Running… results appear as each case finishes.</p>}
      </div>
    </div>
  );
}
