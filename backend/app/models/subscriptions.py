"""Commercial licensing - what each organization has bought, at what price, for which users.

The platform's catalog (platform-level, no tenant):
  LicenseProduct    something sold: the Core platform, Core user licences, each service
                    (Telecom CRM, Training & Learning, Flow Studio ...) and add-ons (AI credit
                    packs, storage, sandbox, premier support). It grants modules (app/licensing.py
                    MODULES), is priced per unit per month in each currency, sold per user, per
                    organization or per pack, may require other products, and brings limits.
  LicenseEdition    a named bundle (Starter / Professional / Enterprise ...) - the products and
                    quantities a subscription starts from, with the edition's own discount.
  LicensingSettings one row: currencies, default currency, tax, grace period, invoice numbering,
                    the seller's details printed on invoices.

Per organization (tenant-scoped, under row-level security):
  TenantSubscription the organization's contract: edition, status, term, billing cycle, currency,
                    discounts, tax, auto-renewal. Its active lines decide the organization's
                    modules - written to tenants.licensed_modules, which every request is checked
                    against (app/licensing.py), so enforcement stays where it was.
  SubscriptionLine  one product on the contract: quantity (seats / packs), its price (the list
                    price unless overridden), its discount and dates.
  UserLicense       a seat of a per-user product held by one user (Salesforce's user / feature
                    licence). A per-user service opens only for users holding its seat.
  LicenseInvoice    a billing statement for a period, with its lines and totals.
  LicenseRequest    an organization's administrator asking for more (seats, a service, renewal).
  LicenseEvent      every change - who did what to whose licences, when.

A tenant without a subscription keeps its module list exactly as before (pre-licensing installs).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

PRODUCT_KINDS = ("core", "service", "add_on")
PRODUCT_UNITS = ("user", "org", "pack")
SUBSCRIPTION_STATUSES = ("trial", "active", "past_due", "suspended", "cancelled", "expired")
# statuses whose lines grant modules (past_due keeps working during the grace period)
GRANTING = ("trial", "active", "past_due")
BILLING_CYCLES = ("monthly", "quarterly", "annual")
CYCLE_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}
INVOICE_STATUSES = ("draft", "issued", "paid", "void")
REQUEST_STATUSES = ("open", "approved", "declined")


class LicenseProduct(Base):
    __tablename__ = "license_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(20), default="service")  # PRODUCT_KINDS
    unit: Mapped[str] = mapped_column(String(10), default="user")  # PRODUCT_UNITS
    unit_label: Mapped[str] = mapped_column(String(40), default="user")  # "user", "organization", "1M AI tokens", "10 GB"
    description: Mapped[str] = mapped_column(Text, default="")
    features: Mapped[list | None] = mapped_column(JSON, nullable=True)  # bullet points shown on the price sheet
    modules: Mapped[list | None] = mapped_column(JSON, nullable=True)  # module keys it grants
    prices: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"INR": 1500, "USD": 18} per unit per month
    annual_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)  # off the monthly price on an annual term
    min_quantity: Mapped[int] = mapped_column(Integer, default=1)
    max_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requires: Mapped[list | None] = mapped_column(JSON, nullable=True)  # product codes that must be on the contract too
    # limits one unit brings, added up over the contract: {"ai_tokens_month": 1000000, "storage_gb": 10, "users": 1}
    limits: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class LicenseEdition(Base):
    __tablename__ = "license_editions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"product": code, "quantity": n}]
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    highlight: Mapped[bool] = mapped_column(Boolean, default=False)  # "most popular" on the price sheet
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class LicensingSettings(Base):
    __tablename__ = "licensing_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    currencies: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["INR", "USD"]
    default_currency: Mapped[str] = mapped_column(String(3), default="INR")
    tax_name: Mapped[str] = mapped_column(String(30), default="GST")
    tax_pct: Mapped[float] = mapped_column(Float, default=18.0)
    grace_days: Mapped[int] = mapped_column(Integer, default=15)
    invoice_prefix: Mapped[str] = mapped_column(String(20), default="BRT-INV-")
    next_invoice_no: Mapped[int] = mapped_column(Integer, default=1)
    seller_name: Mapped[str] = mapped_column(String(200), default="")
    seller_address: Mapped[str] = mapped_column(Text, default="")
    seller_tax_id: Mapped[str] = mapped_column(String(60), default="")
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TenantSubscription(Base):
    __tablename__ = "tenant_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), unique=True)
    edition_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="trial")  # SUBSCRIPTION_STATUSES
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    billing_cycle: Mapped[str] = mapped_column(String(10), default="annual")  # BILLING_CYCLES
    term_months: Mapped[int] = mapped_column(Integer, default=12)
    term_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    term_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    trial_ends: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)  # on the whole contract, after line discounts
    tax_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = the platform's
    grace_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = the platform's
    auto_assign_seats: Mapped[bool] = mapped_column(Boolean, default=True)  # new users get a free seat of each per-user product
    contract_ref: Mapped[str] = mapped_column(String(80), default="")
    po_number: Mapped[str] = mapped_column(String(80), default="")
    billing_contact: Mapped[str] = mapped_column(String(200), default="")
    billing_email: Mapped[str] = mapped_column(String(200), default="")
    billing_address: Mapped[str] = mapped_column(Text, default="")
    tax_id: Mapped[str] = mapped_column(String(60), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status_reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class SubscriptionLine(Base):
    __tablename__ = "subscription_lines"
    __table_args__ = (UniqueConstraint("subscription_id", "product_code", name="uq_subscription_line_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("tenant_subscriptions.id"), index=True)
    product_code: Mapped[str] = mapped_column(String(60))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = the catalog price in the contract's currency
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)  # None = the term's
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class UserLicense(Base):
    __tablename__ = "user_licenses"
    __table_args__ = (UniqueConstraint("user_id", "product_code", name="uq_user_license"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_code: Mapped[str] = mapped_column(String(60))
    assigned_by: Mapped[str] = mapped_column(String(200), default="")
    assigned_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class LicenseInvoice(Base):
    __tablename__ = "license_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="issued")  # INVOICE_STATUSES
    currency: Mapped[str] = mapped_column(String(3))
    period_start: Mapped[dt.date] = mapped_column(Date)
    period_end: Mapped[dt.date] = mapped_column(Date)
    issued_on: Mapped[dt.date] = mapped_column(Date)
    due_on: Mapped[dt.date] = mapped_column(Date)
    lines: Mapped[list | None] = mapped_column(JSON, nullable=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    discount: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    paid_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    payment_ref: Mapped[str] = mapped_column(String(120), default="")
    bill_to: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class LicenseRequest(Base):
    __tablename__ = "license_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="seats")  # seats | product | renewal | other
    product_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="open")  # REQUEST_STATUSES
    requested_by: Mapped[str] = mapped_column(String(200), default="")
    response: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class LicenseEvent(Base):
    __tablename__ = "license_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)  # None = the catalog
    action: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(String(500), default="")
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(200), default="")
    at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
