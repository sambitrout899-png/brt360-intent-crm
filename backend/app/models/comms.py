"""Communications industry layer (Siebel Communications Guide).

Everything here sits beside the existing records - a party, an asset, an
opportunity, a trouble ticket, an agreement keep their own columns; these
tables add what a communications service provider needs on top:

  accounts      hierarchy and account classes, contacts, profiles (billing,
                exemption, financial, fraud, loyalty, site, statement),
                validated addresses, special rating (friends & family) lists
  premises      premises, service points, equipment and infrastructure
  assets        related assets, asset transactions, change requests
  opportunities related sites, partners, qualification profile
  billing       usage, allowances, invoices and their lines, payments,
                adjustment requests, payment arrangements, duplicate bills
  credit        credit alerts and collection actions
  fraud         fraud alerts
  service       trouble ticket details (parent / child, fallout orders,
                group, contact, premises), solutions, satisfaction surveys
  agreements    agreement details, revisions and documents
  work orders   provider work orders

Money is in the tenant's currency, times are naive UTC.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

ACCOUNT_CLASSES = ("customer", "service_aggregator", "service", "billing_aggregator", "billing")
PROFILE_KINDS = ("customer", "exemption", "financial", "fraud", "loyalty", "site", "statement")


# ------------------------------------------------------------------ accounts


class CmAccountDetail(Base):
    """Where a party sits in its account hierarchy and what it does there."""

    __tablename__ = "cm_account_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), unique=True)
    parent_party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account_class: Mapped[str] = mapped_column(String(30), default="customer")
    account_type: Mapped[str] = mapped_column(String(20), default="")  # residential / business
    site: Mapped[str] = mapped_column(String(200), default="")
    synonyms: Mapped[str] = mapped_column(String(300), default="")
    good_standing: Mapped[bool] = mapped_column(Boolean, default=True)  # False = centrally blocked
    block_reason: Mapped[str] = mapped_column(String(200), default="")
    partner: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_assignment: Mapped[bool] = mapped_column(Boolean, default=False)
    primary_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    region: Mapped[str] = mapped_column(String(100), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class CmContact(Base):
    __tablename__ = "cm_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    preferred_channel: Mapped[str] = mapped_column(String(20), default="email")  # email / phone / sms
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmContactAccount(Base):
    __tablename__ = "cm_contact_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("cm_contacts.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    role: Mapped[str] = mapped_column(String(50), default="")  # decision maker / billing / technical / user
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class CmBillingProfile(Base):
    """How an account is billed. Only the last four digits of a card or a
    bank account are ever kept - the rest stays with the payment provider."""

    __tablename__ = "cm_billing_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    billing_account_id: Mapped[int | None] = mapped_column(ForeignKey("billing_accounts.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    frequency: Mapped[str] = mapped_column(String(20), default="monthly")  # monthly / quarterly / annual
    bill_day: Mapped[int] = mapped_column(Integer, default=1)
    payment_type: Mapped[str] = mapped_column(String(20), default="postpaid")  # postpaid / prepaid
    payment_method: Mapped[str] = mapped_column(String(20), default="bill_me")  # credit_card / auto_debit / bill_me
    card_brand: Mapped[str] = mapped_column(String(20), default="")
    card_last4: Mapped[str] = mapped_column(String(4), default="")
    bank_last4: Mapped[str] = mapped_column(String(4), default="")
    card_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[str] = mapped_column(String(200), default="")
    bill_media: Mapped[str] = mapped_column(String(20), default="email")  # paper / email / portal / sms
    email: Mapped[str] = mapped_column(String(200), default="")
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    address: Mapped[str] = mapped_column(String(400), default="")
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=21)
    auto_topup: Mapped[bool] = mapped_column(Boolean, default=False)
    topup_amount: Mapped[float] = mapped_column(Float, default=0.0)
    topup_threshold: Mapped[float] = mapped_column(Float, default=0.0)
    sms_notify: Mapped[bool] = mapped_column(Boolean, default=False)
    prepaid_balance: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="green")  # green / yellow (in collections) / red (blocked)
    sub_status: Mapped[str] = mapped_column(String(30), default="")  # entered_collections / exited_collections
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmProfile(Base):
    """One account profile of a kind (exemption, financial, fraud, loyalty,
    site, statement, customer). Its fields are in data_json."""

    __tablename__ = "cm_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200), default="")
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class CmAddress(Base):
    """An account address; validated against the street guide it can become a premises."""

    __tablename__ = "cm_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw: Mapped[str] = mapped_column(String(400), default="")
    house_number: Mapped[str] = mapped_column(String(20), default="")
    street: Mapped[str] = mapped_column(String(200), default="")
    unit: Mapped[str] = mapped_column(String(50), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    region: Mapped[str] = mapped_column(String(100), default="")
    postal_code: Mapped[str] = mapped_column(String(20), default="")
    country: Mapped[str] = mapped_column(String(60), default="")
    kind: Mapped[str] = mapped_column(String(20), default="service")  # service / billing / mailing
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    validated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    standard_form: Mapped[str] = mapped_column(String(400), default="")
    access_instruction: Mapped[str] = mapped_column(String(300), default="")
    premise_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmRatingList(Base):
    """A special rating list (e.g. friends & family numbers) of an account."""

    __tablename__ = "cm_rating_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    name: Mapped[str] = mapped_column(String(200))
    product: Mapped[str] = mapped_column(String(200), default="")  # the special rating product it serves
    list_type: Mapped[str] = mapped_column(String(20), default="phone_number")
    max_items: Mapped[int] = mapped_column(Integer, default=5)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[str] = mapped_column(String(300), default="")
    last_sync_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmRatingItem(Base):
    __tablename__ = "cm_rating_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    list_id: Mapped[int] = mapped_column(ForeignKey("cm_rating_lists.id"))
    value: Mapped[str] = mapped_column(String(50))
    start_date: Mapped[dt.datetime] = mapped_column(default=utcnow)
    end_date: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    inactive: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------ premises


class CmPremise(Base):
    """A physical location services can be delivered to. Never deleted, so
    its service and usage history outlives any one occupant."""

    __tablename__ = "cm_premises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    address: Mapped[str] = mapped_column(String(400))
    city: Mapped[str] = mapped_column(String(100), default="")
    region: Mapped[str] = mapped_column(String(100), default="")
    postal_code: Mapped[str] = mapped_column(String(20), default="")
    premise_type: Mapped[str] = mapped_column(String(30), default="building")  # building / apartment / land / office
    buildings: Mapped[int] = mapped_column(Integer, default=1)
    floors: Mapped[int] = mapped_column(Integer, default=1)
    read_route: Mapped[str] = mapped_column(String(50), default="")
    read_sequence: Mapped[int] = mapped_column(Integer, default=0)
    bill_group: Mapped[str] = mapped_column(String(50), default="")
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # current occupant
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / vacant
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmServicePoint(Base):
    """A network delivery point at a premises (a jack, a port, a meter)."""

    __tablename__ = "cm_service_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    premise_id: Mapped[int] = mapped_column(ForeignKey("cm_premises.id"))
    identifier: Mapped[str] = mapped_column(String(100))
    product: Mapped[str] = mapped_column(String(200), default="")
    service_type: Mapped[str] = mapped_column(String(50), default="")  # voice / data / video / mobile
    location: Mapped[str] = mapped_column(String(200), default="")
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the service delivered over it
    installed_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    removed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class CmSiteItem(Base):
    """Installed or planned equipment (ours or someone else's) and
    infrastructure attributes at an account's or a premises' site."""

    __tablename__ = "cm_site_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    premise_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="equipment")  # equipment / other_equipment / infrastructure
    service_type: Mapped[str] = mapped_column(String(50), default="")
    name: Mapped[str] = mapped_column(String(200))  # product or attribute
    value: Mapped[str] = mapped_column(String(200), default="")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    owner: Mapped[str] = mapped_column(String(200), default="")  # supplier / owner organisation
    planned: Mapped[bool] = mapped_column(Boolean, default=False)
    install_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


# ------------------------------------------------------------------ assets


class CmAssetRelation(Base):
    __tablename__ = "cm_asset_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    related_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    relation: Mapped[str] = mapped_column(String(20), default="backup")  # backup / option / component / redundant
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmAssetTransaction(Base):
    __tablename__ = "cm_asset_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    component_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # install / replace / remove / upgrade / move
    description: Mapped[str] = mapped_column(String(400), default="")
    at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmChangeRequest(Base):
    __tablename__ = "cm_change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    number: Mapped[str] = mapped_column(String(30), default="")
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cr_type: Mapped[str] = mapped_column(String(30), default="enhancement")  # enhancement / defect / configuration
    area: Mapped[str] = mapped_column(String(100), default="")
    subarea: Mapped[str] = mapped_column(String(100), default="")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / assigned / in_progress / closed / rejected
    owner_name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


# ------------------------------------------------------------------ opportunities


class CmOpportunityLink(Base):
    """An extra account on an opportunity: a related site or a partner."""

    __tablename__ = "cm_opportunity_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    kind: Mapped[str] = mapped_column(String(20), default="related_site")  # related_site / partner
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmOpportunityProfile(Base):
    __tablename__ = "cm_opportunity_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), unique=True)
    answers_json: Mapped[str] = mapped_column(Text, default="{}")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    target_market: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


# ------------------------------------------------------------------ billing


class CmUsage(Base):
    """One rated usage event (a call, a message, a data session)."""

    __tablename__ = "cm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service: Mapped[str] = mapped_column(String(100), default="")  # the service / number it was used on
    event_type: Mapped[str] = mapped_column(String(20), default="voice")  # voice / sms / data
    at: Mapped[dt.datetime] = mapped_column(DateTime)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)  # minutes / messages / MB
    destination: Mapped[str] = mapped_column(String(100), default="")
    origin_location: Mapped[str] = mapped_column(String(100), default="")
    destination_location: Mapped[str] = mapped_column(String(100), default="")
    international: Mapped[bool] = mapped_column(Boolean, default=False)
    premium: Mapped[bool] = mapped_column(Boolean, default=False)  # a sensitive / premium-rate number
    rate_plan: Mapped[str] = mapped_column(String(30), default="standard")  # peak / off_peak / international
    amount: Mapped[float] = mapped_column(Float, default=0.0)  # rated charge before allowances
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CmAllowance(Base):
    """A balance group resource: free minutes, messages or data for a period."""

    __tablename__ = "cm_allowances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    name: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(20), default="voice")
    granted: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(20), default="minutes")
    valid_from: Mapped[dt.date] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    shared: Mapped[bool] = mapped_column(Boolean, default=True)  # across all the account's services


class CmInvoice(Base):
    __tablename__ = "cm_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    billing_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invoice_number: Mapped[str] = mapped_column(String(30), default="")
    period_start: Mapped[dt.date] = mapped_column(Date)
    period_end: Mapped[dt.date] = mapped_column(Date)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    due_date: Mapped[dt.date] = mapped_column(Date)
    recurring: Mapped[float] = mapped_column(Float, default=0.0)
    one_time: Mapped[float] = mapped_column(Float, default=0.0)
    usage: Mapped[float] = mapped_column(Float, default=0.0)
    adjustments: Mapped[float] = mapped_column(Float, default=0.0)
    taxes: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    paid: Mapped[float] = mapped_column(Float, default=0.0)
    credited: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / partially_paid / paid / overdue / written_off
    bill_media: Mapped[str] = mapped_column(String(20), default="email")
    late_fee_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmInvoiceLine(Base):
    __tablename__ = "cm_invoice_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("cm_invoices.id"))
    kind: Mapped[str] = mapped_column(String(20))  # recurring / one_time / usage / adjustment / tax / late_fee / payment_plan
    description: Mapped[str] = mapped_column(String(400))
    service: Mapped[str] = mapped_column(String(200), default="")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    billing_adjustment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the ledger entry it bills


class CmPayment(Base):
    """A payment against the account balance, one or more invoices or a
    billing profile. Never a full card or bank number."""

    __tablename__ = "cm_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    payment_number: Mapped[str] = mapped_column(String(30), default="")
    billing_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit_alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    arrangement_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20))  # credit_card / eft / bank_transfer / cash / cheque / reference / order_payment
    card_brand: Mapped[str] = mapped_column(String(20), default="")
    card_last4: Mapped[str] = mapped_column(String(4), default="")
    bank_last4: Mapped[str] = mapped_column(String(4), default="")
    reference: Mapped[str] = mapped_column(String(100), default="")
    location: Mapped[str] = mapped_column(String(100), default="")
    applied_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"invoice_id", "amount"}]
    unapplied: Mapped[float] = mapped_column(Float, default=0.0)  # left on account as credit
    status: Mapped[str] = mapped_column(String(20), default="posted")  # posted / failed / reversed
    billing_adjustment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmAdjustmentRequest(Base):
    __tablename__ = "cm_adjustment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    request_number: Mapped[str] = mapped_column(String(30), default="")
    level: Mapped[str] = mapped_column(String(20), default="invoice")  # account / invoice / line / unbilled
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invoice_line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit_alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adjustment_type: Mapped[str] = mapped_column(String(10), default="credit")  # credit / debit
    reason: Mapped[str] = mapped_column(String(30), default="billing_error")
    comments: Mapped[str] = mapped_column(String(400), default="")
    original_amount: Mapped[float] = mapped_column(Float, default=0.0)
    requested_amount: Mapped[float] = mapped_column(Float, default=0.0)
    requested_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="submitted")  # submitted / pending_approval / posted / partially_posted / not_posted
    status_description: Mapped[str] = mapped_column(String(300), default="")
    customer_decision: Mapped[str] = mapped_column(String(20), default="")  # accepted / rejected
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    billing_adjustment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmPaymentArrangement(Base):
    __tablename__ = "cm_payment_arrangements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    arrangement_number: Mapped[str] = mapped_column(String(30), default="")
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit_alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_type: Mapped[str] = mapped_column(String(20), default="installment")  # installment / deferral
    installments: Mapped[int] = mapped_column(Integer, default=1)
    original_amount: Mapped[float] = mapped_column(Float, default=0.0)
    original_due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    installment_amount: Mapped[float] = mapped_column(Float, default=0.0)
    interest_charge: Mapped[float] = mapped_column(Float, default=0.0)
    total_due: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / approved / countered / rejected / active / completed / broken
    outcome: Mapped[str] = mapped_column(String(300), default="")
    schedule_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"due", "amount", "paid"}]
    customer_decision: Mapped[str] = mapped_column(String(20), default="")
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    comments: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmDuplicateRequest(Base):
    __tablename__ = "cm_duplicate_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("cm_invoices.id"))
    media: Mapped[str] = mapped_column(String(20), default="email")
    address: Mapped[str] = mapped_column(String(400), default="")
    comments: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="sent")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


# ------------------------------------------------------------------ credit and fraud


class CmCreditAlert(Base):
    __tablename__ = "cm_credit_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    alert_number: Mapped[str] = mapped_column(String(30), default="")
    billing_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agreement_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_owed: Mapped[float] = mapped_column(Float, default=0.0)
    days_overdue: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / pending / closed / rejected / cancelled
    sub_status: Mapped[str] = mapped_column(String(30), default="")  # entered_collections / exited_collections
    action_type: Mapped[str] = mapped_column(String(40), default="no_action")
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="internal")  # internal / collections
    owner_name: Mapped[str] = mapped_column(String(200), default="")
    comments: Mapped[str] = mapped_column(Text, default="")
    history_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"at", "action", "note"}]
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class CmFraudAlert(Base):
    __tablename__ = "cm_fraud_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    alert_number: Mapped[str] = mapped_column(String(30), default="")
    fraud_type: Mapped[str] = mapped_column(String(30), default="exceeds_threshold")  # exceeds_threshold / invalid_calls / stolen_handset / subscription
    fraud_event: Mapped[str] = mapped_column(String(40), default="")  # daily_value / international / sensitive_numbers / simultaneous_calls / ...
    level: Mapped[str] = mapped_column(String(20), default="possible")  # possible / likely / certain
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    observed: Mapped[float] = mapped_column(Float, default=0.0)
    threshold_overflow: Mapped[float] = mapped_column(Float, default=0.0)
    service: Mapped[str] = mapped_column(String(100), default="")
    usage_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / investigating / confirmed / false_positive / closed
    contact_attempts: Mapped[int] = mapped_column(Integer, default=0)
    owner_name: Mapped[str] = mapped_column(String(200), default="")
    comments: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------ service


class CmTicketDetail(Base):
    """What a trouble ticket carries in communications: its parent (a network
    ticket many customer tickets hang off), group, contact, premises, the
    order that failed when it is a fallout ticket, and how it was closed."""

    __tablename__ = "cm_ticket_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    ticket_id: Mapped[int] = mapped_column(ForeignKey("trouble_tickets.id"), unique=True)
    ticket_number: Mapped[str] = mapped_column(String(30), default="")
    parent_ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="customer")  # customer / network / fallout
    ref_number: Mapped[str] = mapped_column(String(60), default="")
    group_name: Mapped[str] = mapped_column(String(100), default="")
    owner_name: Mapped[str] = mapped_column(String(200), default="")
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    premise_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_item: Mapped[str] = mapped_column(String(100), default="")  # exchange / circuit / number the trouble is on
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str] = mapped_column(String(60), default="")
    global_error_code: Mapped[str] = mapped_column(String(60), default="")
    failure_code: Mapped[str] = mapped_column(String(60), default="")
    system_of_failure: Mapped[str] = mapped_column(String(60), default="")
    sub_status: Mapped[str] = mapped_column(String(30), default="")  # pending_verification / resolved / reopened
    solution_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution: Mapped[str] = mapped_column(Text, default="")
    customer_notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    reopened_count: Mapped[int] = mapped_column(Integer, default=0)


class CmSolution(Base):
    """A knowledge base answer - how a problem was (or can be) resolved."""

    __tablename__ = "cm_solutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    title: Mapped[str] = mapped_column(String(200))
    problem: Mapped[str] = mapped_column(Text, default="")
    resolution: Mapped[str] = mapped_column(Text, default="")
    product_line: Mapped[str] = mapped_column(String(100), default="")
    keywords: Mapped[str] = mapped_column(String(300), default="")
    uses: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmSurvey(Base):
    __tablename__ = "cm_surveys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    ticket_id: Mapped[int] = mapped_column(ForeignKey("trouble_tickets.id"))
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="phone")  # phone / email / letter / in_person
    overall: Mapped[int] = mapped_column(Integer, default=0)  # 1..5
    timeliness: Mapped[int] = mapped_column(Integer, default=0)
    professionalism: Mapped[int] = mapped_column(Integer, default=0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=True)
    would_recommend: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0..10
    comments: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)  # 0..100
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


# ------------------------------------------------------------------ agreements and work orders


class CmAgreementDetail(Base):
    __tablename__ = "cm_agreement_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    agreement_id: Mapped[int] = mapped_column(ForeignKey("om_agreements.id"), unique=True)
    parent_agreement_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    base_name: Mapped[str] = mapped_column(String(200), default="")
    agreement_type: Mapped[str] = mapped_column(String(30), default="service")  # contract / sales / service / sla / master
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    approver: Mapped[str] = mapped_column(String(200), default="")
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    po_number: Mapped[str] = mapped_column(String(60), default="")
    vendor: Mapped[str] = mapped_column(String(200), default="")
    account_ids_json: Mapped[str] = mapped_column(Text, default="[]")  # more accounts it covers
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terms_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"term", "value"}]
    order_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    document: Mapped[str] = mapped_column(Text, default="")
    document_generated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    signed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class CmAgreementRevision(Base):
    __tablename__ = "cm_agreement_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    agreement_id: Mapped[int] = mapped_column(ForeignKey("om_agreements.id"))
    revision: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(300), default="")
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")  # the agreement before
    changes_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CmProvider(Base):
    """A supplier that activates part of an order (a mobile operator, an ISP,
    the local exchange carrier) - lines go to it by product keyword."""

    __tablename__ = "cm_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    keywords: Mapped[str] = mapped_column(String(300), default="")
    lead_days: Mapped[int] = mapped_column(Integer, default=5)
    contact: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CmProviderWorkOrder(Base):
    __tablename__ = "cm_provider_work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    number: Mapped[str] = mapped_column(String(30), default="")
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    provider: Mapped[str] = mapped_column(String(200))
    lines_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"line_id", "product", "quantity", "action"}]
    status: Mapped[str] = mapped_column(String(20), default="submitted")  # submitted / in_progress / backordered / complete / failed
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    payment_terms: Mapped[str] = mapped_column(String(50), default="")
    freight_terms: Mapped[str] = mapped_column(String(50), default="")
    return_to: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    notify_on_complete: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class CmSetting(Base):
    """Per-tenant communications policy (collections ladder, fraud defaults,
    adjustment limits, usage rates, survey and qualification questions...)."""

    __tablename__ = "cm_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), unique=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)
