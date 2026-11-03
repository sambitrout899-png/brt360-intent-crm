import { useCallback, useEffect, useMemo, useState } from "react";
import { request } from "../api/client";
import {
  activeRecordTypes,
  assignCustomRecord,
  createCustomRecord,
  deleteCustomRecord,
  getCustomRecord,
  linkCustomRecord,
  listCustomRecords,
  moveCustomRecord,
  problem,
  recordForm,
  searchRecords,
  updateCustomRecord,
} from "../api/customRecords";
import type { CustomRecord, RecordForm, RecordFormField, RecordType, RelatedSection } from "../api/customRecords";
import { useWorkspace } from "../workspace";

/** The records of the tenant's own record types (Admin -> Record Types). Each record shows the page its
 *  administrator designed (Page Designer), moves through its state model, can be assigned, and lists what it
 *  is related to. Every change runs as an intent - flows, assignment rules and the audit trail apply. */

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 disabled:opacity-60";
const BTN = "rounded-lg border border-slate-700 px-3 py-1.5 text-sm font-semibold text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-50";
const SMALL = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-semibold text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-50";
const BTN_PRIMARY = "rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50";
const REMEMBER = "brt360.records.type";

type Opening = { key: string; id: number | "new"; parent?: { id: number; title: string } };

function remembered(): string | null {
  try {
    return localStorage.getItem(REMEMBER);
  } catch {
    return null;
  }
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function StateBadge({ type, state, label }: { type: RecordType; state: string; label?: string }) {
  const def = type.states.find((s) => s.code === state);
  const tone = def?.is_terminal ? "border-slate-600 bg-slate-800 text-slate-300" : def && !def.editable ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300" : "border-sky-500/40 bg-sky-500/10 text-sky-300";
  return <span className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold ${tone}`}>{label ?? def?.label ?? state.replace(/_/g, " ")}</span>;
}

export default function RecordsPage() {
  const { role } = useWorkspace();
  const canWrite = role === "admin" || role === "agent";
  const [types, setTypes] = useState<RecordType[] | null>(null);
  const [key, setKey] = useState<string | null>(remembered);
  const [opening, setOpening] = useState<Opening | null>(null);
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
  // a related record of another type opens on its own tab
  const open = (o: Opening) => {
    pick(o.key);
    setOpening(o);
  };

  if (error) return <div className="page"><p className="text-sm text-red-400">{error}</p></div>;
  if (!types) return <div className="page"><p className="text-sm text-slate-500">Loading…</p></div>;
  if (!current) {
    return (
      <div className="page">
        <div className="rounded-xl border border-dashed border-slate-700 p-10 text-center">
          <p className="text-base font-semibold text-slate-200">No record types yet</p>
          <p className="mt-1 text-sm text-slate-400">
            {role === "admin"
              ? "Describe one in Admin → Record Types (for example “Site Survey for a customer with survey date and floors; states scheduled → completed”) and its records appear here."
              : "An administrator creates them in Admin → Record Types."}
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
      <RecordList
        key={current.key}
        type={current}
        types={types}
        canWrite={canWrite}
        opening={opening?.key === current.key ? opening : null}
        onOpened={() => setOpening(null)}
        onOpenOther={open}
      />
    </div>
  );
}

function cell(type: RecordType, r: CustomRecord, key: string) {
  if (key === "state") return <StateBadge type={type} state={r.state} label={r.state_label} />;
  if (key === "owner") return r.owner ?? "—";
  if (key === "parent") return r.parent?.title ?? "—";
  const shown = r.display?.[key];
  if (shown) return shown.title;
  const value = r.values[key];
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function RecordList({ type, types, canWrite, opening, onOpened, onOpenOther }: {
  type: RecordType;
  types: RecordType[];
  canWrite: boolean;
  opening: Opening | null;
  onOpened: () => void;
  onOpenOther: (o: Opening) => void;
}) {
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [mine, setMine] = useState(false);
  const [data, setData] = useState<{ total: number; items: CustomRecord[]; state_counts: Record<string, number> } | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<{ id: number | "new"; parent?: { id: number; title: string } } | null>(null);

  const load = useCallback(() => {
    listCustomRecords(type.key, { q: q.trim() || undefined, state: state || undefined, mine })
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(problem(e, "Could not load the records.")));
  }, [type.key, q, state, mine]);

  useEffect(() => {
    const t = window.setTimeout(load, 250);
    return () => window.clearTimeout(t);
  }, [load]);

  useEffect(() => {
    if (opening) {
      setOpen({ id: opening.id, parent: opening.parent });
      onOpened();
    }
  }, [opening, onOpened]);

  const columns = type.list_fields.filter((c) => c === "state" || c === "owner" || type.fields.some((f) => f.key === c));
  const label = (c: string) => (c === "state" ? "State" : c === "owner" ? "Owner" : type.fields.find((f) => f.key === c)?.label ?? c);
  const total = data ? Object.values(data.state_counts).reduce((a, b) => a + b, 0) : 0;
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
          <button onClick={() => setOpen({ id: "new" })} className={BTN_PRIMARY}>
            + New {type.label}
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1.5" aria-label="Filter by state">
        <button onClick={() => setState("")} className={`rounded-full border px-2.5 py-1 text-xs ${state === "" ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400"}`}>All {data ? `· ${total}` : ""}</button>
        {type.states.map((s) => (
          <button key={s.code} onClick={() => setState(state === s.code ? "" : s.code)}
            className={`rounded-full border px-2.5 py-1 text-xs ${state === s.code ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400"}`}>
            {s.label} {data ? `· ${data.state_counts[s.code] ?? 0}` : ""}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1.5 text-xs text-slate-400">
          <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Mine
        </label>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="overflow-x-auto rounded-xl border border-slate-800">
        <table className="w-full min-w-[40rem] text-left text-sm">
          <thead className="bg-slate-900/70 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2.5 font-semibold">Number</th>
              <th className="px-4 py-2.5 font-semibold">{type.name_label || "Name"}</th>
              {type.parent_label && <th className="px-4 py-2.5 font-semibold">{type.parent_label}</th>}
              {columns.map((c) => (
                <th key={c} className="px-4 py-2.5 font-semibold">{label(c)}</th>
              ))}
              <th className="px-4 py-2.5 font-semibold">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {data?.items.map((r) => (
              <tr key={r.id} onClick={() => setOpen({ id: r.id })} className="cursor-pointer hover:bg-slate-900/60">
                <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-sky-300">{r.number}</td>
                <td className="px-4 py-2.5 font-medium text-slate-100">{r.name}</td>
                {type.parent_label && <td className="px-4 py-2.5 text-slate-300">{r.parent?.title ?? "—"}</td>}
                {columns.map((c) => (
                  <td key={c} className="px-4 py-2.5 text-slate-300">{cell(type, r, c)}</td>
                ))}
                <td className="whitespace-nowrap px-4 py-2.5 text-slate-500">{when(r.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.items.length === 0 && (
          <p className="px-4 py-8 text-center text-sm text-slate-500">{q || state || mine ? "Nothing matches." : `No ${type.plural_label.toLowerCase()} yet.`}</p>
        )}
        {!data && !error && <p className="px-4 py-8 text-center text-sm text-slate-500">Loading…</p>}
      </div>
      {data && data.total > data.items.length && <p className="text-xs text-slate-500">Showing the latest {data.items.length} of {data.total} - search to narrow down.</p>}

      {open !== null && (
        <RecordDrawer
          key={`${open.id}-${open.parent?.id ?? ""}`}
          type={type}
          types={types}
          recordId={open.id === "new" ? null : open.id}
          initialParent={open.parent ?? null}
          canWrite={canWrite}
          onClose={() => setOpen(null)}
          onSaved={(id) => {
            load();
            setOpen({ id });
          }}
          onChanged={load}
          onDeleted={() => {
            load();
            setOpen(null);
          }}
          onOpenOther={onOpenOther}
        />
      )}
    </div>
  );
}

type Tab = "details" | "related" | "history";

function RecordDrawer({
  type, types, recordId, initialParent, canWrite, onClose, onSaved, onChanged, onDeleted, onOpenOther,
}: {
  type: RecordType;
  types: RecordType[];
  recordId: number | null;
  initialParent: { id: number; title: string } | null;
  canWrite: boolean;
  onClose: () => void;
  onSaved: (id: number) => void;
  onChanged: () => void;
  onDeleted: () => void;
  onOpenOther: (o: Opening) => void;
}) {
  const [record, setRecord] = useState<CustomRecord | null>(null);
  const [form, setForm] = useState<RecordForm | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<Tab>("details");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [assigning, setAssigning] = useState(false);

  const fill = useCallback((r: CustomRecord | null, f: RecordForm) => {
    const v: Record<string, unknown> = {};
    for (const s of f.sections) for (const fld of s.fields) if (!fld.kind) v[fld.key] = fld.value ?? "";
    const t: Record<string, string> = {};
    if (r?.parent) t.parent_id = r.parent.title;
    for (const [key, ref] of Object.entries(r?.display ?? {})) if (ref) t[key] = ref.title;
    if (!r && initialParent) {
      v.parent_id = initialParent.id;
      t.parent_id = initialParent.title;
    }
    setValues(v);
    setTitles(t);
  }, [initialParent]);

  const reload = useCallback(async () => {
    try {
      const [r, f] = await Promise.all([recordId === null ? Promise.resolve(null) : getCustomRecord(type.key, recordId).then((x) => x.record), recordForm(type.key, recordId)]);
      setRecord(r);
      setForm(f);
      fill(r, f);
    } catch (e) {
      setError(problem(e, "Could not open the record."));
    }
  }, [type.key, recordId, fill]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function act(action: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await action();
      if (done) setNote(done);
      onChanged();
      await reload();
    } catch (e) {
      setError(problem(e, "That didn't go through."));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError("");
    setNote("");
    try {
      const fields: Record<string, unknown> = {};
      for (const s of form?.sections ?? []) for (const f of s.fields) {
        if (f.kind || f.core || f.readonly) continue;
        fields[f.key] = values[f.key] === "" ? null : values[f.key];
      }
      const body = { name: String(values.name ?? "").trim(), parent_id: (values.parent_id as number | null) || null, fields };
      const saved = recordId === null ? await createCustomRecord(type.key, body) : await updateCustomRecord(type.key, recordId, body);
      onSaved(saved.id);
      if (recordId !== null) {
        setNote("Saved.");
        await reload();
      }
    } catch (e) {
      setError(problem(e, "Could not save."));
    } finally {
      setBusy(false);
    }
  }

  async function runButton(intent: string) {
    if (!record) return;
    await act(async () => {
      const out = await request<{ status: string; summary?: string; rationale?: string }>("/api/intents", {
        method: "POST", body: JSON.stringify({ text: "", structured: { intent_type: intent, entities: { entity_type: type.entity_type, entity_id: record.id } } }),
      });
      if (out.status !== "executed") throw new Error(out.summary || out.rationale || "It didn't run.");
    }, "Done.");
  }

  const loading = !form && !error;
  const editable = canWrite && (record ? record.editable : true);
  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/50" onClick={onClose} aria-hidden="true" />
      <aside role="dialog" aria-label={record ? `${type.label} ${record.number}` : `New ${type.label}`} className="fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col border-l border-slate-800 bg-[#0a101d] shadow-2xl">
        <header className="space-y-3 border-b border-slate-800 px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {type.icon} {type.label} {record && <span className="font-mono text-sky-300">{record.number}</span>}
              </p>
              <h2 className="truncate text-lg font-semibold text-slate-100">{record ? record.name : `New ${type.label}`}</h2>
              {record && (
                <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  <StateBadge type={type} state={record.state} label={record.state_label} />
                  <span>Owner: {record.owner ?? "not assigned"}</span>
                  {record.parent && (
                    <button className="text-sky-400 hover:text-sky-300" onClick={() => record.parent?.custom_key && onOpenOther({ key: record.parent.custom_key, id: record.parent.id })}>
                      ↑ {record.parent.title}
                    </button>
                  )}
                </p>
              )}
            </div>
            <button onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200">✕</button>
          </div>
          {record && canWrite && (
            <div className="flex flex-wrap items-center gap-1.5">
              {(record.transitions ?? []).map((m) => (
                <button key={`${m.action}-${m.to_state}`} disabled={busy || !!m.passed} title={m.passed ?? `Move to ${m.to_label}`}
                  onClick={() => act(() => moveCustomRecord(type.key, record.id, m.to_state), `Moved to ${m.to_label}.`)} className={SMALL}>
                  {m.action.replace(/_/g, " ")} → {m.to_label}
                </button>
              ))}
              <button onClick={() => setAssigning((a) => !a)} className={SMALL}>👥 Assign</button>
            </div>
          )}
          {assigning && record && (
            <AssignForm onCancel={() => setAssigning(false)} busy={busy}
              onAssign={(body) => act(async () => { await assignCustomRecord(type.key, record.id, body); setAssigning(false); }, "Assigned.")} />
          )}
          {record && (
            <div className="flex gap-1 text-sm" role="tablist">
              {(["details", "related", "history"] as Tab[]).map((t) => (
                <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                  className={`rounded-md px-3 py-1 ${tab === t ? "bg-slate-800 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>
                  {t === "details" ? "Details" : t === "related" ? `Related${record.related ? ` · ${record.related.reduce((n, s) => n + s.items.length, 0)}` : ""}` : "History"}
                </button>
              ))}
            </div>
          )}
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {loading && <p className="text-sm text-slate-500">Loading…</p>}
          {form && tab === "details" && (
            <>
              {record && !record.editable && (
                <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  {record.state_label} - this {type.label.toLowerCase()} can no longer be changed. Move it to another state to edit it.
                </p>
              )}
              {form.sections.map((s) => (
                <fieldset key={s.title} className="space-y-3">
                  <legend className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{s.title}</legend>
                  <div className={`grid gap-3 ${s.columns === 2 ? "sm:grid-cols-2" : ""}`}>
                    {s.fields.map((f) => (
                      <div key={f.key} className={f.span === "full" || f.kind === "text" ? "sm:col-span-2" : ""}>
                        <FormField field={f} type={type} value={values[f.key]} title={titles[f.key]} disabled={!editable || busy} isNew={record === null}
                          onChange={(v, title) => {
                            setValues((cur) => ({ ...cur, [f.key]: v }));
                            if (title !== undefined) setTitles((cur) => ({ ...cur, [f.key]: title }));
                          }}
                          onRun={runButton} />
                      </div>
                    ))}
                  </div>
                </fieldset>
              ))}
            </>
          )}
          {record && tab === "related" && (
            <RelatedPanel sections={record.related ?? []} types={types} canWrite={canWrite} busy={busy}
              onOpen={onOpenOther}
              onNewChild={(section) => {
                const child = types.find((t) => t.entity_type === section.entity_type);
                if (child) onOpenOther({ key: child.key, id: "new", parent: { id: record.id, title: `${record.number} · ${record.name}` } });
              }}
              onLink={(section, related, unlink) => act(() => linkCustomRecord(type.key, record.id, { relationship: section.relationship ?? "", related, unlink }),
                unlink ? "Unlinked." : "Linked.")} />
          )}
          {record && tab === "history" && (
            <div className="space-y-2 text-sm">
              {(record.history ?? []).map((h, i) => (
                <div key={i} className="flex items-center gap-2 rounded-lg border border-slate-800 px-3 py-2">
                  <span className="text-slate-500">{when(h.at)}</span>
                  {h.from_state ? <StateBadge type={type} state={h.from_state} /> : <span className="text-xs text-slate-500">created</span>}
                  <span className="text-slate-500">→</span>
                  <StateBadge type={type} state={h.to_state} />
                  <span className="ml-auto text-xs text-slate-500">{h.source.replace("intent:", "").replace("flow:", "flow #")}</span>
                </div>
              ))}
              <p className="text-xs text-slate-500">Created {when(record.created_at)}{record.created_by ? ` by ${record.created_by}` : ""} · updated {when(record.updated_at)} · every change is in the Audit Log.</p>
              {record.brt_id && <p className="font-mono text-xs text-slate-500">BRT_ID {record.brt_id}</p>}
            </div>
          )}
          {note && <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">{note}</p>}
          {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
        </div>

        {canWrite && form && (
          <footer className="flex items-center gap-2 border-t border-slate-800 px-5 py-3">
            {recordId !== null && !confirmDelete && (
              <button onClick={() => setConfirmDelete(true)} disabled={busy} className={`${BTN} hover:border-red-500 hover:text-red-400`}>Delete</button>
            )}
            {confirmDelete && record && (
              <span className="flex items-center gap-2 text-sm text-red-300">
                Delete for good?
                <button onClick={async () => {
                  setBusy(true);
                  try {
                    await deleteCustomRecord(type.key, record.id);
                    onDeleted();
                  } catch (e) {
                    setError(problem(e, "Could not delete."));
                    setConfirmDelete(false);
                  } finally {
                    setBusy(false);
                  }
                }} disabled={busy} className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-500">Delete</button>
                <button onClick={() => setConfirmDelete(false)} className={BTN}>Keep</button>
              </span>
            )}
            <span className="flex-1" />
            <button onClick={onClose} className={BTN}>Close</button>
            {tab === "details" && editable && (
              <button onClick={save} disabled={busy || !String(values.name ?? "").trim()} className={BTN_PRIMARY}>
                {busy ? "Saving…" : recordId === null ? `Create ${type.label}` : "Save"}
              </button>
            )}
          </footer>
        )}
      </aside>
    </>
  );
}

function AssignForm({ onAssign, onCancel, busy }: { onAssign: (b: { to_user?: string; to_position?: string; to_department?: string }) => void; onCancel: () => void; busy: boolean }) {
  const [to, setTo] = useState<"to_position" | "to_user" | "to_department">("to_position");
  const [who, setWho] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 p-2">
      <select value={to} onChange={(e) => setTo(e.target.value as typeof to)} aria-label="Assign to" className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
        <option value="to_position">a position</option>
        <option value="to_user">a person (email or name)</option>
        <option value="to_department">a department</option>
      </select>
      <input value={who} onChange={(e) => setWho(e.target.value)} placeholder="e.g. Survey Team" className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200" />
      <button disabled={busy || !who.trim()} onClick={() => onAssign({ [to]: who.trim() })} className={SMALL}>Assign</button>
      <button onClick={onCancel} className="text-xs text-slate-500 hover:text-slate-300">Cancel</button>
    </div>
  );
}

function FormField({ field, type, value, title, disabled, isNew, onChange, onRun }: {
  field: RecordFormField;
  type: RecordType;
  value: unknown;
  title?: string;
  disabled: boolean;
  isNew: boolean;
  onChange: (v: unknown, title?: string) => void;
  onRun: (intent: string) => void;
}) {
  if (field.kind === "text") return <p className="whitespace-pre-line rounded-lg bg-slate-900/60 px-3 py-2 text-xs text-slate-400">{field.text}</p>;
  if (field.kind === "button") {
    return isNew ? null : (
      <button type="button" onClick={() => field.intent && onRun(field.intent)} className={field.style === "primary" ? BTN_PRIMARY : field.style === "danger" ? `${BTN} hover:border-red-500 hover:text-red-400` : BTN}>
        {field.label}
      </button>
    );
  }
  const label = (
    <>
      {field.label} {field.mandatory && <span className="text-amber-400">*</span>}
    </>
  );
  const help = field.help && <span className="mt-1 block text-[11px] text-slate-500">{field.help}</span>;
  if (field.key === "state") {
    return (
      <div className="text-xs text-slate-400">
        {field.label}
        <div className="mt-1.5">{isNew ? <span className="text-slate-500">{type.states[0]?.label ?? "—"} when created</span> : <StateBadge type={type} state={String(value ?? "")} />}</div>
      </div>
    );
  }
  const off = disabled || field.readonly;
  const text = value === null || value === undefined ? "" : String(value);
  if (field.type === "lookup") {
    return (
      <div className="text-xs text-slate-400">
        {label}
        <LookupPicker target={field.values[0] ?? ""} value={typeof value === "number" ? value : value ? Number(value) : null} title={title} disabled={off}
          onChange={(id, t) => onChange(id ?? "", t ?? "")} />
        {help}
      </div>
    );
  }
  if (field.type === "boolean") {
    return (
      <label className="mt-5 flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={value === true || value === "true"} disabled={off} onChange={(e) => onChange(e.target.checked)} />
        {label}
        {help}
      </label>
    );
  }
  let input;
  if (field.type === "picklist") {
    input = (
      <select value={text} disabled={off} onChange={(e) => onChange(e.target.value)} className={INPUT}>
        <option value="">—</option>
        {field.values.map((v) => (
          <option key={v} value={v}>{v}</option>
        ))}
      </select>
    );
  } else if (field.type === "textarea") {
    input = <textarea value={text} disabled={off} onChange={(e) => onChange(e.target.value)} rows={3} className={INPUT} />;
  } else {
    const kind = { number: "number", date: "date", email: "email", phone: "tel" }[field.type] ?? "text";
    input = <input type={kind} value={text} disabled={off} onChange={(e) => onChange(e.target.value)} className={INPUT} autoFocus={isNew && field.key === "name"} />;
  }
  return (
    <label className="block text-xs text-slate-400">
      {label}
      {input}
      {help}
    </label>
  );
}

/** Picks one record of any type the user may see - by name or number. */
function LookupPicker({ target, value, title, disabled, onChange, placeholder }: {
  target: string;
  value: number | null;
  title?: string;
  disabled: boolean;
  onChange: (id: number | null, title?: string) => void;
  placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [options, setOptions] = useState<{ id: number; title: string; hint: string }[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (!searching || !target) return;
    const t = window.setTimeout(() => searchRecords(target, q.trim()).then(setOptions).catch(() => setOptions([])), 200);
    return () => window.clearTimeout(t);
  }, [target, q, searching]);

  if (value && !searching) {
    return (
      <div className="mt-1 flex items-center gap-2 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
        <span className="min-w-0 flex-1 truncate">{title || `#${value}`}</span>
        {!disabled && (
          <>
            <button type="button" onClick={() => setSearching(true)} className="text-xs text-sky-400 hover:text-sky-300">Change</button>
            <button type="button" onClick={() => onChange(null, "")} className="text-xs text-slate-500 hover:text-red-400">Remove</button>
          </>
        )}
      </div>
    );
  }
  if (disabled) return <p className="mt-1 text-sm text-slate-500">—</p>;
  return (
    <div className="relative">
      <input value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => setSearching(true)} placeholder={placeholder ?? "Search by name or number…"} className={INPUT} />
      {searching && options.length > 0 && (
        <ul className="absolute z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-md border border-slate-700 bg-slate-900 py-1 shadow-xl">
          {options.map((o) => (
            <li key={o.id}>
              <button type="button" onClick={() => { onChange(o.id, o.title); setSearching(false); setQ(""); }}
                className="block w-full px-3 py-1.5 text-left text-sm text-slate-200 hover:bg-slate-800">
                {o.title} {o.hint && <span className="text-xs text-slate-500">· {o.hint}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {searching && options.length === 0 && <p className="mt-1 text-slate-500">Nothing found.</p>}
    </div>
  );
}

function RelatedPanel({ sections, types, canWrite, busy, onOpen, onNewChild, onLink }: {
  sections: RelatedSection[];
  types: RecordType[];
  canWrite: boolean;
  busy: boolean;
  onOpen: (o: Opening) => void;
  onNewChild: (s: RelatedSection) => void;
  onLink: (s: RelatedSection, related: number, unlink: boolean) => void;
}) {
  const [adding, setAdding] = useState<string | null>(null);
  const known = useMemo(() => new Set(types.map((t) => t.key)), [types]);
  if (sections.length === 0) {
    return <p className="text-sm text-slate-500">Nothing is related to this record type yet - an administrator adds relationships, lookups and types that hang off it in Admin → Record Types.</p>;
  }
  return (
    <div className="space-y-4">
      {sections.map((s) => (
        <section key={s.key} className="rounded-xl border border-slate-800 p-3">
          <div className="mb-2 flex items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-200">{s.label}</h3>
            <span className="text-xs text-slate-500">{s.items.length}{s.kind === "link" ? " linked" : ""}</span>
            <span className="flex-1" />
            {canWrite && s.kind === "children" && <button onClick={() => onNewChild(s)} className={SMALL}>+ New</button>}
            {canWrite && s.kind === "link" && <button onClick={() => setAdding(adding === s.key ? null : s.key)} className={SMALL}>🔗 Link</button>}
          </div>
          {adding === s.key && (
            <div className="mb-2">
              <LookupPicker target={s.entity_type} value={null} disabled={busy} placeholder={`Find a ${s.type_label.toLowerCase()}…`}
                onChange={(id) => { if (id) { onLink(s, id, false); setAdding(null); } }} />
            </div>
          )}
          <ul className="divide-y divide-slate-800/70">
            {s.items.map((i) => (
              <li key={i.id} className="flex items-center gap-2 py-1.5 text-sm">
                {i.custom_key && known.has(i.custom_key) ? (
                  <button onClick={() => onOpen({ key: i.custom_key as string, id: i.id })} className="min-w-0 flex-1 truncate text-left text-sky-300 hover:text-sky-200">{i.title}</button>
                ) : (
                  <span className="min-w-0 flex-1 truncate text-slate-200">{i.title}</span>
                )}
                <span className="text-xs text-slate-500">{i.state.replace(/_/g, " ")}</span>
                {canWrite && s.kind === "link" && (
                  <button disabled={busy} onClick={() => onLink(s, i.id, true)} className="text-xs text-slate-500 hover:text-red-400" aria-label={`Unlink ${i.title}`}>Unlink</button>
                )}
              </li>
            ))}
            {s.items.length === 0 && <li className="py-1.5 text-xs text-slate-500">None yet.</li>}
          </ul>
        </section>
      ))}
    </div>
  );
}
