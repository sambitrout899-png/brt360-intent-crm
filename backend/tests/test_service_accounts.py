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


def test_activation_creates_service_account_named_after_offering(client):
    quote_id = _approved_quote(client, offering_name="Fiber 500 Bundle", company="SvcAcctCo1")
    result = client.post(
        "/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"}
    ).json()["result"]
    assert result["service_account_name"] == "Fiber 500 Bundle Service"
    order_id = result["order_id"]

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["service_account_id"] == result["service_account_id"]
    assert order["service_account_name"] == "Fiber 500 Bundle Service"


def test_reactivating_same_offering_reuses_service_account(client):
    company = "SvcAcctCo2"
    quote_1 = _approved_quote(client, offering_name="Fiber 500 Bundle", company=company)
    first = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_1}"}).json()["result"]

    quote_2 = _approved_quote(client, offering_name="Fiber 500 Bundle", price=1000, company=company)
    second = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_2}"}).json()["result"]

    assert first["service_account_id"] == second["service_account_id"]


def test_different_offering_gets_different_service_account(client):
    company = "SvcAcctCo3"
    quote_1 = _approved_quote(client, offering_name="Fiber 500 Bundle", company=company)
    first = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_1}"}).json()["result"]

    quote_2 = _approved_quote(client, offering_name="Fiber Business Line", price=2000, company=company)
    second = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_2}"}).json()["result"]

    assert first["service_account_id"] != second["service_account_id"]


def test_explicit_service_account_name_overrides_default(client):
    quote_id = _approved_quote(client, offering_name="Fiber 500 Bundle", company="SvcAcctCo4")
    result = client.post(
        "/api/intents",
        json={"text": f"Activate this order end-to-end. quote_id: {quote_id}, service_account_name: HQ Office"},
    ).json()["result"]
    assert result["service_account_name"] == "HQ Office"


def test_party_360_nests_orders_under_service_accounts(client):
    company = "SvcAcctCo5"
    quote_1 = _approved_quote(client, offering_name="Fiber 500 Bundle", company=company)
    client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_1}"})

    quote_2 = _approved_quote(client, offering_name="Fiber Business Line", price=2000, company=company)
    client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_2}"})

    parties = client.get(f"/api/parties?q={company}").json()
    party_id = next(p["id"] for p in parties if p["name"] == company)
    party = client.get(f"/api/parties/{party_id}/360").json()

    assert len(party["service_accounts"]) == 2
    names = {sa["name"] for sa in party["service_accounts"]}
    assert names == {"Fiber 500 Bundle Service", "Fiber Business Line Service"}
    for sa in party["service_accounts"]:
        assert len(sa["orders"]) == 1
    assert len(party["orders"]) == 2
