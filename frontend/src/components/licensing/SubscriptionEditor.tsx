import { useCallback, useEffect, useState } from "react";
import { platformLicApi } from "../../api/client";
import type { Invoice, LicCatalog, SubStatus, Subscription, TenantView } from "./types";
import { BTN, BTN_DANGER, BTN_PRIMARY, Card, CYCLE_LABEL, day, Field, INPUT, InvoiceBadge, Kpi, Meter, money, StatusBadge } from "./ui";

type Props = { tenantId: number; catalog: LicCatalog; onChanged: () => void; setError: (e: string | null) => void; setDone: (d: string | null) => void };
type View = TenantView & { subscription: Subscription | null };

const STATUS_ACTION: Record<SubStatus, { label: string; danger?: boolean; ask?: boolean }> = {
  active: { label: "Activate" },
  trial: { label: "Restart trial" },
  past_due: { label: "Mark past due", ask: true },
  suspended: { label: "Suspend", danger: true, ask: true },
  cancelled: { label: "Cancel", danger: true, ask: true },
  expired: { label: "Expire", danger: true, ask: true },
};

const TERMS = ["currency", "billing_cycle", "term_months", "term_start", "term_end", "discount_pct", "tax_pct", "grace_days", "auto_renew", "auto_assign_seats",
  "contract_ref", "po_number", "billing_contact", "billing_email", "billing_address", "tax_id", "notes"] as const;
type Terms = Record<(typeof TERMS)[number], string | boolean>;

function termsOf(s: Subscription): Terms {
  return {
    currency: s.currency, billing_cycle: s.billing_cycle, term_months: String(s.term_months), term_start: s.term_start ?? "", term_end: s.term_end ?? "",
    discount_pct: String(s.discount_pct), tax_pct: s.tax_pct === null ? "" : String(s.tax_pct), grace_days: s.grace_days === null ? "" : String(s.grace_days),
    auto_renew: s.auto_renew, auto_assign_seats: s.auto_assign_seats, contract_ref: s.contract_ref, po_number: s.po_number, billing_contact: s.billing_contact,
    billing_email: s.billing_email, billing_address: s.billing_address, tax_id: s.tax_id, notes: s.notes ?? "",
  };
}

export default function SubscriptionEditor({ tenantId, catalog, onChanged, setError, setDone }: Props) {
  const [v, setV] = useState<View | null>(null);
  const [terms, setTerms] = useState<Terms | null>(null);
  const [lines, setLines] = useState<Record<string, { quantity: string; unit_price: string; discount_pct: string }>>({});
  const [adding, setAdding] = useState("");
  const [busy, setBusy] = useState(false);
  const [create, setCreate] = useState({ edition_code: catalog.editions.find((e) => e.highlight)?.code ?? catalog.editions[0]?.code ?? "", currency: catalog.settings.default_currency,
    billing_cycle: "annual", term_months: "12" });
  const { products, settings } = catalog;
  const pname = Object.fromEntries(products.map((p) => [p.code, p]));

  const apply = (view: View) => {
    setV(view);
    if (view.subscription) {
      setTerms(termsOf(view.subscription));
      setLines(Object.fromEntries(view.subscription.pricing.lines.map((l) => [l.product, {
        quantity: String(l.quantity), unit_price: l.overridden && l.unit_price !== null ? String(l.unit_price) : "", discount_pct: String(l.discount_pct) }])));
    }
  };
  const load = useCallback(() => {
    platformLicApi<View>(`/tenants/${tenantId}`).then(apply).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [tenantId, setError]);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setDone(done);
      load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!v) return <p className="text-sm text-slate-500">Loading…</p>;
  const s = v.subscription;

  if (!s) {
    return (
      <Card title={`${v.tenant_name} - no subscription`}>
        <p className="mb-3 text-sm text-slate-400">
          It keeps its current modules ({v.modules.map((m) => v.module_labels[m] ?? m).join(", ") || "none"}) until it has a subscription. From then on, the
          subscription decides its modules, seats and limits.
        </p>
        <div className="grid gap-3 md:grid-cols-4">
          <Field label="Start from edition">
            <select className={INPUT} value={create.edition_code} onChange={(e) => setCreate({ ...create, edition_code: e.target.value })}>
              {catalog.editions.filter((e) => e.is_active).map((e) => <option key={e.code} value={e.code}>{e.name}</option>)}
              <option value="">Nothing - add products by hand</option>
            </select>
          </Field>
          <Field label="Currency">
            <select className={INPUT} value={create.currency} onChange={(e) => setCreate({ ...create, currency: e.target.value })}>
              {settings.currencies.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Billing">
            <select className={INPUT} value={create.billing_cycle} onChange={(e) => setCreate({ ...create, billing_cycle: e.target.value })}>
              {Object.entries(CYCLE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </Field>
          <Field label="Term (months)">
            <input className={INPUT} type="number" min={1} max={120} value={create.term_months} onChange={(e) => setCreate({ ...create, term_months: e.target.value })} />
          </Field>
        </div>
        <button type="button" className={`${BTN_PRIMARY} mt-4`} disabled={busy}
                onClick={() => run(() => platformLicApi(`/tenants/${tenantId}`, "PUT", { ...create, edition_code: create.edition_code || undefined, term_months: Number(create.term_months) }),
                  "Subscription created - it starts as a trial; activate it when the contract is signed")}>
          Create subscription
        </button>
      </Card>
    );
  }

  const p = s.pricing;
  const onContract = new Set(p.lines.map((l) => l.product));
  const saveTerms = () => {
    if (!terms) return;
    const body: Record<string, unknown> = {};
    for (const k of TERMS) {
      const val = terms[k];
      if (["term_months", "discount_pct"].includes(k)) body[k] = Number(val || 0);
      else if (["tax_pct", "grace_days"].includes(k)) body[k] = val === "" ? null : Number(val);
      else if (["term_start", "term_end"].includes(k)) body[k] = val || null;
      else body[k] = val;
    }
    return run(() => platformLicApi(`/tenants/${tenantId}`, "PUT", body), "Contract terms saved");
  };
  const setStatus = (status: SubStatus) => {
    const meta = STATUS_ACTION[status];
    const reason = meta.ask ? window.prompt(`${meta.label} ${v.tenant_name} - reason (shown in its history):`) : "";
    if (meta.ask && reason === null) return;
    return run(() => platformLicApi(`/tenants/${tenantId}/status`, "POST", { status, reason }), `${v.tenant_name} is now ${status.replace("_", " ")}`);
  };
  const cur = s.currency;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-lg font-bold text-slate-100">{v.tenant_name}</h3>
        <StatusBadge status={s.status} />
        {s.edition_name && <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{s.edition_name}</span>}
        {s.days_left !== null && s.status !== "expired" && s.status !== "cancelled" && (
          <span className={`text-xs ${s.days_left < 0 ? "text-red-300" : s.days_left <= 30 ? "text-amber-300" : "text-slate-400"}`}>
            {s.status === "trial" ? "Trial" : "Term"} {s.days_left < 0 ? `ended ${-s.days_left} days ago` : `ends in ${s.days_left} days`}
          </span>
        )}
        {s.status_reason && <span className="text-xs text-slate-500">· {s.status_reason}</span>}
        <span className="ml-auto" />
        {s.next_statuses.map((st) => (
          <button key={st} type="button" disabled={busy} className={STATUS_ACTION[st].danger ? BTN_DANGER : st === "active" ? BTN_PRIMARY : BTN} onClick={() => setStatus(st)}>
            {STATUS_ACTION[st].label}
          </button>
        ))}
        <button type="button" disabled={busy} className={BTN} onClick={() => run(() => platformLicApi(`/tenants/${tenantId}/renew`, "POST", {}), "Renewed for another term")}>Renew</button>
        <button type="button" disabled={busy} className={BTN} onClick={() => run(() => platformLicApi(`/tenants/${tenantId}/invoices`, "POST"), "Invoice issued")}>Issue invoice</button>
      </div>

      {s.issues.length > 0 && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          {s.issues.map((i) => <p key={i}>⚠ {i}</p>)}
        </div>
      )}
      {p.unpriced.length > 0 && <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">No {cur} price for {p.unpriced.join(", ")} - set one in the catalog or override it below.</p>}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Kpi label="MRR" value={money(p.mrr, cur)} sub="net of discounts, before tax" />
        <Kpi label="ARR" value={money(p.arr, cur)} />
        <Kpi label={`Per ${s.billing_cycle === "annual" ? "year" : s.billing_cycle === "quarterly" ? "quarter" : "month"}`} value={money(p.per_period, cur)} sub={`incl. ${p.tax_name} ${p.tax_pct}%`} />
        <Kpi label="Term value" value={money(p.term_value, cur)} sub={`${s.term_months} months · ${day(s.term_start)} – ${day(s.term_end)}`} />
        <Kpi label="Modules" value={s.modules.length} sub={s.modules.map((m) => v.module_labels[m] ?? m).join(", ") || (s.granting ? "—" : "none - not granting")} tone={s.granting ? "text-slate-100" : "text-red-300"} />
      </div>

      <Card
        title="Products on the contract"
        action={
          <div className="flex gap-2">
            <select className={`${INPUT} w-56`} value={adding} onChange={(e) => setAdding(e.target.value)}>
              <option value="">Add a product…</option>
              {products.filter((x) => x.is_active && !onContract.has(x.code)).map((x) => <option key={x.code} value={x.code}>{x.name}</option>)}
            </select>
            <button type="button" className={BTN_PRIMARY} disabled={!adding || busy}
                    onClick={() => run(() => platformLicApi(`/tenants/${tenantId}/lines`, "PUT", { product_code: adding, quantity: Math.max(1, pname[adding]?.min_quantity ?? 1), is_active: true }).then(() => setAdding("")), "Product added")}>
              Add
            </button>
          </div>
        }
      >
        <div className="overflow-x-auto rounded-xl border border-slate-800">
          <table className="w-full min-w-[880px] text-sm">
            <thead className="bg-slate-900/80 text-[11px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left">Product</th>
                <th className="px-3 py-2 text-right">Qty</th>
                <th className="px-3 py-2 text-right">List / unit</th>
                <th className="px-3 py-2 text-right">Negotiated / unit</th>
                <th className="px-3 py-2 text-right">Line disc. %</th>
                <th className="px-3 py-2 text-right">Effective / unit</th>
                <th className="px-3 py-2 text-right">Monthly</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {p.lines.map((l) => {
                const d = lines[l.product] ?? { quantity: String(l.quantity), unit_price: "", discount_pct: "0" };
                const dirty = d.quantity !== String(l.quantity) || d.unit_price !== (l.overridden && l.unit_price !== null ? String(l.unit_price) : "") || d.discount_pct !== String(l.discount_pct);
                const set = (k: keyof typeof d, val: string) => setLines({ ...lines, [l.product]: { ...d, [k]: val } });
                return (
                  <tr key={l.product} className="border-t border-slate-800">
                    <td className="px-3 py-2">
                      <p className="font-semibold text-slate-100">{l.name}</p>
                      <p className="text-[11px] text-slate-500">{l.unit_label}{l.annual_discount_pct ? ` · ${l.annual_discount_pct}% annual-term discount` : ""}</p>
                    </td>
                    <td className="px-3 py-2 text-right"><input type="number" min={0} className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right text-slate-100" value={d.quantity} onChange={(e) => set("quantity", e.target.value)} /></td>
                    <td className="px-3 py-2 text-right font-mono text-slate-400">{money(l.list_price, cur, 2)}</td>
                    <td className="px-3 py-2 text-right"><input type="number" min={0} placeholder="list" className="w-28 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right text-slate-100" value={d.unit_price} onChange={(e) => set("unit_price", e.target.value)} /></td>
                    <td className="px-3 py-2 text-right"><input type="number" min={0} max={100} className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right text-slate-100" value={d.discount_pct} onChange={(e) => set("discount_pct", e.target.value)} /></td>
                    <td className="px-3 py-2 text-right font-mono text-slate-300">{money(l.effective_unit_price, cur, 2)}</td>
                    <td className="px-3 py-2 text-right font-mono font-semibold text-slate-100">{money(l.monthly, cur)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      {dirty && (
                        <button type="button" className={`${BTN_PRIMARY} mr-1`} disabled={busy}
                                onClick={() => run(() => platformLicApi(`/tenants/${tenantId}/lines`, "PUT", { product_code: l.product, quantity: Number(d.quantity || 0), unit_price: d.unit_price === "" ? null : Number(d.unit_price), discount_pct: Number(d.discount_pct || 0), is_active: true }), `${l.name} updated`)}>
                          Save
                        </button>
                      )}
                      <button type="button" className="px-1 text-slate-500 hover:text-red-400" title="Remove from the contract" disabled={busy}
                              onClick={() => window.confirm(`Remove ${l.name} from ${v.tenant_name}'s contract? Its seat assignments are released.`) && run(() => platformLicApi(`/tenants/${tenantId}/lines/${l.product}`, "DELETE"), `${l.name} removed`)}>
                        ✕
                      </button>
                    </td>
                  </tr>
                );
              })}
              {p.lines.length === 0 && <tr><td colSpan={8} className="px-3 py-4 text-center text-xs text-slate-500">Nothing on the contract yet.</td></tr>}
            </tbody>
            <tfoot className="text-sm">
              <tr className="border-t border-slate-700"><td colSpan={6} className="px-3 py-1.5 text-right text-slate-400">Subtotal / month</td><td className="px-3 py-1.5 text-right font-mono text-slate-200">{money(p.subtotal_monthly, cur)}</td><td /></tr>
              {p.discount_monthly > 0 && <tr><td colSpan={6} className="px-3 py-1.5 text-right text-slate-400">Contract discount {p.discount_pct}%</td><td className="px-3 py-1.5 text-right font-mono text-emerald-300">−{money(p.discount_monthly, cur)}</td><td /></tr>}
              <tr><td colSpan={6} className="px-3 py-1.5 text-right text-slate-400">{p.tax_name} {p.tax_pct}%</td><td className="px-3 py-1.5 text-right font-mono text-slate-200">{money(p.tax_monthly, cur)}</td><td /></tr>
              <tr><td colSpan={6} className="px-3 py-1.5 text-right font-semibold text-slate-200">Total / month</td><td className="px-3 py-1.5 text-right font-mono font-bold text-slate-100">{money(p.total_monthly, cur)}</td><td /></tr>
            </tfoot>
          </table>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {catalog.editions.filter((e) => e.is_active).map((e) => (
            <button key={e.code} type="button" className={BTN} disabled={busy}
                    onClick={() => window.confirm(`Apply the ${e.name} edition? Its products take the edition's quantities; other lines stay.`) && run(() => platformLicApi(`/tenants/${tenantId}/edition`, "POST", { edition_code: e.code }), `${e.name} applied`)}>
              Apply {e.name}
            </button>
          ))}
        </div>
      </Card>

      {terms && (
        <Card title="Contract terms & billing" action={<button type="button" className={BTN_PRIMARY} disabled={busy} onClick={saveTerms}>Save terms</button>}>
          <div className="grid gap-3 md:grid-cols-4">
            <Field label="Currency">
              <select className={INPUT} value={String(terms.currency)} onChange={(e) => setTerms({ ...terms, currency: e.target.value })}>
                {settings.currencies.map((c) => <option key={c}>{c}</option>)}
              </select>
            </Field>
            <Field label="Billing cycle">
              <select className={INPUT} value={String(terms.billing_cycle)} onChange={(e) => setTerms({ ...terms, billing_cycle: e.target.value })}>
                {Object.entries(CYCLE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </Field>
            <Field label="Term (months)"><input className={INPUT} type="number" min={1} max={120} value={String(terms.term_months)} onChange={(e) => setTerms({ ...terms, term_months: e.target.value })} /></Field>
            <Field label="Contract discount %"><input className={INPUT} type="number" min={0} max={100} value={String(terms.discount_pct)} onChange={(e) => setTerms({ ...terms, discount_pct: e.target.value })} /></Field>
            <Field label="Term start"><input className={INPUT} type="date" value={String(terms.term_start)} onChange={(e) => setTerms({ ...terms, term_start: e.target.value })} /></Field>
            <Field label="Term end"><input className={INPUT} type="date" value={String(terms.term_end)} onChange={(e) => setTerms({ ...terms, term_end: e.target.value })} /></Field>
            <Field label={`Tax % (blank = ${settings.tax_pct}%)`}><input className={INPUT} type="number" min={0} max={100} value={String(terms.tax_pct)} onChange={(e) => setTerms({ ...terms, tax_pct: e.target.value })} /></Field>
            <Field label={`Grace days (blank = ${settings.grace_days})`}><input className={INPUT} type="number" min={0} value={String(terms.grace_days)} onChange={(e) => setTerms({ ...terms, grace_days: e.target.value })} /></Field>
            <Field label="Contract ref"><input className={INPUT} value={String(terms.contract_ref)} onChange={(e) => setTerms({ ...terms, contract_ref: e.target.value })} /></Field>
            <Field label="PO number"><input className={INPUT} value={String(terms.po_number)} onChange={(e) => setTerms({ ...terms, po_number: e.target.value })} /></Field>
            <Field label="Billing contact"><input className={INPUT} value={String(terms.billing_contact)} onChange={(e) => setTerms({ ...terms, billing_contact: e.target.value })} /></Field>
            <Field label="Billing email"><input className={INPUT} type="email" value={String(terms.billing_email)} onChange={(e) => setTerms({ ...terms, billing_email: e.target.value })} /></Field>
            <Field label="Customer tax id"><input className={INPUT} value={String(terms.tax_id)} onChange={(e) => setTerms({ ...terms, tax_id: e.target.value })} /></Field>
            <div className="md:col-span-3"><Field label="Billing address"><input className={INPUT} value={String(terms.billing_address)} onChange={(e) => setTerms({ ...terms, billing_address: e.target.value })} /></Field></div>
            <div className="md:col-span-4"><Field label="Internal notes (never shown to the organization)"><textarea className={INPUT} rows={2} value={String(terms.notes)} onChange={(e) => setTerms({ ...terms, notes: e.target.value })} /></Field></div>
          </div>
          <div className="mt-3 flex flex-wrap gap-5 text-xs text-slate-300">
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={Boolean(terms.auto_renew)} onChange={(e) => setTerms({ ...terms, auto_renew: e.target.checked })} /> Renew automatically at the end of the term</label>
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={Boolean(terms.auto_assign_seats)} onChange={(e) => setTerms({ ...terms, auto_assign_seats: e.target.checked })} /> Give new users a free seat of each per-user service</label>
          </div>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Seats" action={<button type="button" className={BTN} disabled={busy} onClick={() => run(() => platformLicApi(`/tenants/${tenantId}/seats/fill`, "POST"), "Free seats given to users without one")}>Fill free seats</button>}>
          <div className="space-y-3">
            {s.seats.map((seat) => (
              <div key={seat.product}>
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="font-semibold text-slate-200">{seat.name}</span>
                  <span className="ml-auto text-xs text-slate-400">{seat.assigned} assigned · {seat.free} free{seat.over ? ` · ${seat.over} over` : ""}</span>
                </div>
                <Meter used={seat.assigned} limit={seat.purchased} />
              </div>
            ))}
            {s.seats.length === 0 && <p className="text-xs text-slate-500">No per-user products on the contract.</p>}
          </div>
        </Card>
        <Card title="Limits & usage">
          <div className="space-y-3">
            {v.limits.map((l) => (
              <div key={l.key}>
                <p className="text-sm text-slate-200">{l.label}</p>
                <Meter used={l.used} limit={l.limit} />
              </div>
            ))}
            {v.limits.length === 0 && <p className="text-xs text-slate-500">The contract sets no limits.</p>}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Invoices">
          <InvoiceTable invoices={v.invoices} onStatus={(inv, status) => {
            const ref = status === "paid" ? window.prompt(`Payment reference for ${inv.number} (UTR / cheque / transaction id):`) : "";
            if (ref === null) return;
            run(() => platformLicApi(`/invoices/${inv.id}/status`, "POST", { status, payment_ref: ref }), `${inv.number} ${status}`);
          }} />
        </Card>
        <Card title="History">
          <ul className="max-h-80 space-y-1.5 overflow-y-auto text-xs">
            {v.events.map((e) => (
              <li key={e.id} className="flex gap-2">
                <span className="w-28 shrink-0 text-slate-500">{new Date(e.at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
                <span className="min-w-0 flex-1 text-slate-300">{e.summary}<span className="text-slate-600"> · {e.actor}</span></span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

export function InvoiceTable({ invoices, onStatus, showTenant }: { invoices: Invoice[]; onStatus?: (inv: Invoice, status: "paid" | "void") => void; showTenant?: boolean }) {
  if (!invoices.length) return <p className="text-xs text-slate-500">No invoices yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-sm">
        <thead className="text-[11px] uppercase tracking-wider text-slate-500">
          <tr>
            <th className="py-1.5 text-left">Invoice</th>
            {showTenant && <th className="py-1.5 text-left">Organization</th>}
            <th className="py-1.5 text-left">Period</th>
            <th className="py-1.5 text-left">Due</th>
            <th className="py-1.5 text-right">Total</th>
            <th className="py-1.5 text-left pl-3">Status</th>
            {onStatus && <th />}
          </tr>
        </thead>
        <tbody>
          {invoices.map((i) => (
            <tr key={i.id} className="border-t border-slate-800">
              <td className="py-1.5 font-mono text-xs text-slate-200">{i.number}</td>
              {showTenant && <td className="py-1.5 text-slate-300">{i.tenant_name}</td>}
              <td className="py-1.5 text-xs text-slate-400">{day(i.period_start)} – {day(i.period_end)}</td>
              <td className="py-1.5 text-xs text-slate-400">{day(i.due_on)}</td>
              <td className="py-1.5 text-right font-mono text-slate-100">{money(i.total, i.currency, 2)}</td>
              <td className="py-1.5 pl-3"><InvoiceBadge status={i.status} />{i.payment_ref && <span className="ml-1 text-[10px] text-slate-500">{i.payment_ref}</span>}</td>
              {onStatus && (
                <td className="whitespace-nowrap py-1.5 text-right">
                  {(i.status === "issued" || i.status === "overdue") && (
                    <>
                      <button type="button" className={`${BTN} mr-1`} onClick={() => onStatus(i, "paid")}>Mark paid</button>
                      <button type="button" className="text-xs text-slate-500 hover:text-red-400" onClick={() => window.confirm(`Void ${i.number}?`) && onStatus(i, "void")}>Void</button>
                    </>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
