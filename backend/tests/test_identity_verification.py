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
        from app.models.crm import Party
        from app.models.tenant import Tenant, TenantVertical

        b2b_tenant = Tenant(slug="b2b-only", name="B2B Only Co", vertical=TenantVertical.b2b)
        db.add(b2b_tenant)
        db.flush()
        party = Party(tenant_id=b2b_tenant.id, name="B2BOnlyParty", party_type="organization")
        db.add(party)
        db.commit()
        party_id = party.id
    finally:
        db.close()

    resp = client.post(
        "/api/intents",
        json={"text": f"Verify customer identity. party_id: {party_id}, id_number: 1234567890123"},
        headers={"X-Tenant-Slug": "b2b-only"},
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
