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

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class FeasibilityOutcome(str, enum.Enum):
    pending = "pending"
    passed = "passed"
    failed = "failed"


class FeasibilityCheckType(Base):
    __tablename__ = "feasibility_check_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))  # e.g. "Fiber Route Availability"
    resource_type: Mapped[str] = mapped_column(String(50))  # "fiber" | "port" | "switch" | "inventory" | ...
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FeasibilityAssessment(Base):
    """One run of every active check type against one Opportunity."""

    __tablename__ = "feasibility_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
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
