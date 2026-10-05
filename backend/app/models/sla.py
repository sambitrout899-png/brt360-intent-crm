"""Tenant-configurable SLA clocks + escalation engine (Phase 1 primitive #3).

Per the B2C/B2B BRD research, several processes carry an explicit SLA
timer with a configured action if nobody acts in time - KYC verification
auto-approving after 2h, a cancellation's retention window auto-proceeding
after 24-48h, an unpaid invoice auto-cancelling after 72h, a feasibility
check escalating to "Jeopardy" after 5 days. SlaPolicy is the tenant's
admin-configured rule for one clock_type (how long, what happens on
breach); SlaClock is one running instance of that rule, started against a
specific entity (any tenant-scoped row - a Quote, an Order, a
TroubleTicket, ...) and resolved by either completing in time or being
swept up as breached by sla_service.process_due_clocks().

Wiring actual clocks into the 12 intents (e.g. INT-09 starting a
retention-window clock) is Phase 2 - this module builds the reusable
engine, proven today by the one domain-agnostic breach action
("escalate") sla_service implements out of the box.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class SlaClockStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    breached = "breached"
    cancelled = "cancelled"


class SlaPolicy(Base):
    """Admin config: for this tenant, a clock of `clock_type` runs for
    `duration_hours` before `on_breach_action` fires.
    """

    __tablename__ = "sla_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    clock_type: Mapped[str] = mapped_column(String(100))
    duration_hours: Mapped[float] = mapped_column(Float)
    on_breach_action: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class SlaClock(Base):
    """One running/resolved instance of a policy, against one entity."""

    __tablename__ = "sla_clocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    clock_type: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))  # e.g. "quote", "order", "trouble_ticket"
    entity_id: Mapped[int] = mapped_column(Integer)
    on_breach_action: Mapped[str] = mapped_column(String(50))  # copied from policy at start time
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    deadline: Mapped[dt.datetime] = mapped_column()
    status: Mapped[SlaClockStatus] = mapped_column(Enum(SlaClockStatus), default=SlaClockStatus.running)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
