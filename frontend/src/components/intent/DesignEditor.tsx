import { useEffect, useRef, useState } from "react";
import { draftDesign, productDesign, suggestDesign } from "../../api/client";
import type { DesignedAttribute, DesignOp, PreparedField, ProductDesign } from "../../api/types";

interface Impact {
  value: string;
  type: "flat" | "percent" | "per_unit";
  amount: number;
}

// What one attribute card edits (an "upsert_attribute" change).
interface AttributeOp extends DesignOp {
  op: "upsert_attribute";
  name: string;
  kind?: "pricing" | "technical";
  data_type?: string;
  unit?: string | null;
  values?: string[];
  required?: boolean;
  default?: string | null;
  price_impacts?: Impact[];
}

const keyOf = (op: DesignOp) => (op.op === "add_rule" ? `rule:${JSON.stringify(op)}` : `attr:${String(op.name ?? "").trim().toLowerCase()}`);

/** Later changes to the same attribute replace earlier ones; rules add up. */
function merge(current: DesignOp[], incoming: DesignOp[]): DesignOp[] {
  const out = [...current];
  for (const op of incoming) {
    const i = out.findIndex((o) => keyOf(o) === keyOf(op));
    if (i >= 0) out[i] = op;
    else out.push(op);
  }
  return out;
}

function fromDesigned(a: DesignedAttribute): AttributeOp {
  return {
    op: "upsert_attribute", name: a.name, kind: a.kind, data_type: a.data_type, unit: a.unit, values: [...a.values],
    required: a.required, default: a.default, price_impacts: a.price_impacts.map((i) => ({ ...i })),
  };
}

function AttributeCard({ op, onChange, onRemove }: { op: AttributeOp; onChange: (op: AttributeOp) => void; onRemove: () => void }) {
  const kind = op.kind ?? "pricing";
  const values = op.values ?? [];
  const impacts = op.price_impacts ?? [];
  const perUnit = impacts.find((i) => i.type === "per_unit");
  const isNumber = values.length === 0;
  const impactOf = (v: string) => impacts.find((i) => i.value === v && i.type !== "per_unit");

  const setImpact = (value: string, patch: Partial<Impact> | null) => {
    const rest = impacts.filter((i) => i.value !== value);
    const old = impacts.find((i) => i.value === value) ?? { value, type: "flat" as const, amount: 0 };
    onChange({ ...op, price_impacts: patch === null ? rest : [...rest, { ...old, ...patch }] });
  };
  const renameValue = (i: number, next: string) => {
    const old = values[i];
    onChange({
      ...op,
      values: values.map((v, j) => (j === i ? next : v)),
      price_impacts: impacts.map((x) => (x.value === old ? { ...x, value: next } : x)),
      default: op.default === old ? next : op.default,
    });
  };

  return (
    <div className={`rounded-xl border p-3 ${kind === "pricing" ? "border-sky-500/30 bg-sky-500/5" : "border-violet-500/30 bg-violet-500/5"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <input value={op.name} onChange={(e) => onChange({ ...op, name: e.target.value })} className="min-w-[140px] flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-sm font-semibold text-slate-100" />
        <div className="inline-flex rounded-md border border-slate-700 p-0.5 text-[11px]">
          {(["pricing", "technical"] as const).map((k) => (
            <button key={k} type="button" onClick={() => onChange({ ...op, kind: k, price_impacts: k === "technical" ? [] : impacts })}
              className={`rounded px-2 py-0.5 font-semibold ${kind === k ? (k === "pricing" ? "bg-sky-600 text-white" : "bg-violet-600 text-white") : "text-slate-400"}`}>
              {k === "pricing" ? "💰 Pricing" : "🔧 Technical"}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-[11px] text-slate-400">
          <input type="checkbox" checked={!!op.required} onChange={(e) => onChange({ ...op, required: e.target.checked })} />
          required
        </label>
        <button type="button" onClick={onRemove} title="Leave this out" className="text-slate-500 hover:text-red-400">✕</button>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {values.map((v, i) => {
          const impact = impactOf(v);
          return (
            <span key={i} className="flex items-center gap-1 rounded-md border border-slate-700 bg-slate-950 px-1.5 py-0.5">
              <input value={v} onChange={(e) => renameValue(i, e.target.value)} className="w-24 bg-transparent text-xs text-slate-200 outline-none" />
              {kind === "pricing" && (
                <>
                  <input
                    type="number"
                    value={impact?.amount ?? ""}
                    placeholder="±0"
                    onChange={(e) => setImpact(v, e.target.value === "" ? null : { amount: Number(e.target.value) })}
                    className="w-16 bg-transparent text-right text-xs text-emerald-300 outline-none placeholder:text-slate-600"
                  />
                  <button type="button" title="Amount or percentage" onClick={() => impact && setImpact(v, { type: impact.type === "percent" ? "flat" : "percent" })}
                    className="text-[10px] font-bold text-slate-400 hover:text-slate-200">
                    {impact?.type === "percent" ? "%" : "₹"}
                  </button>
                </>
              )}
              <label title="Default" className="text-[10px] text-slate-500">
                <input type="radio" checked={op.default === v} onChange={() => onChange({ ...op, default: v })} className="mr-0.5 align-middle" />
              </label>
              <button type="button" onClick={() => onChange({ ...op, values: values.filter((_, j) => j !== i), price_impacts: impacts.filter((x) => x.value !== v) })}
                className="text-[10px] text-slate-500 hover:text-red-400">✕</button>
            </span>
          );
        })}
        <button type="button" onClick={() => onChange({ ...op, values: [...values, ""], data_type: "enum" })}
          className="rounded-md border border-dashed border-slate-600 px-2 py-0.5 text-[11px] text-slate-400 hover:border-sky-500 hover:text-sky-300">
          + value
        </button>
        {isNumber && kind === "pricing" && (
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            a number, priced at
            <input type="number" value={perUnit?.amount ?? ""} placeholder="0"
              onChange={(e) => onChange({ ...op, data_type: "number", price_impacts: e.target.value === "" ? [] : [{ value: "*", type: "per_unit", amount: Number(e.target.value) }] })}
              className="w-20 rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-xs text-emerald-300" />
            each
          </label>
        )}
        {isNumber && kind === "technical" && <span className="text-[11px] text-slate-500">free value (e.g. a VLAN number)</span>}
      </div>
    </div>
  );
}

/** "Design product attributes": the AI proposes a design, plain English
 * adjusts it, the cards make it exact - Run saves the draft as shown. */
export default function DesignEditor({
  field,
  values,
  onChange,
}: {
  field: PreparedField;
  values: Record<string, unknown>;
  onChange: (ops: DesignOp[]) => void;
}) {
  const offering = typeof values.offering_name === "string" ? values.offering_name : "";
  const draft: DesignOp[] = Array.isArray(field.value) ? (field.value as DesignOp[]) : [];
  const [current, setCurrent] = useState<ProductDesign | null>(null);
  const [ask, setAsk] = useState("");
  const [busy, setBusy] = useState<"suggest" | "ask" | null>(null);
  const [note, setNote] = useState("");
  const seeded = useRef<string | null>(null);

  useEffect(() => {
    if (!offering) return;
    productDesign(offering).then(setCurrent).catch(() => setCurrent(null));
  }, [offering]);

  async function bring(kind: "suggest" | "ask", text = "") {
    if (!offering) return;
    setBusy(kind);
    setNote("");
    try {
      const plan = kind === "suggest" ? await suggestDesign(offering) : await draftDesign(offering, text, draft);
      if (plan.ops.length === 0) {
        setNote(kind === "suggest" ? "Nothing new to suggest - it already has these." : "I couldn't turn that into a change - try rephrasing.");
      } else {
        onChange(merge(draft, plan.ops));
        setNote(plan.reply || (plan.engine === "ai" ? "Here's what the AI proposes - adjust anything below." : "Here's a starting point - adjust anything below."));
        if (kind === "ask") setAsk("");
      }
    } catch (e) {
      setNote(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(null);
    }
  }

  // A sentence typed in the command bar ("Design Fiber 800: ...") becomes a draft straight away.
  useEffect(() => {
    if (typeof field.value === "string" && field.value.trim() && offering && seeded.current !== field.value) {
      seeded.current = field.value;
      bring("ask", field.value);
    }
    // bring is stable enough for this one-shot seed
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [field.value, offering]);

  if (!offering) return <p className="rounded-lg border border-dashed border-slate-700 px-3 py-2 text-xs text-slate-500">Pick the product first.</p>;

  const replace = (i: number, op: DesignOp | null) => onChange(op === null ? draft.filter((_, j) => j !== i) : draft.map((o, j) => (j === i ? op : o)));
  const existing = current?.attributes ?? [];
  const removing = new Set(draft.filter((o) => o.op === "remove_attribute").map((o) => String(o.name).toLowerCase()));
  const editing = new Set(draft.filter((o) => o.op === "upsert_attribute").map((o) => String(o.name).toLowerCase()));

  return (
    <div className="space-y-3">
      {existing.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-slate-500">Today:</span>
          {existing.map((a) => {
            const key = a.name.toLowerCase();
            return (
              <span key={a.id} className={`flex items-center gap-1 rounded-md px-2 py-0.5 ${removing.has(key) ? "bg-red-500/10 text-red-300 line-through" : a.kind === "pricing" ? "bg-sky-500/10 text-sky-300" : "bg-violet-500/10 text-violet-300"}`}>
                {a.name}
                {!removing.has(key) && !editing.has(key) && (
                  <>
                    <button type="button" title="Change it" onClick={() => onChange(merge(draft, [fromDesigned(a)]))} className="text-slate-400 hover:text-slate-100">✎</button>
                    <button type="button" title="Remove it" onClick={() => onChange(merge(draft, [{ op: "remove_attribute", name: a.name }]))} className="text-slate-400 hover:text-red-400">✕</button>
                  </>
                )}
              </span>
            );
          })}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy !== null} onClick={() => bring("suggest")}
          className="rounded-lg bg-gradient-to-r from-violet-600 to-sky-600 px-3 py-2 text-sm font-bold text-white shadow hover:opacity-90 disabled:opacity-50">
          {busy === "suggest" ? "Thinking up attributes…" : `✨ Suggest attributes for ${offering}`}
        </button>
        <div className="flex min-w-[260px] flex-1 items-center gap-2 rounded-lg border border-slate-700 bg-slate-950 px-2">
          <input value={ask} onChange={(e) => setAsk(e.target.value)} disabled={busy !== null}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (ask.trim()) bring("ask", ask);
              }
            }}
            placeholder="Or tell the AI - e.g. “make 1 Gbps 1500 more, add a 60-month term at -15%, VLAN is required”"
            className="w-full bg-transparent py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500" />
          <button type="button" disabled={busy !== null || !ask.trim()} onClick={() => bring("ask", ask)} className="text-xs font-bold text-sky-400 disabled:opacity-40">
            {busy === "ask" ? "…" : "Ask"}
          </button>
        </div>
      </div>
      {note && <p className="text-xs text-violet-300">✨ {note}</p>}

      {draft.length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] text-slate-500">Will be saved when you run it - 💰 pricing attributes change the price, 🔧 technical ones go to the order and provisioning. ◉ marks the default value.</p>
          {draft.map((op, i) =>
            op.op === "upsert_attribute" ? (
              <AttributeCard key={i} op={op as AttributeOp} onChange={(next) => replace(i, next)} onRemove={() => replace(i, null)} />
            ) : (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-1.5 text-xs text-slate-300">
                {op.op === "remove_attribute" ? (
                  <span className="text-red-300">Remove {String(op.name)}</span>
                ) : op.op === "add_rule" ? (
                  <span>
                    Rule: if {String(op.if_attribute)} is {String(op.if_value ?? "")}, {String(op.then_attribute)}{" "}
                    {op.effect === "required" ? "is required" : op.effect === "requires" ? `must be ${String(op.then_value)}` : `must not be ${String(op.then_value)}`}
                  </span>
                ) : (
                  <span>{op.op}</span>
                )}
                <button type="button" onClick={() => replace(i, null)} className="ml-auto text-slate-500 hover:text-red-400">undo</button>
              </div>
            )
          )}
          <button type="button" onClick={() => onChange([...draft, { op: "upsert_attribute", name: "New attribute", kind: "pricing", values: [], price_impacts: [] }])}
            className="rounded-md border border-dashed border-slate-600 px-3 py-1 text-xs text-slate-400 hover:border-sky-500 hover:text-sky-300">
            + add an attribute by hand
          </button>
        </div>
      )}
      {draft.length === 0 && existing.length === 0 && !busy && (
        <p className="text-xs text-slate-500">{offering} has no attributes yet - start with ✨ Suggest, or tell the AI what it should have.</p>
      )}
    </div>
  );
}
