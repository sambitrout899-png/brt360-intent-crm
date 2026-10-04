export interface PlanStep {
  step: number;
  action: string;
  status: "ok" | "failed" | "skipped";
  detail: Record<string, unknown>;
}

export interface IntentResponse {
  intent_execution_id: number;
  intent_type: string | null;
  entities: Record<string, unknown>;
  confidence: number;
  rationale: string;
  status: string;
  plan: PlanStep[];
  result: Record<string, unknown> | null;
  events: string[];
}

export interface Offering {
  id: number;
  name: string;
  segment: string;
  status: "draft" | "active" | "superseded" | "retired";
  version: number;
  price: number;
  price_floor: number;
  composition: string;
  effective_date: string;
  end_date: string | null;
  parent_offering_id: number | null;
  replacement_offering_id: number | null;
}

export interface AuditLogEntry {
  id: number;
  intent_type: string;
  raw_text: string;
  confidence: number;
  status: string;
  rationale: string;
  created_at: string;
  completed_at: string | null;
}

export interface AuditLogDetail extends AuditLogEntry {
  entities_json: string;
  context_json: string;
  plan_json: string;
  human_override_json: string | null;
}
