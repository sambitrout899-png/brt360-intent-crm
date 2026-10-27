import time


def _qualify_party(client, company="VerifyMeCo"):
    client.post(
        "/api/intents",
        json={"text": f"Qualify this lead. company_name: {company}, source: referral, region: North"},
    )
    parties = client.get(f"/api/parties?q={company}").json()
    return next(p["id"] for p in parties if p["name"] == company)


def test_verify_identity_succeeds_by_default(client):
    party_id = _qualify_party(client, "VerifyOkCo")
    resp = client.post(
        "/api/intents",
        json={"text": f"Verify customer identity. party_id: {party_id}, id_number: 1234567890123"},
    )
    body = resp.json()
    assert body["intent_type"] == "INT-13"
    assert body["status"] == "executed"
    assert body["result"]["status"] == "verified"
    assert body["result"]["provider"] == "Yakeen"


def test_verify_identity_rejects_fail_marker(client):
    party_id = _qualify_party(client, "VerifyFailCo")
    resp = client.post(
        "/api/intents",
        json={"text": f"Verify customer identity. party_id: {party_id}, id_number: FAIL999999999"},
    )
    body = resp.json()
    assert body["result"]["status"] == "rejected"


def test_verify_identity_custom_provider(client):
    party_id = _qualify_party(client, "VerifyProviderCo")
    resp = client.post(
        "/api/intents",
        json={"text": f"Verify customer identity. party_id: {party_id}, id_number: 9988776655443, provider: WATHQ"},
    )
    assert resp.json()["result"]["provider"] == "WATHQ"


def test_verify_identity_pending_without_policy_stays_pending(client):
    party_id = _qualify_party(client, "VerifyPendingNoPolicyCo")
    resp = client.post(
        "/api/intents",
        json={"text": f"Verify customer identity. party_id: {party_id}, id_number: PENDING12345"},
    )
    assert resp.json()["result"]["status"] == "pending"

    sweep = client.post("/api/admin/sla/process-due").json()
    assert sweep["checked"] == 0  # no kyc_verification policy configured - no clock was started


def test_verify_identity_pending_auto_approves_after_sla_breach(client):
    client.post(
        "/api/admin/sla-policies",
        json={"clock_type": "kyc_verification", "duration_hours": 0.0001, "on_breach_action": "auto_approve"},
    )
    party_id = _qualify_party(client, "VerifyPendingBreachCo")
    submit = client.post(
        "/api/intents",
        json={"text": f"Verify customer identity. party_id: {party_id}, id_number: PENDING98765"},
    ).json()
    assert submit["result"]["status"] == "pending"
    verification_id = submit["result"]["verification_id"]

    time.sleep(0.5)
    sweep = client.post("/api/admin/sla/process-due").json()
    assert sweep["checked"] == 1
    assert sweep["breached"][0]["outcome"] == "verified"

    party = client.get(f"/api/parties/{party_id}/360").json()
    record = next(v for v in party["identity_verifications"] if v["id"] == verification_id)
    assert record["status"] == "verified"


def test_verify_identity_blocked_for_b2b_only_tenant(client):
    db = client.TestSessionLocal()
    try:
        from app.auth import hash_password
        from app.models.auth import User, UserRole
        from app.models.crm import Party
        from app.models.tenant import Tenant, TenantVertical

        b2b_tenant = Tenant(slug="b2b-only", name="B2B Only Co", vertical=TenantVertical.b2b)
        db.add(b2b_tenant)
        db.flush()
        party = Party(tenant_id=b2b_tenant.id, name="B2BOnlyParty", party_type="organization")
        db.add(party)
        user = User(
            tenant_id=b2b_tenant.id,
            email="admin@b2b-only.test",
            password_hash=hash_password("password123"),
            name="B2B Admin",
            role=UserRole.admin,
        )
        db.add(user)
        db.commit()
        party_id = party.id
        user_id = user.id
    finally:
        db.close()

    resp = client.post(
        "/api/intents",
        json={"text": f"Verify customer identity. party_id: {party_id}, id_number: 1234567890123"},
        headers={"X-Test-User-Id": str(user_id)},
    )
    body = resp.json()
    assert body["status"] == "needs_review"
    assert "B2B only" in body["rationale"]


def test_identity_providers_admin_crud(client):
    providers = client.get("/api/admin/identity-providers").json()
    names = {p["name"] for p in providers}
    assert names == {"Yakeen", "WATHQ", "SIMAH", "Abshir"}

    create_resp = client.post("/api/admin/identity-providers", json={"name": "CustomProvider"})
    assert create_resp.status_code == 201
    provider_id = create_resp.json()["id"]

    deactivate_resp = client.patch(f"/api/admin/identity-providers/{provider_id}/deactivate")
    assert deactivate_resp.json()["is_active"] is False


def test_b2b_only_tenant_verifies_a_business_by_commercial_registration(client):
    db = client.TestSessionLocal()
    try:
        from app.auth import hash_password
        from app.models.auth import User, UserRole
        from app.models.crm import Party
        from app.models.identity import IdentityVerificationProvider as IdentityProvider
        from app.models.tenant import Tenant, TenantVertical

        tenant = Tenant(slug="b2b-cr", name="B2B CR Co", vertical=TenantVertical.b2b)
        db.add(tenant)
        db.flush()
        party = Party(tenant_id=tenant.id, name="Wipro India", party_type="organization")
        db.add_all([party, IdentityProvider(tenant_id=tenant.id, name="WATHQ"), IdentityProvider(tenant_id=tenant.id, name="Yakeen")])
        user = User(tenant_id=tenant.id, email="admin@b2b-cr.test", password_hash=hash_password("password123"), name="Admin", role=UserRole.admin)
        db.add(user)
        db.commit()
        party_id, user_id = party.id, user.id
    finally:
        db.close()

    headers = {"X-Test-User-Id": str(user_id)}
    personal = client.post("/api/intents", json={"text": "", "structured": {"intent_type": "INT-13", "entities": {
        "party_id": party_id, "id_number": "1234567890", "id_type": "national_id"}}}, headers=headers).json()
    assert personal["status"] == "needs_review" and "commercial registration" in personal["rationale"] and "INT-13" not in personal["rationale"]

    business = client.post("/api/intents", json={"text": "", "structured": {"intent_type": "INT-13", "entities": {
        "party_id": party_id, "id_number": "323232323", "id_type": "commercial_registration"}}}, headers=headers).json()
    assert business["status"] == "executed", business["rationale"]
    assert business["result"]["provider"] == "WATHQ"  # the business registry, when no provider is picked


def test_b2c_identity_form_clears_business_defaults_and_execution_rejects_them(client, monkeypatch):
    from app.models.crm import Party
    from app.crm_core import process_service, identity_service
    from app.intent_engine import copilot
    import pytest
    with client.TestSessionLocal() as db:
        party = Party(tenant_id=1, name="Consumer identity regression", party_type="individual")
        db.add(party)
        process_service.party_profile(party, create=True).segment = "B2C"
        db.commit()
        party_id = party.id
    monkeypatch.setattr(copilot, "_remembered", lambda *args: {"id_type": "commercial_registration", "provider": "WATHQ"})
    for entities in ({"party_id": party_id}, {"party_id": party_id, "id_type": "commercial_registration", "provider": "WATHQ"}):
        response = client.post("/api/intents/prepare", json={"intent_type": "INT-13", "entities": entities})
        assert response.status_code == 200, response.text
        body = response.json()
        fields = {f["key"]: f for f in body["fields"]}
        assert "commercial_registration" not in [o["value"] for o in fields["id_type"]["options"]]
        assert "WATHQ" not in [o["value"] for o in fields["provider"]["options"]]
        assert body["values"]["id_type"] == "national_id"
        assert body["values"].get("provider") != "WATHQ"
    with client.TestSessionLocal() as db:
        with pytest.raises(ValueError, match="Commercial registration"):
            identity_service.submit_verification(db, 1, party_id, "WATHQ", "commercial_registration", "123456")
        verified = identity_service.submit_verification(db, 1, party_id, "Yakeen", "national_id", "123456")
        assert verified.id_type == "national_id"
