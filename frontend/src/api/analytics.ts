/** Analytics & Reports (backend app/analytics, /api/analytics). */
import { API_BASE, getAccessToken, getWorkContext, request } from "./client";

export type FieldKind = "dimension" | "number" | "date" | "bool";
export type Fmt = "money" | "pct" | "days" | "hours" | "gb" | "ms" | null | undefined;

export interface AField {
  key: string;
  label: string;
  kind: FieldKind;
  group: string;
  fmt?: Fmt;
  values?: string[] | null;
  measure: boolean;
  custom: boolean;
  computed: boolean;
}
export interface AEntity {
  key: string;
  label: string;
  description: string;
  date: string;
  money: string | null;
  record_type: string | null;
  fields: AField[];
}
export interface ACatalog {
  entities: AEntity[];
  operators: string[];
  grains: string[];
  aggregations: string[];
  calculations: string[];
  charts: string[];
  presets: { key: string; label: string; entity: string; grain: string; fmt?: string }[];
  platform_health?: boolean; // the caller may see Platform health (the operator's admins)
}

export interface HealthKpi {
  key: string;
  label: string;
  value: number | null;
  fmt?: Fmt;
  hint: string;
  tone: "good" | "warn" | "bad" | "info";
}
export interface HealthChart {
  key: string;
  title: string;
  labels: string[];
  series: { name: string; values: (number | null)[] }[];
  fmt?: Fmt;
}
export interface HealthService {
  job: string;
  name: string;
  up: boolean | null;
  requests_24h: number | null;
  p95_ms: number | null;
  error_pct: number | null;
}
export interface PlatformHealth {
  available: boolean;
  reason?: string;
  generated_at: string;
  hours: number;
  kpis: HealthKpi[];
  notes: Narrative[];
  services: HealthService[];
  charts: HealthChart[];
  failing_routes: { method: string; route: string; status: string; count: number }[];
  busiest_routes: { route: string; count: number }[];
  ai_tokens: { tenant: string; tokens: number }[];
}

export interface Dim {
  field: string;
  grain?: string;
  bins?: number;
  label?: string;
}
export interface Meas {
  field?: string;
  agg: string;
  calc?: string;
  label?: string;
}
export interface Filter {
  field: string;
  op: string;
  value?: unknown;
}
export type ChartType =
  | "bar" | "hbar" | "stacked" | "stacked100" | "line" | "area" | "donut" | "pie" | "table" | "pivot" | "kpi" | "heatmap"
  | "scatter" | "funnel" | "treemap" | "combo";

export interface ReportSpec {
  entity: string;
  dimensions: Dim[];
  measures: Meas[];
  filters: Filter[];
  sort?: { key: string; dir: "asc" | "desc" }[];
  top?: { n: number; dir?: "asc" | "desc"; other?: boolean } | null;
  limit?: number;
  chart?: ChartType;
  title?: string;
  palette?: string;
  options?: { labels?: boolean; legend?: boolean; smooth?: boolean };
}

export interface ACol {
  key: string;
  label: string;
  role: "dimension" | "measure";
  field?: string;
  grain?: string | null;
  bins?: number | null;
  edges?: number[] | null;
  kind?: FieldKind;
  agg?: string;
  calc?: string | null;
  fmt?: Fmt;
}
export interface Narrative {
  tone: "good" | "bad" | "warn" | "info";
  text: string;
}
export interface QueryResult {
  entity: string;
  entity_label: string;
  columns: ACol[];
  rows: Record<string, unknown>[];
  groups: number;
  scanned: number;
  truncated: boolean;
  totals: Record<string, number | null>;
  record_type: string | null;
  narrative?: Narrative[];
}
export interface RecordsResult {
  entity: string;
  entity_label: string;
  record_type: string | null;
  columns: { key: string; label: string; kind: FieldKind; fmt?: Fmt }[];
  rows: Record<string, unknown>[];
  total: number;
  offset: number;
  truncated: boolean;
}
export interface Drill {
  field: string;
  grain?: string | null;
  value: unknown;
  edges?: number[] | null;
}

export interface SavedReport {
  id: number;
  kind: "report" | "dashboard";
  name: string;
  description: string;
  spec: ReportSpec & { tiles?: { report_id: number; w: number }[]; columns?: number };
  shared: boolean;
  mine: boolean;
  can_edit: boolean;
  updated_at: string | null;
}

export interface Kpi {
  key: string;
  label: string;
  value: number | null;
  fmt?: Fmt;
  hint: string;
  lower_is_better: boolean;
}
export interface SeriesChart {
  key: string;
  title: string;
  kind: string;
  labels: string[];
  series: { name: string; values: number[] }[];
  fmt?: Fmt;
}
export interface Scorecard {
  groups: { title: string; kpis: Kpi[] }[];
  charts: SeriesChart[];
  notes: Narrative[];
  generated_at: string;
}

export interface ForecastResult {
  label: string;
  grain: string;
  fmt?: Fmt;
  history: { period: string; value: number; fitted: number }[];
  forecast: { period: string; value: number; lower: number; upper: number }[];
  method: string;
  accuracy: { holdout: number; mape?: number | null; mae?: number; skill_vs_naive?: number | null };
  trend: { per_period: number; pct_of_mean: number | null; r2: number; direction: "up" | "down" | "flat" };
  anomalies: { period: string; value: number; expected: number; z: number; kind: "spike" | "drop" }[];
  next: number | null;
  horizon_total: number;
  points: number;
  confidence: "low" | "medium" | "high";
  partial_period?: string | null;
  note?: string;
}

export interface ChurnResult {
  method: string;
  metrics: Record<string, number | null>;
  importance: { feature: string; label: string; share: number; weight: number }[];
  customers: {
    party_id: number; name: string; segment: string | null; mrr: number; services: number; probability: number;
    band: "High" | "Medium" | "Low"; drivers: string[]; tenure_months: number; clv: number; expected_lifetime_months: number;
  }[];
  summary: { customers: number; high: number; medium: number; mrr_at_risk: number; expected_mrr_loss: number; total_clv: number };
}
export interface WinResult {
  method: string;
  metrics: Record<string, number | null>;
  importance: { feature: string; share: number; weight: number }[];
  opportunities: {
    opportunity_id: number; name: string; customer: string | null; stage: string; value: number; probability: number;
    expected_value: number; drivers: string[]; age_days: number;
  }[];
  summary: { open: number; pipeline: number; weighted_pipeline: number; expected_wins: number };
}
export interface SegmentsResult {
  segments: {
    id: number; name: string; size: number; share: number; mrr: number; action: string;
    averages: Record<string, number>; profile: Record<string, number>; members: { party_id: number; name: string; mrr: number }[];
  }[];
  method: string;
  silhouette?: number;
  customers?: number;
  note?: string;
}
export interface OfferResult {
  rules: { if: string; then: string; support: number; confidence: number; lift: number; customers: number }[];
  recommendations: { party_id: number; name: string; segment: string | null; owns: string[]; offer: string; confidence: number; reason: string }[];
  method?: string;
  note?: string;
}
export interface UsageAlerts {
  alerts: { party_id: number; name: string; z: number; recent_daily: number; baseline_daily: number; increase_pct: number | null; reasons: string[]; risk: string }[];
  customers_checked: number;
  method: string;
}
export interface Explanation {
  headline: string;
  insights: string[];
  actions: string[];
  risks: string[];
  by: string;
  note?: string;
}

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const analyticsCatalog = () => request<ACatalog>("/api/analytics/catalog");
export const runQuery = (spec: ReportSpec) => post<QueryResult>("/api/analytics/query", spec);
export const drillRecords = (body: { entity: string; columns?: string[]; filters?: Filter[]; drill?: Drill[]; sort?: { key: string; dir: string }; offset?: number; limit?: number }) =>
  post<RecordsResult>("/api/analytics/records", body);
export const askAnalytics = (text: string) =>
  post<{ spec: ReportSpec; understood: string[]; by: string; result: QueryResult }>("/api/analytics/ask", { text });
export const explainReport = (title: string, facts: string[], table: Record<string, unknown>[] = []) =>
  post<Explanation>("/api/analytics/explain", { title, facts, table });
export const telecomScorecard = (months = 12) => request<Scorecard>(`/api/analytics/telecom?months=${months}`);
// background = the tab's own refresh, which doesn't count as the user being active (idle timeout)
export const platformHealth = (hours = 6, background = false) =>
  request<PlatformHealth>(`/api/analytics/platform-health?hours=${hours}`, background ? { headers: { "X-BRT-Background": "1" } } : undefined);
export const runForecast =(body: Record<string, unknown>) => post<ForecastResult>("/api/analytics/forecast", body);
export const predictChurn = () => request<ChurnResult>("/api/analytics/predict/churn");
export const predictWin = () => request<WinResult>("/api/analytics/predict/win");
export const predictSegments = () => request<SegmentsResult>("/api/analytics/predict/segments");
export const predictNextOffer = () => request<OfferResult>("/api/analytics/predict/next-offer");
export const predictUsageAlerts = () => request<UsageAlerts>("/api/analytics/predict/usage-alerts");
export const listReports = (kind?: "report" | "dashboard") => request<SavedReport[]>(`/api/analytics/reports${kind ? `?kind=${kind}` : ""}`);
export const saveReport = (r: { id?: number; kind: "report" | "dashboard"; name: string; description?: string; spec: unknown; shared?: boolean }) =>
  request<SavedReport>(r.id ? `/api/analytics/reports/${r.id}` : "/api/analytics/reports", {
    method: r.id ? "PUT" : "POST",
    body: JSON.stringify({ kind: r.kind, name: r.name, description: r.description ?? "", spec: r.spec, shared: r.shared ?? true }),
  });
export const deleteReport = (id: number) => request<void>(`/api/analytics/reports/${id}`, { method: "DELETE" });
export const starterDashboard = () => post<SavedReport>("/api/analytics/dashboards/starter", {});

/** Downloads a CSV - the report's table, or every record behind it. */
export async function exportCsv(kind: "query" | "records", query: unknown, name: string): Promise<void> {
  const token = getAccessToken();
  const ctx = getWorkContext();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (ctx.positionId) headers["X-Position-Id"] = String(ctx.positionId);
  if (ctx.scope) headers["X-Data-Scope"] = ctx.scope;
  const res = await fetch(`${API_BASE}/api/analytics/export`, { method: "POST", headers, body: JSON.stringify({ kind, query, name }) });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text().catch(() => "")}`);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name || "report"}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
