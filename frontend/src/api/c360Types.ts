// Configurable Customer 360 (INT-33 "Configure 360") - see backend app/c360/.

export type C360Width = "third" | "half" | "two_thirds" | "full";
export type C360WidgetType = "kpi" | "line" | "area" | "bar" | "donut" | "gauge" | "table" | "records" | "insights";

export interface C360Audience {
  enabled: boolean;
  customer_types: ("account" | "prospect")[];
  segments: ("B2B" | "B2C")[];
}

export interface C360Widget {
  audience?: C360Audience;
  id: string;
  recipe: string | null;
  type: C360WidgetType;
  title: string;
  dataset: string | null;
  source: string | null;
  metric: Record<string, unknown>;
  span?: number;
}

export interface C360Pane {
  audience?: C360Audience;
  id: string;
  title: string;
  domain: string;
  icon: string;
  color: string;
  width: C360Width;
  collapsed: boolean;
  /** the page tab it sits on; null = the tab of its domain */
  tab?: string | null;
  widgets: C360Widget[];
}

export interface C360ThemeSettings {
  preset: string;
  accent: string | null;
  card_style: "glass" | "solid" | "outline";
  density: "comfortable" | "compact";
  header_style: "hero" | "compact";
}

export interface C360Layout {
  component_audiences?: Record<string, C360Audience>;
  version: number;
  template: string | null;
  theme: C360ThemeSettings;
  header: { kpis: string[]; show_health: boolean };
  panes: C360Pane[];
}

export type C360Data =
  | { kind: "value"; value: number; count: number; max?: number }
  | { kind: "series"; points: { label: string; value: number }[]; total: number }
  | { kind: "categories"; items: { label: string; value: number }[]; total: number }
  | { kind: "rows"; columns: { key: string; label: string; type: string }[]; rows: Record<string, unknown>[]; total: number }
  | { kind: "none" };

export interface C360RenderedWidget extends C360Widget {
  format: "money" | "number" | "percent" | null;
  data: C360Data | null;
  status: "ok" | "empty" | "not_connected" | "error";
  source_label: string | null;
  message: string | null;
  domain?: string;
}

export interface C360ThemeTokens extends C360ThemeSettings {
  label: string;
  bg: string;
  surface: string;
  border: string;
  text: string;
  muted: string;
  accent: string;
  hero: string;
}

export interface C360Insight {
  tone: "risk" | "opportunity" | "action" | "info" | "good";
  title: string;
  detail: string;
  action: { label: string; text: string } | null;
}

export interface C360View {
  audience?: { customer_type: string; segment: string | null };
  components?: Record<string, boolean>;
  party: { id: number; name: string; party_type: string; email: string | null; phone: string | null; created_at: string };
  layout: C360Layout;
  theme: C360ThemeTokens;
  header: { kpis: C360RenderedWidget[]; health: { score: number; label: string; reasons: string[] } | null };
  insights: C360Insight[];
  panes: (Omit<C360Pane, "widgets"> & { widgets: C360RenderedWidget[] })[];
  sources: { dataset: string; source: string; label: string; rows: number; error: string | null }[];
  version?: number;
}

export interface C360Endpoint {
  path: string;
  records_path: string;
  field_map: Record<string, string>;
}

export interface C360Connector {
  id: number;
  key: string;
  name: string;
  domain: string;
  kind: "rest" | "demo";
  base_url: string;
  auth_type: "none" | "bearer" | "api_key" | "basic";
  auth_header: string;
  secret_set: boolean;
  secret_hint: string;
  endpoints: Record<string, C360Endpoint>;
  is_active: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string;
}

export interface C360ConnectorInput {
  name: string;
  domain: string;
  kind: "rest" | "demo";
  base_url: string;
  auth_type: string;
  auth_header: string;
  secret?: string | null;
  endpoints: Record<string, C360Endpoint>;
  is_active: boolean;
}

export interface C360ConnectorDraft {
  key: string;
  name: string;
  domain: string;
  kind: "rest" | "demo";
  base_url: string;
  endpoints: Record<string, C360Endpoint>;
}

export interface C360DatasetDef {
  key: string;
  label: string;
  domain: string;
  internal: boolean;
  /** BRT 360's Network Inventory can answer it (when the tenant records its network there) */
  inventory?: boolean;
  description: string;
  date_field: string | null;
  fields: { key: string; label: string; type: string }[];
}

export interface C360Catalog {
  domains: { key: string; label: string; icon: string; color: string }[];
  datasets: C360DatasetDef[];
  recipes: { id: string; title: string; domain: string; type: C360WidgetType; dataset: string | null; description: string }[];
  widget_types: C360WidgetType[];
  widths: C360Width[];
  themes: { key: string; label: string; bg: string; surface: string; border: string; text: string; muted: string; accent: string; hero: string }[];
  card_styles: string[];
  densities: string[];
  templates: { key: string; label: string; description: string }[];
  header_kpis: string[];
  record_sections: string[];
}

export interface C360Studio {
  layout: C360Layout;
  version: number;
  catalog: C360Catalog;
  connectors: C360Connector[];
  history: { version: number; summary: string; changed_by: string; created_at: string }[];
  suggestions: string[];
  ai_engine: "ai" | "rules";
  brand_color: string | null;
  template_layouts: Record<string, C360Layout>;
  recipe_widgets: Record<string, C360Widget>;
  domain_datasets: Record<string, string[]>;
  default_paths: Record<string, string>;
}

export interface C360Plan {
  layout: C360Layout;
  changes: string[];
  notes: string[];
  connectors: C360ConnectorDraft[];
  undo: boolean;
  engine: "ai" | "rules";
  understood: boolean;
  reply: string;
  suggestions: string[];
}

export interface C360MappingSuggestion {
  dataset: string;
  records_path: string;
  mapping: Record<string, { source: string; confidence: number }>;
  field_map: Record<string, string>;
  unmapped: string[];
  their_fields: string[];
  records_found: number;
  preview: Record<string, unknown>[];
  engine: "ai" | "rules";
}

export interface C360TestResult {
  ok: boolean;
  message: string;
  dataset: string;
  customer: string;
  records_found?: number;
  preview?: Record<string, unknown>[];
  raw_sample?: unknown;
  suggestion?: C360MappingSuggestion | null;
}

/** One customer's network from Network Inventory (backend: inventory_service.customer_network). */
export interface CustomerNetworkResource {
  id: number; item_id: number; brt_id: string; resource_type: string; type_name: string; kind: string; identifier: string;
  status: string; oper_status: string; site: string | null; path: string; location_path: string; order_id: number | null;
  service_item_id: number | null; model: string; vendor: string; serial: string; trouble: string[];
}
export interface CustomerNetworkService {
  id: number; item_id: number; brt_id: string; service: string; product: string; status: string; oper_status: string;
  location: string | null; path: string; resources: number; order_id: number | null; at_risk: "yes" | "no"; trouble: string[];
  crm_assets: number[]; address: string; uses: CustomerNetworkResource[];
}
export interface CustomerNetwork {
  in_use: boolean;
  services: CustomerNetworkService[];
  resources: CustomerNetworkResource[];
  elements: { id: number; element: string; type_name: string; site: string | null; status: string; utilization_pct: number | null; availability_pct: number | null }[];
  devices: { device_id: string; item_id: number; type_name: string; model: string; site: string | null; status: string; serial: string; firmware: string }[];
  at_risk: { service: string; item_id: number; order_id: number | null; reasons: string[] }[];
}
