"""The telecom demo history: generated end to end, idempotent, and rich enough for every analytics feature."""
from app.analytics import demo_data
from app.models.comms import CmInvoice, CmUsage
from app.models.crm import Party
from app.models.orders import Order, OrderStatus


def test_demo_history_feeds_kpis_models_and_forecasts(client):
    db = client.TestSessionLocal()
    try:
        tid = db.query(Party).first().tenant_id if db.query(Party).first() else 1
        out = demo_data.seed_history(db, tid, customers=60, months=14, seed=3)
        assert out["seeded"] and out["Order"] > 40 and out["CmInvoice"] > 300 and out["CmUsage"] > 3000
        assert demo_data.seed_history(db, tid, customers=60, months=14)["seeded"] is False  # second run does nothing
        statuses = {s for (s,) in db.query(Order.status).filter(Order.tenant_id == tid)}
        assert {OrderStatus.active, OrderStatus.cancelled} <= statuses
        assert db.query(CmInvoice).filter(CmInvoice.status == "overdue").count() > 0
        assert db.query(CmUsage).filter(CmUsage.international.is_(True)).count() > 0
    finally:
        db.close()

    sc = client.get("/api/analytics/telecom").json()
    k = {x["key"]: x["value"] for g in sc["groups"] for x in g["kpis"]}
    assert k["mrr"] > 0 and k["win_rate"] and k["nps"] is not None and k["activation_days"] and k["sla_breach"] is not None
    assert k["data_gb"] > 0 and k["collection_rate"]

    churn = client.get("/api/analytics/predict/churn").json()
    # the history is generated relative to the current date and hour, so how many customers churned
    # varies through the day (2-6 seen) - what matters is that there are some to learn from
    assert churn["metrics"]["positives"] >= 1 and churn["customers"]
    win = client.get("/api/analytics/predict/win").json()
    assert win["metrics"]["trained_on"] > 20
    assert all(o["stage"] not in ("won", "lost", "feasibility_failed") for o in win["opportunities"])
    assert client.get("/api/analytics/predict/usage-alerts").json()["alerts"]  # this week's international spikes
    assert client.get("/api/analytics/predict/next-offer").json()["rules"]
    fc = client.post("/api/analytics/forecast", json={"preset": "billed", "horizon": 3}).json()
    assert len(fc["forecast"]) == 3 and len(fc["history"]) >= 10
