"""Configurable multi-tier approval chains (Phase 1 primitive #2).

Replaces the old flat single-tier DISCOUNT_MATRIX constant. Per the B2C/B2B
BRD research, an approval chain needs to vary *structurally* per tenant and
segment (e.g. Wholesale vs Corporate gets a different number of tiers, not
just a different threshold), so the chain shape itself - which tiers, in
what order, for which discount band and segment - is admin-configurable
reference data (ApprovalChainTemplate + ApprovalChainTier), not Python.

ApprovalChainInstance/ApprovalStepInstance are the resolved execution of a
template against one entity - originally quotes only (INT-06), now
polymorphic (entity_type + entity_id, same pattern as SlaClock) so the
same engine also gates Lead -> Opportunity qualification (a Sales
Director approval, per the B2B sales journey) without a second approval
engine. ApprovalChainTemplate.entity_type picks which kind of template a given
resolve_* call considers: "quote" templates still match by discount band
+ segment (approval_service.resolve_template); "lead" templates match
simply - one active chain per tenant, no discount band, since there's no
discount concept on a Lead (approval_service.resolve_lead_qualification_template).

Two kinds of approval rule (template.kind):
- "monetary" - money is at stake: a quote's discount band (B2B) or a
  consumer order's (B2C), optionally narrowed by deal value.
- "record" - a record may only enter a state once approved: the lead gate
  (lead -> qualified), the order-control gate (order -> activating), or any
  state of any record type an admin picks (trigger_state).
Older rows carry no kind; approval_service.kind_of derives it (quote ->
monetary, lead/order -> record), so every existing chain keeps working.

A tier may name the position whose holders decide it (position_id). While
nobody holds that position - or a tier names none, like every tier created
before positions existed - anyone allowed to run the approval decides it,
exactly as before.

ApprovalChainInstance.gate says which approval of a record this is:
"discount" for a monetary one, else the state it unlocks. One approval per
record per gate (it used to be one per record), so a record can carry a
discount approval and, say, a stage approval side by side.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # "quote" (discount-band matched) or "lead_qualification" (matched
    # simply - see approval_service.resolve_lead_qualification_template).
    entity_type: Mapped[str] = mapped_column(String(30), default="quote")
    segment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    min_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_discount_pct: Mapped[float] = mapped_column(Float, default=100.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    # "monetary" | "record" (None on rows from before kinds existed - see
    # approval_service.kind_of).
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # record rules: the state the record may only enter once approved
    # (None on older lead/order rows = their classic gate state).
    trigger_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # monetary rules: optional deal-value band on top of the discount band
    min_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

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
    # The position whose holders decide this tier (None = anyone allowed).
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)

    template: Mapped[ApprovalChainTemplate] = relationship(back_populates="tiers")


class ApprovalChainInstance(Base):
    """The resolved chain for one entity (a quote, a lead, ...) -
    instantiated on the first approval call against that entity and
    reused on subsequent calls until it reaches approved/rejected.
    entity_type + entity_id together are unique - one chain per entity.
    """

    __tablename__ = "approval_chain_instances"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "gate", name="uq_approval_chain_instances_entity_gate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    template_id: Mapped[int] = mapped_column(ForeignKey("approval_chain_templates.id"))
    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.pending)
    current_sequence: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    # "discount" (monetary) or the state this approval unlocks (record).
    gate: Mapped[str] = mapped_column(String(50), default="discount", server_default="discount")
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    requested_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

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
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    chain_instance: Mapped[ApprovalChainInstance] = relationship(back_populates="steps")
