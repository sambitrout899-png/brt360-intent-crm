from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class IntentExecutionLog(Base):
    """Immutable record of one intent execution - HLD 4.6 Learning & Feedback
    Loop and the backbone of 8.3 Observability. Never updated after completion;
    a human override is appended as its own JSON field, not an edit of the plan.
    """

    __tablename__ = "intent_execution_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    # Digital self-service vs. agent-assisted vs. partner/dealer - same intent,
    # different governance (e.g. digital may skip steps a human agent wouldn't,
    # or need a stricter auto-execute confidence threshold). See reasoning.py.
    channel: Mapped[str] = mapped_column(String(30), default="agent")
    # Who ran it - lets the command bar remember a user's recent intents
    # and the values they usually enter.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    intent_type: Mapped[str] = mapped_column(String(20))  # "INT-01", "INT-02", "INT-03"
    raw_text: Mapped[str] = mapped_column(Text)
    entities_json: Mapped[str] = mapped_column(Text)  # JSON-encoded extracted entities
    context_json: Mapped[str] = mapped_column(Text)  # JSON-encoded assembled context
    plan_json: Mapped[str] = mapped_column(Text)  # JSON-encoded ordered step list + per-step result
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/executed/failed/needs_review
    rationale: Mapped[str] = mapped_column(Text, default="")
    human_override_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    events: Mapped[list["DomainEvent"]] = relationship(back_populates="execution", cascade="all, delete-orphan")


class DomainEvent(Base):
    """Outbox-pattern event row - HLD 5.3 Event Model. Each event carries a
    correlation_id back to the IntentExecutionLog that caused it. Swapping this
    for a real Kafka producer later only touches data_intelligence/event_bus.py.
    """

    __tablename__ = "domain_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    correlation_id: Mapped[int] = mapped_column(ForeignKey("intent_execution_log.id"))
    event_type: Mapped[str] = mapped_column(String(100))  # e.g. "offering.published"
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    execution: Mapped[IntentExecutionLog] = relationship(back_populates="events")
