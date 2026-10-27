/** Shapes returned by /api/comms (crm_core/comm_service.py, comm_billing.py). */

export interface TreeNode {
  party_id: number;
  name: string;
  account_class: string;
  site: string;
  good_standing: boolean;
  balance: number;
  services: number;
  rollup_balance: number;
  rollup_services: number;
  children: TreeNode[];
}

export interface Contact {
  id: number;
  name: string;
  title: string;
  email: string;
  phone: string;
  accounts: { party_id: number; name: string; role: string; primary: boolean }[];
}

export interface BillingProfile {
  billing_account_id?: number | null;
  id: number;
  name: string;
  primary: boolean;
  frequency: string;
  bill_day: number;
  payment_type: string;
  payment_method: string;
  card: string;
  bank_account: string;
  bill_media: string;
  email: string;
  payment_terms_days: number;
  auto_topup: boolean;
  topup_amount: number;
  prepaid_balance: number;
  status: string;
  sub_status: string;
}

export interface Loyalty {
  churn_risk: number;
  churn_band: string;
  lifetime_value: number;
  monthly_revenue: number;
  debt_risk: number;
  satisfaction: number | null;
  upsell: { product: string; kind: string; message: string }[];
  reasons: string[];
}

export interface RatingList {
  id: number;
  name: string;
  product: string;
  max_items: number;
  active_items: number;
  last_sync_at: string | null;
  items: { id: number; value: string; inactive: boolean }[];
}

export interface AccountView {
  account: { party_id: number; name: string; account_class: string; account_type: string; parent_name: string; site: string; good_standing: boolean; block_reason: string; partner: boolean };
  hierarchy: TreeNode;
  contacts: Contact[];
  billing_profiles: BillingProfile[];
  profiles: Record<string, Record<string, unknown>>;
  loyalty: Loyalty;
  addresses: { id: number; raw: string; standard_form: string; kind: string; primary: boolean; validated: boolean; premise_id: number | null }[];
  premises: { id: number; address: string; occupant: string; status: string; service_points: number }[];
  site_items: { id: number; kind: string; name: string; value: string; quantity: number; owner: string; planned: boolean }[];
  rating_lists: RatingList[];
  change_requests: { id: number; number: string; type: string; status: string; priority: string; description: string }[];
  agreements: { id: number; agreement_number: string; name: string; status: string; end_date: string }[];
}

export interface InvoiceLine {
  id: number;
  kind: string;
  description: string;
  quantity: number;
  amount: number;
}

export interface Invoice {
  billing_profile_id?: number | null;
  id: number;
  invoice_number: string;
  party_id: number;
  customer: string;
  period_start: string;
  period_end: string;
  issue_date: string;
  due_date: string;
  recurring: number;
  one_time: number;
  usage: number;
  adjustments: number;
  taxes: number;
  total: number;
  paid: number;
  credited: number;
  balance_due: number;
  status: string;
  lines?: InvoiceLine[];
}

export interface Payment {
  billing_profile_id?: number | null;
  id: number;
  payment_number: string;
  amount: number;
  method: string;
  card: string;
  reference: string;
  unapplied: number;
  received_at: string;
  applied: { invoice_id: number; invoice_number: string; amount: number }[];
}

export interface Adjustment {
  id: number;
  request_number: string;
  party_id: number;
  customer?: string;
  invoice_number?: string | null;
  level: string;
  type: string;
  reason: string;
  requested_amount: number;
  approved_amount: number;
  status: string;
  status_description: string;
  customer_decision: string;
}

export interface Arrangement {
  id: number;
  number: string;
  party_id: number;
  customer?: string;
  plan_type: string;
  installments: number;
  installment_amount: number;
  total_due: number;
  status: string;
  outcome: string;
  customer_decision: string;
  schedule: { due: string; amount: number; paid: number }[];
}

export interface CreditAlert {
  id: number;
  alert_number: string;
  party_id: number;
  customer: string;
  amount_owed: number;
  days_overdue: number;
  priority: string;
  status: string;
  sub_status: string;
  action_type: string;
  due_date: string | null;
  source: string;
  owner: string;
  history: { at: string; action: string; note: string }[];
}

export interface FraudAlert {
  id: number;
  alert_number: string;
  party_id: number;
  customer: string;
  fraud_type: string;
  fraud_event: string;
  level: string;
  threshold: number;
  observed: number;
  threshold_overflow: number;
  service: string;
  status: string;
  contact_attempts: number;
  comments: string;
  created_at: string;
  events?: { id: number; at: string; event_type: string; quantity: number; destination: string; origin_location: string; amount: number; international: boolean; premium: boolean }[];
}

export interface BillingPortal {
  billing_account: {id: number; party_id: number; status: string} | null;
  balance: number;
  overdue: number;
  invoices: Invoice[];
  payments: Payment[];
  adjustments: Adjustment[];
  arrangements: Arrangement[];
  credit_alerts: CreditAlert[];
  fraud_alerts: FraudAlert[];
  balance_groups: { id: number; name: string; unit: string; granted: number; used: number; remaining: number }[];
  usage_history: { month: string; voice: number; sms: number; data: number; amount: number; international: number }[];
  unbilled: { period_start: string; period_end: string; next_invoice_date: string; recurring: number; non_recurring: number; usage: number; surcharges: number; total: number };
  billing_profiles: BillingProfile[];
}

export interface TicketDetail {
  number: string;
  origin: string;
  parent_ticket_id: number | null;
  group: string;
  owner: string;
  sub_status: string;
  service_item: string;
  order_id: number | null;
  error_code: string;
  closed_at: string | null;
  children: number;
}

export interface CommsOverview {
  billed_this_month: number;
  open_receivables: number;
  overdue: number;
  invoices: number;
  credit_alerts_open: number;
  fraud_alerts_open: number;
  adjustments_waiting: number;
  arrangements_waiting: number;
  tickets: {
    total: number;
    today: number;
    open: number;
    mean_hours_to_resolve: number | null;
    satisfaction: number | null;
    network_parents: number;
    fallout_open: number;
    by: Record<string, Record<string, number>>;
  };
}
