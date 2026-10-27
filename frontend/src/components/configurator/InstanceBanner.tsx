import { useEffect, useState } from "react";
import { productInstances, refreshProductInstance, type ProductInstanceInfo } from "../../api/client";

/** In a deal, the product is configured on the copy of its definition frozen
 * when it was picked - this says which copy, and offers the catalog's newer
 * definition when there is one. */
export default function InstanceBanner({ product, quoteId, opportunityId, onRefreshed }: { product: string; quoteId?: number; opportunityId?: number; onRefreshed: () => void }) {
  const [inst, setInst] = useState<ProductInstanceInfo | null>(null);
  const [error, setError] = useState("");
  const ctx: [string, number] | null = quoteId ? ["quote", quoteId] : opportunityId ? ["opportunity", opportunityId] : null;
  const ctxKey = ctx ? ctx.join(":") : "";

  useEffect(() => {
    if (!ctxKey) return;
    const [type, id] = ctxKey.split(":");
    productInstances(type, Number(id))
      .then((list) => setInst(list.find((i) => i.offering_name.toLowerCase() === product.toLowerCase()) ?? null))
      .catch(() => setInst(null));
  }, [ctxKey, product]);

  if (!ctx) return null;
  const where = `${ctx[0] === "quote" ? "Quote" : "Opportunity"} #${ctx[1]}`;
  if (!inst)
    return (
      <p className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-1.5 text-[11px] text-slate-500">
        📌 The current catalog definition of {product} will be frozen into {where} when you save.
      </p>
    );
  return (
    <div className={`flex flex-wrap items-center gap-2 rounded-lg border px-3 py-1.5 text-[11px] ${inst.stale ? "border-amber-500/30 bg-amber-500/5" : "border-slate-800 bg-slate-950/50"}`}>
      <span className="text-slate-300">
        📌 Configuring {where}'s copy of {product}
        {inst.model_version ? ` (model v${inst.model_version})` : ""}, frozen {new Date(inst.taken_at).toLocaleDateString()}
        {inst.inherited_from.length ? ` · inherits from ${inst.inherited_from.join(", ")}` : ""}
      </span>
      {inst.stale ? (
        <>
          <span className="text-amber-300">The catalog has changed since.</span>
          <button
            type="button"
            onClick={() =>
              refreshProductInstance(inst.id)
                .then((r) => (setInst(r), setError(""), onRefreshed()))
                .catch((e) => setError(String((e as Error).message).replace(/^\d+ [^:]*: /, "")))
            }
            className="ml-auto rounded bg-amber-600 px-2 py-0.5 font-semibold text-white hover:bg-amber-500"
          >
            Take the latest definition
          </button>
        </>
      ) : (
        <span className="ml-auto text-slate-500">The catalog's own definition is untouched.</span>
      )}
      {error && <span className="w-full text-red-300">{error}</span>}
    </div>
  );
}
