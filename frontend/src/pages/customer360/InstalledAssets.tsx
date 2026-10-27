import { Fragment, useEffect, useState } from "react";
import { c360PartyNetwork } from "../../api/client";
import type { CustomerNetwork } from "../../api/c360Types";
import type { Asset, ServiceAccount } from "../../api/types";
import { useWorkspace } from "../../workspace";
import AssetNetwork from "./AssetNetwork";
import { servicesForAsset } from "./networkOfAsset";
import { StatusChip } from "./charts";
import { formatCell } from "./format";
import "./installedAssets.css";

type Values = Record<string, unknown>;
const object = (value: unknown): Values => value && typeof value === "object" && !Array.isArray(value) ? value as Values : {};
function snapshot(asset: Asset): Values {
  try { return object(JSON.parse(asset.instance_json || "{}")); } catch { return {}; }
}
const label = (value: string) => value.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
function fields(value: unknown, prefix = ""): [string, string][] {
  return Object.entries(object(value)).flatMap(([key, v]): [string, string][] => {
    const name = prefix ? `${prefix} / ${label(key)}` : label(key);
    if (v && typeof v === "object" && !Array.isArray(v)) return fields(v, name);
    if (Array.isArray(v)) return v.flatMap((item, i): [string, string][] => typeof item === "object" && item !== null ? fields(item, `${name} ${i + 1}`) : [[`${name} ${i + 1}`, String(item ?? "—")]]);
    return [[name, v == null || v === "" ? "—" : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)]];
  });
}
const money = (v: unknown) => v == null || v === "" || !Number.isFinite(Number(v)) ? "—" : `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
function Facts({ values, empty }: { values: [string, string][]; empty: string }) {
  return values.length ? <dl className="asset-facts">{values.map(([k, v], i) => <div key={`${k}-${i}`}><dt>{k}</dt><dd>{v}</dd></div>)}</dl> : <p className="asset-empty">{empty}</p>;
}

export default function InstalledAssets({ accounts, compact = false, onOpen, initialSelectedId, network: given }: {
  accounts: ServiceAccount[]; compact?: boolean; onOpen?: (id?: number) => void; initialSelectedId?: number;
  /** the customer's network, when the page already loaded it (null = still loading); omitted = fetch it here */
  network?: CustomerNetwork | null;
}) {
  const { runIntent, canRun, openRecord } = useWorkspace();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [accountId, setAccountId] = useState("all");
  const [selectedId, setSelectedId] = useState<number | undefined>(initialSelectedId);
  const [tab, setTab] = useState("Overview");
  const [operation, setOperation] = useState("modify");
  // the customer's network from Network Inventory - fetched once, shown on each asset's Network tab
  const partyId = accounts[0]?.party_id;
  const [fetched, setFetched] = useState<CustomerNetwork | null>(null);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const ownFetch = given === undefined;
  useEffect(() => {
    if (!ownFetch || compact || !partyId) return;
    let live = true;
    c360PartyNetwork(partyId).then((n) => { if (live) setFetched(n); }).catch(() => { if (live) setNetworkError("The network for this customer could not be loaded."); });
    return () => { live = false; };
  }, [partyId, compact, ownFetch]);
  const network = ownFetch ? fetched : given;
  const rows = accounts.flatMap(account => account.assets.map(asset => ({ account, asset, data: snapshot(asset) })));
  const visible = rows.filter(({ asset, account }) => (accountId === "all" || String(account.id) === accountId) && (status === "all" || asset.status === status) && `${asset.asset_type} ${asset.identifier} ${asset.id} ${account.name}`.toLowerCase().includes(query.toLowerCase()));
  const selected = visible.find(r => r.asset.id === selectedId) ?? visible[0];
  const shown = compact ? visible.slice(0, 6) : visible;
  const asset = selected?.asset;
  const data = selected?.data ?? {};
  const config = object(data.configuration);
  const operations = ["modify", "product_change", "promotion_change", "disconnect", "terminate", "suspend", "resume", "relocate", "customer_transfer", "number_portability", "port_out"].filter(op => op !== (asset?.status === "suspended" ? "suspend" : "resume"));
  const chosenOperation = operations.includes(operation) ? operation : "modify";
  // Siebel's open orders: change orders still to be fulfilled (the open one, and any queued behind it)
  const openChanges = (asset?.order_history ?? []).filter(h => h.operation && (h.status === "submitted" || h.status === "queued"));
  return <section className={`installed-assets ${compact ? "installed-assets-compact" : ""}`} aria-label="Installed assets">
    <header className="asset-heading"><div><h3>Service accounts & installed assets <span>{accounts.length} accounts · {rows.length} assets</span></h3>{!compact && <p>Customer → Service account → Installed asset. Select an asset to inspect or change it.</p>}</div>{compact && <button className="asset-view-all" onClick={() => onOpen?.()}>View all →</button>}</header>
    {!compact && <div className="asset-toolbar">
      <input aria-label="Search installed assets" placeholder="Search product, service ID or account…" value={query} onChange={e => setQuery(e.target.value)} />
      <select aria-label="Filter service account" value={accountId} onChange={e => setAccountId(e.target.value)}><option value="all">All service accounts</option>{accounts.map(a => <option key={a.id} value={String(a.id)}>{a.name} (#{a.id})</option>)}</select>
      <select aria-label="Filter asset status" value={status} onChange={e => setStatus(e.target.value)}><option value="all">All statuses</option><option value="active">Active</option><option value="suspended">Suspended</option><option value="decommissioned">Decommissioned</option></select>
      <span>{visible.length} of {rows.length} assets</span>
    </div>}
    <div className="asset-table-scroll"><table className="asset-table"><thead><tr><th>Product / asset</th>{!compact && <th>Service ID</th>}<th>Status</th>{!compact && <th>Quantity</th>}<th>Monthly price</th></tr></thead>
      <tbody>{accounts.filter(a => accountId === "all" || String(a.id) === accountId).filter(a => shown.some(r => r.account.id === a.id) || (!a.assets.length && !query && status === "all")).map(account => <Fragment key={account.id}>
        <tr className="asset-account-group"><th colSpan={compact ? 3 : 5} scope="rowgroup"><span>{account.name}</span><small>Service account #{account.id} · {account.status} · {account.assets.length} installed asset{account.assets.length === 1 ? "" : "s"}</small></th></tr>
        {shown.filter(r => r.account.id === account.id).map(({ asset: a, data: d }) => <tr key={a.id} className={!compact && a.id === asset?.id ? "asset-selected" : ""} onClick={() => compact ? onOpen?.(a.id) : setSelectedId(a.id)}>
          <td><button aria-pressed={!compact && a.id === asset?.id} onClick={e => { e.stopPropagation(); if (compact) onOpen?.(a.id); else setSelectedId(a.id); }}>{a.asset_type}</button><small>Asset #{a.id}{!compact && ` · Version ${a.version}`}</small></td>{!compact && <td>{a.identifier || "—"}</td>}<td><StatusChip value={a.status} /></td>{!compact && <td>{String(d.quantity ?? "—")}</td>}<td>{d.net_unit_price != null && d.quantity != null ? money(Number(d.net_unit_price) * Number(d.quantity)) : "—"}</td>
        </tr>)}
        {!account.assets.length && <tr><td colSpan={compact ? 3 : 5}>No installed assets on this service account yet.</td></tr>}
      </Fragment>)}</tbody></table></div>
    {!selected ? <p className="asset-empty">{rows.length ? "No assets match these filters." : "Installed assets will appear here after service fulfillment."}</p> : !compact && <div className="asset-detail">
      <div className="asset-detail-heading"><div><h4>{asset!.asset_type} <StatusChip value={asset!.status} /></h4><p>Asset #{asset!.id} · {selected.account.name} · Version {asset!.version}</p></div><span className="asset-readonly">Read-only installed configuration</span></div>
      {asset!.status !== "decommissioned" && canRun("INT-107") && <div className="asset-actions"><label>Change this asset <select aria-label="Asset change operation" value={chosenOperation} onChange={e => setOperation(e.target.value)}>{operations.map(op => <option key={op} value={op}>{label(op)}</option>)}</select></label><button onClick={() => runIntent("INT-107", { asset_id: asset!.id, asset_operation: chosenOperation })}>Create change order →</button>{canRun("INT-114") && <button onClick={() => runIntent("INT-114", { asset_id: asset!.id, asset_operation: chosenOperation })}>Quote this change</button>}{canRun("INT-113") && <button onClick={() => runIntent("INT-113", { asset_id: asset!.id })}>Full history</button>}<span>Updates take effect after fulfillment.</span></div>}
      {openChanges.length > 0 && <div className="asset-actions" role="status"><span>{openChanges.length === 1 ? "A change order is" : `${openChanges.length} change orders are`} still to be fulfilled - the service changes when {openChanges.length === 1 ? "it is" : "they are, in turn"}:</span>{openChanges.map(h => <span key={h.order_id}><button onClick={() => openRecord("order", h.order_id)}>Order #{h.order_id} · {label(h.operation!)}{h.due_date ? ` · due ${h.due_date}` : ""}{h.status === "queued" ? " · queued" : ""}</button>{canRun("INT-111") && <button onClick={() => runIntent("INT-111", { order_id: h.order_id })}>Revise</button>}{canRun("INT-110") && <button onClick={() => runIntent("INT-110", { order_id: h.order_id })}>Cancel</button>}</span>)}</div>}
      <div className="asset-tabs" role="tablist" aria-label="Asset details">{["Overview", "Configuration", "Pricing", "Network", "Order history"].map(t => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}{t === "Network" && servicesForAsset(network, asset!).some((s) => s.at_risk === "yes") ? " ⚠" : ""}</button>)}</div>
      <div role="tabpanel" aria-label={tab}>
        {tab === "Overview" && <Facts empty="Asset details are unavailable." values={[["Service ID", asset!.identifier || "—"], ["Installed on", formatCell(asset!.created_at, "date")], ["Service account", `${selected.account.name} (#${selected.account.id})`], ["Billing account", `#${selected.account.billing_account_id}`], ["Quantity", String(data.quantity ?? "—")], ["Product model version", String(data.model_version ?? "—")], ["Site", String(data.site ?? "—")], ["Inventory reference", asset!.inventory_system_ref || "—"]]} />}
        {tab === "Configuration" && <><h5>Technical configuration</h5><Facts values={fields(config.technical)} empty="No technical attributes were recorded for this asset." /><h5>Product options</h5><Facts values={fields(config.pricing)} empty="No configurable product options were recorded." />{Object.keys(object(data.portability)).length > 0 && <><h5>Number portability</h5><Facts values={fields(data.portability)} empty="" /></>}</>}
        {tab === "Pricing" && <><Facts empty="Pricing is unavailable." values={[["Unit price", money(data.unit_price)], ["Line discount", data.discount_pct == null ? "—" : `${data.discount_pct}%`], ["Net unit price", money(data.net_unit_price)], ["Quantity", String(data.quantity ?? "—")], ["Line price / month", data.net_unit_price != null && data.quantity != null ? money(Number(data.net_unit_price) * Number(data.quantity)) : "—"]]} /><p className="asset-empty">Prices reflect the installed asset snapshot. Billing adjustments and invoices are shown in Billing.</p></>}
        {tab === "Network" && <AssetNetwork network={network} asset={asset!} error={networkError} />}
        {tab === "Order history" && <div className="asset-table-scroll"><table className="asset-table"><thead><tr><th>Order</th><th>Operation</th><th>Status</th><th>Due</th><th>Asset version</th></tr></thead><tbody>{asset!.order_history?.map((h, i) => <tr key={i}><td><button onClick={() => openRecord("order", h.order_id)}>Order #{h.order_id}</button></td><td>{h.operation ? label(h.operation) : h.version === 1 ? "New connection" : "Fulfilled revision"}</td><td><StatusChip value={h.status} /></td><td>{h.due_date ?? "—"}</td><td>{h.version ?? "—"}</td></tr>)}</tbody></table>{!asset!.order_history?.length && <p className="asset-empty">No linked orders are available.</p>}</div>}
      </div>
    </div>}
  </section>;
}
