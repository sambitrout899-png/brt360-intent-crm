import { useCallback, useEffect, useState } from "react";
import { platformLicApi } from "../../api/client";
import CatalogEditor from "./CatalogEditor";
import SubscriptionEditor, { InvoiceTable } from "./SubscriptionEditor";
import type { Invoice, LicCatalog, LicRequest, LicSettings, Overview, SubStatus, TenantRow } from "./types";
import { BTN, BTN_PRIMARY, Card, day, Field, INPUT, Kpi, money, Notice, StatusBadge } from "./ui";

type Tab = "overview" | "orgs" | "catalog" | "invoices" | "requests" | "settings";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "orgs", label: "Subscriptions" },
  { id: "catalog", label: "Products & Pricing" },
  { id: "invoices", label: "Invoices" },
  { id: "requests", label: "Requests" },
  { id: "settings", label: "Settings" },
];

function sumMoney(m: Record<string, number>): string {
  const entries = Object.entries(m);
  return entries.length ? entries.map(([c, v]) => money(v, c)).join(" + ") : "—";
}

/** The platform admin's licensing console: revenue, every organization's subscription, the catalog and its prices. */
export default function PlatformLicensing() {
  const [tab, setTab] = useState<Tab>("overview");
  const [catalog, setCatalog] = useState<LicCatalog | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [tenants, setTenants] = useState<TenantRow[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(() => {
    platformLicApi<LicCatalog>("/catalog").then(setCatalog).catch((e) => setError(String(e.message ?? e)));
    platformLicApi<Overview>("/overview").then(setOverview).catch(() => undefined);
    platformLicApi<TenantRow[]>("/tenants").then(setTenants).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  const open = (tenantId: number) => {
    setSelected(tenantId);
    setTab("orgs");
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1 border-b border-slate-800">
        {TABS.map((t) => (
          <button key={t.id} type="button" onClick={() => setTab(t.id)}
                  className={`px-3 py-2 text-sm font-semibold ${tab === t.id ? "border-b-2 border-sky-500 text-sky-300" : "text-slate-400 hover:text-slate-200"}`}>
            {t.label}
            {t.id === "requests" && overview?.open_requests ? <span className="ml-1.5 rounded-full bg-amber-500 px-1.5 text-[10px] text-slate-950">{overview.open_requests}</span> : null}
          </button>
        ))}
      </div>
      <Notice text={error} onClose={() => setError(null)} />
      <Notice text={done} tone="emerald" onClose={() => setDone(null)} />

      {tab === "overview" && overview && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi label="MRR" value={sumMoney(overview.mrr)} sub="active and past-due contracts, net" />
            <Kpi label="ARR" value={sumMoney(overview.arr)} />
            <Kpi label="Organizations" value={`${overview.subscribed} / ${overview.tenants}`} sub={`${overview.unsubscribed} without a subscription`} />
            <Kpi label="Seats" value={`${overview.seats.assigned} / ${overview.seats.purchased}`}
                 sub={overview.seats.purchased ? `${Math.round((overview.seats.assigned / overview.seats.purchased) * 100)}% in use` : "none sold"} />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="By status">
              <div className="space-y-2">
                {Object.entries(overview.by_status).map(([s, n]) => (
                  <div key={s} className="flex items-center gap-2"><StatusBadge status={s as SubStatus} /><span className="ml-auto text-sm font-semibold text-slate-200">{n}</span></div>
                ))}
                {!Object.keys(overview.by_status).length && <p className="text-xs text-slate-500">No subscriptions yet.</p>}
              </div>
            </Card>
            <Card title="Revenue by product (monthly)" className="lg:col-span-2">
              <div className="space-y-1.5">
                {Object.entries(overview.by_product).sort((a, b) => Object.values(b[1])[0] - Object.values(a[1])[0]).map(([name, byCur]) => (
                  <div key={name} className="flex items-center gap-2 text-sm">
                    <span className="min-w-0 flex-1 truncate text-slate-300">{name}</span>
                    <span className="font-mono text-slate-100">{sumMoney(byCur)}</span>
                  </div>
                ))}
                {!Object.keys(overview.by_product).length && <p className="text-xs text-slate-500">No active revenue yet.</p>}
              </div>
            </Card>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Renewals & trials ending (45 days)">
              {overview.renewals.length ? (
                <table className="w-full text-sm">
                  <tbody>
                    {overview.renewals.map((r) => (
                      <tr key={r.tenant_id} className="cursor-pointer border-t border-slate-800 hover:bg-slate-800/40" onClick={() => open(r.tenant_id)}>
                        <td className="py-1.5 text-slate-200">{r.tenant_name}</td>
                        <td className="py-1.5"><StatusBadge status={r.status} /></td>
                        <td className={`py-1.5 text-xs ${r.days_left < 0 ? "text-red-300" : r.days_left <= 14 ? "text-amber-300" : "text-slate-400"}`}>
                          {r.days_left < 0 ? `${-r.days_left}d ago` : `in ${r.days_left}d`} · {day(r.ends)}
                        </td>
                        <td className="py-1.5 text-xs text-slate-500">{r.auto_renew ? "auto-renew" : "manual"}</td>
                        <td className="py-1.5 text-right font-mono text-slate-300">{money(r.mrr, r.currency)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="text-xs text-slate-500">Nothing ends in the next 45 days.</p>}
            </Card>
            <Card title="Overdue invoices">
              <InvoiceTable invoices={overview.overdue_invoices} showTenant />
            </Card>
          </div>
        </div>
      )}

      {tab === "orgs" && catalog && (
        <div className="grid gap-4 xl:grid-cols-[18rem_minmax(0,1fr)]">
          <aside className="space-y-2">
            <input className={INPUT} placeholder="Find an organization…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <div className="space-y-1">
              {tenants.filter((t) => !filter || `${t.name} ${t.slug}`.toLowerCase().includes(filter.toLowerCase())).map((t) => (
                <button key={t.tenant_id} type="button" onClick={() => setSelected(t.tenant_id)}
                        className={`w-full rounded-xl border px-3 py-2 text-left ${selected === t.tenant_id ? "border-sky-500/60 bg-sky-500/10" : "border-slate-800 hover:bg-slate-800/50"}`}>
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{t.name}</span>
                    <StatusBadge status={t.subscription?.status} />
                  </div>
                  <p className="mt-0.5 truncate text-[11px] text-slate-500">
                    {t.slug} · {t.users} users{t.subscription ? ` · ${money(t.subscription.mrr, t.subscription.currency)}/mo` : ""}
                  </p>
                </button>
              ))}
            </div>
          </aside>
          <div className="min-w-0">
            {selected ? (
              <SubscriptionEditor key={selected} tenantId={selected} catalog={catalog} onChanged={load} setError={setError} setDone={setDone} />
            ) : (
              <p className="rounded-2xl border border-dashed border-slate-700 p-8 text-center text-sm text-slate-500">Pick an organization to see or set up its subscription.</p>
            )}
          </div>
        </div>
      )}

      {tab === "catalog" && catalog && <CatalogEditor catalog={catalog} onChanged={load} setError={setError} setDone={setDone} />}
      {tab === "invoices" && <AllInvoices setError={setError} setDone={setDone} onChanged={load} />}
      {tab === "requests" && <Requests setError={setError} setDone={setDone} onChanged={load} />}
      {tab === "settings" && catalog && <SettingsForm settings={catalog.settings} onSaved={load} setError={setError} setDone={setDone} />}
    </div>
  );
}

type Msg = { setError: (e: string | null) => void; setDone: (d: string | null) => void; onChanged: () => void };

function AllInvoices({ setError, setDone, onChanged }: Msg) {
  const [rows, setRows] = useState<Invoice[]>([]);
  const [status, setStatus] = useState("all");
  const load = useCallback(() => {
    platformLicApi<Invoice[]>("/invoices").then(setRows).catch((e) => setError(String(e.message ?? e)));
  }, [setError]);
  useEffect(load, [load]);
  const shown = rows.filter((r) => status === "all" || r.status === status);
  return (
    <Card title="Invoices" action={
      <select className={`${INPUT} w-40`} value={status} onChange={(e) => setStatus(e.target.value)}>
        {["all", "issued", "overdue", "paid", "void"].map((s) => <option key={s}>{s}</option>)}
      </select>
    }>
      <InvoiceTable invoices={shown} showTenant onStatus={async (inv, st) => {
        const ref = st === "paid" ? window.prompt(`Payment reference for ${inv.number}:`) : "";
        if (ref === null) return;
        try {
          await platformLicApi(`/invoices/${inv.id}/status`, "POST", { status: st, payment_ref: ref });
          setDone(`${inv.number} ${st}`);
          load();
          onChanged();
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }} />
    </Card>
  );
}

function Requests({ setError, setDone, onChanged }: Msg) {
  const [rows, setRows] = useState<LicRequest[]>([]);
  const [status, setStatus] = useState("open");
  const load = useCallback(() => {
    platformLicApi<LicRequest[]>(`/requests?status=${status}`).then(setRows).catch((e) => setError(String(e.message ?? e)));
  }, [status, setError]);
  useEffect(load, [load]);
  const resolve = async (r: LicRequest, approve: boolean) => {
    const response = window.prompt(approve ? "Note to the organization (optional):" : "Why is it declined?") ?? null;
    if (response === null) return;
    try {
      await platformLicApi(`/requests/${r.id}`, "POST", { approve, response });
      setDone(`Request #${r.id} ${approve ? "approved" : "declined"}${approve && r.product_code ? " - added to the contract" : ""}`);
      load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <Card title="Requests from organizations" action={
      <select className={`${INPUT} w-40`} value={status} onChange={(e) => setStatus(e.target.value)}>
        {["open", "approved", "declined", "all"].map((s) => <option key={s}>{s}</option>)}
      </select>
    }>
      <div className="space-y-2">
        {rows.map((r) => (
          <div key={r.id} className="flex flex-wrap items-start gap-3 rounded-xl border border-slate-800 px-3 py-2">
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-100">
                <span className="font-semibold">{r.tenant_name}</span> · {r.kind === "seats" ? `${r.quantity} more ${r.product_name}` : r.kind === "product" ? `${r.product_name} (${r.quantity})` : r.kind}
              </p>
              {r.message && <p className="text-xs text-slate-400">“{r.message}”</p>}
              <p className="text-[11px] text-slate-500">{r.requested_by} · {new Date(r.created_at).toLocaleString()}{r.response ? ` · reply: ${r.response}` : ""}</p>
            </div>
            {r.status === "open" ? (
              <div className="flex gap-2">
                <button type="button" className={BTN_PRIMARY} onClick={() => resolve(r, true)}>Approve{r.product_code ? " & add" : ""}</button>
                <button type="button" className={BTN} onClick={() => resolve(r, false)}>Decline</button>
              </div>
            ) : <span className="text-xs capitalize text-slate-400">{r.status}</span>}
          </div>
        ))}
        {rows.length === 0 && <p className="text-xs text-slate-500">No {status === "all" ? "" : status} requests.</p>}
      </div>
    </Card>
  );
}

function SettingsForm({ settings, onSaved, setError, setDone }: { settings: LicSettings; onSaved: () => void } & Omit<Msg, "onChanged">) {
  const [s, setS] = useState({ ...settings, currencies: settings.currencies.join(", ") });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await platformLicApi("/settings", "PUT", { ...s, currencies: s.currencies.split(",").map((c) => c.trim().toUpperCase()).filter(Boolean),
        tax_pct: Number(s.tax_pct), grace_days: Number(s.grace_days), payment_terms_days: Number(s.payment_terms_days), next_invoice_no: Number(s.next_invoice_no) });
      setDone("Licensing settings saved");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Licensing settings" action={<button type="button" className={BTN_PRIMARY} disabled={busy} onClick={save}>Save settings</button>}>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Currencies" hint="3-letter codes, comma-separated - each product can be priced in each">
          <input className={INPUT} value={s.currencies} onChange={(e) => setS({ ...s, currencies: e.target.value })} />
        </Field>
        <Field label="Default currency"><input className={INPUT} value={s.default_currency} onChange={(e) => setS({ ...s, default_currency: e.target.value.toUpperCase() })} /></Field>
        <Field label="Tax name"><input className={INPUT} value={s.tax_name} onChange={(e) => setS({ ...s, tax_name: e.target.value })} /></Field>
        <Field label="Tax %"><input className={INPUT} type="number" min={0} max={100} value={s.tax_pct} onChange={(e) => setS({ ...s, tax_pct: Number(e.target.value) })} /></Field>
        <Field label="Grace period (days)" hint="How long a lapsed or unpaid contract keeps working"><input className={INPUT} type="number" min={0} value={s.grace_days} onChange={(e) => setS({ ...s, grace_days: Number(e.target.value) })} /></Field>
        <Field label="Payment terms (days)"><input className={INPUT} type="number" min={0} value={s.payment_terms_days} onChange={(e) => setS({ ...s, payment_terms_days: Number(e.target.value) })} /></Field>
        <Field label="Invoice prefix"><input className={INPUT} value={s.invoice_prefix} onChange={(e) => setS({ ...s, invoice_prefix: e.target.value })} /></Field>
        <Field label="Next invoice number"><input className={INPUT} type="number" min={1} value={s.next_invoice_no} onChange={(e) => setS({ ...s, next_invoice_no: Number(e.target.value) })} /></Field>
        <Field label="Seller tax id (on invoices)"><input className={INPUT} value={s.seller_tax_id} onChange={(e) => setS({ ...s, seller_tax_id: e.target.value })} /></Field>
        <Field label="Seller name (on invoices)"><input className={INPUT} value={s.seller_name} onChange={(e) => setS({ ...s, seller_name: e.target.value })} /></Field>
        <div className="md:col-span-2"><Field label="Seller address (on invoices)"><input className={INPUT} value={s.seller_address} onChange={(e) => setS({ ...s, seller_address: e.target.value })} /></Field></div>
      </div>
    </Card>
  );
}
