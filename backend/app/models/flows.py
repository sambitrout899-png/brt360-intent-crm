"""Phase 7 - AI Flow Studio: tenant-defined business process flows.

A flow is a small directed graph (graph_json: {"nodes": [...], "edges":
[...]}) built in the Flow Studio's drag-and-drop canvas, optionally
drafted by the AI assist. It is executed by orchestration/flow_engine.py
as ordinary SagaSteps, so a flow's side effects commit or roll back
atomically with the intent that triggered it.

Trigger types:
- "intent": tagged behind a built-in intent (trigger_intent_type, e.g.
  "INT-05"), running "before" (validation - can reject the intent),
  "after" (follow-up actions on the records the intent produced) or
  "error" (when the intent fails - after everything it did was undone; the
  failure is {{error.*}}: notify someone, open a ticket, log it).

Errors (see orchestration/flow_errors.py): a step's "error" output or a
Catch errors step handles a failure inside the flow. on_error says what an
error nothing caught does: "fail" fails the flow and the intent with it (all
undone); "continue" undoes only this flow and lets the intent complete.
- "custom_intent": the flow IS an intent - classified from free text by
  its trigger_phrases (and described to the LLM classifier), addressed as
  FLOW-<id>.
- "state_enter": runs whenever a record of entity_type enters
  trigger_state, whichever intent caused it.
- "validation": a complex validation rule of the Page Designer
  (models/pages.py ValidationRule kind "flow") - runs on the values a page
  is about to save; its Reject node blocks the save with its message.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

FLOW_TRIGGER_TYPES = ("intent", "custom_intent", "state_enter", "validation")
FLOW_TIMINGS = ("before", "after", "error")
FLOW_ERROR_POLICIES = ("fail", "continue")


class FlowDefinition(Base):
    __tablename__ = "flow_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(20), default="intent")
    trigger_intent_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    timing: Mapped[str] = mapped_column(String(10), default="after")
    trigger_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # custom_intent only: comma-separated phrases the fallback classifier
    # matches on, and the entity fields auto-execution requires.
    trigger_phrases: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_fields: Mapped[str | None] = mapped_column(String(300), nullable=True)
    graph_json: Mapped[str] = mapped_column(Text, default='{"nodes": [], "edges": []}')
    # what an error the flow doesn't catch does - see FLOW_ERROR_POLICIES
    on_error: Mapped[str] = mapped_column(String(10), default="fail", server_default="fail")
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


# where a paused run is: waiting; and how it ended once it continued
FLOW_RUN_STATUSES = ("waiting", "completed", "handled", "continued", "rejected", "failed", "cancelled")


class FlowRun(Base):
    """A flow run paused at a Wait or Wait for approval step
    (orchestration/flow_runs.py). Everything up to the step committed with
    the intent that started the flow; the rest runs when the time comes (the
    worker) or the approver decides, in its own transaction. The run keeps
    the graph it started with, so editing the flow meanwhile doesn't move a
    waiting run onto steps it never had."""

    __tablename__ = "flow_runs"
    __table_args__ = (Index("ix_flow_runs_status_resume_at", "status", "resume_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    flow_id: Mapped[int] = mapped_column(ForeignKey("flow_definitions.id"))
    execution_id: Mapped[int] = mapped_column(Integer)  # the intent run that started the flow
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="waiting")
    node_id: Mapped[str] = mapped_column(String(60))  # the step it waits at
    wait_kind: Mapped[str] = mapped_column(String(20))  # timer | approval
    # timer: when it continues; approval: when it expires (None = never)
    resume_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    approver_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approver_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject: Mapped[str] = mapped_column(String(300), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)  # approved | rejected | expired
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    graph_json: Mapped[str] = mapped_column(Text)
    state_json: Mapped[str] = mapped_column(Text)  # the run's context, trace and errors so far
    outcome_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # how the rest went
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)
