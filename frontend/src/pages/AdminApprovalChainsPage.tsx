import { useEffect, useState } from "react";
import { createApprovalChain, deactivateApprovalChain, deleteApprovalChain, listApprovalChains } from "../api/client";
import type { ApprovalChainTemplate } from "../api/types";

function emptyForm() {
  return { name: "", segment: "", minPct: "0", maxPct: "100", tiers: [""] };
}

export default function AdminApprovalChainsPage() {
  const [templates, setTemplates] = useState<ApprovalChainTemplate[]>([]);
  const [form, setForm] = useState(emptyForm());
  const [error, setError] = useState<string | null>(null);

  function load() {
    listApprovalChains().then(setTemplates);
  }

  useEffect(load, []);

  function updateTier(index: number, value: string) {
    setForm((f) => ({ ...f, tiers: f.tiers.map((t, i) => (i === index ? value : t)) }));
  }

  function addTier() {
    setForm((f) => ({ ...f, tiers: [...f.tiers, ""] }));
  }

  function removeTier(index: number) {
    setForm((f) => ({ ...f, tiers: f.tiers.filter((_, i) => i !== index) }));
  }

  async function submit() {
    setError(null);
    const tiers = form.tiers.map((t) => t.trim()).filter(Boolean);
    if (!form.name.trim() || tiers.length === 0) {
      setError("Name and at least one tier are required.");
      return;
    }
    try {
      await createApprovalChain({
        name: form.name.trim(),
        segment: form.segment.trim() || null,
        min_discount_pct: Number(form.minPct),
        max_discount_pct: Number(form.maxPct),
        tiers: tiers.map((approver_tier) => ({ approver_tier })),
      });
      setForm(emptyForm());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create template.");
    }
  }

  async function deactivate(id: number) {
    await deactivateApprovalChain(id);
    load();
  }

  async function remove(id: number) {
    try {
      await deleteApprovalChain(id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete template - it may already be in use on a quote.");
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Approval Chains</h1>
        <p className="mt-1 text-sm text-slate-400">
          Tenant-configurable discount approval chains. INT-06 ("Approve this quote") resolves one of these by discount
          band and product segment, then walks its tiers in order - one decision per call.
        </p>
      </div>

      <div className="space-y-2">
        {templates.map((t) => (
          <div key={t.id} className={`rounded-lg border px-4 py-3 text-sm ${t.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-semibold text-slate-200">{t.name}</span>
              <span className="text-xs text-slate-500">
                {t.min_discount_pct}%–{t.max_discount_pct}% discount
              </span>
              <span className="text-xs text-slate-500">segment: {t.segment ?? "any"}</span>
              {!t.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">inactive</span>}
              <div className="ml-auto flex gap-2">
                {t.is_active && (
                  <button onClick={() => deactivate(t.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-amber-500 hover:text-amber-400">
                    Deactivate
                  </button>
                )}
                <button onClick={() => remove(t.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400">
                  Delete
                </button>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
              {t.tiers.map((tier, i) => (
                <span key={tier.sequence} className="flex items-center gap-2">
                  <span className="rounded-md bg-slate-800 px-2 py-0.5">{tier.sequence}. {tier.approver_tier}</span>
                  {i < t.tiers.length - 1 && <span className="text-slate-600">→</span>}
                </span>
              ))}
            </div>
          </div>
        ))}
        {templates.length === 0 && <p className="text-sm text-slate-500">No approval chains configured yet.</p>}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h2 className="mb-4 text-lg font-bold text-slate-100">New Approval Chain</h2>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-xs text-slate-400">
            Name
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
              placeholder="e.g. Wholesale Carrier Escalation"
            />
          </label>
          <label className="text-xs text-slate-400">
            Segment (blank = any)
            <input
              value={form.segment}
              onChange={(e) => setForm((f) => ({ ...f, segment: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
              placeholder="e.g. B2B-Wholesale"
            />
          </label>
          <label className="text-xs text-slate-400">
            Min discount %
            <input
              type="number"
              value={form.minPct}
              onChange={(e) => setForm((f) => ({ ...f, minPct: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
            />
          </label>
          <label className="text-xs text-slate-400">
            Max discount %
            <input
              type="number"
              value={form.maxPct}
              onChange={(e) => setForm((f) => ({ ...f, maxPct: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
            />
          </label>
        </div>

        <div className="mt-4">
          <span className="text-xs text-slate-400">Tiers, in approval order</span>
          <div className="mt-1 space-y-2">
            {form.tiers.map((tier, i) => (
              <div key={i} className="flex gap-2">
                <span className="flex w-6 items-center justify-center text-xs text-slate-500">{i + 1}</span>
                <input
                  value={tier}
                  onChange={(e) => updateTier(i, e.target.value)}
                  className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
                  placeholder="e.g. Regional Sales Head"
                />
                {form.tiers.length > 1 && (
                  <button onClick={() => removeTier(i)} className="rounded-md border border-slate-700 px-2.5 text-xs text-slate-400 hover:border-red-500 hover:text-red-400">
                    ✕
                  </button>
                )}
              </div>
            ))}
          </div>
          <button onClick={addTier} className="mt-2 rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
            + Add tier
          </button>
        </div>

        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}

        <button onClick={submit} className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Create Approval Chain
        </button>
      </div>
    </div>
  );
}
