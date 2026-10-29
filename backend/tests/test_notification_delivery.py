"""Delivering recorded notifications (app/crm_core/notification_delivery.py)."""

import datetime as dt

from conftest import DEFAULT_TEST_ADMIN_EMAIL
from test_access_guards import _user

from app.config import get_settings
from app.crm_core import notification_delivery
from app.integration import notification_adapter
from app.models.intent_log import DomainEvent, IntentExecutionLog
from app.models.notifications import NotificationDelivery
from app.models.tenant import Tenant
from app.timeutils import utcnow


def _record(client, channel, audience, message="Quote 12 is approved", age=dt.timedelta(0)):
    """A notification recorded the way a Notify step records it."""
    db = client.TestSessionLocal()
    try:
        tenant_id = db.query(Tenant.id).first()[0]
        log = IntentExecutionLog(tenant_id=tenant_id, channel="agent", intent_type="INT-01", raw_text="x", entities_json="{}",
                                 context_json="{}", plan_json="[]", confidence=1.0, status="executed")
        db.add(log)
        db.flush()
        notification_adapter.send(db, log.id, channel, audience, message)
        if age:
            db.query(DomainEvent).filter(DomainEvent.correlation_id == log.id).update({"created_at": utcnow() - age})
        db.commit()
    finally:
        db.close()


def _deliveries(client):
    db = client.TestSessionLocal()
    try:
        return [(d.channel, d.status, d.recipients, d.detail) for d in db.query(NotificationDelivery).order_by(NotificationDelivery.id)]
    finally:
        db.close()


def test_each_notification_is_delivered_once_by_its_channel(client, monkeypatch):
    sent = []
    monkeypatch.setattr(notification_delivery, "_send_email", lambda to, subject, body: sent.append((to, subject, body)))
    monkeypatch.setattr(get_settings(), "notify_email", "smtp")
    _user(client, "ops-person")
    _record(client, "email", f"{DEFAULT_TEST_ADMIN_EMAIL}, partner@outside.example")
    _record(client, "popup", "me")
    _record(client, "sms", "owner")
    _record(client, "email", "nobody-by-that-name")

    assert notification_delivery.run_due(client.TestSessionLocal) == 4
    assert notification_delivery.run_due(client.TestSessionLocal) == 0  # each once
    rows = _deliveries(client)
    assert [(c, s, n) for c, s, n, _ in rows] == [("email", "sent", 2), ("popup", "in_app", 0), ("sms", "no_provider", 0),
                                                ("email", "no_recipients", 0)]
    (to, subject, body), = sent
    assert to == sorted([DEFAULT_TEST_ADMIN_EMAIL, "partner@outside.example"]) and "Quote 12 is approved" in subject + body


def test_nothing_leaves_the_server_until_email_is_switched_on(client, monkeypatch):
    sent = []
    monkeypatch.setattr(notification_delivery, "_send_email", lambda *a: sent.append(a))
    _record(client, "email", DEFAULT_TEST_ADMIN_EMAIL)
    notification_delivery.run_due(client.TestSessionLocal)
    (_, status, n, detail), = _deliveries(client)
    assert sent == [] and status == "skipped" and n == 1 and "would go to" in detail


def test_old_notifications_are_not_sent_when_delivery_starts(client, monkeypatch):
    sent = []
    monkeypatch.setattr(notification_delivery, "_send_email", lambda *a: sent.append(a))
    monkeypatch.setattr(get_settings(), "notify_email", "smtp")
    _record(client, "email", DEFAULT_TEST_ADMIN_EMAIL, age=dt.timedelta(days=3))
    notification_delivery.run_due(client.TestSessionLocal)
    assert sent == [] and _deliveries(client)[0][1] == "skipped"


def test_a_failed_send_is_recorded_and_the_rest_go_on(client, monkeypatch):
    def broken(*a):
        raise ConnectionRefusedError("smtp down")

    monkeypatch.setattr(notification_delivery, "_send_email", broken)
    monkeypatch.setattr(get_settings(), "notify_email", "smtp")
    _record(client, "email", DEFAULT_TEST_ADMIN_EMAIL)
    _record(client, "popup", "me")
    assert notification_delivery.run_due(client.TestSessionLocal) == 2
    assert [s for _, s, _, _ in _deliveries(client)] == ["failed", "in_app"]
    assert "smtp down" in _deliveries(client)[0][3]
    log = client.get("/api/admin/notification-deliveries", params={"status": "failed"}).json()
    assert [r["status"] for r in log] == ["failed"]
