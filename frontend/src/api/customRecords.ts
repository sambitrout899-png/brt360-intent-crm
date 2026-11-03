import { request } from "./client";

/** Custom record types (Admin -> Record Types) and their records (the Records page). Writes run as intents
 *  (INT-151..157) on the server, so flows, assignment rules and the audit trail apply to them. */
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
  /** lookup fields: the record type they point to */
  target?: string | null;
  target_label?: string | null;
}
export interface LifecycleStateDef {
  code: string;
  label: string;
  editable: boolean;
  is_terminal: boolean;
}
export interface LifecycleMove {
  from_state: string;
  to_state: string;
  action: string;
  allowed_roles: string;
}
export interface Relationship {
  id: number;
  key: string;
  from_entity_type: string;
  to_entity_type: string;
  from_label: string;
  to_label: string;
  label: string;
  reverse_label: string;
  description: string;
  links: number;
  /** seen from one side: what that side calls the other, and its type */
  name?: string;
  other_entity_type?: string;
  other_label?: string;
}
export interface RecordType {
  id: number;
  key: string;
  /** "x_<key>" - what its custom fields, state model, page and flows are filed under */
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
  name_label: string;
  list_fields: string[];
  fields: RecordTypeField[];
  states: LifecycleStateDef[];
  transitions: LifecycleMove[];
  state_model_configured: boolean;
  relationships: Relationship[];
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
  name_label?: string | null;
  list_fields?: string[];
}
export interface ParentRef {
  entity_type: string;
  id: number;
  title: string;
  /** set when the parent is another custom record */
  custom_key: string | null;
}
export interface RelatedItem {
  id: number;
  title: string;
  state: string;
  custom_key: string | null;
}
export interface RelatedSection {
  kind: "children" | "lookup" | "link";
  key: string;
  label: string;
  entity_type: string;
  type_label: string;
  relationship?: string;
  field?: string;
  can_add?: boolean;
  items: RelatedItem[];
}
export interface RecordMove {
  action: string;
  to_state: string;
  to_label: string;
  intent_type: string | null;
  passed?: string;
}
export interface CustomRecord {
  id: number;
  number: string;
  name: string;
  parent: ParentRef | null;
  values: Record<string, unknown>;
  /** lookup fields: the record each points to */
  display: Record<string, { id: number; title: string; entity_type: string; custom_key: string | null } | null>;
  state: string;
  state_label: string;
  editable: boolean;
  owner: string | null;
  created_at: string;
  updated_at: string;
  brt_id?: string;
  created_by?: string | null;
  children?: { key: string; label: string; count: number }[];
  related?: RelatedSection[];
  transitions?: RecordMove[];
  history?: { from_state: string | null; to_state: string; source: string; at: string }[];
}
/** The type's page from the Page Designer, ready to render. */
export interface RecordFormField {
  key: string;
  label: string;
  type: string;
  values: string[];
  mandatory: boolean;
  readonly: boolean;
  help: string;
  core: boolean;
  value: unknown;
  span: string | null;
  kind?: "button" | "text";
  intent?: string;
  style?: string;
  text?: string;
}
export interface RecordForm {
  page_key: string;
  name: string;
  sections: { title: string; columns: number; fields: RecordFormField[] }[];
}
export interface DesignPlan {
  type: {
    label: string; plural_label: string; icon: string; key: string; number_prefix: string | null; parent_entity_type: string | null;
    parent_required: boolean; name_label: string; description: string; existing: boolean; entity_type: string;
  };
  fields: { key: string; label: string; data_type: string; values: string[]; target: string | null; required: boolean; exists?: boolean }[];
  states: LifecycleStateDef[];
  transitions: LifecycleMove[];
  relationships: { to: string; to_label: string; label: string; reverse_label: string }[];
  rules: string[];
  notes: string[];
  engine?: string;
  source_text?: string;
  summary?: string;
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

const k = encodeURIComponent;
const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

// ---------- admin

export function listRecordTypes(): Promise<{
  types: RecordType[]; parent_options: { value: string; label: string }[]; entity_options: { value: string; label: string }[]; relationships: Relationship[];
}> {
  return request("/api/admin/record-types");
}

export function createRecordType(body: RecordTypeInput): Promise<RecordType> {
  return post<RecordType>("/api/admin/record-types", body);
}

export function updateRecordType(key: string, body: Partial<RecordTypeInput>): Promise<RecordType> {
  return request<RecordType>(`/api/admin/record-types/${k(key)}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteRecordType(key: string): Promise<void> {
  return request<void>(`/api/admin/record-types/${k(key)}`, { method: "DELETE" });
}

export function designRecordType(body: { text?: string; plan?: DesignPlan }): Promise<DesignPlan> {
  return post<DesignPlan>("/api/admin/record-types/design", body);
}

export function buildRecordType(body: { text?: string; plan?: DesignPlan }): Promise<{
  applied: boolean; record_type: string; record_type_key: string; fields: string[]; states: string[]; relationships: string[]; rules: string[];
  notes: string[]; summary: string;
}> {
  return post("/api/admin/record-types/design/build", body);
}

export function createRelationship(body: { from_entity_type: string; to_entity_type: string; label: string; reverse_label: string; description?: string }): Promise<Relationship> {
  return post<Relationship>("/api/admin/record-types/relationships", body);
}

export function deleteRelationship(id: number, force = false): Promise<{ links_removed: number }> {
  return request(`/api/admin/record-types/relationships/${id}${force ? "?force=true" : ""}`, { method: "DELETE" });
}

export function saveRecordStates(entityType: string, body: { name: string; states: LifecycleStateDef[]; transitions: (LifecycleMove & { intent_type?: string | null })[] }): Promise<unknown> {
  return request(`/api/admin/state-models/${k(entityType)}`, { method: "PUT", body: JSON.stringify(body) });
}

export function addRecordRule(entityType: string, text: string): Promise<{ id: number; explanation: string }> {
  return post("/api/admin/pages/rules", { entity_type: entityType, text });
}

// ---------- records

export function activeRecordTypes(): Promise<RecordType[]> {
  return request<RecordType[]>("/api/custom-records/types");
}

export function listCustomRecords(key: string, opts: { q?: string; state?: string; mine?: boolean; parentId?: number; offset?: number } = {}): Promise<{
  type: RecordType; total: number; items: CustomRecord[]; state_counts: Record<string, number>;
}> {
  const qs = new URLSearchParams();
  if (opts.q) qs.set("q", opts.q);
  if (opts.state) qs.set("state", opts.state);
  if (opts.mine) qs.set("mine", "true");
  if (opts.parentId != null) qs.set("parent_id", String(opts.parentId));
  if (opts.offset) qs.set("offset", String(opts.offset));
  return request(`/api/custom-records/${k(key)}${qs.toString() ? `?${qs}` : ""}`);
}

export function getCustomRecord(key: string, id: number): Promise<{ type: RecordType; record: CustomRecord }> {
  return request(`/api/custom-records/${k(key)}/${id}`);
}

export function recordForm(key: string, recordId?: number | null): Promise<RecordForm> {
  return request(`/api/custom-records/${k(key)}/form${recordId ? `?record_id=${recordId}` : ""}`);
}

export function createCustomRecord(key: string, body: { name: string; parent_id?: number | string | null; fields: Record<string, unknown> }): Promise<CustomRecord> {
  return post<CustomRecord>(`/api/custom-records/${k(key)}`, body);
}

export function updateCustomRecord(key: string, id: number, body: { name?: string; parent_id?: number | string | null; fields?: Record<string, unknown> }): Promise<CustomRecord> {
  return request<CustomRecord>(`/api/custom-records/${k(key)}/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteCustomRecord(key: string, id: number): Promise<void> {
  return request<void>(`/api/custom-records/${k(key)}/${id}`, { method: "DELETE" });
}

export function moveCustomRecord(key: string, id: number, state: string, reason = ""): Promise<{ outcome: Record<string, unknown>; record: CustomRecord }> {
  return post(`/api/custom-records/${k(key)}/${id}/transition`, { state, reason });
}

export function assignCustomRecord(key: string, id: number, body: { to_user?: string; to_position?: string; to_department?: string; reason?: string }): Promise<CustomRecord> {
  return post<CustomRecord>(`/api/custom-records/${k(key)}/${id}/assign`, body);
}

export function linkCustomRecord(key: string, id: number, body: { relationship: string; related: string | number; unlink?: boolean; note?: string }): Promise<CustomRecord> {
  return post<CustomRecord>(`/api/custom-records/${k(key)}/${id}/links`, body);
}

export function parentChoices(key: string, q = ""): Promise<{ id: number; title: string }[]> {
  return request(`/api/custom-records/parents/${k(key)}${q ? `?q=${k(q)}` : ""}`);
}

/** Records of any type (built-in or custom) the user may see - lookup and link pickers. */
export function searchRecords(entityType: string, q = ""): Promise<{ id: number; title: string; hint: string }[]> {
  return request(`/api/custom-records/search/${k(entityType)}${q ? `?q=${k(q)}` : ""}`);
}

/** The tenant's own records linked to any record - a customer's site surveys. */
export function relatedRecords(entityType: string, id: number): Promise<RelatedSection[]> {
  return request(`/api/custom-records/related/${k(entityType)}/${id}`);
}
