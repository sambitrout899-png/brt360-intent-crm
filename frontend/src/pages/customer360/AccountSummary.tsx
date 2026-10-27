import { useState } from "react";
import type { CustomerOverview } from "../../api/client";
import type { C360View, CustomerNetwork } from "../../api/c360Types";
import type { Party360 } from "../../api/types";
import { useWorkspace } from "../../workspace";
import ServicesOverview from "./ServicesOverview";
import { StatusChip } from "./charts";
import { formatCell, formatValue } from "./format";
import { componentVisible } from "./workspacePresentation";

export function AccountOrders({ detail, compact = false, onViewAll }: { detail: Party360; compact?: boolean; onViewAll?: () => void }) {
  const { openRecord } = useWorkspace();
  const [query, setQuery] = useState("");
  const orders = [...detail.orders].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
  const filtered = orders.filter(o => `${o.id} ${o.order_type} ${o.status} ${o.service_account_name}`.toLowerCase().includes(query.toLowerCase()));
  const shown = compact ? orders.slice(0, 5) : filtered;
  const typeLabel = (t: string) => ({ new: "New connection", change: "Change order", cancel: "Cancellation", suspend: "Suspension" } as Record<string, string>)[t] ?? String(formatCell(t, "text"));
  if (compact) {
    // on the summary: one line per order - what, when, where it stands
    return <section className="account-applet" aria-label="Orders">
      <header className="account-applet-heading"><h3>Orders <span>{orders.length}</span></h3>{orders.length > 0 && <button onClick={onViewAll}>View all →</button>}</header>
      {shown.length ? <ul className="account-row-list">{shown.map(o => <li key={o.id}><button type="button" onClick={() => openRecord("order", o.id)}>
        <span><strong>#{o.id} · {typeLabel(o.order_type)}</strong><small>{formatCell(o.created_at, "date")}{o.monthly_price ? ` · ${formatValue(o.monthly_price, "money")}/mo` : ""}</small></span>
        <StatusChip value={o.status} /></button></li>)}</ul>
        : <p className="account-empty">No orders have been placed.</p>}
    </section>;
  }
  return <section className="account-applet" aria-label="Orders">
    <header className="account-applet-heading"><h3>Orders <span>{orders.length}</span></h3>{compact && <button onClick={onViewAll}>View all →</button>}</header>
    {!compact && <div className="account-list-tools"><input aria-label="Search orders" placeholder="Search order, type, status or service account…" value={query} onChange={e => setQuery(e.target.value)} /></div>}
    <div className="account-table-scroll"><table className="account-table"><thead><tr><th>Order</th><th>Type</th><th>Status</th><th>Created</th>{!compact && <><th>Service account</th><th>Monthly price</th></>}</tr></thead><tbody>{shown.map(o => <tr key={o.id}><td><button onClick={() => openRecord("order", o.id)}>#{o.id}</button></td><td>{({ new: "New connection", change: "Change order", cancel: "Cancellation", suspend: "Suspension" })[o.order_type] ?? formatCell(o.order_type, "text")}</td><td><StatusChip value={o.status} /></td><td>{formatCell(o.created_at, "date")}</td>{!compact && <><td>{o.service_account_name || `#${o.service_account_id}`}</td><td>{formatValue(o.monthly_price, "money")}</td></>}</tr>)}</tbody></table></div>
    {!shown.length && <p className="account-empty">{orders.length ? "No orders match your search." : "No orders have been placed."}</p>}
  </section>;
}

export function AccountRequests({ detail, compact = false, onViewAll, onNew }: { detail: Party360; compact?: boolean; onViewAll?: () => void; onNew?: (text: string) => void }) {
  const { canRun, runIntent } = useWorkspace();
  const [selected, setSelected] = useState<number>();
  const tickets = [...detail.tickets].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
  const shown = compact ? tickets.slice(0, 4) : tickets;
  if (compact) {
    return <section className="account-applet" aria-label="Service requests">
      <header className="account-applet-heading"><h3>Service requests <span>{tickets.length}</span></h3>{tickets.length > 0 && <button onClick={onViewAll}>View all →</button>}</header>
      {shown.length ? <ul className="account-row-list">{shown.map(t => <li key={t.id}><button type="button" onClick={onViewAll}>
        <span><strong>#{t.id} · {formatCell(t.category, "text")}</strong><small>{formatCell(t.created_at, "date")}{t.priority ? ` · ${t.priority}` : ""}</small></span>
        <StatusChip value={t.status} /></button></li>)}</ul>
        : <p className="account-empty">No service requests.{onNew && <> <button type="button" className="account-meta-link" onClick={() => onNew(`Log & classify this complaint. company_name: ${detail.party.name}, description: `)}>Log one</button></>}</p>}
    </section>;
  }
  return <section className="account-applet" aria-label="Service requests">
    <header className="account-applet-heading"><h3>Service requests <span>{tickets.length}</span></h3><div>{!compact && canRun("INT-10") && <button onClick={() => runIntent("INT-10", { company_name: detail.party.name })}>+ New request</button>}{compact && <button onClick={onViewAll}>View all →</button>}</div></header>
    <div className="account-table-scroll"><table className="account-table"><thead><tr><th>Request</th><th>Category</th><th>Status</th><th>Priority</th></tr></thead><tbody>{shown.map(t => <tr key={t.id}><td><button aria-expanded={selected === t.id} onClick={() => setSelected(selected === t.id ? undefined : t.id)}>#{t.id}</button></td><td>{formatCell(t.category, "text")}</td><td><StatusChip value={t.status} /></td><td>{formatCell(t.priority, "text")}</td></tr>)}</tbody></table></div>
    {!shown.length && <p className="account-empty">No service requests have been logged.</p>}
    {shown.filter(t => t.id === selected).map(t => <div key={t.id} className="account-request-detail"><strong>Request #{t.id}</strong><p>{t.description || "No description recorded."}</p><small>Opened {formatCell(t.created_at, "date")} · SLA {t.sla_class || "—"}</small>{canRun("INT-11") && !t.root_cause && t.status !== "resolved" && <button onClick={() => runIntent("INT-11", { ticket_id: t.id })}>Diagnose request</button>}</div>)}
  </section>;
}

const TONE_ICON: Record<string, string> = { risk: "!", action: "→", opportunity: "+", info: "i", good: "✓" };

/** What needs doing on this account - risks first, then next steps and opportunities - one line each,
 * shown first on the summary so it is never scrolled past. */
export function AccountAlerts({ view, onRequestIntent }: { view: C360View | null; onRequestIntent: (text: string) => void }) {
  const order = { risk: 0, action: 1, opportunity: 2, info: 3, good: 4 } as const;
  const attention = (view?.insights ?? []).filter(i => i.tone !== "good").sort((a, b) => order[a.tone] - order[b.tone]).slice(0, 4);
  if (!attention.length) {
    return view?.insights.length ? <p className="account-all-clear"><span aria-hidden="true">✓</span>All clear - no open risks or pending actions for this customer.</p> : null;
  }
  return <section className="account-attention" aria-label="Needs attention">
    <header>Needs attention <span>{attention.length}</span></header>
    {attention.map(i => <div key={i.title} data-tone={i.tone}>
      <span className="account-attention-icon" aria-hidden="true">{TONE_ICON[i.tone]}</span>
      <div><strong>{i.title}</strong><p>{i.detail}</p></div>
      {i.action && <button type="button" onClick={() => onRequestIntent(i.action!.text)}>{i.action.label}</button>}
    </div>)}
  </section>;
}

function ago(s: string): string {
  const d = Math.floor((Date.now() - new Date(s).getTime()) / 86400000);
  return d <= 0 ? "Today" : d === 1 ? "Yesterday" : d < 30 ? `${d} days ago` : String(formatCell(s, "date"));
}

function RecentActivity({ items, onViewAll }: { items: CustomerOverview["timeline"]; onViewAll: () => void }) {
  const { openRecord } = useWorkspace();
  return <section className="account-applet" aria-label="Recent activity">
    <header className="account-applet-heading"><h3>Recent activity</h3>{items.length > 4 && <button type="button" onClick={onViewAll}>View all →</button>}</header>
    {items.length === 0 ? <p className="account-empty">Nothing has happened on this account yet.</p> : <ol className="account-activity-list">
      {items.slice(0, 4).map((e, i) => <li key={i}><button type="button" onClick={() => ["party", "lead", "opportunity", "quote", "order"].includes(e.entity_type) && openRecord(e.entity_type as "order", e.entity_id)}>
        <strong>{e.title}</strong><small>{e.detail ? `${e.detail} · ` : ""}{ago(e.at)}</small></button></li>)}
    </ol>}
  </section>;
}

export default function AccountSummary({ overview, detail, view, network, covered = new Set<string>(), showFigures = true, showAlerts = true, onAssets, onOrders, onRequests, onActivity, onRequestIntent }: {
  overview: CustomerOverview; detail: Party360; view: C360View | null; network: CustomerNetwork | null;
  covered?: Set<string>; showFigures?: boolean; showAlerts?: boolean;
  onAssets: (id?: number) => void; onBilling?: () => void; onOrders: () => void; onRequests: () => void; onActivity: () => void; onRequestIntent: (text: string) => void;
}) {
  const { canRun } = useWorkspace();
  return <div className="account-summary">
    {showFigures && <section className="account-financials" aria-label="Account figures">
      <div><span>Monthly recurring revenue</span><strong>{formatValue(overview.numbers.mrr, "money")}</strong></div>
      <div><span>Account balance</span><strong>{overview.numbers.balance === null ? "—" : formatValue(overview.numbers.balance, "money")}</strong></div>
      <div><span>Open pipeline</span><strong>{formatValue(overview.numbers.pipeline, "money")}</strong></div>
      <div><span>Open service requests</span><strong>{overview.numbers.open_tickets}</strong></div>
    </section>}
    {showAlerts && <AccountAlerts view={view} onRequestIntent={onRequestIntent} />}
    <div className="account-summary-grid" data-transactions-hidden={covered.has("orders") && covered.has("tickets") && !componentVisible(view, "activity")}>
      <div className="account-assets-column">
        {!covered.has("service_accounts") && <ServicesOverview detail={detail} network={network} onOpen={onAssets} />}
        {componentVisible(view, "activity") && <RecentActivity items={overview.timeline} onViewAll={onActivity} />}
      </div>
      <div className="account-transaction-column">
        {!covered.has("orders") && <AccountOrders detail={detail} compact onViewAll={onOrders} />}
        {!covered.has("tickets") && <AccountRequests detail={detail} compact onViewAll={onRequests} onNew={canRun("INT-10") ? onRequestIntent : undefined} />}
      </div>
    </div>
  </div>;
}
