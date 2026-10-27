import { useCallback, useEffect, useState } from "react";
import { commsApi } from "../../api/client";
import { useIntentRefresh, useWorkspace } from "../../workspace";
import { BTN, BTN_PRIMARY, BTN_WARN, Chip, Drawer, Empty, Notice, Panel, Stat, Tabs } from "../fieldservice/ui";
import { fmtDate, fmtWhen, money, useAction } from "../fieldservice/style";
import { InvoiceChip, InvoiceDrawer } from "./BillingPanel";
import type { Adjustment, Arrangement, CommsOverview, CreditAlert, FraudAlert, Invoice } from "./types";

type View = "bills" | "collections" | "fraud" | "agreements" | "providers";

/** Orders & Billing: the bills, collections, fraud, agreements and provider work orders of every customer. */
export default function BillingWorkbench() {
  const [view, setView] = useState<View>("bills");
  const [overview, setOverview] = useState<CommsOverview | null>(null);
  const load = useCallback(() => {
    commsApi<CommsOverview>("/overview").then(setOverview).catch(() => setOverview(null));
  }, []);
  useEffect(load, [load]);
  useIntentRefresh(load);
  return (
    <div className="space-y-4">
      {overview && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Billed this month" value={money(overview.billed_this_month)} sub={`${overview.invoices} bills in all`} />
          <Stat label="Receivables" value={money(overview.open_receivables)} sub={`${money(overview.overdue)} overdue`} tone={overview.overdue > 0 ? "text-amber-300" : undefined} />
          <Stat label="Credit alerts" value={overview.credit_alerts_open} sub={`${overview.adjustments_waiting} adjustments, ${overview.arrangements_waiting} plans waiting`} onClick={() => setView("collections")} />
          <Stat label="Fraud alerts" value={overview.fraud_alerts_open} tone={overview.fraud_alerts_open ? "text-red-400" : undefined} onClick={() => setView("fraud")} />
        </div>
      )}
      <Tabs
        value={view}
        onChange={setView}
        items={[
          { id: "bills", label: "Bills" },
          { id: "collections", label: "Collections", count: overview?.credit_alerts_open },
          { id: "fraud", label: "Fraud", count: overview?.fraud_alerts_open },
          { id: "agreements", label: "Agreements" },
          { id: "providers", label: "Provider work orders" },
        ]}
      />
      {view === "bills" && <Bills />}
      {view === "collections" && <Collections />}
      {view === "fraud" && <Fraud />}
      {view === "agreements" && <Agreements />}
      {view === "providers" && <Providers />}
    </div>
  );
}

function Bills() {
  const { runIntent, canRun } = useWorkspace();
  const [rows, setRows] = useState<Invoice[]>([]);
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => {
    commsApi<Invoice[]>(`/invoices${status ? `?status=${status}` : ""}`).then(setRows).catch(() => setRows([]));
  }, [status]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All bills</option>
          {["open", "partially_paid", "overdue", "paid", "written_off"].map((s) => (
            <option key={s} value={s}>
              {s.replace("_", " ")}
            </option>
          ))}
        </select>
        <span className="flex-1" />
        {canRun("INT-84") ? <button type="button" className={BTN_PRIMARY} onClick={() => runIntent("INT-84")}>
          Run a bill
        </button> : null}
        {canRun("INT-85") ? <button type="button" className={BTN} onClick={() => runIntent("INT-85")}>
          Take a payment
        </button> : null}
      </div>
      {rows.length === 0 ? (
        <Empty>No bills yet - run one for a customer (or load the communications starter setup in Admin).</Empty>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-slate-800">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-slate-900/80 text-left text-[11px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-3 py-2">Bill</th>
                <th className="px-3 py-2">Customer</th>
                <th className="px-3 py-2">Period</th>
                <th className="px-3 py-2">Due</th>
                <th className="px-3 py-2 text-right">Total</th>
                <th className="px-3 py-2 text-right">Balance</th>
                <th className="px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {rows.map((i) => (
                <tr key={i.id} className="cursor-pointer hover:bg-slate-800/30" onClick={() => setOpen(i.invoice_number)}>
                  <td className="px-3 py-2 font-mono text-xs text-sky-400">{i.invoice_number}</td>
                  <td className="px-3 py-2 text-slate-200">{i.customer}</td>
                  <td className="px-3 py-2 text-xs text-slate-400">
                    {fmtDate(i.period_start)} – {fmtDate(i.period_end)}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-400">{fmtDate(i.due_date)}</td>
                  <td className="px-3 py-2 text-right text-slate-200">{money(i.total)}</td>
                  <td className="px-3 py-2 text-right text-slate-200">{money(i.balance_due)}</td>
                  <td className="px-3 py-2">
                    <InvoiceChip status={i.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <InvoiceDrawer invoice={open} onClose={() => setOpen(null)} />
    </div>
  );
}

const ACTION_TONE = (a: string) => (/suspend|terminate|agency|harsh|demanding/.test(a) ? "red" : /arrangement|received/.test(a) ? "emerald" : "amber");

function Collections() {
  const { runIntent, canRun } = useWorkspace();
  const [alerts, setAlerts] = useState<CreditAlert[]>([]);
  const [adjustments, setAdjustments] = useState<Adjustment[]>([]);
  const [plans, setPlans] = useState<Arrangement[]>([]);
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState<CreditAlert | null>(null);
  const load = useCallback(() => {
    commsApi<CreditAlert[]>(`/credit-alerts${all ? "" : "?status=active"}`).then(setAlerts).catch(() => setAlerts([]));
    commsApi<Adjustment[]>("/adjustments?status=pending_approval").then(setAdjustments).catch(() => setAdjustments([]));
    commsApi<Arrangement[]>("/arrangements?status=pending").then(setPlans).catch(() => setPlans([]));
  }, [all]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Show closed alerts too
        </label>
        <span className="flex-1" />
        {canRun("INT-91") ? <button type="button" className={BTN_PRIMARY} onClick={() => runIntent("INT-91")}>
          Run collections
        </button> : null}
        {canRun("INT-92") ? <button type="button" className={BTN} onClick={() => runIntent("INT-92")}>
          Raise a credit alert
        </button> : null}
      </div>
      {(adjustments.length > 0 || plans.length > 0) && (
        <Panel title="Waiting for a supervisor">
          <ul className="divide-y divide-slate-800 text-sm">
            {adjustments.map((a) => (
              <li key={`a${a.id}`} className="flex flex-wrap items-center gap-2 py-2">
                <span className="font-mono text-xs text-slate-500">{a.request_number}</span>
                <span className="text-slate-200">{a.customer}</span>
                <span className="text-slate-400">
                  {a.type} {money(a.requested_amount)} on {a.invoice_number ?? a.level} · {a.reason.replace("_", " ")}
                </span>
                <span className="ml-auto flex gap-2">
                  {canRun("INT-86") ? <button type="button" className={BTN} onClick={() => runIntent("INT-86", { adjustment: a.request_number, decision: "approve" })}>
                    Approve
                  </button> : null}
                  {canRun("INT-86") ? <button type="button" className={BTN} onClick={() => runIntent("INT-86", { adjustment: a.request_number, decision: "reject" })}>
                    Reject
                  </button> : null}
                </span>
              </li>
            ))}
            {plans.map((p) => (
              <li key={`p${p.id}`} className="flex flex-wrap items-center gap-2 py-2">
                <span className="font-mono text-xs text-slate-500">{p.number}</span>
                <span className="text-slate-200">{p.customer}</span>
                <span className="text-slate-400">
                  {p.installments} × {money(p.installment_amount)} · {p.outcome}
                </span>
                <span className="ml-auto flex gap-2">
                  {canRun("INT-87") ? <button type="button" className={BTN} onClick={() => runIntent("INT-87", { arrangement: p.number, decision: "approve" })}>
                    Approve
                  </button> : null}
                  {canRun("INT-87") ? <button type="button" className={BTN} onClick={() => runIntent("INT-87", { arrangement: p.number, decision: "reject" })}>
                    Reject
                  </button> : null}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      {alerts.length === 0 ? (
        <Empty>No credit alerts - run collections to find overdue accounts.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {alerts.map((a) => (
            <button key={a.id} type="button" onClick={() => setOpen(a)} className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4 text-left transition hover:border-slate-600">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-500">{a.alert_number}</span>
                <span className="font-semibold text-slate-100">{a.customer}</span>
                <Chip tone={a.priority === "high" ? "red" : "slate"}>{a.priority}</Chip>
                <span className="ml-auto">
                  <Chip tone={a.status === "closed" ? "emerald" : "amber"}>{a.status}</Chip>
                </span>
              </div>
              <p className="mt-2 text-sm text-slate-300">
                {money(a.amount_owed)} · {a.days_overdue} days overdue · {a.source}
              </p>
              <p className="mt-1">
                <Chip tone={ACTION_TONE(a.action_type)}>{a.action_type.replace(/_/g, " ")}</Chip>
                {a.due_date && <span className="ml-2 text-[11px] text-slate-500">next step by {fmtDate(a.due_date)}</span>}
              </p>
            </button>
          ))}
        </div>
      )}
      <Drawer open={!!open} onClose={() => setOpen(null)} title={open ? `${open.alert_number} · ${open.customer}` : ""} subtitle={open ? `${money(open.amount_owed)} owed · ${open.sub_status.replace("_", " ") || open.source}` : undefined}>
        {open && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {canRun("INT-92") ? <button type="button" className={BTN_PRIMARY} onClick={() => runIntent("INT-92", { credit_alert: open.alert_number })}>
                Next action / note
              </button> : null}
              {canRun("INT-85") ? <button type="button" className={BTN} onClick={() => runIntent("INT-85", { credit_alert: open.alert_number, party_id: open.party_id })}>
                Take payment
              </button> : null}
              {canRun("INT-87") ? <button type="button" className={BTN} onClick={() => runIntent("INT-87", { credit_alert: open.alert_number, party_id: open.party_id })}>
                Payment arrangement
              </button> : null}
              {canRun("INT-86") ? <button type="button" className={BTN} onClick={() => runIntent("INT-86", { party_id: open.party_id })}>
                Adjust the account
              </button> : null}
              {/suspend|terminate/.test(open.action_type) && (
                (canRun("INT-09") ? <button type="button" className={BTN_WARN} onClick={() => runIntent("INT-09", { action: "suspend" })}>
                  Suspend the service
                </button> : null)
              )}
            </div>
            <Panel title="History">
              <ol className="space-y-1.5 text-xs">
                {[...open.history].reverse().map((h, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="w-32 shrink-0 text-slate-500">{fmtWhen(h.at)}</span>
                    <span className="text-slate-300">
                      {h.action.replace(/_/g, " ")}
                      {h.note ? ` - ${h.note}` : ""}
                    </span>
                  </li>
                ))}
              </ol>
            </Panel>
          </div>
        )}
      </Drawer>
    </div>
  );
}

function Fraud() {
  const { runIntent, canRun } = useWorkspace();
  const [rows, setRows] = useState<FraudAlert[]>([]);
  const [open, setOpen] = useState<FraudAlert | null>(null);
  const load = useCallback(() => {
    commsApi<FraudAlert[]>("/fraud-alerts").then(setRows).catch(() => setRows([]));
  }, []);
  useEffect(load, [load]);
  useIntentRefresh(load);
  const show = async (a: FraudAlert) => setOpen(await commsApi<FraudAlert>(`/fraud-alerts/${a.alert_number}`).catch(() => a));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <span className="flex-1 text-sm text-slate-400">Usage is checked against each account's thresholds (Customer 360 &gt; Account &gt; Fraud thresholds) and for one number calling from two places at once.</span>
        {canRun("INT-93") ? <button type="button" className={BTN_PRIMARY} onClick={() => runIntent("INT-93", { scan: true })}>
          Run a fraud check
        </button> : null}
        {canRun("INT-93") ? <button type="button" className={BTN} onClick={() => runIntent("INT-93", { fraud_type: "stolen_handset" })}>
          Report a stolen phone
        </button> : null}
      </div>
      {rows.length === 0 ? (
        <Empty>No fraud alerts.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {rows.map((a) => (
            <button key={a.id} type="button" onClick={() => show(a)} className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4 text-left transition hover:border-slate-600">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-500">{a.alert_number}</span>
                <span className="font-semibold text-slate-100">{a.customer}</span>
                <Chip tone={a.level === "certain" ? "red" : a.level === "likely" ? "amber" : "slate"}>{a.level}</Chip>
                <span className="ml-auto">
                  <Chip tone={["closed", "false_positive"].includes(a.status) ? "emerald" : "red"}>{a.status.replace("_", " ")}</Chip>
                </span>
              </div>
              <p className="mt-2 text-sm text-slate-300">
                {a.fraud_type.replace(/_/g, " ")} · {a.fraud_event.replace(/_/g, " ")}
                {a.threshold ? ` · ${a.observed.toLocaleString()} against ${a.threshold.toLocaleString()} (+${a.threshold_overflow.toLocaleString()})` : ""}
              </p>
              {a.comments && <p className="mt-1 line-clamp-2 text-xs text-slate-500">{a.comments}</p>}
            </button>
          ))}
        </div>
      )}
      <Drawer open={!!open} onClose={() => setOpen(null)} title={open ? `${open.alert_number} · ${open.customer}` : ""} subtitle={open ? `${open.fraud_event.replace(/_/g, " ")} · ${open.contact_attempts} contact attempt(s)` : undefined} wide>
        {open && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {[
                ["confirm", "Customer confirms fraud"],
                ["false_positive", "Not fraud"],
                ["no_answer", "No answer"],
                ["investigate", "Investigate"],
                ["disconnect", "Disconnect"],
                ["close", "Close"],
              ].map(([o, label]) => (
                (canRun("INT-94") ? <button key={o} type="button" className={o === "disconnect" || o === "confirm" ? BTN_WARN : BTN} onClick={() => runIntent("INT-94", { fraud_alert: open.alert_number, fraud_outcome: o })}>
                  {label}
                </button> : null)
              ))}
              {canRun("INT-76") ? <button type="button" className={BTN} onClick={() => runIntent("INT-76", { party_id: open.party_id })}>
                Change thresholds
              </button> : null}
            </div>
            <Panel title="Event details">
              {!open.events?.length ? (
                <p className="text-sm text-slate-500">No call records with this alert.</p>
              ) : (
                <ul className="divide-y divide-slate-800 text-xs">
                  {open.events.map((e) => (
                    <li key={e.id} className="flex flex-wrap gap-2 py-1.5 text-slate-300">
                      <span className="w-32 text-slate-500">{fmtWhen(e.at)}</span>
                      <span>{e.event_type}</span>
                      <span className="font-mono">{e.destination}</span>
                      {e.origin_location && <span className="text-slate-500">from {e.origin_location}</span>}
                      {e.international && <Chip tone="amber">intl</Chip>}
                      {e.premium && <Chip tone="red">premium</Chip>}
                      <span className="ml-auto">
                        {e.quantity} · {money(e.amount)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            {open.comments && <p className="whitespace-pre-line text-xs text-slate-400">{open.comments}</p>}
          </div>
        )}
      </Drawer>
    </div>
  );
}

interface AgreementRow {
  id: number;
  agreement_number: string;
  name: string;
  customer: string;
  status: string;
  revision: number;
  parent_agreement_id: number | null;
  end_date: string;
  monthly_value: number;
  document: boolean;
  signed: boolean;
}

interface AgreementFull {
  agreement_number: string;
  name: string;
  document: string;
  terms: { term: string; value: string }[];
  revisions: { revision: number; reason: string; changes: { field: string; new: unknown }[]; at: string }[];
  sub_agreements: { id: number; name: string }[];
}

function Agreements() {
  const { runIntent, canRun } = useWorkspace();
  const [rows, setRows] = useState<AgreementRow[]>([]);
  const [open, setOpen] = useState<AgreementFull | null>(null);
  const load = useCallback(() => {
    commsApi<AgreementRow[]>("/agreements").then(setRows).catch(() => setRows([]));
  }, []);
  useEffect(load, [load]);
  useIntentRefresh(load);
  return (
    <div className="space-y-3">
      {rows.length === 0 ? (
        <Empty>No agreements - convert a quote to one (Convert a quote to an agreement).</Empty>
      ) : (
        <ul className="divide-y divide-slate-800 rounded-2xl border border-slate-800">
          {rows.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm">
              <button type="button" className="font-mono text-xs text-sky-400" onClick={async () => setOpen(await commsApi<AgreementFull>(`/agreements/${a.id}`))}>
                {a.agreement_number}
              </button>
              <span className="text-slate-200">{a.name}</span>
              {a.parent_agreement_id && <Chip tone="violet">sub-agreement</Chip>}
              <span className="text-xs text-slate-500">{a.customer}</span>
              <Chip tone={a.status === "active" ? "emerald" : "slate"}>{a.status}</Chip>
              {a.signed ? <Chip tone="emerald">signed</Chip> : a.document ? <Chip tone="sky">document ready</Chip> : null}
              <span className="ml-auto text-xs text-slate-400">
                {money(a.monthly_value)}/mo · to {fmtDate(a.end_date)}
              </span>
              {canRun("INT-100") ? <button type="button" className={BTN} onClick={() => runIntent("INT-100", { agreement_id: a.agreement_number })}>
                Revise
              </button> : null}
              {canRun("INT-101") ? <button type="button" className={BTN} onClick={() => runIntent("INT-101", { agreement_id: a.agreement_number })}>
                Document
              </button> : null}
            </li>
          ))}
        </ul>
      )}
      <Drawer open={!!open} onClose={() => setOpen(null)} title={open ? `${open.agreement_number} · ${open.name}` : ""} wide>
        {open && (
          <div className="space-y-4">
            {open.terms.length > 0 && (
              <Panel title="Terms">
                <ul className="space-y-1 text-sm text-slate-300">
                  {open.terms.map((t) => (
                    <li key={t.term}>
                      <b className="text-slate-200">{t.term}:</b> {t.value}
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
            {open.sub_agreements.length > 0 && <p className="text-sm text-slate-400">Sub-agreements: {open.sub_agreements.map((s) => s.name).join(", ")}</p>}
            <Panel title="Document">
              {open.document ? <pre className="whitespace-pre-wrap text-xs text-slate-300">{open.document}</pre> : <p className="text-sm text-slate-500">Not generated for this revision yet.</p>}
            </Panel>
            {open.revisions.length > 0 && (
              <Panel title="Revisions">
                <ul className="space-y-1 text-xs text-slate-400">
                  {open.revisions.map((r) => (
                    <li key={r.revision}>
                      Rev {r.revision} · {fmtWhen(r.at)} · {r.changes.map((c) => `${c.field} → ${String(c.new)}`).join("; ")}
                      {r.reason ? ` (${r.reason})` : ""}
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}

interface ProviderWo {
  id: number;
  number: string;
  order_id: number;
  provider: string;
  lines: { product: string; quantity: number; action: string }[];
  status: string;
  due_date: string | null;
  notes: string;
}

function Providers() {
  const { runIntent, canRun } = useWorkspace();
  const [rows, setRows] = useState<ProviderWo[]>([]);
  const load = useCallback(() => {
    commsApi<ProviderWo[]>("/provider-work-orders").then(setRows).catch(() => setRows([]));
  }, []);
  useEffect(load, [load]);
  useIntentRefresh(load);
  const act = useAction(load);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex-1 text-sm text-slate-400">A submitted order is split into one work order per provider by its products (Admin &gt; Communications &gt; Providers).</span>
        {canRun("INT-102") ? <button type="button" className={BTN_PRIMARY} onClick={() => runIntent("INT-102")}>
          Allocate an order
        </button> : null}
      </div>
      <Notice text={act.error} onClose={() => act.setError(null)} />
      {rows.length === 0 ? (
        <Empty>No provider work orders yet.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {rows.map((w) => (
            <div key={w.id} className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-500">{w.number}</span>
                <span className="font-semibold text-slate-100">{w.provider}</span>
                <span className="text-xs text-slate-500">order #{w.order_id}</span>
                <span className="ml-auto">
                  <Chip tone={w.status === "complete" ? "emerald" : w.status === "failed" ? "red" : w.status === "backordered" ? "amber" : "sky"}>{w.status.replace("_", " ")}</Chip>
                </span>
              </div>
              <p className="mt-2 text-xs text-slate-400">
                {w.lines.map((l) => `${l.quantity} x ${l.product} (${l.action})`).join(", ")} · due {fmtDate(w.due_date)}
              </p>
              {w.notes && <p className="mt-1 text-xs text-slate-500">{w.notes}</p>}
              {!["complete", "failed"].includes(w.status) && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {[
                    ["in_progress", "In progress"],
                    ["backordered", "Backordered"],
                    ["complete", "Complete"],
                    ["failed", "Failed"],
                  ].map(([s, label]) => (
                    <button key={s} type="button" className={s === "failed" ? BTN_WARN : BTN} disabled={act.busy || w.status === s}
                      onClick={() => act.run(() => commsApi(`/provider-work-orders/${w.id}`, "PUT", { status: s }))}>
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
