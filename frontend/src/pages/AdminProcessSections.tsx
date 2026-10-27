import { useEffect, useState } from "react";
import {
  addBroadcast,
  applyApprovalPreset,
  approvalLog,
  listBroadcasts,
  removeBroadcast,
  approvalRuleOptions,
  createApprovalRule,
  createPriceList,
  listApprovalRules,
  listCatalog,
  listPriceLists,
  listProcessRoles,
  listQuoteDocumentTemplates,
  previewQuoteDocumentTemplate,
  saveQuoteDocumentTemplate,
  setApprovalRuleActive,
  setProcessRole,
  updateApprovalRule,
  updatePriceList,
} from "../api/client";
import type {
  ApprovalLogEntry, ApprovalRule, Broadcast, ApprovalRuleInput, ApprovalRuleOptions, Offering, PriceList, ProcessRole, QuoteDocumentTemplate,
} from "../api/types";
import { WaitingNow, WhoDecides } from "../components/approvals/ApprovalPolicyPanel";

const INPUT ="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";
const SMALL_BTN = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300";
const CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "SAR"];
const ENTITY_LABEL: Record<string, string> = {
  party: "Customer / account", lead: "Lead", opportunity: "Opportunity", quote: "Quote", order: "Order",
  adjustment: "Bill adjustment", arrangement: "Payment arrangement",
};
type Kind = "monetary" | "record";

function errorText(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback;
}

// ---------------------------------------------------------------- approvals

interface TierForm {
  positionId: number;
  label: string; // a step from before positions existed keeps its name until a position is picked
}

interface RuleForm {
  id: number | null;
  name: string;
  entityType: string;
  triggerState: string;
  segment: string;
  minPct: string;
  maxPct: string;
  minAmount: string;
  maxAmount: string;
  isActive: boolean;
  tiers: TierForm[];
  applyToPending: boolean;
}

function emptyRule(kind: Kind): RuleForm {
  return kind === "monetary"
    ? { id: null, name: "", entityType: "quote", triggerState: "", segment: "", minPct: "", maxPct: "", minAmount: "", maxAmount: "", isActive: true, tiers: [{ positionId: 0, label: "" }], applyToPending: true }
    : { id: null, name: "", entityType: "", triggerState: "", segment: "", minPct: "0", maxPct: "100", minAmount: "", maxAmount: "", isActive: true, tiers: [{ positionId: 0, label: "" }], applyToPending: true };
}

function ruleToForm(r: ApprovalRule): RuleForm {
  return {
    id: r.id,
    name: r.name,
    entityType: r.entity_type,
    triggerState: r.trigger_state ?? "",
    segment: r.segment ?? "",
    minPct: String(r.min_discount_pct),
    maxPct: String(r.max_discount_pct),
    minAmount: r.min_amount == null ? "" : String(r.min_amount),
    maxAmount: r.max_amount == null ? "" : String(r.max_amount),
    isActive: r.is_active,
    // a step that is not a real approver ("Yes") starts empty, so a position has to be picked
    tiers: r.tiers.map((t) => (t.not_an_approver ? { positionId: 0, label: "" } : { positionId: t.position_id ?? 0, label: t.approver_tier })),
    applyToPending: true,
  };
}

function stateLabel(options: ApprovalRuleOptions | null, entityType: string, code: string | null): string {
  return options?.record_types.find((t) => t.entity_type === entityType)?.states.find((s) => s.code === code)?.label ?? code ?? "";
}

function monetaryLabel(options: ApprovalRuleOptions | null, entityType: string): string {
  return options?.monetary_labels?.[entityType] ?? (entityType === "order" ? "Consumer orders (B2C)" : entityType === "quote" ? "Quotes (B2B)" : entityType);
}

function describeRule(r: ApprovalRule, options: ApprovalRuleOptions | null): string {
  if (r.kind === "monetary") {
    const target = monetaryLabel(options, r.entity_type);
    const value = r.min_amount != null || r.max_amount != null ? ` · deal value ${r.min_amount ?? 0}–${r.max_amount ?? "any"}` : "";
    return `${target} · discount ${r.min_discount_pct}% to ${r.max_discount_pct}%${value}${r.segment ? ` · ${r.segment}` : ""}`;
  }
  return `${ENTITY_LABEL[r.entity_type] ?? r.entity_type} may only become “${stateLabel(options, r.entity_type, r.trigger_state)}” once approved`;
}

export function ApprovalsSection() {
  const [kind, setKind] = useState<Kind>("monetary");
  const [rules, setRules] = useState<ApprovalRule[]>([]);
  const [options, setOptions] = useState<ApprovalRuleOptions | null>(null);
  const [form, setForm] = useState<RuleForm>(emptyRule("monetary"));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  // a ready-made rule being switched on: the positions picked for its steps
  const [presetPick, setPresetPick] = useState<{ key: string; ids: number[] } | null>(null);
  const [refresh, setRefresh] = useState(0);

  function load() {
    listApprovalRules().then(setRules).catch(() => setRules([]));
    approvalRuleOptions().then(setOptions).catch(() => setOptions(null));
  }

  // after a change: the rules, and the approvals waiting (a rule edit or a setting can move them)
  function reload() {
    load();
    setRefresh((n) => n + 1);
  }

  useEffect(load, []);

  const shown = rules.filter((r) => r.kind === kind);
  const states = options?.record_types.find((t) => t.entity_type === form.entityType)?.states ?? [];
  const presets = (options?.presets ?? []).filter((p) => p.kind === kind);

  function switchKind(next: Kind) {
    setKind(next);
    setForm(emptyRule(next));
    setError(null);
  }

  function patch(p: Partial<RuleForm>) {
    setForm((f) => ({ ...f, ...p }));
  }

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      reload();
    } catch (e) {
      setError(errorText(e, "That did not work."));
    }
  }

  async function submit() {
    const tiers = form.tiers.filter((t) => t.positionId > 0 || t.label);
    setSaved(null);
    if (!form.name.trim() || tiers.length === 0) {
      setError("Give the rule a name and at least one approver.");
      return;
    }
    if (kind === "record" && (!form.entityType || !form.triggerState)) {
      setError("Pick the record and the stage this approval guards.");
      return;
    }
    if (kind === "monetary" && (form.minPct === "" || form.maxPct === "")) {
      setError("Set the discount band this approval covers.");
      return;
    }
    const payload: ApprovalRuleInput = {
      name: form.name.trim(),
      kind,
      entity_type: form.entityType,
      trigger_state: kind === "record" ? form.triggerState : null,
      segment: form.segment || null,
      min_discount_pct: kind === "monetary" ? Number(form.minPct) : 0,
      max_discount_pct: kind === "monetary" ? Number(form.maxPct) : 100,
      min_amount: kind === "monetary" && form.minAmount !== "" ? Number(form.minAmount) : null,
      max_amount: kind === "monetary" && form.maxAmount !== "" ? Number(form.maxAmount) : null,
      is_active: form.isActive,
      tiers: tiers.map((t) => (t.positionId > 0 ? { position_id: t.positionId } : { position_id: null, approver_tier: t.label })),
      apply_to_pending: form.applyToPending,
    };
    await act(async () => {
      if (form.id) {
        const r = (await updateApprovalRule(form.id, payload)) as ApprovalRule & { rerouted?: number };
        setSaved(r.rerouted ? `Saved - ${r.rerouted} approval${r.rerouted === 1 ? "" : "s"} already waiting now go${r.rerouted === 1 ? "es" : ""} to the new approvers.` : "Saved.");
      } else {
        await createApprovalRule(payload);
        setSaved("Rule added.");
      }
      setForm(emptyRule(kind));
    });
  }

  function pickPreset(key: string, tierNames: string[]) {
    // suggest the position of the same name when there is one; otherwise the admin picks
    const ids = tierNames.map((n) => options?.positions.find((p) => p.name.toLowerCase() === n.toLowerCase())?.id ?? 0);
    setPresetPick({ key, ids });
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Two kinds of approval. <b className="text-slate-300">Money</b>: a discount band (and optionally a deal-value band) on a quote or a
        consumer order. <b className="text-slate-300">Records</b>: a record may only reach a stage once approved - a lead becoming
        qualified, an order going to fulfilment, or any stage of any record. Money rules also cover Order Management discounts,
        bill adjustments and payment arrangements (without a rule those keep their built-in limits and an administrator decides). Each
        step is decided by the people in one position; the record is assigned to that position and waits in their My Work. What happens
        when nobody holds a position, and whether requesters may decide their own requests, is set under <b className="text-slate-300">Who
        decides</b>. Every decision is kept in the approval log below.
      </p>

      <WhoDecides positions={options?.positions ?? []} onSaved={reload} />
      <WaitingNow positions={options?.positions ?? []} refresh={refresh} />

      <div className="inline-flex rounded-lg border border-slate-700 p-0.5">
        {(["monetary", "record"] as Kind[]).map((k) => (
          <button
            key={k}
            onClick={() => switchKind(k)}
            className={`rounded-md px-4 py-1.5 text-sm font-semibold ${kind === k ? "bg-sky-600 text-white" : "text-slate-400 hover:text-slate-200"}`}
          >
            {k === "monetary" ? "💰 Money (discounts)" : "📋 Records (stages)"}
          </button>
        ))}
      </div>

      {presets.some((p) => !p.applied) && (
        <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
          <p className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Ready-made - pick who approves, then switch on</p>
          <div className="flex flex-wrap gap-2">
            {presets.filter((p) => !p.applied).map((p) => (
              <button
                key={p.key}
                onClick={() => pickPreset(p.key, p.tiers)}
                title={p.help}
                className={`rounded-lg border px-3 py-1.5 text-left text-xs text-slate-300 hover:border-sky-500 ${presetPick?.key === p.key ? "border-sky-500" : "border-slate-700"}`}
              >
                <span className="block font-semibold text-slate-200">+ {p.name}</span>
                <span className="text-slate-500">{p.help}</span>
              </button>
            ))}
          </div>
          {presetPick && (() => {
            const preset = presets.find((p) => p.key === presetPick.key);
            if (!preset) return null;
            return (
              <div className="mt-3 space-y-2 rounded-lg border border-sky-700/50 p-3">
                <p className="text-xs text-slate-400">Who decides each step of <b className="text-slate-200">{preset.name}</b>?</p>
                {preset.tiers.map((suggested, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className="w-6 text-center text-xs text-slate-500">{i + 1}</span>
                    <select value={presetPick.ids[i] ?? 0} onChange={(e) => setPresetPick({ ...presetPick, ids: presetPick.ids.map((x, j) => (j === i ? Number(e.target.value) : x)) })}
                      className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
                      <option value={0}>Choose a position (suggested: {suggested})…</option>
                      {(options?.positions ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}{x.holders === 0 ? " - nobody yet" : ` - ${x.holders} ${x.holders === 1 ? "person" : "people"}`}</option>)}
                    </select>
                  </div>
                ))}
                <div className="flex gap-2">
                  <button disabled={presetPick.ids.some((x) => !x)} onClick={() => act(async () => (await applyApprovalPreset(presetPick.key, presetPick.ids), setPresetPick(null)))}
                    className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-bold text-white disabled:opacity-40">Switch on</button>
                  <button onClick={() => setPresetPick(null)} className="text-xs text-slate-400">Cancel</button>
                </div>
              </div>
            );
          })()}
        </div>
      )}

      <div className="space-y-2">
        {shown.map((r) => (
          <div key={r.id} className={`rounded-lg border px-4 py-3 text-sm ${r.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-semibold text-slate-200">{r.name}</span>
              <span className="text-xs text-slate-500">{describeRule(r, options)}</span>
              {!r.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">off</span>}
              <div className="ml-auto flex gap-2">
                <button onClick={() => setForm(ruleToForm(r))} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>Edit</button>
                <button onClick={() => act(() => setApprovalRuleActive(r.id, !r.is_active))} className={`${SMALL_BTN} hover:border-amber-500 hover:text-amber-400`}>
                  {r.is_active ? "Switch off" : "Switch on"}
                </button>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
              {r.tiers.map((t, i) => (
                <span key={t.sequence} className="flex items-center gap-2">
                  <span className={`rounded-md px-2 py-0.5 ${t.not_an_approver ? "bg-red-500/15 text-red-300" : "bg-slate-800"}`}>
                    {t.sequence}. {t.position_name ?? t.approver_tier}
                    {t.not_an_approver ? (
                      <span className="ml-1" title="Not a real approver - edit the rule and pick a position">· not an approver - pick a position</span>
                    ) : t.open_to_anyone && (
                      <span className="ml-1 text-amber-400">· {t.unstaffed_note ?? "nobody holds it"}</span>
                    )}
                  </span>
                  {i < r.tiers.length - 1 && <span className="text-slate-600">→</span>}
                </span>
              ))}
            </div>
          </div>
        ))}
        {shown.length === 0 && (
          <p className="text-sm text-slate-500">
            {kind === "monetary" ? "No discount approvals." : "No record approvals - every stage is reached without sign-off."}
          </p>
        )}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-4 text-base font-bold text-slate-100">{form.id ? "Edit rule" : kind === "monetary" ? "New discount approval" : "New record approval"}</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-xs text-slate-400">
            Name
            <input value={form.name} onChange={(e) => patch({ name: e.target.value })} className={INPUT}
              placeholder={kind === "monetary" ? "e.g. Deep discounts" : "e.g. Lead qualification"} />
          </label>
          {kind === "monetary" ? (
            <label className="text-xs text-slate-400">
              Applies to
              <select value={form.entityType} onChange={(e) => patch({ entityType: e.target.value })} className={INPUT}>
                {(options?.monetary_record_types ?? ["quote", "order"]).map((t) => (
                  <option key={t} value={t}>{monetaryLabel(options, t)}</option>
                ))}
              </select>
            </label>
          ) : (
            <label className="text-xs text-slate-400">
              Record
              <select
                value={form.entityType}
                onChange={(e) => patch({ entityType: e.target.value, triggerState: options?.record_types.find((t) => t.entity_type === e.target.value)?.states[0]?.code ?? "" })}
                className={INPUT}
              >
                <option value="">Choose the record…</option>
                {(options?.record_types ?? []).map((t) => <option key={t.entity_type} value={t.entity_type}>{ENTITY_LABEL[t.entity_type] ?? t.entity_type}</option>)}
              </select>
            </label>
          )}
          {kind === "monetary" ? (
            <>
              <label className="text-xs text-slate-400">
                Discount from %
                <input type="number" value={form.minPct} onChange={(e) => patch({ minPct: e.target.value })} className={INPUT} />
              </label>
              <label className="text-xs text-slate-400">
                Discount up to %
                <input type="number" value={form.maxPct} onChange={(e) => patch({ maxPct: e.target.value })} className={INPUT} />
              </label>
              <label className="text-xs text-slate-400">
                Deal value from (optional)
                <input type="number" value={form.minAmount} onChange={(e) => patch({ minAmount: e.target.value })} className={INPUT} />
              </label>
              <label className="text-xs text-slate-400">
                Deal value up to (optional)
                <input type="number" value={form.maxAmount} onChange={(e) => patch({ maxAmount: e.target.value })} className={INPUT} />
              </label>
              <label className="text-xs text-slate-400">
                Product segment
                <select value={form.segment} onChange={(e) => patch({ segment: e.target.value })} className={INPUT}>
                  <option value="">Any segment</option>
                  {(options?.segments ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
            </>
          ) : (
            <label className="text-xs text-slate-400">
              May only become
              <select value={form.triggerState} onChange={(e) => patch({ triggerState: e.target.value })} className={INPUT}>
                <option value="">Choose the stage…</option>
                {states.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
              </select>
            </label>
          )}
        </div>

        <div className="mt-4">
          <span className="text-xs text-slate-400">Approvers, in order - each step is decided by the people in that position</span>
          <div className="mt-1 space-y-2">
            {form.tiers.map((tier, i) => (
              <div key={i} className="flex gap-2">
                <span className="flex w-6 items-center justify-center text-xs text-slate-500">{i + 1}</span>
                <select
                  value={tier.positionId}
                  onChange={(e) => patch({ tiers: form.tiers.map((t, j) => (j === i ? { ...t, positionId: Number(e.target.value) } : t)) })}
                  className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
                >
                  <option value={0}>{tier.label ? `${tier.label} (anyone may decide - pick its position)` : "Choose a position…"}</option>
                  {(options?.positions ?? []).map((p) => (
                    <option key={p.id} value={p.id}>{p.name}{p.holders === 0 ? " - nobody yet" : ` - ${p.holders} ${p.holders === 1 ? "person" : "people"}`}</option>
                  ))}
                </select>
                {form.tiers.length > 1 && (
                  <button onClick={() => patch({ tiers: form.tiers.filter((_, j) => j !== i) })} className="rounded-md border border-slate-700 px-2.5 text-xs text-slate-400 hover:border-red-500 hover:text-red-400">
                    ✕
                  </button>
                )}
              </div>
            ))}
          </div>
          <button onClick={() => patch({ tiers: [...form.tiers, { positionId: 0, label: "" }] })} className={`mt-2 ${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>
            + Add a step
          </button>
        </div>

        {form.id && (
          <label className="mt-4 flex items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={form.applyToPending} onChange={(e) => patch({ applyToPending: e.target.checked })} />
            Approvals already waiting on this rule go to the new approvers too (for the steps not decided yet)
          </label>
        )}
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
        {saved && <p className="mt-3 text-xs text-emerald-400">{saved}</p>}
        <div className="mt-4 flex gap-2">
          <button onClick={submit} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">{form.id ? "Save rule" : "Add rule"}</button>
          {form.id && <button onClick={() => setForm(emptyRule(kind))} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300">Cancel</button>}
        </div>
      </div>

      <ApprovalLog />
    </div>
  );
}

/** Messages for the running bar on the Dashboard, My Work and Customer 360. */
export function BroadcastsSection() {
  const [items, setItems] = useState<Broadcast[]>([]);
  const [form, setForm] = useState({ text: "", level: "info", place: "all", ends_at: "" });
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    listBroadcasts().then(setItems).catch(() => setItems([]));
  };
  useEffect(load, []);
  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      load();
      return true;
    } catch (e) {
      setError(errorText(e, "That did not work."));
      return false;
    }
  }
  const PLACE: Record<string, string> = { all: "Everywhere", dashboard: "Dashboard", customer: "Customer 360", mywork: "My Work" };
  const TONE: Record<string, string> = { info: "text-sky-300", success: "text-emerald-300", warning: "text-amber-300", urgent: "text-red-300" };
  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Messages scroll in the <b className="text-slate-300">📣 Live</b> bar alongside what the system works out for each person: approvals
        waiting for them, decisions on their requests, and on Customer 360 that customer's pending approvals, urgent tickets and overdue bills.
      </p>
      <div className="space-y-2">
        {items.map((b) => (
          <div key={b.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
            <span className={`font-semibold ${TONE[b.level] ?? ""}`}>{b.text}</span>
            <span className="text-xs text-slate-500">
              {PLACE[b.place] ?? b.place} · {b.level}
              {b.ends_at ? ` · until ${b.ends_at}` : ""}
            </span>
            <button onClick={() => act(() => removeBroadcast(b.id))} className={`ml-auto ${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>
              Remove
            </button>
          </div>
        ))}
        {items.length === 0 && <p className="text-sm text-slate-500">No broadcast messages.</p>}
      </div>
      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-4 text-base font-bold text-slate-100">New message</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-xs text-slate-400 md:col-span-2">
            Message
            <input value={form.text} maxLength={300} onChange={(e) => setForm({ ...form, text: e.target.value })} className={INPUT}
              placeholder="e.g. Fibre maintenance in Riyadh tonight 22:00-02:00" />
          </label>
          <label className="text-xs text-slate-400">
            Importance
            <select value={form.level} onChange={(e) => setForm({ ...form, level: e.target.value })} className={INPUT}>
              {["info", "success", "warning", "urgent"].map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            Show on
            <select value={form.place} onChange={(e) => setForm({ ...form, place: e.target.value })} className={INPUT}>
              {Object.entries(PLACE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            Until (optional)
            <input type="date" value={form.ends_at} onChange={(e) => setForm({ ...form, ends_at: e.target.value })} className={INPUT} />
          </label>
        </div>
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
        <button
          onClick={async () => {
            if (!form.text.trim()) return setError("Write the message.");
            if (await act(() => addBroadcast({ text: form.text.trim(), level: form.level, place: form.place, ends_at: form.ends_at || null })))
              setForm({ text: "", level: "info", place: "all", ends_at: "" });
          }}
          className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500"
        >
          Publish
        </button>
      </div>
    </div>
  );
}

/** Audit trail: every approval decision - who asked, who decided, what, why and when. */
function ApprovalLog() {
  const [rows, setRows] = useState<ApprovalLogEntry[] | null>(null);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    approvalLog().then(setRows).catch(() => setRows([]));
  }, []);
  const shown = (rows ?? []).filter(
    (r) => !filter || `${r.entity_type} ${r.entity_id} ${r.rule} ${r.tier} ${r.decided_by} ${r.requested_by} ${r.summary}`.toLowerCase().includes(filter.toLowerCase())
  );
  const tone: Record<string, string> = { approved: "text-emerald-400", rejected: "text-red-400", conditional: "text-amber-400" };
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-bold text-slate-100">Approval log</h3>
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter…" className="w-56 rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-200" />
      </div>
      {rows === null ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-slate-500">No approval decisions yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-3">When</th>
                <th className="py-1 pr-3">Record</th>
                <th className="py-1 pr-3">Rule · step</th>
                <th className="py-1 pr-3">Asked by</th>
                <th className="py-1 pr-3">Decided by</th>
                <th className="py-1 pr-3">Decision</th>
                <th className="py-1">Reason</th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              {shown.map((r) => (
                <tr key={`${r.approval_id}-${r.sequence}`} className="border-t border-slate-800 align-top">
                  <td className="whitespace-nowrap py-1.5 pr-3">{new Date(r.decided_at).toLocaleString()}</td>
                  <td className="py-1.5 pr-3">
                    {ENTITY_LABEL[r.entity_type] ?? r.entity_type} #{r.entity_id}
                    {r.summary && <span className="block text-slate-500">{r.summary}</span>}
                  </td>
                  <td className="py-1.5 pr-3">{r.rule} · {r.sequence}. {r.tier}</td>
                  <td className="py-1.5 pr-3">{r.requested_by ?? "—"}</td>
                  <td className="py-1.5 pr-3">{r.decided_by ?? "—"}</td>
                  <td className={`py-1.5 pr-3 font-semibold ${tone[r.decision] ?? ""}`}>{r.decision}</td>
                  <td className="py-1.5 text-slate-400">{r.reasoning || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- pricelists

interface PriceListForm {
  id: number | null;
  name: string;
  currency: string;
  adjustment: string;
  isDefault: boolean;
  isActive: boolean;
  validFrom: string;
  validTo: string;
  items: { offering_name: string; price: string; minQty: string }[];
}

function emptyPriceList(): PriceListForm {
  return { id: null, name: "", currency: "INR", adjustment: "0", isDefault: false, isActive: true, validFrom: "", validTo: "", items: [] };
}

function effectiveText(l: PriceList): string {
  if (!l.valid_from && !l.valid_to) return "";
  if (l.valid_from && l.valid_to) return `${l.valid_from} → ${l.valid_to}`;
  return l.valid_from ? `from ${l.valid_from}` : `until ${l.valid_to}`;
}

export function PriceListsSection() {
  const [lists, setLists] = useState<PriceList[]>([]);
  const [offerings, setOfferings] = useState<Offering[]>([]);
  const [form, setForm] = useState<PriceListForm>(emptyPriceList());
  const [error, setError] = useState<string | null>(null);

  function load() {
    listPriceLists().then(setLists).catch(() => setLists([]));
  }

  useEffect(() => {
    load();
    listCatalog().then((rows) => setOfferings(rows.filter((o) => o.status === "active"))).catch(() => setOfferings([]));
  }, []);

  function toForm(l: PriceList): PriceListForm {
    return { id: l.id, name: l.name, currency: l.currency, adjustment: String(l.adjustment_pct), isDefault: l.is_default, isActive: l.is_active,
      validFrom: l.valid_from ?? "", validTo: l.valid_to ?? "",
      items: l.items.map((i) => ({ offering_name: i.offering_name, price: String(i.price), minQty: String(i.min_quantity ?? 1) })) };
  }

  async function save(f: PriceListForm) {
    setError(null);
    if (!f.name.trim()) {
      setError("Give the pricelist a name.");
      return;
    }
    const payload = {
      name: f.name.trim(),
      currency: f.currency,
      adjustment_pct: Number(f.adjustment),
      is_default: f.isDefault,
      is_active: f.isActive,
      valid_from: f.validFrom || null,
      valid_to: f.validTo || null,
      items: f.items.filter((i) => i.offering_name && i.price !== "")
        .map((i) => ({ offering_name: i.offering_name, price: Number(i.price), min_quantity: Math.max(1, Math.floor(Number(i.minQty) || 1)) })),
    };
    try {
      if (f.id) await updatePriceList(f.id, payload);
      else await createPriceList(payload);
      setForm(emptyPriceList());
      load();
    } catch (e) {
      setError(errorText(e, "Could not save the pricelist."));
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        What each B2B account pays. A prospect gets its pricelist when it is converted to an account; quotes are priced from it - an
        explicit price for a product, otherwise the list price adjusted by the pricelist's percentage. Accounts without one use the default.
      </p>
      <div className="space-y-2">
        {lists.map((l) => (
          <div key={l.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${l.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 opacity-60"}`}>
            <span className="font-semibold text-slate-200">{l.name}</span>
            {l.is_default && <span className="rounded-md bg-sky-500/15 px-2 py-0.5 text-xs text-sky-300">default</span>}
            {!l.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">off</span>}
            {l.is_active && l.in_effect === false && (
              <span className="rounded-md bg-amber-500/15 px-2 py-0.5 text-xs text-amber-300">not in effect today</span>
            )}
            <span className="text-xs text-slate-500">
              {l.adjustment_pct ? `${l.adjustment_pct > 0 ? "+" : ""}${l.adjustment_pct}% on list price` : "list price"}
              {l.items.length > 0 && ` · ${l.items.length} product price${l.items.length > 1 ? "s" : ""}`} · {l.currency}
              {effectiveText(l) && ` · ${effectiveText(l)}`}
            </span>
            <div className="ml-auto flex gap-2">
              <button onClick={() => setForm(toForm(l))} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>Edit</button>
              <button onClick={() => save({ ...toForm(l), isActive: !l.is_active })} className={`${SMALL_BTN} hover:border-amber-500 hover:text-amber-400`}>
                {l.is_active ? "Switch off" : "Switch on"}
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-4 text-base font-bold text-slate-100">{form.id ? `Edit ${form.name}` : "New pricelist"}</h3>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="text-xs text-slate-400">
            Name
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={INPUT} placeholder="e.g. Government" />
          </label>
          <label className="text-xs text-slate-400">
            Adjustment on list price %
            <input type="number" value={form.adjustment} onChange={(e) => setForm({ ...form, adjustment: e.target.value })} className={INPUT} />
          </label>
          <label className="text-xs text-slate-400">
            Currency
            <select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} className={INPUT}>
              {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            In effect from
            <input type="date" value={form.validFrom} onChange={(e) => setForm({ ...form, validFrom: e.target.value })} className={INPUT} />
          </label>
          <label className="text-xs text-slate-400">
            In effect until
            <input type="date" value={form.validTo} onChange={(e) => setForm({ ...form, validTo: e.target.value })} className={INPUT} />
          </label>
        </div>
        <p className="mt-1 text-xs text-slate-500">Leave the dates empty for a pricelist that is always in effect. Outside its dates, accounts fall back to the default pricelist.</p>
        <label className="mt-3 flex items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" checked={form.isDefault} onChange={(e) => setForm({ ...form, isDefault: e.target.checked })} />
          Default for accounts without a pricelist
        </label>
        <div className="mt-4 space-y-2">
          <span className="text-xs text-slate-400">
            Product prices (optional) - add the same product again with a higher “from units” for a volume price
          </span>
          {form.items.map((item, i) => (
            <div key={i} className="flex gap-2">
              <select value={item.offering_name} onChange={(e) => setForm({ ...form, items: form.items.map((x, j) => (j === i ? { ...x, offering_name: e.target.value } : x)) })}
                className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
                <option value="">Choose a product…</option>
                {offerings.map((o) => <option key={o.id} value={o.name}>{o.name}</option>)}
              </select>
              <input type="number" value={item.price} onChange={(e) => setForm({ ...form, items: form.items.map((x, j) => (j === i ? { ...x, price: e.target.value } : x)) })}
                className="w-36 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="Monthly price" />
              <input type="number" min={1} value={item.minQty} title="Volume tier: this price applies from this many units up"
                onChange={(e) => setForm({ ...form, items: form.items.map((x, j) => (j === i ? { ...x, minQty: e.target.value } : x)) })}
                className="w-28 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="From units" />
              <button onClick={() => setForm({ ...form, items: form.items.filter((_, j) => j !== i) })} className="rounded-md border border-slate-700 px-2.5 text-xs text-slate-400">✕</button>
            </div>
          ))}
          <button onClick={() => setForm({ ...form, items: [...form.items, { offering_name: "", price: "", minQty: "1" }] })} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>
            + Price a product
          </button>
        </div>
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
        <div className="mt-4 flex gap-2">
          <button onClick={() => save(form)} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">{form.id ? "Save" : "Add pricelist"}</button>
          {form.id && <button onClick={() => setForm(emptyPriceList())} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300">Cancel</button>}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- quote documents

const DOC_HELP: Record<string, { on: string; off: string }> = {
  proposal: {
    on: "After the discount is approved the quote offers “Generate proposal”: this PDF lands on the quote, is checked, then sent to the customer.",
    off: "No proposal step - after approval the quote goes straight to the customer's PO (a proposal can still be sent, as before).",
  },
  agreement: {
    on: "When the Sales Ops Head runs Match all, the quote becomes an order and this agreement PDF is attached to the quote.",
    off: "Match all creates the order without an agreement document.",
  },
};
const STO_STEPS = ["Feasibility passed", "Quote with discount", "Discount approval", "Generate proposal (PDF)", "Send to customer",
  "Customer's PO (PDF)", "Match all - Sales Ops Head", "Order + agreement (PDF)"];

export function QuoteDocumentsSection() {
  const [templates, setTemplates] = useState<QuoteDocumentTemplate[]>([]);
  const [placeholders, setPlaceholders] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Record<string, QuoteDocumentTemplate>>({});
  const [focus, setFocus] = useState<{ kind: string; field: "title" | "intro" | "terms" | "closing" } | null>(null);
  const [message, setMessage] = useState<{ kind: string; text: string; error?: boolean } | null>(null);

  function load() {
    listQuoteDocumentTemplates()
      .then((r) => {
        setTemplates(r.templates);
        setPlaceholders(r.placeholders);
        setDrafts(Object.fromEntries(r.templates.map((t) => [t.kind, t])));
      })
      .catch(() => setTemplates([]));
  }
  useEffect(load, []);

  const patch = (kind: string, p: Partial<QuoteDocumentTemplate>) => setDrafts((d) => ({ ...d, [kind]: { ...d[kind], ...p } }));

  async function save(kind: string, body: Parameters<typeof saveQuoteDocumentTemplate>[1], text: string) {
    setMessage(null);
    try {
      const saved = await saveQuoteDocumentTemplate(kind, body);
      setTemplates((ts) => ts.map((t) => (t.kind === kind ? saved : t)));
      setDrafts((d) => ({ ...d, [kind]: saved }));
      setMessage({ kind, text });
    } catch (e) {
      setMessage({ kind, text: errorText(e, "Could not save."), error: true });
    }
  }

  async function preview(kind: string) {
    setMessage(null);
    const tab = window.open("", "_blank");
    try {
      const d = drafts[kind];
      // what is on screen is what is previewed - save first when it changed
      const original = templates.find((t) => t.kind === kind);
      if (original && (["title", "intro", "terms", "closing"] as const).some((k) => d[k] !== original[k])) {
        await save(kind, { title: d.title, intro: d.intro, terms: d.terms, closing: d.closing }, "Saved - preview opened in a new tab.");
      }
      const url = URL.createObjectURL(await previewQuoteDocumentTemplate(kind));
      if (tab) tab.location.href = url;
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      tab?.close();
      setMessage({ kind, text: errorText(e, "Could not preview it.").replace(/^\d+ [^:]*: /, ""), error: true });
    }
  }

  function insert(token: string) {
    if (!focus) return;
    const d = drafts[focus.kind];
    patch(focus.kind, { [focus.field]: `${d[focus.field]}${d[focus.field] && !/\s$/.test(d[focus.field]) ? " " : ""}{${token}}` });
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        The PDFs a B2B quote gets on its way to an order. Every PDF - generated here or attached by your people (the customer's PO, a signed
        copy) - is kept on the quote itself: open any quote to see them. Write the wording below; <code className="text-sky-300">{"{customer}"}</code>-style
        placeholders are filled in from the quote.
      </p>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-[11px]">
        {STO_STEPS.map((s, i) => (
          <span key={s} className="flex items-center gap-1.5">
            <span className={`rounded-md px-2 py-0.5 ${/PDF/.test(s) ? "bg-sky-500/15 text-sky-300" : "bg-slate-800 text-slate-300"}`}>{s}</span>
            {i < STO_STEPS.length - 1 && <span className="text-slate-600">→</span>}
          </span>
        ))}
        <span className="basis-full pt-1 text-slate-500">
          The steps and what comes next after each one are yours to change in the Functional Map (links and conditions), Flow Studio and Intents.
        </span>
      </div>

      {templates.map((t) => {
        const d = drafts[t.kind] ?? t;
        const changed = (["title", "intro", "terms", "closing"] as const).some((k) => d[k] !== t[k]);
        const field = (key: "title" | "intro" | "terms" | "closing", label: string, rows: number) => (
          <label className="block text-xs text-slate-400">
            {label}
            {rows === 1 ? (
              <input value={d[key]} onFocus={() => setFocus({ kind: t.kind, field: key })} onChange={(e) => patch(t.kind, { [key]: e.target.value })} className={INPUT} />
            ) : (
              <textarea rows={rows} value={d[key]} onFocus={() => setFocus({ kind: t.kind, field: key })} onChange={(e) => patch(t.kind, { [key]: e.target.value })}
                className={`${INPUT} font-mono text-xs leading-relaxed`} />
            )}
          </label>
        );
        return (
          <div key={t.kind} className={`rounded-xl border p-5 ${t.enabled ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60"}`}>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h3 className="text-base font-bold text-slate-100">{t.kind === "proposal" ? "📄" : "📜"} {t.label}</h3>
              {t.customized ? (
                <span className="rounded-md bg-violet-500/15 px-2 py-0.5 text-[11px] text-violet-300">your wording{t.updated_at ? ` · ${new Date(t.updated_at).toLocaleDateString()}` : ""}</span>
              ) : (
                <span className="rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-400">vanilla wording</span>
              )}
              <label className="ml-auto flex items-center gap-2 text-xs text-slate-300">
                <input type="checkbox" checked={t.enabled} onChange={(e) => save(t.kind, { enabled: e.target.checked }, e.target.checked ? "Switched on." : "Switched off.")} />
                In the process
              </label>
            </div>
            <p className="mb-4 text-xs text-slate-500">{t.enabled ? DOC_HELP[t.kind].on : DOC_HELP[t.kind].off}</p>
            <div className="grid gap-3">
              {field("title", "Title", 1)}
              {field("intro", "Opening", 5)}
              <p className="rounded-md border border-dashed border-slate-800 px-3 py-2 text-[11px] text-slate-500">
                Then, written from the quote: the customer and quote details, the products with their options, quantities and prices, the discount and the total
                {t.kind === "agreement" ? ", and the acceptance - PO, Sales Ops' match, the order and the agreement number." : "."}
              </p>
              {field("terms", "Terms", 5)}
              {field("closing", t.kind === "agreement" ? "Signatures" : "Closing", 3)}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-1">
              <span className="mr-1 text-[11px] text-slate-500">{focus?.kind === t.kind ? "Insert into the field you're in:" : "Placeholders (click into a field first):"}</span>
              {placeholders.map((p) => (
                <button key={p} onClick={() => insert(p)} disabled={focus?.kind !== t.kind}
                  className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-sky-300 hover:border-sky-500 disabled:opacity-40">
                  {`{${p}}`}
                </button>
              ))}
            </div>
            {message?.kind === t.kind && <p className={`mt-3 text-xs ${message.error ? "text-red-400" : "text-emerald-400"}`}>{message.text}</p>}
            <div className="mt-4 flex flex-wrap gap-2">
              <button onClick={() => save(t.kind, { title: d.title, intro: d.intro, terms: d.terms, closing: d.closing }, "Saved - new documents use this wording.")}
                disabled={!changed} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-40">
                Save
              </button>
              <button onClick={() => preview(t.kind)} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:border-sky-500 hover:text-sky-300"
                title="Written for your newest quote">
                Preview PDF
              </button>
              {changed && (
                <button onClick={() => patch(t.kind, t)} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-400">Undo changes</button>
              )}
              {t.customized && (
                <button onClick={() => save(t.kind, { reset: true }, "Back to the vanilla wording.")}
                  className="ml-auto rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-400 hover:border-amber-500 hover:text-amber-400">
                  Back to vanilla
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- process roles

const ROLE_HELP: Record<string, string> = {
  account_manager: "Owns prospects, leads, opportunities and quotes; gets the opportunity back after Presales.",
  presales: "Receives configured opportunities for the feasibility check.",
  sales_ops: "Receives quotes with the customer's PO and runs Match all - a full match creates the order and its agreement.",
  cpmo: "Receives new orders for approval before they go to fulfilment.",
  bsd: "The installation team that closes WFM1 once an order is approved.",
};

export function ProcessRolesSection() {
  const [roles, setRoles] = useState<ProcessRole[]>([]);
  const [options, setOptions] = useState<ApprovalRuleOptions | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProcessRoles().then(setRoles).catch(() => setRoles([]));
    approvalRuleOptions().then(setOptions).catch(() => setOptions(null));
  }, []);

  function change(roleKey: string, positionId: number | null) {
    setError(null);
    setProcessRole(roleKey, positionId).then(setRoles).catch((e) => setError(errorText(e, "Could not save.")));
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Who does each step of the B2B Sales-to-Order process. When a step hands a record over - to Presales, to Sales Ops, to CPMO, to the
        installation team - it goes to the position below. Your own assignment rules and flows still run on top.
      </p>
      <div className="divide-y divide-slate-800 rounded-xl border border-slate-700 bg-slate-900/60">
        {roles.map((r) => (
          <div key={r.role_key} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <div className="min-w-[220px] flex-1">
              <div className="text-sm font-semibold text-slate-200">{r.label}</div>
              <div className="text-xs text-slate-500">{ROLE_HELP[r.role_key]}</div>
            </div>
            <select
              value={r.position_id ?? 0}
              onChange={(e) => change(r.role_key, Number(e.target.value) || null)}
              className="w-64 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
            >
              <option value={0}>Nobody (no hand-over)</option>
              {(options?.positions ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        ))}
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  );
}
