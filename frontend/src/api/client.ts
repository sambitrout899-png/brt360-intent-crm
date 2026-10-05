import type {
  ApprovalChainTemplate,
  AuditLogDetail,
  AuditLogEntry,
  DashboardSummary,
  IntentResponse,
  Offering,
  Opportunity,
  Order,
  Party,
  Party360,
  BillingAccount,
  Quote,
  Technician,
  TroubleTicket,
  WorkOrder,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
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
