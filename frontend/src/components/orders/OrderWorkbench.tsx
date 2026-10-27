import { useCallback, useEffect, useState } from "react";
import { omApi } from "../../api/client";
import { useIntentRefresh, useWorkspace } from "../../workspace";
import { BTN, BTN_DANGER, BTN_GO, BTN_PRIMARY, BTN_WARN, CARD, Chip, Empty, Panel, Tabs } from "../../pages/fieldservice/ui";
import { fmtDate, fmtWhen, money } from "../../pages/fieldservice/style";

export interface OmLineView {
  id: number;
  line_number: number;
  product_name: string;
  kind: string;
  price_type: string;
  quantity: number;
  list_price: number;
  discount_pct: number;
  discount_amount: number;
  discount_reason: string;
  net_price: number;
  extended: number;
  action_code: string;
  status: string;
  requested_date: string | null;
  atp_status: string;
  atp_date: string | null;
  shipped_qty: number;
  trackable: boolean;
  asset_id: number | null;
  integration_id: string;
  main: boolean;
  commitment_months: number;
}
export interface OmTotals {
  recurring: number;
  one_time: number;
  usage: number;
  line_discounts: number;
  freight: number;
  tax: number;
  tax_recurring: number;
  due_now: number;
  monthly_with_tax: number;
  lines: number;
}
interface Recommendation {
  product: string;
  kind: string;
  because: string;
  message: string;
}
interface OrderView {
  order_id: number;
  order_number: string;
  revision: number;
  om_status: string;
  status: string;
  order_type: string;
  party_name: string | null;
  quote_id: number | null;
  requested_date: string | null;
  ship_to_address: string;
  shipping_method: string | null;
  carrier: string;
  payment_terms: string;
  hold: boolean;
  hold_reason: string;
  cancel_reason: string;
  submitted_at: string | null;
  issues: { level: string; text: string }[];
  source_order_id: number | null;
  tax_jurisdiction: string;
  lines: OmLineView[];
  totals: OmTotals;
  payments: { id: number; method: string; amount: number; status: string; card: string; po_number: string; created_at: string }[];
  credit_check: { result: string; reason: string } | null;
  shipments: { shipment_number: string; carrier: string; tracking_number: string; status: string; shipped_at: string; delivered_at: string | null }[];
  revisions: { revision: number; kind: string; reason: string; created_at: string }[];
  history: { line_id: number; revision: number; field: string; old: string; new: string; at: string }[];
  recommendations: Recommendation[];
  next_steps: string[];
}
interface QuoteView {
  quote_id: number;
  quote_number: string;
  revision: number;
  status: string;
  party_name: string | null;
  opportunity_id: number;
  effective_through: string;
  expired: boolean;
  verified_at: string | null;
  issues: { level: string; text: string }[];
  promotion: string | null;
  opportunity_updated_at: string | null;
  lines: OmLineView[];
  totals: OmTotals;
  recommendations: Recommendation[];
  agreements: { agreement_number: string; status: string; end_date: string }[];
  history: { line_id: number; field: string; old: string; new: string; at: string }[];
}

const OM_STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: "Being entered", cls: "bg-slate-500/15 text-slate-300" },
  awaiting_approval: { label: "Awaiting approval", cls: "bg-amber-500/15 text-amber-300" },
  booked: { label: "Booked", cls: "bg-sky-500/15 text-sky-300" },
  awaiting_shipping: { label: "Awaiting shipping", cls: "bg-indigo-500/15 text-indigo-300" },
  partially_shipped: { label: "Partly shipped", cls: "bg-indigo-500/15 text-indigo-300" },
  shipped: { label: "Shipped", cls: "bg-violet-500/15 text-violet-300" },
  delivered: { label: "Delivered", cls: "bg-teal-500/15 text-teal-300" },
  fulfilled: { label: "Fulfilled", cls: "bg-emerald-500/15 text-emerald-300" },
  cancelled: { label: "Cancelled", cls: "bg-red-500/15 text-red-300" },
};

export function OmStatus({ status, hold }: { status: string; hold?: boolean }) {
  const s = OM_STATUS[status] ?? { label: status, cls: "bg-slate-500/15 text-slate-300" };
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${s.cls}`}>{s.label}</span>
      {hold && <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-red-300">On hold</span>}
    </span>
  );
}

const ATP: Record<string, string> = { available: "text-emerald-300", partial: "text-amber-300", backorder: "text-red-300", not_tracked: "text-slate-500" };

function Lines({ lines }: { lines: OmLineView[] }) {
  return (
    <div className="thin-scroll overflow-x-auto">
      <table className="w-full min-w-[42rem] text-left text-xs">
        <thead className="text-[11px] uppercase tracking-wider text-slate-500">
          <tr>
            {["#", "Product", "Qty", "Price", "Discount", "Net", "Total", "Action", "Stock", "Status"].map((h) => (
              <th key={h} className="whitespace-nowrap px-2 py-2 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/70">
          {lines.map((l) => (
            <tr key={l.id} className={l.action_code === "Delete" ? "text-slate-600 line-through" : "text-slate-300"}>
              <td className="px-2 py-2 text-slate-500">{l.line_number}</td>
              <td className="px-2 py-2">
                <span className="font-semibold text-slate-100">{l.product_name}</span>
                <span className="ml-1 text-[10px] text-slate-500">
                  {l.kind === "write_in" ? "write-in" : l.kind}
                  {l.main ? " · main" : ""}
                  {l.commitment_months ? ` · ${l.commitment_months}-mo commitment` : ""}
                </span>
              </td>
              <td className="px-2 py-2">{l.quantity}</td>
              <td className="whitespace-nowrap px-2 py-2">
                {money(l.list_price)}
                <span className="text-slate-500">{l.price_type === "recurring" ? "/mo" : l.price_type === "usage" ? "/use" : ""}</span>
              </td>
              <td className="whitespace-nowrap px-2 py-2" title={l.discount_reason}>
                {l.discount_pct ? `${l.discount_pct}%` : l.discount_amount ? money(l.discount_amount) : "—"}
              </td>
              <td className="whitespace-nowrap px-2 py-2">{money(l.net_price)}</td>
              <td className="whitespace-nowrap px-2 py-2 font-semibold text-slate-100">{money(l.extended)}</td>
              <td className="px-2 py-2">
                <Chip tone={l.action_code === "Add" ? "emerald" : l.action_code === "Delete" ? "red" : l.action_code === "Update" ? "amber" : "slate"}>{l.action_code}</Chip>
              </td>
              <td className={`whitespace-nowrap px-2 py-2 ${ATP[l.atp_status] ?? "text-slate-500"}`}>
                {l.atp_status ? `${l.atp_status.replace("_", " ")}${l.atp_date && l.atp_status !== "not_tracked" ? ` · ${fmtDate(l.atp_date)}` : ""}` : "—"}
              </td>
              <td className="whitespace-nowrap px-2 py-2">
                {l.status}
                {l.shipped_qty > 0 && l.shipped_qty < l.quantity ? ` (${l.shipped_qty} shipped)` : ""}
                {l.asset_id ? " · asset" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Totals({ t }: { t: OmTotals }) {
  const cells: [string, number, string?][] = [
    ["Monthly", t.recurring],
    ["One-time", t.one_time],
    ["Freight", t.freight],
    ["Tax now", t.tax],
    ["Due now", t.due_now, "text-sky-300"],
    ["Monthly incl. tax", t.monthly_with_tax, "text-emerald-300"],
  ];
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {cells.map(([label, value, tone]) => (
        <div key={label} className="rounded-xl border border-slate-800 bg-slate-900/40 px-3 py-2">
          <p className="truncate text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
          <p className={`mt-0.5 truncate text-sm font-bold ${tone ?? "text-slate-100"}`}>{money(value)}</p>
        </div>
      ))}
    </div>
  );
}

function Issues({ issues }: { issues: { level: string; text: string }[] }) {
  if (!issues.length) return null;
  return (
    <div className="space-y-1">
      {issues.map((i, n) => (
        <p key={n} className={`rounded-lg px-3 py-1.5 text-xs ${i.level === "error" ? "bg-red-500/10 text-red-200" : "bg-amber-500/10 text-amber-200"}`}>
          {i.level === "error" ? "⛔" : "⚠"} {i.text}
        </p>
      ))}
    </div>
  );
}

function Recommendations({ items, add }: { items: Recommendation[]; add: (product: string) => void }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-sky-500/20 bg-sky-500/5 px-3 py-2">
      <span className="text-xs font-semibold text-sky-300">Suggested:</span>
      {items.map((r) => (
        <button key={r.product} type="button" title={r.message} onClick={() => add(r.product)} className="rounded-full border border-sky-500/30 px-2.5 py-0.5 text-xs text-sky-200 hover:bg-sky-500/10">
          + {r.product} <span className="text-sky-400/70">({r.kind === "up_sell" ? "up-sell" : "cross-sell"})</span>
        </button>
      ))}
    </div>
  );
}

/** One sales order: lines, money, shipping, payment, shipments and history - each action runs its intent. */
export function OrderWorkbench({ orderId }: { orderId: number }) {
  const [o, setO] = useState<OrderView | null>(null);
  const [tab, setTab] = useState<"lines" | "delivery" | "payment" | "history">("lines");
  const { runIntent, canRun } = useWorkspace();
  const load = useCallback(() => {
    omApi<OrderView>(`/orders/${orderId}`).then(setO).catch(() => setO(null));
  }, [orderId]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  if (!o) return <Empty>Loading the order…</Empty>;
  const go = (code: string, extra: Record<string, unknown> = {}) => runIntent(code, { order_id: o.order_id, ...extra });
  const entering = o.om_status === "pending";
  const inFlight = ["booked", "awaiting_shipping", "awaiting_approval"].includes(o.om_status);
  const shippable = ["booked", "awaiting_shipping", "partially_shipped"].includes(o.om_status);

  return (
    <div className="space-y-4">
      <div className={`${CARD} p-4`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-bold text-slate-100">{o.order_number}</span>
          {o.revision > 1 && <Chip tone="amber">rev {o.revision}</Chip>}
          <OmStatus status={o.om_status} hold={o.hold} />
          <span className="text-xs text-slate-500">service: {o.status.replace("_", " ")}</span>
          {o.source_order_id && <Chip tone="violet">follow-on of #{o.source_order_id}</Chip>}
        </div>
        <p className="mt-1 text-sm text-slate-400">
          {o.party_name ?? "—"}
          {o.requested_date && ` · wanted by ${fmtDate(o.requested_date)}`}
          {o.submitted_at && ` · submitted ${fmtWhen(o.submitted_at)}`}
        </p>
        {o.hold && <p className="mt-2 text-xs text-red-300">On hold: {o.hold_reason}</p>}
        {o.cancel_reason && <p className="mt-2 text-xs text-red-300">Cancelled: {o.cancel_reason}</p>}
        {o.next_steps.length > 0 && (
          <p className="mt-2 text-xs text-sky-300">
            Next: {o.next_steps.join(" → ")}
          </p>
        )}
      </div>

      <Totals t={o.totals} />
      <Issues issues={o.issues} />

      <div className="flex flex-wrap gap-2">
        {entering && (
          <>
            {canRun("INT-51") ? <button type="button" className={BTN} onClick={() => go("INT-51")}>+ Line</button> : null}
            {canRun("INT-52") ? <button type="button" className={BTN} onClick={() => go("INT-52")}>Discount a line</button> : null}
            {canRun("INT-69") ? <button type="button" className={BTN} onClick={() => go("INT-69")}>Promotion</button> : null}
            {canRun("INT-54") ? <button type="button" className={BTN} onClick={() => go("INT-54")}>Availability</button> : null}
            {canRun("INT-55") ? <button type="button" className={BTN} onClick={() => go("INT-55", { address: o.ship_to_address })}>Shipping</button> : null}
            {canRun("INT-56") ? <button type="button" className={BTN} onClick={() => go("INT-56")}>Tax</button> : null}
            {canRun("INT-57") ? <button type="button" className={BTN} onClick={() => go("INT-57")}>Payment</button> : null}
            {canRun("INT-58") ? <button type="button" className={BTN} onClick={() => go("INT-58")}>Credit check</button> : null}
            {canRun("INT-53") ? <button type="button" className={BTN} onClick={() => go("INT-53")}>Verify</button> : null}
            {canRun("INT-59") ? <button type="button" className={BTN_PRIMARY} onClick={() => go("INT-59")}>Submit</button> : null}
          </>
        )}
        {o.om_status === "awaiting_approval" && (canRun("INT-59") ? <button type="button" className={BTN_PRIMARY} onClick={() => go("INT-59", { decision: "approve" })}>Approve</button> : null)}
        {(inFlight || o.om_status === "partially_shipped") && (canRun("INT-62") ? <button type="button" className={BTN} onClick={() => go("INT-62")}>Supplemental order</button> : null)}
        {shippable && (canRun("INT-64") ? <button type="button" className={BTN_GO} onClick={() => go("INT-64", { ship_action: "ship" })}>Ship</button> : null)}
        {["shipped", "partially_shipped"].includes(o.om_status) && (canRun("INT-64") ? <button type="button" className={BTN_GO} onClick={() => go("INT-64", { ship_action: "deliver" })}>Confirm delivery</button> : null)}
        {["delivered", "fulfilled", "shipped", "partially_shipped"].includes(o.om_status) && (
          <>
            {canRun("INT-63") ? <button type="button" className={BTN} onClick={() => go("INT-63")}>Follow-on order</button> : null}
            {canRun("INT-67") ? <button type="button" className={BTN} onClick={() => go("INT-67")}>Return items</button> : null}
          </>
        )}
        {o.om_status !== "cancelled" && (canRun("INT-68") ? <button type="button" className={BTN} onClick={() => go("INT-68")}>Activity plan</button> : null)}
        {!["cancelled", "fulfilled", "delivered", "shipped"].includes(o.om_status) &&
          (o.hold ? (
            (canRun("INT-60") ? <button type="button" className={BTN_WARN} onClick={() => go("INT-60", { hold_action: "release" })}>Release hold</button> : null)
          ) : (
            (canRun("INT-60") ? <button type="button" className={BTN_WARN} onClick={() => go("INT-60", { hold_action: "hold" })}>Hold</button> : null)
          ))}
        {(entering || inFlight) && (canRun("INT-61") ? <button type="button" className={BTN_DANGER} onClick={() => go("INT-61")}>Cancel order</button> : null)}
        {o.status === "suspended" && (canRun("INT-70") ? <button type="button" className={BTN_GO} onClick={() => go("INT-70")}>Resume service</button> : null)}
      </div>

      <Recommendations items={entering ? o.recommendations : []} add={(product) => go("INT-51", { product })} />

      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "lines", label: "Lines", count: o.lines.length },
          { id: "delivery", label: "Shipping & shipments", count: o.shipments.length },
          { id: "payment", label: "Payment & credit", count: o.payments.length },
          { id: "history", label: "Revisions & history", count: o.revisions.length },
        ]}
      />
      {tab === "lines" && <Lines lines={o.lines} />}
      {tab === "delivery" && (
        <div className="grid gap-3 md:grid-cols-2">
          <Panel title="Ship to">
            <p className="text-sm text-slate-200">{o.ship_to_address || "No address yet"}</p>
            <p className="mt-1 text-xs text-slate-400">
              {o.shipping_method ? `${o.shipping_method}${o.carrier ? ` with ${o.carrier}` : ""} · freight ${money(o.totals.freight)}` : "No shipping method"}
            </p>
            <p className="mt-1 text-xs text-slate-500">Tax: {o.tax_jurisdiction || "not calculated"}</p>
          </Panel>
          <Panel title="Shipments">
            {o.shipments.length === 0 ? (
              <p className="text-sm text-slate-500">Nothing shipped yet.</p>
            ) : (
              o.shipments.map((s) => (
                <div key={s.shipment_number} className="flex items-center gap-2 py-1 text-xs">
                  <span className="font-mono text-slate-300">{s.shipment_number}</span>
                  <span className="text-slate-400">
                    {s.carrier} · {s.tracking_number}
                  </span>
                  <span className="flex-1" />
                  <Chip tone={s.status === "delivered" ? "emerald" : "sky"}>{s.status}</Chip>
                </div>
              ))
            )}
          </Panel>
        </div>
      )}
      {tab === "payment" && (
        <div className="grid gap-3 md:grid-cols-2">
          <Panel title="Payments">
            {o.payments.length === 0 ? (
              <p className="text-sm text-slate-500">No payment yet{o.payment_terms ? ` - terms ${o.payment_terms}` : ""}.</p>
            ) : (
              o.payments.map((p) => (
                <div key={p.id} className="flex items-center gap-2 py-1 text-xs">
                  <span className="text-slate-200">{p.method.replace("_", " ")}</span>
                  <span className="text-slate-500">{p.card || p.po_number}</span>
                  <span className="flex-1" />
                  <span className="font-semibold text-slate-100">{money(p.amount)}</span>
                  <Chip tone={p.status === "authorized" || p.status === "captured" ? "emerald" : p.status === "declined" ? "red" : "slate"}>{p.status}</Chip>
                </div>
              ))
            )}
          </Panel>
          <Panel title="Credit">
            {o.credit_check ? (
              <p className={`text-sm ${o.credit_check.result === "approved" ? "text-emerald-300" : o.credit_check.result === "declined" ? "text-red-300" : "text-amber-300"}`}>
                {o.credit_check.result}: {o.credit_check.reason}
              </p>
            ) : (
              <p className="text-sm text-slate-500">Not checked.</p>
            )}
          </Panel>
        </div>
      )}
      {tab === "history" && (
        <div className="grid gap-3 md:grid-cols-2">
          <Panel title="Revisions">
            {o.revisions.length === 0 ? (
              <p className="text-sm text-slate-500">The original order.</p>
            ) : (
              o.revisions.map((r, i) => (
                <p key={i} className="py-1 text-xs text-slate-300">
                  rev {r.revision} · {r.kind.replace("_", " ")} {r.reason && `- ${r.reason}`} <span className="text-slate-500">· {fmtWhen(r.created_at)}</span>
                </p>
              ))
            )}
          </Panel>
          <Panel title="Line changes">
            {o.history.length === 0 ? (
              <p className="text-sm text-slate-500">No changes.</p>
            ) : (
              o.history.map((h, i) => (
                <p key={i} className="py-0.5 text-xs text-slate-400">
                  line {o.lines.find((l) => l.id === h.line_id)?.line_number ?? "?"} · {h.field}: <span className="text-slate-500">{h.old || "—"}</span> → <span className="text-slate-200">{h.new}</span>
                </p>
              ))
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}

/** One quote: lines and money, with the pre-order steps - each runs its intent. */
export function QuoteWorkbench({ quoteId }: { quoteId: number }) {
  const [q, setQ] = useState<QuoteView | null>(null);
  const { runIntent, canRun } = useWorkspace();
  const load = useCallback(() => {
    omApi<QuoteView>(`/quotes/${quoteId}`).then(setQ).catch(() => setQ(null));
  }, [quoteId]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  if (!q) return <Empty>Loading the quote…</Empty>;
  const go = (code: string, extra: Record<string, unknown> = {}) => runIntent(code, { quote_id: q.quote_id, ...extra });
  const draft = q.status === "draft";
  return (
    <div className="space-y-4">
      <div className={`${CARD} p-4`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-bold text-slate-100">{q.quote_number}</span>
          <Chip>rev {q.revision}</Chip>
          <Chip tone={q.status === "approved" ? "emerald" : q.status === "draft" ? "slate" : "amber"}>{q.status.replace("_", " ")}</Chip>
          {q.promotion && <Chip tone="violet">{q.promotion}</Chip>}
          <span className="flex-1" />
          <span className={`text-xs ${q.expired ? "text-red-300" : "text-slate-400"}`}>{q.expired ? "expired" : "valid until"} {fmtDate(q.effective_through)}</span>
        </div>
        <p className="mt-1 text-sm text-slate-400">
          {q.party_name ?? "—"}
          {q.opportunity_updated_at && ` · forecast updated ${fmtWhen(q.opportunity_updated_at)}`}
        </p>
        {q.agreements.length > 0 && (
          <p className="mt-1 text-xs text-emerald-300">Agreement {q.agreements.map((a) => `${a.agreement_number} (to ${fmtDate(a.end_date)})`).join(", ")}</p>
        )}
      </div>
      <Totals t={q.totals} />
      <Issues issues={q.issues} />
      <div className="flex flex-wrap gap-2">
        {draft && (
          <>
            {canRun("INT-51") ? <button type="button" className={BTN} onClick={() => go("INT-51")}>+ Line</button> : null}
            {canRun("INT-52") ? <button type="button" className={BTN} onClick={() => go("INT-52")}>Discount a line</button> : null}
            {canRun("INT-69") ? <button type="button" className={BTN} onClick={() => go("INT-69")}>Promotion</button> : null}
          </>
        )}
        {canRun("INT-54") ? <button type="button" className={BTN} onClick={() => go("INT-54")}>Availability</button> : null}
        {canRun("INT-56") ? <button type="button" className={BTN} onClick={() => go("INT-56")}>Tax</button> : null}
        {canRun("INT-53") ? <button type="button" className={BTN} onClick={() => go("INT-53")}>Verify</button> : null}
        {canRun("INT-66") ? <button type="button" className={BTN} onClick={() => go("INT-66")}>Update opportunity</button> : null}
        {canRun("INT-65") ? <button type="button" className={BTN_GO} onClick={() => go("INT-65")}>Agreement</button> : null}
      </div>
      <Recommendations items={draft ? q.recommendations : []} add={(product) => go("INT-51", { product })} />
      <Lines lines={q.lines} />
    </div>
  );
}
