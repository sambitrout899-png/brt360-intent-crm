export type Sla = "breached" | "at_risk" | "on_track" | "none" | "met";

export interface FsOverview {
  open_requests: number;
  unverified: number;
  breached: number;
  at_risk: number;
  open_activities: number;
  unscheduled: number;
  today: number;
  in_progress: number;
  done_today: number;
  first_time_fix: number | null;
  pm_due: number;
  parts_below_min: number;
  repairs_open: number;
  pending_charges: number;
  by_status: Record<string, number>;
  attention: FsRequest[];
}

export interface Entitlement {
  id: number;
  party_id: number;
  party_name: string | null;
  name: string;
  asset_id: number | null;
  asset_label: string | null;
  product: string;
  coverage: "business" | "24x7";
  response_hours: number;
  resolution_hours: number | null;
  labor_discount_pct: number;
  parts_discount_pct: number;
  expense_discount_pct: number;
  visits_allowed: number | null;
  visits_used: number;
  starts_on: string;
  ends_on: string | null;
  active: boolean;
  current: boolean;
}

export interface ChargeLine {
  id: number;
  kind: string;
  description: string;
  quantity: number;
  unit_price: number;
  gross: number;
  discount_pct: number;
  discount_reason: string;
  net: number;
  status: string;
  activity_id: number | null;
}
export interface Charges {
  lines: ChargeLine[];
  gross: number;
  covered: number;
  net: number;
  pending: number;
}

export interface FsActivity {
  id: number;
  work_order_id: number;
  brt_id: string | null;
  kind: string;
  ticket_id: number | null;
  party_id: number | null;
  party_name: string | null;
  asset_id: number | null;
  asset_label: string | null;
  subject: string;
  category: string;
  priority: number;
  priority_label: string;
  duration_minutes: number;
  earliest_start: string | null;
  due_at: string | null;
  planned_start: string | null;
  planned_end: string | null;
  window: string;
  status: string;
  technician_id: number | null;
  technician_name: string | null;
  region_id: number | null;
  address: string;
  skills: string[];
  locked: boolean;
  billable: boolean;
  steps_done: number;
  steps_total: number;
  late: boolean;
  close_intent: string | null;
  actual_start: string | null;
  actual_end: string | null;
  resolution_code: string;
  // full
  steps?: { text: string; done: boolean }[];
  items?: { product: string; quantity: number; tool?: boolean }[];
  instructions?: string;
  notes?: string;
  description?: string;
  time?: { id: number; kind: string; started_at: string; ended_at: string | null; minutes: number; billable: boolean; note: string }[];
  expenses?: { id: number; kind: string; amount: number; billable: boolean; note: string }[];
  parts?: { id: number; product_name: string; quantity: number; direction: string; serial_number: string; billable: boolean; unit_price: number }[];
  charges?: Charges;
  measurements?: Measurement[];
  trunk?: InvLocation | null;
  signature?: string;
}

export interface FsRequest {
  id: number;
  brt_id: string | null;
  description: string;
  category: string;
  severity: string;
  priority: string;
  product_line: string;
  status: string;
  party_id: number | null;
  party_name: string | null;
  asset_id: number | null;
  asset_label: string | null;
  opened_at: string;
  committed_at: string | null;
  verified: boolean;
  verify_note: string;
  entitlement_name: string | null;
  warranty_name: string | null;
  source: string;
  closed_at: string | null;
  resolution_code: string;
  resolution_notes: string;
  sla: Sla;
  activity_count: number;
  open_activities: number;
  root_cause: string | null;
  field_dispatch_required: boolean;
  activities?: FsActivity[];
  charges?: Charges;
  candidates?: Entitlement[];
  customer_assets?: { id: number; label: string }[];
}

export interface Candidate {
  technician_id: number;
  name: string;
  qualified: boolean;
  missing_skills: string[];
  score: number;
  available: boolean;
  next_start: string | null;
  next_end: string | null;
  late: boolean;
  breakdown: Record<string, number>;
  parts_note: string;
  jobs_that_day: number;
}

export interface Slot {
  technician_id: number;
  name: string;
  start: string;
  end: string;
  within_commitment: boolean;
  booked_minutes: number;
}

export interface BoardJob {
  id: number;
  work_order_id: number;
  subject: string;
  status: string;
  priority: number;
  priority_label: string;
  party_name: string | null;
  planned_start: string;
  planned_end: string;
  category: string;
  late: boolean;
  locked: boolean;
  ticket_id: number | null;
  kind: string;
}
export interface BoardRow {
  technician_id: number;
  name: string;
  status: string;
  skills: string[];
  region_id: number | null;
  working: boolean;
  window_start: string | null;
  window_end: string | null;
  jobs: BoardJob[];
  away: { start: string; end: string; reason: string }[];
  booked_minutes: number;
  utilization: number;
}
export interface Board {
  day: string;
  region_id: number | null;
  region_name: string | null;
  timezone: string;
  start: string;
  end: string;
  hours: { at: string; label: string }[];
  rows: BoardRow[];
  unscheduled: FsActivity[];
  now: string;
}

export interface Engineer {
  technician_id: number;
  name: string;
  status: string;
  legacy_region: string;
  region_id: number | null;
  region_name: string | null;
  skills: { skill: string; level: number }[];
  day_start: string;
  day_end: string;
  work_days: number[];
  own_hours: boolean;
  cost_per_hour: number;
  max_jobs_per_day: number;
  phone: string;
  trunk_location_id: number | null;
  trunk_name: string | null;
}

export interface Region {
  id: number;
  name: string;
  timezone: string;
  day_start: string;
  day_end: string;
  work_days: number[];
  holidays: string[];
  labor_rate: number;
  travel_minutes: number;
}

export interface Template {
  id: number;
  name: string;
  category: string;
  description: string;
  duration_minutes: number;
  priority: number;
  skills: string[];
  steps: string[];
  items: { product: string; quantity: number; tool?: boolean }[];
  instructions: string;
  billable: boolean;
  active: boolean;
}

export interface Warranty {
  id: number;
  name: string;
  products: string[];
  duration_days: number;
  covers_parts: boolean;
  covers_labor: boolean;
  covers_expenses: boolean;
  measurement: string;
  usage_limit: number | null;
  transferable: boolean;
  provider: string;
}

export interface Measurement {
  id: number;
  asset_id: number;
  name: string;
  unit: string;
  kind: string;
  min_value: number;
  max_value: number | null;
  lower_limit: number | null;
  upper_limit: number | null;
  last_value: number | null;
  last_read_at: string | null;
  alarm: boolean;
  readings: { id: number; value: number; read_at: string; source: string }[];
}

export interface FsAsset {
  id: number;
  brt_id: string | null;
  status: string;
  asset_type: string;
  identifier: string;
  party_id: number | null;
  party_name: string | null;
  product_name: string;
  serial_number: string;
  installed_on: string | null;
  parent_asset_id: number | null;
  address: string;
  region_id: number | null;
  label: string;
  warranty: { name: string; ends_on: string; days_left: number; covers_parts: boolean; covers_labor: boolean } | null;
  warranties?: { id: number; name: string; starts_on: string; ends_on: string; active: boolean }[];
  measurements?: Measurement[];
  components?: { id: number; label: string }[];
  service_requests?: { id: number; description: string; status: string; created_at: string }[];
  pm_actions?: { id: number; due_on: string; status: string; trigger_kind: string }[];
  repairs?: { id: number; repair_number: string; status: string }[];
}

export interface InvLevel {
  id: number;
  location_id: number;
  product_name: string;
  status: string;
  on_hand: number;
  reserved: number;
  available: number;
  min_qty: number;
  max_qty: number;
  unit_cost: number;
  unit_price: number;
  locator: string;
  below_min: boolean;
}
export interface InvLocation {
  id: number;
  name: string;
  kind: string;
  parent_id: number | null;
  technician_id: number | null;
  technician_name: string | null;
  region_id: number | null;
  address: string;
  active: boolean;
  levels: InvLevel[];
  value: number;
  below_min: number;
}
export interface InvTxn {
  id: number;
  kind: string;
  label: string;
  product_name: string;
  quantity: number;
  from: string;
  from_status: string;
  to: string;
  to_status: string;
  reference_type: string;
  reference_id: number | null;
  note: string;
  created_at: string;
}
export interface PartAvailability {
  product_name: string;
  total: number;
  unit_price: number;
  locations: { location_id: number; location_name: string; kind: string; technician_name: string | null; available: number }[];
}
export interface Replenishment {
  location_id: number;
  location_name: string;
  product_name: string;
  on_hand: number;
  min_qty: number;
  max_qty: number;
  need: number;
  source_id: number | null;
  source_name: string | null;
  quantity: number;
  action: string;
  done: boolean;
}

export interface Repair {
  id: number;
  repair_number: string;
  party_id: number | null;
  party_name: string | null;
  asset_id: number | null;
  ticket_id: number | null;
  product_name: string;
  serial_number: string;
  symptoms: string;
  kind: string;
  vendor: string;
  status: string;
  location_id: number | null;
  location_name: string | null;
  under_warranty: boolean;
  resolution: string;
  received_at: string;
  shipped_at: string | null;
  returned_at: string | null;
  closed_at: string | null;
  actions: string[];
}

export interface PmTrigger {
  type: "interval" | "usage" | "threshold" | "event";
  every_days?: number;
  valid_start?: number | null;
  valid_end?: number | null;
  measurement?: string;
  every?: number;
  lower?: number | null;
  upper?: number | null;
  requests?: number;
  within_days?: number;
}
export interface PmPlan {
  id: number;
  name: string;
  priority: number;
  optional: boolean;
  products: string[];
  asset_ids: number[];
  triggers: PmTrigger[];
  template_id: number | null;
  template_name: string | null;
  active: boolean;
  asset_count: number;
  scheduled: number;
  done: number;
}
export interface PmAction {
  id: number;
  plan_id: number;
  plan_name: string;
  asset_id: number;
  asset_label: string;
  trigger_kind: string;
  due_on: string;
  status: string;
  completed_on: string | null;
  ticket_id: number | null;
  activity_id: number | null;
  reading_value: number | null;
}
