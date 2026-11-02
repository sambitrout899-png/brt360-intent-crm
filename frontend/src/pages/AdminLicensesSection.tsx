import { useCallback, useEffect, useState } from "react";
import { licApi } from "../api/client";
import { InvoiceTable } from "../components/licensing/SubscriptionEditor";
import type { TenantView } from "../components/licensing/types";
import { BTN, BTN_PRIMARY, Card, CYCLE_LABEL, day, Field, INPUT, Kpi, Meter, money, Notice, StatusBadge } from "../components/licensing/ui";

/** An organization's licences, as its administrator sees them (Salesforce's Company Information):
 *  the contract, seats and who holds them, limits and usage, invoices - and asking for more. */
export default function AdminLicensesSection() {
  const [v, setV] = useState<TenantView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState({ kind: "seats", product_code: "", quantity: "5", message: "" });
  const [query, setQuery] = useState("");

  const load = useCallback(() => {
    licApi<TenantView>().then(setV).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<TenantView>, message: string) => {
    setBusy(true);
    setError(null);
    try {
      setV(await fn());
      setDone(message);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!v) return <p className="text-sm text-slate-500">{error ?? "Loading…"}</p>;
  const s = v.subscription;

  if (!s) {
    return (
      <div className="space-y-4">
        <Notice text={error} onClose={() => setError(null)} />
        <Card title="Licenses & Billing">
          <p className="text-sm text-slate-300">{v.tenant_name} has no subscription on record - it uses these modules:</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {v.modules.map((m) => <span key={m} className="rounded-full bg-sky-500/10 px-3 py-1 text-xs text-sky-300">{v.module_labels[m] ?? m}</span>)}
            {!v.modules.length && <span className="text-xs text-slate-500">None.</span>}
          </div>
          <p className="mt-3 text-xs text-slate-500">Your platform provider sets up the subscription; seats, limits and invoices appear here then. Active users: {v.usage.users}.</p>
        </Card>
      </div>
    );
  }

  const seatProducts = s.seats;
  const core = seatProducts.filter((x) => x.kind === "core");
  const services = seatProducts.filter((x) => x.kind !== "core");
  const users = v.users.filter((u) => u.is_active && (!query || `${u.name} ${u.email}`.toLowerCase().includes(query.toLowerCase())));
  const seat = Object.fromEntries(seatProducts.map((x) => [x.product, x]));
  const cur = s.currency;
  const warn =
    s.status === "trial" ? `Trial - ${s.days_left !== null && s.days_left >= 0 ? `${s.days_left} days left` : "ended"}. Ask your provider to activate the subscription to keep access.`
      : s.status === "past_due" ? `Past due${s.status_reason ? ` - ${s.status_reason}` : ""}. Access continues for the ${s.effective_grace_days}-day grace period.`
        : s.status === "suspended" ? `Suspended${s.status_reason ? ` - ${s.status_reason}` : ""}. Licensed modules are closed until it is resolved; your data is kept.`
          : s.status === "expired" || s.status === "cancelled" ? `The subscription is ${s.status}. Licensed modules are closed; your data is kept.`
            : s.days_left !== null && s.days_left <= 30 ? `The term ends in ${s.days_left} days${s.auto_renew ? " and renews automatically" : " - it does not renew automatically"}.` : null;

  return (
    <div className="space-y-4">
      <Notice text={error} onClose={() => setError(null)} />
      <Notice text={done} tone="emerald" onClose={() => setDone(null)} />

      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-slate-100">Licenses & Billing</h2>
        <StatusBadge status={s.status} />
        {s.edition_name && <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{s.edition_name} edition</span>}
        <span className="text-xs text-slate-500">{s.contract_ref && `Contract ${s.contract_ref} · `}{CYCLE_LABEL[s.billing_cycle]} billing · {cur}</span>
      </div>
      {warn && (
        <div className={`rounded-xl border px-3 py-2 text-sm ${s.status === "suspended" || s.status === "expired" || s.status === "cancelled" ? "border-red-500/30 bg-red-500/10 text-red-200" : "border-amber-500/30 bg-amber-500/10 text-amber-200"}`}>
          {warn}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi label="Term" value={`${day(s.term_start)} – ${day(s.term_end)}`} sub={`${s.term_months} months · ${s.auto_renew ? "auto-renews" : "manual renewal"}`} />
        <Kpi label="Core user licences" value={core.length ? `${core.reduce((a, x) => a + x.assigned, 0)} / ${core.reduce((a, x) => a + x.purchased, 0)}` : "—"}
             sub={core.length ? `${core.reduce((a, x) => a + x.free, 0)} free` : ""} />
        <Kpi label="Monthly (incl. tax)" value={money(s.pricing.total_monthly, cur)} sub={`${money(s.pricing.per_period, cur)} per ${s.billing_cycle === "annual" ? "year" : s.billing_cycle === "quarterly" ? "quarter" : "month"}`} />
        <Kpi label="Modules" value={s.modules.length} sub={s.modules.map((m) => v.module_labels[m] ?? m).join(", ") || "none"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Seats">
          <div className="space-y-3">
            {seatProducts.map((x) => (
              <div key={x.product}>
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="font-semibold text-slate-200">{x.name}</span>
                  <span className="ml-auto text-xs text-slate-400">{x.assigned} of {x.purchased} assigned</span>
                </div>
                <Meter used={x.assigned} limit={x.purchased} />
              </div>
            ))}
            {!seatProducts.length && <p className="text-xs text-slate-500">No per-user licences.</p>}
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
            {!v.limits.length && <p className="text-xs text-slate-500">No limits on this contract.</p>}
          </div>
        </Card>
      </div>

      <Card title="Who holds which licence" action={<input className={`${INPUT} w-56`} placeholder="Find a user…" value={query} onChange={(e) => setQuery(e.target.value)} />}>
        <p className="mb-2 text-xs text-slate-400">
          Every active user needs a Core user licence. A per-user service (e.g. Telecom CRM) opens only for users holding its seat. Releasing a Core licence also
          releases that user's service seats.
        </p>
        <div className="overflow-x-auto rounded-xl border border-slate-800">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-slate-900/80 text-[11px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left">User</th>
                {[...core, ...services].map((x) => (
                  <th key={x.product} className="px-3 py-2 text-center">{x.name}<span className="block font-normal normal-case text-slate-600">{x.free} free</span></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-slate-800">
                  <td className="px-3 py-2"><p className="text-slate-100">{u.name}</p><p className="text-[11px] text-slate-500">{u.email} · {u.role}</p></td>
                  {[...core, ...services].map((x) => {
                    const has = u.licenses.includes(x.product);
                    const needsCore = x.kind !== "core" && !core.some((c) => u.licenses.includes(c.product));
                    return (
                      <td key={x.product} className="px-3 py-2 text-center">
                        <input type="checkbox" checked={has} disabled={busy || (!has && (seat[x.product]?.free ?? 0) <= 0) || (!has && needsCore)}
                               title={!has && needsCore ? "Needs a Core user licence first" : !has && (seat[x.product]?.free ?? 0) <= 0 ? "No free seat" : ""}
                               onChange={() => has
                                 ? run(() => licApi<TenantView>(`/seats/${u.id}/${x.product}`, "DELETE"), `${x.name} released from ${u.name}`)
                                 : run(() => licApi<TenantView>("/seats", "POST", { user_id: u.id, product_code: x.product }), `${x.name} given to ${u.name}`)} />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Your contract">
          <table className="w-full text-sm">
            <tbody>
              {s.pricing.lines.map((l) => (
                <tr key={l.product} className="border-t border-slate-800 first:border-t-0">
                  <td className="py-1.5 text-slate-200">{l.name}<span className="block text-[11px] text-slate-500">{l.quantity} × {l.unit_label}</span></td>
                  <td className="py-1.5 text-right font-mono text-slate-300">{money(l.monthly, cur)}<span className="text-[11px] text-slate-500"> /mo</span></td>
                </tr>
              ))}
              {s.pricing.discount_monthly > 0 && <tr className="border-t border-slate-800"><td className="py-1.5 text-slate-400">Discount {s.pricing.discount_pct}%</td><td className="py-1.5 text-right font-mono text-emerald-300">−{money(s.pricing.discount_monthly, cur)}</td></tr>}
              <tr className="border-t border-slate-800"><td className="py-1.5 text-slate-400">{s.pricing.tax_name} {s.pricing.tax_pct}%</td><td className="py-1.5 text-right font-mono text-slate-300">{money(s.pricing.tax_monthly, cur)}</td></tr>
              <tr className="border-t border-slate-700"><td className="py-1.5 font-semibold text-slate-100">Total / month</td><td className="py-1.5 text-right font-mono font-bold text-slate-100">{money(s.pricing.total_monthly, cur)}</td></tr>
            </tbody>
          </table>
        </Card>
        <Card title="Ask for more">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="What">
              <select className={INPUT} value={ask.kind} onChange={(e) => setAsk({ ...ask, kind: e.target.value })}>
                <option value="seats">More seats</option>
                <option value="product">A product we don't have</option>
                <option value="renewal">Renew the contract</option>
                <option value="other">Something else</option>
              </select>
            </Field>
            {(ask.kind === "seats" || ask.kind === "product") && (
              <>
                <Field label="Product">
                  <select className={INPUT} value={ask.product_code} onChange={(e) => setAsk({ ...ask, product_code: e.target.value })}>
                    <option value="">Choose…</option>
                    {v.catalog.filter((p) => (ask.kind === "seats" ? s.pricing.lines.some((l) => l.product === p.code) : !s.pricing.lines.some((l) => l.product === p.code))).map((p) => (
                      <option key={p.code} value={p.code}>{p.name}</option>
                    ))}
                  </select>
                </Field>
                <Field label="How many"><input className={INPUT} type="number" min={1} value={ask.quantity} onChange={(e) => setAsk({ ...ask, quantity: e.target.value })} /></Field>
              </>
            )}
          </div>
          <div className="mt-3"><Field label="Message"><textarea className={INPUT} rows={2} value={ask.message} onChange={(e) => setAsk({ ...ask, message: e.target.value })} placeholder="e.g. a new sales team joins on the 1st" /></Field></div>
          <button type="button" className={`${BTN_PRIMARY} mt-3`} disabled={busy || ((ask.kind === "seats" || ask.kind === "product") && !ask.product_code)}
                  onClick={() => run(() => licApi<TenantView>("/requests", "POST", { ...ask, quantity: Number(ask.quantity || 0), product_code: ask.product_code || null }), "Request sent to your provider")}>
            Send request
          </button>
          {v.requests.length > 0 && (
            <ul className="mt-3 space-y-1 text-xs">
              {v.requests.slice(0, 6).map((r) => (
                <li key={r.id} className="flex gap-2 text-slate-400">
                  <span className={`capitalize ${r.status === "approved" ? "text-emerald-300" : r.status === "declined" ? "text-red-300" : "text-amber-300"}`}>{r.status}</span>
                  <span className="min-w-0 flex-1 truncate">{r.kind === "seats" ? `${r.quantity} more ${r.product_name}` : r.product_name ?? r.kind}{r.response ? ` - ${r.response}` : ""}</span>
                  <span className="text-slate-600">{day(r.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Invoices"><InvoiceTable invoices={v.invoices} /></Card>
      <Card title="Recent licence changes" action={<button type="button" className={BTN} onClick={load}>↻ Refresh</button>}>
        <ul className="space-y-1 text-xs">
          {v.events.map((e) => (
            <li key={e.id} className="flex gap-2">
              <span className="w-28 shrink-0 text-slate-500">{new Date(e.at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
              <span className="text-slate-300">{e.summary}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
