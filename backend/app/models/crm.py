from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class OpportunityStage(str, enum.Enum):
    draft = "draft"
    qualified = "qualified"
    quoted = "quoted"
    won = "won"
    lost = "lost"


class QuoteStatus(str, enum.Enum):
    draft = "draft"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region: Mapped[str] = mapped_column(String(100), unique=True)
    owner_name: Mapped[str] = mapped_column(String(200))


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    source: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(100), default="ALL")
    owner_name: Mapped[str] = mapped_column(String(200))
    propensity_score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    party: Mapped[Party] = relationship()
    opportunity: Mapped["Opportunity"] = relationship(back_populates="lead", uselist=False)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    name: Mapped[str] = mapped_column(String(200))
    stage: Mapped[OpportunityStage] = mapped_column(Enum(OpportunityStage), default=OpportunityStage.draft)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="opportunity")
    quotes: Mapped[list["Quote"]] = relationship(back_populates="opportunity")


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    status: Mapped[QuoteStatus] = mapped_column(Enum(QuoteStatus), default=QuoteStatus.draft)
    total_price: Mapped[float] = mapped_column(Float)
    requested_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    discount_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    opportunity: Mapped[Opportunity] = relationship(back_populates="quotes")
    items: Mapped[list["QuoteItem"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    approval_request: Mapped["ApprovalRequest"] = relationship(back_populates="quote", uselist=False)
    feasibility_result: Mapped["FeasibilityResult"] = relationship(back_populates="quote", uselist=False)


class QuoteItem(Base):
    __tablename__ = "quote_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"))
    product_offering_id: Mapped[int] = mapped_column(ForeignKey("product_offerings.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float)

    quote: Mapped[Quote] = relationship(back_populates="items")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    approver_tier: Mapped[str] = mapped_column(String(100))
    deal_size: Mapped[float] = mapped_column(Float)
    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.pending)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    quote: Mapped[Quote] = relationship(back_populates="approval_request")
    decision: Mapped["ApprovalDecision"] = relationship(back_populates="approval_request", uselist=False)


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approval_request_id: Mapped[int] = mapped_column(ForeignKey("approval_requests.id"), unique=True)
    decision: Mapped[str] = mapped_column(String(20))  # approve/reject/conditional
    reasoning: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    approval_request: Mapped[ApprovalRequest] = relationship(back_populates="decision")


class FeasibilityResult(Base):
    __tablename__ = "feasibility_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    feasible: Mapped[bool] = mapped_column(default=True)
    details: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    quote: Mapped[Quote] = relationship(back_populates="feasibility_result")
