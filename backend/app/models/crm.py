from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class OpportunityStage(str, enum.Enum):
    draft = "draft"
    qualified = "qualified"
    quoted = "quoted"
    won = "won"
    lost = "lost"


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
            "ApprovalChainInstance.entity_type == 'lead')"
        ),
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    name: Mapped[str] = mapped_column(String(200))
    stage: Mapped[OpportunityStage] = mapped_column(Enum(OpportunityStage), default=OpportunityStage.draft)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="opportunity")
    quotes: Mapped[list["Quote"]] = relationship(back_populates="opportunity")
    feasibility_assessments: Mapped[list["FeasibilityAssessment"]] = relationship(viewonly=True)


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    status: Mapped[QuoteStatus] = mapped_column(Enum(QuoteStatus), default=QuoteStatus.draft)
    total_price: Mapped[float] = mapped_column(Float)
    requested_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    discount_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
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
            "ApprovalChainInstance.entity_type == 'quote')"
        ),
    )
    feasibility_result: Mapped["FeasibilityResult"] = relationship(back_populates="quote", uselist=False)


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
