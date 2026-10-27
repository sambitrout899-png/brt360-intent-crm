import { useCallback, useEffect, useState } from "react";
import { customersList, type CustomerRow } from "../api/client";
import type { C360View } from "../api/c360Types";
import { useIntentRefresh, useWorkspace } from "../workspace";
import AccountWorkspace from "./customer360/AccountWorkspace";
import { formatValue, initials } from "./customer360/format";
import { workspaceFrame, type PageTabId } from "./customer360/workspacePresentation";
import "./customer360/accountSummary.css";

/** Customer 360: the account list and one customer's workspace. The
 * workspace itself (header, tabs, content) is the shared AccountWorkspace,
 * which the Configure 360 studio previews unchanged. */
export default function CustomerPage({ onRequestIntent }: { onRequestIntent: (text: string) => void }) {
  const ws = useWorkspace();
  const [rows, setRows] = useState<CustomerRow[]>([]);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "account" | "prospect" | "attention">("all");
  const [segment, setSegment] = useState<"all" | "B2B" | "B2C">("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [tab, setTab] = useState<PageTabId>("overview");
  const [view, setView] = useState<C360View | null>(null);
  const [phoneView, setPhoneView] = useState<"list" | "customer">("list");
  const [error, setError] = useState("");

  const loadList = useCallback(() => {
    customersList(search || undefined).then(list => {
      setRows(list);
      setError("");
      setSelectedId(cur => cur ?? list[0]?.id ?? null);
    }).catch(() => setError("Could not load customers"));
  }, [search]);
  useEffect(() => { const timer = setTimeout(loadList, 250); return () => clearTimeout(timer); }, [loadList]);
  useIntentRefresh(loadList);

  const selectCustomer = (id: number) => {
    setSelectedId(id);
    setTab("overview");
    setPhoneView("customer");
  };
  const selectedName = rows.find(r => r.id === selectedId)?.name;
  const { setCurrentRecord } = ws;
  useEffect(() => { setCurrentRecord?.(selectedId !== null ? { entityType: "party", entityId: selectedId, title: selectedName } : null); }, [selectedId, selectedName, setCurrentRecord]);

  const shown = rows.filter(r => (filter === "all" || (filter === "attention" ? r.urgent_tickets > 0 || r.open_deals > 0 : r.status === filter)) && (segment === "all" || r.segment === segment));

  return (
    <div className="page account-workspace" {...workspaceFrame(view)}>
      <div className="account-page-heading">
        <div>
          <h1>Customer 360</h1>
          <p>Accounts, services, orders, billing and service requests in one place.</p>
        </div>
        <div className="account-page-actions">
          {ws.canRun("INT-15") && <button type="button" className="account-button" onClick={() => ws.runIntent("INT-15")}>Add prospect</button>}
          {ws.openConfigure360 && (
            <button type="button" className="account-button" onClick={() => ws.openConfigure360?.({ partyId: selectedId ?? undefined })} title="Design this page in Configure 360">
              Configure page{view?.version !== undefined && <span className="account-version">v{view.version}</span>}
            </button>
          )}
        </div>
      </div>
      <div className="account-workspace-grid">
        <aside className={`account-customer-list ${phoneView === "customer" ? "account-list-mobile-hidden" : ""}`} aria-label="Accounts">
          <div className="account-customer-search">
            <div className="account-search-box">
              <span aria-hidden="true">⌕</span>
              <input aria-label="Search customers" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search accounts" />
            </div>
            <div className="account-filter-row" role="group" aria-label="Account status">
              {([["all", "All"], ["account", "Accounts"], ["prospect", "Prospects"], ["attention", "Attention"]] as const).map(([k, l]) => (
                <button type="button" key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
              ))}
            </div>
            <div className="account-list-meta">
              <span>{shown.length} {shown.length === 1 ? "account" : "accounts"}</span>
              <select aria-label="Segment" value={segment} onChange={e => setSegment(e.target.value as typeof segment)}>
                <option value="all">All segments</option><option value="B2B">B2B</option><option value="B2C">B2C</option>
              </select>
            </div>
          </div>
          <div className="account-customer-rows">
            {shown.map(r => (
              <button type="button" key={r.id} aria-current={selectedId === r.id ? "true" : undefined} onClick={() => selectCustomer(r.id)}>
                <span className={`account-avatar ${r.status === "account" ? "account-avatar-active" : ""}`}>{initials(r.name)}</span>
                <span className="account-row-text">
                  <strong>{r.name}</strong>
                  <small>{r.segment || "Unclassified"} · {r.status === "account" ? "Account" : "Prospect"}{r.services > 0 && ` · ${r.services} service${r.services === 1 ? "" : "s"}`}</small>
                </span>
                <span className="account-row-side">
                  {r.mrr > 0 && <span className="account-row-mrr" title="Monthly recurring revenue">{formatValue(r.mrr, "money", true)}</span>}
                  {r.urgent_tickets > 0 && <span className="account-alert-count" title={`${r.urgent_tickets} urgent service requests`}>{r.urgent_tickets}</span>}
                </span>
              </button>
            ))}
            {!shown.length && <p className="account-empty">{error || "No matching accounts."}</p>}
          </div>
        </aside>
        <main className={`account-main ${phoneView === "list" ? "account-main-mobile-hidden" : ""}`}>
          {selectedId === null
            ? <p className="account-empty">{error || "Select an account to view its summary."}</p>
            : <AccountWorkspace key={selectedId} partyId={selectedId} tab={tab} onTabChange={setTab} onRequestIntent={onRequestIntent}
                onViewLoaded={setView} onBack={() => setPhoneView("list")} />}
        </main>
      </div>
    </div>
  );
}
