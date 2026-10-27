from datetime import timedelta
from app.crm_core import inventory_sync_service as service
from app.models.feasibility import FeasibilityCheckType, InventorySyncConfig
from app.models.tenant import Tenant
from app.timeutils import utcnow

def test_sample_idempotent_and_preserves_manual(client):
    client.post("/api/admin/feasibility-check-types", json={"name": "Manual", "resource_type": "fiber"})
    first = client.post("/api/admin/inventory-sync/run?sample=true")
    assert first.status_code == 200
    assert first.json()["created"] == 4
    assert client.post("/api/admin/inventory-sync/run?sample=true").json()["created"] == 0
    assert len(client.get("/api/admin/feasibility-check-types").json()) == 5
    assert client.get("/api/admin/inventory-sync").json()["last_success_at"] is None

def test_snapshot_validation_and_tenant_isolation(client, monkeypatch):
    with client.TestSessionLocal() as db:
        tenants = db.query(Tenant).all()
        tenant_id = tenants[0].id
        other = Tenant(name="Other", slug="inventory-other")
        db.add(other); db.flush()
        service.sync(db, other.id, sample=True)
        monkeypatch.setattr(service, "fetch", lambda _: [{"id":"one", "name":"One", "resource_type":"port"}])
        service.sync(db, tenant_id); db.commit()
        monkeypatch.setattr(service, "fetch", lambda _: [{"id":"bad"}])
        try:
            service.sync(db, tenant_id)
            assert False
        except ValueError:
            db.rollback()
        assert db.query(FeasibilityCheckType).filter_by(tenant_id=tenant_id, is_active=True).count() == 1
        monkeypatch.setattr(service, "fetch", lambda _: [])
        service.sync(db, tenant_id); db.commit()
        assert db.query(FeasibilityCheckType).filter_by(tenant_id=tenant_id, is_active=True).count() == 0
        assert db.query(FeasibilityCheckType).filter_by(tenant_id=other.id, is_active=True).count() == 4

def test_scheduler_and_failure_preserves_checks(client, monkeypatch):
    client.get("/api/admin/inventory-sync")
    with client.TestSessionLocal() as db:
        config = db.query(InventorySyncConfig).one()
        config.enabled = True
        db.commit()
    calls = []
    monkeypatch.setattr(service, "fetch", lambda tid: calls.append(tid) or service.SAMPLES)
    service.run_due(client.TestSessionLocal)
    service.run_due(client.TestSessionLocal)
    assert len(calls) == 1
    with client.TestSessionLocal() as db:
        config = db.query(InventorySyncConfig).one()
        config.last_attempt_at = utcnow() - timedelta(hours=2)
        db.commit()
    def fail(_):
        raise RuntimeError("network failure")
    monkeypatch.setattr(service, "fetch", fail)
    service.run_due(client.TestSessionLocal)
    with client.TestSessionLocal() as db:
        assert db.query(InventorySyncConfig).one().last_error
        assert db.query(FeasibilityCheckType).filter_by(is_active=True).count() == 4

def test_integration_intent(client, monkeypatch):
    monkeypatch.setattr(service, "fetch", lambda _: service.SAMPLES)
    result = client.post("/api/intents", json={"text":"Sync inventory check types"})
    assert result.status_code == 200, result.text
    assert result.json()["intent_type"] == "INT-34"
    assert result.json()["status"] == "executed", result.text
    assert result.json()["result"]["created"] == 4


def test_non_admin_cannot_sync(client, monkeypatch):
    from app.models.auth import User, UserRole
    with client.TestSessionLocal() as db:
        user = db.query(User).filter(User.tenant_id.is_not(None)).first()
        user.role = UserRole.agent
        db.commit()
    calls = []
    monkeypatch.setattr(service, "fetch", lambda tid: calls.append(tid) or service.SAMPLES)
    assert client.post("/api/admin/inventory-sync/run?sample=true").status_code == 403
    result = client.post("/api/intents", json={"text":"Sync inventory check types"})
    assert result.json()["status"] != "executed"
    assert calls == []
