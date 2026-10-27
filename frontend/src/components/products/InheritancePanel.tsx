import { useCallback, useEffect, useState } from "react";
import { bindProductClass, deleteModelAttribute, productClasses, productInheritance, saveModelAttribute, type Inheritance, type InheritedAttribute, type ProductClassOut } from "../../api/client";

const clean = (e: unknown) => String((e as Error).message ?? e).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");
const SOURCE: Record<InheritedAttribute["source"], { label: string; cls: string }> = {
  class: { label: "Inherited", cls: "bg-violet-500/15 text-violet-300" },
  override: { label: "Narrowed here", cls: "bg-amber-500/15 text-amber-300" },
  own: { label: "Own", cls: "bg-sky-500/15 text-sky-300" },
};

/** Attribute library -> class -> product: which class the product is bound
 * to, and for every attribute the values each level allows. A product can
 * narrow what it inherits (drop values, re-price one) - never widen it. */
export default function InheritancePanel({ product, onChanged }: { product: string; onChanged: () => void }) {
  const [info, setInfo] = useState<Inheritance | null>(null);
  const [classes, setClasses] = useState<ProductClassOut[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const load = useCallback(() => {
    productInheritance(product).then(setInfo).catch((e) => setError(clean(e)));
    productClasses().then(setClasses).catch(() => setClasses([]));
  }, [product]);
  useEffect(() => {
    load();
  }, [load]);

  const after = (msg: string) => (setNote(msg), setError(""), setEditing(null), load(), onChanged());
  const bind = (cls: string) =>
    bindProductClass(product, cls || null)
      .then(() => after(cls ? `Bound to ${cls} - it now inherits its attributes` : "Unbound from its class"))
      .catch((e) => setError(clean(e)));
  const narrow = (a: InheritedAttribute) =>
    saveModelAttribute(product, { name: a.name, values: picked, affects_price: a.kind === "pricing", affects_provisioning: a.kind === "technical", required: a.required })
      .then(() => after(`${a.name} narrowed to ${picked.join(", ")}`))
      .catch((e) => setError(clean(e)));
  const reset = (a: InheritedAttribute) =>
    deleteModelAttribute(product, a.name).then(() => after(`${a.name} reset to what ${a.from} says`)).catch((e) => setError(clean(e)));

  if (!info) return <p className="text-xs text-slate-500">Loading…</p>;
  const inherited = info.attributes.filter((a) => a.source !== "own");
  const own = info.attributes.filter((a) => a.source === "own");

  return (
    <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-slate-100">Class & inheritance</h4>
        <div className="flex items-center gap-1 text-xs">
          <span className="rounded bg-slate-800 px-2 py-0.5 text-slate-400">Attribute library</span>
          {info.chain.map((c) => (
            <span key={c} className="flex items-center gap-1">
              <span className="text-slate-600">→</span>
              <span className="rounded bg-violet-500/15 px-2 py-0.5 text-violet-200">{c}</span>
            </span>
          ))}
          <span className="text-slate-600">→</span>
          <span className="rounded bg-sky-500/15 px-2 py-0.5 font-semibold text-sky-200">{product}</span>
        </div>
        <select value={info.class ?? ""} onChange={(e) => bind(e.target.value)} className="ml-auto rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
          <option value="">Not bound to a class</option>
          {classes.map((c) => (
            <option key={c.name} value={c.name}>
              Bound to {c.name}
              {c.parent ? ` (under ${c.parent})` : ""}
            </option>
          ))}
        </select>
      </div>
      <p className="text-[11px] leading-relaxed text-slate-500">
        The library holds every value an attribute can have; a class picks the ones its products need; a product inherits them and may narrow further - drop values,
        change a value's price - but never add one the class doesn't offer. Change the class and every product bound to it follows.
      </p>
      {error && <p className="rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
      {note && <p className="text-xs text-emerald-400">{note}</p>}

      {inherited.length === 0 && (
        <p className="text-xs text-slate-500">{info.class ? `${info.class} has no attributes yet - add them under Product classes.` : "Bind the product to a class to inherit its attributes."}</p>
      )}
      <div className="space-y-1.5">
        {inherited.map((a) => {
          const offered = a.class_values ?? a.values;
          const on = new Set(editing === a.name ? picked : a.values);
          return (
            <div key={a.name} className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-slate-100">{a.name}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${SOURCE[a.source].cls}`}>
                  {SOURCE[a.source].label}
                  {a.from ? ` · ${a.from}` : ""}
                </span>
                <span className="text-[10px] text-slate-500">{a.kind === "pricing" ? "💰 pricing" : "🔧 technical"}</span>
                {a.required && <span className="rounded bg-sky-500/15 px-1.5 text-[10px] text-sky-300">required</span>}
                <span className="ml-auto flex gap-2 text-[11px]">
                  {editing === a.name ? (
                    <>
                      <button type="button" disabled={!picked.length} onClick={() => narrow(a)} className="font-semibold text-emerald-400 disabled:opacity-40">
                        Save
                      </button>
                      <button type="button" onClick={() => setEditing(null)} className="text-slate-400">
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      {offered.length > 1 && (
                        <button type="button" onClick={() => (setEditing(a.name), setPicked(a.values))} className="text-sky-400 hover:text-sky-300">
                          Narrow…
                        </button>
                      )}
                      {a.source === "override" && (
                        <button type="button" onClick={() => reset(a)} className="text-slate-400 hover:text-amber-300">
                          Reset to class
                        </button>
                      )}
                    </>
                  )}
                </span>
              </div>
              {offered.length > 0 && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1">
                  {a.library_values
                    .filter((v) => !offered.some((o) => o.toLowerCase() === v.toLowerCase()))
                    .map((v) => (
                      <span key={v} title="In the library, not offered by the class" className="rounded border border-dashed border-slate-800 px-1.5 py-0.5 text-[10px] text-slate-600">
                        {v}
                      </span>
                    ))}
                  {offered.map((v) => {
                    const yes = on.has(v);
                    const impact = a.price_impacts.find((i) => i.value.toLowerCase() === v.toLowerCase());
                    return (
                      <button
                        key={v}
                        type="button"
                        disabled={editing !== a.name}
                        onClick={() => setPicked((p) => (p.includes(v) ? p.filter((x) => x !== v) : offered.filter((o) => o === v || p.includes(o))))}
                        title={yes ? "Offered on this product" : "Offered by the class, dropped on this product"}
                        className={`rounded border px-1.5 py-0.5 text-[11px] ${yes ? "border-sky-500/50 bg-sky-500/10 text-sky-200" : "border-slate-700 text-slate-500 line-through"} ${editing === a.name ? "cursor-pointer" : "cursor-default"}`}
                      >
                        {v}
                        {impact && impact.amount ? <span className="ml-1 text-slate-500">{impact.type === "percent" ? `${impact.amount > 0 ? "+" : ""}${impact.amount}%` : `${impact.amount > 0 ? "+" : ""}${impact.amount}`}</span> : null}
                        {a.default === v && <span className="ml-1 text-[9px] text-emerald-400">default</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {own.length > 0 && (
        <p className="text-[11px] text-slate-500">
          Its own attributes (not from a class): {own.map((a) => a.name).join(", ")}.
        </p>
      )}
    </div>
  );
}
