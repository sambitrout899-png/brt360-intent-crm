import { useEffect, useState } from "react";
import { listBillingAccounts, listOrders } from "../api/client";
import type { BillingAccount, Order } from "../api/types";

const ORDER_STATUS_STYLES: Record<string, string> = {
  created: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  activating: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  active: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  changed: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  suspended: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  pending_cancellation: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  cancelled: "bg-red-500/15 text-red-400 border-red-500/30",
  terminated: "bg-red-500/15 text-red-400 border-red-500/30",
};

export default function OrdersPage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [billingAccounts, setBillingAccounts] = useState<BillingAccount[]>([]);
  const [tab, setTab] = useState<"orders" | "billing">("orders");

  function load() {
    listOrders().then(setOrders);
    listBillingAccounts().then(setBillingAccounts);
  }
  useEffect(load, []);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Orders & Billing</h1>
          <p className="mt-1 text-sm text-slate-400">Order-to-Activate: provisioning state, resources, and the billing ledger behind each order.</p>
        </div>
        <button onClick={load} className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400">
          Refresh
        </button>
      </div>

      <div className="flex gap-2">
        <button onClick={() => setTab("orders")} className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === "orders" ? "bg-sky-600 text-white" : "bg-slate-900/60 text-slate-400"}`}>
          Orders
        </button>
        <button onClick={() => setTab("billing")} className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === "billing" ? "bg-sky-600 text-white" : "bg-slate-900/60 text-slate-400"}`}>
          Billing Accounts
        </button>
      </div>

      {tab === "orders" && (
        <div className="space-y-3">
          {orders.map((o) => (
            <div key={o.id} className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-xs text-slate-500">#{o.id}</span>
                <span className="font-bold text-slate-100">{o.party_name}</span>
                <span className="text-xs text-slate-500">{o.service_account_name}</span>
                <span className="text-slate-300">₹{o.monthly_price.toLocaleString()}/mo</span>
                <span className={`rounded-md border px-2 py-0.5 text-xs font-bold ${ORDER_STATUS_STYLES[o.status]}`}>{o.status}</span>
                {(o.status === "created" || o.status === "activating") && (
                  <button
                    onClick={() => onRequestIntent(`Revise this order. order_id: ${o.id}, new_price: `)}
                    className="ml-auto rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20"
                  >
                    Revise
                  </button>
                )}
                {(o.status === "active" || o.status === "changed") && (
                  <div className="ml-auto flex gap-2">
                    <button
                      onClick={() => onRequestIntent(`Upgrade this service. order_id: ${o.id}, new_price: `)}
                      className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs font-bold text-sky-400 hover:bg-sky-500/20"
                    >
                      Change
                    </button>
                    <button
                      onClick={() => onRequestIntent(`Suspend this service. order_id: ${o.id}`)}
                      className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-xs font-bold text-amber-400 hover:bg-amber-500/20"
                    >
                      Suspend
                    </button>
                    <button
                      onClick={() => onRequestIntent(`Cancel this service. order_id: ${o.id}`)}
                      className="rounded-md border border-red-500/40 bg-red-500/10 px-2.5 py-1 text-xs font-bold text-red-400 hover:bg-red-500/20"
                    >
                      Cancel
                    </button>
                  </div>
                )}
                {o.status === "pending_cancellation" && (
                  <button
                    onClick={() => onRequestIntent(`Cancel this service. order_id: ${o.id}, decision: retain`)}
                    className="ml-auto rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-xs font-bold text-emerald-400 hover:bg-emerald-500/20"
                  >
                    Retain Customer
                  </button>
                )}
              </div>
              {o.service_orders.map((so) => (
                <div key={so.id} className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-slate-950 px-3 py-2 text-xs text-slate-400">
                  <span className="font-semibold text-slate-300">{so.site_label}</span>
                  <span>#{so.id}</span>
                  <span>· {so.status} ·</span>
                  <span>{so.resources.map((r) => `${r.resource_type}:${r.identifier}(${r.status})`).join(", ") || "no resources"}</span>
                  {so.status === "pending" && (
                    <button
                      onClick={() => onRequestIntent(`Provision this site. order_id: ${o.id}, site_label: ${so.site_label}`)}
                      className="ml-auto rounded-md border border-sky-500/40 bg-sky-500/10 px-2 py-0.5 text-xs font-bold text-sky-400 hover:bg-sky-500/20"
                    >
                      Provision
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
          {orders.length === 0 && <p className="text-sm text-slate-500">No orders yet.</p>}
        </div>
      )}

      {tab === "billing" && (
        <div className="space-y-3">
          {billingAccounts.map((b) => (
            <div key={b.id} className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-bold text-slate-100">{b.party_name}</span>
                <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{b.status}</span>
                <span className="text-xs text-slate-500">{b.rate_plan}</span>
                <span className="ml-auto text-slate-200">Balance: ₹{b.balance.toLocaleString()}</span>
              </div>
              {b.adjustments.length > 0 && (
                <div className="mt-3 space-y-1">
                  {b.adjustments.map((a) => (
                    <div key={a.id} className="flex items-center gap-2 rounded-lg bg-slate-950 px-3 py-1.5 text-xs text-slate-400">
                      <span className={a.amount >= 0 ? "text-amber-400" : "text-emerald-400"}>₹{a.amount.toLocaleString()}</span>
                      <span>{a.reason}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
          {billingAccounts.length === 0 && <p className="text-sm text-slate-500">No billing accounts yet.</p>}
        </div>
      )}
    </div>
  );
}
