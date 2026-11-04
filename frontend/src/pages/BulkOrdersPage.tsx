import { useCallback, useEffect, useState } from "react";
import { bulkApi, bulkResultsCsv, bulkSendFile, downloadFile } from "../api/client";
import { useIntentRefresh } from "../workspace";
import { BTN, BTN_DANGER, BTN_PRIMARY, CARD, Chip, Empty, Field, INPUT, Notice, Panel, Section, Stat } from "./fieldservice/ui";

/* ---------------------------------------------------------------- shapes (backend: orchestration/bulk_orders.py) */

type Column = { name: string; required: boolean; help: string };
type ProcessInfo = { process: string; label: string; intents: string[]; columns: Column[]; csv_header: string };
type Summary = {
  id: number; number: string; name: string; process: string; process_label: string; status: string; auto_approve: boolean;
  send_to_fulfillment: boolean; counts: Record<string, number>; rows: number; completed: number; failed: number; in_progress: number;
  created_at: string; started_at: string | null; finished_at: string | null;
};
type Run = { intent: string; execution_id: number; status: string; at: string; note: string; order_id?: number };
type Row = {
  id: number; row_no: number; data: Record<string, unknown>; stage: string; party_id: number | null; order_ids: number[];
  asset_ids: number[]; billing: Record<string, Record<string, unknown>>; runs: Run[]; message: string;
};
type Detail = Summary & { row_items: Row[] };

const STAGE: Record<string, { label: string; tone: "slate" | "sky" | "emerald" | "amber" | "red" | "violet" }> = {
  invalid: { label: "invalid", tone: "red" }, queued: { label: "queued", tone: "slate" }, ordering: { label: "ordering", tone: "sky" },
  awaiting_discount_approval: { label: "discount approval", tone: "amber" }, awaiting_order_approval: { label: "order approval", tone: "amber" },
  ready_for_approval: { label: "ready for approval", tone: "amber" }, ready_for_fulfillment: { label: "ready for fulfilment", tone: "sky" },
  in_fulfillment: { label: "in fulfilment", tone: "sky" }, fallout: { label: "fallout", tone: "red" }, completed: { label: "completed", tone: "emerald" },
  failed: { label: "failed", tone: "red" }, cancelled: { label: "cancelled", tone: "slate" },
};

function errorText(e: unknown, fallback: string) {
  return e instanceof Error && e.message ? e.message : fallback;
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Bulk Orders - many orders in one request. Each row runs the same process as a single order (customer,
 * order, approval, fulfilment through COM / SOM / TOM) intent by intent, as you, and is followed until its
 * services are installed and billing has started. Rows waiting for an engineer or an approver just wait.
 */
export default function BulkOrdersPage() {
  const [list, setList] = useState<Summary[]>([]);
  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [open, setOpen] = useState<Detail | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    Promise.all([bulkApi<Summary[]>(""), bulkApi<ProcessInfo[]>("/processes")])
      .then(([l, p]) => {
        setList(l);
        setProcesses(p);
      })
      .catch((e) => setError(errorText(e, "Could not load the bulk orders")));
  }, []);
  useEffect(load, [load]);
  const openOne = useCallback((id: number) => {
    bulkApi<Detail>(`/${id}`).then(setOpen).catch((e) => setError(errorText(e, "Could not open the bulk order")));
  }, []);
  useIntentRefresh(() => {
    load();
    if (open) openOne(open.id);
  });
  // a running request moves on in the background - keep it fresh while it is open
  useEffect(() => {
    if (!open || open.status !== "running") return;
    const t = window.setInterval(() => openOne(open.id), 15000);
    return () => window.clearInterval(t);
  }, [open, openOne]);

  const act = (path: string, done: (r: Record<string, unknown>) => string) => {
    if (!open) return;
    setBusy(true);
    bulkApi<Record<string, unknown>>(`/${open.id}${path}`, "POST")
      .then((r) => {
        setNote(done(r));
        openOne(open.id);
        load();
      })
      .catch((e) => setError(errorText(e, "That didn't work")))
      .finally(() => setBusy(false));
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Bulk Orders</h1>
          <p className="text-sm text-slate-400">Many orders at once - each row runs the full single-order process to fulfilment, installed services and billing.</p>
        </div>
        <button type="button" className={BTN_PRIMARY} onClick={() => { setCreating(true); setOpen(null); }}>📤 Upload a bulk order (CSV)</button>
      </div>
      <Notice text={error} onClose={() => setError(null)} />
      <Notice text={note} tone="emerald" onClose={() => setNote(null)} />

      {creating && (
        <NewBulk processes={processes} onCancel={() => setCreating(false)} onError={setError}
          onCreated={(d) => { setCreating(false); setOpen(d); load(); setNote(`${d.number} created - ${d.rows} row(s)`); }} />
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Section title="Requests" count={list.length}>
          {list.length === 0 ? <Empty>No bulk orders yet.</Empty> : (
            <ul className="space-y-2">
              {list.map((b) => (
                <li key={b.id}>
                  <button type="button" onClick={() => { openOne(b.id); setCreating(false); }}
                    className={`w-full rounded-lg border p-3 text-left text-sm transition ${open?.id === b.id ? "border-sky-500 bg-sky-500/5" : "border-slate-800 hover:border-slate-600"}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-100">{b.number}</span>
                      <Chip tone={b.status === "completed" ? "emerald" : b.status === "running" ? "sky" : b.status === "completed_with_errors" ? "amber" : "slate"}>
                        {b.status.replace(/_/g, " ")}
                      </Chip>
                    </div>
                    <div className="truncate text-xs text-slate-400">{b.name}</div>
                    <div className="text-xs text-slate-500">{b.completed}/{b.rows} completed · {b.in_progress} in progress · {b.failed} failed</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>

        {open ? (
          <div className="space-y-4">
            <Panel title={`${open.number} · ${open.process_label}`} action={
              <div className="flex flex-wrap gap-2">
                {open.status === "draft" && <button type="button" className={BTN_PRIMARY} disabled={busy}
                  onClick={() => act("/start", () => `${open.number} started`)}>Start</button>}
                {open.status === "running" && <button type="button" className={BTN_PRIMARY} disabled={busy}
                  onClick={() => act("/process", (r) => `Placed ${r.placed_now} row(s), followed ${r.followed_now}`)}>Process now</button>}
                {open.failed > 0 && <button type="button" className={BTN} disabled={busy} onClick={() => act("/retry", (r) => `${r.requeued} row(s) queued again`)}>Retry failed</button>}
                {open.status === "running" && <button type="button" className={BTN_DANGER} disabled={busy}
                  onClick={() => act("/cancel", (r) => `${r.cancelled} queued row(s) cancelled`)}>Cancel queued</button>}
                <button type="button" className={BTN} onClick={() => bulkResultsCsv(open.id).then((t) => download(`${open.number}-results.csv`, t))
                  .catch((e) => setError(errorText(e, "Could not download")))}>CSV</button>
                <button type="button" className={BTN} onClick={() => downloadFile(`/api/bulk-orders/${open.id}/results.xlsx`, `${open.number}-results.xlsx`)
                  .catch((e) => setError(errorText(e, "Could not download")))}>Excel</button>
                <button type="button" className={BTN_PRIMARY} onClick={() => downloadFile(`/api/bulk-orders/${open.id}/results.pdf`, `${open.number}-results.pdf`)
                  .catch((e) => setError(errorText(e, "Could not download")))}>📄 PDF report</button>
              </div>
            }>
              <p className="text-sm text-slate-300">{open.name}</p>
              <p className="mt-1 text-xs text-slate-500">
                {open.auto_approve ? "Orders are approved along the way" : "Each order waits for its approver"} ·{" "}
                {open.send_to_fulfillment ? "orders go to Fulfillment" : "orders wait to be sent to Fulfillment"} · rows run as the person who submitted it
                {open.status === "running" ? " · moves on every minute" : ""}
              </p>
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Rows" value={open.rows} />
                <Stat label="Completed" value={open.completed} tone="emerald" />
                <Stat label="In progress" value={open.in_progress} />
                <Stat label="Failed / invalid" value={open.failed} tone={open.failed ? "red" : undefined} />
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(open.counts).map(([k, v]) => <Chip key={k} tone={STAGE[k]?.tone ?? "slate"}>{v} {STAGE[k]?.label ?? k}</Chip>)}
              </div>
            </Panel>
            <Section title="Rows" count={open.row_items.length}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="text-left text-[11px] uppercase text-slate-500">
                    <tr><th className="py-1">#</th><th>Row</th><th>Stage</th><th>Orders</th><th>Assets</th><th>Steps</th><th>Where / why</th></tr>
                  </thead>
                  <tbody>
                    {open.row_items.map((r) => (
                      <tr key={r.id} className="border-t border-slate-800 align-top">
                        <td className="py-1.5 text-slate-500">{r.row_no}</td>
                        <td className="max-w-[220px] truncate text-xs text-slate-300" title={JSON.stringify(r.data)}>
                          {Object.entries(r.data).slice(0, 3).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}
                        </td>
                        <td><Chip tone={STAGE[r.stage]?.tone ?? "slate"}>{STAGE[r.stage]?.label ?? r.stage}</Chip></td>
                        <td className="text-xs text-slate-300">{r.order_ids.map((o) => `#${o}`).join(" ")}</td>
                        <td className="text-xs text-slate-300">{r.asset_ids.map((a) => `#${a}`).join(" ")}</td>
                        <td className="text-xs text-slate-400">{r.runs.map((x) => `${x.intent}${x.status === "executed" ? "" : ` (${x.status})`}`).join(" → ")}</td>
                        <td className="max-w-[320px] text-xs text-slate-400">{r.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          </div>
        ) : !creating ? (
          <div className={`${CARD} p-6`}><Empty>Open a request, or start a new bulk order.</Empty></div>
        ) : null}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- new request: upload a CSV file */

type Preview = {
  process_label: string; rows: number; valid: number; invalid: number; columns: string[]; unknown_columns: string[];
  renamed: Record<string, string>; missing_required_columns: string[];
  shown: { row_no: number; data: Record<string, unknown>; valid: boolean; message: string }[];
};

function NewBulk({ processes, onCancel, onCreated, onError }: { processes: ProcessInfo[]; onCancel: () => void;
  onCreated: (d: Detail) => void; onError: (s: string | null) => void }) {
  const [process, setProcess] = useState("b2c_new_connect");
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [showPaste, setShowPaste] = useState(false);
  const [defaults, setDefaults] = useState("");
  const [autoApprove, setAutoApprove] = useState(true);
  const [sendToFulfillment, setSendToFulfillment] = useState(true);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const info = processes.find((p) => p.process === process);
  const source = (): Blob | null => file ?? (pasted.trim() ? new Blob([pasted], { type: "text/csv" }) : null);

  // every change to the file, the process or the shared values re-checks the rows
  useEffect(() => {
    const body = file ?? (pasted.trim() ? new Blob([pasted], { type: "text/csv" }) : null);
    if (!body) {
      setPreview(null);
      return;
    }
    let live = true;
    const t = window.setTimeout(() => {
      bulkSendFile<Preview>("/preview", body, { process, defaults })
        .then((p) => { if (live) setPreview(p); })
        .catch((e) => { if (live) { setPreview(null); onError(errorText(e, "Could not read the file")); } });
    }, file ? 0 : 500);
    return () => { live = false; window.clearTimeout(t); };
  }, [file, pasted, process, defaults, onError]);

  const pick = (f: File | undefined) => {
    if (!f) return;
    if (!/\.(csv|txt|xlsx)$/i.test(f.name) && f.type && !/csv|text\/plain|excel|spreadsheetml/.test(f.type)) {
      onError(`${f.name} isn't a CSV or Excel (.xlsx) file`);
      return;
    }
    onError(null);
    setFile(f);
    setPasted("");
    if (!name) setName(f.name.replace(/\.[^.]+$/, ""));
  };
  const submit = (start: boolean) => {
    const body = source();
    if (!body) return;
    setBusy(true);
    bulkSendFile<Detail>("/upload", body, { process, name, file_name: file?.name ?? "", defaults, auto_approve: autoApprove,
      send_to_fulfillment: sendToFulfillment, start })
      .then(onCreated)
      .catch((e) => onError(errorText(e, "Could not create the bulk order")))
      .finally(() => setBusy(false));
  };
  const blocked = !preview || preview.valid === 0 || preview.missing_required_columns.length > 0;
  return (
    <Panel title="New bulk order - upload a CSV file" action={<button type="button" className={BTN} onClick={onCancel}>Close</button>}>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Process">
          <select className={INPUT} value={process} onChange={(e) => setProcess(e.target.value)}>
            {processes.map((p) => <option key={p.process} value={p.process}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="Name"><input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} placeholder="Defaults to the file's name" /></Field>
      </div>
      {info && (
        <div className="mt-3 rounded-lg border border-slate-800 p-3 text-xs text-slate-400">
          <div className="mb-1 text-slate-300">Each row runs: {info.intents.join(" → ")} → fulfilment → assets and billing.</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {info.columns.map((c) => <span key={c.name} title={c.help}><b className={c.required ? "text-slate-100" : ""}>{c.name}</b>{c.required ? "*" : ""}</span>)}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" className={BTN_PRIMARY} onClick={() => downloadFile(`/api/bulk-orders/processes/${info.process}/template.xlsx`, `bulk-${info.process}-template.xlsx`)
              .catch((e) => onError(errorText(e, "Could not download the template")))}>⬇ Excel template</button>
            <button type="button" className={BTN} onClick={() => download(`${info.process}-template.csv`, `${info.csv_header}\n`)}>CSV template</button>
          </div>
        </div>
      )}

      <label
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0]); }}
        className={`mt-3 flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed p-6 text-center transition ${
          dragging ? "border-sky-400 bg-sky-500/10" : "border-slate-700 hover:border-slate-500"}`}>
        <span className="text-2xl" aria-hidden="true">📄</span>
        {file ? (
          <span className="text-sm text-slate-200">{file.name} <span className="text-slate-500">({(file.size / 1024).toFixed(1)} KB)</span></span>
        ) : (
          <span className="text-sm text-slate-300">Drop the filled-in Excel template or a CSV file here, or <span className="text-sky-300 underline">choose a file</span></span>
        )}
        <span className="text-xs text-slate-500">Excel (.xlsx - its first sheet) or CSV with a header row · up to 5 MB / 5,000 rows</span>
        <input type="file" accept=".csv,text/csv,.txt,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="hidden" onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
      </label>
      <div className="mt-2 flex flex-wrap gap-3 text-xs">
        {file && <button type="button" className="text-slate-400 underline" onClick={() => setFile(null)}>Remove the file</button>}
        <button type="button" className="text-slate-400 underline" onClick={() => setShowPaste((v) => !v)}>{showPaste ? "Hide" : "Or paste the rows instead"}</button>
      </div>
      {showPaste && (
        <textarea className={`${INPUT} mt-2 h-40 font-mono text-xs`} value={pasted} spellCheck={false} disabled={!!file}
          onChange={(e) => setPasted(e.target.value)} placeholder={info ? `${info.csv_header}\n...` : ""} />
      )}

      <Field label="Every row gets (name=value; ...)" className="mt-3">
        <input className={INPUT} value={defaults} onChange={(e) => setDefaults(e.target.value)} placeholder="order_channel=store; products=Home Fiber 100" />
      </Field>
      <div className="mt-3 flex flex-wrap gap-4 text-sm text-slate-300">
        <label className="flex items-center gap-2"><input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} /> Approve each order along the way</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={sendToFulfillment} onChange={(e) => setSendToFulfillment(e.target.checked)} /> Send orders to Fulfillment</label>
      </div>

      {preview && (
        <div className="mt-4 space-y-2">
          <div className="flex flex-wrap gap-2">
            <Chip>{preview.rows} row(s)</Chip>
            <Chip tone="emerald">{preview.valid} ready</Chip>
            {preview.invalid > 0 && <Chip tone="red">{preview.invalid} can't run</Chip>}
          </div>
          {preview.missing_required_columns.length > 0 && (
            <Notice tone="red" text={`The file has no ${preview.missing_required_columns.join(", ")} column - add it, or give it under "Every row gets"`} />
          )}
          {preview.unknown_columns.length > 0 && (
            <Notice tone="amber" text={`Columns not used by ${preview.process_label}: ${preview.unknown_columns.join(", ")}`} />
          )}
          {Object.keys(preview.renamed).length > 0 && (
            <p className="text-xs text-slate-500">Read as: {Object.entries(preview.renamed).map(([from, to]) => `${from} → ${to}`).join(", ")}</p>
          )}
          <div className="max-h-72 overflow-auto rounded-lg border border-slate-800">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-slate-900 text-left text-[11px] uppercase text-slate-500">
                <tr><th className="px-2 py-1">#</th><th>Row</th><th>Check</th></tr>
              </thead>
              <tbody>
                {preview.shown.map((r) => (
                  <tr key={r.row_no} className="border-t border-slate-800 align-top">
                    <td className="px-2 py-1 text-slate-500">{r.row_no}</td>
                    <td className="max-w-[460px] truncate text-slate-300" title={JSON.stringify(r.data)}>
                      {Object.entries(r.data).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}
                    </td>
                    <td className={r.valid ? "text-emerald-300" : "text-red-300"}>{r.valid ? "ready" : r.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview.rows > preview.shown.length && <p className="text-xs text-slate-500">Showing the first {preview.shown.length} rows.</p>}
          {preview.invalid > 0 && preview.valid > 0 && <p className="text-xs text-slate-400">Rows that can't run are kept on the request as invalid - the others go ahead.</p>}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className={BTN_PRIMARY} disabled={busy || blocked} onClick={() => submit(true)}>
          {busy ? "Uploading…" : `Create and start${preview ? ` (${preview.valid} order${preview.valid === 1 ? "" : "s"})` : ""}`}
        </button>
        <button type="button" className={BTN} disabled={busy || blocked} onClick={() => submit(false)}>Save as draft</button>
      </div>
    </Panel>
  );
}