import { useEffect, useState } from "react";
import { applyProductDesign, listProductDesigns, planProductDesign, priceSelection, productDesign } from "../api/client";
import type { DesignedAttribute, DesignPlan, PriceQuote, ProductDesign, ProductDesignSummary } from "../api/types";

const SMALL_BTN = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300";
const money = (n: number | undefined) => (n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 });

function impactText(a: DesignedAttribute, value: string): string {
  const i = a.price_impacts.find((x) => x.value === value);
  if (!i || !i.amount) return "";
  return i.type === "percent" ? `${i.amount > 0 ? "+" : ""}${i.amount}%` : `${i.amount > 0 ? "+" : ""}${money(i.amount)}`;
}

function AttributeRow({ a, onRemove }: { a: DesignedAttribute; onRemove: () => void }) {
  const perUnit = a.price_impacts.find((i) => i.type === "per_unit");
  return (
    <div className="flex flex-wrap items-start gap-3 border-t border-slate-800 px-3 py-2 text-sm first:border-t-0">
      <div className="min-w-[160px]">
        <div className="font-semibold text-slate-200">
          {a.name}
          {a.unit && <span className="ml-1 text-xs font-normal text-slate-500">({a.unit})</span>}
        </div>
        <div className="text-[11px] text-slate-500">
          {a.required ? "required" : "optional"}
          {a.default ? ` · default ${a.default}` : ""}
          {a.visible_when ? ` · when ${a.visible_when.attribute} is ${a.visible_when.equals}` : ""}
        </div>
      </div>
      <div className="flex flex-1 flex-wrap gap-1">
        {a.values.map((v) => (
          <span key={v} className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
            {v}
            {impactText(a, v) && <span className="ml-1 text-emerald-400">{impactText(a, v)}</span>}
          </span>
        ))}
        {a.values.length === 0 && <span className="text-xs text-slate-500">{a.data_type === "number" ? "a number" : "free text"}</span>}
        {perUnit && <span className="rounded-md bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-300">{money(perUnit.amount)} each</span>}
      </div>
      <button onClick={onRemove} className={`${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>Remove</button>
    </div>
  );
}

function Simulator({ design }: { design: ProductDesign }) {
  const pricing = design.attributes.filter((a) => a.kind === "pricing");
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [result, setResult] = useState<PriceQuote | null>(null);

  useEffect(() => {
    if (!design.offering_name) return;
    const handle = setTimeout(() => {
      priceSelection(design.offering_name as string, { pricing_attributes: chosen }).then(setResult).catch(() => setResult(null));
    }, 200);
    return () => clearTimeout(handle);
  }, [chosen, design]);

  if (pricing.length === 0) return null;
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
      <h4 className="mb-3 text-sm font-bold text-slate-100">Try a configuration</h4>
      <div className="grid gap-2 sm:grid-cols-2">
        {pricing.map((a) => (
          <label key={a.name} className="text-xs text-slate-400">
            {a.name}
            {a.values.length > 0 ? (
              <select value={chosen[a.name] ?? ""} onChange={(e) => setChosen({ ...chosen, [a.name]: e.target.value })}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200">
                <option value="">{a.default ? `default (${a.default})` : "—"}</option>
                {a.values.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            ) : (
              <input value={chosen[a.name] ?? ""} onChange={(e) => setChosen({ ...chosen, [a.name]: e.target.value })}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200" />
            )}
          </label>
        ))}
      </div>
      {result && (
        <div className="mt-3 text-sm">
          {result.ok ? (
            <>
              <p className="text-slate-300">Base {money(result.base_price)} <span className="text-xs text-slate-500">({result.price_source})</span></p>
              {(result.lines ?? []).map((l) => (
                <p key={l.label} className="text-xs text-slate-400">{l.label}: <span className={l.amount >= 0 ? "text-emerald-400" : "text-amber-400"}>{l.amount >= 0 ? "+" : ""}{money(l.amount)}</span></p>
              ))}
              <p className="mt-1 font-bold text-slate-100">Monthly price {money(result.configured_price)}</p>
            </>
          ) : (
            result.errors.map((e) => <p key={e} className="text-xs text-red-400">{e}</p>)
          )}
        </div>
      )}
    </div>
  );
}

/** Admin -> Product Configuration: each product's pricing and technical
 * attributes, how values change the price, and the rules between them -
 * described in plain English (the AI, or the rule parser, proposes the
 * changes; nothing is saved until you apply them). */
export default function AdminProductConfigSection() {
  const [products, setProducts] = useState<ProductDesignSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [design, setDesign] = useState<ProductDesign | null>(null);
  const [instruction, setInstruction] = useState("");
  const [plan, setPlan] = useState<DesignPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function loadList() {
    listProductDesigns().then((list) => {
      setProducts(list);
      setSelected((current) => current ?? list[0]?.offering_name ?? null);
    }).catch(() => setProducts([]));
  }

  useEffect(loadList, []);
  useEffect(() => {
    if (!selected) return;
    productDesign(selected).then(setDesign).catch(() => setDesign(null));
  }, [selected]);

  function choose(name: string) {
    setSelected(name);
    setPlan(null);
    setMessage(null);
  }

  async function propose() {
    if (!selected || !instruction.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setPlan(await planProductDesign(selected, instruction));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read that.");
    } finally {
      setBusy(false);
    }
  }

  async function apply(ops: DesignPlan["ops"]) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const res = await applyProductDesign(selected, ops);
      setDesign({ ...res.design, list_price: design?.list_price });
      setMessage(res.changes.join(" · "));
      setPlan(null);
      setInstruction("");
      loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not apply that.");
    } finally {
      setBusy(false);
    }
  }

  const pricing = design?.attributes.filter((a) => a.kind === "pricing") ?? [];
  const technical = design?.attributes.filter((a) => a.kind === "technical") ?? [];

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Design what each product is configured with. <b className="text-slate-300">Pricing attributes</b> are chosen while selling and change
        the price - a flat add-on, a percentage, or an amount per unit - on the opportunity and the quote. <b className="text-slate-300">Technical
        attributes</b> are what network provisioning needs; they travel to the order, its work orders, the network and inventory, and an order
        can't go to fulfilment until the required ones are filled.
      </p>

      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <div className="space-y-1">
          {products.map((p) => (
            <button key={p.offering_name} onClick={() => choose(p.offering_name)}
              className={`block w-full rounded-lg border px-3 py-2 text-left text-sm ${selected === p.offering_name ? "border-sky-500 bg-sky-500/10" : "border-slate-800 bg-slate-900/60 hover:border-slate-600"}`}>
              <span className="block font-semibold text-slate-200">{p.offering_name}</span>
              <span className="text-[11px] text-slate-500">
                {p.pricing + p.technical === 0 ? "not designed yet" : `${p.pricing} pricing · ${p.technical} technical · ${p.rules} rules`}
              </span>
            </button>
          ))}
          {products.length === 0 && <p className="text-sm text-slate-500">No active products yet.</p>}
        </div>

        {design && selected && (
          <div className="space-y-4">
            <div className="rounded-xl border border-sky-500/30 bg-slate-900/80 p-4">
              <label className="text-xs font-semibold text-slate-300">
                Describe changes to {selected} in your own words
                <textarea rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)}
                  placeholder="e.g. Bandwidth: 100 Mbps +0, 500 Mbps +800, 1 Gbps +1500; Contract term: 12 months, 36 months -10%; Static IPs: 50 each; technical: VLAN ID required number; CPE type: Standard router, Business router; if Bandwidth is 1 Gbps then SLA is required; remove Static IPs"
                  className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" />
              </label>
              <div className="mt-2 flex gap-2">
                <button disabled={busy || !instruction.trim()} onClick={propose} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
                  {busy ? "Reading…" : "Preview changes"}
                </button>
              </div>
              {plan && (
                <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm">
                  <p className="mb-1 text-xs text-slate-500">{plan.engine === "ai" ? "Proposed by the AI" : "Read with the built-in rules"}{plan.reply ? ` - ${plan.reply}` : ""}</p>
                  {plan.preview.length === 0 && <p className="text-amber-300">Nothing recognised - try the example's style.</p>}
                  <ul className="list-disc space-y-0.5 pl-5 text-slate-300">{plan.preview.map((line, i) => <li key={i}>{line}</li>)}</ul>
                  {plan.ops.length > 0 && (
                    <button disabled={busy} onClick={() => apply(plan.ops)} className="mt-2 rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-bold text-white hover:bg-emerald-500">
                      Apply
                    </button>
                  )}
                </div>
              )}
              {message && <p className="mt-2 text-xs text-emerald-400">✓ {message}</p>}
              {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
            </div>

            {[["Pricing attributes", pricing], ["Technical attributes", technical]].map(([title, list]) => (
              <div key={title as string}>
                <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">{title as string}</h4>
                <div className="rounded-xl border border-slate-700 bg-slate-900/60">
                  {(list as DesignedAttribute[]).map((a) => (
                    <AttributeRow key={a.id} a={a} onRemove={() => apply([{ op: "remove_attribute", name: a.name }])} />
                  ))}
                  {(list as DesignedAttribute[]).length === 0 && <p className="px-3 py-2 text-xs text-slate-500">None yet.</p>}
                </div>
              </div>
            ))}

            {design.rules.length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Rules</h4>
                <div className="space-y-1">
                  {design.rules.map((r) => (
                    <div key={r.id} className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-sm text-slate-300">
                      <span>
                        If <b>{r.if_attribute}</b> is <b>{r.if_value}</b>, <b>{r.then_attribute}</b>{" "}
                        {r.effect === "required" ? "is required" : r.effect === "requires" ? `must be ${r.then_value}` : `must not be ${r.then_value}`}
                      </span>
                      <button onClick={() => apply([{ op: "remove_rule", id: r.id }])} className={`ml-auto ${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>Remove</button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <Simulator design={design} />
          </div>
        )}
      </div>
    </div>
  );
}
