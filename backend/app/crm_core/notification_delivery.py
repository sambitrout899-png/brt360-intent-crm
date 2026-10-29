"""Delivering notifications: the notification service's worker.

Notifications are recorded, not sent, where they happen
(integration/notification_adapter.send - a Flow Studio Notify step, a
bundle launch ...): a "notification.sent" event in the outbox, in the same
transaction. This worker (notification-worker, every minute) reads the
events not delivered yet and delivers each once, recording the outcome in
notification_deliveries:

- "popup": shown on screen by the app itself (notice_service) - recorded as in_app;
- "email": to the audience - "me" (whoever's action started it), "owner"
  (the record's owner), users by email or name, a position's members, or an
  outside email address - through SMTP when NOTIFY_EMAIL=smtp; otherwise
  recorded as skipped, so nothing leaves the server until someone turns it on;
- anything else (sms, whatsapp, portal ...): no_provider, until one is chosen.

An event older than MAX_AGE when the worker first sees it is skipped, not sent:
switching email on doesn't flood people with old notifications.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import smtplib
from email.message import EmailMessage
from typing import Any

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app import rls
from app.config import get_settings
from app.models.auth import User
from app.models.intent_log import DomainEvent, IntentExecutionLog
from app.models.notifications import NotificationDelivery
from app.models.tenant import Tenant
from app.timeutils import utcnow

log = logging.getLogger(__name__)

EVENT = "notification.sent"
MAX_AGE = dt.timedelta(days=1)
_EMAIL_CHANNELS = {"email", "mail", ""}
_EMAIL = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}")


def _send_email(to: list[str], subject: str, body: str) -> None:
    s = get_settings()
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = s.smtp_from, ", ".join(to), subject
    message.set_content(body)
    if s.smtp_ssl:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(message)


def recipients(db: Session, tenant_id: int, audience: str, actor_id: int | None, entity_type: str | None,
               entity_id: int | None) -> list[str]:
    """Email addresses for the audience (the same words a Notify step's popup uses)."""
    from app.crm_core import access_service, lifecycle_service, notice_service

    record = lifecycle_service.get_record(db, tenant_id, entity_type, entity_id) if entity_type and entity_id else None
    marker = access_service.current_actor.set({"user_id": actor_id} if actor_id else None)
    try:
        user_ids = notice_service.popup_recipients(db, tenant_id, audience, entity_type, record)
    finally:
        access_service.current_actor.reset(marker)
    emails = {u.email for u in db.query(User).filter(User.id.in_(user_ids), User.is_active.is_(True))} if user_ids else set()
    # an outside address named in the audience (a customer, a partner) - not a user of the tenant
    for part in (p.strip() for p in (audience or "").replace(";", ",").split(",")):
        if _EMAIL.fullmatch(part) and db.query(User.id).filter(User.tenant_id == tenant_id, User.email.ilike(part)).first() is None:
            emails.add(part)
    return sorted(emails)


def deliver(db: Session, event: DomainEvent, tenant_id: int | None, actor_id: int | None) -> NotificationDelivery:
    try:
        payload: dict[str, Any] = json.loads(event.payload_json or "{}")
    except ValueError:
        payload = {}
    channel = str(payload.get("channel") or "").lower()
    audience = str(payload.get("audience") or "")
    row = NotificationDelivery(tenant_id=tenant_id, event_id=event.id, at=utcnow(), channel=channel or "email",
                               audience=audience[:300], recipients=0, status="skipped", detail="")
    if event.created_at and utcnow() - event.created_at > MAX_AGE:
        row.detail = "older than a day when delivery started - not sent"
    elif channel == "popup":
        row.status, row.detail = "in_app", "shown on screen by the app"
    elif channel not in _EMAIL_CHANNELS:
        row.status, row.detail = "no_provider", f"nothing delivers '{channel}' messages yet"
    elif tenant_id is None:
        row.status, row.detail = "no_recipients", "no organization to resolve the audience in"
    else:
        to = recipients(db, tenant_id, audience, actor_id, payload.get("entity_type"), payload.get("entity_id"))
        row.recipients = len(to)
        if not to:
            row.status, row.detail = "no_recipients", f"nobody matches '{audience}'"
        elif get_settings().notify_email != "smtp":
            row.detail = f"email delivery is off (NOTIFY_EMAIL) - would go to {', '.join(to)}"
        else:
            tenant = db.get(Tenant, tenant_id)
            company = (tenant.display_name or tenant.name) if tenant else "BRT 360"
            message = str(payload.get("message") or "")
            subject = str(payload.get("subject") or f"{company}: {message[:60]}{'...' if len(message) > 60 else ''}")
            try:
                _send_email(to, subject, f"{message}\n\n- {company}, via BRT 360")
                row.status, row.detail = "sent", ", ".join(to)
            except Exception as exc:  # noqa: BLE001 - recorded on the delivery; the next event goes on
                row.status, row.detail = "failed", f"{type(exc).__name__}: {exc}"[:500]
    db.add(row)
    return row


def run_due(session_factory, limit: int = 200) -> int:
    """The worker's job: each undelivered notification event, once. Each is
    committed on its own, right after it was sent."""
    with session_factory() as db:
        due = db.query(DomainEvent.id, IntentExecutionLog.tenant_id, IntentExecutionLog.user_id).outerjoin(
            IntentExecutionLog, IntentExecutionLog.id == DomainEvent.correlation_id,
        ).filter(
            DomainEvent.event_type == EVENT,
            ~exists().where(NotificationDelivery.event_id == DomainEvent.id),
        ).order_by(DomainEvent.id).limit(limit).all()
    done = 0
    for event_id, tenant_id, actor_id in due:
        with session_factory() as db:
            try:
                if tenant_id is not None:
                    rls.scope(db, tenant_id)
                deliver(db, db.get(DomainEvent, event_id), tenant_id, actor_id)
                db.commit()
                done += 1
            except Exception:  # noqa: BLE001 - one event's trouble never stops the rest
                db.rollback()
                log.exception("Notification event %s could not be delivered", event_id)
    return done


def recent(db: Session, tenant_id: int, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query = db.query(NotificationDelivery).filter(NotificationDelivery.tenant_id == tenant_id)
    if status:
        query = query.filter(NotificationDelivery.status == status)
    return [{"id": r.id, "at": r.at, "channel": r.channel, "audience": r.audience, "recipients": r.recipients,
             "status": r.status, "detail": r.detail} for r in query.order_by(NotificationDelivery.id.desc()).limit(min(limit, 500))]
