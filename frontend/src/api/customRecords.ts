import { request } from "./client";

/** Custom record types (Admin -> Record Types) and their records (the Records page). */
export interface RecordTypeField {
  id: number;
  key: string;
  label: string;
  data_type: string;
  allowed_values: string[];
  required: boolean;
  default_value: string | null;
  section: string | null;
  help_text: string | null;
}
export interface RecordType {
  id: number;
  key: string;
  /** "x_<key>" - what its custom fields are filed under */
  entity_type: string;
  label: string;
  plural_label: string;
  description: string;
  icon: string;
  number_prefix: string;
  parent_entity_type: string | null;
  parent_label: string | null;
  parent_required: boolean;
  is_active: boolean;
  record_count: number;
  fields: RecordTypeField[];
}
export interface RecordTypeInput {
  label: string;
  plural_label?: string | null;
  key?: string | null;
  description?: string;
  icon?: string | null;
  number_prefix?: string | null;
  parent_entity_type?: string | null;
  parent_required?: boolean;
  is_active?: boolean;
}
export interface ParentRef {
  entity_type: string;
  id: number;
  title: string;
  /** set when the parent is another custom record */
  custom_key: string | null;
}
export interface CustomRecord {
  id: number;
  number: string;
  name: string;
  parent: ParentRef | null;
  values: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  brt_id?: string;
  created_by?: string | null;
  children?: { key: string; label: string; count: number }[];
}

/** The server's own words from a failed request ("400 Bad Request: {"detail": "..."}" -> "..."). */
export function problem(e: unknown, fallback = "Something went wrong."): string {
  const text = e instanceof Error ? e.message : "";
  const body = text.match(/^\d{3} [^:]*: ([\s\S]*)$/)?.[1];
  if (body) {
    try {
      const detail = (JSON.parse(body) as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? String(d)).join("; ");
    } catch {
      return body || fallback;
    }
  }
  return text || fallback;
}

// ---------- admin

export function listRecordTypes(): Promise<{ types: RecordType[]; parent_options: { value: string; label: string }[] }> {
  return request("/api/admin/record-types");
}

export function createRecordType(body: RecordTypeInput): Promise<RecordType> {
  return request<RecordType>("/api/admin/record-types", { method: "POST", body: JSON.stringify(body) });
}

export function updateRecordType(key: string, body: Partial<RecordTypeInput>): Promise<RecordType> {
  return request<RecordType>(`/api/admin/record-types/${encodeURIComponent(key)}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteRecordType(key: string): Promise<void> {
  return request<void>(`/api/admin/record-types/${encodeURIComponent(key)}`, { method: "DELETE" });
}

// ---------- records

export function activeRecordTypes(): Promise<RecordType[]> {
  return request<RecordType[]>("/api/custom-records/types");
}

export function listCustomRecords(key: string, opts: { q?: string; parentId?: number; offset?: number } = {}): Promise<{ type: RecordType; total: number; items: CustomRecord[] }> {
  const qs = new URLSearchParams();
  if (opts.q) qs.set("q", opts.q);
  if (opts.parentId != null) qs.set("parent_id", String(opts.parentId));
  if (opts.offset) qs.set("offset", String(opts.offset));
  return request(`/api/custom-records/${encodeURIComponent(key)}${qs.toString() ? `?${qs}` : ""}`);
}

export function getCustomRecord(key: string, id: number): Promise<{ type: RecordType; record: CustomRecord }> {
  return request(`/api/custom-records/${encodeURIComponent(key)}/${id}`);
}

export function createCustomRecord(key: string, body: { name: string; parent_id?: number | null; fields: Record<string, unknown> }): Promise<CustomRecord> {
  return request<CustomRecord>(`/api/custom-records/${encodeURIComponent(key)}`, { method: "POST", body: JSON.stringify(body) });
}

export function updateCustomRecord(key: string, id: number, body: { name?: string; parent_id?: number | null; fields?: Record<string, unknown> }): Promise<CustomRecord> {
  return request<CustomRecord>(`/api/custom-records/${encodeURIComponent(key)}/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteCustomRecord(key: string, id: number): Promise<void> {
  return request<void>(`/api/custom-records/${encodeURIComponent(key)}/${id}`, { method: "DELETE" });
}

export function parentChoices(key: string, q = ""): Promise<{ id: number; title: string }[]> {
  return request(`/api/custom-records/parents/${encodeURIComponent(key)}${q ? `?q=${encodeURIComponent(q)}` : ""}`);
}
