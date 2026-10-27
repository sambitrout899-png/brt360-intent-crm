from test_product_hierarchy import _run


def test_customer_overview_tells_the_story_and_suggests_what_next(client):
    client.post("/api/intents", json={"text": "Launch a new bundle. name: Fiber 500 Bundle, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 1000, discount: 0"})
    r = client.post("/api/intents", json={"text": "Qualify this lead. company_name: Story Co, source: referral, region: North"}).json()["result"]
    party_id, opp = r["party_id"], r["opportunity_id"]
    quote = client.post("/api/intents", json={"text": f"Configure and quote this bundle. opportunity_id: {opp}, offering_name: Fiber 500 Bundle"}).json()["result"]["quote_id"]

    o = client.get(f"/api/customers/{party_id}/overview").json()
    assert o["journey"]["current"] == "Quoted"
    assert o["next_actions"][0]["code"] == "INT-06" and o["next_actions"][0]["entities"] == {"quote_id": quote}
    assert o["numbers"]["pipeline"] == 1000 and o["numbers"]["mrr"] == 0

    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote}"})
    client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote}"})
    o = client.get(f"/api/customers/{party_id}/overview").json()
    assert o["journey"]["current"] == "Live" and all(s["reached"] for s in o["journey"]["steps"])
    assert o["numbers"]["mrr"] == 1000 and o["services"][0]["product"] == "Fiber 500 Bundle"
    assert any(a["code"] == "INT-107" for a in o["next_actions"])
    assert {e["icon"] for e in o["timeline"]} >= {"👤", "🌱", "💼", "🧾", "📦"}

    _run(client, "INT-15", name="Fresh Prospect", segment="B2B")
    rows = {c["name"]: c for c in client.get("/api/customers").json()}
    assert rows["Story Co"]["mrr"] == 1000 and rows["Story Co"]["status"] == "account"
    assert rows["Fresh Prospect"]["status"] == "prospect" and rows["Fresh Prospect"]["segment"] == "B2B"
    fresh = client.get(f"/api/customers/{rows['Fresh Prospect']['id']}/overview").json()
    assert fresh["next_actions"][0]["code"] == "INT-46"
