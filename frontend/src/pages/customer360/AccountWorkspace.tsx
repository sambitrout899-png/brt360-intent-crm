import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { c360PartyNetwork, c360PartyView, customerOverview, pageForm, party360, submitStructuredIntent, type CustomerOverview, type PageForm } from "../../api/client";
import type { C360View, CustomerNetwork } from "../../api/c360Types";
import type { EntityType, Party360 } from "../../api/types";
import { PageButton } from "../../components/pages/PageFields";
import { useIntentRefresh, useWorkspace } from "../../workspace";
import AccountPanel from "../comms/AccountPanel";
import BillingPanel from "../comms/BillingPanel";
import AccountSummary, { AccountAlerts, AccountOrders, AccountRequests } from "./AccountSummary";
import { StatusChip } from "./charts";
import { initials } from "./format";
import Customer360View from "./Customer360View";
import InstalledAssets from "./InstalledAssets";
import RecordsWidget from "./RecordsWidget";
import { WorkspaceHealth, WorkspaceMetrics } from "./WorkspaceHeader";
import { componentVisible, PAGE_TABS, panesForTab, sectionState, visibleTabs, workspaceCoverage, type PageTabId } from "./workspacePresentation";
import "./accountSummary.css";

/** One customer's account workspace - identity header, tabs and each tab's
 * content. The Customer 360 page renders it with the published layout; the
 * Configure 360 studio renders the very same component with the unsaved
 * draft (preview), so the preview is the page. */

const date = (s: string | null) => (s ? new Date(s).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");
const ago = (s: string) => {
  const d = Math.floor((Date.now() - new Date(s).getTime()) / 86400000);
  return d <= 0 ? "Today" : d === 1 ? "Yesterday" : d < 30 ? `${d} days ago` : date(s);
};

function Section({ title, action, children, wide = false }: { title: string; action?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`account-applet ${wide ? "account-profile-wide" : ""}`}>
      <header className="account-applet-heading"><h3>{title}</h3>{action}</header>
      <div className="account-applet-body">{children}</div>
    </section>
  );
}

function Secondary({ title, children }: { title: string; children: ReactNode }) {
  return <details className="account-secondary"><summary>{title}</summary><div className="account-secondary-body">{children}</div></details>;
}

function Timeline({ items, open }: { items: CustomerOverview["timeline"]; open: (t: string, id: number) => void }) {
  if (!items.length) return <p className="account-empty">No activity has been recorded for this account.</p>;
  return (
    <ol className="account-timeline">
      {items.map((e, i) => (
        <li key={i}>
          <span className="account-timeline-dot" aria-hidden="true" />
          <button type="button" onClick={() => open(e.entity_type, e.entity_id)}>
            <strong>{e.title}</strong>
            {e.detail && <span>{e.detail}</span>}
            <small>{ago(e.at)}</small>
          </button>
        </li>
      ))}
    </ol>
  );
}

const shown = (v: unknown) => v !== null && v !== undefined && v !== "";
const display = (v: unknown) => (v === "true" || v === true ? "Yes" : v === "false" || v === false ? "No" : String(v));

function Details({ form, onEdit, partyId }: { form: PageForm | null; onEdit?: () => void; partyId: number }) {
  if (!form) return <p className="account-empty">Loading profile…</p>;
  return (
    <div className="account-profile">
      <div className="account-profile-bar">
        <p>Fields as set up in the Page Designer ({form.name})</p>
        {onEdit && <button type="button" className="account-button account-button-primary" onClick={onEdit}>Edit details</button>}
      </div>
      <div className="account-profile-grid">
        {form.sections.map((s, i) => (
          <Section key={s.title} title={s.title} wide={form.sections.length % 2 === 1 && i === form.sections.length - 1}>
            <div>
              {s.fields.filter((f) => f.kind === "text").map((f) => <p key={f.key} className="account-profile-note">{f.text}</p>)}
              <dl className={`account-profile-facts ${s.columns === 1 ? "account-profile-facts-single" : ""}`}>
                {s.fields.filter((f) => !f.kind || f.kind === "field").map((f) => (
                  <div key={f.key} className={f.span === "full" ? "account-profile-full" : undefined}>
                    <dt>{f.label}</dt>
                    <dd className={shown(f.value) ? undefined : "account-muted"}>{shown(f.value) ? display(f.value) : f.mandatory ? "Missing" : "—"}</dd>
                  </div>
                ))}
              </dl>
              {s.fields.some((f) => f.kind === "button") && (
                <div className="account-profile-buttons">
                  {s.fields.filter((f) => f.kind === "button").map((f) => <PageButton key={f.key} field={f} record={{ entityType: "party", id: partyId }} />)}
                </div>
              )}
            </div>
          </Section>
        ))}
      </div>
    </div>
  );
}

export default function AccountWorkspace({
  partyId, tab: requestedTab, onTabChange, onRequestIntent, view: viewOverride, preview = false, onViewLoaded, onConnect, onBack, highlightPane,
}: {
  partyId: number;
  tab: PageTabId;
  onTabChange: (tab: PageTabId) => void;
  onRequestIntent: (text: string) => void;
  /** a layout rendered elsewhere (the studio's draft); omitted = load the published one */
  view?: C360View | null;
  /** studio preview: nothing on the page can run an intent */
  preview?: boolean;
  onViewLoaded?: (view: C360View | null) => void;
  onConnect?: () => void;
  onBack?: () => void;
  highlightPane?: string | null;
}) {
  const ws = useWorkspace();
  const [overview, setOverview] = useState<CustomerOverview | null>(null);
  const [detail, setDetail] = useState<Party360 | null>(null);
  const [form, setForm] = useState<PageForm | null>(null);
  const [loadedView, setLoadedView] = useState<C360View | null>(null);
  const [network, setNetwork] = useState<CustomerNetwork | null>(null);
  const [assetId, setAssetId] = useState<number>();
  const [moreOpen, setMoreOpen] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(partyId);
  const ownView = viewOverride === undefined;
  const view = ownView ? loadedView : viewOverride;

  const load = useCallback((id: number) => {
    Promise.all([customerOverview(id), party360(id)]).then(([o, d]) => {
      if (active.current !== id) return;
      setOverview(o); setDetail(d); setError("");
    }).catch(e => { if (active.current === id) setError(e instanceof Error ? e.message : "Could not load this customer"); });
    // Optional connected insights never prevent the core account summary from loading.
    if (ownView) c360PartyView(id).then(v => { if (active.current === id) { setLoadedView(v); onViewLoaded?.(v); } })
      .catch(() => { if (active.current === id) { setLoadedView(null); onViewLoaded?.(null); } });
    // the network under the customer's services (Network Inventory) - one fetch for the summary and the asset tabs
    c360PartyNetwork(id).then(n => { if (active.current === id) setNetwork(n); }).catch(() => undefined);
    pageForm("party", { recordId: id }).then(f => {
      if (active.current !== id) return;
      setForm(f);
      if (!preview && f.events?.on_open) submitStructuredIntent("Page opened", f.events.on_open, { entity_type: "party", entity_id: id, party_id: id }).catch(() => undefined);
    }).catch(() => { if (active.current === id) setForm(null); });
  }, [ownView, preview, onViewLoaded]);

  // Parents key this component by customer, so a new customer starts fresh.
  useEffect(() => {
    active.current = partyId;
    load(partyId);
  }, [partyId, load]);
  useIntentRefresh(() => { if (!preview) load(partyId); });

  const tabs = visibleTabs(view);
  const tabHidden = !tabs.some(t => t.id === requestedTab);
  const tab: PageTabId = tabHidden && !preview ? "overview" : requestedTab;
  const configuredPanes = panesForTab(view, tab);
  const covered = workspaceCoverage(view, tab);
  const state = (key: string) => sectionState(tab, key, view?.layout.panes ?? [], k => componentVisible(view, k), view?.panes ?? []);
  const nextActions = (overview?.next_actions ?? []).filter(a => ws.canRun(a.code));
  const openRecord = (t: string, id: number) => { if (["party", "lead", "opportunity", "quote", "order"].includes(t)) ws.openRecord(t as EntityType, id); };
  const openAssets = (id?: number) => { setAssetId(id); onTabChange("services"); };

  if (error) return <p role="alert" className="account-error">{error}</p>;
  if (!overview || !detail) return <div className="account-loading" aria-busy="true"><span /><span /><span /></div>;

  const facts = overview.facts;
  // what each tab holds, at a glance (open items for sales and service requests)
  const tabCount: Partial<Record<PageTabId, number>> = {
    services: detail.service_accounts.reduce((n, a) => n + a.assets.length, 0), orders: detail.orders.length,
    sales: overview.numbers.open_deals, support: overview.numbers.open_tickets,
  };
  const alertsOnTop = componentVisible(view, "insights") && !configuredPanes.some(p => p.widgets.some(w => w.type === "insights"));
  // the Services tab lists the installed assets itself - a configured widget repeating that list is left out there
  const repeatsAssets = (w: C360View["panes"][number]["widgets"][number]) => tab === "services" && (
    (w.type === "records" && ((w.metric.sections as string[] | undefined) ?? []).includes("service_accounts")) || (w.type === "table" && w.dataset === "assets"));
  const configured = configuredPanes.length > 0 && view && !(tab === "services" && assetId !== undefined) && (
    <Customer360View view={view} detail={detail} showHero={false} highlightPane={highlightPane} paneFilter={p => configuredPanes.some(pane => pane.id === p.id)}
      widgetFilter={w => !repeatsAssets(w)}
      onRequestIntent={onRequestIntent} onConnect={onConnect ?? (ws.openConfigure360 ? () => ws.openConfigure360?.({ partyId, tab: "sources" }) : undefined)} />
  );
  const salesSections = ["leads", "opportunities", "quotes"].filter(section => !covered.has(section));
  const menu = [...nextActions.slice(2), { code: "INT-29", label: "Edit account", entities: { entity_type: "party", entity_id: partyId } }, { code: "INT-32", label: "Assign account", entities: { entity_type: "party", entity_id: partyId } }]
    .filter((a, i, all) => all.findIndex(b => b.code === a.code) === i && ws.canRun(a.code));

  return (
    <div className="account-detail">
      {onBack && <button type="button" className="account-back" onClick={onBack}>← All accounts</button>}
      <section className="account-identity" aria-label="Customer account">
        <div className="account-identity-top">
          <span className="account-avatar account-avatar-large">{initials(overview.party.name)}</span>
          <div className="account-identity-name">
            <div className="account-identity-title">
              <h2>{overview.party.name}</h2>
              <StatusChip value={facts.status} />
              {facts.segment && <span className="account-segment">{facts.segment}</span>}
              {facts.kyc && <StatusChip value={`KYC ${facts.kyc}`} />}
            </div>
            <div className="account-identity-meta">
              <span>Account #{overview.party.id}</span>
              {facts.brt_id && <span className="account-brt-id">{facts.brt_id}</span>}
              {facts.industry && <span>{facts.industry}</span>}
              {facts.since && <span>Customer since {date(facts.since)}</span>}
              <span>Owner: {facts.owner || "Unassigned"}</span>
              {facts.price_list && <span>Price list: {facts.price_list}</span>}
              {detail.billing_account && componentVisible(view, "billing") && (
                <span><button type="button" className="account-meta-link" onClick={() => onTabChange("billing")}>Billing account #{detail.billing_account.id}</button></span>
              )}
            </div>
          </div>
          <WorkspaceHealth view={view} />
        </div>
        <div className="account-identity-bottom">
          <div className="account-contact">
            {facts.email && <a href={`mailto:${facts.email}`}><span aria-hidden="true">✉</span>{facts.email}</a>}
            {facts.phone && <a href={`tel:${facts.phone}`}><span aria-hidden="true">☎</span>{facts.phone}</a>}
            {!facts.email && !facts.phone && <span>No contact details recorded</span>}
          </div>
          <div className="account-quick-actions" inert={preview}>
            {nextActions.slice(0, 2).map((a, i) => (
              <button type="button" key={`${a.code}-${i}`} className={`account-button ${i === 0 ? "account-button-primary" : ""}`} onClick={() => ws.runIntent(a.code, a.entities)} title={a.why}>{a.label}</button>
            ))}
            {(menu.length > 0 || ws.openAbout) && (
              <div className="account-more">
                <button type="button" className="account-button" aria-expanded={moreOpen} onClick={() => setMoreOpen(!moreOpen)}>More actions ▾</button>
                {moreOpen && (
                  <div className="account-action-menu" role="menu">
                    {menu.map(a => <button type="button" role="menuitem" key={a.code} onClick={() => { setMoreOpen(false); ws.runIntent(a.code, a.entities); }}>{a.label}</button>)}
                    {ws.openAbout && <button type="button" role="menuitem" onClick={() => { setMoreOpen(false); ws.openAbout?.("party", partyId); }}>About this account</button>}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <nav className="account-nav" aria-label="Account views">
          {PAGE_TABS.filter(t => tabs.includes(t) || (preview && t.id === requestedTab)).map(t => (
            <button type="button" key={t.id} aria-current={tab === t.id ? "page" : undefined} className={tabs.includes(t) ? undefined : "account-nav-hidden"} onClick={() => onTabChange(t.id)}>
              {t.label}{tabCount[t.id] ? <span className="account-nav-count" data-alert={t.id === "support" || undefined}>{tabCount[t.id]}</span> : null}
            </button>
          ))}
        </nav>
      </section>

      <div className="account-tab-content" key={partyId} inert={preview}>
        {preview && tabHidden && <p className="account-notice">This tab is not shown to {overview.party.name}: its standard content is hidden by an audience rule and no pane on it applies to this customer.</p>}
        {tab === "overview" && alertsOnTop && <AccountAlerts view={view} onRequestIntent={onRequestIntent} />}
        {tab === "overview" && view && <WorkspaceMetrics view={view} />}
        {configured}

        {tab === "overview" && (
          <AccountSummary overview={overview} detail={detail} view={view} network={network} covered={covered} showFigures={!view} showAlerts={false}
            onAssets={openAssets} onOrders={() => onTabChange("orders")} onRequests={() => onTabChange("support")} onActivity={() => onTabChange("activity")} onRequestIntent={onRequestIntent} />
        )}

        {tab === "services" && state("service_accounts") !== "hidden" && <>
          {assetId !== undefined && configuredPanes.length > 0 && <button type="button" className="workspace-back-link" onClick={() => setAssetId(undefined)}>← Back to the configured service view</button>}
          <InstalledAssets key={`${partyId}-${assetId ?? "all"}`} initialSelectedId={assetId} accounts={detail.service_accounts} network={network} />
        </>}

        {tab === "orders" && state("orders") === "shown" && <AccountOrders detail={detail} />}

        {tab === "sales" && state("sales") === "shown" && salesSections.length > 0 && (
          <Section title="Sales pipeline"><RecordsWidget detail={detail} sections={salesSections} onRequestIntent={onRequestIntent} /></Section>
        )}

        {tab === "billing" && state("billing") === "shown" && <BillingPanel key={partyId} partyId={partyId} />}
        {tab === "billing" && state("billing") === "secondary" && (
          <Secondary title="Billing account, bill profiles and billing actions"><BillingPanel key={partyId} partyId={partyId} /></Secondary>
        )}

        {tab === "support" && state("tickets") === "shown" && <AccountRequests detail={detail} />}

        {tab === "details" && state("profile") === "shown" && <>
          <Details form={form} partyId={partyId} onEdit={ws.canRun("INT-29") ? () => ws.runIntent("INT-29", { entity_type: "party", entity_id: partyId }) : undefined} />
          <Secondary title="Identity verification history"><RecordsWidget detail={detail} sections={["identity"]} onRequestIntent={onRequestIntent} /></Secondary>
          <Secondary title="Customer journey">
            <ol className="account-journey">
              {overview.journey.steps.map(step => <li key={step.label}><StatusChip value={step.reached ? "completed" : "pending"} /><span>{step.label}</span><small>{step.at ? date(step.at) : ""}</small></li>)}
            </ol>
          </Secondary>
        </>}

        {tab === "account" && state("hierarchy") === "shown" && <AccountPanel key={partyId} partyId={partyId} name={overview.party.name} />}

        {tab === "activity" && state("activity") === "shown" && <Section title="Account activity"><Timeline items={overview.timeline} open={openRecord} /></Section>}
      </div>
    </div>
  );
}
