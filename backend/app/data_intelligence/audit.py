"""IntentExecutionLog writer - HLD 4.6 Learning & Feedback Loop."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.intent_log import IntentExecutionLog
from app.timeutils import utcnow


def start_execution(db: Session, raw_text: str) -> IntentExecutionLog:
    log = IntentExecutionLog(
        intent_type="",
        raw_text=raw_text,
        entities_json="{}",
        context_json="{}",
        plan_json="[]",
        confidence=0.0,
        status="pending",
    )
    db.add(log)
    db.flush()
    return log


def record_classification(
    log: IntentExecutionLog, intent_type: str, entities: dict[str, Any], confidence: float, rationale: str
) -> None:
    log.intent_type = intent_type
    log.entities_json = json.dumps(entities, default=str)
    log.confidence = confidence
    log.rationale = rationale


def record_context(log: IntentExecutionLog, context: dict[str, Any]) -> None:
    log.context_json = json.dumps(context, default=str)


def record_plan(log: IntentExecutionLog, plan_steps: list[dict[str, Any]]) -> None:
    log.plan_json = json.dumps(plan_steps, default=str)


def complete(log: IntentExecutionLog, status: str) -> None:
    log.status = status
    log.completed_at = utcnow()
