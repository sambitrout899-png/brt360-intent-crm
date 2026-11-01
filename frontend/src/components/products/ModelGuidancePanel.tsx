import { useState } from "react";
import { applyModelGuidance, modelGuidance, type GuidanceProposal, type ModelGuidance, type ProductTree } from "../../api/client";

const clean = (e: unknown) => String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");

const GROUPS: { key: GuidanceProposal["group"]; title: string; blurb: string; tone: string }[] = [
  { key: "pricing", title: "💰 Pricing", blurb: "Chosen by the sales rep - changes the price.", tone: "border-emerald-500/30" },
  { key: "service", title: "🔧 Service", blurb: "Service-level choices known while selling (no price).", tone: "border-amber-500/30" },
  { key: "network", title: "🛠 Set at installation", blurb: "Network-level - not asked on the quote or at order approval; the installation engineer sets them, prefilled from Inventory.", tone: "border-sky-500/30" },
];

const ACTION: Record<GuidanceProposal["action"], { label: string; cls: string }> = {
  add: { label: "new", cls: "bg-emerald-500/15 text-emerald-300" },
  update: { label: "change", cls: "bg-amber-500/15 text-amber-300" },
  move_to_installation: { label: "to installation", cls: "bg-sky-500/15 text-sky-300" },
  remove: { label: "remove", cls: "bg-red-500/15 text-red-300" },
  keep: { label: "keep", cls: "bg-slate-800 text-slate-400" },
};

/** The Product Modeller's ✨ Guidance: the AI engine proposes this product type's pricing and service attributes,
 *  moves network-level ones to installation, and the administrator keeps what they want. */
export default function ModelGuidancePanel({ product, onApplied }: { product: string; onApplied: (tree: ProductTree, message: string) => void }) {
  const [plan, setPlan] = useState<ModelGuidance | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<"ask" | "apply" | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ applied: string[]; errors: string[] } | null>(null);

  const ask = async () => {
    setBusy("ask");
    setError("");
    setResult(null);
    try {
      const g = await modelGuidance(product);
      setPlan(g);
      setPicked(new Set(g.proposals.filter((p) => p.selected && p.op).map((p) => p.id)));
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(null);
    }
  };

  const apply = async () => {
    if (!plan) return;
    setBusy("apply");
    setError("");
    try {
      const ops = plan.proposals.filter((p) => picked.has(p.id) && p.op).map((p) => p.op!);
      const r = await applyModelGuidance(product, ops);
      setResult(r);
      setPlan(null);
      onApplied(r.tree, `Guidance applied - ${r.applied.length} change${r.applied.length === 1 ? "" : "s"}${r.errors.length ? `, ${r.errors.length} not applied` : ""}`);
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(null);
    }
  };

  const toggle = (id: number) => setPicked((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  return (
    <div className="space-y-3 rounded-xl border border-fuchsia-500/30 bg-fuchsia-500/5 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-100">✨ Model guidance</p>
          <p className="text-xs text-slate-400">
            The AI engine works out what type of product {product} is and proposes only the pricing and service attributes that type needs. Network-level
            attributes are left to installation. Nothing changes until you apply.
          </p>
        </div>
        <button type="button" disabled={busy !== null} onClick={ask} className="rounded-lg bg-fuchsia-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
          {busy === "ask" ? "Thinking…" : plan ? "Ask again" : "Get guidance"}
        </button>
      </div>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
      {result && result.errors.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200">
          {result.errors.map((e) => <p key={e}>Not applied - {e}</p>)}
        </div>
      )}

      {plan && (
        <div className="space-y-3">
          <div className="rounded-lg bg-slate-950/60 px-3 py-2 text-xs">
            {plan.product_type && <p className="font-semibold text-fuchsia-200">{plan.product_type}</p>}
            {plan.summary && <p className="text-slate-300">{plan.summary}</p>}
            <p className={`mt-1 ${plan.live.live ? "text-amber-300" : "text-slate-500"}`}>{plan.live.live ? "● Live model - " : ""}{plan.live.note}</p>
            <p className="mt-1 text-[10px] text-slate-600">{plan.model}</p>
          </div>
          {plan.notes.length > 0 && (
            <ul className="space-y-0.5 text-[11px] text-slate-400">
              {plan.notes.map((n) => <li key={n}>• {n}</li>)}
            </ul>
          )}
          {GROUPS.map((g) => {
            const rows = plan.proposals.filter((p) => p.group === g.key);
            if (!rows.length) return null;
            return (
              <div key={g.key} className={`rounded-lg border ${g.tone} p-2`}>
                <p className="text-xs font-semibold text-slate-200">{g.title}</p>
                <p className="mb-1 text-[11px] text-slate-500">{g.blurb}</p>
                {rows.map((p) => (
                  <label key={p.id} className={`flex items-start gap-2 rounded-md px-2 py-1.5 text-xs ${p.op ? "cursor-pointer hover:bg-slate-800/50" : "opacity-60"}`}>
                    <input type="checkbox" className="mt-0.5" disabled={!p.op} checked={picked.has(p.id)} onChange={() => toggle(p.id)} />
                    <span className="min-w-0 flex-1">
                      <span className="font-semibold text-slate-100">{p.name}</span>{" "}
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${ACTION[p.action].cls}`}>{ACTION[p.action].label}</span>
                      <span className="block text-slate-300">{p.change}</span>
                      {p.current && p.action !== "keep" && <span className="block text-[11px] text-slate-500">now: {p.current}</span>}
                      {p.reason && <span className="block text-[11px] italic text-slate-500">{p.reason}</span>}
                    </span>
                  </label>
                ))}
              </div>
            );
          })}
          <div className="flex items-center gap-2">
            <button type="button" disabled={busy !== null || picked.size === 0} onClick={apply} className="rounded-lg bg-fuchsia-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
              {busy === "apply" ? "Applying…" : `Apply ${picked.size} to the workspace`}
            </button>
            <button type="button" onClick={() => setPlan(null)} className="text-xs text-slate-400">
              Dismiss
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
