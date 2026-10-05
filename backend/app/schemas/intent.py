from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class IntentRequest(BaseModel):
    text: str
    # digital (self-service) | agent (assisted) | partner - see models/intent_log.py
    channel: str = "agent"


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
