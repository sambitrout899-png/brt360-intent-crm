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


def test_multi_tier_chain_requires_one_approval_per_tier(client):
    """>30% discount resolves to the seeded "Executive Escalation" template
    (VP Sales -> CFO). The quote must stay pending_approval after the first
    approve call and only become approved after the second.
    """
    _launch_bundle(client, name="Fiber 1000 Elite", price=2000)
    opp_id = _qualify_lead(client, company="MegaCorp").json()["result"]["opportunity_id"]

    quote_resp = client.post(
        "/api/intents",
        json={
            "text": f"Configure and quote this bundle. opportunity_id: {opp_id}, "
            f"offering_name: Fiber 1000 Elite, discount: 40, justification: strategic deal"
        },
    )
    quote_id = quote_resp.json()["result"]["quote_id"]

    first = client.post(
        "/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}, reasoning: regional ok"}
    ).json()
    assert first["status"] == "executed"
    assert first["result"]["status"] == "pending_approval"
    assert "quote.approved" not in first["events"]

    dashboard_mid = client.get("/api/dashboard/summary").json()
    assert dashboard_mid["pending_approvals"] >= 1

    quote_mid = client.get(f"/api/quotes/{quote_id}").json()
    assert quote_mid["approval_chain"]["status"] == "pending"
    assert quote_mid["approval_chain"]["current_tier"] == "CFO"
    assert quote_mid["approval_chain"]["steps"][0]["status"] == "approved"
    assert quote_mid["approval_chain"]["steps"][1]["status"] == "pending"

    second = client.post(
        "/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}, reasoning: final sign-off"}
    ).json()
    assert second["status"] == "executed"
    assert second["result"]["status"] == "approved"
    assert "quote.approved" in second["events"]

    quote_final = client.get(f"/api/quotes/{quote_id}").json()
    assert quote_final["approval_chain"]["status"] == "approved"
    assert quote_final["approval_chain"]["current_tier"] is None


def test_rejection_at_any_tier_stops_the_chain(client):
    _launch_bundle(client, name="Fiber 1000 Max", price=2000)
    opp_id = _qualify_lead(client, company="RejectCo").json()["result"]["opportunity_id"]

    quote_resp = client.post(
        "/api/intents",
        json={
            "text": f"Configure and quote this bundle. opportunity_id: {opp_id}, "
            f"offering_name: Fiber 1000 Max, discount: 35, justification: big deal"
        },
    )
    quote_id = quote_resp.json()["result"]["quote_id"]

    first = client.post(
        "/api/intents",
        json={"text": f"Reject this quote. quote_id: {quote_id}, decision: reject, reasoning: not viable"},
    ).json()
    assert first["result"]["status"] == "rejected"
    assert "quote.rejected" in first["events"]

    quote_final = client.get(f"/api/quotes/{quote_id}").json()
    assert quote_final["status"] == "rejected"
    assert quote_final["approval_chain"]["status"] == "rejected"
