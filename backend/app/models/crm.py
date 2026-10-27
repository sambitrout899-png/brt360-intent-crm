from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class OpportunityStage(str, enum.Enum):
    draft = "draft"
    qualified = "qualified"
    quoted = "quoted"
    won = "won"
    lost = "lost"
    # B2B Sales-to-Order: the Account Manager configures the product
    # (INT-37), Presales assesses it (INT-38 -> INT-18) and either clears it
    # for quoting or hands it back. Opportunities that never enter these
    # stages keep the original draft -> quoted path.
    configured = "configured"
    in_feasibility = "in_feasibility"
    feasibility_failed = "feasibility_failed"
    feasible = "feasible"


class LeadStatus(str, enum.Enum):
    pending_qualification = "pending_qualification"
    qualified = "qualified"
    rejected = "rejected"


class QuoteStatus(str, enum.Enum):
    draft = "draft"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    # Withdrawn by the requester (INT-20) - distinct from "rejected" (a
    # business decision by an approver), per SID's separation of
    # cancellation from rejection as different lifecycle transitions.
    cancelled = "cancelled"
    # Replaced by a newer revision (INT-30) after this quote was locked -
    # the locked quote is never edited in place, it is revised.
    superseded = "superseded"


class ApprovalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    conditional = "conditional"


class Party(Base):
    """Customer or prospect - HLD 5.1 Party/Account. Minimal fields for Sprint 2
    (a prospect created by INT-04); Sprint 3's billing/service entities link
    back to this same row once a prospect becomes a paying customer.
    """

    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    party_type: Mapped[str] = mapped_column(String(20), default="organization")  # organization/individual
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    # Sales-to-Order data lives beside the party (see PartyProfile), so the
    # party's own columns - and any custom field keys a tenant chose - stay
    # exactly as they were.
    profile: Mapped["PartyProfile | None"] = relationship(back_populates="party", uselist=False, cascade="all, delete-orphan")


class PartyProfile(Base):
    """B2B/B2C details of a party: segment ("B2B"/"B2C"), account_status
    ("prospect" until INT-35 makes it an "account"), the pricelist it buys
    on (None = the tenant's default) and its industry. A party without a
    profile behaves exactly as parties always did."""

    __tablename__ = "party_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), unique=True)
    segment: Mapped[str | None] = mapped_column(String(10), nullable=True)
    account_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price_list_id: Mapped[int | None] = mapped_column(ForeignKey("price_lists.id"), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)

    party: Mapped[Party] = relationship(back_populates="profile")


class TerritoryRule(Base):
    """Reference data: region -> owner. HLD 5.2 Master & Reference Data,
    used by INT-04's territory/ownership assignment rule.
    """

    __tablename__ = "territory_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "region", name="uq_territory_rules_tenant_region"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    region: Mapped[str] = mapped_column(String(100))
    owner_name: Mapped[str] = mapped_column(String(200))


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    source: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(100), default="ALL")
    owner_name: Mapped[str] = mapped_column(String(200))
    propensity_score: Mapped[float] = mapped_column(Float)
    # Sales-Director approval gate (B2B sales journey): a lead starts
    # pending_qualification, and only becomes an Opportunity once an
    # approval chain against it (entity_type "lead") reaches approved -
    # see approval_service.resolve_lead_qualification_template.
    status: Mapped[LeadStatus] = mapped_column(Enum(LeadStatus), default=LeadStatus.pending_qualification)
    # Product interest captured at lead stage, before qualification -
    # MVP-simple single offering name, same convention as
    # ProductOffering.composition's plain-string approach elsewhere.
    interested_offering_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    party: Mapped[Party] = relationship()
    opportunity: Mapped["Opportunity"] = relationship(back_populates="lead", uselist=False)
    chain_instance: Mapped["ApprovalChainInstance"] = relationship(
        viewonly=True,
        uselist=False,
        primaryjoin=(
            "and_(foreign(ApprovalChainInstance.entity_id) == Lead.id, "
            "ApprovalChainInstance.entity_type == 'lead', ApprovalChainInstance.gate == 'qualified')"
        ),
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    name: Mapped[str] = mapped_column(String(200))
    stage: Mapped[OpportunityStage] = mapped_column(Enum(OpportunityStage), default=OpportunityStage.draft)
    # The product/bundle being sold and how many - set when the lead is
    # qualified (from its product interest) or edited on the opportunity;
    # quoting (INT-05) starts from it.
    offering_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="opportunity")
    quotes: Mapped[list["Quote"]] = relationship(back_populates="opportunity")
    feasibility_assessments: Mapped[list["FeasibilityAssessment"]] = relationship(viewonly=True)
    config: Mapped["OpportunityConfig | None"] = relationship(back_populates="opportunity", uselist=False, cascade="all, delete-orphan")


class OpportunityConfig(Base):
    """Sales-to-Order: what the Account Manager configured and handed to
    Presales (INT-37/INT-38), what Presales concluded (INT-18), and who the
    Account Manager is - kept beside the opportunity's own columns."""

    __tablename__ = "opportunity_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), unique=True)
    pricing_attributes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    technical_parameters: Mapped[str | None] = mapped_column(Text, nullable=True)
    feasibility_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_manager_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The opportunity's own pricelist (beats the customer's; a quote's beats it)
    price_list_id: Mapped[int | None] = mapped_column(ForeignKey("price_lists.id"), nullable=True)
    # Technical attributes captured while selling (e.g. by Presales) - they flow to the order
    technical_attributes_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    opportunity: Mapped[Opportunity] = relationship(back_populates="config")


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    status: Mapped[QuoteStatus] = mapped_column(Enum(QuoteStatus), default=QuoteStatus.draft)
    total_price: Mapped[float] = mapped_column(Float)
    requested_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    discount_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Revision chain (Phase 7, INT-30): revision 1 is the original; each
    # revision points back at the quote it superseded.
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    revision_of_quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    opportunity: Mapped[Opportunity] = relationship(back_populates="quotes")
    items: Mapped[list["QuoteItem"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    # ApprovalChainInstance is polymorphic (entity_type/entity_id, not a
    # quote_id FK) - this relationship is query-only, matched explicitly
    # rather than via a foreign key SQLAlchemy can infer on its own.
    chain_instance: Mapped["ApprovalChainInstance"] = relationship(
        viewonly=True,
        uselist=False,
        primaryjoin=(
            "and_(foreign(ApprovalChainInstance.entity_id) == Quote.id, "
            "ApprovalChainInstance.entity_type == 'quote', ApprovalChainInstance.gate == 'discount')"
        ),
    )
    feasibility_result: Mapped["FeasibilityResult"] = relationship(back_populates="quote", uselist=False)
    purchase_order: Mapped["QuotePurchaseOrder | None"] = relationship(back_populates="quote", uselist=False, cascade="all, delete-orphan")


class QuotePurchaseOrder(Base):
    """Sales-to-Order: the customer's purchase order for an approved quote
    (INT-39) and Sales Ops' "match all" (INT-40). The quote itself stays
    "approved" - this only records what happened after approval."""

    __tablename__ = "quote_purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    po_number: Mapped[str] = mapped_column(String(100))
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    document_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    received_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    matched_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    match_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    quote: Mapped[Quote] = relationship(back_populates="purchase_order")


class QuotePriceOverride(Base):
    """A negotiated total that replaces a quote's computed price (INT-116).
    One row per negotiation round - the active one is the quote's current
    override, the rest are its history. While one is active the quote's
    total_price is override_price; computed_price is what the system priced
    it at, so removing the override puts that back. Its effective discount
    against list_price is what the approval matrix routes on."""

    __tablename__ = "quote_price_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"))
    list_price: Mapped[float] = mapped_column(Float)
    computed_price: Mapped[float] = mapped_column(Float)
    override_price: Mapped[float] = mapped_column(Float)
    effective_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # why an override stopped applying: replaced / removed / repriced
    ended_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class QuoteItem(Base):
    __tablename__ = "quote_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"))
    product_offering_id: Mapped[int] = mapped_column(ForeignKey("product_offerings.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float)

    quote: Mapped[Quote] = relationship(back_populates="items")


class FeasibilityResult(Base):
    __tablename__ = "feasibility_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    feasible: Mapped[bool] = mapped_column(default=True)
    details: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    quote: Mapped[Quote] = relationship(back_populates="feasibility_result")
