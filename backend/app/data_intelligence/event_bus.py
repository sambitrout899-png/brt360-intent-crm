"""Outbox-pattern event bus (HLD 5.3 Event Model).

MVP implementation: publish() writes a DomainEvent row in the same DB
transaction as the orchestration step that caused it, so an event is never
lost even if a downstream consumer is temporarily unavailable. Swapping this
module's publish() for a real Kafka producer is the only change needed to
move to the HLD 9.1 production topology - no caller elsewhere changes.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.intent_log import DomainEvent


def publish(db: Session, correlation_id: int, event_type: str, payload: dict[str, Any]) -> DomainEvent:
    event = DomainEvent(
        correlation_id=correlation_id,
        event_type=event_type,
        payload_json=json.dumps(payload, default=str),
    )
    db.add(event)
    db.flush()
    return event
