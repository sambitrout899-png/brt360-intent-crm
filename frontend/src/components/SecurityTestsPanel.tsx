import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  cancelAssessment, downloadAssessment, getAssessment, listAssessments, recordProcedure, securityCatalog, startAssessment,
  type Assessment, type Catalog, type Refs, type Result, type Severity,
} from "../api/securityTests";

/** Admin -> Test Suite -> Security testing (VAPT): an assessment of the platform, its multi-tenancy and its AI against
 *  OWASP WSTG / ASVS / Top 10 / API Top 10 / LLM Top 10, rated with CVSS v3.1. Automated checks run in an isolated
 *  sandbox of the deployed code (two synthetic organizations attack each other), plus read-only checks of the live
 *  configuration and the public address; the manual VAPT procedures are recorded here. The PDF report is for customers. */

const BTN = "rounded-lg border border-slate-700 px-3 py-1.5 text-sm font-semibold text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-50";
const PRIMARY = "rounded-lg bg-sky-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50";
const SEVERITY: Record<Severity, string> = {
  critical: "text-rose-200 border-rose-500/60 bg-rose-600/30", high: "text-red-300 border-red-500/50 bg-red-500/15",
  medium: "text-amber-300 border-amber-500/50 bg-amber-500/10", low: "text-sky-300 border-sky-500/50 bg-sky-500/10", info: "text-slate-300 border-slate-600 bg-slate-800",
};
const STATUS: Record<string, { label: string; style: string; icon: string }> = {
  passed: { label: "Passed", style: "text-emerald-400", icon: "✔" }, failed: { label: "Failed", style: "text-red-400", icon: "✖" },
  error: { label: "Error", style: "text-fuchsia-400", icon: "!" }, skipped: { label: "Not run here", style: "text-amber-300", icon: "–" },
  not_applicable: { label: "N/A", style: "text-slate-400", icon: "○" }, pending: { label: "Pending", style: "text-slate-400", icon: "…" },
};
const RUN: Record<string, string> = {
  queued: "text-slate-300 border-slate-600 bg-slate-800", running: "text-sky-300 border-sky-500/40 bg-sky-500/10",
  completed: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10", cancelled: "text-slate-400 border-slate-600 bg-slate-800",
  error: "text-red-300 border-red-500/40 bg-red-500/10",
};
const WHERE: Record<string, string> = { sandbox: "Sandbox attack", live: "Live configuration (read-only)", edge: "Public address", manual: "Manual procedure" };
const GRADE: Record<string, string> = { "A+": "text-emerald-300", A: "text-emerald-300", B: "text-lime-300", C: "text-amber-300", D: "text-orange-400", F: "text-red-400" };
const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

function refsText(r: Refs): string[] {
  return [
    r.top10 && `OWASP ${r.top10}:2021`, r.api && `${r.api}:2023`, r.llm && `${r.llm} (LLM Top 10)`, r.asvs && `ASVS ${r.asvs}`, r.cwe, r.wstg,
    r.iso && `ISO 27001 A.${r.iso}`, r.nist && `NIST ${r.nist}`,
  ].filter(Boolean) as string[];
}

function Score({ a }: { a: Assessment }) {
  const pct = a.score ?? 0;
  const colour = pct >= 95 ? "#34d399" : pct >= 85 ? "#a3e635" : pct >= 70 ? "#fbbf24" : "#f87171";
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-16 w-16">
        <svg viewBox="0 0 36 36" className="h-16 w-16 -rotate-90">
          <circle cx="18" cy="18" r="15.5" fill="none" stroke="#1e293b" strokeWidth="3.5" />
          <circle cx="18" cy="18" r="15.5" fill="none" stroke={colour} strokeWidth="3.5" strokeDasharray={`${(pct / 100) * 97.4} 97.4`} strokeLinecap="round" />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-sm font-bold text-slate-100">{a.score == null ? "–" : `${Math.round(pct)}%`}</span>
      </div>
      <div>
        <div className={`text-3xl font-black leading-none ${GRADE[a.grade ?? ""] ?? "text-slate-300"}`}>{a.grade ?? "–"}</div>
        <div className="text-[11px] uppercase tracking-wide text-slate-500">grade</div>
      </div>
    </div>
  );
}

function ResultRow({ r, open, toggle }: { r: Result; open: boolean; toggle: () => void }) {
  const s = STATUS[r.status] ?? STATUS.pending;
  const finding = r.status === "failed" || r.status === "error";
  return (
    <Fragment>
      <button onClick={toggle} className={`grid w-full grid-cols-[1.25rem_7.5rem_1fr_auto] items-start gap-2 border-b border-slate-800/80 px-3 py-2 text-left text-sm hover:bg-slate-800/40 ${open ? "bg-slate-800/30" : ""}`}>
        <span className={`font-bold ${s.style}`}>{s.icon}</span>
        <span className="font-mono text-xs text-slate-400">{r.code}</span>
        <span>
          <span className="text-slate-200">{r.title}</span>
          <span className="block text-xs text-slate-500">{r.message}</span>
        </span>
        <span className="flex items-center gap-2">
          {finding && <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-bold uppercase ${SEVERITY[r.severity]}`}>{r.severity} {r.cvss.toFixed(1)}</span>}
          {!finding && <span className={`text-xs ${s.style}`}>{s.label}</span>}
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-b border-slate-800 bg-slate-950/60 px-4 py-3 text-xs">
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <div className="font-semibold uppercase tracking-wide text-slate-500">What it proves</div>
              <p className="mt-0.5 text-slate-300">{r.objective}</p>
              <div className="mt-2 font-semibold uppercase tracking-wide text-slate-500">How</div>
              <p className="mt-0.5 text-slate-300">{WHERE[r.where] ?? r.where}{r.duration_ms ? ` · ${r.duration_ms.toLocaleString()} ms` : ""}</p>
            </div>
            <div>
              <div className="font-semibold uppercase tracking-wide text-slate-500">Standards</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {refsText(r.refs).map((t) => <span key={t} className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-slate-300">{t}</span>)}
              </div>
              {r.vector && <p className="mt-2 font-mono text-[11px] text-slate-500">CVSS:3.1/{r.vector} · {r.cvss.toFixed(1)} if it fails</p>}
            </div>
          </div>
          {finding && <p className="rounded-md border-l-4 border-sky-500 bg-sky-500/10 px-3 py-2 text-slate-200"><b>Remediation:</b> {r.fix}</p>}
          {r.evidence.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="text-[10px] uppercase tracking-wide text-slate-500">
                  <tr><th className="py-1 pr-2" /><th className="py-1 pr-2">Probe</th><th className="py-1 pr-2">Request</th><th className="py-1 pr-2">Result</th><th className="py-1">Detail</th></tr>
                </thead>
                <tbody>
                  {r.evidence.map((e, i) => (
                    <tr key={i} className="border-t border-slate-800 align-top">
                      <td className={`py-1 pr-2 font-bold ${e.ok ? "text-emerald-400" : "text-red-400"}`}>{e.ok ? "✔" : "✖"}</td>
                      <td className="py-1 pr-2 text-slate-300">{e.probe}</td>
                      <td className="py-1 pr-2 font-mono text-[11px] text-slate-400">{e.request}</td>
                      <td className="py-1 pr-2 font-mono text-slate-300">{e.status ?? ""}</td>
                      <td className="max-w-md break-words py-1 font-mono text-[11px] text-slate-500">{e.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Fragment>
  );
}

function Procedure({ r, onSave }: { r: Result; onSave: (status: string, note: string, severity?: Severity) => Promise<void> }) {
  const [status, setStatus] = useState(r.status);
  const [note, setNote] = useState(r.tester_note);
  const [severity, setSeverity] = useState<Severity>(r.status === "failed" ? r.severity : "medium");
  const [busy, setBusy] = useState(false);
  const dirty = status !== r.status || note !== r.tester_note || (status === "failed" && severity !== r.severity);
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <span className="font-mono text-xs text-slate-500">{r.code}</span> <span className="text-sm font-semibold text-slate-200">{r.title}</span>
          {r.tool && <div className="text-[11px] text-slate-500">Tools: {r.tool}</div>}
        </div>
        <span className={`text-xs font-semibold ${STATUS[r.status]?.style}`}>{STATUS[r.status]?.label}{r.recorded_at ? ` · ${new Date(r.recorded_at).toLocaleDateString()}` : ""}</span>
      </div>
      {r.steps && (
        <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-xs text-slate-400">
          {r.steps.map((s) => <li key={s}>{s}</li>)}
        </ol>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {(["pending", "passed", "failed", "not_applicable"] as const).map((k) => (
          <button key={k} onClick={() => setStatus(k)}
            className={`rounded-md border px-2 py-0.5 text-xs ${status === k ? "border-sky-500 bg-sky-500/15 text-sky-200" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
            {k === "failed" ? "Finding" : STATUS[k].label}
          </button>
        ))}
        {status === "failed" && (
          <select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)} className="rounded-md border border-slate-700 bg-slate-950 px-2 py-0.5 text-xs text-slate-200">
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
      </div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="What was done, by whom, the tool output / report reference, and any finding"
        className="mt-2 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600" />
      {dirty && (
        <button disabled={busy} onClick={async () => { setBusy(true); try { await onSave(status, note, status === "failed" ? severity : undefined); } finally { setBusy(false); } }} className={`${PRIMARY} mt-1 !py-1 text-xs`}>
          {busy ? "Saving..." : "Record"}
        </button>
      )}
    </div>
  );
}

export default function SecurityTestsPanel() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [list, setList] = useState<Assessment[]>([]);
  const [current, setCurrent] = useState<(Assessment & { results: Result[] }) | null>(null);
  const [name, setName] = useState("");
  const [cats, setCats] = useState<string[]>([]);
  const [filter, setFilter] = useState<"all" | "findings" | "passed" | "other">("all");
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async (id?: number) => {
    try {
      const rows = await listAssessments();
      setList(rows);
      const pick = id ?? rows[0]?.id;
      if (pick) setCurrent(await getAssessment(pick));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    securityCatalog().then((c) => { setCatalog(c); setCats(c.categories.map((k) => k.key)); }).catch((e) => setError(String(e)));
    refresh();
  }, [refresh]);

  // follow a running assessment
  useEffect(() => {
    if (!current || !["queued", "running"].includes(current.status)) return;
    const timer = window.setInterval(() => refresh(current.id), 3000);
    return () => window.clearInterval(timer);
  }, [current, refresh]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const a = await startAssessment(name, cats.length === catalog?.categories.length ? [] : cats);
      setName("");
      await refresh(a.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const results = useMemo(() => current?.results ?? [], [current]);
  const automated = results.filter((r) => !r.code.startsWith("VAPT"));
  const manual = results.filter((r) => r.code.startsWith("VAPT"));
  const shown = automated.filter((r) => filter === "all" || (filter === "findings" ? ["failed", "error"].includes(r.status) : filter === "passed" ? r.status === "passed" : ["skipped", "not_applicable"].includes(r.status)));
  const byCat = (catalog?.categories ?? []).map((c) => ({ ...c, rows: shown.filter((r) => r.cat === c.key) })).filter((c) => c.rows.length);
  const open_ = current?.summary.open;
  const running = current && ["queued", "running"].includes(current.status);

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-slate-800 bg-gradient-to-br from-slate-900 to-slate-950 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <h3 className="text-lg font-bold text-slate-100">🛡️ Security testing (VAPT)</h3>
            <p className="mt-1 text-sm text-slate-400">
              An assessment of the platform, its multi-tenancy and its AI. {catalog ? `${catalog.checks.length} automated controls and ${catalog.procedures.length} manual VAPT procedures` : "Automated controls and manual VAPT procedures"},
              rated with CVSS v3.1. Automated attacks run against the deployed code in an isolated sandbox - two synthetic organizations try to read, change and export each other's data -
              so nothing touches your live records. The PDF report is written for customers and auditors.
            </p>
          </div>
          <div className="flex max-w-md flex-wrap gap-1.5">
            {(catalog?.methodology ?? []).map((m) => <span key={m.name} title={m.about} className="rounded-full border border-slate-700 bg-slate-900 px-2 py-0.5 text-[11px] text-slate-300">{m.name}</span>)}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {(catalog?.categories ?? []).map((c) => {
            const on = cats.includes(c.key);
            return (
              <button key={c.key} title={c.blurb} onClick={() => setCats(on ? cats.filter((k) => k !== c.key) : [...cats, c.key])}
                className={`rounded-lg border px-2.5 py-1 text-xs ${on ? "border-sky-500/70 bg-sky-500/10 text-sky-200" : "border-slate-700 text-slate-500 hover:border-slate-500"}`}>
                {on ? "✔ " : ""}{c.name} <span className="text-slate-500">{c.count}</span>
              </button>
            );
          })}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Release 2026.11 - pre go-live)" maxLength={200}
            className="w-80 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 placeholder:text-slate-600" />
          <button onClick={start} disabled={busy || !cats.length || !!running} className={PRIMARY}>{busy ? "Starting..." : "Run assessment"}</button>
          <span className="text-xs text-slate-500">Runs in the background - about 2-4 minutes for everything.</span>
        </div>
        {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
      </section>

      <div className="grid gap-4 xl:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className="space-y-1.5">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Assessments</h4>
          {list.map((a) => (
            <button key={a.id} onClick={() => refresh(a.id)}
              className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${current?.id === a.id ? "border-sky-500 bg-sky-500/10" : "border-slate-800 bg-slate-900/50 hover:border-slate-600"}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-slate-400">{a.number}</span>
                <span className={`text-lg font-black ${GRADE[a.grade ?? ""] ?? "text-slate-500"}`}>{a.grade ?? ""}</span>
              </div>
              <div className="truncate text-slate-200">{a.name}</div>
              <div className="flex items-center gap-2 text-[11px] text-slate-500">
                <span className={`rounded border px-1 ${RUN[a.status]}`}>{a.status}</span>
                {new Date(a.created_at).toLocaleString()}
              </div>
            </button>
          ))}
          {!list.length && <p className="text-sm text-slate-500">No assessments yet - run the first one above.</p>}
        </aside>

        {current && (
          <main className="space-y-4">
            <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="flex items-center gap-5">
                  <Score a={current} />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm text-slate-400">{current.number}</span>
                      <span className={`rounded-md border px-2 py-0.5 text-[11px] font-bold ${RUN[current.status]}`}>{current.status}</span>
                    </div>
                    <div className="text-base font-semibold text-slate-100">{current.name}</div>
                    <div className="text-xs text-slate-500">
                      {current.passed} verified · {current.failed + current.errors} findings · {current.skipped} N/A or not run
                      {current.seconds != null ? ` · ${current.seconds}s` : ""}
                      {current.summary.sandbox ? ` · ${current.summary.sandbox.requests.toLocaleString()} attack requests on ${current.summary.sandbox.endpoints} endpoints` : ""}
                    </div>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {running && <button className={BTN} onClick={async () => { await cancelAssessment(current.id); refresh(current.id); }}>Cancel</button>}
                  <button className={PRIMARY} disabled={!!running} onClick={() => downloadAssessment(current, "pdf").catch((e) => setError(String(e)))}>📄 PDF report</button>
                  <button className={BTN} disabled={!!running} onClick={() => downloadAssessment(current, "xlsx").catch((e) => setError(String(e)))}>⬇ Excel</button>
                </div>
              </div>
              {running && (
                <div className="mt-3">
                  <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                    <div className="h-2 rounded-full bg-sky-500 transition-all" style={{ width: `${current.total ? (100 * current.done) / current.total : 5}%` }} />
                  </div>
                  <p className="mt-1 text-xs text-slate-500">{current.done} of {current.total} controls checked...</p>
                </div>
              )}
              {current.status === "error" && <p className="mt-2 text-sm text-red-400">{current.message}</p>}
              {open_ && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {SEVERITIES.map((s) => (
                    <span key={s} className={`rounded-lg border px-3 py-1 text-xs font-semibold capitalize ${open_[s] ? SEVERITY[s] : "border-slate-800 text-slate-600"}`}>
                      {s} <b className="ml-1 text-sm">{open_[s] ?? 0}</b>
                    </span>
                  ))}
                  {current.summary.manual && current.summary.manual.total > 0 && (
                    <span className="rounded-lg border border-slate-700 px-3 py-1 text-xs text-slate-400">Manual procedures recorded <b className="text-slate-200">{current.summary.manual.recorded}/{current.summary.manual.total}</b></span>
                  )}
                </div>
              )}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-900/40">
              <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-2">
                {([["all", `All (${automated.length})`], ["findings", `Findings (${automated.filter((r) => ["failed", "error"].includes(r.status)).length})`],
                  ["passed", `Verified (${automated.filter((r) => r.status === "passed").length})`], ["other", `N/A / not run (${automated.filter((r) => ["skipped", "not_applicable"].includes(r.status)).length})`]] as const).map(([k, label]) => (
                  <button key={k} onClick={() => setFilter(k)} className={`rounded-md px-2.5 py-1 text-xs ${filter === k ? "bg-sky-500/15 font-semibold text-sky-200" : "text-slate-400 hover:text-slate-200"}`}>{label}</button>
                ))}
              </div>
              {byCat.map((c) => (
                <div key={c.key}>
                  <div className="flex items-center justify-between bg-slate-950/50 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                    <span>{c.name}</span>
                    <span className="text-slate-500">{c.rows.filter((r) => r.status === "passed").length}/{c.rows.length} verified</span>
                  </div>
                  {c.rows.map((r) => <ResultRow key={r.id} r={r} open={open === r.id} toggle={() => setOpen(open === r.id ? null : r.id)} />)}
                </div>
              ))}
              {!byCat.length && <p className="px-3 py-6 text-center text-sm text-slate-500">{running ? "Results appear here as each control is checked." : "Nothing to show for this filter."}</p>}
            </section>

            {manual.length > 0 && (
              <section className="space-y-2">
                <div>
                  <h4 className="text-sm font-bold text-slate-100">Manual VAPT procedures</h4>
                  <p className="text-xs text-slate-500">What a penetration tester does by hand (PTES / NIST SP 800-115). Record each outcome with the evidence reference - it goes into the report.</p>
                </div>
                <div className="grid gap-2 lg:grid-cols-2">
                  {manual.map((r) => (
                    <Procedure key={`${r.id}-${r.status}-${r.recorded_at}`} r={r} onSave={async (status, note, severity) => {
                      try { setCurrent(await recordProcedure(current.id, r.code, status, note, severity)); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
                    }} />
                  ))}
                </div>
              </section>
            )}
          </main>
        )}
      </div>
    </div>
  );
}
