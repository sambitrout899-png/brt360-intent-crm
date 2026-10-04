def _launch_bundle(client, name="Fiber 500 Bundle", price=1000):
    return client.post(
        "/api/intents",
        json={
            "text": f"Launch a new bundle. name: {name}, composition: Fiber500, "
            f"segment: B2B-SMB, channel: B2B, price: {price}, discount: 0"
        },
    )


def _qualify_lead(client, company="Acme Telecom"):
    return client.post(
        "/api/intents",
        json={"text": f"Qualify this lead. company_name: {company}, source: referral, region: North"},
    )


def test_full_l2q_chain_configure_and_approve(client):
    _launch_bundle(client)
    opp_id = _qualify_lead(client).json()["result"]["opportunity_id"]

    quote_resp = client.post(
        "/api/intents",
        json={
            "text": f"Configure and quote this bundle. opportunity_id: {opp_id}, "
            f"offering_name: Fiber 500 Bundle, quantity: 1, discount: 5"
        },
    )
    quote_body = quote_resp.json()
    assert quote_body["intent_type"] == "INT-05"
    assert quote_body["status"] == "executed"
    quote_id = quote_body["result"]["quote_id"]
    assert quote_body["result"]["total_price"] == 950.0  # 1000 * (1 - 5%)

    approve_resp = client.post(
        "/api/intents",
        json={"text": f"Approve this quote. quote_id: {quote_id}, reasoning: strategic account"},
    )
    approve_body = approve_resp.json()
    assert approve_body["intent_type"] == "INT-06"
    assert approve_body["status"] == "executed"
    assert approve_body["result"]["status"] == "approved"
    assert "quote.approved" in approve_body["events"]

    catalog_audit = client.get("/api/audit").json()
    assert catalog_audit[0]["intent_type"] == "INT-06"


def test_high_discount_requires_justification(client):
    _launch_bundle(client, name="Fiber 1000 Elite", price=2000)
    opp_id = _qualify_lead(client, company="BigCorp").json()["result"]["opportunity_id"]

    resp = client.post(
        "/api/intents",
        json={
            "text": f"Configure and quote this bundle. opportunity_id: {opp_id}, "
            f"offering_name: Fiber 1000 Elite, discount: 25"
        },
    )
    body = resp.json()
    assert body["status"] == "failed"
    assert "justification" in body["rationale"]


def test_approve_unknown_quote_needs_review(client):
    resp = client.post("/api/intents", json={"text": "Approve this quote. quote_id: 99999"})
    assert resp.json()["status"] == "needs_review"
