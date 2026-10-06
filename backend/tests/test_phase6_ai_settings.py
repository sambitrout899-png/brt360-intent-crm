"""Phase 6: tenant-configurable AI settings (admin CRUD + safe fallback
resolution) and the Expression Builder's live /api/intents/assist
endpoint. The test environment has no OPENAI_API_KEY anywhere (global
or per-tenant), so classification here always exercises the
deterministic rule-based parser - exactly the "no key configured" path
every real deployment without an AI key also takes. Tests only ever
set a *fake* key when checking resolve_api_key_and_model() in
isolation (never through classify(), which would attempt a real
network call against a fake key).
"""

from app.crm_core import ai_settings_service
from app.models.ai_settings import AiSettings
from app.tenancy import DEFAULT_TENANT_SLUG


def _default_tenant_id(db):
    from app.models.tenant import Tenant

    return db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id


def test_ai_settings_default_uses_global_fallback(client):
    resp = client.get("/api/admin/ai-settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["using_global_fallback"] is True
    assert body["masked_api_key"] is None
    assert body["model"] == "gpt-4o-mini"


def test_ai_settings_update_and_clear(client):
    update_resp = client.put("/api/admin/ai-settings", json={"api_key": "sk-test-abcdefgh1234", "model": "gpt-4o"})
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["configured"] is True
    assert body["using_global_fallback"] is False
    assert body["model"] == "gpt-4o"
    # Never echoes the real key back.
    assert body["masked_api_key"] is not None
    assert "sk-test-abcdefgh1234" not in body["masked_api_key"]
    assert body["masked_api_key"].startswith("sk-t")
    assert body["masked_api_key"].endswith("1234")

    get_resp = client.get("/api/admin/ai-settings")
    assert get_resp.json()["configured"] is True

    # Updating just the model (blank api_key) must not clear the stored key.
    model_only = client.put("/api/admin/ai-settings", json={"api_key": None, "model": "gpt-4o-mini"})
    assert model_only.json()["configured"] is True
    assert model_only.json()["model"] == "gpt-4o-mini"

    clear_resp = client.delete("/api/admin/ai-settings")
    assert clear_resp.status_code == 204
    assert client.get("/api/admin/ai-settings").json()["configured"] is False


def test_resolve_api_key_and_model_prefers_tenant_key(client):
    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        db.add(AiSettings(tenant_id=tenant_id, api_key="sk-fake-tenant-key", model="gpt-4o"))
        db.commit()

        api_key, model = ai_settings_service.resolve_api_key_and_model(db, tenant_id)
        assert api_key == "sk-fake-tenant-key"
        assert model == "gpt-4o"
    finally:
        db.close()


def test_resolve_api_key_and_model_falls_back_to_global_when_unconfigured(client):
    from app.config import get_settings

    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        api_key, model = ai_settings_service.resolve_api_key_and_model(db, tenant_id)
        settings = get_settings()
        assert api_key == settings.openai_api_key
        assert model == settings.openai_model
    finally:
        db.close()


def test_assist_reports_required_and_missing_fields(client):
    resp = client.post(
        "/api/intents/assist", json={"text": "Launch a new bundle. name: Test Bundle"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent_type"] == "INT-01"
    assert body["intent_label"] == "Launch a new bundle"
    assert "composition" in body["required_fields"]
    assert "list_price" in body["required_fields"]
    assert "name" in body["required_fields"]
    assert "composition" in body["missing_fields"]
    # The deterministic parser defaults an absent "price:" field to 0.0
    # rather than None - exactly reasoning.py's own real submit-time
    # behavior, which this endpoint deliberately mirrors - so list_price
    # does not show as "missing" here even though nothing was typed for it.
    assert "name" not in body["missing_fields"]


def test_assist_with_no_recognizable_intent_returns_nulls(client):
    resp = client.post("/api/intents/assist", json={"text": "hello there"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent_type"] is None
    assert body["intent_label"] is None
    assert body["required_fields"] == []
    assert body["missing_fields"] == []


def test_assist_does_not_execute_or_audit(client):
    before = len(client.get("/api/audit").json())
    client.post("/api/intents/assist", json={"text": "Qualify this lead. company_name: AssistOnlyCo"})
    after = len(client.get("/api/audit").json())
    assert after == before
