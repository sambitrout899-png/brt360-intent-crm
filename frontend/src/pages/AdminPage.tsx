import { useEffect, useState } from "react";
import {
  createApprovalChain,
  createSlaPolicy,
  deactivateApprovalChain,
  deactivateSlaPolicy,
  deleteApprovalChain,
  deleteSlaPolicy,
  listApprovalChains,
  listSlaClocks,
  listSlaPolicies,
  processDueSlaClocks,
} from "../api/client";
import { SLA_BREACH_ACTIONS } from "../api/types";
import type { ApprovalChainTemplate, SlaClock, SlaPolicy } from "../api/types";

type Section = "approvals" | "sla";

function emptyChainForm() {
  return { name: "", segment: "", minPct: "0", maxPct: "100", tiers: [""] };
}

function ApprovalChainsSection() {
  const [templates, setTemplates] = useState<ApprovalChainTemplate[]>([]);
  const [form, setForm] = useState(emptyChainForm());
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
      setForm(emptyChainForm());
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
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Tenant-configurable discount approval chains. INT-06 ("Approve this quote") resolves one of these by discount
        band and product segment, then walks its tiers in order - one decision per call.
      </p>

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
        <h3 className="mb-4 text-base font-bold text-slate-100">New Approval Chain</h3>
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

const CLOCK_STATUS_STYLES: Record<string, string> = {
  running: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  completed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  breached: "bg-red-500/15 text-red-400 border-red-500/30",
  cancelled: "bg-slate-500/15 text-slate-400 border-slate-500/30",
};

function emptyPolicyForm() {
  return { clockType: "", durationHours: "24", onBreachAction: SLA_BREACH_ACTIONS[0] as string };
}

function SlaPoliciesSection() {
  const [policies, setPolicies] = useState<SlaPolicy[]>([]);
  const [clocks, setClocks] = useState<SlaClock[]>([]);
  const [form, setForm] = useState(emptyPolicyForm());
  const [error, setError] = useState<string | null>(null);
  const [sweepMessage, setSweepMessage] = useState<string | null>(null);

  function load() {
    listSlaPolicies().then(setPolicies);
    listSlaClocks().then(setClocks);
  }

  useEffect(load, []);

  async function submit() {
    setError(null);
    if (!form.clockType.trim()) {
      setError("Clock type is required.");
      return;
    }
    try {
      await createSlaPolicy({
        clock_type: form.clockType.trim(),
        duration_hours: Number(form.durationHours),
        on_breach_action: form.onBreachAction,
      });
      setForm(emptyPolicyForm());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create policy.");
    }
  }

  async function deactivate(id: number) {
    await deactivateSlaPolicy(id);
    load();
  }

  async function remove(id: number) {
    await deleteSlaPolicy(id);
    load();
  }

  async function runSweep() {
    setSweepMessage(null);
    const result = await processDueSlaClocks();
    setSweepMessage(`Checked ${result.checked} clock(s), breached ${result.breached.length}.`);
    load();
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        SLA clocks run against any entity (a quote, order, ticket, ...) and fire a configured action if nobody acts
        before the deadline. Wiring these into specific intents (e.g. a retention window before cancellation) is
        upcoming Phase 2 work - today this is the generic engine: configure a policy, and whatever starts a clock of
        that type will be governed by it. Only "escalate" has a real handler right now; other actions fall back to
        escalate and say so until a handler is registered for that clock type.
      </p>

      <div className="flex items-center justify-between">
        <h3 className="text-base font-bold text-slate-100">Policies</h3>
        <button onClick={runSweep} className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
          Run sweep now
        </button>
      </div>
      {sweepMessage && <p className="text-xs text-slate-400">{sweepMessage}</p>}

      <div className="space-y-2">
        {policies.map((p) => (
          <div key={p.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${p.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="font-semibold text-slate-200">{p.clock_type}</span>
            <span className="text-xs text-slate-500">{p.duration_hours}h</span>
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{p.on_breach_action}</span>
            {!p.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">inactive</span>}
            <div className="ml-auto flex gap-2">
              {p.is_active && (
                <button onClick={() => deactivate(p.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-amber-500 hover:text-amber-400">
                  Deactivate
                </button>
              )}
              <button onClick={() => remove(p.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400">
                Delete
              </button>
            </div>
          </div>
        ))}
        {policies.length === 0 && <p className="text-sm text-slate-500">No SLA policies configured yet.</p>}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-4 text-base font-bold text-slate-100">New SLA Policy</h3>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="text-xs text-slate-400">
            Clock type
            <input
              value={form.clockType}
              onChange={(e) => setForm((f) => ({ ...f, clockType: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
              placeholder="e.g. kyc_verification"
            />
          </label>
          <label className="text-xs text-slate-400">
            Duration (hours)
            <input
              type="number"
              value={form.durationHours}
              onChange={(e) => setForm((f) => ({ ...f, durationHours: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
            />
          </label>
          <label className="text-xs text-slate-400">
            On breach
            <select
              value={form.onBreachAction}
              onChange={(e) => setForm((f) => ({ ...f, onBreachAction: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
            >
              {SLA_BREACH_ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
        <button onClick={submit} className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Create SLA Policy
        </button>
      </div>

      <div>
        <h3 className="mb-3 text-base font-bold text-slate-100">Clocks</h3>
        <div className="space-y-2">
          {clocks.map((c) => (
            <div key={c.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
              <span className="font-mono text-xs text-slate-500">#{c.id}</span>
              <span className="text-slate-200">{c.clock_type}</span>
              <span className="text-xs text-slate-500">{c.entity_type} #{c.entity_id}</span>
              <span className="text-xs text-slate-500">deadline {new Date(c.deadline).toLocaleString()}</span>
              <span className={`rounded-md border px-2 py-0.5 text-xs font-bold ${CLOCK_STATUS_STYLES[c.status]}`}>{c.status}</span>
              {c.detail && <span className="text-xs text-slate-500">{c.detail}</span>}
            </div>
          ))}
          {clocks.length === 0 && <p className="text-sm text-slate-500">No clocks have been started yet.</p>}
        </div>
      </div>
    </div>
  );
}

export default function AdminPage() {
  const [section, setSection] = useState<Section>("approvals");

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Admin</h1>
        <p className="mt-1 text-sm text-slate-400">Tenant configuration - reference data admins edit without touching code.</p>
      </div>

      <div className="flex gap-1 border-b border-slate-800">
        {([
          ["approvals", "Approval Chains"],
          ["sla", "SLA Policies"],
        ] as [Section, string][]).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setSection(id)}
            className={`rounded-t-lg px-4 py-2 text-sm font-semibold transition ${
              section === id ? "border-b-2 border-sky-500 text-sky-400" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {section === "approvals" ? <ApprovalChainsSection /> : <SlaPoliciesSection />}
    </div>
  );
}
