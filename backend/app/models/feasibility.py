"""Configurable, multi-resource feasibility assessment (B2B: run at the
Opportunity level before quoting; see actions_feasibility.py's INT-18).

Per the BRD research, "feasibility" isn't one boolean - it's a set of
independent checks (fiber route availability, port capacity, switch
capacity, inventory on hand, ...) that varies per tenant/deployment, so
which checks run is admin-configurable reference data
(FeasibilityCheckType), not hardcoded. A check that can't be decided
synchronously starts a "feasibility_assessment" SLA clock - the BRD's
"5-day SLA -> Jeopardy escalation" pattern, reusing the same SLA engine
Phase 1 built (sla_service.register_breach_handler), not a second one.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class FeasibilityOutcome(str, enum.Enum):
    pending = "pending"
    passed = "passed"
    failed = "failed"


class FeasibilityCheckType(Base):
    __tablename__ = "feasibility_check_types"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id", name="uq_feasibility_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    name: Mapped[str] = mapped_column(String(150))  # e.g. "Fiber Route Availability"
    resource_type: Mapped[str] = mapped_column(String(50))  # "fiber" | "port" | "switch" | "inventory" | ...
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FeasibilityAssessment(Base):
    """One run of every active check type against one Opportunity (B2B,
    before quoting) or one Order (B2C, run by the Order Module - no
    opportunity exists on that path)."""

    __tablename__ = "feasibility_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    outcome: Mapped[FeasibilityOutcome] = mapped_column(Enum(FeasibilityOutcome), default=FeasibilityOutcome.pending)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    results: Mapped[list["FeasibilityCheckResult"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class FeasibilityCheckResult(Base):
    __tablename__ = "feasibility_check_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("feasibility_assessments.id"))
    check_type_name: Mapped[str] = mapped_column(String(150))
    resource_type: Mapped[str] = mapped_column(String(50))
    outcome: Mapped[FeasibilityOutcome] = mapped_column(Enum(FeasibilityOutcome), default=FeasibilityOutcome.pending)
    detail: Mapped[str] = mapped_column(Text, default="")

    assessment: Mapped[FeasibilityAssessment] = relationship(back_populates="results")


class InventorySyncConfig(Base):
    __tablename__ = "inventory_sync_configs"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
