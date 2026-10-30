import { useEffect, useRef, useState } from "react";
import { downloadArchitectDocument, processArchitect, processArchitectImplement } from "../../api/client";
import type { FunctionMap } from "../../api/functionMapTypes";
import type { ArchitectAnalysis, ProcessPlacement } from "../../api/types";

const KIND_TONE: Record<string, string> = {
  approval: "text-emerald-300", flow: "text-violet-300", page: "text-sky-300", assignment: "text-amber-300", journey: "text-teal-300", engineering: "text-orange-300",
};

function condition(when: ArchitectAnalysis["links"][number]["when"]): string {
  if (!when) return "";
  if (when.operator === "otherwise") return " - otherwise";
  const parts = (when.field ?? "").split(".");
  const field = (parts[0] === "customer" ? parts : parts.slice(1)).join(" ").replace(/_/g, " ");
  return ` - only when ${field} ${when.operator.replace("_", " ")}${when.value ? ` ${when.value}` : ""}`;
}

/** The AI solution architect: the whole BRD read into a design for this
 * organization - requirements and how each is met, the process steps and
 * hand-offs, configuration, gaps, assumptions and alternatives - verified
 * against the live setup, then put into a draft version of the Functional Map
 * (built when it goes live) and written up as a Word document. */
export default function ArchitectPanel({ text, name, placement, onClose }: {
  text: string;
  name?: string | null;
  placement?: ProcessPlacement | null;
  onClose?: () => void;
}) {
  const [analysis, setAnalysis] = useState<ArchitectAnalysis | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"" | "design" | "implement" | "doc">("design");
  const [error, setError] = useState("");
  const [version, setVersion] = useState<FunctionMap | null>(null);
  const started = useRef(false);

  async function run(job: () => Promise<ArchitectAnalysis>) {
    setBusy("design");
    setError("");
    try {
      const out = await job();
      setAnalysis(out);
      setDraft(Object.fromEntries(out.questions.filter((q) => q.default).map((q) => [q.id, q.default as string])));
    } catch (e) {
      setError(e instanceof Error ? e.message : "The architect couldn't design it");
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void run(() => processArchitect({ text, name, placement }));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, for the document it was opened with
  }, []);

  function answer() {
    if (!analysis) return;
    const own = analysis.questions.filter((q) => !q.builder && draft[q.id]);
    const builder = Object.fromEntries(analysis.questions.filter((q) => q.builder && draft[q.id]).map((q) => [q.id, draft[q.id]]));
    const answers = { ...analysis.builder.answers, ...builder };
    // the architect's own questions take a new design round; the builder's are checked again on the same design
    void run(() =>
      own.length
        ? processArchitect({ text: analysis.text ?? text, name, placement, decisions: Object.fromEntries(own.map((q) => [`${q.id} (${q.prompt})`, draft[q.id]])), answers })
        : processArchitect({ analysis, answers }),
    );
  }

  async function implement() {
    if (!analysis) return;
    setBusy("implement");
    setError("");
    try {
      setVersion(await processArchitectImplement(analysis));
    } catch (e) {
      setError(e instanceof Error ? e.message : "It couldn't be put into a map version");
    } finally {
      setBusy("");
    }
  }

  const sec = "space-y-2 rounded-2xl border border-slate-800 bg-slate-900/40 p-4 sm:p-5";
  const h = "text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500";

  if (!analysis) {
    return (
      <section className={sec}>
        <p className="text-sm text-slate-300">{busy ? "🧠 Reading the whole document, matching it to your setup and designing the process…" : error}</p>
        {busy && <p className="text-xs text-slate-500">A long BRD can take up to a minute and a half.</p>}
        {error && onClose && <button onClick={onClose} className="text-xs text-sky-400">Back</button>}
      </section>
    );
  }
  const gaps = [...analysis.gaps, ...analysis.builder.engineering.map((e) => ({ requirement: "", need: e.need, why: e.why, effort: e.effort, workaround: e.workaround }))];
  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-sky-500/30 bg-sky-500/[0.05] p-4 sm:p-5">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className={h}>🧠 Solution design</p>
            <h3 className="mt-1 text-lg font-semibold text-slate-50">{analysis.name}</h3>
            <p className="mt-1 text-sm text-slate-300">{analysis.summary}</p>
            <p className="mt-2 text-xs text-slate-500">
              Designed by {analysis.model}
              {analysis.by === "ai+review" ? " - reviewed and repaired in a second round" : ""} · checked against your live setup ·{" "}
              {analysis.requirements.length} requirement{analysis.requirements.length === 1 ? "" : "s"}
              {analysis.by === "rules" && " · no AI model is set for this organization (Admin → AI Settings), so each sentence was read on its own"}
            </p>
          </div>
          <button disabled={busy !== ""} onClick={async () => { setBusy("doc"); try { await downloadArchitectDocument(analysis); } catch (e) { setError(String(e)); } finally { setBusy(""); } }}
            className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:border-sky-500">
            ⬇ Design document (Word)
          </button>
        </div>
        <p className="mt-3 text-sm text-slate-200">
          <span className="font-semibold">Where it goes:</span>{" "}
          {analysis.placement.mode === "append" ? `extends “${analysis.placement.label}”` : `a new process “${analysis.placement.label}”`}
          {analysis.placement.why ? <span className="text-slate-400"> - {analysis.placement.why}</span> : null}
        </p>
      </section>

      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-300">{error}</p>}

      {analysis.questions.length > 0 && (
        <section className="space-y-3 rounded-2xl border border-amber-500/30 bg-amber-500/[0.05] p-4 sm:p-5">
          <h4 className="text-sm font-semibold text-amber-100">
            {analysis.questions.length === 1 ? "One thing blocks the design" : `${analysis.questions.length} things block the design`} - everything else I decided (see assumptions)
          </h4>
          {analysis.questions.map((q) => (
            <label key={q.id} className="block text-sm text-slate-200">
              {q.prompt}
              {q.options.length > 0 ? (
                <select value={draft[q.id] ?? ""} onChange={(e) => setDraft((d) => ({ ...d, [q.id]: e.target.value }))}
                  className="mt-1 block w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm">
                  <option value="">Choose…</option>
                  {q.options.map((o, i) => <option key={o} value={q.values?.[i] ?? o}>{o}</option>)}
                </select>
              ) : (
                <input value={draft[q.id] ?? ""} onChange={(e) => setDraft((d) => ({ ...d, [q.id]: e.target.value }))}
                  className="mt-1 block w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" />
              )}
            </label>
          ))}
          <button onClick={answer} disabled={busy !== "" || analysis.questions.some((q) => !draft[q.id])}
            className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-40">
            {busy === "design" ? "Designing…" : "Continue with these answers"}
          </button>
        </section>
      )}

      <section className={sec}>
        <h4 className={h}>Requirements and how each is met</h4>
        <ul className="space-y-1.5 text-[13px]">
          {analysis.coverage.map((c) => {
            const fit = analysis.fit.find((f) => f.requirement === c.requirement);
            return (
              <li key={c.requirement} className="rounded-lg bg-slate-950/50 px-3 py-2">
                <span className="font-mono text-[11px] text-slate-500">{c.requirement}</span> <span className="text-slate-100">{c.text}</span>
                {fit && <span className={`ml-2 rounded px-1.5 text-[10px] font-semibold ${fit.status === "gap" ? "bg-orange-500/15 text-orange-300" : fit.status === "covered" ? "bg-emerald-500/15 text-emerald-300" : "bg-sky-500/15 text-sky-300"}`}>{fit.status}</span>}
                <span className={`mt-0.5 block text-xs ${c.by.length ? "text-slate-400" : "text-amber-300"}`}>→ {c.by.length ? c.by.join(" · ") : "not carried by anything yet"}</span>
              </li>
            );
          })}
        </ul>
      </section>

      {(analysis.steps.length > 0 || analysis.links.length > 0) && (
        <section className={sec}>
          <h4 className={h}>The process on the map</h4>
          {analysis.steps.length > 0 && (
            <ol className="list-decimal space-y-1 pl-5 text-[13px] text-slate-200">
              {analysis.steps.map((s) => <li key={s.code}>{s.label} <span className="text-slate-500">({s.code}) - {s.why}</span></li>)}
            </ol>
          )}
          {analysis.links.map((l, i) => (
            <p key={i} className="text-[13px] text-slate-300">
              {l.kind === "auto" ? "⚡" : "→"} {l.from_label} → {l.to_label}
              <span className="text-violet-300">{condition(l.when)}</span>
              {l.replaces_existing && <span className="text-amber-300"> (replaces what came next)</span>}
              {l.why && <span className="text-slate-500"> - {l.why}</span>}
            </p>
          ))}
        </section>
      )}

      {analysis.builder.components.length > 0 && (
        <section className={sec}>
          <h4 className={h}>Configuration</h4>
          <div className="grid gap-3 lg:grid-cols-2">
            {analysis.builder.components.map((c) => (
              <article key={c.id} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
                <p className={`text-[11px] font-semibold uppercase tracking-wider ${KIND_TONE[c.kind] ?? "text-slate-400"}`}>{c.engine}</p>
                <p className="text-sm font-semibold text-slate-100">{c.title}</p>
                <ul className="mt-1 list-disc pl-5 text-xs text-slate-300">{c.lines.map((l) => <li key={l}>{l}</li>)}</ul>
                {c.warnings.map((w) => <p key={w} className="mt-1 text-xs text-amber-300">⚠ {w}</p>)}
              </article>
            ))}
          </div>
        </section>
      )}

      {gaps.length > 0 && (
        <section className={sec}>
          <h4 className={h}>Gaps - need an engineering change, not built</h4>
          {gaps.map((g, i) => (
            <p key={i} className="text-[13px] text-slate-300">
              <span className="font-semibold text-orange-200">{g.need}</span> <span className="text-slate-500">(effort {g.effort})</span> - {g.why}
              {g.workaround && <span className="text-slate-400"> Meanwhile: {g.workaround}</span>}
            </p>
          ))}
        </section>
      )}

      {(analysis.assumptions.length > 0 || analysis.alternatives.length > 0 || analysis.notes.length > 0) && (
        <section className={sec}>
          {analysis.assumptions.length > 0 && (
            <>
              <h4 className={h}>Decided for you - assumptions (change any by answering differently or editing the draft)</h4>
              <ul className="list-disc space-y-0.5 pl-5 text-[13px] text-slate-300">{analysis.assumptions.map((a) => <li key={a.text}>{a.text}</li>)}</ul>
            </>
          )}
          {analysis.alternatives.length > 0 && (
            <>
              <h4 className={`${h} pt-2`}>Alternatives considered</h4>
              <ul className="list-disc space-y-0.5 pl-5 text-[13px] text-slate-400">{analysis.alternatives.map((a) => <li key={a.option}>{a.option} - not chosen: {a.why_not}</li>)}</ul>
            </>
          )}
          {analysis.notes.length > 0 && (
            <>
              <h4 className={`${h} pt-2`}>What the verification changed</h4>
              <ul className="list-disc space-y-0.5 pl-5 text-[13px] text-amber-200/90">{analysis.notes.map((n) => <li key={n}>{n}</li>)}</ul>
            </>
          )}
        </section>
      )}

      {version?.version ? (
        <section className="rounded-2xl border border-emerald-500/40 bg-emerald-500/10 p-4 sm:p-5">
          <p className="text-[15px] font-semibold text-emerald-100">
            ✓ It is in draft map version {version.version.label ?? `v${version.version.number}`} “{version.version.name}”
            {version.version.workspace ? ` in your workspace “${version.version.workspace}”` : ""} - nothing is built yet.
          </p>
          <p className="mt-1 text-sm text-emerald-200/80">
            Open Admin → Functional Map and pick it: look at the process on the map, ▶ Simulate it, then{" "}
            {version.version.workspace ? "submit the workspace - it goes live with its release." : "Activate… (the root administrator)."}
          </p>
        </section>
      ) : (
        <div className="flex flex-wrap items-center justify-end gap-2">
          {!analysis.ready && analysis.questions.length === 0 && <span className="text-xs text-amber-300">Nothing in it can be built yet - see the gaps.</span>}
          <button onClick={implement} disabled={busy !== "" || !analysis.ready}
            className="rounded-lg bg-emerald-600 px-5 py-2.5 text-sm font-bold text-white hover:bg-emerald-500 disabled:opacity-40">
            {busy === "implement" ? "Putting it in…" : "Put it into a draft map version"}
          </button>
        </div>
      )}
    </div>
  );
}
