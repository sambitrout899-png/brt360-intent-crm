import type {
  ApprovalChainTemplate,
  AttributeConstraintRule,
  AuditLogDetail,
  AuditLogEntry,
  DashboardSummary,
  IdentityProvider,
  IntentResponse,
  Offering,
  Opportunity,
  Order,
  Party,
  Party360,
  BillingAccount,
  ProductAttributeDefinition,
  ProductAttributeValue,
  ProductSpecification,
  Quote,
  SlaClock,
  SlaPolicy,
  SlaSweepResult,
  Technician,
  Tenant,
  TestRun,
  TestRunSummary,
  TroubleTicket,
  WorkOrder,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

const TENANT_STORAGE_KEY = "brt360_active_tenant_slug";

export function getActiveTenantSlug(): string | null {
  try {
    return localStorage.getItem(TENANT_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setActiveTenantSlug(slug: string | null): void {
  try {
    if (slug) {
      localStorage.setItem(TENANT_STORAGE_KEY, slug);
    } else {
      localStorage.removeItem(TENANT_STORAGE_KEY);
    }
  } catch {
    // Private browsing or storage disabled - the backend falls back to
    // the "default" tenant when no header is sent, so this is harmless.
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const tenantSlug = getActiveTenantSlug();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (tenantSlug) {
    headers["X-Tenant-Slug"] = tenantSlug;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export function listTenants(): Promise<Tenant[]> {
  return request<Tenant[]>("/api/platform/tenants");
}

export function createTenant(payload: { name: string; slug: string; vertical: string }): Promise<Tenant> {
  return request<Tenant>("/api/platform/tenants", { method: "POST", body: JSON.stringify(payload) });
}

export function triggerTestRun(): Promise<TestRun> {
  return request<TestRun>("/api/admin/test-runs", { method: "POST" });
}

export function listTestRuns(): Promise<TestRunSummary[]> {
  return request<TestRunSummary[]>("/api/admin/test-runs");
}

export function getTestRun(id: number): Promise<TestRun> {
  return request<TestRun>(`/api/admin/test-runs/${id}`);
}

export function submitIntent(text: string): Promise<IntentResponse> {
  return request<IntentResponse>("/api/intents", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function listCatalog(): Promise<Offering[]> {
  return request<Offering[]>("/api/catalog");
}

export function offeringVersions(id: number): Promise<Offering[]> {
  return request<Offering[]>(`/api/catalog/${id}/versions`);
}

export function listAudit(): Promise<AuditLogEntry[]> {
  return request<AuditLogEntry[]>("/api/audit");
}

export function auditDetail(id: number): Promise<AuditLogDetail> {
  return request<AuditLogDetail>(`/api/audit/${id}`);
}

export function dashboardSummary(): Promise<DashboardSummary> {
  return request<DashboardSummary>("/api/dashboard/summary");
}

export function listParties(q?: string): Promise<Party[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return request<Party[]>(`/api/parties${qs}`);
}

export function party360(id: number): Promise<Party360> {
  return request<Party360>(`/api/parties/${id}/360`);
}

export function listOpportunities(stage?: string): Promise<Opportunity[]> {
  const qs = stage ? `?stage=${encodeURIComponent(stage)}` : "";
  return request<Opportunity[]>(`/api/opportunities${qs}`);
}

export function listQuotes(status?: string): Promise<Quote[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<Quote[]>(`/api/quotes${qs}`);
}

export function quoteDetail(id: number): Promise<Quote> {
  return request<Quote>(`/api/quotes/${id}`);
}

export function listOrders(status?: string): Promise<Order[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<Order[]>(`/api/orders${qs}`);
}

export function orderDetail(id: number): Promise<Order> {
  return request<Order>(`/api/orders/${id}`);
}

export function listBillingAccounts(): Promise<BillingAccount[]> {
  return request<BillingAccount[]>("/api/billing-accounts");
}

export function listTickets(status?: string, severity?: string): Promise<TroubleTicket[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (severity) params.set("severity", severity);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return request<TroubleTicket[]>(`/api/tickets${qs}`);
}

export function ticketDetail(id: number): Promise<TroubleTicket> {
  return request<TroubleTicket>(`/api/tickets/${id}`);
}

export function listWorkOrders(status?: string): Promise<WorkOrder[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<WorkOrder[]>(`/api/work-orders${qs}`);
}

export function listTechnicians(): Promise<Technician[]> {
  return request<Technician[]>("/api/technicians");
}

export function listApprovalChains(): Promise<ApprovalChainTemplate[]> {
  return request<ApprovalChainTemplate[]>("/api/admin/approval-chains");
}

export function createApprovalChain(payload: {
  name: string;
  segment: string | null;
  min_discount_pct: number;
  max_discount_pct: number;
  tiers: { approver_tier: string }[];
}): Promise<ApprovalChainTemplate> {
  return request<ApprovalChainTemplate>("/api/admin/approval-chains", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deactivateApprovalChain(id: number): Promise<ApprovalChainTemplate> {
  return request<ApprovalChainTemplate>(`/api/admin/approval-chains/${id}/deactivate`, { method: "PATCH" });
}

export function deleteApprovalChain(id: number): Promise<void> {
  return request<void>(`/api/admin/approval-chains/${id}`, { method: "DELETE" });
}

export function listSlaPolicies(): Promise<SlaPolicy[]> {
  return request<SlaPolicy[]>("/api/admin/sla-policies");
}

export function createSlaPolicy(payload: { clock_type: string; duration_hours: number; on_breach_action: string }): Promise<SlaPolicy> {
  return request<SlaPolicy>("/api/admin/sla-policies", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateSlaPolicy(id: number): Promise<SlaPolicy> {
  return request<SlaPolicy>(`/api/admin/sla-policies/${id}/deactivate`, { method: "PATCH" });
}

export function deleteSlaPolicy(id: number): Promise<void> {
  return request<void>(`/api/admin/sla-policies/${id}`, { method: "DELETE" });
}

export function listSlaClocks(status?: string): Promise<SlaClock[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<SlaClock[]>(`/api/admin/sla-clocks${qs}`);
}

export function processDueSlaClocks(): Promise<SlaSweepResult> {
  return request<SlaSweepResult>("/api/admin/sla/process-due", { method: "POST" });
}

export function listProductSpecifications(): Promise<ProductSpecification[]> {
  return request<ProductSpecification[]>("/api/admin/product-specifications");
}

export function listAttributeDefinitions(): Promise<ProductAttributeDefinition[]> {
  return request<ProductAttributeDefinition[]>("/api/admin/attribute-definitions");
}

export function createAttributeDefinition(payload: {
  name: string;
  data_type: string;
  unit: string | null;
  allowed_values: string | null;
}): Promise<ProductAttributeDefinition> {
  return request<ProductAttributeDefinition>("/api/admin/attribute-definitions", { method: "POST", body: JSON.stringify(payload) });
}

export function deleteAttributeDefinition(id: number): Promise<void> {
  return request<void>(`/api/admin/attribute-definitions/${id}`, { method: "DELETE" });
}

export function listAttributeValues(productSpecificationId?: number): Promise<ProductAttributeValue[]> {
  const qs = productSpecificationId != null ? `?product_specification_id=${productSpecificationId}` : "";
  return request<ProductAttributeValue[]>(`/api/admin/attribute-values${qs}`);
}

export function setAttributeValue(payload: {
  product_specification_id: number;
  attribute_definition_id: number;
  value: string;
}): Promise<ProductAttributeValue> {
  return request<ProductAttributeValue>("/api/admin/attribute-values", { method: "PUT", body: JSON.stringify(payload) });
}

export function listAttributeConstraintRules(): Promise<AttributeConstraintRule[]> {
  return request<AttributeConstraintRule[]>("/api/admin/attribute-constraint-rules");
}

export function createAttributeConstraintRule(payload: {
  if_product_spec_id: number;
  if_attribute_id: number;
  operator: string;
  threshold: string;
  rule_type: string;
  then_product_spec_id: number;
}): Promise<AttributeConstraintRule> {
  return request<AttributeConstraintRule>("/api/admin/attribute-constraint-rules", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateAttributeConstraintRule(id: number): Promise<void> {
  return request<void>(`/api/admin/attribute-constraint-rules/${id}/deactivate`, { method: "PATCH" });
}

export function deleteAttributeConstraintRule(id: number): Promise<void> {
  return request<void>(`/api/admin/attribute-constraint-rules/${id}`, { method: "DELETE" });
}

export function listIdentityProviders(): Promise<IdentityProvider[]> {
  return request<IdentityProvider[]>("/api/admin/identity-providers");
}

export function createIdentityProvider(name: string): Promise<IdentityProvider> {
  return request<IdentityProvider>("/api/admin/identity-providers", { method: "POST", body: JSON.stringify({ name }) });
}

export function deactivateIdentityProvider(id: number): Promise<IdentityProvider> {
  return request<IdentityProvider>(`/api/admin/identity-providers/${id}/deactivate`, { method: "PATCH" });
}

export function deleteIdentityProvider(id: number): Promise<void> {
  return request<void>(`/api/admin/identity-providers/${id}`, { method: "DELETE" });
}
