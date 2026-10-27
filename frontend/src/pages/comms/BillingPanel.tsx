import { useCallback, useEffect, useState } from "react";
import { commsApi } from "../../api/client";
import { useIntentRefresh, useWorkspace } from "../../workspace";
import { BTN, BTN_PRIMARY, Chip, Drawer, Panel, Stat } from "../fieldservice/ui";
import { fmtDate, fmtWhen, money } from "../fieldservice/style";
import type { BillingPortal, Invoice } from "./types";

const INVOICE_TONE: Record<string, "slate" | "sky" | "emerald" | "amber" | "red" | "violet"> = {
  open: "sky", partially_paid: "amber", paid: "emerald", overdue: "red", written_off: "slate",
};

export function InvoiceChip({ status }: { status: string }) {
  return <Chip tone={INVOICE_TONE[status] ?? "slate"}>{status.replace("_", " ")}</Chip>;
}

interface Explained {
  invoice: Invoice & { lines: { id: number; kind: string; description: string; amount: number }[]; payments: { payment_number: string; amount: number; method: string }[] };
  previous: Invoice | null;
  changes: { part: string; previous: number; now: number; change: number; pct: number | null }[];
  top_destinations: { destination: string; events: number; quantity: number; amount: number; international: boolean; location: string }[];
  notes: string[];
}

/** One bill, explained: its lines, against the previous bill, where the usage went - and what to do about it. */
export function InvoiceDrawer({ invoice, onClose }: { invoice: string | null; onClose: () => void }) {
  const { runIntent, canRun } = useWorkspace();
  const [loaded, setLoaded] = useState<{ ref: string; data: Explained | null; error: string } | null>(null);
  useEffect(() => {
    if (!invoice) return;
    commsApi<Explained>(`/invoices/${invoice}`)
      .then((d) => setLoaded({ ref: invoice, data: d, error: "" }))
      .catch((e) => setLoaded({ ref: invoice, data: null, error: e instanceof Error ? e.message : "Could not load the bill" }));
  }, [invoice]);
  const current = loaded && loaded.ref === invoice ? loaded : null;
  const data = current?.data ?? null;
  const error = current?.error ?? "";
  const inv = data?.invoice;
  return (
    <Drawer open={!!invoice} onClose={onClose} title={inv ? `${inv.invoice_number} · ${inv.customer}` : invoice ?? ""} subtitle={inv ? `${fmtDate(inv.period_start)} – ${fmtDate(inv.period_end)} · due ${fmtDate(inv.due_date)}` : undefined} wide>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {inv && data && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <InvoiceChip status={inv.status} />
            <span className="text-sm text-slate-300">
              Total {money(inv.total)} · paid {money(inv.paid)} · credited {money(inv.credited)} · <b className="text-slate-100">due {money(inv.balance_due)}</b>
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {inv.balance_due > 0 && (
              (canRun("INT-85") ? <button type="button" className={BTN_PRIMARY} onClick={() => runIntent("INT-85", { invoice: inv.invoice_number })}>
                Take payment
              </button> : null)
            )}
            {canRun("INT-86") ? <button type="button" className={BTN} onClick={() => runIntent("INT-86", { invoice: inv.invoice_number })}>
              Adjust
            </button> : null}
            {inv.balance_due > 0 && (
              (canRun("INT-87") ? <button type="button" className={BTN} onClick={() => runIntent("INT-87", { invoice: inv.invoice_number })}>
                Payment arrangement
              </button> : null)
            )}
            {canRun("INT-89") ? <button type="button" className={BTN} onClick={() => runIntent("INT-89", { invoice: inv.invoice_number })}>
              Send a copy
            </button> : null}
          </div>
          {data.notes.length > 0 && (
            <ul className="space-y-1 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
              {data.notes.map((n) => (
                <li key={n}>• {n}</li>
              ))}
            </ul>
          )}
          <Panel title="Lines">
            <ul className="divide-y divide-slate-800 text-sm">
              {inv.lines.map((l, i) => (
                <li key={l.id} className="flex items-center gap-2 py-1.5">
                  <span className="w-5 text-[11px] text-slate-600">{i + 1}</span>
                  <Chip>{l.kind.replace("_", " ")}</Chip>
                  <span className="min-w-0 flex-1 truncate text-slate-300">{l.description}</span>
                  <span className={l.amount < 0 ? "text-emerald-400" : "text-slate-200"}>{money(l.amount)}</span>
                  {canRun("INT-86") ? <button type="button" className="text-[11px] text-sky-400" title="Adjust this line" onClick={() => runIntent("INT-86", { invoice: inv.invoice_number, line: String(i + 1) })}>
                    adjust
                  </button> : null}
                </li>
              ))}
            </ul>
          </Panel>
          {data.changes.length > 0 && data.previous && (
            <Panel title={`Against ${data.previous.invoice_number}`}>
              <ul className="space-y-1 text-xs text-slate-300">
                {data.changes.map((c) => (
                  <li key={c.part}>
                    {c.part.replace("_", " ")}: {money(c.previous)} → {money(c.now)}{" "}
                    <span className={c.change > 0 ? "text-amber-300" : "text-emerald-400"}>
                      ({c.change > 0 ? "+" : ""}
                      {money(c.change)}
                      {c.pct !== null ? `, ${c.pct}%` : ""})
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          {data.top_destinations.length > 0 && (
            <Panel title="Where the usage went">
              <ul className="space-y-1 text-xs text-slate-300">
                {data.top_destinations.map((d) => (
                  <li key={d.destination} className="flex gap-2">
                    <span className="font-mono">{d.destination}</span>
                    {d.international && <Chip tone="amber">international</Chip>}
                    <span className="text-slate-500">{d.location}</span>
                    <span className="ml-auto">
                      {d.events} × {d.quantity} · {money(d.amount)}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          {inv.payments.length > 0 && (
            <p className="text-xs text-slate-400">Payments: {inv.payments.map((p) => `${p.payment_number} ${money(p.amount)} (${p.method.replace("_", " ")})`).join(" · ")}</p>
          )}
        </div>
      )}
    </Drawer>
  );
}

/** Customer 360 > Billing: balance, what is not billed yet, bills, payments, adjustments, plans and alerts. */
export default function BillingPanel({ partyId }: { partyId: number }) {
  const { runIntent, canRun } = useWorkspace();
  const [data, setData] = useState<BillingPortal | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [profileId, setProfileId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    commsApi<BillingPortal>(`/accounts/${partyId}/billing`).then(d => { setData(d); setError(""); }).catch(() => setError("Could not load billing information. Please retry."));
  }, [partyId]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  if (error) return <p role="alert" className="text-sm text-red-400">{error} <button onClick={load} className="text-sky-400">Retry</button></p>;
  if (!data) return <p className="text-sm text-slate-500">Loading billing…</p>;
  const party = { party_id: partyId };
  const selectedProfile = data.billing_profiles.find(p => p.id === profileId);
  const invoices = profileId === null ? data.invoices : data.invoices.filter(i => profileId === -1 ? !i.billing_profile_id : i.billing_profile_id === profileId);
  const invoiceIds = new Set(invoices.map(i => i.id));
  const payments = profileId === null ? data.payments : data.payments.filter(p => profileId === -1 ? !p.billing_profile_id && (!p.applied.length || p.applied.some(a => invoiceIds.has(a.invoice_id))) : p.billing_profile_id === profileId || p.applied.some(a => invoiceIds.has(a.invoice_id)));
  const maxUsage = Math.max(1, ...data.usage_history.map((m) => m.amount));
  return (
    <div className="space-y-4">
      <div className="account-applet">
        <header className="account-applet-heading"><h3>Account #{partyId} → Billing account {data.billing_account ? `#${data.billing_account.id}` : "not created"}</h3>{canRun("INT-74") && <button onClick={() => runIntent("INT-74", party)}>+ / Edit bill profile</button>}</header>
        <div className="billing-profile-tree" aria-label="Bill profiles">
          <button aria-pressed={profileId === null} onClick={() => setProfileId(null)}><strong>Billing account summary</strong><small>All bill profiles</small></button>
          {data.billing_profiles.map(p => <button key={p.id} aria-pressed={profileId === p.id} onClick={() => setProfileId(p.id)}><strong>↳ {p.name} {p.primary ? "· Primary" : ""}</strong><small>Bill profile #{p.id} · {p.frequency} · {p.bill_media}</small></button>)}
          {(data.invoices.some(i => !i.billing_profile_id) || data.payments.some(p => !p.billing_profile_id && !p.applied.length)) && <button aria-pressed={profileId === -1} onClick={() => setProfileId(-1)}><strong>↳ Account-level / unassigned</strong><small>Records without a bill-profile reference</small></button>}
        </div>
        {!data.billing_profiles.length && <p className="account-empty">No bill profiles yet. Create a bill profile to define billing frequency, payment method and bill delivery.</p>}
      </div>
      {selectedProfile && <Panel title={`Bill profile: ${selectedProfile.name}`} action={canRun("INT-74") ? <button className={BTN} onClick={() => runIntent("INT-74", { ...party, billing_profile_id: selectedProfile.id, profile_name: selectedProfile.name })}>Edit profile</button> : undefined}>
        <p className="text-sm text-slate-300">{selectedProfile.frequency} · {selectedProfile.payment_type} · {selectedProfile.payment_method.replaceAll("_", " ")} · bills by {selectedProfile.bill_media}{selectedProfile.email ? ` to ${selectedProfile.email}` : ""} · due in {selectedProfile.payment_terms_days} days</p>
        <p className="mt-2 text-xs text-slate-500">Invoices and linked payments below belong to this bill profile. Shared account totals, unbilled charges, arrangements and alerts remain at billing-account level.</p>
      </Panel>}
      <p className="text-xs text-slate-500">Billing-account totals · Actions below operate on the account or the invoice selected in the action form.</p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Balance" value={money(data.balance)} tone={data.balance > 0 ? "text-amber-300" : "text-emerald-400"} />
        <Stat label="Overdue" value={money(data.overdue)} tone={data.overdue > 0 ? "text-red-400" : undefined} />
        <Stat label="Not billed yet" value={money(data.unbilled.total)} sub={`next bill ${fmtDate(data.unbilled.next_invoice_date)}`} />
        <Stat label="Bills" value={data.invoices.length} sub={data.invoices[0] ? `last ${data.invoices[0].invoice_number}` : "none yet"} />
      </div>
      <div className="flex flex-wrap gap-2">
        {canRun("INT-84") ? <button type="button" className={BTN_PRIMARY} onClick={() => runIntent("INT-84", party)}>
          Run a bill
        </button> : null}
        {canRun("INT-85") ? <button type="button" className={BTN} onClick={() => runIntent("INT-85", party)}>
          Take a payment
        </button> : null}
        {canRun("INT-86") ? <button type="button" className={BTN} onClick={() => runIntent("INT-86", party)}>
          Adjust
        </button> : null}
        {canRun("INT-87") ? <button type="button" className={BTN} onClick={() => runIntent("INT-87", party)}>
          Payment arrangement
        </button> : null}
        {canRun("INT-92") ? <button type="button" className={BTN} onClick={() => runIntent("INT-92", party)}>
          Credit alert
        </button> : null}
        {canRun("INT-93") ? <button type="button" className={BTN} onClick={() => runIntent("INT-93", { ...party, fraud_type: "stolen_handset" })}>
          Report fraud
        </button> : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title={selectedProfile ? `Bills / invoices · ${selectedProfile.name}` : profileId === -1 ? "Unassigned bills / invoices" : "Bills / invoices · All bill profiles"}>
          {invoices.length === 0 ? (
            <p className="text-sm text-slate-500">No bills in this selection.</p>
          ) : (
            <ul className="divide-y divide-slate-800">
              {invoices.map((i) => (
                <li key={i.id}>
                  <button type="button" onClick={() => setOpen(i.invoice_number)} className="flex w-full flex-wrap items-center gap-2 py-2 text-left hover:bg-slate-800/30">
                    <span className="font-mono text-xs text-slate-400">{i.invoice_number}</span>
                    <span className="text-xs text-slate-500">
                      {fmtDate(i.period_start)} – {fmtDate(i.period_end)}
                    </span>
                    <InvoiceChip status={i.status} />
                    {profileId === null && <span className="text-xs text-slate-500">{data.billing_profiles.find(p => p.id === i.billing_profile_id)?.name || "Account-level / unassigned"}</span>}
                    <span className="ml-auto text-sm text-slate-200">{money(i.total)}</span>
                    {i.balance_due > 0 && <span className="text-xs text-amber-300">due {money(i.balance_due)}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Not billed yet">
          <ul className="space-y-1 text-sm text-slate-300">
            <li className="flex">Recurring<span className="ml-auto">{money(data.unbilled.recurring)}</span></li>
            <li className="flex">One-time & credits<span className="ml-auto">{money(data.unbilled.non_recurring)}</span></li>
            <li className="flex">Usage<span className="ml-auto">{money(data.unbilled.usage)}</span></li>
            <li className="flex">Taxes<span className="ml-auto">{money(data.unbilled.surcharges)}</span></li>
            <li className="flex border-t border-slate-800 pt-1 font-semibold text-slate-100">Total<span className="ml-auto">{money(data.unbilled.total)}</span></li>
          </ul>
          {data.balance_groups.length > 0 && (
            <div className="mt-3 space-y-2">
              {data.balance_groups.map((g) => (
                <div key={g.id}>
                  <p className="flex text-xs text-slate-400">
                    {g.name}
                    <span className="ml-auto">
                      {g.used.toLocaleString()} / {g.granted.toLocaleString()} {g.unit}
                    </span>
                  </p>
                  <div className="mt-0.5 h-1.5 rounded-full bg-slate-800">
                    <div className={`h-1.5 rounded-full ${g.used > g.granted ? "bg-red-400" : "bg-sky-500"}`} style={{ width: `${Math.min(100, (g.used / Math.max(g.granted, 1)) * 100)}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {data.usage_history.length > 0 && (
        <Panel title="Usage by month">
          <div className="flex h-32 items-end gap-2">
            {data.usage_history.map((m) => (
              <div key={m.month} className="flex flex-1 flex-col items-center gap-1" title={`${m.voice} min · ${m.sms} SMS · ${m.data} MB · ${money(m.amount)} (${money(m.international)} international)`}>
                <div className="flex w-full flex-col justify-end" style={{ height: 96 }}>
                  <div className="w-full rounded-t bg-amber-400/70" style={{ height: `${(m.international / maxUsage) * 96}px` }} />
                  <div className="w-full bg-sky-500/70" style={{ height: `${((m.amount - m.international) / maxUsage) * 96}px` }} />
                </div>
                <span className="text-[10px] text-slate-500">{m.month.slice(5)}</span>
              </div>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-slate-500">Rated usage; amber is international.</p>
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title={selectedProfile ? `Linked payments · ${selectedProfile.name}` : "Payments"}>
          {payments.length === 0 ? (
            <p className="text-sm text-slate-500">No payments in this selection.</p>
          ) : (
            <ul className="divide-y divide-slate-800 text-sm">
              {payments.map((p) => (
                <li key={p.id} className="flex flex-wrap items-center gap-2 py-1.5">
                  <span className="font-mono text-xs text-slate-500">{p.payment_number}</span>
                  <span className="text-slate-300">{p.method.replace("_", " ")} {p.card}</span>
                  <span className="text-xs text-slate-500">{fmtWhen(p.received_at)}</span>
                  <span className="ml-auto text-emerald-400">{money(p.amount)}</span>
                  {p.unapplied > 0 && <span className="text-[11px] text-slate-500">({money(p.unapplied)} on account)</span>}
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Adjustments & payment plans">
          {data.adjustments.length + data.arrangements.length === 0 ? (
            <p className="text-sm text-slate-500">None.</p>
          ) : (
            <ul className="divide-y divide-slate-800 text-sm">
              {data.adjustments.map((a) => (
                <li key={`a${a.id}`} className="flex flex-wrap items-center gap-2 py-1.5">
                  <span className="font-mono text-xs text-slate-500">{a.request_number}</span>
                  <span className="text-slate-300">
                    {a.type} {money(a.requested_amount)} · {a.reason.replace("_", " ")}
                  </span>
                  <Chip tone={a.status === "posted" ? "emerald" : a.status === "pending_approval" ? "amber" : a.status === "not_posted" ? "red" : "slate"}>{a.status.replace("_", " ")}</Chip>
                  {a.status !== "pending_approval" && !a.customer_decision && (
                    (canRun("INT-88") ? <button type="button" className="ml-auto text-xs text-sky-400" onClick={() => runIntent("INT-88", { adjustment: a.request_number })}>
                      Customer decision
                    </button> : null)
                  )}
                </li>
              ))}
              {data.arrangements.map((a) => (
                <li key={`p${a.id}`} className="py-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-slate-500">{a.number}</span>
                    <span className="text-slate-300">
                      {a.installments} × {money(a.installment_amount)} ({a.plan_type})
                    </span>
                    <Chip tone={a.status === "active" || a.status === "completed" ? "emerald" : a.status === "broken" || a.status === "rejected" ? "red" : "amber"}>{a.status}</Chip>
                    {(a.status === "approved" || a.status === "countered") && !a.customer_decision && (
                      (canRun("INT-88") ? <button type="button" className="ml-auto text-xs text-sky-400" onClick={() => runIntent("INT-88", { arrangement: a.number })}>
                        Customer decision
                      </button> : null)
                    )}
                  </div>
                  <p className="text-[11px] text-slate-500">{a.outcome}</p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {(data.credit_alerts.length > 0 || data.fraud_alerts.length > 0) && (
        <Panel title="Alerts">
          <ul className="divide-y divide-slate-800 text-sm">
            {data.credit_alerts.map((a) => (
              <li key={`c${a.id}`} className="flex flex-wrap items-center gap-2 py-1.5">
                <Chip tone="amber">credit</Chip>
                <span className="font-mono text-xs text-slate-500">{a.alert_number}</span>
                <span className="text-slate-300">
                  {money(a.amount_owed)} · {a.days_overdue} days · {a.action_type.replace(/_/g, " ")}
                </span>
                <Chip tone={a.status === "closed" ? "emerald" : "amber"}>{a.status}</Chip>
                {a.status !== "closed" && (
                  (canRun("INT-92") ? <button type="button" className="ml-auto text-xs text-sky-400" onClick={() => runIntent("INT-92", { credit_alert: a.alert_number })}>
                    Work it
                  </button> : null)
                )}
              </li>
            ))}
            {data.fraud_alerts.map((a) => (
              <li key={`f${a.id}`} className="flex flex-wrap items-center gap-2 py-1.5">
                <Chip tone="red">fraud</Chip>
                <span className="font-mono text-xs text-slate-500">{a.alert_number}</span>
                <span className="text-slate-300">
                  {a.fraud_event.replace(/_/g, " ")} · {a.level}
                </span>
                <Chip tone={a.status === "closed" || a.status === "false_positive" ? "emerald" : "red"}>{a.status.replace("_", " ")}</Chip>
                {!["closed", "false_positive"].includes(a.status) && (
                  (canRun("INT-94") ? <button type="button" className="ml-auto text-xs text-sky-400" onClick={() => runIntent("INT-94", { fraud_alert: a.alert_number })}>
                    Work it
                  </button> : null)
                )}
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <InvoiceDrawer invoice={open} onClose={() => setOpen(null)} />
    </div>
  );
}
