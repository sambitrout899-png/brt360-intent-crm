import { useCallback, useEffect, useState } from "react";
import {
  compileProductRule,
  deleteModelAttribute,
  deleteModelComponent,
  deleteProductRule,
  exportProducts,
  importProducts,
  modelProducts,
  modelTree,
  newModelProduct,
  publishProduct,
  setSlot,
  saveModelAttribute,
  saveModelComponent,
  saveProductRule,
  toggleProductRule,
  type ModelAttribute,
  type ModelComponent,
  type ModelProduct,
  type ProductTree,
} from "../api/client";
import { INPUT_CLS } from "../components/intent/intentUi";
import { ModelLifecycle, ProductClassesPanel } from "./AdminModelLifecycle";
import InheritancePanel from "../components/products/InheritancePanel";
import PriceFormulaBuilder from "../components/products/PriceFormulaBuilder";
import ProductPictureView from "../components/products/ProductPictureView";
import { applyProductDesign, catalogProduct, planProductDesign, type ProductPicture } from "../api/client";
import type { DesignPlan } from "../api/types";
import { useIntentRefresh } from "../workspace";

const clean = (e: unknown) => String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");
const REFERENCE_TYPES = ["IP Address", "Place", "Logical Device", "Physical Device", "Device Interface", "Port", "VLAN", "Location", "Site", "Circuit"];
const RULE_HELP: Record<string, { title: string; what: string; examples: string[] }> = {
  eligibility: {
    title: "Eligibility",
    what: "Who may buy it - checked when it's offered, quoted or ordered for a customer.",
    examples: ["only for B2B customers", "only for B2B customers in North or South", "not for prospects", "only for customers who already have Fiber 800"],
  },
  compatibility: {
    title: "Compatibility",
    what: "Which other products it needs or can't go with - on the same quote/order or among what the customer already has.",
    examples: ["requires Static IP Pack", "requires Static IP Pack already installed", "cannot be combined with Home Fiber 100"],
  },
  constraint: {
    title: "Constraints",
    what: "Inside its configuration - attributes and child products that require, exclude or limit each other.",
    examples: ["if Access Medium is DSL then Bandwidth must be at most 2048", "CPE requires CPE Type", "if Access Medium is Fiber then CPE is required"],
  },
};

function save(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}

function domainText(a: ModelAttribute) {
  if (a.min !== null || a.max !== null) return `${a.min ?? "…"} – ${a.max ?? "…"} ${a.unit ?? ""}`.trim();
  if (a.reference_type) return `${a.reference_type} (from inventory)`;
  if (a.values.length) return a.values.length > 5 ? `${a.values.slice(0, 5).join(", ")} +${a.values.length - 5}` : a.values.join(", ");
  return a.description || "free text";
}

type AttrDraft = {
  original?: string; name: string; component: string | null; affects_price: boolean; affects_provisioning: boolean; required: boolean;
  domain: "values" | "range" | "reference" | "text" | "yesno"; values: string; min: string; max: string; unit: string; reference_type: string;
  default: string; description: string; impacts: string;
};
type CompDraft = {
  rename_from?: string; name: string; parent: string | null; description: string; mandatory: boolean; max_qty: string; charge_type: string; price: string;
  affects_provisioning: boolean; slot: "none" | "products" | "class"; slot_products: string; slot_class: string; slot_default: string;
};

function attrDraft(a: ModelAttribute | null, component: string | null): AttrDraft {
  if (!a) return { name: "", component, affects_price: false, affects_provisioning: true, required: false, domain: "values", values: "", min: "", max: "", unit: "", reference_type: "IP Address", default: "", description: "", impacts: "" };
  const yesno = a.values.length === 2 && a.values.join(",") === "Yes,No";
  return {
    original: a.name, name: a.name, component: a.component, affects_price: a.affects_price, affects_provisioning: a.affects_provisioning, required: a.required,
    domain: a.min !== null || a.max !== null ? "range" : a.reference_type ? "reference" : yesno ? "yesno" : a.values.length ? "values" : "text",
    values: a.values.join(", "), min: a.min?.toString() ?? "", max: a.max?.toString() ?? "", unit: a.unit ?? "", reference_type: a.reference_type ?? "IP Address",
    default: a.default ?? "", description: a.description ?? "",
    impacts: a.price_impacts.map((i) => `${i.value}: ${i.type === "per_unit" ? `${i.amount} each` : `${i.amount >= 0 ? "+" : ""}${i.amount}${i.type === "percent" ? "%" : ""}`}`).join("; "),
  };
}

function parseImpacts(text: string) {
  return text
    .split(/;|\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => {
      const m = /^(.+?)\s*[:=]\s*([+-])?\s*(\d[\d,]*(?:\.\d+)?)\s*(%)?\s*(each|per\s+\w+)?$/i.exec(p);
      if (!m) throw new Error(`Price impact "${p}" - write it like "Fiber: +500", "DSL: -10%" or "*: 2 each"`);
      const amount = Number(m[3].replace(/,/g, "")) * (m[2] === "-" ? -1 : 1);
      return { value: m[1].trim(), type: m[5] ? "per_unit" : m[4] ? "percent" : "flat", amount };
    });
}

function AttributeEditor({ draft, product, onDone, onCancel }: { draft: AttrDraft; product: string; onDone: (t: ProductTree, msg: string) => void; onCancel: () => void }) {
  const [d, setD] = useState(draft);
  const [error, setError] = useState("");
  const submit = async () => {
    setError("");
    try {
      const body: Record<string, unknown> = {
        name: d.name.trim(), component: d.component, affects_price: d.affects_price, affects_provisioning: d.affects_provisioning, required: d.required,
        default: d.default || null, description: d.description || null, values: [], min: null, max: null, reference_type: null, unit: d.unit || null,
        price_impacts: d.affects_price ? parseImpacts(d.impacts) : [],
      };
      if (d.domain === "values") body.values = d.values.split(",").map((v) => v.trim()).filter(Boolean);
      if (d.domain === "yesno") body.values = ["Yes", "No"];
      if (d.domain === "range") Object.assign(body, { min: d.min === "" ? null : Number(d.min), max: d.max === "" ? null : Number(d.max) });
      if (d.domain === "reference") body.reference_type = d.reference_type;
      if (d.original && d.original !== d.name.trim()) await deleteModelAttribute(product, d.original);
      const r = await saveModelAttribute(product, body);
      onDone(r.tree, r.change);
    } catch (e) {
      setError(clean(e));
    }
  };
  return (
    <div className="space-y-2 rounded-lg border border-sky-700/50 bg-slate-950 p-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <input autoFocus value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="Attribute name, e.g. Access Medium" className={INPUT_CLS} />
        <select value={d.domain} onChange={(e) => setD({ ...d, domain: e.target.value as AttrDraft["domain"] })} className={INPUT_CLS}>
          <option value="values">List of values</option>
          <option value="range">Range (min – max)</option>
          <option value="reference">Inventory reference (filled by provisioning)</option>
          <option value="yesno">Yes / No</option>
          <option value="text">Free text</option>
        </select>
        {d.domain === "values" && <input value={d.values} onChange={(e) => setD({ ...d, values: e.target.value })} placeholder="Fiber, DSL, Microwave" className={`${INPUT_CLS} sm:col-span-2`} />}
        {d.domain === "range" && (
          <div className="flex gap-2 sm:col-span-2">
            <input value={d.min} onChange={(e) => setD({ ...d, min: e.target.value })} placeholder="Min" className={INPUT_CLS} />
            <input value={d.max} onChange={(e) => setD({ ...d, max: e.target.value })} placeholder="Max" className={INPUT_CLS} />
            <input value={d.unit} onChange={(e) => setD({ ...d, unit: e.target.value })} placeholder="Unit, e.g. Kbps" className={INPUT_CLS} />
          </div>
        )}
        {d.domain === "reference" && (
          <select value={d.reference_type} onChange={(e) => setD({ ...d, reference_type: e.target.value })} className={`${INPUT_CLS} sm:col-span-2`}>
            {REFERENCE_TYPES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        )}
        <input value={d.default} onChange={(e) => setD({ ...d, default: e.target.value })} placeholder="Default (optional)" className={INPUT_CLS} />
        <input value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} placeholder="Description (optional)" className={INPUT_CLS} />
      </div>
      <div className="flex flex-wrap gap-4 text-xs text-slate-300">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={d.required} onChange={(e) => setD({ ...d, required: e.target.checked })} /> Mandatory
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={d.affects_price} onChange={(e) => setD({ ...d, affects_price: e.target.checked })} /> 💰 Pricing impact
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={d.affects_provisioning} onChange={(e) => setD({ ...d, affects_provisioning: e.target.checked })} /> 🔧 Provisioning impact
        </label>
      </div>
      {d.affects_price && (
        <input value={d.impacts} onChange={(e) => setD({ ...d, impacts: e.target.value })} placeholder="Price impacts: Fiber: +500; DSL: -10%; *: 0.5 each" className={INPUT_CLS} />
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}
      <div className="flex gap-2">
        <button type="button" disabled={!d.name.trim()} onClick={submit} className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
          Save attribute
        </button>
        <button type="button" onClick={onCancel} className="text-xs text-slate-400">
          Cancel
        </button>
      </div>
    </div>
  );
}

function ComponentEditor({ draft, product, onDone, onCancel }: { draft: CompDraft; product: string; onDone: (t: ProductTree, msg: string) => void; onCancel: () => void }) {
  const [d, setD] = useState(draft);
  const [error, setError] = useState("");
  const submit = async () => {
    setError("");
    try {
      let r = await saveModelComponent(product, {
        name: d.name.trim(), parent: d.parent, description: d.description, mandatory: d.mandatory, min_qty: d.mandatory ? 1 : 0, default_qty: d.mandatory ? 1 : 0,
        max_qty: Math.max(1, Number(d.max_qty) || 1), charge_type: d.slot === "none" ? d.charge_type || null : null, price: d.price === "" ? 0 : Number(d.price),
        affects_provisioning: d.affects_provisioning, ...(d.rename_from ? { rename_from: d.rename_from } : {}),
      });
      if (d.slot !== "none" || d.rename_from) {
        r = await setSlot(product, d.name.trim(), {
          domain_type: d.slot, products: d.slot_products.split(",").map((p) => p.trim()).filter(Boolean), class_name: d.slot_class || null, default: d.slot_default || null,
        });
      }
      onDone(r.tree, r.change);
    } catch (e) {
      setError(clean(e));
    }
  };
  return (
    <div className="space-y-2 rounded-lg border border-violet-700/50 bg-slate-950 p-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <input autoFocus value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="Child product, e.g. CPE or BBI-MRC" className={INPUT_CLS} />
        <input value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} placeholder="Description" className={INPUT_CLS} />
        <select value={d.slot} onChange={(e) => setD({ ...d, slot: e.target.value as CompDraft["slot"] })} className={`${INPUT_CLS} sm:col-span-2`}>
          <option value="none">A fixed part of this product</option>
          <option value="products">A slot filled by one of these catalog products…</option>
          <option value="class">A slot filled by any product of a class…</option>
        </select>
        {d.slot === "products" && <input value={d.slot_products} onChange={(e) => setD({ ...d, slot_products: e.target.value })} placeholder="Router Basic, Router Pro" className={INPUT_CLS} />}
        {d.slot === "class" && <input value={d.slot_class} onChange={(e) => setD({ ...d, slot_class: e.target.value })} placeholder="Class, e.g. Routers" className={INPUT_CLS} />}
        {d.slot !== "none" && <input value={d.slot_default} onChange={(e) => setD({ ...d, slot_default: e.target.value })} placeholder="Default product (optional)" className={INPUT_CLS} />}
        {d.slot === "none" && <select value={d.charge_type} onChange={(e) => setD({ ...d, charge_type: e.target.value })} className={INPUT_CLS}>
          <option value="">No charge of its own</option>
          <option value="recurring">Monthly recurring charge (MRC)</option>
          <option value="one_time">One-time charge (NRC)</option>
        </select>}
        {d.slot === "none" && d.charge_type && <input value={d.price} onChange={(e) => setD({ ...d, price: e.target.value })} placeholder="Price" className={INPUT_CLS} />}
        <label className="flex items-center gap-2 text-xs text-slate-300">
          Up to
          <input value={d.max_qty} onChange={(e) => setD({ ...d, max_qty: e.target.value })} className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100" />
          per product
        </label>
      </div>
      <div className="flex flex-wrap gap-4 text-xs text-slate-300">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={d.mandatory} onChange={(e) => setD({ ...d, mandatory: e.target.checked })} /> Mandatory
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={d.affects_provisioning} onChange={(e) => setD({ ...d, affects_provisioning: e.target.checked })} /> 🔧 Provisioning impact
        </label>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      <div className="flex gap-2">
        <button type="button" disabled={!d.name.trim()} onClick={submit} className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
          Save child product
        </button>
        <button type="button" onClick={onCancel} className="text-xs text-slate-400">
          Cancel
        </button>
      </div>
    </div>
  );
}

function AttributeRow({ a, onEdit, onDelete }: { a: ModelAttribute; onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="group flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-slate-800/60">
      <span className="text-slate-500">•</span>
      <button type="button" onClick={onEdit} className="text-left text-slate-200 hover:text-sky-300">
        {a.name}
        {a.required && <span className="text-sky-400"> *</span>}
      </button>
      <span className="truncate text-xs text-slate-500">{domainText(a)}</span>
      <span className="ml-auto flex shrink-0 items-center gap-1 text-[10px]">
        {a.affects_price && <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300" title="Pricing impact">💰 price{a.price_impacts.length ? ` (${a.price_impacts.length})` : ""}</span>}
        {a.affects_provisioning && <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-300" title="Provisioning impact">🔧 provisioning</span>}
        <span className={`rounded px-1.5 py-0.5 ${a.required ? "bg-sky-500/15 text-sky-300" : "bg-slate-800 text-slate-400"}`}>{a.required ? "M" : "O"}</span>
        <button type="button" onClick={onDelete} className="px-1 text-slate-600 opacity-0 hover:text-red-400 group-hover:opacity-100" title="Remove">
          ×
        </button>
      </span>
    </div>
  );
}

export default function AdminProductModelSection() {
  const [list, setList] = useState<ModelProduct[]>([]);
  const [tree, setTree] = useState<ProductTree | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<{ kind: "attr"; at: string; draft: AttrDraft } | { kind: "comp"; at: string; draft: CompDraft } | null>(null);
  const [tab, setTab] = useState<"overview" | "structure" | "pricing" | "rules" | "lifecycle" | "import" | "classes">("overview");
  const [picture, setPicture] = useState<ProductPicture | null>(null);
  const [aiText, setAiText] = useState("");
  const [aiPlan, setAiPlan] = useState<DesignPlan | null>(null);
  const [creating, setCreating] = useState<{ name: string; segment: string; price: string } | null>(null);
  const [upload, setUpload] = useState<{ name: string; b64: string } | null>(null);
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof importProducts>> | null>(null);
  const [busy, setBusy] = useState(false);
  const [ruleText, setRuleText] = useState<Record<string, string>>({});
  const [ruleCheck, setRuleCheck] = useState<Record<string, string>>({});

  const loadList = useCallback(() => modelProducts().then(setList).catch((e) => setError(clean(e))), []);
  const open = useCallback((name: string) => {
    setEditing(null);
    setStatus("");
    setError("");
    setAiPlan(null);
    modelTree(name).then(setTree).catch((e) => setError(clean(e)));
    catalogProduct(name).then(setPicture).catch(() => setPicture(null));
  }, []);

  useEffect(() => {
    loadList();
    // opened from the Catalog (or elsewhere) on a product
    let wanted: string | null = null;
    try {
      wanted = localStorage.getItem("brt360.admin.product");
      localStorage.removeItem("brt360.admin.product");
    } catch {
      wanted = null;
    }
    if (wanted) Promise.resolve(wanted).then(open);
  }, [loadList, open]);
  // an intent run from the Overview's buttons (price, pricelist, release ...) refreshes what is shown
  useIntentRefresh(() => {
    loadList();
    if (tree) open(tree.name);
  });

  const done = (t: ProductTree, msg: string) => {
    setTree(t);
    setEditing(null);
    setStatus(msg);
    loadList();
  };

  const download = async (opts: Parameters<typeof exportProducts>[0]) => {
    try {
      const { blob, fileName } = await exportProducts(opts);
      save(blob, fileName);
    } catch (e) {
      setError(clean(e));
    }
  };

  const runImport = async (dry: boolean) => {
    if (!upload) return;
    setBusy(true);
    setError("");
    try {
      const r = await importProducts(upload.name, upload.b64, dry);
      setPreview(r);
      if (!dry) {
        loadList();
        if (tree) open(tree.name);
        else if (r.created?.length) open(r.created[0]);
      }
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  const renderEditor = (at: string) =>
    editing && editing.at === at && tree ? (
      editing.kind === "attr" ? (
        <AttributeEditor draft={editing.draft} product={tree.name} onDone={done} onCancel={() => setEditing(null)} />
      ) : (
        <ComponentEditor draft={editing.draft} product={tree.name} onDone={done} onCancel={() => setEditing(null)} />
      )
    ) : null;

  const nodeActions = (component: string | null, at: string) => (
    <div className="flex gap-3 pl-2 text-[11px]">
      <button type="button" onClick={() => setEditing({ kind: "attr", at, draft: attrDraft(null, component) })} className="text-sky-400 hover:text-sky-300">
        + Attribute
      </button>
      <button
        type="button"
        onClick={() => setEditing({ kind: "comp", at, draft: { name: "", parent: component, description: "", mandatory: false, max_qty: "1", charge_type: "", price: "", affects_provisioning: true, slot: "none", slot_products: "", slot_class: "", slot_default: "" } })}
        className="text-violet-400 hover:text-violet-300"
      >
        + Child product
      </button>
    </div>
  );

  const renderComponent = (c: ModelComponent, depth: number) => (
    <div key={c.id} className="space-y-1 border-l border-slate-700 pl-3" style={{ marginLeft: depth ? 8 : 0 }}>
      <div className="group flex items-center gap-2 rounded-md bg-violet-500/5 px-2 py-1.5">
        <span className="text-violet-300">▸</span>
        <button
          type="button"
          onClick={() =>
            setEditing({
              kind: "comp", at: `c${c.id}`,
              draft: {
                rename_from: c.name, name: c.name, parent: c.parent, description: c.description, mandatory: c.mandatory, max_qty: String(c.max_qty), charge_type: c.charge_type ?? "",
                price: c.price ? String(c.price) : "", affects_provisioning: c.affects_provisioning, slot: c.slot ? c.slot.type : "none",
                slot_products: c.slot?.listed?.join(", ") ?? "", slot_class: c.slot?.class ?? "", slot_default: c.slot?.default ?? "",
              },
            })
          }
          className="font-semibold text-slate-100 hover:text-violet-300"
        >
          {c.name}
        </button>
        <span className="truncate text-xs text-slate-500">{c.description}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1 text-[10px]">
          {c.slot && <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-violet-300">slot: {c.slot.type === "class" ? `class ${c.slot.class}` : `${c.slot.products.length} products`}</span>}
          {c.charge_type && <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300">{c.price.toLocaleString()} {c.charge_type === "recurring" ? "/ month" : "one-time"}</span>}
          {c.max_qty > 1 && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">up to {c.max_qty}</span>}
          {c.affects_provisioning && <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-300">🔧</span>}
          <span className={`rounded px-1.5 py-0.5 ${c.mandatory ? "bg-sky-500/15 text-sky-300" : "bg-slate-800 text-slate-400"}`}>{c.mandatory ? "M" : "O"}</span>
          <button
            type="button"
            onClick={() => tree && window.confirm(`Remove ${c.name} and everything under it?`) && deleteModelComponent(tree.name, c.name).then((r) => done(r.tree, r.change)).catch((e) => setError(clean(e)))}
            className="px-1 text-slate-600 opacity-0 hover:text-red-400 group-hover:opacity-100"
          >
            ×
          </button>
        </span>
      </div>
      {renderEditor(`c${c.id}`)}
      <div className="pl-3">
        {c.attributes.map((a) => (
          <div key={a.id}>
            <AttributeRow
              a={a}
              onEdit={() => setEditing({ kind: "attr", at: `a${a.id}`, draft: attrDraft(a, c.name) })}
              onDelete={() => tree && deleteModelAttribute(tree.name, a.name).then((r) => done(r.tree, r.change)).catch((e) => setError(clean(e)))}
            />
            {renderEditor(`a${a.id}`)}
          </div>
        ))}
        {c.children.map((child) => renderComponent(child, depth + 1))}
        {nodeActions(c.name, `new-${c.id}`)}
        {renderEditor(`new-${c.id}`)}
      </div>
    </div>
  );

  const rulesOf = (type: string) => tree?.rules.filter((r) => r.rule_type === type) ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-slate-100">Product Modeller</h2>
          <p className="text-sm text-slate-400">
            Product hierarchies - root product, child products and their attributes - with pricing and provisioning impact, imported from or exported to the
            same spreadsheet product managers keep, and eligibility, compatibility and constraint rules written in plain English.
          </p>
        </div>
        <button type="button" onClick={() => download({ template: true, format: "xlsx" })} className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-300">
          ⬇ Template
        </button>
        <button type="button" onClick={() => download({ format: "xlsx" })} className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-300">
          ⬇ Export all (Excel)
        </button>
        <button type="button" onClick={() => (setTab("import"), setPreview(null))} className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white">
          ⬆ Import
        </button>
      </div>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}

      <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className="space-y-1">
          {list.map((p) => (
            <button
              key={p.name}
              type="button"
              onClick={() => (open(p.name), tab === "import" && setTab("structure"))}
              className={`w-full rounded-lg px-3 py-2 text-left ${tree?.name === p.name ? "bg-sky-500/15" : "hover:bg-slate-800"}`}
            >
              <span className="block truncate text-sm text-slate-100">{p.name}</span>
              <span className="text-[10px] text-slate-500">
                {p.segment} · {p.status} · {p.children} child · {p.attributes} attr · {p.rules} rules
              </span>
            </button>
          ))}
          {creating ? (
            <div className="space-y-2 rounded-lg border border-slate-700 p-2">
              <input autoFocus value={creating.name} onChange={(e) => setCreating({ ...creating, name: e.target.value })} placeholder="Root product name" className={INPUT_CLS} />
              <div className="flex gap-2">
                <select value={creating.segment} onChange={(e) => setCreating({ ...creating, segment: e.target.value })} className={INPUT_CLS}>
                  {["B2B", "B2C", "B2B-SMB", "B2B-Enterprise"].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
                <input value={creating.price} onChange={(e) => setCreating({ ...creating, price: e.target.value })} placeholder="Base price" className={INPUT_CLS} />
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={!creating.name.trim()}
                  onClick={() =>
                    newModelProduct({ name: creating.name.trim(), segment: creating.segment, price: Number(creating.price) || 0 })
                      .then((t) => (setCreating(null), done(t, `Created ${t.name} as a draft`)))
                      .catch((e) => setError(clean(e)))
                  }
                  className="rounded-lg bg-sky-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-40"
                >
                  Create
                </button>
                <button type="button" onClick={() => setCreating(null)} className="text-xs text-slate-400">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button type="button" onClick={() => setCreating({ name: "", segment: "B2B", price: "" })} className="w-full rounded-lg border border-dashed border-slate-600 px-3 py-2 text-sm text-slate-300 hover:border-sky-500">
              + New root product
            </button>
          )}
          <button type="button" onClick={() => setTab("classes")} className={`w-full rounded-lg px-3 py-2 text-left text-sm ${tab === "classes" ? "bg-sky-500/15 text-sky-200" : "text-slate-300 hover:bg-slate-800"}`}>
            🗂 Product classes
          </button>
        </aside>

        <section className="min-w-0 space-y-3">
          {tab === "classes" ? (
            <ProductClassesPanel products={list.map((p) => p.name)} />
          ) : tab === "import" ? (
            <div className="space-y-3 rounded-xl border border-slate-800 p-4">
              <h3 className="text-sm font-semibold text-slate-200">Import products</h3>
              <p className="text-xs text-slate-400">
                Excel (.xlsx) or CSV with the columns <b>Product Element, Type, Parent, Description, Mandatory/Optional, Pricing Impact, Provisioning Impact</b> - Type is Root
                Product, Child Product or Product Attribute; an attribute's Description is its values (comma-separated), a range ("512 Kbps to 4Mbps") or an inventory type (IP
                Address, Place, Logical Device, Device Interface). Optional: Price, Charge Type, Price Impacts, Segment ... and a <b>Rules</b> sheet. Nothing is removed - existing
                products are updated.
              </p>
              <label className="inline-block cursor-pointer rounded-lg border border-dashed border-slate-600 px-3 py-2 text-sm text-slate-300 hover:border-sky-500">
                {upload ? `📄 ${upload.name}` : "Choose .xlsx / .csv…"}
                <input
                  type="file"
                  accept=".xlsx,.csv"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (!f) return;
                    const reader = new FileReader();
                    reader.onload = () => {
                      setUpload({ name: f.name, b64: String(reader.result).split(",")[1] ?? "" });
                      setPreview(null);
                    };
                    reader.readAsDataURL(f);
                  }}
                />
              </label>
              <div className="flex gap-2">
                <button type="button" disabled={!upload || busy} onClick={() => runImport(true)} className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-200 disabled:opacity-40">
                  Check file
                </button>
                <button type="button" disabled={!upload || busy || !preview?.dry_run} onClick={() => runImport(false)} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
                  Import
                </button>
              </div>
              {preview && (
                <div className="space-y-2">
                  {preview.errors.length > 0 && (
                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200">
                      {preview.errors.map((e, i) => (
                        <p key={i}>
                          Row {e.row ?? "?"}
                          {e.element ? ` (${e.element})` : ""}: {e.error}
                        </p>
                      ))}
                    </div>
                  )}
                  <pre className="max-h-96 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">{(preview.dry_run ? preview.preview : preview.changes)?.join("\n")}</pre>
                  {!preview.dry_run && <p className="text-xs text-emerald-400">Imported {preview.changes?.length ?? 0} changes{preview.created?.length ? ` - new: ${preview.created.join(", ")}` : ""}.</p>}
                </div>
              )}
            </div>
          ) : tree ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-slate-100">{tree.name}</h3>
                <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">{tree.segment}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${tree.status === "active" ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>{tree.status}</span>
                <span className="font-mono text-[10px] text-slate-500">{tree.brt_id}</span>
                {status && <span className="text-xs text-emerald-400">{status}</span>}
                <span className="ml-auto" />
                {tree.status !== "active" && (
                  <button type="button" onClick={() => publishProduct(tree.name).then((t) => done(t, "Published - it can be sold now")).catch((e) => setError(clean(e)))} className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white">
                    Publish
                  </button>
                )}
                <button type="button" onClick={() => download({ names: [tree.name], format: "xlsx" })} className="rounded-lg border border-slate-600 px-2.5 py-1 text-xs text-slate-300">
                  ⬇ Excel
                </button>
                <button type="button" onClick={() => download({ names: [tree.name], format: "csv" })} className="rounded-lg border border-slate-600 px-2.5 py-1 text-xs text-slate-300">
                  ⬇ CSV
                </button>
              </div>
              <div className="flex gap-1 border-b border-slate-800">
                {(["overview", "structure", "pricing", "rules", "lifecycle"] as const).map((t) => (
                  <button key={t} type="button" onClick={() => setTab(t)} className={`px-3 py-1.5 text-sm font-semibold ${tab === t ? "border-b-2 border-sky-500 text-sky-400" : "text-slate-400"}`}>
                    {t === "overview" ? "Overview" : t === "structure" ? "Hierarchy" : t === "pricing" ? `Pricing (${tree.rules.filter((r) => r.rule_type === "price").length})`
                      : t === "rules" ? `Rules (${tree.rules.filter((r) => r.rule_type !== "price").length})` : "Configure & Release"}
                  </button>
                ))}
              </div>

              {tab === "structure" && (
                <div className="space-y-2 rounded-xl border border-violet-500/30 bg-violet-500/5 p-3">
                  <div className="flex gap-2">
                    <input
                      value={aiText}
                      onChange={(e) => setAiText(e.target.value)}
                      onKeyDown={(e) =>
                        e.key === "Enter" && aiText.trim() && planProductDesign(tree.name, aiText).then(setAiPlan).catch((err) => setError(clean(err)))
                      }
                      placeholder="✨ Tell the AI - e.g. Bandwidth: 100 Mbps +0, 1 Gbps +1500; technical: VLAN ID required; make 36 months 10% cheaper"
                      className={`${INPUT_CLS} flex-1`}
                    />
                    <button
                      type="button"
                      disabled={!aiText.trim()}
                      onClick={() => planProductDesign(tree.name, aiText).then(setAiPlan).catch((err) => setError(clean(err)))}
                      className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
                    >
                      Preview
                    </button>
                  </div>
                  {aiPlan && (
                    <div className="space-y-1 text-xs">
                      {aiPlan.preview.map((line) => (
                        <p key={line} className="text-slate-200">
                          • {line}
                        </p>
                      ))}
                      {aiPlan.ops.length === 0 && <p className="text-amber-300">I couldn't work out a change from that.</p>}
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={!aiPlan.ops.length}
                          onClick={() =>
                            applyProductDesign(tree.name, aiPlan.ops)
                              .then((r) => (setAiPlan(null), setAiText(""), setStatus(r.changes.join("; ")), open(tree.name), loadList()))
                              .catch((err) => setError(clean(err)))
                          }
                          className="rounded-lg bg-violet-600 px-3 py-1 font-semibold text-white disabled:opacity-40"
                        >
                          Apply to the workspace
                        </button>
                        <button type="button" onClick={() => setAiPlan(null)} className="text-slate-400">
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {tab === "structure" && <InheritancePanel key={tree.name} product={tree.name} onChanged={() => (open(tree.name), loadList())} />}

              {tab === "structure" && (
                <div className="space-y-1 rounded-xl border border-slate-800 bg-slate-950/40 p-3">
                  <div className="flex items-center gap-2 rounded-md bg-sky-500/10 px-2 py-1.5">
                    <span className="text-sky-300">◆</span>
                    <span className="font-semibold text-slate-100">{tree.name}</span>
                    <span className="text-xs text-slate-500">root product · base {tree.price.toLocaleString()}</span>
                  </div>
                  <div className="pl-3">
                    {tree.attributes.map((a) => (
                      <div key={a.id}>
                        <AttributeRow
                          a={a}
                          onEdit={() => setEditing({ kind: "attr", at: `a${a.id}`, draft: attrDraft(a, null) })}
                          onDelete={() => deleteModelAttribute(tree.name, a.name).then((r) => done(r.tree, r.change)).catch((e) => setError(clean(e)))}
                        />
                        {renderEditor(`a${a.id}`)}
                      </div>
                    ))}
                    {tree.children.map((c) => renderComponent(c, 0))}
                    {nodeActions(null, "new-root")}
                    {renderEditor("new-root")}
                  </div>
                  <p className="pt-2 text-[11px] text-slate-500">
                    M = mandatory, O = optional · 💰 changes the price (flows to the quote and opportunity) · 🔧 needed by provisioning (flows to the order, work orders, network
                    and inventory). Click any element to edit it.
                  </p>
                </div>
              )}

              {tab === "pricing" && <PriceFormulaBuilder key={tree.name} product={tree.name} rules={tree.rules} onChanged={() => (open(tree.name), loadList())} />}

              {tab === "overview" && (picture ? <ProductPictureView p={picture} inModeller onChanged={() => open(tree.name)} /> : <p className="text-xs text-slate-500">Loading…</p>)}

              {tab === "lifecycle" && <ModelLifecycle key={tree.name} product={tree.name} onChanged={() => (open(tree.name), loadList())} />}

              {tab === "rules" &&
                (["eligibility", "compatibility", "constraint"] as const).map((type) => (
                  <div key={type} className="space-y-2 rounded-xl border border-slate-800 p-3">
                    <div>
                      <p className="text-sm font-semibold text-slate-200">{RULE_HELP[type].title}</p>
                      <p className="text-xs text-slate-500">{RULE_HELP[type].what}</p>
                    </div>
                    {rulesOf(type).map((r) => (
                      <div key={r.id} className={`flex items-start gap-2 rounded-lg border border-slate-800 px-3 py-2 text-xs ${r.is_active ? "" : "opacity-50"}`}>
                        <div className="min-w-0 flex-1">
                          <p className="text-slate-200">{r.explanation}</p>
                          <p className="text-[10px] text-slate-500">
                            "{r.source_text}"{r.message ? ` · says "${r.message}"` : ""}
                          </p>
                        </div>
                        <button type="button" onClick={() => toggleProductRule(r.id, !r.is_active).then(() => open(tree.name))} className="text-[10px] text-slate-400 hover:text-sky-300">
                          {r.is_active ? "Switch off" : "Switch on"}
                        </button>
                        <button type="button" onClick={() => deleteProductRule(r.id).then(() => (open(tree.name), loadList()))} className="text-slate-500 hover:text-red-400">
                          ×
                        </button>
                      </div>
                    ))}
                    <div className="flex gap-2">
                      <input
                        value={ruleText[type] ?? ""}
                        onChange={(e) => (setRuleText({ ...ruleText, [type]: e.target.value }), setRuleCheck({ ...ruleCheck, [type]: "" }))}
                        placeholder={`✨ e.g. ${RULE_HELP[type].examples[0]}`}
                        className={`${INPUT_CLS} flex-1`}
                      />
                      <button
                        type="button"
                        disabled={!ruleText[type]?.trim()}
                        onClick={() =>
                          compileProductRule(tree.name, type, ruleText[type])
                            .then((c) => setRuleCheck({ ...ruleCheck, [type]: `✓ ${c.explanation}${c.engine === "ai" ? " (AI)" : ""}` }))
                            .catch((e) => setRuleCheck({ ...ruleCheck, [type]: `✗ ${clean(e)}` }))
                        }
                        className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-200 disabled:opacity-40"
                      >
                        Check
                      </button>
                      <button
                        type="button"
                        disabled={!ruleText[type]?.trim()}
                        onClick={() =>
                          saveProductRule(tree.name, { rule_type: type, text: ruleText[type] })
                            .then(() => (setRuleText({ ...ruleText, [type]: "" }), setRuleCheck({ ...ruleCheck, [type]: "" }), open(tree.name), loadList()))
                            .catch((e) => setRuleCheck({ ...ruleCheck, [type]: `✗ ${clean(e)}` }))
                        }
                        className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
                      >
                        Add rule
                      </button>
                    </div>
                    {ruleCheck[type] && <p className={`text-xs ${ruleCheck[type].startsWith("✓") ? "text-emerald-300" : "text-red-400"}`}>{ruleCheck[type]}</p>}
                    <div className="flex flex-wrap gap-1">
                      {RULE_HELP[type].examples.map((x) => (
                        <button key={x} type="button" onClick={() => setRuleText({ ...ruleText, [type]: x })} className="rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400 hover:border-sky-500 hover:text-sky-300">
                          {x}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
            </>
          ) : (
            <p className="rounded-xl border border-dashed border-slate-700 p-6 text-center text-sm text-slate-500">Pick a product on the left, create one, or import your spreadsheet.</p>
          )}
        </section>
      </div>
    </div>
  );
}
