"""Recording a notification (HLD 6.2 - Notification / CPaaS, outbound).

send() records the notification as a "notification.sent" event in the outbox,
in the caller's transaction - so the Verification step can confirm "the
customer/agent was notified", and a rolled-back action notifies nobody. The
notification service's worker delivers it afterwards
(crm_core/notification_delivery.py): in-app, by email when switched on, and
other channels once a provider exists. The record it is about (entity_type,
entity_id) lets the audience "owner" find that record's owner.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.data_intelligence import event_bus


def send(db: Session, correlation_id: int, channel: str, audience: str, message: str,
         entity_type: str | None = None, entity_id: int | None = None) -> None:
    payload = {"channel": channel, "audience": audience, "message": message}
    if entity_type and entity_id:
        payload.update(entity_type=entity_type, entity_id=entity_id)
    event_bus.publish(db, correlation_id, "notification.sent", payload)
