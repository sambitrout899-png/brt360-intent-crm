"""CPaaS-shaped notification stub (HLD 6.2 - Notification / CPaaS, outbound,
REST/Webhook). No real SMS/email provider is wired yet - this records the
notification as a domain event so the Verification step can confirm "the
customer/agent was notified" without needing real credentials during Sprint 1.
Swapping in a real provider later means implementing send() for real; nothing
calling this module changes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.data_intelligence import event_bus


def send(db: Session, correlation_id: int, channel: str, audience: str, message: str) -> None:
    event_bus.publish(
        db,
        correlation_id,
        "notification.sent",
        {"channel": channel, "audience": audience, "message": message},
    )
