"""Configurable multi-tier approval chains (Phase 1 primitive #2).

Replaces the old flat single-tier DISCOUNT_MATRIX constant. Per the B2C/B2B
BRD research, an approval chain needs to vary *structurally* per tenant and
segment (e.g. Wholesale vs Corporate gets a different number of tiers, not
just a different threshold), so the chain shape itself - which tiers, in
what order, for which discount band and segment - is admin-configurable
reference data (ApprovalChainTemplate + ApprovalChainTier), not Python.

ApprovalChainInstance/ApprovalStepInstance are the resolved, per-quote
execution of a template: one instance per quote, one step row per tier,
advanced one at a time by successive INT-06 "Approve this quote" calls.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.crm import ApprovalStatus
from app.timeutils import utcnow


class ApprovalChainTemplate(Base):
    """Admin-configured chain for one tenant: applies to quotes whose
    discount falls in [min_discount_pct, max_discount_pct] and whose
    offering segment matches `segment` (None = applies to any segment,
    but a segment-specific template always wins when both cover the same
    discount - see approval_service.resolve_template).
    """

    __tablename__ = "approval_chain_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    segment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    min_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_discount_pct: Mapped[float] = mapped_column(Float, default=100.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    tiers: Mapped[list["ApprovalChainTier"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="ApprovalChainTier.sequence"
    )


class ApprovalChainTier(Base):
    """One ordered tier within a template, e.g. (1, "Regional Sales Head")."""

    __tablename__ = "approval_chain_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("approval_chain_templates.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    approver_tier: Mapped[str] = mapped_column(String(100))

    template: Mapped[ApprovalChainTemplate] = relationship(back_populates="tiers")


class ApprovalChainInstance(Base):
    """The resolved chain for one quote - instantiated on the first
    "Approve this quote" call and reused on subsequent calls until it
    reaches approved/rejected.
    """

    __tablename__ = "approval_chain_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("approval_chain_templates.id"))
    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.pending)
    current_sequence: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    template: Mapped[ApprovalChainTemplate] = relationship()
    steps: Mapped[list["ApprovalStepInstance"]] = relationship(
        back_populates="chain_instance", cascade="all, delete-orphan", order_by="ApprovalStepInstance.sequence"
    )


class ApprovalStepInstance(Base):
    """One tier's decision within a chain instance."""

    __tablename__ = "approval_step_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_instance_id: Mapped[int] = mapped_column(ForeignKey("approval_chain_instances.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    approver_tier: Mapped[str] = mapped_column(String(100))
    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.pending)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    chain_instance: Mapped[ApprovalChainInstance] = relationship(back_populates="steps")
