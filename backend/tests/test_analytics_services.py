"""Analytics counts a completed order's service by its asset (analytics/services.py):
live while the service is, once even after change orders, churned once it ends."""

from test_o2a_chain import _approved_quote


def _scorecard(client):
    sc = client.get("/api/analytics/telecom").json()
    k = {x["key"]: x for g in sc["groups"] for x in g["kpis"]}
    return k["mrr"]["value"], k["mrr"]["hint"]


def test_a_completed_orders_service_counts_live_once_then_churns(client):
    quote_id = _approved_quote(client, company="Analytics Live Co")
    activated = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"}).json()
    assert activated["result"]["status"] == "completed"
    order_id = activated["result"]["order_id"]

    mrr, hint = _scorecard(client)
    assert mrr == 1000 and hint == "1 live services"  # was 0: completed orders were invisible to analytics

    upgrade = client.post("/api/intents", json={"text": f"Upgrade this service. order_id: {order_id}, new_price: 1200"}).json()
    assert upgrade["status"] == "executed"
    mrr, hint = _scorecard(client)
    assert mrr == 1200 and hint == "1 live services"  # the change order doesn't count as a second service

    cancel = client.post("/api/intents", json={"text": f"Cancel this service. order_id: {order_id}"}).json()
    assert cancel["result"]["status"] == "cancelled"
    mrr, hint = _scorecard(client)
    assert mrr == 0 and hint == "0 live services"
    sc = client.get("/api/analytics/telecom").json()
    k = {x["key"]: x for g in sc["groups"] for x in g["kpis"]}
    assert "1 cancelled or terminated" in k["service_churn"]["hint"]


def test_report_builder_sees_the_service_state(client):
    quote_id = _approved_quote(client, company="Analytics Report Co")
    order_id = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"}).json()["result"]["order_id"]
    client.post("/api/intents", json={"text": f"Suspend this service. order_id: {order_id}"})

    from app.analytics import engine, semantic

    with client.TestSessionLocal() as db:
        m = semantic.model(db, 1, "order")
        rows, _ = engine.read(db, 1, None, m, ["id", "status", "mrr", "is_live"], [])
    row = next(r for r in rows if r["id"] == order_id)
    assert row["status"] == "suspended" and row["is_live"] is True and row["mrr"] == 1000
