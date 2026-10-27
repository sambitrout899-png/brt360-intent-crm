import { useEffect, useMemo, useState } from "react";
import { adminIntents, bulkIntentRoles, createIntent, deleteIntent, resetIntent, updateIntent } from "../api/client";
import type { AdminIntentCatalog, IntentCreateResult, IntentDef, IntentFieldInput, IntentFieldType } from "../api/types";
import { INPUT_CLS, categoryStyle } from "../components/intent/intentUi";

const ICONS = ["✨", "🔥", "🚀", "📞", "📝", "✅", "📦", "🧾", "💬", "🔔", "⚠️", "🎯", "🤝", "📈", "🛠️", "📅"];
const TYPE_LABELS: Record<string, string> = {
  text: "Short text",
  textarea: "Long text",
  number: "Number",
  money: "Amount",
  percent: "Percent",
  picklist: "Choice list",
  record: "Pick a record",
  list: "List of items",
  boolean: "Yes / No",
};
const ENTITY_NAMES: Record<string, string> = { party: "Customer", lead: "Lead", opportunity: "Opportunity", quote: "Quote", order: "Order" };
const BEHAVIOUR_EXAMPLES = [
  "Notify the owner that the deal needs management attention, then assign it to the Deal Desk",
  "If total price is greater than 50000, assign to Head of Sales. Otherwise notify the owner",
  "Set priority to high and move it to escalated",
];

function splitList(raw: string): string[] {
  return raw.split(",").map((s) => s.trim()).filter(Boolean);
}

function Switch({ on, onChange, disabled }: { on: boolean; onChange: (on: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!on)}
      title={on ? "On - click to switch off" : "Off - click to switch on"}
      className={`relative h-5 w-9 shrink-0 rounded-full transition disabled:opacity-50 ${on ? "bg-emerald-500" : "bg-slate-700"}`}
    >
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

function RolesPicker({ roles, value, onChange }: { roles: AdminIntentCatalog["roles"]; value: number[]; onChange: (ids: number[]) => void }) {
  if (roles.length === 0) return <p className="text-xs text-slate-500">No roles defined yet (Admin → Roles & Access) - everyone can use every intent.</p>;
  return (
    <div className="flex flex-wrap gap-1.5">
      <button
        type="button"
        onClick={() => onChange([])}
        className={`rounded-full border px-3 py-1 text-xs font-semibold ${value.length === 0 ? "border-emerald-500 bg-emerald-500/15 text-emerald-300" : "border-slate-700 text-slate-400"}`}
      >
        Everyone
      </button>
      {roles.map((r) => {
        const on = value.includes(r.id);
        return (
          <button
            key={r.id}
            type="button"
            onClick={() => onChange(on ? value.filter((id) => id !== r.id) : [...value, r.id])}
            className={`rounded-full border px-3 py-1 text-xs font-semibold ${on ? "border-sky-500 bg-sky-500/15 text-sky-300" : "border-slate-700 text-slate-400"} ${r.is_active ? "" : "opacity-50"}`}
          >
            {r.name}
          </button>
        );
      })}
    </div>
  );
}

function IconPicker({ value, onChange }: { value: string; onChange: (icon: string) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {ICONS.map((icon) => (
        <button
          key={icon}
          type="button"
          onClick={() => onChange(icon)}
          className={`h-8 w-8 rounded-lg text-lg ${value === icon ? "bg-sky-500/30 ring-1 ring-sky-500" : "hover:bg-slate-800"}`}
        >
          {icon}
        </button>
      ))}
      <input value={value} onChange={(e) => onChange(e.target.value)} className="h-8 w-14 rounded-lg border border-slate-700 bg-slate-950 text-center text-lg" />
    </div>
  );
}

/** Rows of fields for a custom intent: label, kind, required, choices. */
function FieldsBuilder({
  fields,
  data,
  onChange,
}: {
  fields: IntentFieldInput[];
  data: AdminIntentCatalog;
  onChange: (fields: IntentFieldInput[]) => void;
}) {
  const patch = (i: number, p: Partial<IntentFieldInput>) => onChange(fields.map((f, j) => (j === i ? { ...f, ...p } : f)));
  const libraryLeft = data.field_library.filter((l) => !fields.some((f) => f.key === l.key));
  return (
    <div className="space-y-2">
      {fields.map((f, i) => (
        <div key={i} className="grid items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2 sm:grid-cols-[1.4fr_1fr_auto_auto]">
          <input value={f.label} onChange={(e) => patch(i, { label: e.target.value })} placeholder="What to ask, e.g. Urgency" className={INPUT_CLS} />
          <select value={f.type} onChange={(e) => patch(i, { type: e.target.value as IntentFieldType })} className={INPUT_CLS}>
            {data.field_types.map((t) => (
              <option key={t} value={t}>
                {TYPE_LABELS[t] ?? t}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-slate-400">
            <input type="checkbox" checked={f.required} onChange={(e) => patch(i, { required: e.target.checked })} className="accent-sky-500" />
            required
          </label>
          <button type="button" onClick={() => onChange(fields.filter((_, j) => j !== i))} className="px-2 text-slate-500 hover:text-red-400">
            ✕
          </button>
          {f.type === "picklist" && !f.source && (
            <input
              value={(f.values ?? []).join(", ")}
              onChange={(e) => patch(i, { values: splitList(e.target.value) })}
              placeholder="Choices, separated by commas - e.g. high, critical"
              className={`${INPUT_CLS} sm:col-span-4`}
            />
          )}
          {f.type === "record" && (
            <select value={f.record_type ?? ""} onChange={(e) => patch(i, { record_type: e.target.value })} className={`${INPUT_CLS} sm:col-span-2`}>
              <option value="">Which record?</option>
              {data.entity_types.map((t) => (
                <option key={t} value={t}>
                  {ENTITY_NAMES[t] ?? t}
                </option>
              ))}
            </select>
          )}
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onChange([...fields, { label: "", type: "text", required: false, values: [] }])}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-sky-500"
        >
          + Add a field
        </button>
        {libraryLeft.length > 0 && (
          <select
            value=""
            onChange={(e) => {
              const lib = data.field_library.find((l) => l.key === e.target.value);
              if (lib) onChange([...fields, { key: lib.key, label: lib.label, type: lib.type, required: false, values: lib.values, source: lib.source, record_type: lib.record_type, help: lib.help, placeholder: lib.placeholder }]);
            }}
            className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-300"
          >
            <option value="">+ Ready-made field…</option>
            {libraryLeft.map((l) => (
              <option key={l.key} value={l.key}>
                {l.label} ({TYPE_LABELS[l.type] ?? l.type})
              </option>
            ))}
          </select>
        )}
      </div>
    </div>
  );
}

function asInputs(intent: IntentDef): IntentFieldInput[] {
  return intent.fields.map((f) => ({ key: f.key, label: f.label, type: f.type, required: f.required, values: f.values, source: f.source, record_type: f.record_type, help: f.help, placeholder: f.placeholder }));
}

/** Edit how an intent appears, who may use it, its phrases and defaults. */
function IntentEditor({ intent, data, onSaved, onClose, onOpenFlowStudio }: {
  intent: IntentDef;
  data: AdminIntentCatalog;
  onSaved: (message: string) => void;
  onClose: () => void;
  onOpenFlowStudio?: () => void;
}) {
  const [form, setForm] = useState({
    label: intent.label,
    description: intent.description,
    category: intent.category,
    icon: intent.icon,
    role_ids: intent.role_ids,
    phrases: intent.phrases.join(", "),
    defaults: Object.fromEntries(intent.fields.filter((f) => f.default !== null && f.default !== undefined).map((f) => [f.key, String(f.default)])) as Record<string, string>,
    fields: asInputs(intent),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const custom = intent.kind === "custom";

  async function save() {
    setBusy(true);
    setError("");
    try {
      await updateIntent(intent.code, {
        label: form.label,
        description: form.description,
        category: form.category,
        icon: form.icon,
        role_ids: form.role_ids,
        phrases: splitList(form.phrases),
        defaults: Object.fromEntries(Object.entries(form.defaults).filter(([, v]) => v !== "")),
        ...(custom ? { fields: form.fields } : {}),
      });
      onSaved(`Saved “${form.label}”.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      if (custom) await deleteIntent(intent.code);
      else await resetIntent(intent.code);
      onSaved(custom ? `Deleted “${intent.label}”.` : `“${intent.default_label}” is back to its built-in settings.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4 rounded-2xl border border-sky-500/30 bg-slate-900/80 p-5">
      <div className="flex items-center gap-3">
        <span className="text-2xl">{form.icon}</span>
        <h3 className="flex-1 text-base font-bold text-slate-100">
          <span className="mr-2 rounded bg-slate-800 px-1.5 py-0.5 align-middle font-mono text-[11px] font-semibold text-sky-300">{intent.code}</span>
          Edit “{intent.label}”{" "}
          {intent.kind === "builtin" && <span className="text-xs font-normal text-slate-500">standard action - extend it with a flow</span>}
        </h3>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
          ✕
        </button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-slate-400">
          Name people see
          <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} className={`mt-1 ${INPUT_CLS}`} />
          {!custom && form.label !== intent.default_label && <span className="text-[11px] text-slate-500">built-in name: {intent.default_label}</span>}
        </label>
        <label className="text-xs text-slate-400">
          Area
          <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className={`mt-1 ${INPUT_CLS}`}>
            {data.categories.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-400 md:col-span-2">
          Description
          <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={`mt-1 ${INPUT_CLS}`} />
        </label>
        <div className="text-xs text-slate-400 md:col-span-2">
          Icon
          <div className="mt-1">
            <IconPicker value={form.icon} onChange={(icon) => setForm({ ...form, icon })} />
          </div>
        </div>
        <div className="text-xs text-slate-400 md:col-span-2">
          Who can use it
          <div className="mt-1">
            <RolesPicker roles={data.roles} value={form.role_ids} onChange={(role_ids) => setForm({ ...form, role_ids })} />
          </div>
        </div>
        <label className="text-xs text-slate-400 md:col-span-2">
          Phrases people say for it (comma-separated) - the assistant learns them
          <input value={form.phrases} onChange={(e) => setForm({ ...form, phrases: e.target.value })} placeholder="e.g. raise a PO, book the order" className={`mt-1 ${INPUT_CLS}`} />
        </label>
      </div>

      {custom && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-300">Fields the form asks for</p>
          <FieldsBuilder fields={form.fields} data={data} onChange={(fields) => setForm({ ...form, fields })} />
        </div>
      )}

      {intent.fields.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-300">Default values (prefilled for everyone)</p>
          <div className="grid gap-2 md:grid-cols-2">
            {intent.fields
              .filter((f) => !["record", "updates", "transition"].includes(f.type))
              .map((f) => (
                <label key={f.key} className="text-xs text-slate-400">
                  {f.label}
                  {f.values.length > 0 ? (
                    <select
                      value={form.defaults[f.key] ?? ""}
                      onChange={(e) => setForm({ ...form, defaults: { ...form.defaults, [f.key]: e.target.value } })}
                      className={`mt-1 ${INPUT_CLS}`}
                    >
                      <option value="">no default</option>
                      {f.values.map((v) => (
                        <option key={v} value={v}>
                          {v}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={form.defaults[f.key] ?? ""}
                      onChange={(e) => setForm({ ...form, defaults: { ...form.defaults, [f.key]: e.target.value } })}
                      placeholder="no default"
                      className={`mt-1 ${INPUT_CLS}`}
                    />
                  )}
                </label>
              ))}
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button onClick={save} disabled={busy} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
          Save
        </button>
        {custom && onOpenFlowStudio && (
          <button onClick={onOpenFlowStudio} className="rounded-lg border border-fuchsia-500/40 px-4 py-2 text-sm font-semibold text-fuchsia-300 hover:bg-fuchsia-500/10">
            Edit what it does (Flow Studio) →
          </button>
        )}
        <button onClick={remove} disabled={busy} className="ml-auto rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-400 hover:border-red-500 hover:text-red-400">
          {custom ? "Delete intent" : "Reset to built-in"}
        </button>
      </div>
    </div>
  );
}

/** Add a new intent without code: name it, say what should happen (the
 * flow is drafted from that), choose what the form asks, who may use it. */
function NewIntentWizard({ data, onCreated, onClose, onOpenFlowStudio }: {
  data: AdminIntentCatalog;
  onCreated: () => void;
  onClose: () => void;
  onOpenFlowStudio?: () => void;
}) {
  const [form, setForm] = useState({
    label: "",
    description: "",
    behaviour: "",
    category: "custom",
    icon: "✨",
    entity_type: "",
    phrases: "",
    fields: [] as IntentFieldInput[],
    role_ids: [] as number[],
    is_active: true,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<IntentCreateResult | null>(null);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const result = await createIntent({
        ...form,
        entity_type: form.entity_type || null,
        phrases: splitList(form.phrases),
        fields: form.fields.filter((f) => f.label.trim()),
      });
      setCreated(result);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the intent");
    } finally {
      setBusy(false);
    }
  }

  if (created) {
    return (
      <div className="space-y-3 rounded-2xl border border-emerald-500/40 bg-emerald-500/5 p-5">
        <p className="text-base font-bold text-emerald-300">
          {created.intent.icon} “{created.intent.label}” is {created.intent.is_active ? "live" : "created (switched off)"}
        </p>
        <p className="text-sm text-slate-300">
          People with access can now find it in the Intent Command Bar, or just say it: {created.intent.phrases.map((p) => `“${p}”`).join(", ")}.
        </p>
        {created.notes && <p className="text-xs text-slate-400">What it does was drafted for you: {created.notes}</p>}
        {created.corrections.length > 0 && <p className="text-xs text-violet-300">✨ Auto-corrected {created.corrections.length} reference(s) to real fields.</p>}
        {created.flow_errors.length > 0 && <p className="text-xs text-amber-300">⚠ Its flow needs attention before it can run: {created.flow_errors.join("; ")}</p>}
        <div className="flex gap-2">
          {onOpenFlowStudio && (
            <button onClick={onOpenFlowStudio} className="rounded-lg border border-fuchsia-500/40 px-4 py-2 text-sm font-semibold text-fuchsia-300 hover:bg-fuchsia-500/10">
              Review what it does (Flow Studio) →
            </button>
          )}
          <button onClick={onClose} className="rounded-lg bg-slate-100 px-4 py-2 text-sm font-bold text-slate-900">
            Done
          </button>
        </div>
      </div>
    );
  }

  const step = (n: number, title: string) => (
    <p className="flex items-center gap-2 text-sm font-semibold text-slate-200">
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-sky-600 text-xs font-bold text-white">{n}</span>
      {title}
    </p>
  );

  return (
    <div className="space-y-5 rounded-2xl border border-sky-500/30 bg-slate-900/80 p-5">
      <div className="flex items-center">
        <h3 className="flex-1 text-base font-bold text-slate-100">New intent</h3>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
          ✕
        </button>
      </div>

      <div className="space-y-3">
        {step(1, "Name it")}
        <div className="grid gap-3 md:grid-cols-2">
          <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} placeholder="e.g. Escalate a deal" className={INPUT_CLS} />
          <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className={INPUT_CLS}>
            {data.categories.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
          <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="One line people see under the name" className={`${INPUT_CLS} md:col-span-2`} />
          <div className="md:col-span-2">
            <IconPicker value={form.icon} onChange={(icon) => setForm({ ...form, icon })} />
          </div>
        </div>
      </div>

      <div className="space-y-3">
        {step(2, "What does it act on?")}
        <div className="flex flex-wrap gap-1.5">
          {["", ...data.entity_types].map((t) => (
            <button
              key={t || "none"}
              type="button"
              onClick={() => setForm({ ...form, entity_type: t })}
              className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${form.entity_type === t ? "border-sky-500 bg-sky-500/20 text-sky-200" : "border-slate-700 text-slate-300"}`}
            >
              {t ? ENTITY_NAMES[t] ?? t : "Nothing specific"}
            </button>
          ))}
        </div>
        {form.entity_type && <p className="text-[11px] text-slate-500">The form will ask which {ENTITY_NAMES[form.entity_type]?.toLowerCase()} - prefilled from the one on screen.</p>}
      </div>

      <div className="space-y-3">
        {step(3, "What should happen when someone runs it?")}
        <textarea
          rows={3}
          value={form.behaviour}
          onChange={(e) => setForm({ ...form, behaviour: e.target.value })}
          placeholder="Describe it in plain words - AI drafts the steps, checks them against your real fields, and you can refine them in the Flow Studio."
          className={`${INPUT_CLS} resize-y`}
        />
        <div className="flex flex-wrap gap-1.5">
          {BEHAVIOUR_EXAMPLES.map((ex) => (
            <button key={ex} type="button" onClick={() => setForm({ ...form, behaviour: ex })} className="rounded-md border border-slate-800 px-2 py-1 text-left text-[11px] text-slate-400 hover:border-slate-600">
              {ex}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-3">
        {step(4, "What should the form ask for?")}
        <FieldsBuilder fields={form.fields} data={data} onChange={(fields) => setForm({ ...form, fields })} />
      </div>

      <div className="space-y-3">
        {step(5, "How people say it, and who can use it")}
        <input
          value={form.phrases}
          onChange={(e) => setForm({ ...form, phrases: e.target.value })}
          placeholder="Phrases, comma-separated - e.g. escalate this deal, needs management attention"
          className={INPUT_CLS}
        />
        <RolesPicker roles={data.roles} value={form.role_ids} onChange={(role_ids) => setForm({ ...form, role_ids })} />
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <Switch on={form.is_active} onChange={(is_active) => setForm({ ...form, is_active })} />
          Switch it on straight away
        </label>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}
      <button onClick={create} disabled={busy || form.label.trim().length < 2} className="rounded-lg bg-sky-600 px-5 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
        {busy ? "Creating…" : "Create intent"}
      </button>
    </div>
  );
}

/** Bulk access: give or take away many intents for many roles in one go. */
function BulkRolesBar({ data, selected, shown, onSelect, onDone }: {
  data: AdminIntentCatalog; selected: Set<string>; shown: IntentDef[];
  onSelect: (codes: Set<string>) => void; onDone: (message: string, error?: string) => void;
}) {
  const [roles, setRoles] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  if (data.roles.length === 0) return null;
  const allShown = shown.length > 0 && shown.every((i) => selected.has(i.code));
  const run = async (mode: "add" | "remove" | "set" | "everyone") => {
    const codes = [...selected];
    const names = roles.map((id) => data.roles.find((r) => r.id === id)?.name ?? `#${id}`).join(", ");
    const what = { add: `Give ${names} access to`, remove: `Remove ${names} from`, set: `Restrict to only ${names}:`, everyone: "Open to everyone:" }[mode];
    if (!window.confirm(`${what} ${codes.length} intent${codes.length === 1 ? "" : "s"}?`)) return;
    setBusy(true);
    try {
      const out = await bulkIntentRoles({ codes, role_ids: mode === "everyone" ? [] : roles, mode });
      const skipped = out.skipped.length ? ` ${out.skipped.length} skipped: ${out.skipped.map((s) => `${s.code} (${s.reason})`).join("; ")}.` : "";
      onDone(`${out.changed.length} intent${out.changed.length === 1 ? "" : "s"} updated.${skipped}`);
    } catch (e) {
      onDone("", e instanceof Error ? e.message : "Bulk update failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm font-semibold text-slate-100">Bulk role access</p>
        <span className="text-xs text-slate-400">{selected.size} selected</span>
        <button type="button" onClick={() => onSelect(new Set(allShown ? [...selected].filter((c) => !shown.some((i) => i.code === c)) : [...selected, ...shown.map((i) => i.code)]))}
          className="text-xs font-semibold text-sky-400 hover:text-sky-300">{allShown ? "Unselect the ones shown" : `Select the ${shown.length} shown`}</button>
        {selected.size > 0 && <button type="button" onClick={() => onSelect(new Set())} className="text-xs text-slate-400 hover:text-slate-200">Clear</button>}
        {(data.processes ?? []).length > 0 && (
          <select value="" onChange={(e) => { const p = data.processes?.find((x) => x.key === e.target.value); if (p) onSelect(new Set([...selected, ...p.steps])); }}
            className={`${INPUT_CLS} ml-auto w-auto text-xs`} aria-label="Select every step of a process">
            <option value="">+ Select a whole process…</option>
            {data.processes!.map((p) => <option key={p.key} value={p.key}>{p.label} ({p.steps.length})</option>)}
          </select>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-slate-500">Roles:</span>
        {data.roles.map((r) => {
          const on = roles.includes(r.id);
          return (
            <button key={r.id} type="button" onClick={() => setRoles(on ? roles.filter((id) => id !== r.id) : [...roles, r.id])}
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${on ? "border-sky-500 bg-sky-500/15 text-sky-300" : "border-slate-700 text-slate-400"} ${r.is_active ? "" : "opacity-50"}`}>
              {r.name}
            </button>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy || !selected.size || !roles.length} onClick={() => run("add")} className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-40">Give access</button>
        <button type="button" disabled={busy || !selected.size || !roles.length} onClick={() => run("remove")} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-red-500 hover:text-red-300 disabled:opacity-40">Remove access</button>
        <button type="button" disabled={busy || !selected.size || !roles.length} onClick={() => run("set")} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-sky-500 disabled:opacity-40">Only these roles</button>
        <button type="button" disabled={busy || !selected.size} onClick={() => run("everyone")} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-emerald-500 disabled:opacity-40">Everyone</button>
        <p className="w-full text-[11px] text-slate-500">An intent with no roles is open to everyone. Giving a role access to such an intent restricts it to that role. Removing an intent's last role is skipped, so it never opens up by accident.</p>
      </div>
    </div>
  );
}

/** Admin → Intents: every intent of the tenant - on/off, who may use it,
 * how it's named and described, its phrases and defaults - and new ones. */
export default function AdminIntentsSection({ onOpenFlowStudio }: { onOpenFlowStudio?: () => void }) {
  const [data, setData] = useState<AdminIntentCatalog | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [processKey, setProcessKey] = useState("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);

  function load() {
    adminIntents()
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load intents"));
  }

  useEffect(load, []);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const steps = data?.processes?.find((p) => p.key === processKey)?.steps;
    const list = (data?.intents ?? []).filter(
      (i) => (category === "all" || i.category === category) && (!steps || steps.includes(i.code)) &&
        (!q || `${i.code} ${i.label} ${i.default_label} ${i.description} ${i.phrases.join(" ")}`.toLowerCase().includes(q))
    );
    // A process lists its intents in process order.
    return steps ? [...list].sort((a, b) => steps.indexOf(a.code) - steps.indexOf(b.code)) : list;
  }, [data, query, category, processKey]);

  async function toggle(intent: IntentDef, on: boolean) {
    try {
      await updateIntent(intent.code, { is_active: on });
      setMessage(`${intent.label} switched ${on ? "on" : "off"}.`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    }
  }

  if (!data) return <p className="text-sm text-slate-500">{error || "Loading intents…"}</p>;

  const roleName = (id: number) => data.roles.find((r) => r.id === id)?.name ?? `#${id}`;
  const active = data.intents.filter((i) => i.is_active).length;
  const editingIntent = data.intents.find((i) => i.code === editing);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h2 className="text-lg font-bold text-slate-100">Intents</h2>
          <p className="text-sm text-slate-400">
            {active} of {data.intents.length} on. Decide which actions people see in the Intent Command Bar, what they're called, who may use them - or add your own.
          </p>
        </div>
        <button onClick={() => setEditing("new")} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          + New intent
        </button>
      </div>

      {message && <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">{message}</p>}
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</p>}

      {editing === "new" && (
        <NewIntentWizard data={data} onCreated={load} onClose={() => setEditing(null)} onOpenFlowStudio={onOpenFlowStudio} />
      )}
      {editingIntent && (
        <IntentEditor
          key={editingIntent.code}
          intent={editingIntent}
          data={data}
          onClose={() => setEditing(null)}
          onOpenFlowStudio={onOpenFlowStudio}
          onSaved={(m) => {
            setMessage(m);
            setEditing(null);
            load();
          }}
        />
      )}

      <BulkRolesBar data={data} selected={selected} shown={shown} onSelect={setSelected}
        onDone={(m, err) => { if (err) setError(err); else { setError(""); setMessage(m); load(); } }} />

      <div className="flex flex-wrap items-center gap-2">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search…" className={`${INPUT_CLS} max-w-xs`} />
        {(data.processes ?? []).length > 0 && (
          <select value={processKey} onChange={(e) => setProcessKey(e.target.value)} className={`${INPUT_CLS} w-auto`} aria-label="Filter by process">
            <option value="all">All processes</option>
            {data.processes!.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        )}
        {[{ key: "all", label: "All" }, ...data.categories].map((c) => (
          <button
            key={c.key}
            onClick={() => setCategory(c.key)}
            className={`rounded-full px-3 py-1 text-xs font-semibold ${category === c.key ? "bg-slate-100 text-slate-900" : "text-slate-400 hover:bg-slate-800"}`}
          >
            {c.label}
          </button>
        ))}
      </div>

      <div className="divide-y divide-slate-800 rounded-xl border border-slate-800">
        {shown.map((i) => {
          const style = categoryStyle(i.category);
          return (
            <div key={i.code} className={`flex flex-wrap items-center gap-3 px-4 py-3 ${i.is_active ? "" : "opacity-60"} ${selected.has(i.code) ? "bg-sky-500/[0.06]" : ""}`}>
              {data.roles.length > 0 && (
                <input type="checkbox" aria-label={`Select ${i.label}`} checked={selected.has(i.code)} className="h-4 w-4 accent-sky-500"
                  onChange={() => { const next = new Set(selected); if (next.has(i.code)) next.delete(i.code); else next.add(i.code); setSelected(next); }} />
              )}
              <Switch on={i.is_active} onChange={(on) => toggle(i, on)} />
              <span className={`flex h-9 w-9 items-center justify-center rounded-lg text-lg ${style.bg}`}>{i.icon}</span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-slate-100">
                  <span className="mr-2 rounded bg-slate-800 px-1.5 py-0.5 align-middle font-mono text-[11px] font-semibold text-sky-300">{i.code}</span>
                  {i.label}
                  {i.kind === "custom" && <span className="ml-2 rounded bg-fuchsia-500/15 px-1.5 py-0.5 text-[10px] font-bold text-fuchsia-300">CUSTOM</span>}
                  {i.kind === "builtin" && i.label !== i.default_label && <span className="ml-2 text-xs font-normal text-slate-500">({i.default_label})</span>}
                </p>
                <p className="truncate text-xs text-slate-400">{i.description}</p>
              </div>
              <span className={`text-xs ${style.text}`}>{data.categories.find((c) => c.key === i.category)?.label}</span>
              <span className="flex flex-wrap gap-1">
                {i.role_ids.length === 0 ? (
                  <span className="rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400">Everyone</span>
                ) : (
                  i.role_ids.map((id) => (
                    <span key={id} className="rounded-full border border-sky-500/40 px-2 py-0.5 text-[10px] text-sky-300">
                      {roleName(id)}
                    </span>
                  ))
                )}
              </span>
              <button onClick={() => setEditing(i.code)} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-semibold text-slate-300 hover:border-sky-500 hover:text-sky-300">
                Edit
              </button>
            </div>
          );
        })}
        {shown.length === 0 && <p className="px-4 py-6 text-sm text-slate-500">No intent matches.</p>}
      </div>
    </div>
  );
}
