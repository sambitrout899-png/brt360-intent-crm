import { useEffect, useRef, useState } from "react";
import {
  checkFormula,
  deleteProductRule,
  formulaVocabulary,
  saveFormula,
  toggleProductRule,
  type FormulaCheck,
  type FormulaVocabulary,
  type ProductRuleOut,
} from "../../api/client";
import { INPUT_CLS } from "../intent/intentUi";

/* Price impact expression builder - a product's price formulas (backend crm_core/price_formula.py).
   Describe the impact in plain English (AI, else the built-in reader) or build the formula from the
   palette; every keystroke is checked against the product, explained in words, and previewed as the
   amount it adds for the values worth seeing. */

const clean = (e: unknown) => String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1").replace(/\\"/g, '"');
const money = (v: number) => `${v < 0 ? "−" : v > 0 ? "+" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const ref = (name: string) => (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : `[${name}]`);
const ENGINE: Record<string, string> = { ai: "written by AI", rules: "read by the built-in reader", formula: "formula" };
const OPERATORS: [string, string][] = [["+", " + "], ["−", " - "], ["×", " * "], ["÷", " / "], ["=", " = "], ["≠", " != "], [">", " > "], ["≥", " >= "], ["<", " < "], ["≤", " <= "], ["and", " and "], ["or", " or "], ["not", "not "], ["( )", "()"]];
const TEMPLATES: { label: string; hint: string; make: (a: string, v: string, n: string) => string }[] = [
  { label: "Add when…", hint: "a flat amount when a value is chosen", make: (a, v) => `if(${a} = "${v}", 500, 0)` },
  { label: "% discount when…", hint: "a percentage of the base price off", make: (a, v) => `if(${a} = "${v}", -10% * base, 0)` },
  { label: "Per unit above an allowance", hint: "charge each unit past a free amount", make: (_a, _v, n) => `max(0, ${n} - 100) * 2` },
  { label: "Tiers", hint: "first matching band wins", make: (_a, _v, n) => `if(${n} <= 1024, 0, if(${n} <= 10240, 800, 2000))` },
  { label: "Cap / floor", hint: "keep an amount within limits", make: (_a, _v, n) => `min(5000, max(500, ${n} * 2))` },
];

type Draft = { id: number | null; described: string; expression: string; label: string; charge: "recurring" | "one_time" };
const EMPTY: Draft = { id: null, described: "", expression: "", label: "", charge: "recurring" };

export default function PriceFormulaBuilder({ product, rules, onChanged }: { product: string; rules: ProductRuleOut[]; onChanged: () => void }) {
  const [vocab, setVocab] = useState<FormulaVocabulary | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [checked, setResult] = useState<FormulaCheck | null>(null);
  const [checkProblem, setProblem] = useState("");
  const [building, setBuilding] = useState(false);
  const [engine, setEngine] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [tryPicks, setTryPicks] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const seq = useRef(0);
  const formulas = rules.filter((r) => r.rule_type === "price");

  useEffect(() => {
    formulaVocabulary(product).then(setVocab).catch((e) => setError(clean(e)));
  }, [product, rules]);

  // every change to the formula is checked (and priced for the "try it" values) as you type
  const expression = draft?.expression ?? "";
  const label = draft?.label ?? "";
  const charge = draft?.charge ?? "recurring";
  const id = draft?.id ?? null;
  const picksKey = JSON.stringify(tryPicks);
  // an empty formula has nothing to check
  const result = expression.trim() ? checked : null;
  const problem = checkProblem;
  useEffect(() => {
    if (!expression.trim()) return;
    const mine = ++seq.current;
    const t = setTimeout(() => {
      checkFormula(product, { text: expression, label: label || null, charge, picks: JSON.parse(picksKey), rule_id: id, use_ai: false })
        .then((r) => mine === seq.current && (setResult(r), setProblem("")))
        .catch((e) => mine === seq.current && (setResult(null), setProblem(clean(e))));
    }, 350);
    return () => clearTimeout(t);
  }, [product, expression, label, charge, id, picksKey]);

  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...(d ?? EMPTY), ...patch }));

  const insert = (text: string) => {
    const el = box.current;
    const cur = draft?.expression ?? "";
    const start = el?.selectionStart ?? cur.length;
    const end = el?.selectionEnd ?? cur.length;
    const next = cur.slice(0, start) + text + cur.slice(end);
    // functions and "( )" leave the cursor inside the brackets
    const caret = start + (text.endsWith("()") ? text.length - 1 : text.length);
    set({ expression: next });
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(caret, caret);
    });
  };

  const build = async () => {
    if (!draft?.described.trim()) return;
    setBuilding(true);
    setProblem("");
    try {
      const r = await checkFormula(product, { text: draft.described, rule_id: draft.id, use_ai: true });
      setEngine(r.engine);
      set({ expression: r.expression, label: draft.label || r.label, charge: r.charge });
    } catch (e) {
      setProblem(clean(e));
    } finally {
      setBuilding(false);
    }
  };

  const save = async () => {
    if (!draft || !result) return;
    try {
      await saveFormula(product, { expression: draft.expression, label: draft.label || result.label, charge: draft.charge, described_as: draft.described, rule_id: draft.id });
      setDraft(null);
      setResult(null);
      setTryPicks({});
      onChanged();
    } catch (e) {
      setProblem(clean(e));
    }
  };

  const edit = (r: ProductRuleOut) => {
    const d = r.definition as { expression?: string; label?: string; charge?: "recurring" | "one_time"; described_as?: string };
    setDraft({ id: r.id, described: d.described_as ?? "", expression: d.expression ?? "", label: d.label ?? "", charge: d.charge ?? "recurring" });
    setEngine("");
    setTryPicks({});
  };

  const attr = vocab?.attributes.find((a) => a.name === picked);
  const firstList = vocab?.attributes.find((a) => a.values.length && a.min === null && a.max === null);
  const firstNumber = vocab?.attributes.find((a) => a.min !== null || a.max !== null || !a.values.length) ?? null;
  const used = result?.uses ?? [];

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-3">
        <div className="flex flex-wrap items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-slate-200">💰 Price formulas</p>
            <p className="text-xs text-slate-400">
              Price impacts worked out of several attributes and child products at once - discounts for combinations, tiers, charges per unit above an allowance. Each formula is a line
              of the quote's price breakdown. Per-value impacts (Fiber: +500) stay on the attribute itself.
            </p>
          </div>
          {!draft && (
            <button type="button" onClick={() => (setDraft({ ...EMPTY }), setEngine(""), setTryPicks({}))} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white">
              + New price formula
            </button>
          )}
        </div>
        {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      </div>

      {formulas.length === 0 && !draft && <p className="rounded-xl border border-dashed border-slate-700 p-4 text-center text-xs text-slate-500">No price formulas yet - the price is the base, child products and per-value impacts.</p>}

      {formulas.map((r) => {
        const d = r.definition as { expression?: string; label?: string; charge?: string; described_as?: string };
        return (
          <div key={r.id} className={`flex items-start gap-3 rounded-xl border border-slate-800 px-3 py-2 ${r.is_active ? "" : "opacity-50"} ${draft?.id === r.id ? "border-emerald-500/60" : ""}`}>
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-slate-100">{d.label || "Price formula"}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${d.charge === "one_time" ? "bg-amber-500/15 text-amber-300" : "bg-emerald-500/15 text-emerald-300"}`}>{d.charge === "one_time" ? "one-time" : "monthly"}</span>
                {!r.is_active && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">off</span>}
              </div>
              <p className="text-xs text-slate-300">{r.explanation.replace(/^[^:]*:\s*/, "")}</p>
              <code className="block break-all font-mono text-[11px] text-slate-500">{d.expression}</code>
              {d.described_as && <p className="text-[10px] text-slate-500">"{d.described_as}"</p>}
            </div>
            <div className="flex shrink-0 items-center gap-2 text-[11px]">
              <button type="button" onClick={() => edit(r)} className="text-sky-400 hover:text-sky-300">Edit</button>
              <button type="button" onClick={() => toggleProductRule(r.id, !r.is_active).then(onChanged).catch((e) => setError(clean(e)))} className="text-slate-400 hover:text-sky-300">
                {r.is_active ? "Switch off" : "Switch on"}
              </button>
              <button type="button" title="Remove" onClick={() => window.confirm(`Remove the price formula "${d.label || d.expression}"?`) && deleteProductRule(r.id).then(onChanged).catch((e) => setError(clean(e)))} className="text-slate-500 hover:text-red-400">
                ×
              </button>
            </div>
          </div>
        );
      })}

      {draft && (
        <div className="space-y-3 rounded-xl border border-emerald-600/50 bg-slate-950 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{draft.id ? "Edit price formula" : "New price formula"}</p>

          {/* 1. say it */}
          <div className="space-y-1.5">
            <label className="text-xs text-slate-300">1 · Describe the price impact</label>
            <div className="flex gap-2">
              <input
                autoFocus
                value={draft.described}
                onChange={(e) => set({ described: e.target.value })}
                onKeyDown={(e) => e.key === "Enter" && build()}
                placeholder="✨ e.g. 10% off when Access Medium is Fiber and Bandwidth is more than 2 Mbps"
                className={`${INPUT_CLS} flex-1`}
              />
              <button type="button" disabled={!draft.described.trim() || building} onClick={build} className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
                {building ? "Building…" : "✨ Build formula"}
              </button>
            </div>
            <div className="flex flex-wrap gap-1">
              {vocab?.examples.map((x) => (
                <button key={x} type="button" onClick={() => set({ described: x })} className="rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400 hover:border-violet-500 hover:text-violet-300">
                  {x}
                </button>
              ))}
            </div>
          </div>

          {/* 2. the formula, with its palette */}
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <label className="text-xs text-slate-300">2 · Formula</label>
              {engine && <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] text-violet-300">{ENGINE[engine] ?? engine} - check it below</span>}
            </div>
            <textarea
              ref={box}
              value={draft.expression}
              onChange={(e) => (set({ expression: e.target.value }), setEngine(""), !e.target.value.trim() && setProblem(""))}
              rows={3}
              spellCheck={false}
              placeholder='if([Access Medium] = "Fiber", 500, 0)'
              className={`${INPUT_CLS} w-full font-mono text-[13px]`}
            />
            <div className="space-y-1.5 rounded-lg border border-slate-800 bg-slate-900/60 p-2">
              <div className="flex flex-wrap items-center gap-1">
                <span className="w-20 shrink-0 text-[10px] uppercase text-slate-500">Attributes</span>
                {vocab?.attributes.map((a) => (
                  <button key={a.name} type="button" onClick={() => (insert(ref(a.name)), setPicked(a.name))} title={a.component ? `in ${a.component}` : undefined}
                    className={`rounded px-1.5 py-0.5 text-[11px] ${picked === a.name ? "bg-emerald-500/30 text-emerald-100" : "bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"}`}>
                    {a.name}
                  </button>
                ))}
                {vocab && vocab.attributes.length === 0 && <span className="text-[11px] text-slate-500">none with 💰 Pricing impact yet</span>}
              </div>
              {attr && (
                <div className="flex flex-wrap items-center gap-1 pl-[5.25rem]">
                  {attr.min !== null || attr.max !== null || !attr.values.length ? (
                    <span className="text-[10px] text-slate-400">
                      {attr.name} is a number{attr.unit ? ` in ${attr.unit}` : ""}{attr.min !== null || attr.max !== null ? ` (${attr.min ?? "…"} – ${attr.max ?? "…"})` : ""} - compare it with numbers, e.g. {ref(attr.name)} &gt; {attr.min ?? 100}
                    </span>
                  ) : (
                    <>
                      <span className="text-[10px] text-slate-500">values:</span>
                      {attr.values.map((v) => (
                        <button key={v} type="button" onClick={() => insert(`"${v}"`)} className="rounded border border-emerald-500/30 px-1.5 py-0.5 text-[11px] text-emerald-200 hover:bg-emerald-500/10">
                          "{v}"
                        </button>
                      ))}
                    </>
                  )}
                </div>
              )}
              {vocab && vocab.components.length > 0 && (
                <div className="flex flex-wrap items-center gap-1">
                  <span className="w-20 shrink-0 text-[10px] uppercase text-slate-500">Child products</span>
                  {vocab.components.map((c) => (
                    <button key={c.name} type="button" title={`how many are chosen (0 – ${c.max_qty})`} onClick={() => insert(ref(c.name))} className="rounded bg-violet-500/10 px-1.5 py-0.5 text-[11px] text-violet-300 hover:bg-violet-500/20">
                      {c.name}
                    </button>
                  ))}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-1">
                <span className="w-20 shrink-0 text-[10px] uppercase text-slate-500">Prices</span>
                {vocab && Object.entries(vocab.variables).map(([k, v]) => (
                  <button key={k} type="button" title={v} onClick={() => insert(k)} className="rounded bg-sky-500/10 px-1.5 py-0.5 text-[11px] text-sky-300 hover:bg-sky-500/20">
                    {k} <span className="text-sky-500/70">· {k === "base" ? `₹${vocab.base.toLocaleString("en-IN")}` : v}</span>
                  </button>
                ))}
                {["10%", "500"].map((x) => (
                  <button key={x} type="button" onClick={() => insert(x)} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-300 hover:bg-slate-700">{x}</button>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <span className="w-20 shrink-0 text-[10px] uppercase text-slate-500">Operators</span>
                {OPERATORS.map(([shown, text]) => (
                  <button key={shown} type="button" onClick={() => insert(text)} className="min-w-[1.75rem] rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-200 hover:bg-slate-700">{shown}</button>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <span className="w-20 shrink-0 text-[10px] uppercase text-slate-500">Functions</span>
                {[["if(condition, then, otherwise)", "if()"], ["min(a, b)", "min()"], ["max(a, b)", "max()"], ["round(x, digits)", "round()"], ["between(x, low, high)", "between()"], ["ceil(x)", "ceil()"], ["floor(x)", "floor()"]].map(([title, text]) => (
                  <button key={text} type="button" title={title} onClick={() => insert(text)} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-amber-200 hover:bg-slate-700">{text.replace("()", "(…)")}</button>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <span className="w-20 shrink-0 text-[10px] uppercase text-slate-500">Start from</span>
                {TEMPLATES.map((t) => (
                  <button key={t.label} type="button" title={t.hint}
                    onClick={() => (set({ expression: t.make(ref(firstList?.name ?? "Attribute"), firstList?.values[0] ?? "Value", ref(firstNumber?.name ?? "Quantity")) }), setEngine(""))}
                    className="rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-300 hover:border-emerald-500 hover:text-emerald-300">
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
            {problem ? (
              <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-xs text-red-300">✗ {problem}</p>
            ) : result && result.engine !== "formula" ? (
              <p className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-200">
                That reads as <code className="font-mono text-amber-100">{result.expression}</code>
                <button type="button" onClick={() => set({ expression: result.expression, charge: result.charge })} className="rounded bg-amber-600 px-2 py-0.5 font-semibold text-white">
                  Use this formula
                </button>
              </p>
            ) : result ? (
              <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2 py-1.5 text-xs text-emerald-200">✓ {result.explanation}</p>
            ) : null}
          </div>

          {/* 3. name and charge */}
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
            <input value={draft.label} onChange={(e) => set({ label: e.target.value })} placeholder={`3 · Line label on the quote${result ? ` - e.g. ${result.label}` : ""}`} className={INPUT_CLS} />
            <div className="flex overflow-hidden rounded-lg border border-slate-700 text-xs">
              {(["recurring", "one_time"] as const).map((c) => (
                <button key={c} type="button" onClick={() => set({ charge: c })} className={`px-3 py-1.5 ${draft.charge === c ? "bg-emerald-600 font-semibold text-white" : "text-slate-300 hover:bg-slate-800"}`}>
                  {c === "recurring" ? "Monthly" : "One-time"}
                </button>
              ))}
            </div>
          </div>

          {/* 4. what it does */}
          {result && (
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="space-y-1">
                <p className="text-xs text-slate-300">4 · Impact preview</p>
                <div className="max-h-64 overflow-auto rounded-lg border border-slate-800">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-slate-900 text-[10px] uppercase text-slate-500">
                      <tr>
                        {used.slice(0, 3).map((u) => <th key={u} className="px-2 py-1 text-left font-medium">{u}</th>)}
                        <th className="px-2 py-1 text-right font-medium">{draft.charge === "one_time" ? "One-time" : "Per month"}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.preview.map((row, i) => (
                        <tr key={i} className="border-t border-slate-800/70">
                          {used.slice(0, 3).map((u) => <td key={u} className="px-2 py-1 text-slate-300">{String(row.picks[u] ?? "—")}</td>)}
                          <td className={`px-2 py-1 text-right font-mono ${row.amount > 0 ? "text-emerald-300" : row.amount < 0 ? "text-rose-300" : "text-slate-500"}`}>{row.amount ? money(row.amount) : "no change"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-[10px] text-slate-500">Other attributes at their defaults; "the price so far" is worked out for each row.</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-slate-300">5 · Try it</p>
                <div className="space-y-1.5 rounded-lg border border-slate-800 p-2">
                  {used.length === 0 && <p className="text-[11px] text-slate-500">This formula uses no attributes - it is always the same amount.</p>}
                  {used.map((u) => {
                    const a = vocab?.attributes.find((x) => x.name === u);
                    const c = vocab?.components.find((x) => x.name === u);
                    const value = tryPicks[u] ?? "";
                    const change = (v: string) => setTryPicks({ ...tryPicks, [u]: v });
                    return (
                      <label key={u} className="flex items-center gap-2 text-xs text-slate-300">
                        <span className="w-32 shrink-0 truncate">{u}</span>
                        {a && a.values.length && a.min === null && a.max === null ? (
                          <select value={value} onChange={(e) => change(e.target.value)} className={INPUT_CLS}>
                            <option value="">default</option>
                            {a.values.map((v) => <option key={v}>{v}</option>)}
                          </select>
                        ) : (
                          <input value={value} onChange={(e) => change(e.target.value)} placeholder={c ? `0 – ${c.max_qty}` : a?.unit ? `e.g. ${a.min ?? 1} ${a.unit}` : "number"} className={INPUT_CLS} />
                        )}
                      </label>
                    );
                  })}
                  {result.amount !== undefined && (
                    <p className="pt-1 text-sm">
                      <span className="text-slate-400">Adds </span>
                      <span className={`font-mono font-semibold ${result.amount > 0 ? "text-emerald-300" : result.amount < 0 ? "text-rose-300" : "text-slate-300"}`}>{result.amount ? money(result.amount) : "nothing"}</span>
                      <span className="text-slate-400"> {draft.charge === "one_time" ? "once" : "a month"}</span>
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="flex gap-2">
            <button type="button" disabled={!result || !!problem || result.engine !== "formula"} onClick={save} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
              {draft.id ? "Save formula" : "Add price formula"}
            </button>
            <button type="button" onClick={() => (setDraft(null), setResult(null), setProblem(""), setTryPicks({}))} className="text-xs text-slate-400">
              Cancel
            </button>
            <span className="ml-auto self-center text-[10px] text-slate-500">Selling picks it up when the model is released (or at once, if it was never released).</span>
          </div>
        </div>
      )}
    </div>
  );
}
