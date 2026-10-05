import { useEffect, useState } from "react";
import {
  createApprovalChain,
  createAttributeConstraintRule,
  createAttributeDefinition,
  createIdentityProvider,
  createSlaPolicy,
  deactivateApprovalChain,
  deactivateAttributeConstraintRule,
  deactivateIdentityProvider,
  deactivateSlaPolicy,
  deleteApprovalChain,
  deleteAttributeConstraintRule,
  deleteAttributeDefinition,
  deleteIdentityProvider,
  deleteSlaPolicy,
  listApprovalChains,
  listAttributeConstraintRules,
  listAttributeDefinitions,
  listAttributeValues,
  listIdentityProviders,
  listProductSpecifications,
  listSlaClocks,
  listSlaPolicies,
  processDueSlaClocks,
  setAttributeValue,
} from "../api/client";
import { ATTRIBUTE_DATA_TYPES, CONSTRAINT_OPERATORS, CONSTRAINT_RULE_TYPES, SLA_BREACH_ACTIONS } from "../api/types";
import type {
  ApprovalChainTemplate,
  AttributeConstraintRule,
  IdentityProvider,
  ProductAttributeDefinition,
  ProductAttributeValue,
  ProductSpecification,
  SlaClock,
  SlaPolicy,
} from "../api/types";

type Section = "approvals" | "sla" | "attributes" | "identity";

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

function emptyDefinitionForm() {
  return { name: "", dataType: ATTRIBUTE_DATA_TYPES[0] as string, unit: "", allowedValues: "" };
}

function ProductAttributesSection() {
  const [specs, setSpecs] = useState<ProductSpecification[]>([]);
  const [definitions, setDefinitions] = useState<ProductAttributeDefinition[]>([]);
  const [values, setValues] = useState<ProductAttributeValue[]>([]);
  const [rules, setRules] = useState<AttributeConstraintRule[]>([]);

  const [defForm, setDefForm] = useState(emptyDefinitionForm());
  const [valueForm, setValueForm] = useState({ specId: 0, defId: 0, value: "" });
  const [ruleForm, setRuleForm] = useState({ ifSpecId: 0, ifDefId: 0, operator: "eq", threshold: "", ruleType: "requires", thenSpecId: 0 });
  const [error, setError] = useState<string | null>(null);

  function load() {
    listProductSpecifications().then(setSpecs);
    listAttributeDefinitions().then(setDefinitions);
    listAttributeValues().then(setValues);
    listAttributeConstraintRules().then(setRules);
  }

  useEffect(load, []);

  useEffect(() => {
    if (specs.length && !valueForm.specId) {
      setValueForm((f) => ({ ...f, specId: specs[0].id }));
      setRuleForm((f) => ({ ...f, ifSpecId: specs[0].id, thenSpecId: specs[0].id }));
    }
    if (definitions.length && !valueForm.defId) {
      setValueForm((f) => ({ ...f, defId: definitions[0].id }));
      setRuleForm((f) => ({ ...f, ifDefId: definitions[0].id }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specs, definitions]);

  async function submitDefinition() {
    setError(null);
    if (!defForm.name.trim()) {
      setError("Attribute name is required.");
      return;
    }
    try {
      await createAttributeDefinition({
        name: defForm.name.trim(),
        data_type: defForm.dataType,
        unit: defForm.unit.trim() || null,
        allowed_values: defForm.allowedValues.trim() || null,
      });
      setDefForm(emptyDefinitionForm());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create attribute.");
    }
  }

  async function removeDefinition(id: number) {
    await deleteAttributeDefinition(id);
    load();
  }

  async function submitValue() {
    setError(null);
    if (!valueForm.value.trim() || !valueForm.specId || !valueForm.defId) {
      setError("Product, attribute and value are all required.");
      return;
    }
    try {
      await setAttributeValue({
        product_specification_id: valueForm.specId,
        attribute_definition_id: valueForm.defId,
        value: valueForm.value.trim(),
      });
      setValueForm((f) => ({ ...f, value: "" }));
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to set attribute value.");
    }
  }

  async function submitRule() {
    setError(null);
    if (!ruleForm.threshold.trim()) {
      setError("Threshold is required.");
      return;
    }
    try {
      await createAttributeConstraintRule({
        if_product_spec_id: ruleForm.ifSpecId,
        if_attribute_id: ruleForm.ifDefId,
        operator: ruleForm.operator,
        threshold: ruleForm.threshold.trim(),
        rule_type: ruleForm.ruleType,
        then_product_spec_id: ruleForm.thenSpecId,
      });
      setRuleForm((f) => ({ ...f, threshold: "" }));
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create constraint rule.");
    }
  }

  async function deactivateRule(id: number) {
    await deactivateAttributeConstraintRule(id);
    load();
  }

  async function removeRule(id: number) {
    await deleteAttributeConstraintRule(id);
    load();
  }

  const specName = (id: number) => specs.find((s) => s.id === id)?.name ?? `#${id}`;

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Typed attributes on atomic products (e.g. Fiber1000's Download Speed), and requires/excludes rules keyed off
        those values - a second, finer-grained check that runs alongside the existing product-line compatibility
        rules every time a bundle composition is validated (INT-01/02/05).
      </p>

      <div>
        <h3 className="mb-3 text-base font-bold text-slate-100">Attribute Definitions</h3>
        <div className="space-y-2">
          {definitions.map((d) => (
            <div key={d.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
              <span className="font-semibold text-slate-200">{d.name}</span>
              <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{d.data_type}</span>
              {d.unit && <span className="text-xs text-slate-500">{d.unit}</span>}
              {d.allowed_values && <span className="text-xs text-slate-500">values: {d.allowed_values}</span>}
              <button onClick={() => removeDefinition(d.id)} className="ml-auto rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400">
                Delete
              </button>
            </div>
          ))}
          {definitions.length === 0 && <p className="text-sm text-slate-500">No attributes defined yet.</p>}
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-2 rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <label className="text-xs text-slate-400">
            Name
            <input value={defForm.name} onChange={(e) => setDefForm((f) => ({ ...f, name: e.target.value }))} className="mt-1 w-40 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="Download Speed" />
          </label>
          <label className="text-xs text-slate-400">
            Type
            <select value={defForm.dataType} onChange={(e) => setDefForm((f) => ({ ...f, dataType: e.target.value }))} className="mt-1 w-28 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
              {ATTRIBUTE_DATA_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            Unit
            <input value={defForm.unit} onChange={(e) => setDefForm((f) => ({ ...f, unit: e.target.value }))} className="mt-1 w-24 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="Mbps" />
          </label>
          <label className="text-xs text-slate-400">
            Allowed values (enum)
            <input value={defForm.allowedValues} onChange={(e) => setDefForm((f) => ({ ...f, allowedValues: e.target.value }))} className="mt-1 w-40 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="a,b,c" />
          </label>
          <button onClick={submitDefinition} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
            Add
          </button>
        </div>
      </div>

      <div>
        <h3 className="mb-3 text-base font-bold text-slate-100">Attribute Values</h3>
        <div className="space-y-2">
          {values.map((v) => (
            <div key={v.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
              <span className="text-slate-200">{specName(v.product_specification_id)}</span>
              <span className="text-xs text-slate-500">{v.attribute_name} =</span>
              <span className="font-semibold text-slate-200">{v.value}</span>
            </div>
          ))}
          {values.length === 0 && <p className="text-sm text-slate-500">No attribute values set yet.</p>}
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-2 rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <label className="text-xs text-slate-400">
            Product
            <select value={valueForm.specId} onChange={(e) => setValueForm((f) => ({ ...f, specId: Number(e.target.value) }))} className="mt-1 w-40 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
              {specs.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            Attribute
            <select value={valueForm.defId} onChange={(e) => setValueForm((f) => ({ ...f, defId: Number(e.target.value) }))} className="mt-1 w-40 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
              {definitions.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            Value
            <input value={valueForm.value} onChange={(e) => setValueForm((f) => ({ ...f, value: e.target.value }))} className="mt-1 w-28 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="1000" />
          </label>
          <button onClick={submitValue} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
            Set
          </button>
        </div>
      </div>

      <div>
        <h3 className="mb-3 text-base font-bold text-slate-100">Constraint Rules</h3>
        <div className="space-y-2">
          {rules.map((r) => (
            <div key={r.id} className={`flex flex-wrap items-center gap-2 rounded-lg border px-4 py-3 text-sm ${r.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
              <span className="text-slate-200">{r.if_product_name}'s {r.if_attribute_name}</span>
              <span className="text-xs text-slate-500">{r.operator} {r.threshold}</span>
              <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-bold text-slate-300">{r.rule_type}</span>
              <span className="text-slate-200">{r.then_product_name}</span>
              {!r.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">inactive</span>}
              <div className="ml-auto flex gap-2">
                {r.is_active && (
                  <button onClick={() => deactivateRule(r.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-amber-500 hover:text-amber-400">
                    Deactivate
                  </button>
                )}
                <button onClick={() => removeRule(r.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400">
                  Delete
                </button>
              </div>
            </div>
          ))}
          {rules.length === 0 && <p className="text-sm text-slate-500">No constraint rules configured yet.</p>}
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-2 rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <label className="text-xs text-slate-400">
            If product
            <select value={ruleForm.ifSpecId} onChange={(e) => setRuleForm((f) => ({ ...f, ifSpecId: Number(e.target.value) }))} className="mt-1 w-32 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
              {specs.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            attribute
            <select value={ruleForm.ifDefId} onChange={(e) => setRuleForm((f) => ({ ...f, ifDefId: Number(e.target.value) }))} className="mt-1 w-32 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
              {definitions.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            op
            <select value={ruleForm.operator} onChange={(e) => setRuleForm((f) => ({ ...f, operator: e.target.value }))} className="mt-1 w-20 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
              {CONSTRAINT_OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            threshold
            <input value={ruleForm.threshold} onChange={(e) => setRuleForm((f) => ({ ...f, threshold: e.target.value }))} className="mt-1 w-24 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="500" />
          </label>
          <label className="text-xs text-slate-400">
            then
            <select value={ruleForm.ruleType} onChange={(e) => setRuleForm((f) => ({ ...f, ruleType: e.target.value }))} className="mt-1 w-24 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
              {CONSTRAINT_RULE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            product
            <select value={ruleForm.thenSpecId} onChange={(e) => setRuleForm((f) => ({ ...f, thenSpecId: Number(e.target.value) }))} className="mt-1 w-32 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
              {specs.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <button onClick={submitRule} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
            Add Rule
          </button>
        </div>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  );
}

function IdentityProvidersSection() {
  const [providers, setProviders] = useState<IdentityProvider[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  function load() {
    listIdentityProviders().then(setProviders);
  }

  useEffect(load, []);

  async function submit() {
    setError(null);
    if (!name.trim()) {
      setError("Provider name is required.");
      return;
    }
    try {
      await createIdentityProvider(name.trim());
      setName("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create provider.");
    }
  }

  async function deactivate(id: number) {
    await deactivateIdentityProvider(id);
    load();
  }

  async function remove(id: number) {
    await deleteIdentityProvider(id);
    load();
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        B2C process pack: pluggable identity-verification providers for INT-13 ("Verify customer identity"), blocked
        outright for tenants configured B2B-only. "Yakeen", "WATHQ", "SIMAH" and "Abshir" are seeded by default,
        matching the providers named in the B2C BRD docs - add, deactivate or remove as needed.
      </p>

      <div className="space-y-2">
        {providers.map((p) => (
          <div key={p.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${p.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="font-semibold text-slate-200">{p.name}</span>
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
        {providers.length === 0 && <p className="text-sm text-slate-500">No identity providers configured yet.</p>}
      </div>

      <div className="flex flex-wrap items-end gap-2 rounded-xl border border-slate-700 bg-slate-900/60 p-4">
        <label className="text-xs text-slate-400">
          Provider name
          <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-48 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="e.g. Nafath" />
        </label>
        <button onClick={submit} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Add Provider
        </button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}
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
          ["attributes", "Product Attributes"],
          ["identity", "Identity Providers"],
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

      {section === "approvals" && <ApprovalChainsSection />}
      {section === "sla" && <SlaPoliciesSection />}
      {section === "attributes" && <ProductAttributesSection />}
      {section === "identity" && <IdentityProvidersSection />}
    </div>
  );
}
