import { useEffect, useMemo, useState } from "react";
import { listBillingAccounts, listOrders, omApi } from "../api/client";
import { OmStatus, OrderWorkbench } from "../components/orders/OrderWorkbench";
import { useRecordButtons } from "../components/useRecordButtons";
import { Drawer } from "./fieldservice/ui";
import BillingWorkbench from "./comms/BillingWorkbench";
import type { BillingAccount, Order } from "../api/types";
import { useIntentRefresh, useWorkspace } from "../workspace";

const ORDER_STATUS_STYLES: Record<string, string> = {
  created: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  activating: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  active: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  changed: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  suspended: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  pending_cancellation: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  cancelled: "bg-red-500/15 text-red-300 ring-red-500/30",
  terminated: "bg-red-500/15 text-red-300 ring-red-500/30",
  pending_approval: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  rejected: "bg-red-500/15 text-red-300 ring-red-500/30",
};

const WORK_ORDER_LABELS: Record<string, string> = {
  installation: "WFM1: Installation",
  connectivity_test: "WFM2: Testing & ESAN",
  acceptance_letter: "WFM3: ESAN signed / acceptance letter",
};

const WORK_ORDER_CLOSE_ACTIONS: Record<string, (id: number) => string> = {
  installation: (id) => `Close Work Order 1 (Installation). work_order_id: ${id}, technical_parameters: `,
  connectivity_test: (id) => `Close Work Order 2 (Connectivity Test & Acceptance). work_order_id: ${id}, signature: `,
  acceptance_letter: (id) => `Close Work Order 3 (Acceptance Letter). work_order_id: ${id}`,
};

const CLOSED = new Set(["cancelled", "terminated", "rejected"]);
type StatusFilter = "all" | "progress" | "active" | "closed";
type OmInfo = { order_number: string; om_status: string; hold: boolean; lines: number; due_now: number; revision: number };

const groupOf = (o: Order): StatusFilter => (o.status === "active" ? "active" : CLOSED.has(o.status) ? "closed" : "progress");
const inr = (n: number) => `₹${n.toLocaleString()}`;
const nice = (s: string) => s.replace(/_/g, " ");
const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter((w) => /[A-Za-z0-9]/.test(w))
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("") || "?";

function StatusPill({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ring-1 ring-inset ${ORDER_STATUS_STYLES[status] ?? "bg-slate-500/15 text-slate-300 ring-slate-500/30"}`}>
      {nice(status)}
    </span>
  );
}

function Kpi({ label, value, sub, tone = "text-slate-100" }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="min-w-0 bg-[#0b1120] px-5 py-4 sm:px-6 sm:py-5">
      <p className="truncate text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">{label}</p>
      <p className={`mt-2 truncate text-2xl font-semibold leading-none ${tone}`}>{value}</p>
      {sub && <p className="mt-1.5 truncate text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h4 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">{title}</h4>
      {children}
    </div>
  );
}

export default function OrdersPage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [billingAccounts, setBillingAccounts] = useState<BillingAccount[]>([]);
  const [tab, setTab] = useState<"orders" | "billing" | "bills">("orders");
  const [om, setOm] = useState<Record<number, OmInfo>>({});
  const [manage, setManage] = useState<number | null>(null);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  function load() {
    listOrders().then(setOrders);
    listBillingAccounts().then(setBillingAccounts);
    omApi<Record<number, OmInfo>>("/orders").then(setOm).catch(() => setOm({}));
  }
  useEffect(load, []);
  useIntentRefresh(load);
  const { openRecord, runIntent } = useWorkspace();
  const buttons = useRecordButtons(orders.map((o) => `order:${o.id}`));

  const toggle = (id: number) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const counts = useMemo(() => {
    const c = { all: orders.length, progress: 0, active: 0, closed: 0 };
    for (const o of orders) c[groupOf(o)] += 1;
    return c;
  }, [orders]);
  const mrr = orders.filter((o) => o.status === "active").reduce((s, o) => s + o.monthly_price, 0);
  const awaiting = orders.filter((o) => o.discount_approval?.current_tier || o.order_control_chain?.current_tier).length;
  const balance = billingAccounts.reduce((s, b) => s + b.balance, 0);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return orders.filter(
      (o) =>
        (status === "all" || groupOf(o) === status) &&
        (!q || `${o.id} ${om[o.id]?.order_number ?? ""} ${o.party_name} ${o.service_account_name} ${(o.configuration ?? []).map((c) => c.offering_name).join(" ")}`.toLowerCase().includes(q))
    );
  }, [orders, om, query, status]);

  const TABS = [
    ["orders", "Orders", orders.length],
    ["billing", "Billing Accounts", billingAccounts.length],
    ["bills", "Bills, Collections & Fraud", null],
  ] as const;

  return (
    <div className="page space-y-8">
      {/* ---------- title ---------- */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[1.75rem] font-semibold leading-tight text-slate-50">Orders & Billing</h1>
          <p className="mt-2 text-[15px] text-slate-400">Order-to-Activate: provisioning state, resources, and the billing ledger behind each order.</p>
        </div>
        <button onClick={load} className="h-10 rounded-lg border border-slate-700 bg-slate-900/60 px-4 text-sm font-semibold text-slate-200 hover:border-sky-500/60 hover:text-sky-300">
          ↻ Refresh
        </button>
      </div>

      {/* ---------- the numbers ---------- */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-slate-800 bg-slate-800 sm:grid-cols-3 xl:grid-cols-5">
        <Kpi label="Orders" value={String(orders.length)} sub={`${counts.closed} closed`} />
        <Kpi label="In progress" value={String(counts.progress)} sub="being fulfilled" tone="text-sky-300" />
        <Kpi label="Live" value={String(counts.active)} sub="active services" tone="text-emerald-300" />
        <Kpi label="Monthly recurring" value={inr(mrr)} sub="from live orders" tone="text-emerald-300" />
        <Kpi label="Awaiting approval" value={String(awaiting)} sub={`billing balance ${inr(balance)}`} tone={awaiting ? "text-amber-300" : "text-slate-100"} />
      </div>

      {/* ---------- tabs ---------- */}
      <nav className="flex gap-1 overflow-x-auto border-b border-slate-800">
        {TABS.map(([k, label, n]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`whitespace-nowrap border-b-2 px-4 py-3 text-sm font-semibold transition sm:px-5 ${tab === k ? "border-sky-400 text-slate-50" : "border-transparent text-slate-400 hover:text-slate-200"}`}
          >
            {label}
            {n !== null && <span className="ml-2 rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{n}</span>}
          </button>
        ))}
      </nav>

      {tab === "bills" && <BillingWorkbench />}

      {/* ---------- orders ---------- */}
      {tab === "orders" && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-3">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by customer, order number or product…"
              className="h-10 w-full rounded-lg border border-slate-700 bg-slate-950/80 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-sky-500 sm:max-w-sm"
            />
            <div className="flex gap-0.5 rounded-lg bg-slate-900/80 p-0.5 ring-1 ring-slate-800">
              {(
                [
                  ["all", "All"],
                  ["progress", "In progress"],
                  ["active", "Live"],
                  ["closed", "Closed"],
                ] as const
              ).map(([k, l]) => (
                <button
                  key={k}
                  onClick={() => setStatus(k)}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${status === k ? "bg-sky-600 text-white shadow" : "text-slate-400 hover:text-slate-200"}`}
                >
                  {l} <span className={status === k ? "text-sky-100" : "text-slate-600"}>{counts[k]}</span>
                </button>
              ))}
            </div>
            <span className="ml-auto flex gap-3 text-xs">
              <button onClick={() => setOpen(new Set(shown.map((o) => o.id)))} className="text-slate-400 hover:text-slate-200">Expand all</button>
              <span className="text-slate-700">|</span>
              <button onClick={() => setOpen(new Set())} className="text-slate-400 hover:text-slate-200">Collapse all</button>
            </span>
          </div>

          {shown.map((o) => {
            const info = om[o.id];
            const milestones = o.milestones ?? [];
            const latest = milestones[milestones.length - 1];
            const isOpen = open.has(o.id);
            const config = o.configuration ?? [];
            const wos = o.fulfillment_work_orders;
            const woDone = wos.filter((w) => w.status === "complete").length;
            const actions = buttons[`order:${o.id}`] ?? [];
            const editable = o.status !== "active" && o.status !== "cancelled" && o.status !== "terminated";
            return (
              <article key={o.id} className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/40 transition hover:border-slate-700">
                {/* header: who, what, how much, where it stands */}
                <div className="flex flex-wrap items-start gap-4 p-5 sm:p-6">
                  <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-sm font-bold ${o.status === "active" ? "bg-emerald-500/15 text-emerald-300" : "bg-sky-500/15 text-sky-300"}`}>
                    {initials(o.party_name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <h3 className="truncate text-lg font-semibold text-slate-50">{o.party_name}</h3>
                      {o.segment && <span className="rounded-md bg-slate-800 px-2 py-0.5 text-[10px] font-bold text-slate-400">{o.segment}</span>}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-slate-500">
                      <button onClick={() => openRecord("order", o.id)} className="font-mono text-sky-400 hover:underline" title="Open the order record">
                        #{o.id}
                      </button>
                      {info && (
                        <span className="font-mono text-slate-400">
                          {info.order_number}
                          {info.revision > 1 ? ` rev ${info.revision}` : ""}
                        </span>
                      )}
                      <span className="text-slate-700">·</span>
                      <span>{o.service_account_name}</span>
                      {config.length > 0 && (
                        <>
                          <span className="text-slate-700">·</span>
                          <span className="text-slate-300">{config.map((c) => c.offering_name).join(", ")}</span>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-2">
                    <p className="text-xl font-semibold text-slate-50">
                      {inr(o.monthly_price)}
                      <span className="text-sm font-normal text-slate-500">/mo</span>
                    </p>
                    <div className="flex flex-wrap items-center justify-end gap-1.5">
                      <StatusPill status={o.status} />
                      {info && <OmStatus status={info.om_status} hold={info.hold} />}
                    </div>
                    {info && info.due_now > 0 && <p className="text-xs text-slate-500">{inr(info.due_now)} one-time</p>}
                  </div>
                </div>

                {/* approvals waiting */}
                {(o.discount_approval?.current_tier || o.order_control_chain) && (
                  <div className="flex flex-wrap gap-x-6 gap-y-1 px-5 pb-4 text-xs sm:px-6 sm:pl-[84px]">
                    {o.discount_approval && o.discount_approval.current_tier && <span className="text-amber-300">⏳ Discount awaiting {o.discount_approval.current_tier}</span>}
                    {o.order_control_chain && (
                      <span className={o.order_control_chain.current_tier ? "text-amber-300" : "text-slate-500"}>
                        Order Control Unit: {o.order_control_chain.current_tier ? `awaiting ${o.order_control_chain.current_tier}` : o.order_control_chain.status}
                      </span>
                    )}
                  </div>
                )}

                {/* progress: the latest step, every reached step as a segment */}
                {milestones.length > 0 && (
                  <div className="px-5 pb-5 sm:px-6 sm:pl-[84px]">
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-slate-400">
                        Latest: <span className="font-semibold text-emerald-300">✓ {latest?.label}</span>
                      </span>
                      <span className="text-slate-500">{milestones.length} steps done</span>
                    </div>
                    <div className="mt-2 flex gap-1">
                      {milestones.map((m, i) => (
                        <span
                          key={`${m.code}-${i}`}
                          title={`${m.label} - ${new Date(m.reached_at).toLocaleString()}${m.detail ? ` - ${m.detail}` : ""}`}
                          className="h-1.5 flex-1 rounded-full bg-emerald-500/70 transition hover:bg-emerald-300"
                        />
                      ))}
                    </div>
                  </div>
                )}

                {/* actions */}
                <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 bg-slate-950/30 px-5 py-3 sm:px-6">
                  <button onClick={() => setManage(o.id)} className="rounded-lg bg-sky-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow shadow-sky-950 hover:bg-sky-500">
                    Manage order{info && info.lines > 1 ? ` (${info.lines} lines)` : ""}
                  </button>
                  {actions.map((b) => {
                    const [verb, result] = b.label.split(/\s*(?:→|->)\s*/);
                    return (
                      <button
                        key={`${b.intent}-${b.label}`}
                        type="button"
                        onClick={() => runIntent(b.intent, b.entities)}
                        title={result ? `${verb} - the order becomes ${result}` : b.label}
                        className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-sky-500/60 hover:text-sky-300"
                      >
                        {verb}
                        {result && <span className="ml-1 font-normal text-slate-500">→ {result}</span>}
                      </button>
                    );
                  })}
                  <button onClick={() => toggle(o.id)} className="ml-auto rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-400 hover:bg-slate-800/60 hover:text-slate-200" aria-expanded={isOpen}>
                    {isOpen ? "Hide details ▴" : `Details ▾`}
                    {!isOpen && wos.length > 0 && <span className="ml-1.5 text-slate-500">· {woDone}/{wos.length} work orders done</span>}
                  </button>
                </div>

                {/* details */}
                {isOpen && (
                  <div className="space-y-6 border-t border-slate-800 p-5 sm:p-6">
                    {config.length > 0 && (
                      <Section title="Configuration">
                        <div className="space-y-3">
                          {config.map((c) => {
                            const pricing = Object.entries(c.pricing_attributes);
                            const seen = new Set(pricing.map(([k, v]) => `${k}=${v}`));
                            const technical = Object.entries(c.technical_attributes).filter(([k, v]) => !seen.has(`${k}=${v}`));
                            return (
                              <div key={c.order_item_id} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                                <div className="flex flex-wrap items-center gap-3">
                                  <p className="text-sm font-semibold text-slate-100">{c.offering_name}</p>
                                  {c.status && <span className="text-xs text-emerald-300">{c.status === "completed" ? "Completed" : c.status}</span>}
                                  {c.unit_price != null && <p className="text-xs text-slate-300">Quantity {c.quantity} · Unit price {c.unit_price.toLocaleString()} · Line discount {c.discount_pct ?? 0}% · Net unit price {c.net_unit_price?.toLocaleString()}</p>}
                                  {c.missing.length > 0 && <span className="text-xs text-amber-300">needs {c.missing.map((m) => m.replace(" is required", "")).join(", ")}</span>}
                                  {editable && (
                                    <button
                                      onClick={() => onRequestIntent(`Order ${o.id}: `)}
                                      className="ml-auto rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-1 text-xs font-semibold text-violet-300 hover:bg-violet-500/20"
                                    >
                                      Set technical attributes
                                    </button>
                                  )}
                                </div>
                                {(pricing.length > 0 || technical.length > 0) && (
                                  <dl className="mt-3 grid gap-x-6 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                                    {pricing.map(([k, v]) => (
                                      <div key={`p-${k}`} className="min-w-0">
                                        <dt className="text-[11px] text-sky-400/80">{k}</dt>
                                        <dd className="truncate text-sm text-slate-200">{v}</dd>
                                      </div>
                                    ))}
                                    {technical.map(([k, v]) => (
                                      <div key={`t-${k}`} className="min-w-0">
                                        <dt className="text-[11px] text-violet-400/80">{k}</dt>
                                        <dd className="truncate text-sm text-slate-200">{v}</dd>
                                      </div>
                                    ))}
                                  </dl>
                                )}
                              </div>
                            );
                          })}
                          {config.some((c) => Object.keys(c.technical_attributes).length > 0) && (
                            <p className="flex gap-4 text-[11px] text-slate-500">
                              <span><span className="text-sky-400">■</span> pricing attribute</span>
                              <span><span className="text-violet-400">■</span> technical attribute</span>
                            </p>
                          )}
                        </div>
                      </Section>
                    )}

                    {o.service_orders.length > 0 && (
                      <Section title="Sites & resources">
                        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                          {o.service_orders.map((so) => (
                            <div key={so.id} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4 text-sm">
                              <div className="flex items-center gap-2">
                                <p className="font-semibold text-slate-100">{so.site_label}</p>
                                <span className="text-xs text-slate-500">#{so.id}</span>
                                <span className="ml-auto rounded-full bg-slate-800 px-2 py-0.5 text-[11px] capitalize text-slate-300">{nice(so.status)}</span>
                              </div>
                              <p className="mt-2 text-xs leading-relaxed text-slate-400">
                                {so.resources.map((r) => `${r.resource_type}:${r.identifier}(${r.status})`).join(", ") || "no resources"}
                              </p>
                              {so.status === "pending" && (
                                <button
                                  onClick={() => onRequestIntent(`Provision this site. order_id: ${o.id}, site_label: ${so.site_label}`)}
                                  className="mt-3 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-1 text-xs font-semibold text-sky-300 hover:bg-sky-500/20"
                                >
                                  Provision
                                </button>
                              )}
                            </div>
                          ))}
                        </div>
                      </Section>
                    )}

                    {wos.length > 0 && (
                      <Section title={`Work orders · ${woDone} of ${wos.length} done`}>
                        <div className="grid gap-3 md:grid-cols-3">
                          {wos.map((wo) => {
                            const done = wo.status === "complete";
                            return (
                              <div key={wo.id} className={`rounded-xl border p-4 ${done ? "border-emerald-500/20 bg-emerald-500/5" : "border-amber-500/30 bg-amber-500/5"}`}>
                                <div className="flex items-start gap-3">
                                  <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${done ? "bg-emerald-500/20 text-emerald-300" : "bg-amber-500/20 text-amber-300"}`}>
                                    {done ? "✓" : "…"}
                                  </span>
                                  <div className="min-w-0 flex-1">
                                    <p className="text-sm font-semibold text-slate-100">{WORK_ORDER_LABELS[wo.kind] ?? wo.kind}</p>
                                    <p className="mt-0.5 text-xs capitalize text-slate-500">
                                      #{wo.id} · {nice(wo.status)}
                                    </p>
                                  </div>
                                </div>
                                {!done && WORK_ORDER_CLOSE_ACTIONS[wo.kind] && (
                                  <button
                                    onClick={() => onRequestIntent(WORK_ORDER_CLOSE_ACTIONS[wo.kind](wo.id))}
                                    className="mt-3 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-1 text-xs font-semibold text-sky-300 hover:bg-sky-500/20"
                                  >
                                    Close
                                  </button>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </Section>
                    )}

                    {milestones.length > 0 && (
                      <Section title={`Journey · ${milestones.length} steps`}>
                        <ol className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                          {milestones.map((m, i) => (
                            <li key={`${m.code}-${i}`} className="flex items-start gap-2.5" title={m.detail ?? undefined}>
                              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] font-bold text-emerald-300">{i + 1}</span>
                              <span className="min-w-0">
                                <span className="block text-[13px] text-slate-200">{m.label}</span>
                                <span className="block text-[11px] text-slate-500">{new Date(m.reached_at).toLocaleString()}</span>
                              </span>
                            </li>
                          ))}
                        </ol>
                      </Section>
                    )}
                  </div>
                )}
              </article>
            );
          })}
          {orders.length === 0 && <p className="rounded-2xl border border-dashed border-slate-800 p-10 text-center text-sm text-slate-500">No orders yet.</p>}
          {orders.length > 0 && shown.length === 0 && <p className="rounded-2xl border border-dashed border-slate-800 p-10 text-center text-sm text-slate-500">No orders match.</p>}
        </div>
      )}

      {/* ---------- billing accounts ---------- */}
      {tab === "billing" && (
        <div className="grid gap-5 lg:grid-cols-2 2xl:grid-cols-3">
          {billingAccounts.map((b) => (
            <article key={b.id} className="flex flex-col rounded-2xl border border-slate-800 bg-slate-900/40 p-5 sm:p-6">
              <div className="flex items-start gap-4">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-sky-500/15 text-sm font-bold text-sky-300">{initials(b.party_name)}</span>
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-base font-semibold text-slate-50">{b.party_name}</h3>
                  <p className="mt-0.5 truncate text-xs text-slate-500">{b.rate_plan}</p>
                </div>
                <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-xs capitalize text-slate-300">{nice(b.status)}</span>
              </div>
              <div className="mt-5 flex items-end justify-between border-t border-slate-800 pt-4">
                <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Balance</span>
                <span className={`text-2xl font-semibold ${b.balance > 0 ? "text-amber-300" : "text-slate-100"}`}>{inr(b.balance)}</span>
              </div>
              {b.adjustments.length > 0 && (
                <div className="mt-4 space-y-1.5">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Adjustments</p>
                  {b.adjustments.map((a) => (
                    <div key={a.id} className="flex items-center gap-3 rounded-lg bg-slate-950/50 px-3 py-2 text-xs">
                      <span className="min-w-0 flex-1 truncate text-slate-400">{a.reason}</span>
                      <span className={`shrink-0 font-semibold ${a.amount >= 0 ? "text-amber-300" : "text-emerald-300"}`}>{inr(a.amount)}</span>
                    </div>
                  ))}
                </div>
              )}
            </article>
          ))}
          {billingAccounts.length === 0 && <p className="rounded-2xl border border-dashed border-slate-800 p-10 text-center text-sm text-slate-500 lg:col-span-2">No billing accounts yet.</p>}
        </div>
      )}

      <Drawer open={manage !== null} onClose={() => setManage(null)} title={manage ? (om[manage]?.order_number ?? `Order ${manage}`) : ""} subtitle="Lines, shipping, payment and history - every step runs its intent" wide>
        {manage && <OrderWorkbench orderId={manage} />}
      </Drawer>
    </div>
  );
}
