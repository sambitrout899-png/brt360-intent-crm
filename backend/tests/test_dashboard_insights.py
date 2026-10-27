from test_product_hierarchy import _run


def test_dashboard_insights_cover_the_lead_to_cash_picture(client):
    client.post("/api/intents", json={"text": "Launch a new bundle. name: Fiber 500 Bundle, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 1000, discount: 0"})
    opp = client.post("/api/intents", json={"text": "Qualify this lead. company_name: Dash Co, source: referral, region: North"}).json()["result"]["opportunity_id"]
    quote = client.post("/api/intents", json={"text": f"Configure and quote this bundle. opportunity_id: {opp}, offering_name: Fiber 500 Bundle"}).json()["result"]["quote_id"]
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote}"})
    client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote}"})
    _run(client, "INT-15", name="Waiting Prospect", segment="B2B")

    r = client.get("/api/dashboard/insights", params={"days": 30})
    assert r.status_code == 200, r.text
    d = r.json()
    kpis = {k["key"]: k for k in d["kpis"]}
    assert kpis["mrr"]["value"] == 1000 and kpis["new_mrr"]["value"] == 1000 and kpis["win_rate"]["value"] == 100
    funnel = {f["label"]: f["count"] for f in d["funnel"]}
    assert funnel["Leads"] == 1 and funnel["Orders"] == 1 and funnel["Live services"] == 1 and funnel["Customers & prospects"] == 2
    assert d["revenue_by_product"][0] == {"name": "Fiber 500 Bundle", "mrr": 1000, "share": 100}
    assert d["mrr_trend"][-1]["mrr"] == 1000 and len(d["mrr_trend"]) == 6
    assert sum(a["executed"] for a in d["activity"]) >= 5 and d["top_intents"]
    assert d["insights"] and any("MRR" in i["text"] or "product" in i["text"].lower() for i in d["insights"])
    assert client.get("/api/dashboard/summary").status_code == 200  # the old summary is unchanged
