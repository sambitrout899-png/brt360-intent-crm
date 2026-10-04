def _approved_quote(client, offering_name="Fiber 500 Bundle", price=1000, company="Acme Telecom"):
    client.post(
        "/api/intents",
        json={
            "text": f"Launch a new bundle. name: {offering_name}, composition: Fiber500, "
            f"segment: B2B-SMB, channel: B2B, price: {price}, discount: 0"
        },
    )
    opp_id = client.post(
        "/api/intents",
        json={"text": f"Qualify this lead. company_name: {company}, source: referral, region: North"},
    ).json()["result"]["opportunity_id"]
    quote_id = client.post(
        "/api/intents",
        json={"text": f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: {offering_name}"},
    ).json()["result"]["quote_id"]
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}"})
    return quote_id


def test_full_o2a_chain_activate_change_cancel(client):
    quote_id = _approved_quote(client)

    activate = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"})
    activate_body = activate.json()
    assert activate_body["intent_type"] == "INT-07"
    assert activate_body["status"] == "executed"
    assert activate_body["result"]["status"] == "active"
    assert "order.activated" in activate_body["events"]
    order_id = activate_body["result"]["order_id"]

    change = client.post(
        "/api/intents", json={"text": f"Upgrade this service. order_id: {order_id}, new_price: 1200"}
    )
    change_body = change.json()
    assert change_body["intent_type"] == "INT-08"
    assert change_body["status"] == "executed"
    assert change_body["result"]["new_monthly_price"] == 1200.0

    cancel = client.post("/api/intents", json={"text": f"Cancel this service. order_id: {order_id}"})
    cancel_body = cancel.json()
    assert cancel_body["intent_type"] == "INT-09"
    assert cancel_body["status"] == "executed"
    assert cancel_body["result"]["status"] == "cancelled"


def test_activate_requires_approved_quote(client):
    client.post(
        "/api/intents",
        json={
            "text": "Launch a new bundle. name: Fiber Starter, composition: Fiber100, "
            "segment: B2C, channel: B2C, price: 400, discount: 0"
        },
    )
    opp_id = client.post(
        "/api/intents",
        json={"text": "Qualify this lead. company_name: SmallCo, source: web, region: South"},
    ).json()["result"]["opportunity_id"]
    quote_id = client.post(
        "/api/intents",
        json={"text": "Configure and quote this bundle. opportunity_id: " + str(opp_id) + ", offering_name: Fiber Starter"},
    ).json()["result"]["quote_id"]

    # No approval step - quote is still in draft.
    resp = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"})
    assert resp.json()["status"] == "needs_review"


def test_suspend_does_not_release_resources(client):
    quote_id = _approved_quote(client, offering_name="Fiber 500 Bundle Suspend Test", company="SuspendCo")
    order_id = client.post(
        "/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"}
    ).json()["result"]["order_id"]

    resp = client.post("/api/intents", json={"text": f"Suspend this service. order_id: {order_id}"})
    body = resp.json()
    assert body["result"]["status"] == "suspended"
    assert body["result"]["early_termination_fee"] == 0.0
