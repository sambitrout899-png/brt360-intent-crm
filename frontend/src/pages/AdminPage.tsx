import { getWorkspace, onWorkspaceChange, request } from "../api/client";
import WorkspaceBar from "../components/admin/WorkspaceBar";
import AdminReleasesSection from "./AdminReleasesSection";
import { getInventorySync, saveInventorySync, runInventorySync, type InventorySyncSettings } from "../api/client";
import { useEffect, useState } from "react";
import {
  createAttributeConstraintRule,
  createAttributeDefinition,
  createFeasibilityCheckType,
  createIdentityProvider,
  createOrchestrationPolicy,
  createPonrPolicy,
  createSlaPolicy,
  createUser,
  deactivateAttributeConstraintRule,
  deactivateFeasibilityCheckType,
  deactivateIdentityProvider,
  deactivateOrchestrationPolicy,
  deactivatePonrPolicy,
  deactivateSlaPolicy,
  deactivateUser,
  deleteAttributeConstraintRule,
  deleteFeasibilityCheckType,
  deleteIdentityProvider,
  deleteOrchestrationPolicy,
  deletePonrPolicy,
  deleteSlaPolicy,
  getSecurityPolicy,
  getTestRun,
  intentCatalog,
  listAttributeConstraintRules,
  listAttributeDefinitions,
  listAttributeValues,
  listFeasibilityCheckTypes,
  listIdentityProviders,
  listOrchestrationPolicies,
  listPonrPolicies,
  listProductSpecifications,
  listSlaClocks,
  listSlaPolicies,
  listTestRuns,
  listUsers,
  processDueSlaClocks,
  resetUserMfa,
  setAttributeValue,
  setSecurityPolicy,
  triggerTestRun,
  unlockUser,
  type SecurityPolicy,
} from "../api/client";
import { problem } from "../api/customRecords";
import { ATTRIBUTE_DATA_TYPES, CONSTRAINT_OPERATORS, CONSTRAINT_RULE_TYPES, ORCHESTRATION_MODES, SLA_BREACH_ACTIONS, USER_ROLES } from "../api/types";
import type {
  AttributeConstraintRule,
  FeasibilityCheckType,
  IdentityProvider,
  OrchestrationPolicy,
  PonrPolicy,
  ProductAttributeDefinition,
  ProductAttributeValue,
  ProductSpecification,
  SlaClock,
  SlaPolicy,
  TestRun,
  TestRunSummary,
  User,
} from "../api/types";
import { AssignmentSection, CustomFieldsSection, StateModelsSection } from "./AdminLifecycleSections";
import { OrgStructureSection, RolesAccessSection } from "./AdminOrgSections";
import AdminAiSection from "./AdminAiSection";
import AdminIntentsSection from "./AdminIntentsSection";
import FlowStudioSection from "./FlowStudio";
import AdminFunctionMap from "./AdminFunctionMap";
import AdminConflictsSection from "./AdminConflictsSection";
import { intentLabel } from "../intentLabels";
import NotificationLog from "../components/NotificationLog";
import { ApprovalsSection, BroadcastsSection, PriceListsSection, ProcessRolesSection, QuoteDocumentsSection } from "./AdminProcessSections";
import AdminPageDesignerSection from "./AdminPageDesignerSection";
import AdminRecordTypesSection from "./AdminRecordTypesSection";
import AdminBimSection from "./AdminBimSection";
import AdminOrderMgmtSection from "./AdminOrderMgmtSection";
import AdminFulfillmentSection from "./AdminFulfillmentSection";
import AdminCommsSection from "./AdminCommsSection";
import AdminProductModelSection from "./AdminProductModelSection";
import AttributeLibrary from "./AdminAttributeLibrary";

type Section = "releases" | "functionmap" | "conflicts" | "org" | "access" | "intents" | "pages" | "bim" | "productmodel" | "lifecycle" | "assignment" | "customfields" | "recordtypes" | "flows" | "approvals" | "broadcasts" | "roles" | "pricelists" | "productconfig" | "sla" | "attributes" | "identity" | "ponr" | "feasibility" | "orchestration" | "ai" | "users" | "tests" | "ordermgmt" | "fulfillment" | "comms" | "quotedocs";

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
      <AttributeLibrary onChanged={load} />

      <div>
        <h3 className="mb-1 text-base font-bold text-slate-100">Add an attribute to the library</h3>
        <p className="text-xs text-slate-500">Products usually add their own from the Product Modeller or "Design attributes" - this is for library entries made up front.</p>
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
        <h3 className="mb-1 text-base font-bold text-slate-100">Atomic product values</h3>
        <p className="mb-3 text-xs text-slate-500">Values on the atomic products bundles are composed of (e.g. Fiber1000's Download Speed) - checked with the composition rules below when a product is launched, changed or quoted.</p>
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
        B2C process pack: pluggable identity-verification providers for "Verify customer identity", blocked
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

function emptyPonrForm() {
  return { entityType: "quote", ponrStatuses: "" };
}

function PonrPoliciesSection() {
  const [policies, setPolicies] = useState<PonrPolicy[]>([]);
  const [form, setForm] = useState(emptyPonrForm());
  const [error, setError] = useState<string | null>(null);

  function load() {
    listPonrPolicies().then(setPolicies);
  }

  useEffect(load, []);

  async function submit() {
    setError(null);
    if (!form.ponrStatuses.trim()) {
      setError("At least one PONR status is required (comma-separated).");
      return;
    }
    try {
      await createPonrPolicy({ entity_type: form.entityType, ponr_statuses: form.ponrStatuses.trim() });
      setForm(emptyPonrForm());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create policy.");
    }
  }

  async function deactivate(id: number) {
    await deactivatePonrPolicy(id);
    load();
  }

  async function remove(id: number) {
    await deletePonrPolicy(id);
    load();
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        The Point of No Return: once a quote or order reaches one of these statuses, "Revise this quote"/"Revise this
        order" and "Cancel this quote" are blocked - use "Change this live service" or "Suspend, cancel or terminate"
        instead. Unconfigured defaults: a quote's PONR is "approved"; an order's PONR is anything past
        created/activating.
      </p>

      <div className="space-y-2">
        {policies.map((p) => (
          <div key={p.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${p.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-bold text-slate-300">{p.entity_type}</span>
            <span className="font-mono text-xs text-slate-400">{p.ponr_statuses}</span>
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
        {policies.length === 0 && <p className="text-sm text-slate-500">No PONR policies configured yet - defaults apply.</p>}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-4 text-base font-bold text-slate-100">New PONR Policy</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-xs text-slate-400">
            Entity type
            <select
              value={form.entityType}
              onChange={(e) => setForm((f) => ({ ...f, entityType: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
            >
              <option value="quote">quote</option>
              <option value="order">order</option>
            </select>
          </label>
          <label className="text-xs text-slate-400">
            PONR statuses (comma-separated)
            <input
              value={form.ponrStatuses}
              onChange={(e) => setForm((f) => ({ ...f, ponrStatuses: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
              placeholder="e.g. approved,rejected"
            />
          </label>
        </div>
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
        <button onClick={submit} className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Create PONR Policy
        </button>
      </div>
    </div>
  );
}

function FeasibilityCheckTypesSection() {
  const [settings, setSettings] = useState<InventorySyncSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { getInventorySync().then(setSettings).catch(e => setError(String(e))); }, []);
  async function inventoryAction(action: "save" | "sample" | "sync") {
    setBusy(true); setError(null); setMessage("");
    try {
      if (action === "save" && settings) {
        setSettings(await saveInventorySync(settings.enabled, settings.interval_minutes));
        setMessage("Schedule saved.");
      } else {
        const result = await runInventorySync(action === "sample");
        setCheckTypes(await listFeasibilityCheckTypes());
        setSettings(await getInventorySync());
        setMessage(`${result.created} added, ${result.updated} updated, ${result.deactivated} deactivated.`);
      }
    } catch (e) { setError(e instanceof Error ? e.message : "Inventory sync failed."); }
    finally { setBusy(false); }
  }

  const [checkTypes, setCheckTypes] = useState<FeasibilityCheckType[]>([]);
  const [name, setName] = useState("");
  const [resourceType, setResourceType] = useState("fiber");
  const [error, setError] = useState<string | null>(null);

  function load() {
    listFeasibilityCheckTypes().then(setCheckTypes).catch(e => setError(String(e)));
  }

  useEffect(load, []);

  async function submit() {
    setError(null);
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    try {
      await createFeasibilityCheckType({ name: name.trim(), resource_type: resourceType });
      setName("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create check type.");
    }
  }

  async function deactivate(id: number) {
    await deactivateFeasibilityCheckType(id);
    load();
  }

  async function remove(id: number) {
    await deleteFeasibilityCheckType(id);
    load();
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Which resource checks "Run feasibility assessment" (B2B, at the Opportunity level) actually runs - fiber
        route availability, port/switch capacity, inventory on hand. With none configured, the intent still runs a
        single address-validation check so it does something meaningful even for an unconfigured tenant.
      </p>

      <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-sm text-slate-300">
        <h3 className="font-semibold">Inventory integration</h3>
        <p>Refresh check types from the tenant inventory. Manual checks are preserved. Sample data is for testing and is not scheduled.</p>
        {settings && <>
          <p>{settings.connected ? "Inventory endpoint configured" : "Ask your operator to configure the tenant inventory API endpoint."}</p>
          <div className="flex flex-wrap items-center gap-3">
            <label><input type="checkbox" checked={settings.enabled} onChange={e => setSettings({...settings, enabled: e.target.checked})} /> Periodic refresh</label>
            <label>Every <input aria-label="Refresh interval in minutes" type="number" min={5} max={10080} value={settings.interval_minutes} onChange={e => setSettings({...settings, interval_minutes: Number(e.target.value)})} className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1" /> minutes</label>
            <button disabled={busy} onClick={() => inventoryAction("save")} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-50">Save schedule</button>
          </div>
          <p>Last inventory sync: {settings.last_success_at ? new Date(settings.last_success_at + "Z").toLocaleString() : "Never"}</p>
          {settings.last_error && <p role="alert" className="text-red-400">{settings.last_error}</p>}
        </>}
        <div className="flex gap-3">
          <button disabled={busy || !settings?.connected} onClick={() => inventoryAction("sync")} className="rounded-lg bg-sky-600 px-4 py-2 font-bold text-white disabled:opacity-50">Sync inventory now</button>
          <button disabled={busy} onClick={() => inventoryAction("sample")} className="rounded-lg border border-sky-600 px-4 py-2 text-sky-300 disabled:opacity-50">{busy ? "Working…" : "Load sample check types"}</button>
        </div>
        {message && <p role="status" className="text-emerald-400">{message}</p>}
      </div>
      <div className="space-y-2">
        {checkTypes.map((c) => (
          <div key={c.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${c.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="font-semibold text-slate-200">{c.name}</span>
            {c.external_id && <span className="text-xs text-sky-400">{c.external_id.startsWith("sample:") ? "Sample" : "Inventory"}</span>}
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{c.resource_type}</span>
            {!c.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">inactive</span>}
            <div className="ml-auto flex gap-2">
              {c.is_active && (
                <button onClick={() => deactivate(c.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-amber-500 hover:text-amber-400">
                  Deactivate
                </button>
              )}
              <button onClick={() => remove(c.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400">
                Delete
              </button>
            </div>
          </div>
        ))}
        {checkTypes.length === 0 && <p className="text-sm text-slate-500">No feasibility check types configured yet - address validation only.</p>}
      </div>

      <div className="flex flex-wrap items-end gap-2 rounded-xl border border-slate-700 bg-slate-900/60 p-4">
        <label className="text-xs text-slate-400">
          Check name
          <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-56 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="e.g. Fiber Route Availability" />
        </label>
        <label className="text-xs text-slate-400">
          Resource type
          <select value={resourceType} onChange={(e) => setResourceType(e.target.value)} className="mt-1 w-36 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
            <option value="fiber">fiber</option>
            <option value="port">port</option>
            <option value="switch">switch</option>
            <option value="inventory">inventory</option>
          </select>
        </label>
        <button onClick={submit} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Add Check Type
        </button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  );
}

// The fulfilment decision points an orchestration mode can cover, by name.
const DECISION_POINTS: [string, string][] = [
  ["ALL", "Every fulfilment step"],
  ["INT-25", "After an order is approved"],
  ["INT-26", "After installation (WFM1)"],
  ["INT-27", "After testing (WFM2)"],
  ["INT-28", "After the ESAN / acceptance letter (WFM3)"],
];
const decisionPointLabel = (code: string) => DECISION_POINTS.find(([c]) => c === code)?.[1] ?? intentLabel(code);

function emptyOrchestrationForm() {
  return { intentType: "INT-25", mode: ORCHESTRATION_MODES[0] as string };
}

function OrchestrationPoliciesSection() {
  const [policies, setPolicies] = useState<OrchestrationPolicy[]>([]);
  const [form, setForm] = useState(emptyOrchestrationForm());
  const [error, setError] = useState<string | null>(null);

  function load() {
    listOrchestrationPolicies().then(setPolicies);
  }

  useEffect(load, []);

  async function submit() {
    setError(null);
    if (!form.intentType.trim()) {
      setError("Pick the step this mode applies to.");
      return;
    }
    try {
      await createOrchestrationPolicy({ intent_type: form.intentType.trim(), mode: form.mode });
      setForm(emptyOrchestrationForm());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create policy.");
    }
  }

  async function deactivate(id: number) {
    await deactivateOrchestrationPolicy(id);
    load();
  }

  async function remove(id: number) {
    await deleteOrchestrationPolicy(id);
    load();
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        How the order-fulfilment steps decide what happens next: "deterministic"
        (default - a fixed, tested step sequence) or "llm_planned" (an LLM chooses which pre-vetted operation runs
        next, only from a short explicit allow-list - never arbitrary code). llm_planned silently falls back to
        deterministic whenever no OPENAI_API_KEY is configured or the LLM's plan doesn't validate, so this is always
        safe to turn on. "Every fulfilment step" covers them all at once; a row for one specific step wins over it.
      </p>

      <div className="space-y-2">
        {policies.map((p) => (
          <div key={p.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${p.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-bold text-slate-300">{decisionPointLabel(p.intent_type)}</span>
            <span
              className={`rounded-md border px-2 py-0.5 text-xs font-bold ${
                p.mode === "llm_planned" ? "bg-violet-500/15 text-violet-400 border-violet-500/30" : "bg-slate-500/15 text-slate-400 border-slate-500/30"
              }`}
            >
              {p.mode}
            </span>
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
        {policies.length === 0 && <p className="text-sm text-slate-500">No orchestration policies configured yet - every fulfillment intent runs deterministically.</p>}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-4 text-base font-bold text-slate-100">New Orchestration Policy</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-xs text-slate-400">
            Applies to
            <select
              value={form.intentType}
              onChange={(e) => setForm((f) => ({ ...f, intentType: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
            >
              {DECISION_POINTS.map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            Mode
            <select
              value={form.mode}
              onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value }))}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
            >
              {ORCHESTRATION_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
        <button onClick={submit} className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Create Policy
        </button>
      </div>
    </div>
  );
}

function emptyUserForm() {
  return { email: "", password: "", name: "", role: USER_ROLES[1] as string };
}

function UsersSection() {
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState(emptyUserForm());
  const [error, setError] = useState<string | null>(null);

  function load() {
    listUsers().then(setUsers);
  }

  useEffect(load, []);

  async function submit() {
    setError(null);
    if (!form.email.trim() || form.password.length < 8 || !form.name.trim()) {
      setError("Email, name, and a password (8+ characters) are all required.");
      return;
    }
    try {
      await createUser({ email: form.email.trim(), password: form.password, name: form.name.trim(), role: form.role });
      setForm(emptyUserForm());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create user.");
    }
  }

  async function deactivate(id: number) {
    await deactivateUser(id);
    load();
  }

  async function act(run: () => Promise<unknown>) {
    setError(null);
    try {
      await run();
      load();
    } catch (e) {
      setError(problem(e, "That didn't work."));
    }
  }

  const locked = (u: User) => !!u.locked_until && new Date(u.locked_until.endsWith("Z") ? u.locked_until : `${u.locked_until}Z`) > new Date();

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Accounts for your team. <span className="font-semibold text-slate-300">admin</span> can configure everything on
        this Admin page; <span className="font-semibold text-slate-300">agent</span> can do day-to-day CRM work
        (submit intents) but not touch admin configuration; <span className="font-semibold text-slate-300">viewer</span>{" "}
        is read-only everywhere.
      </p>

      <SignInPolicy onError={setError} />

      <div className="space-y-2">
        {users.map((u) => (
          <div key={u.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${u.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="font-semibold text-slate-200">{u.name}</span>
            <span className="font-mono text-xs text-slate-500">{u.email}</span>
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{u.role}</span>
            {u.mfa_enabled && <span className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-400">two-step</span>}
            {locked(u) && <span className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-400">locked</span>}
            {!u.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">deactivated</span>}
            {u.is_active && (
              <span className="ml-auto flex gap-2">
                {locked(u) && (
                  <button onClick={() => act(() => unlockUser(u.id))} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-300">
                    Unlock
                  </button>
                )}
                {u.mfa_enabled && (
                  <button onClick={() => act(() => resetUserMfa(u.id))} title="For a user who lost their phone and recovery codes: they set two-step sign-in up again"
                    className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-amber-500 hover:text-amber-300">
                    Reset two-step
                  </button>
                )}
                <button onClick={() => deactivate(u.id)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400">
                  Deactivate
                </button>
              </span>
            )}
          </div>
        ))}
        {users.length === 0 && <p className="text-sm text-slate-500">No users yet.</p>}
      </div>

      <div className="flex flex-wrap items-end gap-2 rounded-xl border border-slate-700 bg-slate-900/60 p-4">
        <label className="text-xs text-slate-400">
          Name
          <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} className="mt-1 w-40 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" />
        </label>
        <label className="text-xs text-slate-400">
          Email
          <input type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} className="mt-1 w-48 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" />
        </label>
        <label className="text-xs text-slate-400">
          Password
          <input type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} className="mt-1 w-36 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="8+ characters" />
        </label>
        <label className="text-xs text-slate-400">
          Role
          <select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))} className="mt-1 w-28 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
            {USER_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <button onClick={submit} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Create User
        </button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  );
}

const IDLE_CHOICES: [number, string][] = [[0, "Off"], [15, "15 minutes"], [30, "30 minutes"], [60, "1 hour"], [120, "2 hours"], [240, "4 hours"]];

/** Company-wide sign-in rules: two-step sign-in for everyone, and the idle timeout. */
function SignInPolicy({ onError }: { onError: (message: string | null) => void }) {
  const [policy, setPolicy] = useState<SecurityPolicy | null>(null);

  useEffect(() => {
    getSecurityPolicy().then(setPolicy).catch(() => setPolicy(null));
  }, []);

  async function save(change: Partial<SecurityPolicy>) {
    if (!policy) return;
    onError(null);
    try {
      setPolicy(await setSecurityPolicy({ mfa_required: policy.mfa_required, ...change }));
    } catch (e) {
      onError(problem(e, "Couldn't change the sign-in policy."));
    }
  }

  if (policy === null) return null;
  const idle = policy.idle_timeout_minutes ?? 0;
  return (
    <div className="space-y-4 rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-sm">
      <label className="flex items-start gap-3">
        <input type="checkbox" checked={policy.mfa_required} onChange={(e) => save({ mfa_required: e.target.checked })} className="mt-1" />
        <span>
          <span className="font-semibold text-slate-200">Require two-step sign-in for everyone</span>
          <span className="block text-xs text-slate-400">
            Everyone signs in with their password and a code from an authenticator app; whoever hasn't set it up does so at
            their next sign-in. Turn it on for your own account first (click your name, top right).
          </span>
        </span>
      </label>
      <label className="flex flex-wrap items-center gap-3">
        <span className="font-semibold text-slate-200">Sign out after no activity for</span>
        <select value={idle} onChange={(e) => save({ idle_timeout_minutes: Number(e.target.value) })}
          className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-200">
          {IDLE_CHOICES.map(([minutes, label]) => <option key={minutes} value={minutes}>{label}</option>)}
          {!IDLE_CHOICES.some(([m]) => m === idle) && <option value={idle}>{idle} minutes</option>}
        </select>
        <span className="block w-full text-xs text-slate-400">
          A minute before, the screen offers to keep you signed in. Off: sessions end only after 8 hours.
        </span>
      </label>
    </div>
  );
}

const TEST_STATUS_STYLES: Record<string, string> = {
  running: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  passed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  error: "bg-red-500/15 text-red-400 border-red-500/30",
};

function TestSuiteSection() {
  const [runs, setRuns] = useState<TestRunSummary[]>([]);
  const [selected, setSelected] = useState<TestRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    listTestRuns().then(setRuns);
  }

  useEffect(load, []);

  async function run() {
    setError(null);
    setRunning(true);
    try {
      const result = await triggerTestRun();
      setSelected(result);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to run tests.");
    } finally {
      setRunning(false);
    }
  }

  async function viewDetail(id: number) {
    const detail = await getTestRun(id);
    setSelected(detail);
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Runs the real backend test suite as a subprocess and reports the result - no SSH access needed to verify a
        deployment is healthy. Tests run against their own isolated in-memory database, never the tenant's real data,
        so this is safe to run against production. Takes roughly 10-20 seconds.
      </p>

      <button onClick={run} disabled={running} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
        {running ? "Running…" : "Run Full Test Suite"}
      </button>
      {error && <p className="text-xs text-red-400">{error}</p>}

      {selected && (
        <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <span className={`rounded-md border px-2 py-0.5 text-xs font-bold ${TEST_STATUS_STYLES[selected.status]}`}>{selected.status}</span>
            <span className="text-sm text-slate-300">{selected.passed} passed</span>
            {selected.failed > 0 && <span className="text-sm text-red-400">{selected.failed} failed</span>}
            {selected.errors > 0 && <span className="text-sm text-red-400">{selected.errors} errors</span>}
            <span className="text-xs text-slate-500">{selected.duration_seconds}s</span>
          </div>
          <pre className="max-h-96 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-400">{selected.output}</pre>
        </div>
      )}

      <div>
        <h3 className="mb-3 text-base font-bold text-slate-100">History</h3>
        <div className="space-y-2">
          {runs.map((r) => (
            <button
              key={r.id}
              onClick={() => viewDetail(r.id)}
              className="flex w-full flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm text-left hover:border-sky-500"
            >
              <span className={`rounded-md border px-2 py-0.5 text-xs font-bold ${TEST_STATUS_STYLES[r.status]}`}>{r.status}</span>
              <span className="text-slate-300">{r.passed} passed</span>
              {r.failed > 0 && <span className="text-red-400">{r.failed} failed</span>}
              <span className="text-xs text-slate-500">{new Date(r.started_at).toLocaleString()}</span>
              <span className="ml-auto text-xs text-slate-500">{r.duration_seconds}s</span>
            </button>
          ))}
          {runs.length === 0 && <p className="text-sm text-slate-500">No test runs yet.</p>}
        </div>
      </div>
    </div>
  );
}

type NavItem = { id: Section; label: string; icon: string; hint: string; wide?: "xl" | "lg" };
type NavGroup = { title: string; icon: string; blurb: string; items: NavItem[] };

// The admin areas, grouped by what an administrator is trying to do.
const NAV: NavGroup[] = [
  {
    title: "People & Organization", icon: "🏢", blurb: "Who works here, what they see and which part of the process they own.",
    items: [
      { id: "org", label: "Org Structure", icon: "🌳", hint: "Departments, positions and who holds them", wide: "lg" },
      { id: "users", label: "Users", icon: "👥", hint: "Sign-ins and their built-in role" },
      { id: "access", label: "Roles & Access", icon: "🔐", hint: "Pages each role opens and data each position sees" },
      { id: "roles", label: "Process Roles", icon: "🎭", hint: "Account Manager, Presales, Sales Ops, CPMO, BSD" },
    ],
  },
  {
    title: "Products & Pricing", icon: "📦", blurb: "What you sell, how it is configured, what it costs and who may buy it.",
    items: [
      { id: "productmodel", label: "Product Modeller", icon: "🧩", hint: "Each product's overview, attributes (by hand or AI), child products, rules, import/export, release", wide: "lg" },
      { id: "attributes", label: "Product Attributes", icon: "🏷️", hint: "The shared attribute library - where each is used; atomic product values" },
      { id: "pricelists", label: "Pricelists", icon: "💰", hint: "Standard, Enterprise, Strategic ... and their prices" },
      { id: "feasibility", label: "Feasibility Checks", icon: "📡", hint: "Coverage and capacity checks Presales runs" },
      { id: "ordermgmt", label: "Order Management", icon: "🛒", hint: "Tax, shipping, favorites, recommendations, promotions and credit limits", wide: "lg" },
      { id: "fulfillment", label: "Fulfillment", icon: "🧭", hint: "Design COM / SOM / TOM fulfillment patterns on eTOM; monitor orders and fallout", wide: "lg" },
      { id: "comms", label: "Communications", icon: "📡", hint: "Billing, collections and fraud policy, providers, solutions and allowances", wide: "lg" },
    ],
  },
  {
    title: "Processes & Automation", icon: "⚙️", blurb: "How work flows: intents, states, approvals, assignment and timers.",
    items: [
      { id: "functionmap", label: "Functional Map", icon: "🗺️", hint: "Every function point and process as a picture - rearrange it and the intents follow", wide: "xl" },
      { id: "intents", label: "Intents", icon: "🎯", hint: "The actions people run, their names, fields and phrases", wide: "lg" },
      { id: "conflicts", label: "Conflicts", icon: "⚖️", hint: "Automations and actions that pull against each other - set their priority and conditions", wide: "lg" },
      { id: "flows", label: "Flow Studio", icon: "🔀", hint: "Visual flows around intents, states and validations", wide: "lg" },
      { id: "lifecycle", label: "Lifecycle", icon: "🔄", hint: "States and transitions of each record type" },
      { id: "approvals", label: "Approvals", icon: "✅", hint: "Discount and record approval rules, and the approval log" },
      { id: "quotedocs", label: "Quote Documents", icon: "📄", hint: "The proposal and agreement PDFs a quote gets - wording, switch on / off" },
      { id: "broadcasts", label: "Broadcasts", icon: "📣", hint: "Messages in the running bar on the Dashboard, My Work and Customer 360 - and the notification delivery log" },
      { id: "assignment", label: "Assignment Rules", icon: "📌", hint: "Who records are handed to" },
      { id: "sla", label: "SLA Policies", icon: "⏱️", hint: "Clocks and what happens on breach" },
      { id: "ponr", label: "PONR Policies", icon: "🚧", hint: "Points of no return for orders" },
      { id: "orchestration", label: "Orchestration Mode", icon: "🎼", hint: "Saga vs choreography per intent" },
    ],
  },
  {
    title: "Data & Pages", icon: "🗂️", blurb: "What each record holds, how its pages look, and how data gets in.",
    items: [
      { id: "pages", label: "Page Designer", icon: "🖼️", hint: "B2B/B2C pages, mandatory fields and rules", wide: "xl" },
      { id: "customfields", label: "Custom Fields", icon: "➕", hint: "Extra fields on records" },
      { id: "recordtypes", label: "Record Types", icon: "🗃️", hint: "Record types of your own (Site Survey, Contract ...) and their fields", wide: "lg" },
      { id: "bim", label: "Integration (BIM)", icon: "🔌", hint: "BRT_IDs, batch loads and EAI interfaces", wide: "lg" },
    ],
  },
  {
    title: "Platform", icon: "🛠️", blurb: "AI, identity verification and quality.",
    items: [
      { id: "releases", label: "Releases & Workspaces", icon: "🚀", hint: "Admins' workspaces, locks, and the releases the root administrator deploys or rolls back", wide: "lg" },
      { id: "ai", label: "AI Settings", icon: "🤖", hint: "Which AI model the platform uses" },
      { id: "identity", label: "Identity Providers", icon: "🪪", hint: "KYC / ID verification services" },
      { id: "tests", label: "Test Suite", icon: "🧪", hint: "Run the platform's checks" },
    ],
  },
];
const ITEMS = NAV.flatMap((g) => g.items.map((i) => ({ ...i, group: g.title })));
const REMEMBER = "brt360.admin.section";

function remembered(): Section | "home" {
  try {
    const saved = localStorage.getItem(REMEMBER);
    return saved && (saved === "home" || ITEMS.some((i) => i.id === saved)) ? (saved as Section | "home") : "home";
  } catch {
    return "home";
  }
}

function AdminHome({ open, allowed }: { open: (id: Section) => void; allowed: (id: Section) => boolean }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {NAV.map(g => ({ ...g, items: g.items.filter(i => allowed(i.id)) })).filter(g => g.items.length).map((g) => (
        <section key={g.title} className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
          <h2 className="text-base font-semibold text-slate-100">
            {g.icon} {g.title}
          </h2>
          <p className="mb-3 mt-0.5 text-xs text-slate-400">{g.blurb}</p>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {g.items.map((i) => (
              <button
                key={i.id}
                type="button"
                onClick={() => open(i.id)}
                className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2 text-left transition hover:border-sky-600 hover:bg-slate-900"
              >
                <span className="block text-sm font-semibold text-slate-200">
                  {i.icon} {i.label}
                </span>
                <span className="block text-[11px] leading-snug text-slate-500">{i.hint}</span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export default function AdminPage({ onAccessChanged }: { onAccessChanged?: () => void }) {
  const [selectedSection, setSectionState] = useState<Section | "home">(remembered);
  const [modules, setModules] = useState<string[]>([]);
  const [moduleError, setModuleError] = useState('');
  useEffect(() => { request<{key:string; enabled:boolean}[]>('/api/modules').then(rows => setModules(rows.filter(r => r.enabled).map(r => r.key))).catch(e => setModuleError(String(e))); }, []);
  const allowed = (id: Section) => {
    if (['org','access','users','ai','broadcasts','approvals','roles','lifecycle','assignment','releases'].includes(id)) return true;
    const module = id==='flows' ? 'flow_studio' : id==='pages' ? 'page_designer' : ['intents','customfields','recordtypes','functionmap','conflicts'].includes(id) ? 'custom_development' : 'telecom_crm';
    return modules.includes(module);
  };
  const section = selectedSection==='home' || allowed(selectedSection) ? selectedSection : 'home';
  // Flow Studio shows the selected workspace's drafts - it reloads when the workspace changes
  const [workspaceKey, setWorkspaceKey] = useState(() => String(getWorkspace() ?? "live"));
  useEffect(() => onWorkspaceChange((id) => setWorkspaceKey(String(id ?? "live"))), []);
  const [filter, setFilter] = useState("");
  const setSection = (id: Section | "home") => {
    setSectionState(id);
    try {
      localStorage.setItem(REMEMBER, id);
    } catch {
      /* private window - just don't remember */
    }
  };
  // Action names for every admin screen (codes stay internal).
  useEffect(() => {
    intentCatalog().catch(() => null);
  }, []);

  const current = ITEMS.find((i) => i.id === section);
  const q = filter.trim().toLowerCase();
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((i) => allowed(i.id) && (!q || `${i.label} ${i.hint} ${g.title}`.toLowerCase().includes(q))) })).filter((g) => g.items.length);
  const width = current?.wide === "xl" ? "max-w-[110rem]" : current?.wide === "lg" || section === "home" ? "max-w-[90rem]" : "max-w-[90rem]";

  return (
    <div className={`mx-auto ${width}`}>
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-slate-100">Admin</h1>
          <p className="mt-1 text-sm text-slate-400">
            {current ? (
              <>
                <button type="button" onClick={() => setSection("home")} className="text-sky-400 hover:text-sky-300">
                  Admin
                </button>{" "}
                › {current.group} › <span className="text-slate-200">{current.label}</span> - {current.hint}
              </>
            ) : (
              "Configure your organization, products and processes - without touching code."
            )}
          </p>
        </div>
        {/* phone / narrow screens: one picker instead of the side menu */}
        <select value={section} onChange={(e) => setSection(e.target.value as Section | "home")} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 lg:hidden">
          <option value="home">Overview</option>
          {NAV.map(g => ({ ...g, items: g.items.filter(i => allowed(i.id)) })).filter(g => g.items.length).map((g) => (
            <optgroup key={g.title} label={g.title}>
              {g.items.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      <div className="flex gap-6">
        <nav className="sticky top-4 hidden max-h-[calc(100vh-2rem)] w-60 shrink-0 self-start overflow-y-auto pr-1 lg:block">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Find a setting…"
            className="mb-3 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-500"
          />
          <button
            type="button"
            onClick={() => setSection("home")}
            className={`mb-3 flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-sm ${section === "home" ? "bg-sky-500/15 font-semibold text-sky-300" : "text-slate-300 hover:bg-slate-800"}`}
          >
            🏠 Overview
          </button>
          {groups.map((g) => (
            <div key={g.title} className="mb-3">
              <p className="mb-1 px-3 text-[10px] font-bold uppercase tracking-wider text-slate-500">{g.title}</p>
              {g.items.map((i) => (
                <button
                  key={i.id}
                  type="button"
                  onClick={() => setSection(i.id)}
                  title={i.hint}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-sm transition ${
                    section === i.id ? "bg-sky-500/15 font-semibold text-sky-300" : "text-slate-300 hover:bg-slate-800 hover:text-slate-100"
                  }`}
                >
                  <span className="w-5 text-center">{i.icon}</span>
                  {i.label}
                </button>
              ))}
            </div>
          ))}
          {groups.length === 0 && <p className="px-3 text-xs text-slate-500">Nothing matches.</p>}
        </nav>

        <main className="min-w-0 flex-1 space-y-6">
          {section === "home" && <><p className="text-red-300">{moduleError}</p><AdminHome open={setSection} allowed={allowed} /></>}
          {section === "org" && <OrgStructureSection />}
          {section === "access" && <RolesAccessSection onAccessChanged={onAccessChanged} />}
          {section === "intents" && <AdminIntentsSection onOpenFlowStudio={() => setSection("flows")} />}
          {section === "flows" && (
            <>
              <WorkspaceBar onOpenReleases={() => setSection("releases")} />
              <FlowStudioSection key={workspaceKey} />
            </>
          )}
          {section === "releases" && <AdminReleasesSection />}
          {section === "conflicts" && <AdminConflictsSection onOpenFlowStudio={() => setSection("flows")} />}
          {section === "functionmap" && <AdminFunctionMap onOpenFlowStudio={() => setSection("flows")} onOpenIntents={() => setSection("intents")} />}
          {section === "pages" && <AdminPageDesignerSection />}
          {section === "bim" && <AdminBimSection />}
      {section === "ordermgmt" && <AdminOrderMgmtSection />}
      {section === "fulfillment" && <AdminFulfillmentSection />}
      {section === "comms" && <AdminCommsSection />}
          {section === "lifecycle" && <StateModelsSection />}
          {section === "assignment" && <AssignmentSection />}
          {section === "customfields" && <CustomFieldsSection />}
          {section === "recordtypes" && <AdminRecordTypesSection />}
          {section === "approvals" && <ApprovalsSection />}
          {section === "broadcasts" && <><BroadcastsSection /><NotificationLog /></>}
          {section === "roles" && <ProcessRolesSection />}
          {section === "pricelists" && <PriceListsSection />}
          {section === "quotedocs" && <QuoteDocumentsSection />}
          {section === "productmodel" && <AdminProductModelSection />}
          {section === "sla" && <SlaPoliciesSection />}
          {section === "attributes" && <ProductAttributesSection />}
          {section === "identity" && <IdentityProvidersSection />}
          {section === "ponr" && <PonrPoliciesSection />}
          {section === "feasibility" && <FeasibilityCheckTypesSection />}
          {section === "orchestration" && <OrchestrationPoliciesSection />}
          {section === "ai" && <AdminAiSection />}
          {section === "users" && <UsersSection />}
          {section === "tests" && <TestSuiteSection />}
        </main>
      </div>
    </div>
  );
}
