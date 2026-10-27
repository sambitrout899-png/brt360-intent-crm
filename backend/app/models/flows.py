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

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
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
