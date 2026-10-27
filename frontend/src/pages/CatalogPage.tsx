import { useCallback, useEffect, useState } from "react";
import { catalogProduct, catalogProducts, type ProductPicture } from "../api/client";
import ProductPictureView from "../components/products/ProductPictureView";
import { MODEL_BADGE, STATUS_BADGE } from "../components/products/productBadges";
import { useIntentRefresh, useWorkspace } from "../workspace";

/** The product catalog: every product with its price, who may buy it, how it
 * is configured and its versions - the same picture the Product Modeller and
 * the product intents work on. */
export default function CatalogPage() {
  const ws = useWorkspace();
  const [products, setProducts] = useState<ProductPicture[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProductPicture | null>(null);
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState("on-sale");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    catalogProducts()
      .then((p) => (setProducts(p), setError("")))
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the catalog"))
      .finally(() => setLoading(false));
  }, []);
  const loadDetail = useCallback((name: string) => {
    catalogProduct(name).then(setDetail).catch(() => setDetail(null));
  }, []);

  useEffect(load, [load]);
  useIntentRefresh(() => {
    load();
    if (open) loadDetail(open);
  });

  const q = filter.trim().toLowerCase();
  const shown = products.filter(
    (p) =>
      (!q || `${p.name} ${p.segment} ${p.composition}`.toLowerCase().includes(q)) &&
      (status === "all" || (status === "on-sale" ? p.status === "active" : p.status === status))
  );

  return (
    <div className="page space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-slate-100">Product Catalog</h1>
          <p className="mt-1 text-sm text-slate-400">Every product, its prices, who may buy it and how it is configured. Nothing is ever deleted - every version is kept.</p>
        </div>
        {ws.canRun("INT-01") ? <button type="button" onClick={() => ws.runIntent("INT-01")} className="rounded-lg bg-sky-600 px-3 py-2 text-xs font-semibold text-white">
          🚀 Launch a product
        </button> : null}
        {ws.openProductModeller && (
          <button type="button" onClick={() => ws.openProductModeller?.()} className="rounded-lg border border-slate-600 px-3 py-2 text-xs text-slate-200">
            Product Modeller
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find a product…" className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100" />
        {[["on-sale", "On sale"], ["draft", "Drafts"], ["retired", "Retired"], ["all", "All"]].map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setStatus(id)}
            className={`rounded-lg px-3 py-2 text-xs font-semibold ${status === id ? "bg-sky-500/20 text-sky-300" : "text-slate-400 hover:bg-slate-800"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">{error}</div>}
      {loading && <p className="text-sm text-slate-500">Loading…</p>}
      {!loading && shown.length === 0 && <p className="text-sm text-slate-500">No products here yet - launch one, or import your product sheet in the Product Modeller.</p>}

      <div className="space-y-2">
        {shown.map((p) => (
          <div key={p.name} className="rounded-xl border border-slate-800 bg-slate-900/50">
            <button
              type="button"
              onClick={() => {
                const next = open === p.name ? null : p.name;
                setOpen(next);
                setDetail(null);
                if (next) loadDetail(next);
              }}
              className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left"
            >
              <span className="font-semibold text-slate-100">{p.name}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_BADGE[p.status] ?? ""}`}>{p.status}</span>
              <span className="text-xs text-slate-400">{p.segment}</span>
              <span className="text-xs text-slate-300">{p.price.toLocaleString()} / month</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${MODEL_BADGE[p.model.status]}`}>
                {p.model.status === "not_modelled" ? "no options" : `${p.model.attributes} attributes · ${p.model.children} child products`}
                {p.model.released_version ? ` · model v${p.model.released_version}` : ""}
                {p.model.changed ? " · unreleased changes" : ""}
              </span>
              <span className="ml-auto text-xs text-slate-500">{open === p.name ? "▲" : "▼"}</span>
            </button>
            {open === p.name && (
              <div className="border-t border-slate-800 px-4 py-4">
                {detail ? <ProductPictureView p={detail} onChanged={() => (load(), loadDetail(p.name))} /> : <p className="text-xs text-slate-500">Loading…</p>}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
