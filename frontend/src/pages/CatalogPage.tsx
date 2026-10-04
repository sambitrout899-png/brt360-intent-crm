import { useEffect, useState } from "react";
import { listCatalog } from "../api/client";
import type { Offering } from "../api/types";

const STATUS_STYLES: Record<string, string> = {
  active: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  draft: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  superseded: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  retired: "bg-red-500/15 text-red-400 border-red-500/30",
};

export default function CatalogPage() {
  const [offerings, setOfferings] = useState<Offering[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      setOfferings(await listCatalog());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load catalog");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const groups = new Map<string, Offering[]>();
  for (const o of offerings) {
    const list = groups.get(o.name) ?? [];
    list.push(o);
    groups.set(o.name, list);
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Bundle & Offer Catalog</h1>
          <p className="mt-1 text-sm text-slate-400">Every version is retained — nothing is ever deleted.</p>
        </div>
        <button
          onClick={load}
          className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400"
        >
          Refresh
        </button>
      </div>

      {error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">{error}</div>}
      {loading && <p className="text-sm text-slate-500">Loading…</p>}
      {!loading && offerings.length === 0 && <p className="text-sm text-slate-500">No bundles yet — launch one from the Intent Command Bar.</p>}

      <div className="space-y-4">
        {[...groups.entries()].map(([name, versions]) => (
          <div key={name} className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
            <h2 className="mb-3 font-bold text-slate-100">{name}</h2>
            <div className="space-y-2">
              {versions
                .sort((a, b) => b.version - a.version)
                .map((o) => (
                  <div key={o.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-slate-950 px-3 py-2 text-sm">
                    <span className="font-mono text-xs text-slate-500">v{o.version}</span>
                    <span
                      className={`rounded-md border px-2 py-0.5 text-xs font-bold ${STATUS_STYLES[o.status] ?? ""}`}
                    >
                      {o.status}
                    </span>
                    <span className="text-slate-300">₹{o.price.toFixed(2)}</span>
                    <span className="text-xs text-slate-500">{o.segment}</span>
                    <span className="truncate text-xs text-slate-500">{o.composition}</span>
                    {o.replacement_offering_id && (
                      <span className="text-xs text-sky-400">→ migrates to #{o.replacement_offering_id}</span>
                    )}
                  </div>
                ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
