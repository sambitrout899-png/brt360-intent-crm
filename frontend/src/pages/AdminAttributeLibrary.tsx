import { useCallback, useEffect, useState } from "react";
import { attributeUsage, deleteAttributeDefinition, tidyAttributeDefinition, updateAttributeDefinition, type AttributeUsage } from "../api/client";
import { useWorkspace } from "../workspace";

const clean = (e: unknown) => String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");

/** The shared attribute library and where every attribute is used - products
 * (Product Modeller / Design attributes), classes, atomic products and
 * composition rules. Renaming here renames it in every product. */
export default function AttributeLibrary({ onChanged }: { onChanged: () => void }) {
  const ws = useWorkspace();
  const [rows, setRows] = useState<AttributeUsage[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [edit, setEdit] = useState<{ id: number; name: string; unit: string; values: string } | null>(null);
  const [filter, setFilter] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => attributeUsage().then(setRows).catch((e) => setError(clean(e))), []);
  useEffect(() => {
    load();
  }, [load]);
  const done = (text: string) => {
    setMessage(text);
    setError("");
    load();
    onChanged();
  };

  const q = filter.trim().toLowerCase();
  const shown = rows.filter((r) => !q || `${r.name} ${r.products.map((p) => p.offering_name).join(" ")}`.toLowerCase().includes(q));

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="flex-1 text-base font-bold text-slate-100">Attribute library</h3>
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find an attribute or product…" className="w-64 rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200" />
      </div>
      <p className="mb-3 text-xs text-slate-500">
        One library for every product: the Product Modeller, "Design attributes", imports and atomic products all use these attributes. Each product chooses its own values,
        unit and price impacts; the library keeps the list every product can pick from.
      </p>
      {message && <p className="mb-2 text-xs text-emerald-400">{message}</p>}
      {error && <p className="mb-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
      <div className="space-y-1.5">
        {shown.map((r) => (
          <div key={r.id} className="rounded-lg border border-slate-700 bg-slate-900/60 text-sm">
            <button type="button" onClick={() => setOpen(open === r.id ? null : r.id)} className="flex w-full flex-wrap items-center gap-3 px-4 py-2.5 text-left">
              <span className="font-semibold text-slate-200">{r.name}</span>
              <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{r.data_type}</span>
              {r.unit && <span className="text-xs text-slate-500">{r.unit}</span>}
              <span className="text-xs text-slate-400">
                {r.products.length ? `used by ${r.products.length} product${r.products.length > 1 ? "s" : ""}` : "not used by any product"}
                {r.classes.length ? ` · ${r.classes.length} class(es)` : ""}
                {r.spec_values ? ` · ${r.spec_values} atomic value(s)` : ""}
              </span>
              {r.unused_values.length > 0 && <span className="rounded bg-amber-500/15 px-1.5 text-[10px] text-amber-300">{r.unused_values.length} unused values</span>}
              <span className="ml-auto text-xs text-slate-500">{open === r.id ? "▲" : "▼"}</span>
            </button>
            {open === r.id && (
              <div className="space-y-2 border-t border-slate-800 px-4 py-3 text-xs">
                {r.products.map((p) => (
                  <div key={p.offering_name} className="flex flex-wrap items-center gap-2">
                    <button type="button" onClick={() => ws.openProductModeller?.(p.offering_name)} className="font-semibold text-sky-300 hover:text-sky-200">
                      {p.offering_name}
                    </button>
                    <span className="text-slate-500">{p.kind === "pricing" ? "💰 pricing" : "🔧 technical"}{p.required ? " · mandatory" : ""}{p.unit ? ` · ${p.unit}` : ""}</span>
                    <span className="text-slate-400">{p.values.join(", ") || "free value"}</span>
                    {!p.own_values && <span className="text-slate-600">(the library's list)</span>}
                  </div>
                ))}
                {r.classes.map((c) => (
                  <p key={c} className="text-slate-400">
                    Class {c} (inherited by its products)
                  </p>
                ))}
                <p className="text-slate-500">
                  Library values: {r.values.join(", ") || "none"}
                  {r.unused_values.length > 0 && <span className="text-amber-300"> · unused: {r.unused_values.join(", ")}</span>}
                </p>
                {edit?.id === r.id ? (
                  <div className="flex flex-wrap gap-2">
                    <input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} className="w-44 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" />
                    <input value={edit.unit} onChange={(e) => setEdit({ ...edit, unit: e.target.value })} placeholder="unit" className="w-24 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" />
                    <input value={edit.values} onChange={(e) => setEdit({ ...edit, values: e.target.value })} placeholder="values, comma-separated" className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" />
                    <button
                      type="button"
                      onClick={() =>
                        updateAttributeDefinition(r.id, { name: edit.name, unit: edit.unit || null, values: edit.values.split(",").map((v) => v.trim()).filter(Boolean) })
                          .then(() => (setEdit(null), done(`Saved ${edit.name} - every product using it follows`)))
                          .catch((e) => setError(clean(e)))
                      }
                      className="rounded-md bg-sky-600 px-3 py-1 font-semibold text-white"
                    >
                      Save
                    </button>
                    <button type="button" onClick={() => setEdit(null)} className="text-slate-400">
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={() => setEdit({ id: r.id, name: r.name, unit: r.unit ?? "", values: r.values.join(", ") })} className="rounded-md border border-slate-700 px-2.5 py-1 text-slate-300 hover:border-sky-500">
                      Edit
                    </button>
                    {r.unused_values.length > 0 && (
                      <button type="button" onClick={() => tidyAttributeDefinition(r.id).then((t) => done(`Removed unused values: ${t.removed.join(", ")}`)).catch((e) => setError(clean(e)))} className="rounded-md border border-slate-700 px-2.5 py-1 text-slate-300 hover:border-amber-500">
                        Remove unused values
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => deleteAttributeDefinition(r.id).then(() => done(`Deleted ${r.name}`)).catch((e) => setError(clean(e)))}
                      className="rounded-md border border-slate-700 px-2.5 py-1 text-slate-300 hover:border-red-500 hover:text-red-400"
                      title={r.products.length || r.classes.length || r.spec_values ? "Still in use - remove it from those products first" : ""}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
