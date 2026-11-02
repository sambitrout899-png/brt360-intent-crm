import { useState } from "react";
import { platformLicApi } from "../../api/client";
import type { LicCatalog, LicEdition, LicProduct } from "./types";
import { BTN, BTN_PRIMARY, Card, Field, INPUT, KIND_LABEL, money, num, UNIT_LABEL } from "./ui";

type Props = { catalog: LicCatalog; onChanged: () => void; setError: (e: string | null) => void; setDone: (d: string | null) => void };

const blankProduct = (currencies: string[]): ProductDraft => ({
  code: "", name: "", kind: "service", unit: "user", unit_label: "user", description: "", features: "", modules: [],
  prices: Object.fromEntries(currencies.map((c) => [c, ""])), annual_discount_pct: "10", min_quantity: "1", max_quantity: "",
  requires: [], limits: "", trial_days: "0", is_active: true,
});

type ProductDraft = {
  code: string; name: string; kind: string; unit: string; unit_label: string; description: string; features: string; modules: string[];
  prices: Record<string, string>; annual_discount_pct: string; min_quantity: string; max_quantity: string; requires: string[]; limits: string;
  trial_days: string; is_active: boolean;
};

function toDraft(p: LicProduct, currencies: string[]): ProductDraft {
  return {
    code: p.code, name: p.name, kind: p.kind, unit: p.unit, unit_label: p.unit_label, description: p.description, features: p.features.join("\n"),
    modules: p.modules, prices: Object.fromEntries(currencies.map((c) => [c, p.prices[c] !== undefined ? String(p.prices[c]) : ""])),
    annual_discount_pct: String(p.annual_discount_pct), min_quantity: String(p.min_quantity), max_quantity: p.max_quantity === null ? "" : String(p.max_quantity),
    requires: p.requires, limits: Object.entries(p.limits).map(([k, v]) => `${k}=${v}`).join(", "), trial_days: String(p.trial_days), is_active: p.is_active,
  };
}

function parseLimits(text: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const part of text.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean)) {
    const m = /^([a-z][a-z0-9_]*)\s*[=:]\s*([\d.]+)$/.exec(part);
    if (!m) throw new Error(`Limit "${part}" - write it like ai_tokens_month=1000000`);
    out[m[1]] = Number(m[2]);
  }
  return out;
}

export default function CatalogEditor({ catalog, onChanged, setError, setDone }: Props) {
  const { products, editions, settings, modules } = catalog;
  const currencies = settings.currencies;
  const [editing, setEditing] = useState<{ original: string | null; d: ProductDraft } | null>(null);
  const [edition, setEdition] = useState<{ original: string | null; e: { code: string; name: string; description: string; discount_pct: string; trial_days: string; highlight: boolean; is_active: boolean; items: Record<string, string> } } | null>(null);
  const [busy, setBusy] = useState(false);
  const names = Object.fromEntries(products.map((p) => [p.code, p.name]));

  const saveProduct = async () => {
    if (!editing) return;
    const d = editing.d;
    setBusy(true);
    setError(null);
    try {
      const body = {
        ...(editing.original ? {} : { code: d.code.trim() }), name: d.name.trim(), kind: d.kind, unit: d.unit, unit_label: d.unit_label.trim() || d.unit,
        description: d.description, features: d.features.split("\n").map((f) => f.trim()).filter(Boolean), modules: d.modules,
        prices: Object.fromEntries(Object.entries(d.prices).filter(([, v]) => v !== "").map(([c, v]) => [c, Number(v)])),
        annual_discount_pct: Number(d.annual_discount_pct || 0), min_quantity: Number(d.min_quantity || 0),
        max_quantity: d.max_quantity === "" ? null : Number(d.max_quantity), requires: d.requires, limits: parseLimits(d.limits),
        trial_days: Number(d.trial_days || 0), is_active: d.is_active,
      };
      await platformLicApi(editing.original ? `/products/${editing.original}` : "/products", editing.original ? "PUT" : "POST", body);
      setDone(`Saved ${body.name}`);
      setEditing(null);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveEdition = async () => {
    if (!edition) return;
    const e = edition.e;
    setBusy(true);
    setError(null);
    try {
      const body = {
        ...(edition.original ? {} : { code: e.code.trim() }), name: e.name.trim(), description: e.description, discount_pct: Number(e.discount_pct || 0),
        trial_days: Number(e.trial_days || 0), highlight: e.highlight, is_active: e.is_active,
        items: Object.entries(e.items).filter(([, q]) => Number(q) > 0).map(([product, q]) => ({ product, quantity: Number(q) })),
      };
      await platformLicApi(edition.original ? `/editions/${edition.original}` : "/editions", edition.original ? "PUT" : "POST", body);
      setDone(`Saved edition ${body.name}`);
      setEdition(null);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const group = (kind: string) => products.filter((p) => p.kind === kind);

  return (
    <div className="space-y-4">
      <Card
        title="Products & price book"
        action={<button type="button" className={BTN_PRIMARY} onClick={() => setEditing({ original: null, d: blankProduct(currencies) })}>+ Product</button>}
      >
        <p className="mb-3 text-xs text-slate-400">
          Prices are per unit per month in each currency. On an annual term the product's annual discount comes off the list price (a negotiated price on a contract
          isn't discounted again). Products grant modules; a per-user product opens its modules only for users holding its seat.
        </p>
        {(["core", "service", "add_on"] as const).map((kind) => (
          <div key={kind} className="mb-3">
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{KIND_LABEL[kind]}</p>
            <div className="overflow-x-auto rounded-xl border border-slate-800">
              <table className="w-full min-w-[720px] text-sm">
                <thead className="bg-slate-900/80 text-[11px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-3 py-2 text-left">Product</th>
                    <th className="px-3 py-2 text-left">Sold</th>
                    {currencies.map((c) => <th key={c} className="px-3 py-2 text-right">{c} / mo</th>)}
                    <th className="px-3 py-2 text-right">Annual off</th>
                    <th className="px-3 py-2 text-left">Grants</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {group(kind).map((p) => (
                    <tr key={p.code} className={`border-t border-slate-800 ${p.is_active ? "" : "opacity-50"}`}>
                      <td className="px-3 py-2">
                        <p className="font-semibold text-slate-100">{p.name}</p>
                        <p className="font-mono text-[11px] text-slate-500">{p.code}{p.requires.length ? ` · needs ${p.requires.map((r) => names[r] ?? r).join(", ")}` : ""}</p>
                      </td>
                      <td className="px-3 py-2 text-xs text-slate-400">{UNIT_LABEL[p.unit]}<br /><span className="text-slate-500">{p.unit_label}</span></td>
                      {currencies.map((c) => (
                        <td key={c} className="px-3 py-2 text-right font-mono text-slate-200">{p.prices[c] !== undefined ? money(p.prices[c], c) : <span className="text-amber-400">not set</span>}</td>
                      ))}
                      <td className="px-3 py-2 text-right text-slate-300">{p.annual_discount_pct ? `${p.annual_discount_pct}%` : "—"}</td>
                      <td className="px-3 py-2 text-xs text-slate-400">
                        {[...p.modules.map((m) => modules.find((x) => x.key === m)?.label ?? m), ...Object.entries(p.limits).map(([k, v]) => `${num(v)} ${catalog.limit_labels[k] ?? k}`)].join(" · ") || "—"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button type="button" className={BTN} onClick={() => setEditing({ original: p.code, d: toDraft(p, currencies) })}>Edit</button>
                      </td>
                    </tr>
                  ))}
                  {group(kind).length === 0 && (
                    <tr><td colSpan={5 + currencies.length} className="px-3 py-3 text-xs text-slate-500">None.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </Card>

      {editing && (
        <Card title={editing.original ? `Edit ${editing.d.name}` : "New product"} className="border-sky-500/40">
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Code">
              <input className={INPUT} disabled={!!editing.original} value={editing.d.code} placeholder="e.g. wfm" onChange={(e) => setEditing({ ...editing, d: { ...editing.d, code: e.target.value.toLowerCase() } })} />
            </Field>
            <Field label="Name">
              <input className={INPUT} value={editing.d.name} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, name: e.target.value } })} />
            </Field>
            <Field label="Type">
              <select className={INPUT} value={editing.d.kind} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, kind: e.target.value } })}>
                <option value="core">Core</option>
                <option value="service">Service</option>
                <option value="add_on">Add-on</option>
              </select>
            </Field>
            <Field label="Sold">
              <select className={INPUT} value={editing.d.unit} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, unit: e.target.value } })}>
                <option value="user">Per user (seats)</option>
                <option value="org">Per organization</option>
                <option value="pack">Per pack / unit</option>
              </select>
            </Field>
            <Field label="Unit label" hint='Shown after the quantity: "user", "1M AI tokens", "10 GB"'>
              <input className={INPUT} value={editing.d.unit_label} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, unit_label: e.target.value } })} />
            </Field>
            <Field label="Annual-term discount %">
              <input className={INPUT} type="number" min={0} max={100} value={editing.d.annual_discount_pct} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, annual_discount_pct: e.target.value } })} />
            </Field>
            {currencies.map((c) => (
              <Field key={c} label={`List price ${c} / unit / month`}>
                <input className={INPUT} type="number" min={0} value={editing.d.prices[c] ?? ""} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, prices: { ...editing.d.prices, [c]: e.target.value } } })} />
              </Field>
            ))}
            <Field label="Min quantity">
              <input className={INPUT} type="number" min={0} value={editing.d.min_quantity} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, min_quantity: e.target.value } })} />
            </Field>
            <Field label="Max quantity" hint="Blank = no limit">
              <input className={INPUT} type="number" min={0} value={editing.d.max_quantity} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, max_quantity: e.target.value } })} />
            </Field>
            <Field label="Trial days">
              <input className={INPUT} type="number" min={0} value={editing.d.trial_days} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, trial_days: e.target.value } })} />
            </Field>
            <Field label="Limits per unit" hint="e.g. ai_tokens_month=1000000, storage_gb=10, users=1">
              <input className={INPUT} value={editing.d.limits} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, limits: e.target.value } })} />
            </Field>
            <Field label="Description">
              <input className={INPUT} value={editing.d.description} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, description: e.target.value } })} />
            </Field>
            <Field label="Features (one per line)">
              <textarea className={INPUT} rows={3} value={editing.d.features} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, features: e.target.value } })} />
            </Field>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div>
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Grants modules</p>
              <div className="flex flex-wrap gap-2">
                {modules.map((m) => (
                  <label key={m.key} className="flex items-center gap-1.5 rounded-lg border border-slate-800 px-2 py-1 text-xs text-slate-300">
                    <input type="checkbox" checked={editing.d.modules.includes(m.key)}
                           onChange={(e) => setEditing({ ...editing, d: { ...editing.d, modules: e.target.checked ? [...editing.d.modules, m.key] : editing.d.modules.filter((x) => x !== m.key) } })} />
                    {m.label}
                  </label>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Requires</p>
              <div className="flex flex-wrap gap-2">
                {products.filter((p) => p.code !== editing.original).map((p) => (
                  <label key={p.code} className="flex items-center gap-1.5 rounded-lg border border-slate-800 px-2 py-1 text-xs text-slate-300">
                    <input type="checkbox" checked={editing.d.requires.includes(p.code)}
                           onChange={(e) => setEditing({ ...editing, d: { ...editing.d, requires: e.target.checked ? [...editing.d.requires, p.code] : editing.d.requires.filter((x) => x !== p.code) } })} />
                    {p.name}
                  </label>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-slate-300">
              <input type="checkbox" checked={editing.d.is_active} onChange={(e) => setEditing({ ...editing, d: { ...editing.d, is_active: e.target.checked } })} /> On sale
            </label>
            <span className="ml-auto" />
            <button type="button" className={BTN} onClick={() => setEditing(null)}>Cancel</button>
            <button type="button" className={BTN_PRIMARY} disabled={busy || !editing.d.name.trim()} onClick={saveProduct}>{busy ? "Saving…" : "Save product"}</button>
          </div>
        </Card>
      )}

      <Card
        title="Editions"
        action={
          <button type="button" className={BTN_PRIMARY}
                  onClick={() => setEdition({ original: null, e: { code: "", name: "", description: "", discount_pct: "0", trial_days: "14", highlight: false, is_active: true, items: {} } })}>
            + Edition
          </button>
        }
      >
        <p className="mb-3 text-xs text-slate-400">Bundles a subscription starts from. Estimates are monthly list prices in {settings.default_currency}, after the edition discount.</p>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {editions.map((e: LicEdition) => (
            <div key={e.code} className={`relative rounded-2xl border p-4 ${e.highlight ? "border-sky-500/60 bg-sky-500/5" : "border-slate-800 bg-slate-950/40"} ${e.is_active ? "" : "opacity-50"}`}>
              {e.highlight && <span className="absolute -top-2 right-3 rounded-full bg-sky-600 px-2 py-0.5 text-[10px] font-bold text-white">Most popular</span>}
              <p className="text-base font-bold text-slate-100">{e.name}</p>
              <p className="text-xs text-slate-400">{e.description}</p>
              <p className="mt-2 text-2xl font-bold text-slate-100">{money(e.monthly_estimate, e.currency)}<span className="text-xs font-normal text-slate-500"> / month</span></p>
              {e.discount_pct > 0 && <p className="text-xs text-emerald-300">{e.discount_pct}% edition discount</p>}
              <ul className="mt-2 space-y-0.5 text-xs text-slate-300">
                {e.items.map((i) => <li key={i.product}>• {i.quantity} × {names[i.product] ?? i.product}</li>)}
              </ul>
              {e.trial_days > 0 && <p className="mt-2 text-[11px] text-violet-300">{e.trial_days}-day trial</p>}
              <button type="button" className={`${BTN} mt-3`}
                      onClick={() => setEdition({ original: e.code, e: { code: e.code, name: e.name, description: e.description, discount_pct: String(e.discount_pct), trial_days: String(e.trial_days), highlight: e.highlight, is_active: e.is_active, items: Object.fromEntries(e.items.map((i) => [i.product, String(i.quantity)])) } })}>
                Edit
              </button>
            </div>
          ))}
        </div>
      </Card>

      {edition && (
        <Card title={edition.original ? `Edit edition ${edition.e.name}` : "New edition"} className="border-sky-500/40">
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Code"><input className={INPUT} disabled={!!edition.original} value={edition.e.code} onChange={(ev) => setEdition({ ...edition, e: { ...edition.e, code: ev.target.value.toLowerCase() } })} /></Field>
            <Field label="Name"><input className={INPUT} value={edition.e.name} onChange={(ev) => setEdition({ ...edition, e: { ...edition.e, name: ev.target.value } })} /></Field>
            <Field label="Edition discount %"><input className={INPUT} type="number" min={0} max={100} value={edition.e.discount_pct} onChange={(ev) => setEdition({ ...edition, e: { ...edition.e, discount_pct: ev.target.value } })} /></Field>
            <Field label="Description"><input className={INPUT} value={edition.e.description} onChange={(ev) => setEdition({ ...edition, e: { ...edition.e, description: ev.target.value } })} /></Field>
            <Field label="Trial days"><input className={INPUT} type="number" min={0} value={edition.e.trial_days} onChange={(ev) => setEdition({ ...edition, e: { ...edition.e, trial_days: ev.target.value } })} /></Field>
            <div className="flex items-end gap-4 pb-2 text-xs text-slate-300">
              <label className="flex items-center gap-1.5"><input type="checkbox" checked={edition.e.highlight} onChange={(ev) => setEdition({ ...edition, e: { ...edition.e, highlight: ev.target.checked } })} /> Most popular</label>
              <label className="flex items-center gap-1.5"><input type="checkbox" checked={edition.e.is_active} onChange={(ev) => setEdition({ ...edition, e: { ...edition.e, is_active: ev.target.checked } })} /> On sale</label>
            </div>
          </div>
          <p className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Included quantities (blank = not included)</p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {products.map((p) => (
              <label key={p.code} className="flex items-center gap-2 rounded-lg border border-slate-800 px-2 py-1.5 text-xs text-slate-300">
                <input type="number" min={0} className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100" value={edition.e.items[p.code] ?? ""}
                       onChange={(ev) => setEdition({ ...edition, e: { ...edition.e, items: { ...edition.e.items, [p.code]: ev.target.value } } })} />
                <span className="min-w-0 truncate">{p.name} <span className="text-slate-500">({p.unit_label})</span></span>
              </label>
            ))}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className={BTN} onClick={() => setEdition(null)}>Cancel</button>
            <button type="button" className={BTN_PRIMARY} disabled={busy || !edition.e.name.trim()} onClick={saveEdition}>{busy ? "Saving…" : "Save edition"}</button>
          </div>
        </Card>
      )}
    </div>
  );
}
