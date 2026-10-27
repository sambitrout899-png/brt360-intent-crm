import { useState } from "react";
import type { IntentDef, ProcessFit } from "../../api/types";
import BuildFromScratch from "./BuildFromScratch";

const KIND: Record<ProcessFit["items"][number]["kind"], { icon: string; label: string }> = {
  step: { icon: "🧭", label: "Vanilla step" },
  data: { icon: "🗂️", label: "Information to capture" },
  config: { icon: "⚙️", label: "Rule / automation" },
  rule: { icon: "⚡", label: "Automation" },
  note: { icon: "📝", label: "Note" },
  new_step: { icon: "🆕", label: "New step" },
  context: { icon: "🏢", label: "About your business" },
  intro: { icon: "🏷️", label: "Name" },
  variant: { icon: "🔀", label: "Process variant" },
};

/** A described process, read against the vanilla processes: which one it is,
 * what is already in it, and what is missing - fields to capture, rules and
 * automations (built by the process builder below, on consent) and new steps
 * (each built from scratch as a new action, on consent). */
export default function ProcessFitReport({
  fit,
  canBuild,
  onBuilt,
}: {
  fit: ProcessFit;
  intents: IntentDef[];
  /** an administrator - may build the missing pieces */
  canBuild: boolean;
  onBuilt?: (summary: string) => void;
}) {
  const [building, setBuilding] = useState<string | null>(null);
  const [built, setBuilt] = useState<Record<string, string>>({});
  const said = fit.process.steps.filter((s) => s.said).length;
  const v = fit.variant;
  const auto = new Set(v?.automated ?? []);
  const skipped = new Set((v?.skipped ?? []).filter((c) => !auto.has(c)));

  return (
    <div className="space-y-4 rounded-2xl border border-emerald-500/25 bg-emerald-500/[0.03] p-4">
      {fit.data_only ? (
        <p className="text-[11px] font-semibold uppercase tracking-wider text-sky-300">🗂️ New custom fields - Page Designer</p>
      ) : (<>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-emerald-300">Closest vanilla process</span>
        <span className="text-base font-semibold text-slate-100">{fit.process.label}</span>
        {fit.process.segment && <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{fit.process.segment}</span>}
        <span className="text-[11px] text-slate-500" title="How clearly it beats the next best match">match {Math.round(fit.confidence * 100)}%</span>
        {fit.alternatives.length > 0 && (
          <span className="text-[11px] text-slate-500">· also close: {fit.alternatives.map((a) => a.label).join(", ")}</span>
        )}
      </div>

      <div>
        <p className="mb-1.5 text-[11px] text-slate-500">
          Its steps - the ones you described are highlighted ({said} of {fit.process.steps.length}); the rest run as standard.
        </p>
        <div className="flex flex-wrap items-center gap-1">
          {fit.process.steps.map((s, i) => (
            <span key={s.code} className="flex items-center gap-1">
              <span
                title={auto.has(s.code) ? `${s.code} - runs by itself in the variant` : skipped.has(s.code) ? `${s.code} - left out in the variant` : s.code}
                className={`rounded-md px-1.5 py-0.5 text-[11px] ${
                  auto.has(s.code) ? "bg-violet-500/20 italic text-violet-200"
                    : skipped.has(s.code) ? "bg-slate-900 text-slate-600 line-through"
                      : s.said ? "bg-emerald-500/20 font-semibold text-emerald-200" : "bg-slate-800/80 text-slate-400"}`}
              >
                {s.icon} {s.label}{auto.has(s.code) ? " ⚡" : ""}
              </span>
              {i < fit.process.steps.length - 1 && <span className="text-[10px] text-slate-600">→</span>}
            </span>
          ))}
        </div>
      </div>
      </>)}

      {v && (
        <div className="space-y-2 rounded-xl border border-violet-500/30 bg-violet-500/[0.05] p-3 text-xs">
          <p className="font-semibold text-violet-200">
            🔀 Variant for {v.condition.segment} prospects whose {v.condition.field} is “{v.condition.value}”
          </p>
          <ul className="space-y-0.5 text-slate-300">
            <li>
              • {v.condition.exists
                ? <>Uses the existing field <b>{v.condition.existing}</b> on the {v.condition.page}.</>
                : <>Adds a <b>{v.condition.field}</b> field to the {v.condition.page ?? "prospect page"}.</>}
            </li>
            {v.automated_labels.length > 0 && (
              <li>• Right after the prospect is added: <b>{v.automated_labels.join(" → ")}</b> run by themselves ⚡{v.product ? <> (product <b>{v.product}</b>)</> : null}.</li>
            )}
            {v.target_label && <li>• Then <b>{v.target_label}</b> is offered straight away{v.product ? " - the product is already on it" : ""}.</li>}
            {v.skipped_labels.filter((l) => !v.automated_labels.includes(l)).length > 0 && (
              <li>• Left out: {v.skipped_labels.filter((l) => !v.automated_labels.includes(l)).join(", ")}.</li>
            )}
            <li className="text-slate-500">• Every other prospect follows the vanilla process unchanged.</li>
          </ul>
          {v.conflicts.map((c) => (
            <div key={c.what} className="rounded-lg border border-amber-500/40 bg-amber-500/[0.07] p-2 text-amber-100">
              <p className="font-semibold">⚠ Not built - {c.what}</p>
              <p className="mt-0.5 text-amber-100/80">{c.why}</p>
              <p className="mt-0.5 text-amber-100/80"><b>Instead:</b> {c.instead}</p>
            </div>
          ))}
        </div>
      )}

      <ul className="space-y-1.5">
        {fit.items.map((it, i) => {
          const gap = it.status === "gap";
          const k = KIND[it.kind];
          return (
            <li key={i} className={`rounded-lg border px-3 py-2 text-xs ${gap ? "border-amber-500/30 bg-amber-500/[0.05]" : "border-slate-800 bg-slate-950/40"}`}>
              <div className="flex flex-wrap items-start gap-2">
                <span className={`mt-0.5 font-bold ${gap ? "text-amber-300" : "text-emerald-400"}`}>{gap ? "＋" : "✓"}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-slate-200">“{it.text}”</span>
                  <span className="mt-0.5 block text-slate-400">
                    <span className="mr-1">{k.icon}</span>
                    <span className="font-semibold text-slate-300">{k.label}</span> - {it.note}
                  </span>
                  {it.fields && it.fields.length > 0 && (
                    <span className="mt-1 flex flex-wrap gap-1">
                      {it.fields.map((f) => (
                        <span key={f.label} className={`rounded px-1.5 py-0.5 text-[10px] ${f.exists ? "bg-slate-800 text-slate-400" : "bg-sky-500/15 text-sky-200"}`}
                          title={f.exists ? `Already on the page as “${f.existing}”` : `New ${f.data_type} field${f.mandatory ? ", mandatory" : ""}`}>
                          {f.exists ? "✓ " : "+ "}{f.label} <span className="opacity-70">({f.exists ? f.existing : f.data_type}{!f.exists && f.mandatory ? ", required" : ""})</span>
                        </span>
                      ))}
                    </span>
                  )}
                </span>
                {it.kind === "new_step" && !fit.journey && canBuild && !built[it.text] && building !== it.text && (
                  <button onClick={() => setBuilding(it.text)}
                    className="shrink-0 rounded-md border border-amber-500/40 px-2 py-1 text-[11px] font-semibold text-amber-200 hover:bg-amber-500/10">
                    Build it as a new action
                  </button>
                )}
                {built[it.text] && <span className="shrink-0 text-[11px] font-semibold text-emerald-300">✓ built as {built[it.text]}</span>}
              </div>
              {building === it.text && (
                <div className="mt-2">
                  <BuildFromScratch
                    request={it.text}
                    onCancel={() => setBuilding(null)}
                    onBuilt={(b) => {
                      setBuilt((m) => ({ ...m, [it.text]: b.intent.label ?? b.code }));
                      setBuilding(null);
                      onBuilt?.(`New step built: ${b.intent.label ?? b.code}`);
                    }}
                  />
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <p className="text-[11px] text-slate-500">
        {fit.gaps === 0
          ? "Nothing to build - your set-up already runs all of this."
          : fit.journey
            ? "The journey is built below as you described it (stages vanilla already covers are shown above)."
            : canBuild
              ? "The fields, rules and automations are designed below - rehearsed first, built only when you agree. New steps are built one by one above."
              : "An administrator can build the missing pieces - share this with yours."}
      </p>
    </div>
  );
}
