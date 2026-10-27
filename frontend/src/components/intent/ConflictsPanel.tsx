import { useState } from "react";
import { conflictClear, conflictResolve } from "../../api/client";
import type { Conflict, ConflictCondition } from "../../api/conflictTypes";

const OPERATORS: [string, string][] = [
  ["eq", "is"],
  ["ne", "is not"],
  ["contains", "contains"],
  ["in", "is one of (a, b)"],
  ["gt", ">"],
  ["gte", "≥"],
  ["lt", "<"],
  ["lte", "≤"],
  ["empty", "is empty"],
  ["not_empty", "is filled"],
];

const TONE: Record<Conflict["severity"], string> = {
  high: "border-red-500/40 bg-red-500/[0.07]",
  medium: "border-amber-500/40 bg-amber-500/[0.07]",
  low: "border-sky-500/30 bg-sky-500/[0.05]",
};
const BADGE: Record<Conflict["severity"], string> = {
  high: "bg-red-500/20 text-red-200",
  medium: "bg-amber-500/20 text-amber-200",
  low: "bg-sky-500/20 text-sky-200",
};

/** Conflicts found by a rehearsal, a plan's pre-run scan or Admin → Conflicts:
 * automations on one trigger that pull against each other, actions sharing a
 * phrase, steps of a plan that clash. An admin settles each with a priority
 * and conditions (or the action that keeps a phrase); a plan's own clashes
 * are settled by dropping a step. */
export default function ConflictsPanel({
  conflicts,
  canSettle = false,
  onChanged,
  onDropStep,
  title = "Conflicts on this run",
}: {
  conflicts: Conflict[];
  canSettle?: boolean;
  /** after a rule was saved or cleared - the fresh list (Admin → Conflicts) or just "re-check" */
  onChanged?: (fresh?: Conflict[]) => void;
  onDropStep?: (n: number) => void;
  title?: string | null;
}) {
  const [open, setOpen] = useState<Conflict | null>(null);
  const [error, setError] = useState("");
  if (conflicts.length === 0) return null;
  const waiting = conflicts.filter((c) => !c.resolved).length;

  async function clear(c: Conflict) {
    setError("");
    try {
      const out = await conflictClear(c.key);
      onChanged?.(out.conflicts);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not clear the rule");
    }
  }

  return (
    <section className="space-y-2">
      {title && (
        <p className="text-xs font-bold uppercase tracking-wide text-slate-500">
          ⚖️ {title} · {waiting ? `${waiting} to settle` : "all settled"}
        </p>
      )}
      {conflicts.map((c) => (
        <article key={c.key} className={`rounded-xl border p-3 ${c.resolved ? "border-emerald-500/30 bg-emerald-500/[0.05]" : TONE[c.severity]}`}>
          <div className="flex flex-wrap items-start gap-2">
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${c.resolved ? "bg-emerald-500/20 text-emerald-200" : BADGE[c.severity]}`}>
              {c.resolved ? "settled" : c.severity}
            </span>
            <p className="min-w-0 flex-1 text-sm font-semibold text-slate-100">{c.title}</p>
          </div>
          <ul className="mt-1.5 space-y-0.5 text-xs text-slate-300">
            {c.reasons.map((r) => (
              <li key={r}>• {r}</li>
            ))}
          </ul>
          {c.resolved && c.rule && (
            <p className="mt-1.5 text-xs text-emerald-200/90">
              Rule: {c.rule.mode === "first" ? "only the first that applies runs" : "all run, in order"} -{" "}
              {c.rule.order.map((id) => c.members?.find((m) => m.id === id)?.name ?? `#${id}`).join(" → ")}
              {c.rule.by ? ` (set by ${c.rule.by})` : ""}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1 text-[11px] text-slate-400">{c.ask}</span>
            {c.type === "steps" && onDropStep && c.drop && (
              <button type="button" onClick={() => onDropStep(c.drop!)} className="rounded-md border border-slate-600 px-2.5 py-1 text-xs font-semibold text-slate-200 hover:border-red-400">
                Drop step {c.drop}
              </button>
            )}
            {c.type !== "steps" && canSettle && (
              <>
                {c.resolved && c.rule && (
                  <button type="button" onClick={() => clear(c)} className="rounded-md px-2 py-1 text-xs text-slate-400 hover:text-red-300">
                    Clear rule
                  </button>
                )}
                <button type="button" onClick={() => setOpen(c)} className="rounded-md bg-violet-600 px-3 py-1 text-xs font-bold text-white hover:bg-violet-500">
                  {c.resolved ? "Change" : c.type === "actions" ? "Pick the one that keeps it" : "Set priority & conditions"}
                </button>
              </>
            )}
            {c.type !== "steps" && !canSettle && !c.resolved && <span className="text-[11px] italic text-amber-200/80">An administrator settles this.</span>}
          </div>
        </article>
      ))}
      {error && <p className="text-xs text-red-400">{error}</p>}
      {open && (
        <SettleModal
          conflict={open}
          onClose={() => setOpen(null)}
          onSaved={(fresh) => {
            setOpen(null);
            onChanged?.(fresh);
          }}
        />
      )}
    </section>
  );
}

function SettleModal({ conflict, onClose, onSaved }: { conflict: Conflict; onClose: () => void; onSaved: (fresh: Conflict[]) => void }) {
  const members = conflict.members ?? [];
  const [order, setOrder] = useState<number[]>(() => conflict.rule?.order?.filter((id) => members.some((m) => m.id === id)) ?? members.map((m) => m.id));
  const [mode, setMode] = useState<"all" | "first">(conflict.rule?.mode ?? "all");
  const [conditions, setConditions] = useState<Record<string, ConflictCondition>>(() => {
    const out: Record<string, ConflictCondition> = {};
    for (const m of members) {
      const c = conflict.rule?.conditions?.[String(m.id)] ?? m.condition;
      if (c) out[String(m.id)] = { ...c, field: c.field.replace(/^record\./, "") };
    }
    return out;
  });
  const [winner, setWinner] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // members added since the rule was saved go last
  const fullOrder = [...order, ...members.map((m) => m.id).filter((id) => !order.includes(id))];

  function move(id: number, by: number) {
    const list = [...fullOrder];
    const i = list.indexOf(id);
    const j = i + by;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    setOrder(list);
  }

  function setCondition(id: number, patch: Partial<ConflictCondition>) {
    setConditions((c) => {
      const current: ConflictCondition = c[String(id)] ?? { field: "", operator: "eq", value: "" };
      return { ...c, [String(id)]: { ...current, ...patch } };
    });
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      const out =
        conflict.type === "actions"
          ? await conflictResolve({ key: conflict.key, winner: winner ?? undefined })
          : await conflictResolve({
              key: conflict.key,
              order: fullOrder,
              mode,
              conditions: Object.fromEntries(Object.entries(conditions).map(([id, c]) => [id, c.field.trim() ? c : null])),
            });
      onSaved(out.conflicts);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => !busy && onClose()}>
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-slate-100">⚖️ {conflict.title}</h3>
        {conflict.trigger && <p className="text-sm text-slate-400">{conflict.trigger}</p>}
        <ul className="mt-2 space-y-0.5 text-xs text-slate-300">
          {conflict.reasons.map((r) => (
            <li key={r}>• {r}</li>
          ))}
        </ul>

        {conflict.type === "actions" ? (
          <div className="mt-4 space-y-2">
            <p className="text-sm font-semibold text-slate-200">Which action keeps the phrase?</p>
            {members.map((m) => (
              <label key={m.id} className="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:border-violet-500/60">
                <input type="radio" name="winner" checked={winner === m.id} onChange={() => setWinner(m.id)} />
                {m.name}
              </label>
            ))}
            <p className="text-xs text-slate-500">The others stop answering to it - they keep their other phrases, and nothing else changes.</p>
          </div>
        ) : (
          <div className="mt-4 space-y-4">
            <div>
              <p className="text-sm font-semibold text-slate-200">Priority - who runs first</p>
              <ol className="mt-2 space-y-2">
                {fullOrder.map((id, i) => {
                  const m = members.find((x) => x.id === id);
                  if (!m) return null;
                  const c = conditions[String(id)];
                  return (
                    <li key={id} className="rounded-xl border border-slate-700 bg-slate-950/50 p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-violet-600/30 text-xs font-bold text-violet-200">{i + 1}</span>
                        <span className="min-w-0 flex-1 text-sm font-semibold text-slate-100">{m.name}</span>
                        <button type="button" onClick={() => move(id, -1)} disabled={i === 0} className="rounded px-2 text-slate-400 hover:text-white disabled:opacity-30" title="Earlier">
                          ▲
                        </button>
                        <button type="button" onClick={() => move(id, 1)} disabled={i === fullOrder.length - 1} className="rounded px-2 text-slate-400 hover:text-white disabled:opacity-30" title="Later">
                          ▼
                        </button>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                        <span>Runs only when</span>
                        <input
                          value={c?.field ?? ""}
                          onChange={(e) => setCondition(id, { field: e.target.value })}
                          placeholder="a field, e.g. segment (blank = always)"
                          className="w-52 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-100"
                        />
                        <select
                          value={c?.operator ?? "eq"}
                          onChange={(e) => setCondition(id, { operator: e.target.value })}
                          className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-100"
                        >
                          {OPERATORS.map(([v, l]) => (
                            <option key={v} value={v}>
                              {l}
                            </option>
                          ))}
                        </select>
                        {!["empty", "not_empty"].includes(c?.operator ?? "eq") && (
                          <input
                            value={String(c?.value ?? "")}
                            onChange={(e) => setCondition(id, { value: e.target.value })}
                            placeholder="value"
                            className="w-36 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-100"
                          />
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>
            </div>
            <div className="space-y-1.5">
              <p className="text-sm font-semibold text-slate-200">When several apply</p>
              <label className="flex items-start gap-2 text-sm text-slate-300">
                <input type="radio" name="mode" checked={mode === "all"} onChange={() => setMode("all")} className="mt-1" />
                <span>
                  <b>All of them run</b>, in this order - where they set the same thing, the last one wins.
                </span>
              </label>
              <label className="flex items-start gap-2 text-sm text-slate-300">
                <input type="radio" name="mode" checked={mode === "first"} onChange={() => setMode("first")} className="mt-1" />
                <span>
                  <b>Only the first that applies</b> - the others stand aside (an automation applies when its condition holds, or always without one).
                </span>
              </label>
            </div>
          </div>
        )}

        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:border-slate-500">
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={busy || (conflict.type === "actions" && winner === null)}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-bold text-white hover:bg-violet-500 disabled:opacity-40"
          >
            {busy ? "Saving…" : "Save the rule"}
          </button>
        </div>
      </div>
    </div>
  );
}
