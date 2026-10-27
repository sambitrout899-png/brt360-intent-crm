import { useCallback, useEffect, useState } from "react";
import {
  activeRecordTypes,
  createCustomRecord,
  deleteCustomRecord,
  getCustomRecord,
  listCustomRecords,
  parentChoices,
  problem,
  updateCustomRecord,
} from "../api/customRecords";
import type { CustomRecord, RecordType, RecordTypeField } from "../api/customRecords";
import { useWorkspace } from "../workspace";

/** The records of the tenant's own record types (Admin -> Record Types): find, add, change and delete them. */

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";
const BTN = "rounded-lg border border-slate-700 px-3 py-1.5 text-sm font-semibold text-slate-300 hover:border-slate-500 hover:text-slate-100";
const BTN_PRIMARY = "rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50";
const REMEMBER = "brt360.records.type";

function remembered(): string | null {
  try {
    return localStorage.getItem(REMEMBER);
  } catch {
    return null;
  }
}

function show(value: unknown, field?: RecordTypeField): string {
  if (value === null || value === undefined || value === "") return "—";
  if (field?.data_type === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export default function RecordsPage() {
  const { role } = useWorkspace();
  const canWrite = role === "admin" || role === "agent";
  const [types, setTypes] = useState<RecordType[] | null>(null);
  const [key, setKey] = useState<string | null>(remembered);
  const [error, setError] = useState("");

  useEffect(() => {
    activeRecordTypes()
      .then(setTypes)
      .catch((e) => setError(problem(e, "Could not load the record types.")));
  }, []);

  const current = types?.find((t) => t.key === key) ?? types?.[0] ?? null;
  const pick = (k: string) => {
    setKey(k);
    try {
      localStorage.setItem(REMEMBER, k);
    } catch {
      /* private window - just don't remember */
    }
  };

  if (error) return <div className="page"><p className="text-sm text-red-400">{error}</p></div>;
  if (!types) return <div className="page"><p className="text-sm text-slate-500">Loading…</p></div>;
  if (!current) {
    return (
      <div className="page">
        <div className="rounded-xl border border-dashed border-slate-700 p-10 text-center">
          <p className="text-base font-semibold text-slate-200">No record types yet</p>
          <p className="mt-1 text-sm text-slate-400">
            {role === "admin" ? "Create one in Admin → Record Types (for example a Site Survey), give it fields, and its records appear here." : "An administrator creates them in Admin → Record Types."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="page space-y-5">
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Record types">
        {types.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={t.key === current.key}
            onClick={() => pick(t.key)}
            className={`rounded-full border px-3.5 py-1.5 text-sm font-medium ${t.key === current.key ? "border-sky-500 bg-sky-500/10 text-sky-300" : "border-slate-700 text-slate-400 hover:text-slate-200"}`}
          >
            <span aria-hidden>{t.icon}</span> {t.plural_label}
          </button>
        ))}
      </div>
      <RecordList key={current.key} type={current} canWrite={canWrite} />
    </div>
  );
}

function RecordList({ type, canWrite }: { type: RecordType; canWrite: boolean }) {
  const [q, setQ] = useState("");
  const [data, setData] = useState<{ total: number; items: CustomRecord[] } | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<number | "new" | null>(null);

  const load = useCallback(() => {
    listCustomRecords(type.key, { q: q.trim() || undefined })
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(problem(e, "Could not load the records.")));
  }, [type.key, q]);

  useEffect(() => {
    const t = window.setTimeout(load, 250);
    return () => window.clearTimeout(t);
  }, [load]);

  const shown = type.fields.slice(0, 3);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold text-slate-100">
            {type.icon} {type.plural_label}
          </h1>
          {type.description && <p className="mt-0.5 text-sm text-slate-400">{type.description}</p>}
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search by name or ${type.number_prefix}-…`} aria-label="Search" className="w-64 max-w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200" />
        {canWrite && (
          <button onClick={() => setOpen("new")} className={BTN_PRIMARY}>
            + New {type.label}
          </button>
        )}
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="overflow-x-auto rounded-xl border border-slate-800">
        <table className="w-full min-w-[40rem] text-left text-sm">
          <thead className="bg-slate-900/70 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2.5 font-semibold">Number</th>
              <th className="px-4 py-2.5 font-semibold">Name</th>
              {type.parent_label && <th className="px-4 py-2.5 font-semibold">{type.parent_label}</th>}
              {shown.map((f) => (
                <th key={f.key} className="px-4 py-2.5 font-semibold">{f.label}</th>
              ))}
              <th className="px-4 py-2.5 font-semibold">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {data?.items.map((r) => (
              <tr key={r.id} onClick={() => setOpen(r.id)} className="cursor-pointer hover:bg-slate-900/60">
                <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-sky-300">{r.number}</td>
                <td className="px-4 py-2.5 font-medium text-slate-100">{r.name}</td>
                {type.parent_label && <td className="px-4 py-2.5 text-slate-300">{r.parent?.title ?? "—"}</td>}
                {shown.map((f) => (
                  <td key={f.key} className="px-4 py-2.5 text-slate-300">{show(r.values[f.key], f)}</td>
                ))}
                <td className="whitespace-nowrap px-4 py-2.5 text-slate-500">{when(r.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.items.length === 0 && (
          <p className="px-4 py-8 text-center text-sm text-slate-500">{q ? "Nothing matches." : `No ${type.plural_label.toLowerCase()} yet.`}</p>
        )}
        {!data && !error && <p className="px-4 py-8 text-center text-sm text-slate-500">Loading…</p>}
      </div>
      {data && data.total > data.items.length && <p className="text-xs text-slate-500">Showing the latest {data.items.length} of {data.total} - search to narrow down.</p>}

      {open !== null && (
        <RecordDrawer
          key={String(open)}
          type={type}
          recordId={open === "new" ? null : open}
          canWrite={canWrite}
          onClose={() => setOpen(null)}
          onSaved={(id) => {
            load();
            setOpen(id);
          }}
          onDeleted={() => {
            load();
            setOpen(null);
          }}
        />
      )}
    </div>
  );
}

function initialValues(type: RecordType, record: CustomRecord | null): Record<string, unknown> {
  return Object.fromEntries(type.fields.map((f) => [f.key, record ? record.values[f.key] ?? "" : f.default_value ?? (f.data_type === "boolean" ? false : "")]));
}

function RecordDrawer({
  type, recordId, canWrite, onClose, onSaved, onDeleted,
}: {
  type: RecordType;
  recordId: number | null;
  canWrite: boolean;
  onClose: () => void;
  onSaved: (id: number) => void;
  onDeleted: () => void;
}) {
  const [record, setRecord] = useState<CustomRecord | null>(null);
  const [name, setName] = useState("");
  const [parent, setParent] = useState<{ id: number; title: string } | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>(() => initialValues(type, null));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (recordId === null) return;
    getCustomRecord(type.key, recordId)
      .then(({ record: r }) => {
        setRecord(r);
        setName(r.name);
        setParent(r.parent ? { id: r.parent.id, title: r.parent.title } : null);
        setValues(initialValues(type, r));
      })
      .catch((e) => setError(problem(e, "Could not open the record.")));
  }, [type, recordId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function save() {
    setBusy(true);
    setError("");
    try {
      const fields = Object.fromEntries(type.fields.map((f) => [f.key, values[f.key] === "" ? null : values[f.key]]));
      const body = { name: name.trim(), parent_id: parent?.id ?? null, fields };
      const saved = recordId === null ? await createCustomRecord(type.key, body) : await updateCustomRecord(type.key, recordId, body);
      onSaved(saved.id);
    } catch (e) {
      setError(problem(e, "Could not save."));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (recordId === null) return;
    setBusy(true);
    try {
      await deleteCustomRecord(type.key, recordId);
      onDeleted();
    } catch (e) {
      setError(problem(e, "Could not delete."));
      setConfirmDelete(false);
    } finally {
      setBusy(false);
    }
  }

  const loading = recordId !== null && !record && !error;
  const readOnly = !canWrite;
  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/50" onClick={onClose} aria-hidden="true" />
      <aside role="dialog" aria-label={record ? `${type.label} ${record.number}` : `New ${type.label}`} className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-slate-800 bg-[#0a101d] shadow-2xl">
        <header className="flex items-start gap-3 border-b border-slate-800 px-5 py-4">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {type.icon} {type.label} {record && <span className="font-mono text-sky-300">{record.number}</span>}
            </p>
            <h2 className="truncate text-lg font-semibold text-slate-100">{record ? record.name : `New ${type.label}`}</h2>
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200">✕</button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {loading && <p className="text-sm text-slate-500">Loading…</p>}
          {!loading && (
            <>
              <label className="block text-xs text-slate-400">
                Name <span className="text-amber-400">*</span>
                <input value={name} onChange={(e) => setName(e.target.value)} disabled={readOnly} className={INPUT} autoFocus={recordId === null} />
              </label>
              {type.parent_entity_type && (
                <ParentPicker type={type} value={parent} onChange={setParent} disabled={readOnly} />
              )}
              {type.fields.map((f) => (
                <FieldInput key={f.key} field={f} value={values[f.key]} disabled={readOnly} onChange={(v) => setValues((cur) => ({ ...cur, [f.key]: v }))} />
              ))}
              {type.fields.length === 0 && <p className="text-xs text-slate-500">This type has no fields of its own yet - an administrator adds them in Admin → Record Types.</p>}

              {record && (
                <div className="space-y-1 border-t border-slate-800 pt-3 text-xs text-slate-500">
                  {record.children?.map((c) => (
                    <p key={c.key}>{c.count} {c.label.toLowerCase()} hang off this record</p>
                  ))}
                  <p>Created {when(record.created_at)}{record.created_by ? ` by ${record.created_by}` : ""} · updated {when(record.updated_at)}</p>
                  {record.brt_id && <p className="font-mono">BRT_ID {record.brt_id}</p>}
                </div>
              )}
            </>
          )}
          {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
        </div>

        {canWrite && !loading && (
          <footer className="flex items-center gap-2 border-t border-slate-800 px-5 py-3">
            {recordId !== null && !confirmDelete && (
              <button onClick={() => setConfirmDelete(true)} disabled={busy} className={`${BTN} hover:border-red-500 hover:text-red-400`}>Delete</button>
            )}
            {confirmDelete && (
              <span className="flex items-center gap-2 text-sm text-red-300">
                Delete for good?
                <button onClick={remove} disabled={busy} className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-500">Delete</button>
                <button onClick={() => setConfirmDelete(false)} className={BTN}>Keep</button>
              </span>
            )}
            <span className="flex-1" />
            <button onClick={onClose} className={BTN}>Cancel</button>
            <button onClick={save} disabled={busy || !name.trim()} className={BTN_PRIMARY}>{busy ? "Saving…" : recordId === null ? `Create ${type.label}` : "Save"}</button>
          </footer>
        )}
      </aside>
    </>
  );
}

function ParentPicker({ type, value, onChange, disabled }: {
  type: RecordType;
  value: { id: number; title: string } | null;
  onChange: (v: { id: number; title: string } | null) => void;
  disabled: boolean;
}) {
  const [q, setQ] = useState("");
  const [options, setOptions] = useState<{ id: number; title: string }[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (!searching) return;
    const t = window.setTimeout(() => parentChoices(type.key, q.trim()).then(setOptions).catch(() => setOptions([])), 200);
    return () => window.clearTimeout(t);
  }, [type.key, q, searching]);

  return (
    <div className="text-xs text-slate-400">
      {type.parent_label} {type.parent_required && <span className="text-amber-400">*</span>}
      {value && !searching ? (
        <div className="mt-1 flex items-center gap-2 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
          <span className="min-w-0 flex-1 truncate">{value.title}</span>
          {!disabled && (
            <>
              <button type="button" onClick={() => setSearching(true)} className="text-xs text-sky-400 hover:text-sky-300">Change</button>
              <button type="button" onClick={() => onChange(null)} className="text-xs text-slate-500 hover:text-red-400">Remove</button>
            </>
          )}
        </div>
      ) : disabled ? (
        <p className="mt-1 text-sm text-slate-500">—</p>
      ) : (
        <div className="relative">
          <input value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => setSearching(true)} placeholder={`Search ${type.parent_label?.toLowerCase()}…`} className={INPUT} />
          {searching && options.length > 0 && (
            <ul className="absolute z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-md border border-slate-700 bg-slate-900 py-1 shadow-xl">
              {options.map((o) => (
                <li key={o.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onChange(o);
                      setSearching(false);
                      setQ("");
                    }}
                    className="block w-full px-3 py-1.5 text-left text-sm text-slate-200 hover:bg-slate-800"
                  >
                    {o.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {searching && options.length === 0 && <p className="mt-1 text-slate-500">Nothing found.</p>}
        </div>
      )}
    </div>
  );
}

function FieldInput({ field, value, onChange, disabled }: { field: RecordTypeField; value: unknown; onChange: (v: unknown) => void; disabled: boolean }) {
  const text = value === null || value === undefined ? "" : String(value);
  const label = (
    <>
      {field.label} {field.required && <span className="text-amber-400">*</span>}
    </>
  );
  const help = field.help_text && <span className="mt-1 block text-[11px] text-slate-500">{field.help_text}</span>;
  if (field.data_type === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={value === true || value === "true"} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        {label}
        {help}
      </label>
    );
  }
  let input;
  if (field.data_type === "picklist") {
    input = (
      <select value={text} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={INPUT}>
        <option value="">—</option>
        {field.allowed_values.map((v) => (
          <option key={v} value={v}>{v}</option>
        ))}
      </select>
    );
  } else if (field.data_type === "textarea") {
    input = <textarea value={text} disabled={disabled} onChange={(e) => onChange(e.target.value)} rows={3} className={INPUT} />;
  } else {
    const kind = { number: "number", date: "date", email: "email", phone: "tel" }[field.data_type] ?? "text";
    input = <input type={kind} value={text} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={INPUT} />;
  }
  return (
    <label className="block text-xs text-slate-400">
      {label}
      {input}
      {help}
    </label>
  );
}
