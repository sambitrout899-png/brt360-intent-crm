import { useCallback, useEffect, useState } from "react";
import { createCustomField, deactivateCustomField, deleteCustomField, listCustomFields } from "../api/client";
import { createRecordType, deleteRecordType, listRecordTypes, problem, updateRecordType } from "../api/customRecords";
import type { RecordType } from "../api/customRecords";
import type { CustomFieldDefinition } from "../api/types";

/** Admin -> Record Types: the tenant's own record types ("custom objects") and their fields. */

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";
const SMALL_BTN = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300";
const BTN_PRIMARY = "rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50";
// what custom_field_service validates (models/custom_fields.py CUSTOM_FIELD_DATA_TYPES)
const DATA_TYPES = [
  ["text", "Text"], ["textarea", "Long text"], ["number", "Number"], ["date", "Date"], ["boolean", "Yes / no"],
  ["picklist", "Pick list"], ["email", "Email"], ["phone", "Phone"],
] as const;

function slug(text: string, max = 28): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, max).replace(/_+$/, "");
}

type TypeForm = { label: string; plural_label: string; key: string; icon: string; number_prefix: string; description: string; parent_entity_type: string; parent_required: boolean };

function formOf(t: RecordType | null): TypeForm {
  return {
    label: t?.label ?? "", plural_label: t?.plural_label ?? "", key: t?.key ?? "", icon: t?.icon ?? "📄", number_prefix: t?.number_prefix ?? "",
    description: t?.description ?? "", parent_entity_type: t?.parent_entity_type ?? "", parent_required: t?.parent_required ?? false,
  };
}

export default function AdminRecordTypesSection() {
  const [types, setTypes] = useState<RecordType[]>([]);
  const [parents, setParents] = useState<{ value: string; label: string }[]>([]);
  const [selected, setSelected] = useState<string | "new" | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listRecordTypes()
      .then((d) => {
        setTypes(d.types);
        setParents(d.parent_options);
      })
      .catch((e) => setError(problem(e, "Could not load the record types.")));
  }, []);
  useEffect(load, [load]);

  const current = types.find((t) => t.key === selected) ?? null;
  return (
    <div className="space-y-5">
      <p className="text-sm text-slate-400">
        Record types of your own - a Site Survey, a Contract, an Asset Inspection - with the fields you give them. Their records are kept on the{" "}
        <span className="font-semibold text-slate-300">Custom Records</span> page (give a role that page in Roles &amp; Access), can hang off a customer, an
        order or another of your types, follow the user's data scope, and every change is in the Audit Log.
      </p>
      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="space-y-2">
          <button onClick={() => setSelected("new")} className={`${BTN_PRIMARY} w-full`}>+ New record type</button>
          {types.map((t) => (
            <button
              key={t.key}
              onClick={() => setSelected(t.key)}
              className={`block w-full rounded-lg border px-3 py-2 text-left ${t.key === selected ? "border-sky-500 bg-sky-500/10" : "border-slate-800 bg-slate-900/50 hover:border-slate-600"} ${t.is_active ? "" : "opacity-60"}`}
            >
              <span className="block text-sm font-semibold text-slate-100">{t.icon} {t.label}</span>
              <span className="block text-[11px] text-slate-500">
                {t.fields.length} field{t.fields.length === 1 ? "" : "s"} · {t.record_count} record{t.record_count === 1 ? "" : "s"}
                {t.parent_label ? ` · under ${t.parent_label}` : ""}
                {t.is_active ? "" : " · off"}
              </span>
            </button>
          ))}
          {types.length === 0 && <p className="text-sm text-slate-500">None yet.</p>}
        </div>

        <div className="min-w-0">
          {selected === "new" && (
            <TypeEditor
              key="new"
              type={null}
              parents={parents}
              onSaved={(t) => {
                load();
                setSelected(t.key);
              }}
            />
          )}
          {current && (
            <div className="space-y-6">
              <TypeEditor key={current.key} type={current} parents={parents.filter((p) => p.value !== current.entity_type)} onSaved={load} onDeleted={() => { setSelected(null); load(); }} />
              <FieldsEditor key={`f-${current.key}`} type={current} onChanged={load} />
            </div>
          )}
          {selected === null && <p className="text-sm text-slate-500">Pick a record type, or create one.</p>}
        </div>
      </div>
    </div>
  );
}

function TypeEditor({ type, parents, onSaved, onDeleted }: {
  type: RecordType | null;
  parents: { value: string; label: string }[];
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
    parent_required: form.parent_required,
  };
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <h2 className="mb-3 text-base font-semibold text-slate-100">{type ? `${type.icon} ${type.label}` : "New record type"}</h2>
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
          <input value={form.key} disabled={!isNew} onChange={(e) => { setKeyTouched(true); set({ key: slug(e.target.value) }); }} className={`${INPUT} font-mono disabled:opacity-60`} />
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
          <select value={form.parent_entity_type} disabled={!!type && type.record_count > 0} onChange={(e) => set({ parent_entity_type: e.target.value, parent_required: e.target.value ? form.parent_required : false })} className={`${INPUT} disabled:opacity-60`}>
            <option value="">Nothing - they stand alone</option>
            {parents.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </label>
        <label className="mt-6 flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={form.parent_required} disabled={!form.parent_entity_type} onChange={(e) => set({ parent_required: e.target.checked })} />
          Every record needs one
        </label>
        <label className="block text-xs text-slate-400 sm:col-span-2">
          Description
          <textarea value={form.description} onChange={(e) => set({ description: e.target.value })} rows={2} className={INPUT} />
        </label>
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
                Delete the type and its fields?
                <button onClick={() => run(async () => { await deleteRecordType(type.key); onDeleted?.(); })} className="rounded-md bg-red-600 px-2.5 py-1 font-bold text-white hover:bg-red-500">Delete</button>
                <button onClick={() => setConfirmDelete(false)} className={SMALL_BTN}>Keep</button>
              </span>
            )}
            <span className="ml-auto font-mono text-[11px] text-slate-500">{type.entity_type}</span>
          </>
        )}
      </div>
      {type && type.record_count > 0 && <p className="mt-2 text-[11px] text-slate-500">What its records hang off is fixed once records exist; a type with records can be switched off but not deleted.</p>}
    </section>
  );
}

function emptyField() {
  return { label: "", key: "", dataType: "text", allowedValues: "", required: false, defaultValue: "" };
}

function FieldsEditor({ type, onChanged }: { type: RecordType; onChanged: () => void }) {
  const [fields, setFields] = useState<CustomFieldDefinition[]>([]);
  const [form, setForm] = useState(emptyField());
  const [keyTouched, setKeyTouched] = useState(false);
  const [error, setError] = useState("");

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
        allowed_values: form.dataType === "picklist" ? form.allowedValues.trim() : null, required: form.required,
        default_value: form.defaultValue.trim() || null,
      })
    );
    if (ok) {
      setForm(emptyField());
      setKeyTouched(false);
    }
  }

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <h3 className="text-base font-semibold text-slate-100">Fields</h3>
      <p className="mb-3 mt-0.5 text-xs text-slate-400">Every {type.label.toLowerCase()} also has a number ({type.number_prefix}-0001) and a name.</p>
      <div className="space-y-2">
        {fields.map((f) => (
          <div key={f.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-sm ${f.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="font-semibold text-slate-200">{f.label}</span>
            <span className="font-mono text-xs text-violet-400">{f.key}</span>
            <span className="text-xs text-slate-400">
              {DATA_TYPES.find(([v]) => v === f.data_type)?.[1] ?? f.data_type}
              {f.allowed_values && `: ${f.allowed_values}`}
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
        <label className="block text-xs text-slate-400">
          Default
          <input value={form.defaultValue} onChange={(e) => setForm((f) => ({ ...f, defaultValue: e.target.value }))} className={INPUT} />
        </label>
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
        disabled={!form.label.trim() || !form.key || (form.dataType === "picklist" && !form.allowedValues.trim())}
        className={`${BTN_PRIMARY} mt-3`}
      >
        Add field
      </button>
    </section>
  );
}
