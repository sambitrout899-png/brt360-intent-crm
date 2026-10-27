import { useEffect, useRef, useState } from "react";
import { EdgeHandle } from "./Resizable";
import { useStoredSize } from "./useStoredSize";
import { listAssignees, pageForm, recordApprovals, recordLifecycle, submitStructuredIntent, type FormField } from "../api/client";
import { PageButton } from "./pages/PageFields";
import { ENTITY_LABELS } from "../api/types";
import type { ApprovalRequest, Assignees, EntityType, IntentResponse, RecordAction, RecordLifecycle } from "../api/types";
import { notifyIntentExecuted, useIntentRefresh, useWorkspace } from "../workspace";
import IntentFollowUp from "./IntentFollowUp";
import IntentResult from "./intent/IntentResult";
import QuoteDocuments from "./QuoteDocuments";
import PresalesNetwork from "./PresalesNetwork";

type FieldValue = string | number | boolean | null;

function display(value: FieldValue): string {
  if (value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

/** Everything the panel changes goes through an intent (INT-29 update,
 * INT-31 transition, INT-32 assign, or the transition's owning intent), so
 * gates, flows and the audit log apply exactly as they do from the drawer. */
export default function RecordPanel({
  entityType,
  entityId,
  onClose,
  onOpenRecord,
}: {
  entityType: EntityType;
  entityId: number;
  onClose: () => void;
  onOpenRecord: (entityType: EntityType, entityId: number) => void;
}) {
  const { runIntent, canRun, openAbout } = useWorkspace();
  // the panel's width - drag its left edge (remembered)
  const panel = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useStoredSize("recordPanel", null);
  const [record, setRecord] = useState<RecordLifecycle | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  // buttons the Page Designer put on this record type's page
  const [pageButtons, setPageButtons] = useState<FormField[]>([]);
  const [loadError, setLoadError] = useState("");
  const [mode, setMode] = useState<"view" | "edit" | "assign">("view");
  const [draft, setDraft] = useState<Record<string, FieldValue>>({});
  const [assignees, setAssignees] = useState<Assignees | null>(null);
  const [assignForm, setAssignForm] = useState({ toUser: "", toPosition: "", toDepartment: "", related: true, reason: "" });
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<IntentResponse | null>(null);
  const [error, setError] = useState("");
  // An intent that came back needs_review for missing fields: ask for just
  // those, then resubmit the same intent with them merged in.
  const [followUp, setFollowUp] = useState<{
    text: string;
    intentType: string;
    entities: Record<string, unknown>;
    values: Record<string, string>;
  } | null>(null);

  function load() {
    recordApprovals(entityType, entityId).then(setApprovals).catch(() => setApprovals([]));
    pageForm(entityType, { recordId: entityId })
      .then((f) => setPageButtons(f.sections.flatMap((s) => s.fields.filter((x) => x.kind === "button"))))
      .catch(() => setPageButtons([]));
    recordLifecycle(entityType, entityId)
      .then((r) => {
        setRecord(r);
        setLoadError("");
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : "Failed to load record"));
  }

  useEffect(() => {
    setRecord(null);
    setMode("view");
    setOutcome(null);
    setFollowUp(null);
    setError("");
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityType, entityId]);

  useIntentRefresh(load);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function run(text: string, intentType: string, entities: Record<string, unknown>) {
    setBusy(true);
    setError("");
    setOutcome(null);
    setFollowUp(null);
    try {
      const result = await submitStructuredIntent(text, intentType, entities);
      setOutcome(result);
      // missing_fields also carries context notes ("user 'x' (not found)") -
      // only plain field names can be asked for.
      const askable = result.missing_fields.filter((f) => /^[a-z][a-z0-9_]*$/.test(f));
      if (result.status === "needs_review" && askable.length > 0) {
        setFollowUp({ text, intentType, entities, values: Object.fromEntries(askable.map((f) => [f, ""])) });
      }
      if (result.status === "executed") {
        setMode("view");
        notifyIntentExecuted();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  function startEdit() {
    if (!record) return;
    const values: Record<string, FieldValue> = {};
    for (const key of record.updatable_core_fields) values[key] = record.core_fields[key] ?? "";
    for (const f of record.custom_fields) values[f.key] = f.value ?? (f.data_type === "boolean" ? false : "");
    setDraft(values);
    setOutcome(null);
    setMode("edit");
  }

  function saveEdit() {
    if (!record) return;
    const original: Record<string, FieldValue> = { ...record.core_fields };
    for (const f of record.custom_fields) original[f.key] = f.value;
    const fields: Record<string, FieldValue> = {};
    for (const [key, value] of Object.entries(draft)) {
      if ((value ?? "") !== (original[key] ?? "")) fields[key] = value;
    }
    if (Object.keys(fields).length === 0) {
      setError("Nothing changed.");
      return;
    }
    run(`Update ${record.entity_type} ${record.entity_id}: ${Object.keys(fields).join(", ")}`, "INT-29", {
      entity_type: record.entity_type,
      entity_id: record.entity_id,
      fields,
    });
  }

  function startAssign() {
    setAssignForm({ toUser: "", toPosition: "", toDepartment: "", related: true, reason: "" });
    setOutcome(null);
    setMode("assign");
    if (!assignees) listAssignees().then(setAssignees);
  }

  function saveAssign() {
    if (!record) return;
    if (!assignForm.toUser && !assignForm.toPosition && !assignForm.toDepartment) {
      setError("Pick a user and/or a position - or a department.");
      return;
    }
    const target = assignForm.toDepartment ? `the ${assignForm.toDepartment} department` : [assignForm.toUser, assignForm.toPosition].filter(Boolean).join(" / ");
    run(`Assign ${record.entity_type} ${record.entity_id} to ${target}`, "INT-32", {
      entity_type: record.entity_type,
      entity_id: record.entity_id,
      ...(assignForm.toUser ? { to_user: assignForm.toUser } : {}),
      ...(assignForm.toPosition ? { to_position: assignForm.toPosition } : {}),
      ...(assignForm.toDepartment ? { to_department: assignForm.toDepartment } : {}),
      include_related: assignForm.related,
      ...(assignForm.reason ? { reason: assignForm.reason } : {}),
    });
  }

  function runAction(a: RecordAction) {
    if (!record) return;
    const text = `${a.action} ${record.entity_type} ${record.entity_id} → ${a.to_label}`;
    if (a.intent_type) {
      // Transition owned by a business intent (approve, quote, cancel…) -
      // open that intent's guided form, prefilled with this record and the
      // decision the button names, so its own gates and side effects apply.
      runIntent(a.intent_type, {
        [`${record.entity_type}_id`]: record.entity_id,
        ...(["approve", "reject"].includes(a.action) ? { decision: a.action } : {}),
        ...(["suspend", "cancel", "terminate"].includes(a.action) ? { action: a.action } : {}),
      });
    } else {
      run(text, "INT-31", { entity_type: record.entity_type, entity_id: record.entity_id, action: a.action });
    }
  }

  function fieldInput(key: string, dataType: string, allowed: string[] = []) {
    const value = draft[key];
    const set = (v: FieldValue) => setDraft((d) => ({ ...d, [key]: v }));
    const cls = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200";
    if (dataType === "boolean") {
      return <input type="checkbox" checked={value === true} onChange={(e) => set(e.target.checked)} className="mt-1 block" />;
    }
    if (dataType === "picklist") {
      return (
        <select value={String(value ?? "")} onChange={(e) => set(e.target.value)} className={cls}>
          <option value="">—</option>
          {allowed.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      );
    }
    return (
      <input
        type={dataType === "number" ? "number" : dataType === "date" ? "date" : "text"}
        value={String(value ?? "")}
        onChange={(e) => set(dataType === "number" && e.target.value !== "" ? Number(e.target.value) : e.target.value)}
        className={cls}
      />
    );
  }

  const coreLabel = (key: string) => record?.core_field_labels?.[key] ?? key.replace(/_/g, " ");

  const owner = record
    ? [record.owner_user?.name, record.owner_position && `[${record.owner_position.name}]`,
       record.owner_department && `${record.owner_department.name} department`].filter(Boolean).join(" ") || "unassigned"
    : "";

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-950/60" onClick={onClose}>
      <div
        ref={panel}
        style={width ? { width } : undefined}
        className={`relative flex h-full max-w-[calc(100vw-40px)] flex-col border-l border-slate-700 bg-slate-900 shadow-2xl ${width ? "" : "w-full sm:w-[512px]"}`}
        onClick={(e) => e.stopPropagation()}
      >
        <EdgeHandle panel={panel} width={width} onWidth={setWidth} />
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-bold uppercase tracking-wide text-slate-500">{ENTITY_LABELS[entityType]}</p>
            <h2 className="text-lg font-bold text-slate-100">{record?.title ?? `#${entityId}`}</h2>
          </div>
          <div className="flex items-center gap-1">
            {openAbout && (
              <button
                onClick={() => openAbout(entityType, entityId)}
                title="Everything about this record - fields, related records, lines, payments, history"
                className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-300 hover:bg-sky-500/20"
              >
                ⓘ About
              </button>
            )}
            <button onClick={onClose} className="rounded-md px-2 py-1 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200">
              ✕
            </button>
          </div>
        </div>

        {loadError && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">{loadError}</p>}
        {!record && !loadError && <p className="text-sm text-slate-500">Loading…</p>}

        {record && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span
                className={`rounded-md border px-2 py-0.5 font-bold ${
                  record.is_terminal
                    ? "border-slate-600 text-slate-400"
                    : record.editable
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                      : "border-amber-500/30 bg-amber-500/10 text-amber-400"
                }`}
              >
                {record.state_label}
              </span>
              <span className="text-slate-500">{record.is_terminal ? "closed" : record.editable ? "editable" : "🔒 locked"}</span>
              {record.built_in_status !== record.state && <span className="text-slate-500">status: {record.built_in_status}</span>}
              <span className="ml-auto text-slate-500">{record.state_model_name}</span>
            </div>
            {record.brt_id && (
              <p className="text-[11px] text-slate-500">
                BRT_ID <span className="font-mono text-slate-300">{record.brt_id}</span>
                {record.sys_created_at && <> · created {new Date(record.sys_created_at).toLocaleString()}</>}
                {record.sys_created_by && <> by {record.sys_created_by.name || record.sys_created_by.email}</>}
              </p>
            )}

            <div className="flex items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm">
              <span className="text-xs text-slate-500">Owner</span>
              <span className="text-slate-200">{owner}</span>
              {!record.is_terminal && (
                <button
                  onClick={startAssign}
                  className="ml-auto rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400"
                >
                  Assign / Transfer
                </button>
              )}
            </div>

            {mode === "assign" && (
              <div className="space-y-2 rounded-lg border border-sky-500/30 bg-slate-950 p-3">
                <div className="grid gap-2 md:grid-cols-2">
                  <label className="text-xs text-slate-400">
                    User
                    <select
                      value={assignForm.toUser}
                      onChange={(e) => setAssignForm((f) => ({ ...f, toUser: e.target.value }))}
                      className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200"
                    >
                      <option value="">—</option>
                      {assignees?.users.map((u) => (
                        <option key={u.id} value={u.email}>
                          {u.name} ({u.role})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-slate-400">
                    Position
                    <select
                      value={assignForm.toPosition}
                      onChange={(e) => setAssignForm((f) => ({ ...f, toPosition: e.target.value }))}
                      className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200"
                    >
                      <option value="">—</option>
                      {assignees?.positions.map((p) => (
                        <option key={p.id} value={p.name}>
                          {p.name} ({p.member_count})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-slate-400 md:col-span-2">
                    Or a whole department
                    <select
                      value={assignForm.toDepartment}
                      onChange={(e) => setAssignForm((f) => ({ ...f, toDepartment: e.target.value, ...(e.target.value ? { toUser: "", toPosition: "" } : {}) }))}
                      className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200"
                    >
                      <option value="">—</option>
                      {(assignees?.departments ?? []).map((d) => (
                        <option key={d.id} value={d.name}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {(record.entity_type === "opportunity" || record.entity_type === "lead") && (
                  <label className="flex items-center gap-2 text-xs text-slate-400">
                    <input type="checkbox" checked={assignForm.related} onChange={(e) => setAssignForm((f) => ({ ...f, related: e.target.checked }))} />
                    {record.entity_type === "lead" ? "Move its opportunity and open quotes too" : "Move its open quotes too"}
                  </label>
                )}
                <input
                  value={assignForm.reason}
                  onChange={(e) => setAssignForm((f) => ({ ...f, reason: e.target.value }))}
                  placeholder="Reason (optional)"
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200"
                />
                <div className="flex gap-2">
                  <button onClick={saveAssign} disabled={busy} className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-50">
                    {busy ? "Assigning…" : "Assign"}
                  </button>
                  <button onClick={() => setMode("view")} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-bold text-slate-300">
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {pageButtons.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {pageButtons.map((b) => <PageButton key={b.key} field={b} record={{ entityType: record.entity_type, id: record.entity_id }} />)}
              </div>
            )}

            {record.approval && (
              <div className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2">
                <p className="text-xs text-amber-200">
                  ⏳ Waiting for <b>{record.approval.waiting_for}</b> approval
                  <span className="text-amber-200/70">
                    {" "}- {record.approval.rule}{record.approval.steps > 1 ? `, step ${record.approval.step} of ${record.approval.steps}` : ""}
                  </span>
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  {(["approve", "reject"] as const).map((decision) => (
                    <button
                      key={decision}
                      onClick={() => runIntent("INT-36", { entity_type: record.entity_type, entity_id: record.entity_id, decision })}
                      disabled={busy || !record.approval?.can_decide}
                      title={record.approval?.can_decide ? `Decide as ${record.approval.waiting_for} - the record then moves on` : record.approval?.why}
                      className={`rounded-md border px-2.5 py-1 text-xs font-bold disabled:cursor-not-allowed disabled:opacity-40 ${
                        decision === "approve"
                          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
                          : "border-red-500/40 bg-red-500/10 text-red-300 hover:bg-red-500/20"}`}
                    >
                      {decision === "approve" ? "✓ Approve" : "✕ Reject"}
                    </button>
                  ))}
                  {!record.approval.can_decide && <span className="text-[11px] text-slate-400">{record.approval.why}</span>}
                </div>
              </div>
            )}

            {/* Presales: the deal's network check (BRT 360 Network Inventory) right where the feasibility decision is made */}
            {entityType === "opportunity" && ["in_feasibility", "feasible", "feasibility_failed"].includes(record.state) && (
              <details open={record.state === "in_feasibility"} className="rounded-lg border border-slate-800 bg-slate-950/40">
                <summary className="cursor-pointer px-3 py-2 text-xs font-bold uppercase tracking-wide text-slate-400">Network feasibility</summary>
                <div className="px-3 pb-3">
                  <PresalesNetwork opportunityId={entityId} decisions={false} />
                </div>
              </details>
            )}

            {record.actions !== undefined ? (
              record.actions.filter((b) => !(record.approval && ["approve", "reject"].includes(String(b.entities.decision ?? "")))).length > 0 && (
                <div>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Actions</h3>
                  <div className="flex flex-wrap gap-2">
                    {record.actions.filter((b) => !(record.approval && ["approve", "reject"].includes(String(b.entities.decision ?? "")))).map((b) => (
                      <button
                        key={`${b.intent}-${b.label}`}
                        onClick={() =>
                          b.intent === "INT-31"
                            ? run(`${b.label} - ${record.entity_type} ${record.entity_id}`, "INT-31", b.entities)
                            : runIntent(b.intent, b.entities)
                        }
                        disabled={busy}
                        title={b.intent === "INT-31" ? "Moves the record to this state" : "Opens the guided form for this step"}
                        className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20 disabled:opacity-50"
                      >
                        {b.label}
                      </button>
                    ))}
                  </div>
                </div>
              )
            ) : record.available_actions.some((a) => canRun(a.intent_type ?? "INT-31")) && (
              <div>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Actions</h3>
                <div className="flex flex-wrap gap-2">
                  {record.available_actions.filter((a) => canRun(a.intent_type ?? "INT-31")).map((a) => (
                    <button
                      key={a.action}
                      onClick={() => runAction(a)}
                      disabled={busy}
                      title={a.intent_type ? "Opens the guided form for this step" : "Moves the record to this state"}
                      className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20 disabled:opacity-50"
                    >
                      {a.action.replace(/_/g, " ")} → {a.to_label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</p>}
            {outcome && <IntentResult response={outcome} compact resizable />}
            {followUp && (
              <IntentFollowUp
                key={followUp.intentType + followUp.text}
                title="This action needs a bit more information:"
                fields={Object.keys(followUp.values)}
                busy={busy}
                submitLabel="Submit"
                onSubmit={(values) => run(followUp.text, followUp.intentType, { ...followUp.entities, ...values })}
                onCancel={() => setFollowUp(null)}
              />
            )}

            {entityType === "quote" && <QuoteDocuments quoteId={entityId} canUpload={canRun("INT-29") || canRun("INT-39")} />}

            <div>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500">Fields</h3>
                {mode !== "edit" && record.editable && !record.is_terminal && (
                  <button onClick={startEdit} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
                    Edit
                  </button>
                )}
              </div>

              {mode === "edit" ? (
                <div className="space-y-2 rounded-lg border border-sky-500/30 bg-slate-950 p-3">
                  {record.updatable_core_fields.map((key) => {
                    const options = record.core_field_options?.[key];
                    const current = record.core_fields[key];
                    return (
                      <label key={key} className="block text-xs text-slate-400">
                        {coreLabel(key)}
                        {options
                          ? fieldInput(key, "picklist", current && !options.includes(String(current)) ? [String(current), ...options] : options)
                          : fieldInput(key, typeof current === "number" ? "number" : "text")}
                      </label>
                    );
                  })}
                  {record.custom_fields.map((f) => (
                    <label key={f.key} className="block text-xs text-violet-300">
                      {f.label}
                      {f.required && " *"}
                      {fieldInput(f.key, f.data_type, f.allowed_values)}
                    </label>
                  ))}
                  <div className="flex gap-2 pt-1">
                    <button onClick={saveEdit} disabled={busy} className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-50">
                      {busy ? "Saving…" : "Save"}
                    </button>
                    <button onClick={() => setMode("view")} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-bold text-slate-300">
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-lg border border-slate-800 p-3 text-sm">
                  {Object.entries(record.core_fields).map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="text-xs text-slate-500">{coreLabel(k)}</dt>
                      <dd className="truncate text-slate-200">
                        {k === "offering_name" && !v && record.editable && !record.is_terminal ? (
                          <button onClick={startEdit} className="text-xs font-semibold text-sky-400 hover:text-sky-300">
                            + Add product / bundle
                          </button>
                        ) : (
                          display(v)
                        )}
                      </dd>
                    </div>
                  ))}
                  {record.custom_fields.map((f) => (
                    <div key={f.key} className="contents">
                      <dt className="text-xs text-violet-400">{f.label}</dt>
                      <dd className="truncate text-slate-200">{display(f.value)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>

            {record.revisions.length > 1 && (
              <div>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Revisions</h3>
                <div className="space-y-1">
                  {record.revisions.map((r) => (
                    <button
                      key={r.id}
                      onClick={() => onOpenRecord("quote", r.id)}
                      disabled={r.id === record.entity_id}
                      className={`flex w-full items-center gap-3 rounded-md px-3 py-1.5 text-left text-xs ${
                        r.id === record.entity_id ? "bg-sky-500/10 text-sky-300" : "bg-slate-950 text-slate-300 hover:bg-slate-800"
                      }`}
                    >
                      <span className="font-mono">rev {r.revision_number}</span>
                      <span>#{r.id}</span>
                      <span>₹{r.total_price.toLocaleString()}</span>
                      <span className="ml-auto text-slate-500">{r.status}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {approvals.length > 0 && (
              <div>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Approvals</h3>
                <div className="space-y-2">
                  {approvals.map((a) => (
                    <div key={a.id ?? a.gate} className="rounded-md bg-slate-950 px-3 py-2 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-slate-200">{a.rule}</span>
                        <span className={a.status === "approved" ? "text-emerald-400" : a.status === "rejected" ? "text-red-400" : "text-amber-400"}>
                          {a.status}{a.waiting_for ? ` - waiting for ${a.waiting_for}` : ""}
                        </span>
                        <span className="ml-auto text-slate-600">
                          {a.requested_by ? `asked by ${a.requested_by} · ` : ""}{new Date(a.requested_at).toLocaleString()}
                        </span>
                      </div>
                      {a.steps.map((s) => (
                        <div key={s.sequence} className="mt-1 flex flex-wrap gap-2 text-slate-400">
                          <span>{s.sequence}. {s.approver_tier}</span>
                          <span>{s.status}</span>
                          {s.decided_by && <span className="text-slate-300">by {s.decided_by}</span>}
                          {s.reasoning && <span className="italic">“{s.reasoning}”</span>}
                          {s.decided_at && <span className="ml-auto text-slate-600">{new Date(s.decided_at).toLocaleString()}</span>}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">History</h3>
              <div className="space-y-1">
                {record.history.map((h, i) => (
                  <div key={i} className="flex items-center gap-2 rounded-md bg-slate-950 px-3 py-1.5 text-xs">
                    <span className={h.kind === "state" ? "text-sky-400" : "text-violet-400"}>{h.kind === "state" ? "◆" : "👤"}</span>
                    <span className="text-slate-300">{h.summary}</span>
                    <span className="ml-auto text-slate-600">
                      {h.source} · {new Date(h.created_at).toLocaleString()}
                    </span>
                  </div>
                ))}
                {record.history.length === 0 && <p className="text-xs text-slate-500">No lifecycle history yet.</p>}
              </div>
            </div>
          </>
        )}
        </div>
      </div>
    </div>
  );
}
