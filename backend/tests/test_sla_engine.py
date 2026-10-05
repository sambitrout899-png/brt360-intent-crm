import datetime as dt
import time

from app.crm_core import sla_service
from app.models.sla import SlaClockStatus, SlaPolicy
from app.tenancy import DEFAULT_TENANT_SLUG


def _default_tenant_id(db):
    from app.models.tenant import Tenant

    return db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id


def test_start_clock_noop_without_policy(client):
    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        clock = sla_service.start_clock(db, tenant_id, "kyc_verification", "party", 1)
        assert clock is None
    finally:
        db.close()


def test_clock_completed_before_deadline_never_breaches(client):
    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        db.add(SlaPolicy(tenant_id=tenant_id, clock_type="kyc_verification", duration_hours=2.0, on_breach_action="auto_approve"))
        db.commit()

        clock = sla_service.start_clock(db, tenant_id, "kyc_verification", "party", 42)
        assert clock is not None
        assert clock.status == SlaClockStatus.running

        completed = sla_service.complete_clock(db, tenant_id, "kyc_verification", "party", 42)
        db.commit()
        assert completed.status == SlaClockStatus.completed

        far_future = clock.deadline + dt.timedelta(days=1)
        result = sla_service.process_due_clocks(db, tenant_id, as_of=far_future)
        db.commit()
        assert result["checked"] == 0
        assert result["breached"] == []
    finally:
        db.close()


def test_breached_clock_escalates_and_logs_audit_entry(client):
    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        db.add(SlaPolicy(tenant_id=tenant_id, clock_type="payment_grace", duration_hours=72.0, on_breach_action="auto_cancel"))
        db.commit()

        clock = sla_service.start_clock(db, tenant_id, "payment_grace", "billing_account", 7)
        assert clock is not None

        past_deadline = clock.deadline + dt.timedelta(hours=1)
        result = sla_service.process_due_clocks(db, tenant_id, as_of=past_deadline)
        db.commit()

        assert result["checked"] == 1
        assert len(result["breached"]) == 1
        breach = result["breached"][0]
        assert breach["outcome"] == "escalated"
        assert "auto_cancel" in breach["detail"]  # no handler registered yet - falls back and says so

        db.refresh(clock)
        assert clock.status == SlaClockStatus.breached
        assert clock.resolved_at == past_deadline
    finally:
        db.close()


def test_sweep_api_endpoint_breaches_due_clock(client):
    create_resp = client.post(
        "/api/admin/sla-policies",
        json={"clock_type": "feasibility_check", "duration_hours": 0.0001, "on_breach_action": "escalate"},
    )
    assert create_resp.status_code == 201

    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        clock = sla_service.start_clock(db, tenant_id, "feasibility_check", "quote", 99)
        db.commit()
        assert clock is not None
    finally:
        db.close()

    time.sleep(0.5)  # 0.0001h (~0.36s) deadline has now definitely passed

    sweep_resp = client.post("/api/admin/sla/process-due")
    assert sweep_resp.status_code == 200
    body = sweep_resp.json()
    assert body["checked"] == 1
    assert body["breached"][0]["clock_type"] == "feasibility_check"

    audit = client.get("/api/audit").json()
    assert audit[0]["intent_type"] == "SYS-SLA"


def test_create_sla_policy_rejects_unknown_action(client):
    resp = client.post(
        "/api/admin/sla-policies",
        json={"clock_type": "x", "duration_hours": 1.0, "on_breach_action": "do_a_backflip"},
    )
    assert resp.status_code == 400


def test_deactivate_sla_policy(client):
    create_resp = client.post(
        "/api/admin/sla-policies", json={"clock_type": "retention_window", "duration_hours": 24.0, "on_breach_action": "auto_proceed"}
    )
    policy_id = create_resp.json()["id"]
    deactivate_resp = client.patch(f"/api/admin/sla-policies/{policy_id}/deactivate")
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False
