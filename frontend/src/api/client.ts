import type { AuditLogDetail, AuditLogEntry, IntentResponse, Offering } from "./types";

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
