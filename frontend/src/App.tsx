import TrainingPage from "./pages/TrainingPage";
import AssessmentPage from "./pages/AssessmentPage";
import ParticipantPortal from "./pages/training/ParticipantPortal";
import { LearningPage } from "./pages/training/LearningPages";
import { LEARNING_PAGES } from "./pages/training/learningMeta";
import type { LearningPageKey } from "./pages/training/learningMeta";
import { useCallback, useEffect, useMemo, useState } from "react";
import IntentDrawer from "./components/IntentDrawer";
import { ApprovalBell, LoginApprovals, NoticeTicker } from "./components/NoticeBar";
import PaneKit from "./components/layout/PaneKit";
import RecordPanel from "./components/RecordPanel";
import AboutRecordModal from "./components/AboutRecordModal";
import AccountSecurity from "./components/shell/AccountSecurity";
import IdleWatch from "./components/shell/IdleWatch";
import { BrtEmblem, BrtFooter, PoweredByBrt } from "./components/shell/BrtBrand";
import NavIcon from "./components/shell/NavIcon";
import PopupMessages from "./components/shell/PopupMessages";
import Configure360Studio from "./pages/customer360/Configure360Studio";
import AdminPage from "./pages/AdminPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import AuditLogPage from "./pages/AuditLogPage";
import CatalogPage from "./pages/CatalogPage";
import CustomerPage from "./pages/CustomerPage";
import DashboardPage from "./pages/DashboardPage";
import FieldServicePage from "./pages/FieldServicePage";
import IntentCommandBar from "./pages/IntentCommandBar";
import InventoryPage from "./pages/InventoryPage";
import LoginPage from "./pages/LoginPage";
import MobilePage from "./pages/MobilePage";
import BulkOrdersPage from "./pages/BulkOrdersPage";
import MyWorkPage from "./pages/MyWorkPage";
import OrdersPage from "./pages/OrdersPage";
import PipelinePage from "./pages/PipelinePage";
import PlatformAdminPage from "./pages/PlatformAdminPage";
import { UserThemeButton } from "./components/ThemePicker";
import { applyOrganizationTheme } from "./theme";
import { setLayout, toggleGroup, useLayout } from "./layoutPrefs";
import RecordsPage from "./pages/RecordsPage";
import TicketsPage from "./pages/TicketsPage";
import { currentUser, getAccessToken, getBranding, getWorkContext, intentCatalog, logout, myAccess, setWorkContext } from "./api/client";
import { ENTITY_LABELS } from "./api/types";
import type { Branding, CurrentUser, EntityType, MyAccess } from "./api/types";
import { WorkspaceContext } from "./workspace";

type Tab = "training" | LearningPageKey | "dashboard" | "mywork" | "customer" | "pipeline" | "orders" | "tickets" | "field" | "inventory" | "mobile" | "bulk_orders" | "catalog" | "analytics" | "records" | "intent" | "audit" | "admin";

const TABS: { id: Tab; label: string; group: string }[] = [
  { id: "training", label: "Learning home", group: "Learning" },
  ...LEARNING_PAGES.map((p) => ({ id: p.id, label: p.label, group: "Learning" })),
  { id: "dashboard", label: "Dashboard", group: "Workspace" },
  { id: "mywork", label: "My Work", group: "Workspace" },
  { id: "intent", label: "Intent Command Bar", group: "Workspace" },
  { id: "analytics", label: "Analytics & Reports", group: "Workspace" },
  { id: "records", label: "Custom Records", group: "Workspace" },
  { id: "customer", label: "Customer 360", group: "Customers & Sales" },
  { id: "pipeline", label: "Sales Pipeline", group: "Customers & Sales" },
  { id: "orders", label: "Orders & Billing", group: "Operations" },
  { id: "tickets", label: "Tickets", group: "Operations" },
  { id: "field", label: "Field Service", group: "Operations" },
  { id: "inventory", label: "Network Inventory", group: "Operations" },
  { id: "mobile", label: "Mobile Subscribers", group: "Operations" },
  { id: "bulk_orders", label: "Bulk Orders", group: "Operations" },
  { id: "catalog", label: "Catalog", group: "Product & Governance" },
  { id: "audit", label: "Audit Log", group: "Product & Governance" },
  { id: "admin", label: "Admin", group: "Product & Governance" },
];

const NAV_COLLAPSED_KEY = "brt360.nav.collapsed";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(NAV_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function CrmApp({ user, onLoggedOut }: { user: CurrentUser; onLoggedOut: () => void }) {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [navOpen, setNavOpen] = useState(false); // the menu as an overlay on small screens
  const [securityOpen, setSecurityOpen] = useState(false); // Account security: password and two-step sign-in
  const [drawer, setDrawer] = useState<{
    open: boolean;
    text: string;
    nonce: number;
    intent: { code: string; entities: Record<string, unknown> } | null;
  }>({ open: false, text: "", nonce: 0, intent: null });
  const [record, setRecord] = useState<{ entityType: EntityType; entityId: number } | null>(null);
  // "About this record": the pop-up, and the record the floating button offers (the one open, or the one a page shows)
  const [about, setAbout] = useState<{ entityType: EntityType; entityId: number; nonce: number } | null>(null);
  const [current, setCurrent] = useState<{ entityType: EntityType; entityId: number; title?: string } | null>(null);
  const [access, setAccess] = useState<MyAccess | null>(null);
  const [accessError, setAccessError] = useState("");
  // Bumped when the position or data scope changes: remounts the page so
  // it refetches the records visible in the new context.
  const [generation, setGeneration] = useState(0);

  // The intents this user may run - in-page buttons for the others are hidden.
  const [runnable, setRunnable] = useState<Set<string> | null>(null);

  const loadAccess = useCallback(() => {
    intentCatalog()
      .then((c) => setRunnable(new Set(c.intents.map((i) => i.code))))
      .catch(() => setRunnable(null));
    myAccess()
      .then((a) => {
        setAccess(a);
        setAccessError("");
        // keep the stored context in step with what the server granted
        setWorkContext({ positionId: a.active_position_id, scope: a.scope });
      })
      .catch((e) => setAccessError(e instanceof Error ? e.message : "Could not load your access"));
  }, []);

  useEffect(loadAccess, [loadAccess]);

  // The tenant's own branding: logo + company name in the header and tab title.
  const [branding, setBranding] = useState<Branding | null>(null);
  useEffect(() => {
    getBranding()
      .then((b) => {
        setBranding(b);
        document.title = `${b.display_name} · Intent CRM | BRT 360`;
        applyOrganizationTheme(b.theme, b.theme_user_choice, b.accent, b.brand_color); // the organization's theme and accent (Platform Admin sets them)
      })
      .catch(() => setBranding(null));
  }, []);

  function changeContext(next: { positionId: number | null; scope: string | null }) {
    setWorkContext(next);
    setRecord(null);
    setGeneration((g) => g + 1);
    loadAccess();
  }
  const requestIntent = useCallback((text: string) => {
    setDrawer((d) => ({ open: true, text, nonce: d.nonce + 1, intent: null }));
  }, []);

  const runIntent = useCallback((code: string, entities: Record<string, unknown> = {}) => {
    setDrawer((d) => ({ open: true, text: "", nonce: d.nonce + 1, intent: { code, entities } }));
  }, []);

  const openRecord = useCallback((entityType: EntityType, entityId: number) => {
    setRecord({ entityType, entityId });
    setCurrent({ entityType, entityId });
    setDrawer((d) => (d.open ? { ...d, open: false } : d));
  }, []);
  const openAbout = useCallback((entityType: EntityType, entityId: number) => {
    setAbout({ entityType, entityId, nonce: Date.now() });
  }, []);
  const setCurrentRecord = useCallback((r: { entityType: EntityType; entityId: number; title?: string } | null) => setCurrent(r), []);

  // Configure 360 studio (admins) - opened from Customer 360 or the INT-33 intent.
  const [studio, setStudio] = useState<{ nonce: number; instruction?: string; partyId?: number; tab?: "ai" | "layout" | "look" | "sources" } | null>(null);
  const openConfigure360 = useCallback((opts: { instruction?: string; partyId?: number; tab?: "ai" | "layout" | "look" | "sources" } = {}) => {
    setDrawer((d) => (d.open ? { ...d, open: false } : d));
    setStudio({ nonce: Date.now(), ...opts });
  }, []);

  // Admins: jump from anywhere (the Catalog, a result) to a product in Admin > Product Modeller.
  const openProductModeller = useCallback((product?: string) => {
    try {
      localStorage.setItem("brt360.admin.section", "productmodel");
      if (product) localStorage.setItem("brt360.admin.product", product);
    } catch {
      /* private window - Admin opens on its overview instead */
    }
    setDrawer((d) => (d.open ? { ...d, open: false } : d));
    setTab("admin");
  }, []);

  const closeDrawer = useCallback(() => setDrawer((d) => ({ ...d, open: false })), []);
  const closeRecord = useCallback(() => setRecord(null), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setDrawer((d) => (d.open ? { ...d, open: false } : { open: true, text: "", nonce: d.nonce + 1, intent: null }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const canRun = useCallback((code: string) => runnable === null || runnable.has(code), [runnable]);

  const workspace = useMemo(
    () => ({
      role: user.role, requestIntent, runIntent, canRun, openRecord, openAbout, setCurrentRecord,
      openConfigure360: user.role === "admin" ? openConfigure360 : undefined,
      openProductModeller: user.role === "admin" ? openProductModeller : undefined,
    }),
    [user.role, requestIntent, runIntent, canRun, openRecord, openAbout, setCurrentRecord, openConfigure360, openProductModeller]
  );

  // Pages come from the union of the user's roles; Admin also needs the
  // built-in admin level, since the admin APIs require it.
  const tabs = access ? TABS.filter((t) => access.pages.includes(t.id) && (t.id !== "admin" || user.role === "admin")) : [];
  const activeTab = tabs.some((t) => t.id === tab) ? tab : tabs[0]?.id;
  // A second page docked beside the first (Layout menu, or "open beside" in the menu) - never the same page twice.
  const layout = useLayout();
  const besideTab = layout.split && layout.split !== activeTab && tabs.some((t) => t.id === layout.split) ? (layout.split as Tab) : null;
  function renderPage(id: Tab | undefined) {
    return (
      <>
        {(id === "dashboard" || id === "mywork") && <NoticeTicker place={id} onOpenTab={openTab} />}
        {id === "training" && <TrainingPage pages={access?.pages ?? []} onOpen={openTab} />}
        {id?.startsWith("training_") && <LearningPage key={id} page={id as LearningPageKey} />}
        {id === "dashboard" && <DashboardPage />}
        {id === "mywork" && <MyWorkPage />}
        {id === "customer" && <CustomerPage onRequestIntent={requestIntent} />}
        {id === "pipeline" && <PipelinePage onRequestIntent={requestIntent} />}
        {id === "orders" && <OrdersPage onRequestIntent={requestIntent} />}
        {id === "tickets" && <TicketsPage onRequestIntent={requestIntent} onOpenFieldService={tabs.some((t) => t.id === "field") ? () => setTab("field") : undefined} />}
        {id === "field" && <FieldServicePage onRequestIntent={requestIntent} />}
        {id === "inventory" && <InventoryPage onRequestIntent={requestIntent} />}
        {id === "mobile" && <MobilePage />}
        {id === "bulk_orders" && <BulkOrdersPage />}
        {id === "catalog" && <CatalogPage />}
        {id === "analytics" && <AnalyticsPage />}
        {id === "records" && <RecordsPage />}
        {id === "intent" && <IntentCommandBar />}
        {id === "audit" && <AuditLogPage />}
        {id === "admin" && <AdminPage onAccessChanged={loadAccess} />}
      </>
    );
  }

  // the divider between the two pages
  const dragSplit = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget;
    const box = handle.parentElement!.getBoundingClientRect();
    handle.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => setLayout({ splitRatio: (ev.clientX - box.left) / box.width });
    const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  };
  const activePosition = access?.positions.find((p) => p.id === access.active_position_id);
  // Approvals live in My Work; without that page the bell/notice opens the first page instead.
  const openTab = (id: string) => {
    if (tabs.some((t) => t.id === id)) setTab(id as typeof tab);
  };
  const openMyWork = () => openTab("mywork");
  const activeMeta = TABS.find((t) => t.id === activeTab);
  const groups = [...new Set(tabs.map((t) => t.group))].map((g) => [g, tabs.filter((t) => t.group === g)] as const);
  const toggleCollapsed = () =>
    setCollapsed((c) => {
      try {
        localStorage.setItem(NAV_COLLAPSED_KEY, c ? "0" : "1");
      } catch {
        /* private window - the choice lasts for this visit */
      }
      return !c;
    });

  return (
    <WorkspaceContext.Provider value={workspace}>
    <div data-app-body className="flex min-h-screen bg-[var(--app-bg)]">
      {/* ---------- sidebar: grouped navigation (overlay below lg) ---------- */}
      {navOpen && <div className="fixed inset-0 z-30 bg-black/60 lg:hidden" onClick={() => setNavOpen(false)} aria-hidden="true" />}
      <aside
        data-app-nav
        className={`fixed inset-y-0 left-0 z-40 flex flex-col border-r border-slate-800/80 bg-[var(--app-panel)] transition-[width,transform] duration-200 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${
          navOpen ? "translate-x-0" : "-translate-x-full"
        } ${collapsed ? "w-[76px]" : "w-64"}`}
      >
        <div className={`flex h-16 shrink-0 items-center gap-3 border-b border-slate-800/80 ${collapsed ? "justify-center px-2" : "px-5"}`}>
          {branding?.logo_data_url ? (
            <img src={branding.logo_data_url} alt={`${branding.display_name} logo`} className={`rounded-md object-contain ${collapsed ? "h-9 w-9" : "h-9 max-w-[120px]"}`} />
          ) : (
            // no tenant logo: the BRT emblem is the mark
            <BrtEmblem size="h-9 w-9" />
          )}
          {!collapsed && (
            <span className="min-w-0 leading-tight" title={branding?.name}>
              <span className="block truncate text-[15px] font-bold text-slate-50">{branding?.display_name ?? "BRT 360"}</span>
              <span className="block text-[11px] font-medium uppercase tracking-[0.12em] text-slate-500">Intent CRM · BRT 360</span>
            </span>
          )}
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-5" aria-label="Main">
          {groups.map(([group, items]) => {
            // a folded group keeps showing the page you are on
            const folded = !collapsed && layout.foldedGroups.includes(group);
            const shown = folded ? items.filter((t) => t.id === activeTab || t.id === besideTab) : items;
            return (
            <div key={group} className="mb-4 last:mb-0">
              {!collapsed && (
                <button type="button" onClick={() => toggleGroup(group)} aria-expanded={!folded}
                  className="group/g mb-1.5 flex w-full items-center gap-1.5 rounded-md px-3 py-0.5 text-left text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500 hover:text-slate-300"
                  title={folded ? `Show ${group}` : `Fold ${group} away`}>
                  <span className="flex-1">{group}</span>
                  {folded && <span className="rounded-full bg-slate-800 px-1.5 text-[10px] tracking-normal text-slate-400">{items.length}</span>}
                  <svg viewBox="0 0 20 20" className={`h-3.5 w-3.5 transition-transform ${folded ? "-rotate-90" : ""} opacity-60 group-hover/g:opacity-100`} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M5 8l5 5 5-5" /></svg>
                </button>
              )}
              {collapsed && <div className="mx-auto mb-2 h-px w-8 bg-slate-800" />}
              <ul className="space-y-0.5">
                {shown.map((t) => {
                  const active = activeTab === t.id;
                  const beside = besideTab === t.id;
                  return (
                    <li key={t.id} className="group/item relative">
                      <button
                        onClick={() => {
                          setTab(t.id);
                          setCurrent(null);
                          setNavOpen(false);
                        }}
                        title={collapsed ? t.label : undefined}
                        aria-current={active ? "page" : undefined}
                        className={`group relative flex w-full items-center gap-3 rounded-lg py-[7px] text-sm font-medium transition ${collapsed ? "justify-center px-0" : "pl-3 pr-9"} ${
                          active ? "bg-sky-500/10 text-sky-300" : beside ? "bg-slate-800/50 text-slate-200" : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-100"
                        }`}
                      >
                        {active && <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-sky-400" />}
                        {beside && <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-[var(--a2-400)]" />}
                        <NavIcon name={t.id} className={`h-[18px] w-[18px] shrink-0 ${active ? "text-sky-400" : beside ? "text-[var(--a2-400)]" : "text-slate-500 group-hover:text-slate-300"}`} />
                        {!collapsed && <span className="truncate">{t.label}</span>}
                      </button>
                      {/* open this page beside the current one (wide screens) */}
                      {!collapsed && !active && (
                        <button type="button" onClick={() => setLayout({ split: beside ? null : t.id })}
                          className={`absolute right-1.5 top-1/2 hidden h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 hover:bg-slate-700/60 hover:text-slate-100 lg:flex ${beside ? "!flex text-[var(--a2-400)]" : "opacity-0 group-hover/item:opacity-100 focus:opacity-100"}`}
                          title={beside ? `Close ${t.label} beside` : `Open ${t.label} beside this page`} aria-label={beside ? `Close ${t.label} beside` : `Open ${t.label} beside`}>
                          <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="2.5" y="4" width="15" height="12" rx="1.5" />{beside ? <path d="M10 4v12M12.5 8.5l3 3M15.5 8.5l-3 3" /> : <path d="M10 4v12M13.75 7.5v5M11.25 10h5" />}</svg>
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
            );
          })}
        </nav>

        <div className={`shrink-0 border-t border-slate-800/80 py-4 ${collapsed ? "flex justify-center px-2" : "px-5"}`}>
          <PoweredByBrt compact={collapsed} />
        </div>

        <div className="hidden shrink-0 border-t border-slate-800/80 p-3 lg:block">
          <button
            onClick={toggleCollapsed}
            className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-800/60 hover:text-slate-200 ${collapsed ? "justify-center px-0" : ""}`}
            title={collapsed ? "Expand the menu" : "Collapse the menu"}
          >
            <NavIcon name={collapsed ? "expand" : "collapse"} className="h-4 w-4" />
            {!collapsed && "Collapse menu"}
          </button>
        </div>
      </aside>

      {/* ---------- top bar + page ---------- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header data-app-header className="sticky top-0 z-20 border-b border-slate-800/80 bg-[var(--app-bg)]/85 backdrop-blur-md">
          <div className="flex h-16 items-center gap-4 px-4 sm:px-6 lg:px-8">
            <button onClick={() => setNavOpen(true)} className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-slate-100 lg:hidden" aria-label="Open the menu">
              <NavIcon name="menu" className="h-5 w-5" />
            </button>
            <div className="hidden w-52 shrink-0 md:block">
              <p className="truncate text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">{activeMeta?.group ?? "Workspace"}</p>
              <p className="truncate text-[15px] font-semibold text-slate-100">{activeMeta?.label ?? ""}</p>
            </div>

            <button
              onClick={() => requestIntent("")}
              className="mx-auto flex h-10 w-full min-w-0 max-w-lg items-center gap-3 rounded-xl border border-slate-700/80 bg-slate-900/70 px-4 text-left text-sm text-slate-500 transition hover:border-sky-500/50 hover:text-slate-300"
            >
              <NavIcon name="bolt" className="h-4 w-4 shrink-0 text-sky-400" />
              <span className="min-w-0 flex-1 truncate">Ask BRT 360 or run an action…</span>
              <kbd className="hidden rounded-md border border-slate-700 bg-slate-800 px-1.5 py-0.5 font-sans text-[11px] font-semibold text-slate-400 sm:inline">Ctrl K</kbd>
            </button>

            <div className="flex shrink-0 items-center gap-2 sm:gap-3">
              {access && <PaneKit page={activeTab ?? "home"} pages={tabs} />}
              {access && <ApprovalBell onOpenMyWork={openMyWork} />}
              {access && access.positions.length === 1 && (
                <span
                  title="The position you are working as - decides which records you see"
                  className="hidden h-9 max-w-[15rem] items-center truncate rounded-lg border border-slate-800 px-3 text-xs font-medium text-slate-300 xl:flex"
                >
                  {access.positions[0].name}
                  {access.positions[0].department_name ? ` · ${access.positions[0].department_name}` : ""}
                </span>
              )}
              {access && access.positions.length > 1 && (
                <select
                  value={access.active_position_id ?? ""}
                  onChange={(e) => changeContext({ positionId: Number(e.target.value), scope: null })}
                  title="The position you are working as - decides which records you see"
                  className="hidden h-9 w-auto max-w-[15rem] rounded-lg border border-slate-700 bg-slate-900 px-2.5 text-xs font-medium text-slate-200 xl:block"
                >
                  {access.positions.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.department_name ? ` · ${p.department_name}` : ""}
                      {p.is_primary ? " (primary)" : ""}
                    </option>
                  ))}
                </select>
              )}
              {access && (
                <select
                  value={access.scope}
                  disabled={access.available_scopes.length < 2}
                  onChange={(e) => changeContext({ positionId: getWorkContext().positionId, scope: e.target.value })}
                  title={activePosition ? `Widest view for ${activePosition.name}: ${access.max_scope}` : "Data visibility"}
                  className="hidden h-9 w-auto max-w-[11rem] rounded-lg border border-slate-700 bg-slate-900 px-2.5 text-xs font-medium text-slate-200 disabled:opacity-70 lg:block"
                >
                  {access.available_scopes.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              )}
              <UserThemeButton />
              <span className="mx-1 hidden h-8 w-px bg-slate-800 sm:block" />
              <button type="button" onClick={() => setSecurityOpen(true)} className="flex items-center gap-2.5 rounded-lg text-left hover:opacity-90"
                title={`${user.name} - account security`} aria-label="Account security">
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-sky-600 to-indigo-600 text-xs font-bold text-white">
                  {initials(user.name)}
                </span>
                <span className="hidden leading-tight 2xl:block">
                  <span className="block max-w-[160px] truncate text-sm font-semibold text-slate-100">{user.name}</span>
                  <span className="block max-w-[160px] truncate text-[11px] text-slate-500">{activePosition?.name ?? user.role}</span>
                </span>
              </button>
              {securityOpen && <AccountSecurity onClose={() => setSecurityOpen(false)} />}
              <button
                onClick={() => {
                  logout();
                  onLoggedOut();
                }}
                title="Log out"
                aria-label="Log out"
                className="rounded-lg p-2 text-slate-500 hover:bg-red-500/10 hover:text-red-400"
              >
                <NavIcon name="logout" className="h-[18px] w-[18px]" />
              </button>
            </div>
          </div>
        </header>

        <main key={generation} data-pane-root className={besideTab ? "flex-1" : "flex-1 px-4 py-6 sm:px-6 lg:px-10 lg:py-8"}>
          <div className="page">
            {accessError && <p className="text-sm text-red-400">{accessError}</p>}
            {!access && !accessError && <p className="text-sm text-slate-500">Loading your workspace…</p>}
            {access && tabs.length === 0 && (
              <p className="text-sm text-slate-400">
                {access.roles.length === 0
                  ? "You don't have a role yet - ask your administrator for access."
                  : "No pages are assigned to your roles yet - ask an administrator."}
              </p>
            )}
          </div>
          {besideTab ? (
            <div className="split-view" style={{ "--split-a": `${layout.splitRatio}fr`, "--split-b": `${1 - layout.splitRatio}fr` } as React.CSSProperties}>
              <div data-page={activeTab}>
                <div className="split-head">
                  <span className="split-head-title">{activeMeta?.label}</span>
                  <span className="ml-auto" />
                  <button type="button" onClick={() => { setLayout({ split: activeTab ?? null }); if (besideTab) setTab(besideTab); }} title="Swap the two pages">⇄ Swap</button>
                </div>
                <div className="split-pad">{renderPage(activeTab)}</div>
              </div>
              <div className="split-gutter" onPointerDown={dragSplit} onDoubleClick={() => setLayout({ splitRatio: 0.5 })} role="separator" aria-orientation="vertical"
                title="Drag to share the width · double-click for half and half" />
              <div data-page={besideTab}>
                <div className="split-head">
                  <span className="split-head-title">Beside</span>
                  <select value={besideTab} onChange={(e) => setLayout({ split: e.target.value })} aria-label="Page shown beside">
                    {tabs.filter((t) => t.id !== activeTab).map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                  </select>
                  <span className="ml-auto" />
                  <button type="button" onClick={() => { setTab(besideTab); setLayout({ split: null }); }} title="Make this the only page">Open alone</button>
                  <button type="button" onClick={() => setLayout({ split: null })} title="Close this page" aria-label="Close the page beside">✕</button>
                </div>
                <div className="split-pad">{renderPage(besideTab)}</div>
              </div>
            </div>
          ) : (
            <div data-page={activeTab}>
              {renderPage(activeTab)}
              <div className="page">
                <BrtFooter />
              </div>
            </div>
          )}
        </main>
        {access && <PopupMessages />}
      </div>

      {record && (
        <RecordPanel entityType={record.entityType} entityId={record.entityId} onClose={closeRecord} onOpenRecord={openRecord} />
      )}
      {/* About this record - on every page, for the record you are on */}
      {current && !about && (
        <button
          type="button"
          onClick={() => openAbout(current.entityType, current.entityId)}
          title="Everything about this record in one place"
          className="fixed bottom-5 left-1/2 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full border border-sky-500/40 bg-slate-900/95 px-4 py-2 text-sm font-semibold text-sky-200 shadow-xl shadow-sky-950/40 backdrop-blur hover:border-sky-400 hover:text-white lg:left-[calc(50%+8rem)]"
        >
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-sky-500 text-xs font-bold text-white" aria-hidden>i</span>
          About this record
          <span className="max-w-[16rem] truncate text-xs font-normal text-slate-400">
            {current.title ?? `${ENTITY_LABELS[current.entityType]} #${current.entityId}`}
          </span>
          <span
            role="button"
            tabIndex={0}
            aria-label="Hide"
            onClick={(e) => {
              e.stopPropagation();
              setCurrent(null);
            }}
            onKeyDown={(e) => e.key === "Enter" && setCurrent(null)}
            className="ml-1 rounded-full px-1 text-slate-500 hover:text-slate-200"
          >
            ✕
          </span>
        </button>
      )}
      {about && <AboutRecordModal key={about.nonce} start={{ entityType: about.entityType, entityId: about.entityId }} onClose={() => setAbout(null)} />}
      <IntentDrawer
        open={drawer.open}
        initialText={drawer.text}
        intent={drawer.intent}
        nonce={drawer.nonce}
        context={record ? { page: activeTab, entity_type: record.entityType, entity_id: record.entityId } : { page: activeTab }}
        onClose={closeDrawer}
      />
      {access && <LoginApprovals userId={user.id} onOpenMyWork={openMyWork} />}
      {studio && user.role === "admin" && (
        <Configure360Studio
          key={studio.nonce}
          initialInstruction={studio.instruction}
          initialPartyId={studio.partyId}
          initialTab={studio.tab}
          onClose={() => setStudio(null)}
        />
      )}
    </div>
    </WorkspaceContext.Provider>
  );
}

export default function App() {
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined); // undefined = still checking
  const [signedOutNote, setSignedOutNote] = useState<string | null>(null); // why the last session ended, if not by choice

  const signedOut = useCallback((reason?: string) => {
    setSignedOutNote(reason ?? null);
    setUser(null);
  }, []);

  function refreshSession() {
    if (!getAccessToken()) {
      setUser(null);
      return;
    }
    currentUser()
      .then(setUser)
      .catch(() => setUser(null));
  }

  useEffect(refreshSession, []);

  const assessmentToken = window.location.pathname.match(/^\/assessment\/([A-Za-z0-9_-]+)$/)?.[1];
  if (assessmentToken) return <AssessmentPage token={assessmentToken} />;
  // participant links of the training module (no login): /p/a|q|r|u/<token or code>
  const participantRoute = window.location.pathname.match(/^\/p\/([aqru])\/([A-Za-z0-9_.=-]+)$/);
  if (participantRoute) return <ParticipantPortal kind={participantRoute[1] as "a" | "q" | "r" | "u"} value={participantRoute[2]} />;

  if (user === undefined) {
    return <div className="flex min-h-screen items-center justify-center bg-slate-950 text-sm text-slate-500">Loading…</div>;
  }

  if (user === null) {
    return <LoginPage notice={signedOutNote} onLoggedIn={() => { setSignedOutNote(null); refreshSession(); }} />;
  }

  const idleWatch = user.idle_timeout_minutes ? <IdleWatch minutes={user.idle_timeout_minutes} onSignedOut={signedOut} /> : null;
  if (user.role === "platform_admin") {
    return <PlatformAdminPage onLoggedOut={refreshSession} />;
  }

  return (
    <>
      {idleWatch}
      <CrmApp user={user} onLoggedOut={refreshSession} />
    </>
  );
}
