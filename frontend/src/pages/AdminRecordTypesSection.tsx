import { useCallback, useEffect, useState } from "react";
import { createCustomField, deactivateCustomField, deleteCustomField, deletePageRule, listCustomFields, listFlows, listPageRules } from "../api/client";
import type { PageRule } from "../api/client";
import {
  addRecordRule,
  buildRecordType,
  createRecordType,
  createRelationship,
  deleteRecordType,
  deleteRelationship,
  designRecordType,
  listRecordTypes,
  problem,
  saveRecordStates,
  updateRecordType,
} from "../api/customRecords";
import type { DesignPlan, LifecycleMove, LifecycleStateDef, RecordType, Relationship } from "../api/customRecords";
import type { CustomFieldDefinition, Flow } from "../api/types";

/** Admin -> Record Types: the tenant's own record types ("custom objects"). Describe one in plain words and the
 *  AI drafts it - fields, lookups, states, relationships, rules - or build it by hand. Each type then runs on the
 *  platform's own engines: its page and rules (Page Designer), its state model, its flows (Flow Studio), its
 *  intents (INT-151..157, in the command bar too) and the Audit Log. */

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 disabled:opacity-60";
const CELL = "w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200";
const SMALL_BTN = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-slate-500 disabled:opacity-50";
const BTN_PRIMARY = "rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50";
const CARD = "rounded-xl border border-slate-800 bg-slate-900/40 p-4";
// what custom_field_service validates (models/custom_fields.py CUSTOM_FIELD_DATA_TYPES)
const DATA_TYPES = [
  ["text", "Text"], ["textarea", "Long text"], ["number", "Number"], ["date", "Date"], ["boolean", "Yes / no"],
  ["picklist", "Pick list"], ["email", "Email"], ["phone", "Phone"], ["lookup", "Link to a record"],
] as const;
const typeName = (t: string) => DATA_TYPES.find(([v]) => v === t)?.[1] ?? t;

type Option = { value: string; label: string };
type AdminSection = "pages" | "flows" | "lifecycle" | "assignment";

function slug(text: string, max = 28): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, max).replace(/_+$/, "");
}

export default function AdminRecordTypesSection({ onOpen }: { onOpen?: (section: AdminSection) => void }) {
  const [types, setTypes] = useState<RecordType[]>([]);
  const [options, setOptions] = useState<Option[]>([]);
  const [selected, setSelected] = useState<string | "new" | "describe" | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listRecordTypes()
      .then((d) => {
        setTypes(d.types);
        setOptions(d.entity_options);
      })
      .catch((e) => setError(problem(e, "Could not load the record types.")));
  }, []);
  useEffect(load, [load]);

  const current = types.find((t) => t.key === selected) ?? null;
  return (
    <div className="space-y-5">
      <p className="text-sm text-slate-400">
        Record types of your own - a Site Survey, a Contract, an Asset Inspection. Give them fields and links to other records, states to move
        through, relationships, validation rules and automations; their records are kept on the <span className="font-semibold text-slate-300">Custom Records</span> page,
        can be created and moved in plain words from the command bar, follow the user's data scope, and every change is in the Audit Log.
      </p>
      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="space-y-2">
          <button onClick={() => setSelected("describe")} className={`${BTN_PRIMARY} w-full`}>✨ Describe a record type</button>
          <button onClick={() => setSelected("new")} className={`${SMALL_BTN} w-full py-1.5`}>+ Build one by hand</button>
          {types.map((t) => (
            <button
              key={t.key}
              onClick={() => setSelected(t.key)}
              className={`block w-full rounded-lg border px-3 py-2 text-left ${t.key === selected ? "border-sky-500 bg-sky-500/10" : "border-slate-800 bg-slate-900/50 hover:border-slate-600"} ${t.is_active ? "" : "opacity-60"}`}
            >
              <span className="block text-sm font-semibold text-slate-100">{t.icon} {t.label}</span>
              <span className="block text-[11px] text-slate-500">
                {t.fields.length} field{t.fields.length === 1 ? "" : "s"} · {t.states.length} states · {t.record_count} record{t.record_count === 1 ? "" : "s"}
                {t.parent_label ? ` · under ${t.parent_label}` : ""}
                {t.is_active ? "" : " · off"}
              </span>
            </button>
          ))}
          {types.length === 0 && <p className="text-sm text-slate-500">None yet.</p>}
        </div>

        <div className="min-w-0">
          {selected === "describe" && (
            <Designer onBuilt={(key) => { load(); setSelected(key); }} />
          )}
          {selected === "new" && (
            <TypeEditor key="new" type={null} options={options} onSaved={(t) => { load(); setSelected(t.key); }} />
          )}
          {current && <TypeWorkspace key={current.key} type={current} types={types} options={options} onChanged={load} onDeleted={() => { setSelected(null); load(); }} onOpen={onOpen} />}
          {selected === null && <p className="text-sm text-slate-500">Pick a record type, describe a new one, or build one by hand.</p>}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- AI designer

const EXAMPLE = "Site Survey for a customer (required): survey date (date, required), floors (number), outcome (Pending, Feasible, Not feasible), "
  + "report (long text). States: scheduled -> in progress -> completed, cancelled. Rules: if State is completed then Report is mandatory.";

function Designer({ onBuilt }: { onBuilt: (key: string) => void }) {
  const [text, setText] = useState("");
  const [plan, setPlan] = useState<DesignPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<{ notes: string[]; summary: string } | null>(null);

  async function draft() {
    setBusy(true);
    setError("");
    setReport(null);
    try {
      setPlan(await designRecordType({ text }));
    } catch (e) {
      setError(problem(e, "Couldn't draft it."));
    } finally {
      setBusy(false);
    }
  }

  async function build() {
    if (!plan) return;
    setBusy(true);
    setError("");
    try {
      const out = await buildRecordType({ plan: { ...plan, source_text: text } });
      setReport({ notes: out.notes, summary: out.summary });
      onBuilt(out.record_type_key);
    } catch (e) {
      setError(problem(e, "Couldn't build it."));
    } finally {
      setBusy(false);
    }
  }

  const edit = (patch: (p: DesignPlan) => DesignPlan) => setPlan((p) => (p ? patch(structuredClone(p)) : p));
  return (
    <section className={`${CARD} space-y-4`}>
      <div>
        <h2 className="text-base font-semibold text-slate-100">✨ Describe a record type</h2>
        <p className="mt-0.5 text-xs text-slate-400">
          Say what it is, what it belongs to, its fields (a field can link to another record), its states, relationships and rules - in your own words.
          The AI drafts it; nothing is saved until you build it. Naming a type you already have adds what it lacks.
        </p>
      </div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} placeholder={EXAMPLE} className={INPUT} />
      <div className="flex flex-wrap gap-2">
        <button onClick={draft} disabled={busy || text.trim().length < 3} className={BTN_PRIMARY}>{busy && !plan ? "Drafting…" : plan ? "Draft again" : "Draft it"}</button>
        {!text && <button onClick={() => setText(EXAMPLE)} className={SMALL_BTN}>Use the example</button>}
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}

      {plan && (
        <div className="space-y-4 border-t border-slate-800 pt-4">
          <p className="text-xs text-slate-500">
            {plan.type.existing ? `Adds to the existing ${plan.type.label}` : "A new record type"} · drafted by {plan.engine === "ai" ? "your AI model" : "the built-in reader"}
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block text-xs text-slate-400">Name
              <input value={plan.type.label} disabled={plan.type.existing} onChange={(e) => edit((p) => ({ ...p, type: { ...p.type, label: e.target.value } }))} className={INPUT} />
            </label>
            <label className="block text-xs text-slate-400">Plural
              <input value={plan.type.plural_label} disabled={plan.type.existing} onChange={(e) => edit((p) => ({ ...p, type: { ...p.type, plural_label: e.target.value } }))} className={INPUT} />
            </label>
            <label className="block text-xs text-slate-400">Belongs to
              <input value={plan.type.parent_entity_type ? `${plan.type.parent_entity_type}${plan.type.parent_required ? " (required)" : ""}` : "nothing - stands alone"} disabled className={INPUT} />
            </label>
          </div>

          <div>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Fields</h3>
            <div className="space-y-1.5">
              {plan.fields.map((f, i) => (
                <div key={f.key} className="grid grid-cols-[minmax(0,1fr)_9rem_minmax(0,1fr)_auto_auto] items-center gap-2 text-xs">
                  <input value={f.label} onChange={(e) => edit((p) => { p.fields[i].label = e.target.value; return p; })} className={CELL} disabled={f.exists} />
                  <span className="text-slate-400">{typeName(f.data_type)}{f.target ? ` → ${f.target}` : ""}</span>
                  <span className="truncate text-slate-500">{f.values.join(", ")}</span>
                  <label className="flex items-center gap-1 text-slate-400">
                    <input type="checkbox" checked={f.required} disabled={f.exists} onChange={(e) => edit((p) => { p.fields[i].required = e.target.checked; return p; })} /> required
                  </label>
                  {f.exists ? <span className="text-slate-500">has it</span> : (
                    <button onClick={() => edit((p) => { p.fields.splice(i, 1); return p; })} className="text-slate-500 hover:text-red-400" aria-label={`Leave out ${f.label}`}>✕</button>
                  )}
                </div>
              ))}
              {plan.fields.length === 0 && <p className="text-xs text-slate-500">No fields - say them, e.g. "survey date (date), floors (number)".</p>}
            </div>
          </div>

          {plan.states.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Lifecycle</h3>
              <p className="flex flex-wrap items-center gap-1.5 text-xs">
                {plan.states.map((s, i) => (
                  <span key={s.code} className="flex items-center gap-1.5">
                    {i > 0 && <span className="text-slate-600">·</span>}
                    <span className={`rounded-full border px-2 py-0.5 ${s.is_terminal ? "border-slate-600 text-slate-400" : s.editable ? "border-sky-500/40 text-sky-300" : "border-emerald-500/40 text-emerald-300"}`}>{s.label}</span>
                  </span>
                ))}
              </p>
              <p className="mt-1 text-[11px] text-slate-500">{plan.transitions.map((t) => `${t.from_state === "*" ? "any" : t.from_state} → ${t.to_state} (${t.action})`).join(" · ")}</p>
            </div>
          )}
          {plan.relationships.length > 0 && (
            <p className="text-xs text-slate-300"><span className="font-semibold uppercase tracking-wide text-slate-500">Related to </span>
              {plan.relationships.map((r) => `${r.to_label} as “${r.label}”`).join(", ")}</p>
          )}
          {plan.rules.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Rules</h3>
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-300">{plan.rules.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
          )}
          {plan.notes.length > 0 && <ul className="space-y-0.5 text-xs text-amber-300">{plan.notes.map((n) => <li key={n}>• {n}</li>)}</ul>}
          <button onClick={build} disabled={busy || !plan.type.label.trim()} className={BTN_PRIMARY}>{busy ? "Building…" : plan.type.existing ? `Add to ${plan.type.label}` : `Build ${plan.type.label}`}</button>
          {report && (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
              Built: {report.summary}
              {report.notes.map((n) => <p key={n} className="text-amber-300">• {n}</p>)}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- one type

type Tab = "settings" | "fields" | "lifecycle" | "relationships" | "rules" | "automations";

function TypeWorkspace({ type, types, options, onChanged, onDeleted, onOpen }: {
  type: RecordType; types: RecordType[]; options: Option[]; onChanged: () => void; onDeleted: () => void; onOpen?: (s: AdminSection) => void;
}) {
  const [tab, setTab] = useState<Tab>("fields");
  const tabs: [Tab, string][] = [["fields", `Fields · ${type.fields.length}`], ["lifecycle", `Lifecycle · ${type.states.length}`],
    ["relationships", `Relationships · ${type.relationships.length}`], ["rules", "Page & rules"], ["automations", "Automations"], ["settings", "Settings"]];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-2 text-lg font-semibold text-slate-100">{type.icon} {type.label}</h2>
        <span className="font-mono text-[11px] text-slate-500">{type.entity_type} · {type.number_prefix}-0001</span>
      </div>
      <div className="flex flex-wrap gap-1 border-b border-slate-800 pb-2" role="tablist">
        {tabs.map(([t, l]) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1 text-sm ${tab === t ? "bg-slate-800 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
        ))}
      </div>
      {tab === "settings" && <TypeEditor type={type} options={options.filter((p) => p.value !== type.entity_type)} onSaved={onChanged} onDeleted={onDeleted} />}
      {tab === "fields" && <FieldsEditor type={type} options={options} onChanged={onChanged} />}
      {tab === "lifecycle" && <StatesEditor type={type} onChanged={onChanged} />}
      {tab === "relationships" && <RelationshipsEditor type={type} options={options} onChanged={onChanged} />}
      {tab === "rules" && <RulesEditor type={type} onOpen={onOpen} />}
      {tab === "automations" && <Automations type={type} types={types} onOpen={onOpen} />}
    </div>
  );
}

type TypeForm = { label: string; plural_label: string; key: string; icon: string; number_prefix: string; description: string; parent_entity_type: string; parent_required: boolean; name_label: string; list_fields: string[] };

function formOf(t: RecordType | null): TypeForm {
  return {
    label: t?.label ?? "", plural_label: t?.plural_label ?? "", key: t?.key ?? "", icon: t?.icon ?? "📄", number_prefix: t?.number_prefix ?? "",
    description: t?.description ?? "", parent_entity_type: t?.parent_entity_type ?? "", parent_required: t?.parent_required ?? false,
    name_label: t && t.name_label !== "Name" ? t.name_label : "", list_fields: t?.list_fields ?? [],
  };
}

function TypeEditor({ type, options, onSaved, onDeleted }: {
  type: RecordType | null;
  options: Option[];
  onSaved: (t: RecordType) => void;
  onDeleted?: () => void;
}) {
  const [form, setForm] = useState<TypeForm>(() => formOf(type));
  const [keyTouched, setKeyTouched] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const set = (patch: Partial<TypeForm>) => setForm((f) => ({ ...f, ...patch }));
  const isNew = type === null;

  async function run(action: () => Promise<RecordType | void>) {
    setBusy(true);
    setError("");
    try {
      const out = await action();
      if (out) onSaved(out);
    } catch (e) {
      setError(problem(e, "Could not save."));
    } finally {
      setBusy(false);
    }
  }

  const body = {
    label: form.label.trim(), plural_label: form.plural_label.trim() || null, icon: form.icon.trim() || null,
    number_prefix: form.number_prefix.trim() || null, description: form.description, parent_entity_type: form.parent_entity_type || null,
    parent_required: form.parent_required, name_label: form.name_label.trim() || null, ...(type ? { list_fields: form.list_fields } : {}),
  };
  const columnChoices = type ? [["state", "State"], ["owner", "Owner"], ...type.fields.map((f) => [f.key, f.label])] : [];
  return (
    <section className={CARD}>
      <h2 className="mb-3 text-base font-semibold text-slate-100">{type ? "Settings" : "New record type"}</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs text-slate-400">
          Name
          <input value={form.label} onChange={(e) => set({ label: e.target.value, ...(isNew && !keyTouched ? { key: slug(e.target.value) } : {}) })} placeholder="Site Survey" className={INPUT} />
        </label>
        <label className="block text-xs text-slate-400">
          Plural
          <input value={form.plural_label} onChange={(e) => set({ plural_label: e.target.value })} placeholder={form.label ? `${form.label}s` : "Site Surveys"} className={INPUT} />
        </label>
        <label className="block text-xs text-slate-400">
          Key {isNew ? "(can't change later)" : ""}
          <input value={form.key} disabled={!isNew} onChange={(e) => { setKeyTouched(true); set({ key: slug(e.target.value) }); }} className={`${INPUT} font-mono`} />
        </label>
        <div className="grid grid-cols-[5rem_minmax(0,1fr)] gap-3">
          <label className="block text-xs text-slate-400">
            Icon
            <input value={form.icon} onChange={(e) => set({ icon: e.target.value })} maxLength={10} className={INPUT} />
          </label>
          <label className="block text-xs text-slate-400">
            Number prefix
            <input value={form.number_prefix} onChange={(e) => set({ number_prefix: e.target.value.toUpperCase() })} placeholder="SS → SS-0001" maxLength={10} className={`${INPUT} font-mono`} />
          </label>
        </div>
        <label className="block text-xs text-slate-400">
          Records hang off
          <select value={form.parent_entity_type} disabled={!!type && type.record_count > 0} onChange={(e) => set({ parent_entity_type: e.target.value, parent_required: e.target.value ? form.parent_required : false })} className={INPUT}>
            <option value="">Nothing - they stand alone</option>
            {options.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </label>
        <label className="mt-6 flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={form.parent_required} disabled={!form.parent_entity_type} onChange={(e) => set({ parent_required: e.target.checked })} />
          Every record needs one
        </label>
        <label className="block text-xs text-slate-400">
          What its name is called
          <input value={form.name_label} onChange={(e) => set({ name_label: e.target.value })} placeholder="Name" className={INPUT} />
        </label>
        <label className="block text-xs text-slate-400 sm:col-span-2">
          Description
          <textarea value={form.description} onChange={(e) => set({ description: e.target.value })} rows={2} className={INPUT} />
        </label>
        {type && (
          <div className="text-xs text-slate-400 sm:col-span-2">
            Columns of the list
            <div className="mt-1 flex flex-wrap gap-1.5">
              {columnChoices.map(([key, label]) => {
                const on = form.list_fields.includes(key);
                return (
                  <button key={key} type="button" onClick={() => set({ list_fields: on ? form.list_fields.filter((k) => k !== key) : [...form.list_fields, key] })}
                    className={`rounded-full border px-2.5 py-1 ${on ? "border-sky-500 bg-sky-500/10 text-sky-300" : "border-slate-700 text-slate-400"}`}>{label}</button>
                );
              })}
            </div>
          </div>
        )}
      </div>
      {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button disabled={busy || !form.label.trim()} onClick={() => run(() => (type ? updateRecordType(type.key, body) : createRecordType({ ...body, key: form.key || null })))} className={BTN_PRIMARY}>
          {type ? "Save" : "Create"}
        </button>
        {type && (
          <>
            <button disabled={busy} onClick={() => run(() => updateRecordType(type.key, { is_active: !type.is_active }))} className={`${SMALL_BTN} hover:border-amber-500 hover:text-amber-400`}>
              {type.is_active ? "Switch off" : "Switch on"}
            </button>
            {!confirmDelete ? (
              <button disabled={busy} onClick={() => setConfirmDelete(true)} className={`${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>Delete</button>
            ) : (
              <span className="flex items-center gap-2 text-xs text-red-300">
                Delete the type, its fields, states, page and rules?
                <button onClick={() => run(async () => { await deleteRecordType(type.key); onDeleted?.(); })} className="rounded-md bg-red-600 px-2.5 py-1 font-bold text-white hover:bg-red-500">Delete</button>
                <button onClick={() => setConfirmDelete(false)} className={SMALL_BTN}>Keep</button>
              </span>
            )}
          </>
        )}
      </div>
      {type && type.record_count > 0 && <p className="mt-2 text-[11px] text-slate-500">What its records hang off is fixed once records exist; a type with records can be switched off but not deleted.</p>}
    </section>
  );
}

function emptyField() {
  return { label: "", key: "", dataType: "text", allowedValues: "", target: "", required: false, defaultValue: "" };
}

function FieldsEditor({ type, options, onChanged }: { type: RecordType; options: Option[]; onChanged: () => void }) {
  const [fields, setFields] = useState<CustomFieldDefinition[]>([]);
  const [form, setForm] = useState(emptyField());
  const [keyTouched, setKeyTouched] = useState(false);
  const [error, setError] = useState("");
  const label = (v: string | null) => options.find((o) => o.value === v)?.label ?? v;

  const load = useCallback(() => {
    listCustomFields(type.entity_type).then(setFields).catch((e) => setError(problem(e, "Could not load the fields.")));
  }, [type.entity_type]);
  useEffect(load, [load]);

  async function run(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      load();
      onChanged();
      return true;
    } catch (e) {
      setError(problem(e, "Could not save the field."));
      return false;
    }
  }

  async function add() {
    const ok = await run(() =>
      createCustomField({
        entity_type: type.entity_type, key: form.key, label: form.label.trim(), data_type: form.dataType,
        allowed_values: form.dataType === "picklist" ? form.allowedValues.trim() : form.dataType === "lookup" ? form.target : null, required: form.required,
        default_value: form.dataType === "lookup" ? null : form.defaultValue.trim() || null,
      })
    );
    if (ok) {
      setForm(emptyField());
      setKeyTouched(false);
    }
  }

  return (
    <section className={CARD}>
      <p className="mb-3 text-xs text-slate-400">
        Every {type.label.toLowerCase()} also has a number ({type.number_prefix}-0001), a {type.name_label.toLowerCase()}, a state and an owner. A field that
        links to a record (a customer, an order, another of your types) is a lookup - that record lists it under Related.
      </p>
      <div className="space-y-2">
        {fields.map((f) => (
          <div key={f.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-sm ${f.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="font-semibold text-slate-200">{f.label}</span>
            <span className="font-mono text-xs text-violet-400">{f.key}</span>
            <span className="text-xs text-slate-400">
              {typeName(f.data_type)}
              {f.allowed_values && (f.data_type === "lookup" ? ` → ${label(f.allowed_values)}` : `: ${f.allowed_values}`)}
            </span>
            {f.required && <span className="text-xs text-amber-400">required</span>}
            {f.default_value && <span className="text-xs text-slate-500">default {f.default_value}</span>}
            {!f.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">inactive</span>}
            <div className="ml-auto flex gap-2">
              {f.is_active && (
                <button onClick={() => run(() => deactivateCustomField(f.id))} className={`${SMALL_BTN} hover:border-amber-500 hover:text-amber-400`}>Deactivate</button>
              )}
              <button onClick={() => run(() => deleteCustomField(f.id))} title="Deletes the field and every value in it" className={`${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>Delete</button>
            </div>
          </div>
        ))}
        {fields.length === 0 && <p className="text-sm text-slate-500">No fields yet.</p>}
      </div>

      <div className="mt-4 grid gap-3 border-t border-slate-800 pt-4 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block text-xs text-slate-400">
          Field name
          <input value={form.label} onChange={(e) => setForm((f) => ({ ...f, label: e.target.value, ...(keyTouched ? {} : { key: slug(e.target.value, 60) }) }))} placeholder="Survey date" className={INPUT} />
        </label>
        <label className="block text-xs text-slate-400">
          Key
          <input value={form.key} onChange={(e) => { setKeyTouched(true); setForm((f) => ({ ...f, key: slug(e.target.value, 60) })); }} className={`${INPUT} font-mono`} />
        </label>
        <label className="block text-xs text-slate-400">
          Type
          <select value={form.dataType} onChange={(e) => setForm((f) => ({ ...f, dataType: e.target.value }))} className={INPUT}>
            {DATA_TYPES.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </label>
        {form.dataType === "lookup" ? (
          <label className="block text-xs text-slate-400">
            Points to
            <select value={form.target} onChange={(e) => setForm((f) => ({ ...f, target: e.target.value }))} className={INPUT}>
              <option value="">Choose…</option>
              {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
        ) : (
          <label className="block text-xs text-slate-400">
            Default
            <input value={form.defaultValue} onChange={(e) => setForm((f) => ({ ...f, defaultValue: e.target.value }))} className={INPUT} />
          </label>
        )}
        {form.dataType === "picklist" && (
          <label className="block text-xs text-slate-400 sm:col-span-2 lg:col-span-3">
            Choices (comma-separated)
            <input value={form.allowedValues} onChange={(e) => setForm((f) => ({ ...f, allowedValues: e.target.value }))} placeholder="Pending, Feasible, Not feasible" className={INPUT} />
          </label>
        )}
        <label className="flex items-center gap-2 text-sm text-slate-300 lg:mt-6">
          <input type="checkbox" checked={form.required} onChange={(e) => setForm((f) => ({ ...f, required: e.target.checked }))} />
          Required
        </label>
      </div>
      {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
      <button
        onClick={add}
        disabled={!form.label.trim() || !form.key || (form.dataType === "picklist" && !form.allowedValues.trim()) || (form.dataType === "lookup" && !form.target)}
        className={`${BTN_PRIMARY} mt-3`}
      >
        Add field
      </button>
    </section>
  );
}

function StatesEditor({ type, onChanged }: { type: RecordType; onChanged: () => void }) {
  const [states, setStates] = useState<LifecycleStateDef[]>(() => structuredClone(type.states));
  const [moves, setMoves] = useState<LifecycleMove[]>(() => structuredClone(type.transitions));
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const codes = states.map((s) => s.code);

  async function save() {
    setError("");
    setSaved("");
    try {
      await saveRecordStates(type.entity_type, { name: `${type.label} lifecycle`, states, transitions: moves.map((m) => ({ ...m, intent_type: null })) });
      setSaved("Saved - records move this way from now on.");
      onChanged();
    } catch (e) {
      setError(problem(e, "Could not save the lifecycle."));
    }
  }

  return (
    <section className={`${CARD} space-y-4`}>
      <p className="text-xs text-slate-400">
        The first state is where a new {type.label.toLowerCase()} starts. A locked state can't be edited (move it on first); a final state ends its life.
        {!type.state_model_configured && " This type still uses the default Open → Closed."} Flow Studio flows can run when a record enters a state, and
        a rule can depend on the state (Page &amp; rules).
      </p>
      <div className="space-y-1.5">
        <div className="grid grid-cols-[8rem_minmax(0,1fr)_5rem_5rem_auto] gap-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
          <span>Code</span><span>Label</span><span>Locked</span><span>Final</span><span />
        </div>
        {states.map((s, i) => (
          <div key={i} className="grid grid-cols-[8rem_minmax(0,1fr)_5rem_5rem_auto] items-center gap-2">
            <input value={s.code} onChange={(e) => setStates((cur) => cur.map((x, j) => (j === i ? { ...x, code: slug(e.target.value, 50) } : x)))} className={`${CELL} font-mono`} />
            <input value={s.label} onChange={(e) => setStates((cur) => cur.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} className={CELL} />
            <input type="checkbox" checked={!s.editable} onChange={(e) => setStates((cur) => cur.map((x, j) => (j === i ? { ...x, editable: !e.target.checked } : x)))} aria-label="Locked" />
            <input type="checkbox" checked={s.is_terminal} onChange={(e) => setStates((cur) => cur.map((x, j) => (j === i ? { ...x, is_terminal: e.target.checked } : x)))} aria-label="Final" />
            <button onClick={() => setStates((cur) => cur.filter((_, j) => j !== i))} className="text-xs text-slate-500 hover:text-red-400" aria-label={`Remove ${s.label}`}>✕</button>
          </div>
        ))}
        <button onClick={() => setStates((cur) => [...cur, { code: "", label: "", editable: true, is_terminal: false }])} className={SMALL_BTN}>+ State</button>
      </div>
      <div className="space-y-1.5 border-t border-slate-800 pt-3">
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] gap-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
          <span>From</span><span>To</span><span>Move (button)</span><span>Who (blank = everyone)</span><span />
        </div>
        {moves.map((m, i) => (
          <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2">
            <select value={m.from_state} onChange={(e) => setMoves((cur) => cur.map((x, j) => (j === i ? { ...x, from_state: e.target.value } : x)))} className={CELL}>
              <option value="*">any open state</option>
              {codes.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={m.to_state} onChange={(e) => setMoves((cur) => cur.map((x, j) => (j === i ? { ...x, to_state: e.target.value } : x)))} className={CELL}>
              {codes.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <input value={m.action} onChange={(e) => setMoves((cur) => cur.map((x, j) => (j === i ? { ...x, action: slug(e.target.value, 50) } : x)))} className={`${CELL} font-mono`} />
            <input value={m.allowed_roles} placeholder="e.g. admin, Survey Lead" onChange={(e) => setMoves((cur) => cur.map((x, j) => (j === i ? { ...x, allowed_roles: e.target.value } : x)))} className={CELL} />
            <button onClick={() => setMoves((cur) => cur.filter((_, j) => j !== i))} className="text-xs text-slate-500 hover:text-red-400" aria-label="Remove move">✕</button>
          </div>
        ))}
        <button onClick={() => setMoves((cur) => [...cur, { from_state: codes[0] ?? "*", to_state: codes[1] ?? codes[0] ?? "", action: "", allowed_roles: "" }])} disabled={codes.length === 0} className={SMALL_BTN}>+ Move</button>
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {saved && <p className="text-sm text-emerald-300">{saved}</p>}
      <button onClick={save} disabled={states.length === 0 || states.some((s) => !s.code) || moves.some((m) => !m.action)} className={BTN_PRIMARY}>Save lifecycle</button>
    </section>
  );
}

function RelationshipsEditor({ type, options, onChanged }: { type: RecordType; options: Option[]; onChanged: () => void }) {
  const [form, setForm] = useState({ to: "", label: "", reverse_label: "" });
  const [error, setError] = useState("");

  async function run(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      onChanged();
      return true;
    } catch (e) {
      setError(problem(e, "Could not save the relationship."));
      return false;
    }
  }

  return (
    <section className={`${CARD} space-y-4`}>
      <p className="text-xs text-slate-400">
        Many-to-many links between {type.plural_label.toLowerCase()} and another type - a site's contacts, a contract's sites. Records are linked from their
        Related tab, or in plain words (“link {type.number_prefix}-0001 to …”). One-to-many links are the parent a type hangs off, or a lookup field.
      </p>
      <div className="space-y-2">
        {type.relationships.map((r: Relationship) => (
          <div key={r.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm">
            <span className="font-semibold text-slate-200">{r.name}</span>
            <span className="text-xs text-slate-400">↔ {r.other_label} · seen from there: “{r.name === r.label ? r.reverse_label : r.label}”</span>
            <span className="font-mono text-xs text-violet-400">{r.key}</span>
            <span className="text-xs text-slate-500">{r.links} link{r.links === 1 ? "" : "s"}</span>
            <button onClick={() => run(() => deleteRelationship(r.id, true))} className={`${SMALL_BTN} ml-auto hover:border-red-500 hover:text-red-400`} title="Deletes it and its links">Delete</button>
          </div>
        ))}
        {type.relationships.length === 0 && <p className="text-sm text-slate-500">No relationships yet.</p>}
      </div>
      <div className="grid gap-3 border-t border-slate-800 pt-4 sm:grid-cols-3">
        <label className="block text-xs text-slate-400">Related to
          <select value={form.to} onChange={(e) => setForm((f) => ({ ...f, to: e.target.value }))} className={INPUT}>
            <option value="">Choose…</option>
            {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            <option value={type.entity_type}>{type.label} (each other)</option>
          </select>
        </label>
        <label className="block text-xs text-slate-400">What a {type.label.toLowerCase()} calls them
          <input value={form.label} onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))} placeholder="Contacts" className={INPUT} />
        </label>
        <label className="block text-xs text-slate-400">What they call {type.plural_label.toLowerCase()}
          <input value={form.reverse_label} onChange={(e) => setForm((f) => ({ ...f, reverse_label: e.target.value }))} placeholder={type.plural_label} className={INPUT} />
        </label>
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      <button disabled={!form.to || !form.label.trim()} className={BTN_PRIMARY} onClick={async () => {
        if (await run(() => createRelationship({ from_entity_type: type.entity_type, to_entity_type: form.to, label: form.label.trim(), reverse_label: form.reverse_label.trim() }))) {
          setForm({ to: "", label: "", reverse_label: "" });
        }
      }}>Add relationship</button>
    </section>
  );
}

function RulesEditor({ type, onOpen }: { type: RecordType; onOpen?: (s: AdminSection) => void }) {
  const [rules, setRules] = useState<PageRule[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    listPageRules(type.entity_type).then((r) => setRules(r.rules.filter((x) => x.entity_type === type.entity_type))).catch((e) => setError(problem(e, "Could not load the rules.")));
  }, [type.entity_type]);
  useEffect(load, [load]);

  return (
    <section className={`${CARD} space-y-4`}>
      <p className="text-xs text-slate-400">
        Validation rules in plain English guard every save - on the page, from the command bar and from flows. A rule can depend on the state: it's checked
        before a record enters it. Lay out the {type.label.toLowerCase()} page (sections, mandatory and read-only fields, buttons that run actions) in the{" "}
        <button onClick={() => onOpen?.("pages")} className="font-semibold text-sky-400 hover:text-sky-300">Page Designer</button>.
      </p>
      <ul className="space-y-1.5">
        {rules.map((r) => (
          <li key={r.id} className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm">
            <span className="min-w-0 flex-1 text-slate-200">{r.explanation}</span>
            {r.source_text && r.source_text !== r.explanation && <span className="hidden truncate text-xs text-slate-500 sm:inline">“{r.source_text}”</span>}
            <button onClick={async () => { await deletePageRule(r.id); load(); }} className="text-xs text-slate-500 hover:text-red-400" aria-label="Delete rule">✕</button>
          </li>
        ))}
        {rules.length === 0 && <li className="text-sm text-slate-500">No rules yet.</li>}
      </ul>
      <div className="flex flex-wrap gap-2">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. if State is completed then Report is mandatory · Floors must be at least 1"
          className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" />
        <button disabled={busy || !text.trim()} className={BTN_PRIMARY} onClick={async () => {
          setBusy(true);
          setError("");
          try {
            await addRecordRule(type.entity_type, text.trim());
            setText("");
            load();
          } catch (e) {
            setError(problem(e, "Couldn't turn that into a rule."));
          } finally {
            setBusy(false);
          }
        }}>Add rule</button>
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
    </section>
  );
}

function Automations({ type, types, onOpen }: { type: RecordType; types: RecordType[]; onOpen?: (s: AdminSection) => void }) {
  const [flows, setFlows] = useState<Flow[] | null>(null);
  useEffect(() => {
    listFlows().then((all) => setFlows(all.filter((f) => (f.entity_type as string | null) === type.entity_type))).catch(() => setFlows([]));
  }, [type.entity_type]);
  const prefix = type.number_prefix;
  return (
    <section className={`${CARD} space-y-4`}>
      <p className="text-xs text-slate-400">
        {type.plural_label} change through intents - so Flow Studio flows can run before or after them, when a record enters a state, or as an action
        people start. Build them in the <button onClick={() => onOpen?.("flows")} className="font-semibold text-sky-400 hover:text-sky-300">Flow Studio</button> with
        record type “{type.label}”; give records to people automatically with <button onClick={() => onOpen?.("assignment")} className="font-semibold text-sky-400 hover:text-sky-300">Assignment rules</button>.
      </p>
      <div>
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Flows on {type.plural_label.toLowerCase()}</h3>
        {flows === null && <p className="text-sm text-slate-500">Loading…</p>}
        <ul className="space-y-1.5">
          {flows?.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm">
              <span className="font-semibold text-slate-200">{f.name}</span>
              <span className="text-xs text-slate-400">
                {f.trigger_type === "state_enter" ? `when it enters ${f.trigger_state}` : f.trigger_type === "intent" ? `${f.timing} ${f.trigger_intent_type}` : f.trigger_type === "custom_intent" ? "an action people start" : "a validation"}
              </span>
              <span className={`ml-auto text-xs ${f.is_active ? "text-emerald-300" : "text-slate-500"}`}>{f.is_active ? "on" : "off"}</span>
            </li>
          ))}
          {flows?.length === 0 && <li className="text-sm text-slate-500">None yet.</li>}
        </ul>
      </div>
      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-400">
        <p className="mb-1 font-semibold text-slate-300">The intents a flow can hang on - and what people can say in the command bar</p>
        <ul className="space-y-0.5">
          <li><span className="font-mono text-violet-400">INT-151</span> Create a record - “Create a {type.label.toLowerCase()}{type.parent_label ? ` for <${type.parent_label.toLowerCase()}>` : ""} with …”</li>
          <li><span className="font-mono text-violet-400">INT-152</span> Update a record - “Update {prefix}-0001 {type.fields[0]?.key ?? "field"}=…”</li>
          <li><span className="font-mono text-violet-400">INT-153</span> Move a record - “Move {prefix}-0001 to {type.states[1]?.label.toLowerCase() ?? type.states[0]?.label.toLowerCase() ?? "closed"}”</li>
          <li><span className="font-mono text-violet-400">INT-154</span> Assign a record - “Assign {prefix}-0001 to the Survey team”</li>
          <li><span className="font-mono text-violet-400">INT-155</span> Link records - “Link {prefix}-0001 to {types.find((t) => t.key !== type.key)?.number_prefix ?? "CT"}-0002”</li>
          <li><span className="font-mono text-violet-400">INT-156</span> Find records - “Show {type.states[0]?.label.toLowerCase() ?? "open"} {type.plural_label.toLowerCase()}”</li>
          <li><span className="font-mono text-violet-400">INT-157</span> Delete a record - “Delete {prefix}-0001”</li>
        </ul>
      </div>
    </section>
  );
}
