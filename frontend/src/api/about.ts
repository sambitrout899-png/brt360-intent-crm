import { request } from "./client";
import type { EntityType } from "./types";

/** About this record - everything about one record (GET /api/records/{type}/{id}/about). */
export interface AboutField {
  key: string;
  label: string;
  value: unknown;
  record?: EntityType | null;
  custom?: boolean;
}
export interface AboutRelated {
  entity_type: EntityType;
  entity_id: number;
  type_label: string;
  title: string;
  state: string;
  relation: string;
  detail: string;
  amount: number | null;
  brt_id: string | null;
  at: string | null;
}
export interface AboutSection {
  key: string;
  title: string;
  icon: string;
  rows?: AboutField[];
  items?: { title: string; state?: string; detail?: string; at?: string | null; amount?: number }[];
}
export interface AboutRecord {
  header: {
    entity_type: EntityType;
    entity_id: number;
    type_label: string;
    title: string;
    state: string;
    brt_id: string | null;
    created_at: string | null;
    created_by: string | null;
    updated_at: string | null;
    updated_by: string | null;
    owner: { user?: string | null; position?: string | null; department?: string | null };
    customer: { id: number; name: string } | null;
  };
  summary: { label: string; value: unknown; money?: boolean }[];
  fields: AboutField[];
  related: AboutRelated[];
  sections: AboutSection[];
  timeline: { at: string; icon: string; title: string; detail: string; execution_id?: number }[];
  actions: { intent: string; label: string; entities: Record<string, unknown> }[];
}

export function aboutRecord(entityType: EntityType, id: number): Promise<AboutRecord> {
  return request<AboutRecord>(`/api/records/${entityType}/${id}/about`);
}
