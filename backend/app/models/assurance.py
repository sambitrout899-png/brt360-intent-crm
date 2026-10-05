from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class Technician(Base):
    """Reference data: field technician skill/location - HLD 5.2 Technician
    Skill/Parts Matrix, used by INT-12 to match work orders to resources.
    """

    __tablename__ = "technicians"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    skills: Mapped[str] = mapped_column(String(300))  # comma-separated skill tags
    region: Mapped[str] = mapped_column(String(100), default="ALL")
    status: Mapped[str] = mapped_column(String(20), default="available")  # available/busy


class TroubleTicket(Base):
    __tablename__ = "trouble_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    service_order_id: Mapped[int | None] = mapped_column(ForeignKey("service_orders.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(20))  # low/medium/high/critical
    product_line: Mapped[str] = mapped_column(String(100), default="")
    sla_class: Mapped[str] = mapped_column(String(50), default="standard")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open/diagnosing/resolved
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    incident_correlation: Mapped["IncidentCorrelation"] = relationship(back_populates="ticket", uselist=False)
    root_cause_analysis: Mapped["RootCauseAnalysis"] = relationship(back_populates="ticket", uselist=False)
    work_orders: Mapped[list["WorkOrder"]] = relationship(back_populates="ticket")


class IncidentCorrelation(Base):
    __tablename__ = "incident_correlations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("trouble_tickets.id"), unique=True)
    correlated_incident_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    ticket: Mapped[TroubleTicket] = relationship(back_populates="incident_correlation")


class RootCauseAnalysis(Base):
    __tablename__ = "root_cause_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("trouble_tickets.id"), unique=True)
    probable_cause: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    remediation_suggestion: Mapped[str] = mapped_column(Text, default="")
    field_dispatch_required: Mapped[bool] = mapped_column(Boolean, default=False)
    escalated_to_human: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    ticket: Mapped[TroubleTicket] = relationship(back_populates="root_cause_analysis")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("trouble_tickets.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    scheduled_window: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled/en_route/on_site/complete
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    ticket: Mapped[TroubleTicket | None] = relationship(back_populates="work_orders")
    assignment: Mapped["TechnicianAssignment"] = relationship(back_populates="work_order", uselist=False)
    completion_evidence: Mapped["CompletionEvidence"] = relationship(back_populates="work_order", uselist=False)


class TechnicianAssignment(Base):
    __tablename__ = "technician_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), unique=True)
    technician_id: Mapped[int] = mapped_column(ForeignKey("technicians.id"))
    assigned_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    work_order: Mapped[WorkOrder] = relationship(back_populates="assignment")
    technician: Mapped[Technician] = relationship()


class CompletionEvidence(Base):
    __tablename__ = "completion_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), unique=True)
    checklist: Mapped[str] = mapped_column(Text, default="")
    signature: Mapped[str] = mapped_column(String(200), default="")
    completed_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    work_order: Mapped[WorkOrder] = relationship(back_populates="completion_evidence")
