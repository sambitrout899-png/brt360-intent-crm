def test_qualify_lead_creates_lead_and_opportunity(client):
    resp = client.post(
        "/api/intents",
        json={"text": "Qualify this lead. company_name: Acme Telecom, source: referral, region: North"},
    )
    body = resp.json()
    assert body["intent_type"] == "INT-04"
    assert body["status"] == "executed"
    assert body["result"]["lead_id"] is not None
    assert body["result"]["opportunity_id"] is not None
    assert "lead.qualified" in body["events"]

    # Same company always enriches/scores the same way (deterministic stand-in).
    resp2 = client.post(
        "/api/intents",
        json={"text": "Qualify this lead. company_name: Acme Telecom, source: web, region: North"},
    )
    assert resp2.json()["result"] is not None


def test_qualify_lead_missing_company_needs_review(client):
    resp = client.post("/api/intents", json={"text": "qualify this one please"})
    assert resp.json()["status"] == "needs_review"
