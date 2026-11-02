import { useCallback, useEffect, useState } from "react";
import { bulkApi, bulkResultsCsv } from "../api/client";
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
        <button type="button" className={BTN_PRIMARY} onClick={() => { setCreating(true); setOpen(null); }}>📚 New bulk order</button>
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
                {open.status === "running" && <button type="button" className={BTN_PRIMARY} disabled={busy}
                  onClick={() => act("/process", (r) => `Placed ${r.placed_now} row(s), followed ${r.followed_now}`)}>Process now</button>}
                {open.failed > 0 && <button type="button" className={BTN} disabled={busy} onClick={() => act("/retry", (r) => `${r.requeued} row(s) queued again`)}>Retry failed</button>}
                {open.status === "running" && <button type="button" className={BTN_DANGER} disabled={busy}
                  onClick={() => act("/cancel", (r) => `${r.cancelled} queued row(s) cancelled`)}>Cancel queued</button>}
                <button type="button" className={BTN} onClick={() => bulkResultsCsv(open.id).then((t) => download(`${open.number}-results.csv`, t))
                  .catch((e) => setError(errorText(e, "Could not download")))}>Results CSV</button>
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

/* ---------------------------------------------------------------- new request */

function NewBulk({ processes, onCancel, onCreated, onError }: { processes: ProcessInfo[]; onCancel: () => void;
  onCreated: (d: Detail) => void; onError: (s: string | null) => void }) {
  const [process, setProcess] = useState("b2c_new_connect");
  const [name, setName] = useState("");
  const [csv, setCsv] = useState("");
  const [defaults, setDefaults] = useState("");
  const [autoApprove, setAutoApprove] = useState(true);
  const [sendToFulfillment, setSendToFulfillment] = useState(true);
  const [busy, setBusy] = useState(false);
  const info = processes.find((p) => p.process === process);
  const submit = () => {
    const pairs: Record<string, string> = {};
    for (const part of defaults.split(/[;\n]/)) {
      const [k, ...v] = part.split("=");
      if (k.trim() && v.length) pairs[k.trim()] = v.join("=").trim();
    }
    setBusy(true);
    bulkApi<Detail>("", "POST", { process, name, csv, defaults: pairs, auto_approve: autoApprove, send_to_fulfillment: sendToFulfillment, start: true })
      .then(onCreated)
      .catch((e) => onError(errorText(e, "Could not create the bulk order")))
      .finally(() => setBusy(false));
  };
  return (
    <Panel title="New bulk order" action={<button type="button" className={BTN} onClick={onCancel}>Close</button>}>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Process">
          <select className={INPUT} value={process} onChange={(e) => setProcess(e.target.value)}>
            {processes.map((p) => <option key={p.process} value={p.process}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="Name"><input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. October campus connects" /></Field>
      </div>
      {info && (
        <div className="mt-3 rounded-lg border border-slate-800 p-3 text-xs text-slate-400">
          <div className="mb-1 text-slate-300">Each row runs: {info.intents.join(" → ")} → fulfilment → assets and billing.</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {info.columns.map((c) => <span key={c.name} title={c.help}><b className={c.required ? "text-slate-100" : ""}>{c.name}</b>{c.required ? "*" : ""}</span>)}
          </div>
          <button type="button" className={`${BTN} mt-2`} onClick={() => download(`${info.process}-template.csv`, `${info.csv_header}\n`)}>Download template</button>
        </div>
      )}
      <Field label="Rows (CSV with a header line)" className="mt-3">
        <textarea className={`${INPUT} h-48 font-mono text-xs`} value={csv} onChange={(e) => setCsv(e.target.value)} spellCheck={false}
          placeholder={info ? `${info.csv_header}\n...` : ""} />
      </Field>
      <input type="file" accept=".csv,text/csv" className="mt-2 text-xs text-slate-400" aria-label="Upload a CSV"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) f.text().then(setCsv); }} />
      <Field label="Every row gets (name=value; ...)" className="mt-3">
        <input className={INPUT} value={defaults} onChange={(e) => setDefaults(e.target.value)} placeholder="order_channel=store; products=Home Fiber 100" />
      </Field>
      <div className="mt-3 flex flex-wrap gap-4 text-sm text-slate-300">
        <label className="flex items-center gap-2"><input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} /> Approve each order along the way</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={sendToFulfillment} onChange={(e) => setSendToFulfillment(e.target.checked)} /> Send orders to Fulfillment</label>
      </div>
      <div className="mt-4 flex gap-2">
        <button type="button" className={BTN_PRIMARY} disabled={busy || !csv.trim()} onClick={submit}>{busy ? "Creating…" : "Create and start"}</button>
      </div>
    </Panel>
  );
}
