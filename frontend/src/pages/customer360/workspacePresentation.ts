import type { C360View, C360ThemeTokens, C360Widget, C360Audience } from "../../api/c360Types";

/** The Customer 360 page's tabs - the one definition the live page and the
 * Configure 360 studio both read, so what is designed is what is published.
 * `component` is the standard component whose audience rule shows or hides
 * the tab's built-in content; `domains` are where panes of those data areas
 * land unless a pane names its tab; `builtIn` lists the standard sections. */
export const PAGE_TABS = [
  { id: "overview", label: "Overview", component: null, domains: ["custom"], builtIn: ["account_information", "service_accounts", "orders", "tickets"] },
  { id: "services", label: "Services & network", component: "service_accounts", domains: ["network", "inventory"], builtIn: ["service_accounts"] },
  { id: "orders", label: "Orders", component: "orders", domains: [], builtIn: ["orders"] },
  { id: "sales", label: "Sales", component: "sales", domains: ["sales"], builtIn: ["sales"] },
  { id: "billing", label: "Billing", component: "billing", domains: ["billing"], builtIn: ["billing"] },
  { id: "support", label: "Requests", component: "tickets", domains: ["service"], builtIn: ["tickets"] },
  { id: "details", label: "Profile", component: "profile", domains: [], builtIn: ["profile"] },
  { id: "account", label: "Hierarchy", component: "hierarchy", domains: [], builtIn: ["hierarchy"] },
  { id: "activity", label: "Activity", component: "activity", domains: [], builtIn: ["activity"] },
] as const;
export type PageTabId = typeof PAGE_TABS[number]["id"];
/** @deprecated the tabs panes reach through their domain; use PAGE_TABS */
export const WORKSPACE_TABS = PAGE_TABS.filter(tab => tab.domains.length > 0);
export type ConfiguredTab = PageTabId;
type Pane = C360View["panes"][number];

export function audienceLabel(rule?: C360Audience): string {
  if (!rule) return "All customers";
  if (!rule.enabled || !rule.customer_types.length || !rule.segments.length) return "Hidden";
  const type = rule.customer_types.length === 2 ? "Accounts + Prospects" : rule.customer_types[0] === "account" ? "Accounts" : "Prospects";
  return `${type} · ${rule.segments.join(" + ")}`;
}

export const STANDARD_COMPONENTS: Record<string, string> = {
  account_information: "Account information", service_accounts: "Service accounts and installed assets",
  orders: "Orders", sales: "Sales pipeline", billing: "Billing accounts and bill profiles",
  tickets: "Service requests", insights: "Account alerts", health: "Customer health score",
  profile: "Customer profile", hierarchy: "Hierarchy and preferences", activity: "Account activity",
};

export function componentVisible(view: C360View | null, key: string | null | undefined): boolean {
  return !key || view?.components?.[key] !== false;
}

export function tabOfDomain(domain: string): PageTabId {
  return PAGE_TABS.find(tab => (tab.domains as readonly string[]).includes(domain))?.id ?? "overview";
}

/** The tab a pane is shown on: its own choice, else the tab of its domain. */
export function paneTab(pane: { domain: string; tab?: string | null }): PageTabId {
  return PAGE_TABS.find(tab => tab.id === pane.tab)?.id ?? tabOfDomain(pane.domain);
}

export function panesForTab(view: C360View | null, tab: string): Pane[] {
  return (view?.panes ?? []).filter(p => paneTab(p) === tab);
}

/** Tabs the customer sees: the summary always; any other when its standard
 * content is visible to this customer or a published pane sits on it. */
export function visibleTabs(view: C360View | null) {
  return PAGE_TABS.filter(tab => tab.id === "overview" || componentVisible(view, tab.component) || panesForTab(view, tab.id).length > 0);
}

/** Reserve configured lists even when excluded by audience, so their native
 * fallback cannot reappear and defeat the administrator's visibility rule. */
export function workspaceCoverage(view: C360View | null, tab: string): Set<string> {
  const panes = (view?.layout?.panes ?? []).filter(p => paneTab(p) === tab);
  const covered = coveredRecords(panes);
  for (const key of Object.keys(STANDARD_COMPONENTS)) if (!componentVisible(view, key)) covered.add(key);
  if (!componentVisible(view, "sales")) for (const key of ["leads", "opportunities", "quotes"]) covered.add(key);
  return covered;
}

/** A configured record list replaces its built-in counterpart, never another
 * published widget. Charts and metrics do not replace operational lists. */
export function coveredRecords(panes: { widgets: Pick<C360Widget, "type" | "metric" | "dataset">[] }[]): Set<string> {
  const covered = new Set<string>();
  for (const pane of panes) for (const widget of pane.widgets) {
    if (widget.type === "records" && Array.isArray(widget.metric.sections)) {
      for (const section of widget.metric.sections) if (typeof section === "string") covered.add(section);
    } else if (widget.type === "table" && widget.dataset) {
      covered.add(widget.dataset);
      if (widget.dataset === "assets") covered.add("service_accounts");
    }
  }
  return covered;
}

export type SectionState = "shown" | "secondary" | "replaced" | "hidden";
type PaneLike = { domain: string; tab?: string | null; widgets: Pick<C360Widget, "type" | "metric" | "dataset">[] };

/** How one standard section of a tab renders, given every pane of the layout
 * and who it is visible to. The live page and the studio canvas both use it. */
export function sectionState(tab: string, key: string, panes: PaneLike[], visible: (key: string) => boolean = () => true, shownPanes: PaneLike[] = panes): SectionState {
  if (!visible(key)) return "hidden";
  const onTab = panes.filter(p => paneTab(p) === tab);
  const covered = coveredRecords(onTab);
  if (tab === "sales") return ["leads", "opportunities", "quotes"].every(s => covered.has(s)) ? "replaced" : "shown";
  // Asset and billing workbenches carry actions a configured list does not,
  // so they stay available (collapsed) below the configured panes.
  if (tab === "services") return covered.has("service_accounts") ? "secondary" : "shown";
  if (tab === "billing") return shownPanes.some(p => paneTab(p) === tab) ? "secondary" : "shown";
  if (tab === "overview" || tab === "support") return covered.has(key) ? "replaced" : "shown";
  return "shown";
}

export const DEFAULT_WORKSPACE_THEME: C360ThemeTokens = {
  preset: "midnight", label: "Midnight", accent: "#38bdf8", bg: "#020617", surface: "rgba(15,23,42,0.72)",
  border: "#1e293b", text: "#e2e8f0", muted: "#94a3b8", hero: "linear-gradient(135deg,#0f172a,#082f49)",
  card_style: "glass", density: "comfortable", header_style: "hero",
};

/** Theme variables and look attributes for the element that frames the page
 * (the live page and the studio preview use the same frame). */
export function workspaceFrame(view: C360View | null) {
  return {
    style: workspaceTheme(view?.theme),
    "data-density": view?.theme.density ?? "comfortable",
    "data-card-style": view?.theme.card_style ?? "glass",
    "data-header-style": view?.theme.header_style ?? "hero",
    "data-layout-version": view?.version,
  };
}

export function workspaceTheme(theme?: C360ThemeTokens): Record<string, string> {
  const t = theme ?? DEFAULT_WORKSPACE_THEME;
  return {
    "--c-bg": t.bg, "--c-surface": t.surface, "--c-border": t.border, "--c-text": t.text,
    "--c-muted": t.muted, "--c-accent": t.accent, "--c-hero": t.hero,
    "--account-card-bg": t.card_style === "outline" ? "transparent" : t.card_style === "solid" ? t.surface.replace(/[\d.]+\)$/, "1)") : t.surface,
    "--account-gap": t.density === "compact" ? "12px" : "16px",
    "--account-row-padding": t.density === "compact" ? "8px 12px" : "11px 14px",
    "--account-header-padding": t.header_style === "compact" ? "14px 18px" : "20px 24px",
  };
}
