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

export interface Party {
  id: number;
  name: string;
  party_type: string;
  email: string | null;
  phone: string | null;
  created_at: string;
}

export interface Lead {
  id: number;
  party_id: number;
  source: string;
  region: string;
  owner_name: string;
  propensity_score: number;
  created_at: string;
}

export interface Opportunity {
  id: number;
  lead_id: number;
  name: string;
  stage: "draft" | "qualified" | "quoted" | "won" | "lost";
  created_at: string;
  updated_at: string;
}

export interface QuoteItem {
  id: number;
  product_offering_id: number;
  product_offering_name: string;
  quantity: number;
  unit_price: number;
}

export interface Approval {
  approver_tier: string;
  status: string;
  decision: string | null;
  reasoning: string | null;
}

export interface Quote {
  id: number;
  opportunity_id: number;
  status: "draft" | "pending_approval" | "approved" | "rejected";
  total_price: number;
  requested_discount_pct: number;
  discount_justification: string | null;
  created_at: string;
  items: QuoteItem[];
  approval: Approval | null;
  feasible: boolean | null;
}

export interface Resource {
  id: number;
  resource_type: string;
  identifier: string;
  status: string;
}

export interface ServiceOrder {
  id: number;
  status: string;
  resources: Resource[];
}

export interface Order {
  id: number;
  party_id: number;
  party_name: string;
  quote_id: number | null;
  order_type: "new" | "change" | "cancel" | "suspend";
  status: "created" | "activating" | "active" | "changed" | "suspended" | "cancelled" | "terminated";
  monthly_price: number;
  contract_months: number;
  created_at: string;
  service_orders: ServiceOrder[];
}

export interface BillingAdjustment {
  id: number;
  order_id: number | null;
  amount: number;
  reason: string;
  created_at: string;
}

export interface BillingAccount {
  id: number;
  party_id: number;
  party_name: string;
  status: string;
  rate_plan: string;
  balance: number;
  adjustments: BillingAdjustment[];
}

export interface RootCause {
  probable_cause: string;
  confidence: number;
  remediation_suggestion: string;
  field_dispatch_required: boolean;
  escalated_to_human: boolean;
}

export interface TroubleTicket {
  id: number;
  party_id: number | null;
  category: string;
  severity: string;
  product_line: string;
  sla_class: string;
  priority: string;
  status: string;
  description: string;
  created_at: string;
  root_cause: RootCause | null;
}

export interface CompletionEvidence {
  checklist: string;
  signature: string;
  completed_at: string;
}

export interface WorkOrder {
  id: number;
  ticket_id: number | null;
  order_id: number | null;
  scheduled_window: string;
  status: string;
  technician_name: string | null;
  completion_evidence: CompletionEvidence | null;
}

export interface Technician {
  id: number;
  name: string;
  skills: string;
  region: string;
  status: string;
}

export interface Party360 {
  party: Party;
  leads: Lead[];
  opportunities: Opportunity[];
  quotes: Quote[];
  orders: Order[];
  billing_account: BillingAccount | null;
  tickets: TroubleTicket[];
}

export interface DashboardSummary {
  total_parties: number;
  open_opportunities: number;
  pipeline_value: number;
  pending_approvals: number;
  active_orders: number;
  monthly_recurring_revenue: number;
  open_tickets_by_severity: Record<string, number>;
  open_work_orders: number;
}
