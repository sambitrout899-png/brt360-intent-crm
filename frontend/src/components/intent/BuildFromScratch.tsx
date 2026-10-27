import { useEffect, useRef, useState } from "react";
import { scratchBuild, scratchDraft } from "../../api/client";
import type { ScratchDraft, ScratchField } from "../../api/conflictTypes";
import type { IntentDef } from "../../api/types";

const TYPES = ["text", "textarea", "number", "money", "percent", "picklist", "boolean", "list"];

/** A request nothing in the set-up covers: the new action I'd build for it -
 * name, phrases, form, what it does - editable, and built only when the admin
 * agrees. It is an ordinary custom action afterwards (Admin → Intents, Flow Studio). */
export default function BuildFromScratch({
  request,
  draft: given,
  journalId,
  onBuilt,
  onCancel,
  autonomous = false,
}: {
  request: string;
  draft?: ScratchDraft | null;
  journalId?: number;
  onBuilt: (built: { intent: IntentDef; code: string }) => void;
  onCancel?: () => void;
  autonomous?: boolean;
}) {
  const [draft, setDraft] = useState<ScratchDraft | null>(given ?? null);
  const [phrases, setPhrases] = useState((given?.phrases ?? []).join(", "));
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(!given);
  const [error, setError] = useState("");
  const autoBuilt = useRef(false);
  useEffect(() => {
    if (!autonomous || !draft || busy || autoBuilt.current || draft.warnings.length) return;
    autoBuilt.current = true;
    void build();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autonomous, draft, busy]);

  useEffect(() => {
    if (given) return;
    let cancelled = false;
    scratchDraft(request)
      .then((d) => !cancelled && (setDraft(d), setPhrases(d.phrases.join(", "))))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Could not draft it"))
      .finally(() => !cancelled && setBusy(false));
    return () => {
      cancelled = true;
    };
  }, [request, given]);

  if (!draft) {
    return (
      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/[0.05] p-4 text-sm text-slate-300">
        {error ? <span className="text-red-400">{error}</span> : <span className="animate-pulse">🏗️ Drafting a new action for “{request}”…</span>}
      </div>
    );
  }

  const setField = (i: number, patch: Partial<ScratchField>) => setDraft({ ...draft, fields: draft.fields.map((f, j) => (j === i ? { ...f, ...patch } : f)) });

  async function build() {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const spec = { ...draft, phrases: phrases.split(",").map((p) => p.trim()).filter(Boolean) };
      onBuilt(await scratchBuild(spec, journalId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not build it");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4 rounded-2xl border border-emerald-500/40 bg-gradient-to-br from-emerald-950/40 via-slate-900 to-slate-950 p-5">
      <div>
        <h3 className="text-base font-bold text-emerald-100">🏗️ Nothing does this yet - shall I build it from scratch?</h3>
        <p className="text-sm text-slate-400">Here is what I'd make. Change anything, then agree - nothing is built until you do.</p>
      </div>

      <div className="grid gap-3 md:grid-cols-[4rem_1fr]">
        <label className="text-xs text-slate-400">
          Icon
          <input
            value={draft.icon}
            onChange={(e) => setDraft({ ...draft, icon: e.target.value })}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-center text-lg"
          />
        </label>
        <label className="text-xs text-slate-400">
          The new action
          <input
            value={draft.label}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm font-semibold text-slate-100"
          />
        </label>
      </div>
      <label className="block text-xs text-slate-400">
        It answers to (comma-separated)
        <input value={phrases} onChange={(e) => setPhrases(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100" />
      </label>

      <div className="space-y-2">
        <p className="text-xs font-bold uppercase tracking-wide text-slate-500">
          Its form{draft.entity_type ? ` · acts on a ${draft.entity_type.replace(/_/g, " ")} (asked for first)` : ""}
        </p>
        {draft.fields.length === 0 && <p className="text-xs text-slate-500">No details to ask for - add some below if it needs them.</p>}
        {draft.fields.map((f, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input value={f.label} onChange={(e) => setField(i, { label: e.target.value, key: "" })} className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100" />
            <select value={f.type} onChange={(e) => setField(i, { type: e.target.value })} className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
              {[...new Set([f.type, ...TYPES])].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            {f.type === "picklist" && (
              <input
                value={f.values.join(", ")}
                onChange={(e) => setField(i, { values: e.target.value.split(",").map((v) => v.trim()) })}
                placeholder="choices, comma-separated"
                className="w-44 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-100"
              />
            )}
            <label className="flex items-center gap-1 text-xs text-slate-300">
              <input type="checkbox" checked={f.required} onChange={(e) => setField(i, { required: e.target.checked })} /> required
            </label>
            <button type="button" onClick={() => setDraft({ ...draft, fields: draft.fields.filter((_, j) => j !== i) })} className="text-xs text-slate-500 hover:text-red-400">
              ✕
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setDraft({ ...draft, fields: [...draft.fields, { key: "", label: "New detail", type: "text", required: false, values: [], source: "", placeholder: "", help: "" }] })}
          className="text-xs font-semibold text-emerald-300 hover:text-emerald-200"
        >
          + Add a detail
        </button>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
        <p className="text-xs font-bold uppercase tracking-wide text-slate-500">What I'll create</p>
        <ul className="mt-1.5 space-y-1 text-sm text-slate-300">
          {draft.creates.map((c) => (
            <li key={c}>✓ {c}</li>
          ))}
        </ul>
        {draft.warnings.map((w) => (
          <p key={w} className="mt-2 text-xs text-amber-200">
            ⚠ {w}
          </p>
        ))}
      </div>

      <label className="flex items-start gap-2 text-sm text-slate-200">
        <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-1" />
        <span>I agree - build this action and switch it on. (It can be changed or switched off any time in Admin → Intents.)</span>
      </label>
      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="flex justify-end gap-2">
        {onCancel && (
          <button type="button" onClick={onCancel} disabled={busy} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:border-slate-500">
            Not now
          </button>
        )}
        <button
          type="button"
          onClick={build}
          disabled={!agreed || busy || draft.label.trim().length < 2}
          className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-bold text-white hover:bg-emerald-500 disabled:opacity-40"
        >
          {busy ? "Building…" : "▶ Build it and switch it on"}
        </button>
      </div>
    </section>
  );
}
