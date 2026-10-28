import type { Conflict, ScratchDraft } from "./conflictTypes";

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
  mfa_enabled: boolean;
  locked_until: string | null;
}

export interface Tenant {
  licensed_modules: string[] | null;
  id: number;
  name: string;
  slug: string;
  vertical: string;
  display_name: string | null;
  logo_data_url: string | null;
  brand_color: string | null;
  created_at: string;
}

export interface TenantUpdate {
  licensed_modules?: string[];
  name?: string;
  vertical?: string;
  display_name?: string;
  logo_data_url?: string;
  brand_color?: string;
}

export interface Branding {
  name: string;
  display_name: string;
  logo_data_url: string | null;
  brand_color: string | null;
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
  missing_fields: string[];
  suggestions: IntentSuggestion[];
  intent_label: string | null;
  intent_icon?: string | null;
  summary?: string;
  records?: OutcomeRecord[];
  next_actions?: NextAction[];
  rehearsal?: boolean;
  impact?: RehearsalImpact | null;
  /** a rehearsal: what pulls against something else on this run */
  conflicts?: Conflict[];
  /** status "failed" after execution: the failure, classified, and the intent's "on error" flows that ran */
  error?: (FlowError & { step?: string; flows_run?: { flow_id: number; flow_name: string; outcome: string; error?: FlowError }[] }) | null;
}

/** What a rehearsed intent would do (POST /api/intents/rehearse) - nothing of it was kept. */
export interface RehearsalImpact {
  headline: string;
  blast_radius: number;
  records_created: { entity: string; id: number; label: string }[];
  records_changed: { entity: string; id: number; label: string; fields: { field: string; from: unknown; to: unknown }[] }[];
  records_deleted: { entity: string; id: number; label: string }[];
  state_moves: { record: string; entity_type: string; entity_id: number; from: string | null; to: string; by: string }[];
  assignments: { record: string; to: string; by: string }[];
  approvals: { record: string; gate: string; summary: string; status: string; tiers: string[] }[];
  notifications: { channel: string; audience: string; message: string }[];
  flows: { name: string; steps: number }[];
  money: { record: string; field: string; from: unknown; to: unknown }[];
  events: string[];
  warnings: string[];
}

// ---------- the agent: a goal carried out as a chain of intents ----------

export interface AgentStep {
  n: number;
  text: string;
  intent_type: string | null;
  label: string;
  icon: string;
  entities: Record<string, unknown>;
  why: string;
  required: string[];
  /** missing required fields - from_step: the earlier step that supplies it, null = the user */
  needs: { field: string; from_step: number | null }[];
  fields: string[];
}

export interface AgentPlan {
  goal: string;
  /** "memory": the plan that carried this exact request out before */
  planner: "rules" | "ai" | "memory";
  steps: AgentStep[];
  /** parts of the goal that needed no step of their own, and why */
  notes?: string[];
  /** pre-run scan: what this plan touches that pulls against something else */
  conflicts?: Conflict[];
  can_settle?: boolean;
}

export interface AgentRun {
  goal: string;
  mode: "rehearse" | "execute";
  status: "rehearsed" | "done" | "paused";
  message: string;
  steps: {
    n: number;
    intent_type?: string;
    label?: string;
    icon?: string;
    status: string;
    summary: string;
    entities_used?: Record<string, unknown>;
    filled_from_memory?: string[];
    missing_fields?: string[];
  }[];
  stopped: {
    step: number;
    kind: "needs_input" | "blocked" | "unclear";
    message: string;
    missing_fields?: string[];
    /** a stage the goal skipped - steps that satisfy it, offered as a choice */
    repairs?: { id: string; message: string; at: number; insert: { intent_type: string; entities: Record<string, unknown>; text: string }[] }[];
  } | null;
  impact: RehearsalImpact;
  memory: Record<string, number>;
  conflicts?: Conflict[];
  can_settle?: boolean;
}

/** Ask anything (orchestration/assistant.py): how a request was understood and where it goes. */
export interface AssistantUnderstanding {
  request: string;
  route: "answer" | "action" | "chain" | "process" | "unknown";
  label: string;
  why: string;
  /** a question: the actions that do what it asks about */
  answers?: { intent_type: string; label: string; description: string; icon?: string; entities: Record<string, unknown>; text: string }[];
  memory?: "process" | "chain";
  needs_admin?: boolean;
  action?: { intent_type: string; entities: Record<string, unknown>; text: string; label: string; icon?: string };
  plan?: AgentPlan;
  /** nothing covers the request: a new action drafted for it, built only on an admin's consent */
  build_offer?: ScratchDraft | null;
  /** nothing covers the request and an admin may build it - the draft is fetched by the panel itself */
  can_build?: boolean;
  /** a business process described: read against the vanilla processes - only the gaps get built */
  fit?: ProcessFit;
  journal_id: number;
}

/** How a described process fits the vanilla ones (backend: orchestration/process_fit.py). */
export interface ProcessFit {
  process: { key: string; label: string; segment: string | null; steps: { code: string; label: string; icon?: string; said: boolean }[] };
  alternatives: { key: string; label: string }[];
  confidence: number;
  journey: boolean;
  items: {
    text: string;
    kind: "step" | "data" | "config" | "rule" | "note" | "new_step" | "context" | "intro" | "variant";
    status: "covered" | "gap";
    note: string;
    codes?: string[];
    label?: string;
    page?: string | null;
    fields?: { label: string; data_type: string; exists: boolean; existing: string | null; mandatory: boolean }[];
  }[];
  covered: number;
  gaps: number;
  build_steps: { kind: string; text: string }[];
  new_steps: string[];
  /** only fields were asked for - no process to place them in */
  data_only?: boolean;
  /** some records take a shorter road: a condition, the steps that run by themselves, where the user lands */
  variant?: {
    condition: { entity_type: string; segment: string; field: string; key: string; value: string; exists: boolean; existing: string | null; page: string | null };
    skipped: string[];
    skipped_labels: string[];
    automated: string[];
    automated_labels: string[];
    target: string | null;
    target_label: string | null;
    product: string | null;
    conflicts: { what: string; why: string; instead: string }[];
  } | null;
}

export interface AssistantJournalEntry {
  id: number;
  request: string;
  route: AssistantUnderstanding["route"] | null;
  label: string;
  why: string | null;
  memory: string | null;
  at: string;
  outcome: { status: string; summary: string; at: string } | null;
  by?: string;
}

/** Process Builder (orchestration/process_builder.py). */
export interface ProcessStep {
  kind: string;
  text: string;
}

export interface ProcessInput {
  request: string;
  name?: string | null;
  steps?: ProcessStep[];
  answers?: Record<string, string>;
  graphs?: Record<string, unknown>;
}

export interface ProcessComponent {
  id: string;
  kind: "approval" | "flow" | "page" | "assignment";
  engine: string;
  icon: string;
  text: string;
  title: string;
  lines: string[];
  warnings: string[];
  spec: Record<string, unknown> & { graph?: unknown };
}

export interface ProcessQuestion {
  id: string;
  prompt: string;
  hint: string;
  allow_text: boolean;
  default: string | null;
  options: { value: string; label: string }[];
}

export interface ProcessEngineering {
  id: string;
  text: string;
  need: string;
  why: string;
  workaround: string;
  effort: string;
}

export interface ProcessBlueprint {
  name: string;
  /** "actions": nothing to configure - a chain of things to do, planned by the agent */
  mode?: "configure" | "actions";
  actions?: AgentPlan;
  request: string;
  planner: "rules" | "ai" | "given" | "memory";
  memory?: { id: number; name: string } | null;
  answers?: Record<string, string>;
  steps: ProcessStep[];
  components: ProcessComponent[];
  questions: ProcessQuestion[];
  engineering: ProcessEngineering[];
  skipped: string[];
  new_positions: string[];
  /** how an automation was reasoned out - the road, the waits, the choices (reasoner) */
  reasoning?: string[];
  /** what already runs and is reused instead of built again */
  recommendations?: string[];
  ready: boolean;
}

export interface ProcessBuild {
  memory_id?: number | null;
  status: "rehearsed" | "built" | "needs_input" | "nothing" | "failed";
  message?: string;
  blueprint: ProcessBlueprint;
  made: { component: string; kind: string; id: number | string; name: string; where: string; active?: boolean }[];
  checks: { component: string; ok: boolean; message: string }[];
  impact: RehearsalImpact | null;
}

// ---------- intent catalog / command bar copilot ----------

export type IntentFieldType =
  | "text"
  | "textarea"
  | "number"
  | "money"
  | "percent"
  | "picklist"
  | "record"
  | "list"
  | "boolean"
  | "updates"
  | "transition"
  | "attributes"
  | "design"
  | "page"
  | "products";

export interface IntentFieldSpec {
  key: string;
  label: string;
  type: IntentFieldType;
  required: boolean;
  help: string;
  placeholder: string;
  values: string[];
  source: string;
  record_type: string;
  default: unknown;
  /** shown only when another field holds one of these values; empty = always */
  show_when?: { field?: string; in?: string[] };
}

export interface IntentDef {
  code: string;
  kind: "builtin" | "custom";
  label: string;
  default_label: string;
  description: string;
  category: string;
  icon: string;
  target: string | null;
  fields: IntentFieldSpec[];
  one_of: string[][];
  examples: string[];
  phrases: string[];
  is_active: boolean;
  role_ids: number[];
  flow_id: number | null;
  /** "B2B" / "B2C" for a step of one segment's process; null = both. */
  segment?: string | null;
}

export interface IntentCategory {
  key: string;
  label: string;
}

/** A business process and its steps (intent codes), in process order. */
export interface IntentProcess {
  key: string;
  label: string;
  steps: string[];
}

export interface IntentCatalog {
  intents: IntentDef[];
  categories: IntentCategory[];
  /** the user's actions per process - only the steps their roles allow */
  processes?: IntentProcess[];
}

export interface IntentOption {
  value: string;
  label: string;
  hint?: string;
  via?: string | null;
  /** "attributes" fields: the values this attribute may take. */
  values?: string[];
}

export interface RecordOption {
  id: number;
  label: string;
  hint: string;
  suggested: boolean;
}

export type ValueOrigin = "you" | "understood" | "context" | "linked" | "default" | "usual" | "guess" | "account" | "business";

export interface PreparedField extends IntentFieldSpec {
  options: IntentOption[];
  suggestions: RecordOption[];
  value: unknown;
  origin: ValueOrigin | null;
  origin_note: string;
  display: string;
  /** set for you (e.g. the product carried from the lead) - shown, not picked again */
  locked?: boolean;
  /** a locked field the user may still unlock and change */
  locked_change?: boolean;
  locked_note?: string;
  /** shown only - never sent (e.g. the pricing attributes Presales sees) */
  readonly?: boolean;
}

export interface IntentBlocker {
  message: string;
  fix: { code: string; label?: string | null; entities: Record<string, unknown> } | null;
}

export interface PreparedIntent {
  code: string;
  label: string;
  icon: string;
  description: string;
  target: string | null;
  fields: PreparedField[];
  values: Record<string, unknown>;
  missing: string[];
  blockers: IntentBlocker[];
  tips: string[];
  summary: string;
  ready: boolean;
}

export interface OutcomeRecord {
  entity_type: string;
  entity_id: number;
  brt_id?: string | null;
  type_label: string;
  title: string;
  hint: string;
  state: string;
  change: "created" | "updated" | "involved";
  from_state: string | null;
  to_state: string | null;
  fields: { key: string; label: string; value: unknown }[];
  openable: boolean;
}

export interface NextAction {
  code: string;
  label: string;
  icon: string;
  reason: string;
  entities: Record<string, unknown>;
}

export interface RecentIntent {
  code: string;
  label: string;
  icon: string;
  status: string;
  text: string;
  entities: Record<string, unknown>;
  at: string;
}

export interface AdminIntentCatalog {
  intents: IntentDef[];
  categories: IntentCategory[];
  roles: { id: number; name: string; is_active: boolean }[];
  field_types: IntentFieldType[];
  field_library: IntentFieldSpec[];
  entity_types: string[];
  processes?: IntentProcess[];
}

export interface IntentFieldInput {
  key?: string;
  label: string;
  type: IntentFieldType;
  required: boolean;
  help?: string;
  placeholder?: string;
  values?: string[];
  source?: string;
  record_type?: string;
}

export interface IntentCreateInput {
  label: string;
  description: string;
  behaviour: string;
  category: string;
  icon: string;
  entity_type: string | null;
  phrases: string[];
  fields: IntentFieldInput[];
  role_ids: number[];
  is_active: boolean;
}

export interface IntentCreateResult {
  intent: IntentDef;
  flow_id: number;
  notes: string;
  corrections: FlowCorrection[];
  flow_errors: string[];
}

export interface IntentUpdateInput {
  label?: string;
  description?: string;
  category?: string;
  icon?: string;
  is_active?: boolean;
  role_ids?: number[];
  phrases?: string[];
  defaults?: Record<string, unknown>;
  fields?: IntentFieldInput[];
}

/** What the user is looking at when invoking an intent - lets "approve
 * this" resolve to the open record. */
export interface IntentContext {
  page?: string;
  entity_type?: string;
  entity_id?: number;
  label?: string;
}

export interface IntentSuggestion {
  intent_type: string;
  label: string;
  required_fields: string[];
}

export interface IntentAssist {
  intent_type: string | null;
  intent_label: string | null;
  confidence: number;
  rationale: string;
  entities: Record<string, unknown>;
  required_fields: string[];
  missing_fields: string[];
  suggestions: IntentSuggestion[];
}

export interface AiSettings {
  configured: boolean;
  masked_api_key: string | null;
  model: string;
  is_active: boolean;
  using_global_fallback: boolean;
  provider: AiProvider;
  effective: AiProvider;
  effective_label: string;
  platform_key_available: boolean;
  local_model: string;
  local_live_assist: boolean;
}

export type AiProvider = "external" | "local" | "rules";

export interface LocalModelStatus {
  base_url: string;
  default_model: string;
  reachable: boolean;
  models: { name: string; size_gb?: number; parameters?: string; quantization?: string }[];
  error: string | null;
  latency_ms: number;
}

export interface AiTestResult {
  ok: boolean;
  provider: AiProvider;
  model: string | null;
  latency_ms: number;
  intent_type: string | null;
  entities: Record<string, unknown>;
  rationale: string;
  error: string | null;
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
  profile?: PartyProfile | null;
}

export interface PartyProfile {
  segment: string | null;
  account_status: string | null;
  price_list_id: number | null;
  industry: string | null;
}

export interface Lead {
  id: number;
  party_id: number;
  source: string;
  region: string;
  owner_name: string;
  propensity_score: number;
  status: "pending_qualification" | "qualified" | "rejected";
  interested_offering_name: string | null;
  created_at: string;
  approval_chain: ApprovalChain | null;
}

export interface FeasibilityCheckResult {
  check_type_name: string;
  outcome: "pending" | "passed" | "failed";
  detail: string;
}

export interface FeasibilityAssessment {
  id: number;
  outcome: "pending" | "passed" | "failed";
  created_at: string;
  resolved_at: string | null;
  results: FeasibilityCheckResult[];
}

export interface Opportunity {
  id: number;
  lead_id: number;
  name: string;
  stage: "draft" | "qualified" | "quoted" | "won" | "lost" | "configured" | "in_feasibility" | "feasibility_failed" | "feasible";
  offering_name?: string | null;
  quantity?: number;
  created_at: string;
  updated_at: string;
  feasibility_assessments: FeasibilityAssessment[];
  config?: OpportunityConfig | null;
}

export interface OpportunityConfig {
  pricing_attributes: Record<string, string>;
  site_address: string | null;
  technical_parameters: string | null;
  feasibility_notes: string | null;
  account_manager_user_id: number | null;
  technical_attributes?: Record<string, string>;
  price_list_id?: number | null;
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

export interface Proposal {
  id: number;
  quote_id: number;
  status: "sent" | "customer_approved" | "customer_rejected";
  document_ref: string;
  sent_at: string;
  decided_at: string | null;
}

export interface Quote {
  id: number;
  opportunity_id: number;
  status: "draft" | "pending_approval" | "approved" | "rejected" | "cancelled" | "superseded";
  total_price: number;
  requested_discount_pct: number;
  discount_justification: string | null;
  created_at: string;
  items: QuoteItem[];
  approval_chain: ApprovalChain | null;
  feasible: boolean | null;
  proposals: Proposal[];
  revision_number: number;
  revision_of_quote_id: number | null;
  purchase_order?: QuotePurchaseOrder | null;
  configuration?: QuoteConfiguration | null;
  /** the PDFs on the quote - proposal, PO, agreement, others */
  documents?: QuoteDocument[];
  /** the order the quote became */
  order_id?: number | null;
  /** the price negotiated with the customer (INT-116); null = never overridden */
  price_override?: { active: QuotePriceOverride | null; history: QuotePriceOverride[] } | null;
}

export interface QuotePriceOverride {
  override_price: number;
  /** what the system priced the quote at */
  computed_price: number;
  list_price: number;
  effective_discount_pct: number;
  reason: string;
  by_user_id: number | null;
  at: string;
  active: boolean;
  ended_reason: "replaced" | "removed" | "repriced" | null;
}

export type QuoteDocumentKind = "proposal" | "purchase_order" | "agreement" | "other";

export interface QuoteDocument {
  id: number;
  ref: string;
  quote_id: number;
  kind: QuoteDocumentKind;
  kind_label: string;
  title: string;
  file_name: string;
  size: number;
  source: "generated" | "uploaded";
  version: number;
  order_id: number | null;
  note: string;
  created_at: string;
  created_by: string | null;
}

/** Admin -> Quote Documents: the wording of a generated PDF (proposal, agreement). */
export interface QuoteDocumentTemplate {
  kind: "proposal" | "agreement";
  label: string;
  enabled: boolean;
  title: string;
  intro: string;
  terms: string;
  closing: string;
  customized: boolean;
  updated_at: string | null;
}

export interface PriceLine {
  label: string;
  amount: number;
}

export interface QuoteConfiguration {
  pricing_attributes: Record<string, string>;
  technical_attributes: Record<string, string>;
  price_breakdown: { price_list?: string | null; base_source?: string; base?: number; lines?: PriceLine[]; configured?: number };
  quote_price_list: string | null;
}

export interface QuotePurchaseOrder {
  po_number: string;
  amount: number | null;
  document_ref: string | null;
  received_at: string;
  matched_at: string | null;
  match_notes: string | null;
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

export interface PonrPolicy {
  id: number;
  entity_type: "quote" | "order";
  ponr_statuses: string;
  is_active: boolean;
  created_at: string;
}

export interface FeasibilityCheckType {
  external_id: string | null;
  id: number;
  name: string;
  resource_type: string;
  is_active: boolean;
  created_at: string;
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

export interface FulfillmentWorkOrder {
  id: number;
  ticket_id: number | null;
  order_id: number | null;
  order_item_id: number | null;
  kind: "field_dispatch" | "installation" | "connectivity_test" | "acceptance_letter";
  technical_parameters: string;
  scheduled_window: string;
  status: string;
  technician_name: string | null;
  completion_evidence: { checklist: string; signature: string; completed_at: string } | null;
}

export interface Order {
  id: number;
  party_id: number;
  party_name: string;
  service_account_id: number;
  service_account_name: string;
  quote_id: number | null;
  order_type: "new" | "change" | "cancel" | "suspend";
  status:
    | "completed"
    | "created"
    | "activating"
    | "active"
    | "changed"
    | "suspended"
    | "pending_cancellation"
    | "cancelled"
    | "terminated"
    | "pending_approval"
    | "rejected";
  monthly_price: number;
  contract_months: number;
  created_at: string;
  service_orders: ServiceOrder[];
  order_control_chain: ApprovalChain | null;
  fulfillment_work_orders: FulfillmentWorkOrder[];
  milestones?: OrderMilestone[];
  segment?: string | null;
  configuration?: OrderItemConfiguration[];
  discount_approval?: ApprovalChain | null;
}

export interface OrderMilestone {
  code: string;
  label: string;
  detail: string;
  reached_at: string;
}

export interface Asset {
  order_history: {order_id: number; status: string; operation?: string; version?: number; due_date?: string | null}[];
  instance_json: string;
  version: number;
  id: number;
  service_account_id: number;
  order_item_id: number | null;
  asset_type: string;
  identifier: string;
  status: "active" | "suspended" | "decommissioned";
  inventory_system_ref: string | null;
  created_at: string;
}

export interface ServiceAccount {
  party_id?: number | null;
  id: number;
  billing_account_id: number;
  name: string;
  status: string;
  created_at: string;
  orders: Order[];
  assets: Asset[];
}

export const ORCHESTRATION_MODES = ["deterministic", "llm_planned"] as const;

export interface OrchestrationPolicy {
  id: number;
  intent_type: string;
  mode: "deterministic" | "llm_planned";
  is_active: boolean;
  created_at: string;
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
  pending_lead_qualifications: number;
  active_orders: number;
  monthly_recurring_revenue: number;
  open_tickets_by_severity: Record<string, number>;
  open_work_orders: number;
}

// ---------- Phase 7: lifecycle, assignment, custom fields, Flow Studio ----------

export const ENTITY_TYPES = ["party", "lead", "opportunity", "quote", "order"] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

export const ENTITY_LABELS: Record<EntityType, string> = {
  party: "Customer / Prospect",
  lead: "Lead",
  opportunity: "Opportunity",
  quote: "Quote",
  order: "Order",
};

export const CUSTOM_FIELD_DATA_TYPES = ["text", "number", "boolean", "date", "picklist"] as const;
export const ASSIGNMENT_STRATEGIES = ["position", "round_robin", "user", "department"] as const;

export interface LifecycleState {
  code: string;
  label: string;
  editable: boolean;
  is_terminal: boolean;
  built_in?: boolean;
}

export interface LifecycleTransition {
  from_state: string;
  to_state: string;
  action: string;
  intent_type: string | null;
  allowed_roles: string;
}

export interface StateModelConfig {
  entity_type: EntityType;
  name: string;
  configured: boolean;
  built_in_states: string[];
  states: LifecycleState[];
  transitions: LifecycleTransition[];
}

export interface PositionMemberRef {
  id: number;
  name: string;
  email: string;
}

export interface Position {
  id: number;
  name: string;
  parent_position_id: number | null;
  department_id: number | null;
  data_scope: string;
  is_active: boolean;
  members: (PositionMemberRef & { is_primary: boolean })[];
}

// ---------- Org structure, roles & access ----------

export interface Department {
  id: number;
  name: string;
  parent_department_id: number | null;
  is_active: boolean;
}

export interface AppRole {
  id: number;
  name: string;
  description: string;
  pages: string[];
  is_active: boolean;
  user_count: number;
}

export interface UserAccess {
  id: number;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
  role_ids: number[];
  position_ids: number[];
  primary_position_id: number | null;
  pages: string[];
}

export interface KeyLabel {
  key: string;
  label: string;
}

export interface AccessCatalog {
  pages: KeyLabel[];
  data_scopes: KeyLabel[];
}

export interface HeldPosition {
  id: number;
  name: string;
  department_id: number | null;
  department_name: string | null;
  data_scope: string;
  is_primary: boolean;
}

/** What the signed-in user gets for the position + scope in effect. */
export interface MyAccess {
  user: { id: number; name: string; email: string; role: string };
  pages: string[];
  roles: { id: number; name: string }[];
  positions: HeldPosition[];
  active_position_id: number | null;
  max_scope: string;
  scope: string;
  available_scopes: KeyLabel[];
}

export interface AssignmentRule {
  id: number;
  name: string;
  entity_type: EntityType;
  on_state: string | null;
  strategy: string;
  position_id: number | null;
  user_id: number | null;
  department_id?: number | null;
  priority: number;
  is_active: boolean;
}

export interface CustomFieldDefinition {
  id: number;
  entity_type: EntityType;
  key: string;
  label: string;
  data_type: string;
  allowed_values: string | null;
  required: boolean;
  default_value: string | null;
  is_active: boolean;
}

export interface RecordAction {
  action: string;
  to_state: string;
  to_label: string;
  intent_type: string | null;
}

/** A record's button, worked out on the server from its current state. */
export interface RecordButton {
  label: string;
  intent: string;
  entities: Record<string, unknown>;
  to_state?: string;
}

export interface TickerItem {
  text: string;
  level: "info" | "success" | "warning" | "urgent";
  kind: string;
  link?: string;
  entity_type?: string;
  entity_id?: number;
}

export interface Notices {
  approvals_waiting: number;
  approvals: { entity_type: string; entity_id: number; summary: string; waiting_for: string | null; requested_at: string; requested_by?: string | null }[];
  ticker: TickerItem[];
}

export interface Broadcast {
  id: number;
  text: string;
  level: string;
  place: string;
  ends_at: string | null;
  created_at: string;
}

export interface RecordCustomField {
  key: string;
  label: string;
  data_type: string;
  allowed_values: string[];
  required: boolean;
  value: string | number | boolean | null;
}

export interface RecordHistoryItem {
  kind: "state" | "assignment";
  summary: string;
  source: string;
  created_at: string;
}

export interface RecordLifecycle {
  entity_type: EntityType;
  entity_id: number;
  title: string;
  brt_id?: string | null;
  sys_created_at?: string | null;
  sys_created_by?: { id: number; name: string; email: string } | null;
  state: string;
  state_label: string;
  built_in_status: string;
  editable: boolean;
  is_terminal: boolean;
  state_model_name: string;
  actions?: RecordButton[];
  /** The approval the record waits for, and whether the viewer's position lets them decide it. */
  approval?: { rule: string; waiting_for: string; step: number; steps: number; can_decide: boolean; why: string } | null;
  available_actions: RecordAction[];
  owner_user: { id: number; name: string; email: string } | null;
  owner_position: { id: number; name: string } | null;
  owner_department?: { id: number; name: string } | null;
  core_fields: Record<string, string | number | boolean | null>;
  updatable_core_fields: string[];
  core_field_labels?: Record<string, string>;
  core_field_options?: Record<string, string[]>;
  custom_fields: RecordCustomField[];
  history: RecordHistoryItem[];
  revisions: { id: number; revision_number: number; status: string; total_price: number }[];
}

export interface Assignees {
  users: { id: number; name: string; email: string; role: string }[];
  positions: { id: number; name: string; member_count: number }[];
  departments?: { id: number; name: string }[];
}

export interface MyWorkItem {
  entity_type: EntityType | "work_order";
  entity_id: number;
  title: string;
  state: string;
  via_position: string | null;
  updated_at: string;
  /** a work order opens its order */
  open_entity_type?: EntityType | null;
  open_entity_id?: number | null;
}

export interface FlowNode {
  id: string;
  type: string;
  label: string;
  x: number;
  y: number;
  config: Record<string, string>;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  label: string;
}

export interface FlowGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

export interface Flow {
  id: number;
  name: string;
  description: string;
  entity_type: EntityType | null;
  trigger_type: "intent" | "custom_intent" | "state_enter" | "validation";
  trigger_intent_type: string | null;
  /** before (can reject the intent) · after (on its result) · error (when the intent fails - {{error.*}}) */
  timing: "before" | "after" | "error";
  trigger_state: string | null;
  trigger_phrases: string | null;
  required_fields: string | null;
  graph: FlowGraph;
  /** an error the flow doesn't catch: fail the intent too, or undo only this flow and let the intent complete */
  on_error: FlowErrorPolicy;
  version: number;
  is_active: boolean;
  intent_code: string | null;
  validation_errors: string[];
  warnings: string[];
  corrections: FlowCorrection[];
  updated_at: string;
}

export type FlowInput = Omit<
  Flow,
  "id" | "version" | "is_active" | "intent_code" | "validation_errors" | "warnings" | "corrections" | "updated_at"
>;

/** A field a flow node can read / write (flow_schema.field_catalog). */
export interface FlowFieldDef {
  path: string;
  key: string;
  label: string;
  data_type: "text" | "number" | "boolean" | "date" | "picklist";
  source: "core" | "custom" | "lifecycle" | "intent" | "result" | "error";
  values: string[];
  settable: boolean;
  recent: string[];
}

export interface FlowFieldCatalog {
  handoff?: FlowFieldDef[];
  fields: FlowFieldDef[];
  states: { code: string; label: string }[];
  users: { email: string; name: string }[];
  positions: string[];
  channels: string[];
  strategies: string[];
}

export interface FlowCorrection {
  node_id: string;
  node: string;
  key: string;
  before: unknown;
  after: unknown;
  reason: string;
}

export interface FlowIssue {
  node_id: string;
  node: string;
  key: string;
  message: string;
  suggestion: string | null;
}

export interface FlowReview {
  graph: FlowGraph;
  corrections: FlowCorrection[];
  issues: FlowIssue[];
}

export interface FlowNodeType {
  type: string;
  label: string;
  outputs: string[];
  config: string[];
}

export interface FlowCatalog {
  record_entities: { name: string; label: string; fields: { name: string; type: string; writable: boolean; required: boolean; choices: string[]; references: string[] }[] }[];
  node_types: FlowNodeType[];
  operators: string[];
  entity_types: EntityType[];
  trigger_types: string[];
  timings: string[];
  intents: { code: string; label: string }[];
  error_policies?: FlowErrorPolicy[];
  /** what a Catch errors step can be limited to - error codes, then categories */
  error_codes?: { value: string; category: FlowErrorCategory; label: string }[];
}

export interface FlowGenerateResult {
  graph: FlowGraph;
  source: "llm" | "heuristic";
  notes: string;
  validation_errors: string[];
  corrections: FlowCorrection[];
  issues: FlowIssue[];
}

export type FlowErrorPolicy = "fail" | "continue";
export type FlowErrorCategory = "business" | "validation" | "system";

/** A classified failure (backend orchestration/flow_errors.py). */
export interface FlowError {
  code: string;
  category: FlowErrorCategory;
  message: string;
  retryable: boolean;
  node_id: string | null;
  node: string | null;
  node_type: string | null;
  flow_id: number | null;
  flow: string | null;
  intent: string | null;
  details: Record<string, unknown>;
  /** a system error's reference in the server log */
  ref?: string;
}

export interface FlowTraceEntry {
  node_id: string;
  type: string;
  label: string;
  /** ok · failed · caught (handled by an error output or Catch errors) · skipped · rejected */
  status?: "ok" | "failed" | "caught" | "skipped" | "rejected";
  error?: FlowError;
  caught_by?: string;
  attempts?: number;
  [key: string]: unknown;
}

export interface FlowSimulateResult {
  ok: boolean;
  error: string | null;
  /** the run - up to the failing step when it failed */
  run: {
    flow_name: string;
    entity_type: string | null;
    entity_id: number | null;
    trace: FlowTraceEntry[];
    trigger?: { input: Record<string, unknown>; output: Record<string, unknown> };
    outcome?: "completed" | "handled" | "failed" | "rejected";
    errors?: FlowError[];
  } | null;
  error_detail?: FlowError | null;
}

// ---------------------------------------------------------------- approvals (two kinds) + Sales-to-Order setup

export interface ApprovalRuleTier {
  sequence: number;
  approver_tier: string;
  position_id: number | null;
  position_name: string | null;
  holders: number;
  /** No position, or nobody in it yet - what happens then is the "Who decides" setting (unstaffed_note). */
  open_to_anyone: boolean;
  unstaffed_note?: string | null;
  /** The step is a consent word ("Yes") - not a real approver. */
  not_an_approver?: boolean;
}

/** Admin → Approvals → Who decides. */
export interface ApprovalPolicy {
  unstaffed: "anyone" | "admins" | "position";
  fallback_position_id: number | null;
  self_approval: boolean;
  admins_decide_any: boolean;
  segments: string[];
  configured?: boolean;
}

export interface ApprovalRule {
  id: number;
  name: string;
  kind: "monetary" | "record";
  entity_type: string;
  trigger_state: string | null;
  segment: string | null;
  min_discount_pct: number;
  max_discount_pct: number;
  min_amount: number | null;
  max_amount: number | null;
  is_active: boolean;
  tiers: ApprovalRuleTier[];
}

export interface ApprovalRuleInput {
  name: string;
  kind: "monetary" | "record";
  entity_type: string;
  trigger_state: string | null;
  segment: string | null;
  min_discount_pct: number;
  max_discount_pct: number;
  min_amount: number | null;
  max_amount: number | null;
  tiers: { position_id: number | null; approver_tier?: string }[];
  is_active: boolean;
  /** Approvals already under way take the new approvers for the steps they haven't decided (default true). */
  apply_to_pending?: boolean;
}

export interface ApprovalPreset {
  key: string;
  name: string;
  kind: "monetary" | "record";
  entity_type: string;
  trigger_state: string | null;
  tiers: string[];
  help: string;
  applied: boolean;
}

export interface ApprovalRuleOptions {
  record_types: { entity_type: string; states: { code: string; label: string }[] }[];
  monetary_record_types: string[];
  monetary_labels?: Record<string, string>;
  segments: string[];
  positions: { id: number; name: string; holders: number }[];
  presets: ApprovalPreset[];
}

export interface ApprovalRequest {
  id: number | null;
  entity_type: string;
  entity_id: number;
  kind: string;
  rule: string;
  gate: string;
  summary: string;
  status: string;
  waiting_for: string | null;
  requested_at: string;
  requested_by?: string | null;
  decided_at: string | null;
  steps: { sequence: number; approver_tier: string; status: string; reasoning: string; decided_at: string | null; decided_by?: string | null }[];
  record_label?: string;
  can_decide?: boolean;
  /** Admin → Approvals → Waiting now: why nobody can decide it as things stand. */
  stuck?: string | null;
  position_id?: number | null;
}

export interface AccessPolicy {
  require_role_and_position: boolean;
  strict_approvals: boolean;
}

export interface ApprovalLogEntry {
  approval_id: number;
  entity_type: string;
  entity_id: number;
  gate: string;
  rule: string;
  summary: string;
  tier: string;
  sequence: number;
  decision: string;
  reasoning: string;
  decided_at: string;
  decided_by: string | null;
  requested_by: string | null;
  requested_at: string;
  chain_status: string;
}

export interface PriceListItem {
  offering_name: string;
  price: number;
  /** volume tier: this price from this many units up (1 = the plain price) */
  min_quantity?: number;
}

export interface PriceList {
  id: number;
  name: string;
  currency: string;
  adjustment_pct: number;
  is_default: boolean;
  is_active: boolean;
  valid_from?: string | null;
  valid_to?: string | null;
  in_effect?: boolean;
  items: PriceListItem[];
}

export type PriceListInput = Omit<PriceList, "id" | "in_effect">;

export interface ProcessRole {
  role_key: string;
  label: string;
  position_id: number | null;
  position_name: string | null;
}

// ---------------------------------------------------------------- product configuration

export interface OrderItemConfiguration {
  status?: string;
  quantity?: number;
  unit_price?: number | null;
  discount_pct?: number | null;
  net_unit_price?: number | null;
  order_item_id: number;
  offering_name: string | null;
  pricing_attributes: Record<string, string>;
  technical_attributes: Record<string, string>;
  missing: string[];
}

export interface PriceImpact {
  value: string;
  type: "flat" | "percent" | "per_unit";
  amount: number;
}

export interface DesignedAttribute {
  id: number;
  name: string;
  kind: "pricing" | "technical";
  data_type: string;
  unit: string | null;
  values: string[];
  required: boolean;
  default: string | null;
  help: string | null;
  visible_when: { attribute: string; equals: string } | null;
  price_impacts: PriceImpact[];
}

export interface DesignedRule {
  id: number;
  if_attribute: string;
  operator: string;
  if_value: string | null;
  then_attribute: string;
  effect: "required" | "requires" | "excludes";
  then_value: string | null;
  message: string | null;
}

export interface ProductDesign {
  offering_name: string | null;
  designed: boolean;
  attributes: DesignedAttribute[];
  rules: DesignedRule[];
  list_price?: number;
}

export interface ProductDesignSummary {
  offering_name: string;
  pricing: number;
  technical: number;
  rules: number;
}

export type DesignOp = Record<string, unknown> & { op: string };

export interface DesignPlan {
  ops: DesignOp[];
  engine: "ai" | "rules";
  reply: string;
  preview: string[];
}

export interface PriceQuote {
  ok: boolean;
  errors: string[];
  price_list?: string | null;
  price_source?: string;
  base_price?: number;
  lines?: PriceLine[];
  configured_price?: number;
  discount_applied_pct?: number;
  unit_price?: number;
  total?: number;
}

// ---------------------------------------------------------------- Fulfillment (COM / SOM / TOM order orchestration)

export type FulfillmentLayer = "COM" | "SOM" | "TOM";
export type FulfillmentTaskStatus = "pending" | "running" | "waiting" | "completed" | "skipped" | "failed";
export type FulfillmentOrderStatus = "in_progress" | "waiting" | "fallout" | "completed" | "cancelled";

export interface FulfillmentNode {
  id: string;
  layer: FulfillmentLayer;
  function: string;
  label: string;
  x: number;
  y: number;
  /** settings of the function, plus the "only when" condition (when_field / when_operator / when_value) */
  config: Record<string, string>;
  /** an AI plan may never leave it out */
  mandatory?: boolean;
}

export interface FulfillmentGraph {
  nodes: FulfillmentNode[];
  /** target waits for source - within one lane */
  edges: { id: string; source: string; target: string }[];
}

export interface FulfillmentPattern {
  id: number;
  name: string;
  description: string;
  products: string;
  segment: string;
  order_type: string;
  priority: number;
  mode: "deterministic" | "llm";
  /** vanilla (INT-n) / custom (FLOW-n) intents that start it, comma-separated - blank = Order Control approval */
  trigger_intents: string;
  started_by: string[];
  graph: FulfillmentGraph;
  version: number;
  is_active: boolean;
  validation_errors: string[];
  updated_at: string;
}

export type FulfillmentPatternInput = Pick<FulfillmentPattern, "name" | "description" | "products" | "segment" | "order_type" | "priority" | "mode" | "trigger_intents" | "graph">;

export interface FulfillmentTriggerIntent {
  code: string;
  label: string;
  kind: "builtin" | "custom";
  target: string | null;
}

/** A pattern drafted from plain English (backend: orchestration/fulfillment_ai.py) - not saved yet. */
export interface FulfillmentDraft {
  pattern: FulfillmentPatternInput;
  source: "llm" | "rules";
  model: string | null;
  notes: string;
  fixes: string[];
  validation_errors: string[];
}

export interface FulfillmentFunction {
  function: string;
  layer: FulfillmentLayer | "*";
  label: string;
  etom: string;
  description: string;
  config: string[];
  route?: FulfillmentLayer;
  waits?: boolean;
  inventory?: string;
  default_resources?: string[];
  presets: { label: string; config: Record<string, string> }[];
}

export interface FulfillmentCatalog {
  layers: { layer: FulfillmentLayer; label: string; etom: string }[];
  functions: FulfillmentFunction[];
  modes: string[];
  work_order_kinds: string[];
  logical_resources: string[];
  physical_resources: string[];
  operators: string[];
  condition_fields: string[];
  products: string[];
  templates: string[];
  trigger_intents: FulfillmentTriggerIntent[];
  default_trigger: string;
  /** the model drafting from plain English ("own model (...)" / "external API (...)"), null = built-in reader */
  ai: string | null;
}

export interface FulfillmentPreview {
  pattern: string;
  mode: string;
  errors: string[];
  note: string;
  order: Record<string, unknown> | null;
  layers: { layer: FulfillmentLayer; steps: { id: string; label: string; function: string; depends_on: string[]; etom: string; runs: boolean }[] }[];
}

export interface FulfillmentTask {
  id: number;
  node_id: string;
  function: string;
  label: string;
  sequence: number;
  status: FulfillmentTaskStatus;
  depends_on: string[];
  config: Record<string, string>;
  request: Record<string, unknown>;
  response: Record<string, unknown>;
  error: FlowError | null;
  work_order_id: number | null;
  external_ref: string;
  child_order_id: number | null;
  attempts: number;
  note: string;
  etom: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface FulfillmentResource {
  type: string;
  kind: "logical" | "physical";
  identifier: string;
  status: string;
}

export interface FulfillmentOrderView {
  id: number;
  number: string;
  layer: FulfillmentLayer;
  status: FulfillmentOrderStatus;
  crm_order_id: number;
  parent_id: number | null;
  pattern: string | null;
  pattern_id: number | null;
  pattern_version: number;
  /** deterministic · llm · llm_fallback (the designed plan, the model being unavailable or its plan unusable) */
  planned_by: string;
  plan_note: string;
  created_at: string;
  completed_at: string | null;
  billing: Record<string, unknown>;
  resources: FulfillmentResource[];
  work: Record<string, Record<string, unknown>>;
  order: Record<string, unknown>;
  tasks: FulfillmentTask[];
  children?: FulfillmentOrderView[];
}

export interface FulfillmentOrderSummary {
  id: number;
  number: string;
  status: FulfillmentOrderStatus;
  crm_order_id: number;
  created_at: string;
  completed_at: string | null;
  pattern: string | null;
  planned_by: string;
  /** the deepest layer still open */
  stage: FulfillmentLayer;
  stage_status: FulfillmentOrderStatus;
  fallout: string[];
  waiting: string[];
  tasks_done: number;
  tasks_total: number;
}
