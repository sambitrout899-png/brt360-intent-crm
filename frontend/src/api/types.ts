export const TENANT_VERTICALS = ["b2c", "b2b", "both"] as const;
export const USER_ROLES = ["admin", "agent", "viewer"] as const;

export interface CurrentUser {
  id: number;
  tenant_id: number | null;
  email: string;
  name: string;
  role: "platform_admin" | "admin" | "agent" | "viewer";
}

export interface User {
  id: number;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface Tenant {
  id: number;
  name: string;
  slug: string;
  vertical: string;
  created_at: string;
}

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

export interface ApprovalStep {
  sequence: number;
  approver_tier: string;
  status: string;
  reasoning: string | null;
}

export interface ApprovalChain {
  template_name: string;
  status: string;
  current_tier: string | null;
  steps: ApprovalStep[];
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
  approval_chain: ApprovalChain | null;
  feasible: boolean | null;
}

export const ATTRIBUTE_DATA_TYPES = ["number", "string", "boolean", "enum"] as const;
export const CONSTRAINT_OPERATORS = ["eq", "ne", "gt", "gte", "lt", "lte"] as const;
export const CONSTRAINT_RULE_TYPES = ["requires", "excludes"] as const;

export interface ProductSpecification {
  id: number;
  name: string;
  product_line: string;
  is_atomic: boolean;
}

export interface ProductAttributeDefinition {
  id: number;
  name: string;
  data_type: string;
  unit: string | null;
  allowed_values: string | null;
  created_at: string;
}

export interface ProductAttributeValue {
  id: number;
  product_specification_id: number;
  attribute_definition_id: number;
  attribute_name: string;
  value: string;
}

export interface AttributeConstraintRule {
  id: number;
  if_product_spec_id: number;
  if_product_name: string;
  if_attribute_id: number;
  if_attribute_name: string;
  operator: string;
  threshold: string;
  rule_type: string;
  then_product_spec_id: number;
  then_product_name: string;
  is_active: boolean;
  created_at: string;
}

export interface ApprovalChainTier {
  sequence: number;
  approver_tier: string;
}

export const SLA_BREACH_ACTIONS = ["escalate", "auto_approve", "auto_cancel", "auto_proceed"] as const;

export interface SlaPolicy {
  id: number;
  clock_type: string;
  duration_hours: number;
  on_breach_action: string;
  is_active: boolean;
  created_at: string;
}

export interface SlaClock {
  id: number;
  clock_type: string;
  entity_type: string;
  entity_id: number;
  on_breach_action: string;
  started_at: string;
  deadline: string;
  status: "running" | "completed" | "breached" | "cancelled";
  resolved_at: string | null;
  detail: string;
}

export interface SlaSweepResult {
  execution_id: number;
  checked: number;
  breached: { clock_id: number; clock_type: string; outcome: string; detail: string }[];
}

export interface ApprovalChainTemplate {
  id: number;
  name: string;
  segment: string | null;
  min_discount_pct: number;
  max_discount_pct: number;
  is_active: boolean;
  created_at: string;
  tiers: ApprovalChainTier[];
}

export interface Resource {
  id: number;
  resource_type: string;
  identifier: string;
  status: string;
}

export interface ServiceOrder {
  id: number;
  site_label: string;
  status: string;
  resources: Resource[];
}

export interface Order {
  id: number;
  party_id: number;
  party_name: string;
  service_account_id: number;
  service_account_name: string;
  quote_id: number | null;
  order_type: "new" | "change" | "cancel" | "suspend";
  status: "created" | "activating" | "active" | "changed" | "suspended" | "pending_cancellation" | "cancelled" | "terminated";
  monthly_price: number;
  contract_months: number;
  created_at: string;
  service_orders: ServiceOrder[];
}

export interface ServiceAccount {
  id: number;
  billing_account_id: number;
  name: string;
  status: string;
  created_at: string;
  orders: Order[];
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

export interface IdentityVerification {
  id: number;
  provider_name: string;
  id_type: string;
  id_last4: string;
  status: "pending" | "verified" | "rejected";
  detail: string;
  created_at: string;
  resolved_at: string | null;
}

export interface IdentityProvider {
  id: number;
  name: string;
  is_active: boolean;
  created_at: string;
}

export interface TestRunSummary {
  id: number;
  status: "running" | "passed" | "failed" | "error";
  total: number;
  passed: number;
  failed: number;
  errors: number;
  duration_seconds: number;
  started_at: string;
  completed_at: string | null;
}

export interface TestRun extends TestRunSummary {
  output: string;
}

export interface Party360 {
  party: Party;
  leads: Lead[];
  opportunities: Opportunity[];
  quotes: Quote[];
  orders: Order[];
  service_accounts: ServiceAccount[];
  identity_verifications: IdentityVerification[];
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
