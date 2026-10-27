from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StructuredIntent(BaseModel):
    intent_type: str
    entities: dict[str, Any] = {}


class IntentContext(BaseModel):
    """What the user is looking at when they invoke an intent - lets
    "approve this" / "move it to won" resolve without stating an id."""

    page: str | None = None
    entity_type: str | None = None
    entity_id: int | None = None
    label: str | None = None


class IntentSuggestion(BaseModel):
    intent_type: str
    label: str
    required_fields: list[str]


class IntentRequest(BaseModel):
    text: str
    # digital (self-service) | agent (assisted) | partner - see models/intent_log.py
    channel: str = "agent"
    # Phase 7: in-page forms (record edit, assign, lifecycle buttons) already
    # know the exact intent and entities - they send them here and skip NLU
    # classification. Everything after classification (context, reasoning
    # gates, saga, flows, verification, audit) is identical; `text` is still
    # recorded in the audit log as the human-readable statement.
    structured: StructuredIntent | None = None
    context: IntentContext | None = None


class PlanStep(BaseModel):
    step: int
    action: str
    status: str  # "ok" | "failed" | "skipped"
    detail: dict[str, Any] = {}


class IntentResponse(BaseModel):
    intent_execution_id: int
    intent_type: str | None
    entities: dict[str, Any]
    confidence: float
    rationale: str
    status: str
    plan: list[PlanStep]
    result: dict[str, Any] | None = None
    events: list[str] = []
    # Set on needs_review: what a caller must supply to resubmit (lets an
    # in-page form ask for just those fields instead of parsing rationale).
    missing_fields: list[str] = []
    # Set when the classifier was unsure: other intents the user may have
    # meant, so the UI can offer "did you mean" instead of a dead end.
    suggestions: list[IntentSuggestion] = []
    intent_label: str | None = None
    intent_icon: str | None = None
    # Business view of the outcome (copilot.outcome): one-line summary,
    # cards for the records created/changed, recommended next steps.
    summary: str = ""
    records: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []
    # Set by POST /api/intents/rehearse: nothing was kept, and `impact` is
    # what the run would have done (orchestration/rehearsal.py).
    rehearsal: bool = False
    impact: dict[str, Any] | None = None
    # Set by a rehearsal: automations and actions that pull against each
    # other on this run, for an admin to settle (orchestration/conflicts.py).
    conflicts: list[dict[str, Any]] = []
    # Set on status "failed" after execution: the failure, classified - code,
    # category, message, the flow/step/intent it came from (orchestration/
    # flow_errors.py) - and in flows_run the intent's "on error" flows.
    error: dict[str, Any] | None = None


class IntentAssistRequest(BaseModel):
    text: str
    context: IntentContext | None = None


class IntentAssistResponse(BaseModel):
    """Live "Expression Builder" feedback while the user is still
    typing - never executes anything, just runs the same classifier the
    real submission will use and reports what it currently understands.
    """

    intent_type: str | None
    intent_label: str | None
    confidence: float
    rationale: str
    entities: dict[str, Any]
    required_fields: list[str]
    missing_fields: list[str]
    suggestions: list[IntentSuggestion] = []
