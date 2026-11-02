/** Licensing & billing shapes (backend crm_core/subscription_service.py). */

export type ProductKind = "core" | "service" | "add_on";
export type ProductUnit = "user" | "org" | "pack";
export type SubStatus = "trial" | "active" | "past_due" | "suspended" | "cancelled" | "expired";

export interface LicProduct {
  id: number;
  code: string;
  name: string;
  kind: ProductKind;
  unit: ProductUnit;
  unit_label: string;
  description: string;
  features: string[];
  modules: string[];
  prices: Record<string, number>;
  annual_discount_pct: number;
  min_quantity: number;
  max_quantity: number | null;
  requires: string[];
  limits: Record<string, number>;
  trial_days: number;
  is_active: boolean;
  sort_order: number;
}

export interface LicEdition {
  id: number;
  code: string;
  name: string;
  description: string;
  items: { product: string; quantity: number }[];
  discount_pct: number;
  trial_days: number;
  highlight: boolean;
  is_active: boolean;
  sort_order: number;
  monthly_estimate: number;
  currency: string;
  modules: string[];
}

export interface LicSettings {
  currencies: string[];
  default_currency: string;
  tax_name: string;
  tax_pct: number;
  grace_days: number;
  invoice_prefix: string;
  next_invoice_no: number;
  seller_name: string;
  seller_address: string;
  seller_tax_id: string;
  payment_terms_days: number;
}

export interface LicCatalog {
  products: LicProduct[];
  editions: LicEdition[];
  settings: LicSettings;
  modules: { key: string; label: string; kind: string; description: string }[];
  limit_labels: Record<string, string>;
}

export interface PriceLine {
  product: string;
  name: string;
  kind: ProductKind;
  unit: ProductUnit;
  unit_label: string;
  quantity: number;
  list_price: number | null;
  unit_price: number | null;
  overridden: boolean;
  annual_discount_pct: number;
  discount_pct: number;
  effective_unit_price: number | null;
  monthly: number;
  priced: boolean;
  line_id?: number;
  start_date?: string | null;
  end_date?: string | null;
  notes?: string;
}

export interface Pricing {
  currency: string;
  billing_cycle: string;
  lines: PriceLine[];
  unpriced: string[];
  subtotal_monthly: number;
  discount_pct: number;
  discount_monthly: number;
  net_monthly: number;
  tax_name: string;
  tax_pct: number;
  tax_monthly: number;
  total_monthly: number;
  mrr: number;
  arr: number;
  per_period: number;
  period_months: number;
  term_value: number;
}

export interface Seat {
  product: string;
  name: string;
  kind: string;
  modules: string[];
  purchased: number;
  assigned: number;
  free: number;
  over: number;
}

export interface Subscription {
  id: number;
  tenant_id: number;
  tenant_name: string;
  tenant_slug: string;
  edition_code: string | null;
  edition_name: string | null;
  status: SubStatus;
  status_reason: string;
  granting: boolean;
  currency: string;
  billing_cycle: "monthly" | "quarterly" | "annual";
  term_months: number;
  term_start: string | null;
  term_end: string | null;
  trial_ends: string | null;
  days_left: number | null;
  auto_renew: boolean;
  discount_pct: number;
  tax_pct: number | null;
  grace_days: number | null;
  effective_grace_days: number;
  auto_assign_seats: boolean;
  contract_ref: string;
  po_number: string;
  billing_contact: string;
  billing_email: string;
  billing_address: string;
  tax_id: string;
  notes?: string;
  modules: string[];
  limits: Record<string, number>;
  pricing: Pricing;
  seats: Seat[];
  issues: string[];
  next_statuses: SubStatus[];
}

export interface Invoice {
  id: number;
  tenant_id: number;
  tenant_name?: string;
  number: string;
  status: "draft" | "issued" | "paid" | "void" | "overdue";
  currency: string;
  period_start: string;
  period_end: string;
  issued_on: string;
  due_on: string;
  lines: { product: string; description: string; quantity: number; unit_price: number; months: number; amount: number }[];
  subtotal: number;
  discount: number;
  tax: number;
  total: number;
  paid_on: string | null;
  payment_ref: string;
  bill_to: Record<string, unknown>;
}

export interface LicRequest {
  id: number;
  tenant_id: number;
  tenant_name?: string;
  kind: string;
  product_code: string | null;
  product_name: string | null;
  quantity: number;
  message: string;
  status: "open" | "approved" | "declined";
  requested_by: string;
  response: string;
  created_at: string;
  resolved_at: string | null;
}

export interface LicEvent {
  id: number;
  tenant_id: number | null;
  action: string;
  summary: string;
  actor: string;
  at: string;
}

export interface TenantView {
  tenant_name: string;
  subscription: Subscription | null;
  modules: string[];
  module_labels: Record<string, string>;
  usage: Record<string, number | null>;
  limits: { key: string; label: string; limit: number; used: number | null }[];
  users: { id: number; name: string; email: string; role: string; is_active: boolean; licenses: string[] }[];
  invoices: Invoice[];
  requests: LicRequest[];
  catalog: LicProduct[];
  events: LicEvent[];
}

export interface TenantRow {
  tenant_id: number;
  name: string;
  slug: string;
  modules: string[];
  users: number;
  subscription: { status: SubStatus; edition_code: string | null; currency: string; mrr: number; term_end: string | null; trial_ends: string | null; billing_cycle: string } | null;
}

export interface Overview {
  tenants: number;
  subscribed: number;
  unsubscribed: number;
  by_status: Record<string, number>;
  mrr: Record<string, number>;
  arr: Record<string, number>;
  by_product: Record<string, Record<string, number>>;
  seats: { purchased: number; assigned: number };
  renewals: { tenant_id: number; tenant_name: string; status: SubStatus; ends: string; days_left: number; auto_renew: boolean; mrr: number; currency: string }[];
  overdue_invoices: Invoice[];
  open_requests: number;
}
