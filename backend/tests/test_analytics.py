"""Analytics & Reports: slice and dice, drill to the records, saved reports and
dashboards, the telecom scorecard, forecasts and predictions - on a tenant
with 18 months of history."""

import datetime as dt
import random

import pytest

from app.analytics import ml
from app.models.accounts import ServiceAccount
from app.models.assurance import TroubleTicket
from app.models.catalog import ProductOffering
from app.models.comms import CmInvoice, CmPayment, CmSurvey, CmUsage
from app.models.crm import Lead, LeadStatus, Opportunity, OpportunityStage, Party, PartyProfile, Quote, QuoteStatus
from app.models.orders import BillingAccount, Order, OrderItem, OrderStatus

NOW = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
PRODUCTS = ["Fiber 500", "Fiber 1G", "SIP Trunk", "Managed Wi-Fi", "Static IP"]


def _seed(client) -> dict:
    rng = random.Random(7)
    db = client.TestSessionLocal()
    try:
        tid = db.query(Party).first().tenant_id if db.query(Party).first() else 1
        offerings = {}
        for name in PRODUCTS:
            o = ProductOffering(tenant_id=tid, name=name, segment="B2B", price=1000, composition=name)
            db.add(o)
            db.flush()
            offerings[name] = o.id
        churned, live = 0, 0
        for i in range(48):
            seg = "B2B" if i % 3 else "B2C"
            created = NOW - dt.timedelta(days=540 - i * 10)
            p = Party(tenant_id=tid, name=f"Customer {i:02d}", created_at=created)
            db.add(p)
            db.flush()
            db.add(PartyProfile(party_id=p.id, segment=seg, industry="Government" if i % 4 == 0 else "Retail"))
            ba = BillingAccount(tenant_id=tid, party_id=p.id)
            db.add(ba)
            db.flush()
            sa = ServiceAccount(tenant_id=tid, party_id=p.id, billing_account_id=ba.id, name=f"SA {i}")
            db.add(sa)
            db.flush()
            # unhappy customers (many tickets, late bills, falling usage) churn - the pattern the model should learn
            unhappy = i % 4 == 1
            gone = unhappy and i < 40
            status = OrderStatus.cancelled if gone else OrderStatus.active
            churned += gone
            live += not gone
            o = Order(tenant_id=tid, party_id=p.id, service_account_id=sa.id, status=status, monthly_price=500 + 100 * (i % 5),
                      contract_months=12 + 12 * (i % 3), created_at=created + dt.timedelta(days=5),
                      updated_at=(created + dt.timedelta(days=200)) if gone else created + dt.timedelta(days=6))
            db.add(o)
            db.flush()
            owned = [PRODUCTS[i % 2]] + ([PRODUCTS[2]] if i % 2 == 0 else []) + ([PRODUCTS[3]] if i % 5 == 0 else [])
            for name in owned:
                db.add(OrderItem(order_id=o.id, product_offering_id=offerings[name], quantity=1, unit_price=300, net_unit_price=300))
            as_of = o.updated_at if gone else NOW
            for k in range(6 if unhappy else rng.randint(0, 1)):
                t = TroubleTicket(tenant_id=tid, party_id=p.id, category="Connectivity" if k % 2 else "Billing",
                                  severity="critical" if unhappy and k % 2 else "low", status="resolved" if k % 3 else "open",
                                  created_at=as_of - dt.timedelta(days=10 + 9 * k), updated_at=as_of - dt.timedelta(days=8 + 9 * k))
                db.add(t)
                db.flush()
                if k == 0:
                    db.add(CmSurvey(tenant_id=tid, ticket_id=t.id, overall=1 if unhappy else 5, would_recommend=2 if unhappy else 10, score=20 if unhappy else 95))
            for m in range(3):
                issue = (as_of - dt.timedelta(days=30 * (m + 1))).date()
                paid = 0 if unhappy else 1000
                db.add(CmInvoice(tenant_id=tid, party_id=p.id, period_start=issue, period_end=issue, issue_date=issue,
                                 due_date=issue + dt.timedelta(days=15), total=1000, paid=paid, status="paid" if paid else "overdue"))
            for d in range(60):
                at = as_of - dt.timedelta(days=d, hours=rng.randint(0, 23))
                amount = (2 if d < 30 else 10) if unhappy else 8 + rng.random()
                db.add(CmUsage(tenant_id=tid, party_id=p.id, at=at, event_type="data" if d % 2 else "voice", quantity=500, amount=amount))
            if not unhappy:
                db.add(CmPayment(tenant_id=tid, party_id=p.id, amount=1000, method="eft", received_at=NOW - dt.timedelta(days=i)))
        # a usage spike this week for one live customer
        spiky = db.query(Party).filter(Party.name == "Customer 02").one()
        for d in range(7):
            db.add(CmUsage(tenant_id=tid, party_id=spiky.id, at=NOW - dt.timedelta(days=d, hours=1), event_type="voice", quantity=900,
                           amount=400, international=True))
        # deals: referral leads win, cold calls lose
        for j in range(30):
            p = db.query(Party).filter(Party.name == f"Customer {j:02d}").one()
            source = "Referral" if j % 2 else "Cold call"
            lead = Lead(tenant_id=tid, party_id=p.id, source=source, owner_name="Asha", propensity_score=0.8 if j % 2 else 0.3,
                        status=LeadStatus.qualified, created_at=NOW - dt.timedelta(days=200 - j))
            db.add(lead)
            db.flush()
            stage = (OpportunityStage.won if j % 2 else OpportunityStage.lost) if j < 24 else OpportunityStage.quoted
            opp = Opportunity(tenant_id=tid, lead_id=lead.id, name=f"Deal {j}", stage=stage, quantity=1 + j % 3,
                              created_at=NOW - dt.timedelta(days=190 - j), updated_at=NOW - dt.timedelta(days=150 - j))
            db.add(opp)
            db.flush()
            db.add(Quote(tenant_id=tid, opportunity_id=opp.id, total_price=10000 + 1000 * j, status=QuoteStatus.approved,
                         requested_discount_pct=5 if j % 2 else 18, created_at=NOW - dt.timedelta(days=180 - j)))
        db.commit()
        return {"churned": churned, "live": live}
    finally:
        db.close()


@pytest.fixture()
def seeded(client):
    return client, _seed(client)


def test_catalog_lists_entities_fields_and_custom_fields(seeded):
    client, _ = seeded
    cat = client.get("/api/analytics/catalog").json()
    keys = {e["key"]: e for e in cat["entities"]}
    assert {"order", "invoice", "ticket", "usage", "opportunity", "party", "order_item"} <= set(keys)
    order_fields = {f["key"]: f for f in keys["order"]["fields"]}
    assert order_fields["monthly_price"]["measure"] and order_fields["monthly_price"]["fmt"] == "money"
    assert order_fields["status"]["values"] and "active" in order_fields["status"]["values"]
    assert "customer_profile.segment" in order_fields and order_fields["tcv"]["computed"]
    assert "month" in cat["grains"] and "pct_total" in cat["calculations"]


def test_slice_and_dice_pivot_with_grains_filters_and_calculations(seeded):
    client, data = seeded
    out = client.post("/api/analytics/query", json={
        "entity": "order", "dimensions": [{"field": "customer_profile.segment"}, {"field": "status"}],
        "measures": [{"agg": "count"}, {"field": "monthly_price", "agg": "sum"}, {"field": "monthly_price", "agg": "sum", "calc": "pct_total"}]}).json()
    assert out["scanned"] == 48 and out["totals"]["count"] == 48
    cells = {(r["customer_profile.segment"], r["status"]): r for r in out["rows"]}
    assert sum(r["count"] for r in out["rows"]) == 48
    assert cells[("B2B", "active")]["count"] > 0
    assert abs(sum(r["sum(monthly_price)~pct_total"] for r in out["rows"]) - 100) < 0.1
    assert out["narrative"] and "48 records" in out["narrative"][0]["text"]

    by_month = client.post("/api/analytics/query", json={
        "entity": "order", "dimensions": [{"field": "created_at", "grain": "month"}], "measures": [{"agg": "count", "calc": "running"}],
        "filters": [{"field": "customer_profile.segment", "op": "eq", "value": "B2B"}]}).json()
    months = [r["created_at:month"] for r in by_month["rows"]]
    assert months == sorted(months) and by_month["rows"][-1]["count~running"] == 32

    binned = client.post("/api/analytics/query", json={"entity": "quote", "dimensions": [{"field": "total_price", "bins": 4}],
                                                       "measures": [{"agg": "count"}]}).json()
    assert sum(r["count"] for r in binned["rows"]) == 30 and "–" in binned["rows"][0]["total_price:bins"]

    top = client.post("/api/analytics/query", json={"entity": "order_item", "dimensions": [{"field": "offering.name"}],
                                                    "measures": [{"field": "line_value", "agg": "sum"}], "top": {"n": 2, "other": True}}).json()
    assert len(top["rows"]) == 3 and any(r["offering.name"] == "Other" for r in top["rows"])

    # enum fields filter by their values - on the entity and through a join
    live = client.post("/api/analytics/query", json={"entity": "order", "filters": [{"field": "status", "op": "in", "value": ["active"]}]}).json()
    assert live["totals"]["count"] == data["live"]
    lines = client.post("/api/analytics/query", json={"entity": "order_item", "filters": [{"field": "order.status", "op": "eq", "value": "cancelled"}]}).json()
    assert lines["totals"]["count"] > 0

    bad = client.post("/api/analytics/query", json={"entity": "order", "measures": [{"field": "status", "agg": "sum"}]})
    assert bad.status_code == 422 and "not a number" in bad.text


def test_drill_down_to_records_and_export(seeded):
    client, _ = seeded
    rec = client.post("/api/analytics/records", json={"entity": "order", "drill": [{"field": "customer_profile.segment", "value": "B2C"}],
                                                      "sort": {"key": "monthly_price", "dir": "desc"}, "limit": 5}).json()
    assert rec["total"] == 16 and len(rec["rows"]) == 5 and rec["record_type"] == "order"
    prices = [r["monthly_price"] for r in rec["rows"]]
    assert prices == sorted(prices, reverse=True) and all(r["__id"] for r in rec["rows"])

    month = client.post("/api/analytics/query", json={"entity": "order", "dimensions": [{"field": "created_at", "grain": "month"}]}).json()["rows"][0]
    cell = client.post("/api/analytics/records", json={"entity": "order", "drill": [{"field": "created_at", "grain": "month",
                                                                                     "value": month["created_at:month"]}]}).json()
    assert cell["total"] == month["count"]

    csv = client.post("/api/analytics/export", json={"kind": "records", "query": {"entity": "invoice"}, "name": "invoices"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.text.count("\n") == 48 * 3 + 1


def test_saved_reports_and_the_starter_dashboard(seeded):
    client, _ = seeded
    made = client.post("/api/analytics/reports", json={"name": "MRR by segment", "spec": {"entity": "order", "chart": "donut"}}).json()
    assert made["can_edit"] and made["spec"]["chart"] == "donut"
    assert client.put(f"/api/analytics/reports/{made['id']}", json={"name": "MRR split", "spec": {"entity": "order"}}).json()["name"] == "MRR split"
    dash = client.post("/api/analytics/dashboards/starter").json()
    assert dash["kind"] == "dashboard" and len(dash["spec"]["tiles"]) == 8
    reports = client.get("/api/analytics/reports?kind=report").json()
    assert len(reports) == 9
    # every starter report runs
    for r in reports:
        spec = r["spec"]
        resp = client.post("/api/analytics/query", json=spec)
        assert resp.status_code == 200 and resp.json()["rows"], (r["name"], spec)
    assert client.delete(f"/api/analytics/reports/{made['id']}").status_code == 204


def test_telecom_scorecard(seeded):
    client, data = seeded
    sc = client.get("/api/analytics/telecom").json()
    k = {x["key"]: x for g in sc["groups"] for x in g["kpis"]}
    assert k["mrr"]["value"] > 0 and k["arpu"]["value"] > 0
    assert k["win_rate"]["value"] == 50.0
    assert k["open_tickets"]["value"] > 0 and k["nps"]["value"] is not None
    charts = {c["key"]: c for c in sc["charts"]}
    assert len(charts["mrr_trend"]["labels"]) == 12 and charts["funnel"]["series"][0]["values"][0] == 30


def test_forecasts_and_anomalies(seeded):
    client, _ = seeded
    out = client.post("/api/analytics/forecast", json={"preset": "orders", "horizon": 4}).json()
    assert len(out["forecast"]) == 4 and out["history"] and out["method"]
    assert all(f["lower"] <= f["value"] <= f["upper"] for f in out["forecast"])
    daily = client.post("/api/analytics/forecast", json={"preset": "usage_revenue", "grain": "day", "horizon": 7}).json()
    assert len(daily["forecast"]) == 7

    # the maths on a known series: a trend with a 12-month season is continued
    y = [100 + 5 * t + 20 * ((t % 12) in (10, 11)) for t in range(36)]
    labels = [f"{2023 + t // 12}-{t % 12 + 1:02d}" for t in range(36)]
    fc = ml.forecast(labels, y, "month", 12, lambda lab: f"{int(lab[:4]) + (lab[5:] == '12')}-{int(lab[5:]) % 12 + 1:02d}")
    assert fc["season"] == 12 and fc["trend"]["direction"] == "up"
    assert abs(fc["forecast"][0]["value"] - (100 + 5 * 36)) < 15 and fc["forecast"][10]["value"] > fc["forecast"][9]["value"]
    assert fc["accuracy"]["mape"] is not None and fc["accuracy"]["mape"] < 5
    spikes = ml.anomalies([str(i) for i in range(20)], [10.0] * 9 + [80.0] + [10.0, 11.0, 9.0] * 3 + [10.0])
    assert [a["period"] for a in spikes] == ["9"]


def test_churn_model_learns_the_pattern(seeded):
    client, data = seeded
    out = client.get("/api/analytics/predict/churn").json()
    assert out["method"].startswith("Logistic regression"), out["method"]
    assert out["metrics"]["positives"] == data["churned"] and out["metrics"]["auc_train"] >= 0.9
    # live unhappy customers (i % 4 == 1, i >= 40) are ranked most at risk
    assert {c["name"] for c in out["customers"][:2]} == {"Customer 41", "Customer 45"}, out["customers"][:5]
    assert out["customers"][0]["band"] == "High"
    top = out["customers"][0]
    assert top["drivers"] and top["clv"] > 0
    assert out["summary"]["mrr_at_risk"] > 0


def test_win_probability_segments_offers_and_usage_alerts(seeded):
    client, _ = seeded
    win = client.get("/api/analytics/predict/win").json()
    assert win["summary"]["open"] == 6 and win["summary"]["weighted_pipeline"] > 0
    probs = {o["name"]: o["probability"] for o in win["opportunities"]}
    assert probs["Deal 25"] > probs["Deal 24"]  # referral beats cold call

    seg = client.get("/api/analytics/predict/segments").json()
    assert seg["segments"] and sum(s["size"] for s in seg["segments"]) == seg["customers"]
    assert all(s["name"] and s["action"] for s in seg["segments"])

    nbo = client.get("/api/analytics/predict/next-offer").json()
    assert nbo["rules"] and nbo["recommendations"]
    assert all(r["offer"] not in r["owns"] for r in nbo["recommendations"])

    alerts = client.get("/api/analytics/predict/usage-alerts").json()
    assert [a["name"] for a in alerts["alerts"]][:1] == ["Customer 02"] and alerts["alerts"][0]["risk"] == "Possible fraud"


def test_ask_in_plain_english(seeded, monkeypatch):
    client, _ = seeded
    from app.crm_core import llm

    monkeypatch.setattr(llm, "resolve", lambda *a, **k: None)
    out = client.post("/api/analytics/ask", json={"text": "Total monthly price of orders by segment for B2B customers as a pie"}).json()
    spec = out["spec"]
    assert spec["entity"] == "order" and spec["measures"] == [{"field": "monthly_price", "agg": "sum"}]
    assert spec["dimensions"] == [{"field": "customer_profile.segment"}] and spec["chart"] == "donut"
    assert {"field": "customer_profile.segment", "op": "contains", "value": "B2B"} in spec["filters"]
    assert out["result"]["rows"][0]["customer_profile.segment"] == "B2B"

    trend = client.post("/api/analytics/ask", json={"text": "How many tickets per month by severity in the last 6 months"}).json()["spec"]
    assert trend["entity"] == "ticket" and trend["dimensions"][0] == {"field": "created_at", "grain": "month"}
    assert {"field": "severity"} in trend["dimensions"] and trend["filters"][0]["op"] == "last_n_days"

    top = client.post("/api/analytics/ask", json={"text": "top 5 customers by outstanding invoices"}).json()
    assert top["spec"]["entity"] == "invoice" and top["spec"]["top"]["n"] == 5 and len(top["result"]["rows"]) <= 5

    # the AI model's spec is used when it holds, and the rules take over when it doesn't
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"entity": "invoice", "dimensions": [{"field": "aging_bucket"}],
                                                            "measures": [{"field": "outstanding", "agg": "sum"}], "chart": "bar", "title": "AR ageing"})
    ai = client.post("/api/analytics/ask", json={"text": "how old is our debt"}).json()
    assert ai["spec"]["dimensions"] == [{"field": "aging_bucket"}] and ai["by"] != "rules"
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"entity": "invoice", "dimensions": [{"field": "no_such_field"}]})
    fallback = client.post("/api/analytics/ask", json={"text": "invoices by status"}).json()
    assert fallback["by"] == "rules" and fallback["spec"]["dimensions"] == [{"field": "status"}]


def test_explain_with_and_without_a_model(seeded, monkeypatch):
    client, _ = seeded
    from app.crm_core import llm

    monkeypatch.setattr(llm, "resolve", lambda *a, **k: None)
    plain = client.post("/api/analytics/explain", json={"title": "Churn", "facts": ["Service churn is 12% over 90 days", "Overdue receivables grew"]}).json()
    assert plain["by"] == "rules" and plain["actions"]
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"headline": "Churn is rising", "insights": ["B2C leads it"], "actions": ["Call them"]})
    ai = client.post("/api/analytics/explain", json={"title": "Churn", "facts": ["Service churn is 12%"]}).json()
    assert ai["headline"] == "Churn is rising" and ai["by"] != "rules"


def test_reports_follow_the_page_and_the_position(seeded):
    client, _ = seeded
    from test_access_guards import _as, _give, _role, _user

    analysts = _role(client, "Analysts", ["analytics", "intent"])
    sales = _role(client, "Sales", ["pipeline", "intent"])
    pos = client.post("/api/admin/positions", json={"name": "Rep Own", "data_scope": "own"}).json()["id"]
    rep, seller = _user(client, "rep"), _user(client, "seller")
    _give(client, rep, [analysts], [pos])
    _give(client, seller, [sales])
    assert client.post("/api/analytics/query", json={"entity": "lead"}, headers=_as(seller)).status_code == 403

    body = client.post("/api/intents", json={"text": "Qualify this lead. company_name: Own Co, source: referral, region: North"}, headers=_as(rep)).json()
    assert body["status"] == "executed", body["rationale"]
    mine = client.post("/api/analytics/query", json={"entity": "lead", "measures": [{"agg": "count"}]}, headers=_as(rep)).json()
    everyone = client.post("/api/analytics/query", json={"entity": "lead", "measures": [{"agg": "count"}]}).json()
    assert mine["totals"]["count"] == 1 and everyone["totals"]["count"] == 31
    # the scorecard and the predictions read through the same scope
    assert client.get("/api/analytics/predict/churn", headers=_as(rep)).json()["customers"] == []
    rows = client.post("/api/analytics/records", json={"entity": "invoice"}, headers=_as(rep)).json()
    assert rows["total"] == 0
