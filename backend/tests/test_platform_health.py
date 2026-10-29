"""Analytics -> Platform health: the Grafana dashboards' numbers from Prometheus
(app/analytics/platform_health.py), for the operator's admins only."""

import httpx

from app.analytics import platform_health
from app.config import get_settings
from app.models.auth import User, UserRole
from app.models.tenant import Tenant


class _Resp:
    def __init__(self, body, status_code=200):
        self._body, self.status_code = body, status_code

    def json(self):
        return self._body


def _fake_prometheus(url, params, timeout):
    q = params["query"]
    if url.endswith("/query_range"):
        stamps = range(int(params["start"]), int(params["end"]) + 1, int(params["step"]))
        return _Resp({"status": "success", "data": {"resultType": "matrix", "result": [
            {"metric": {"job": "brt360-api", "class": "2xx", "route": "/api/x", "outcome": "ok"}, "values": [[t, "0.25"] for t in stamps]}]}})
    if q.startswith("up{"):
        result = [{"metric": {"job": "brt360-api"}, "value": [0, "1"]}, {"metric": {"job": "brt360-bim"}, "value": [0, "0"]}]
    elif "brt360_ai_tokens_total" in q:
        result = [{"metric": {"tenant": "1"}, "value": [0, "1200"]}]
    elif 'status=~"5.."}[1h])) > 0' in q:
        result = [{"metric": {"method": "GET", "route": "/api/broken", "status": "500"}, "value": [0, "3"]}]
    elif "histogram_quantile" in q:
        result = [{"metric": {"job": "brt360-api"} if "job" in q else {}, "value": [0, "0.12"]}]
    elif "probe_success" in q:
        result = [{"metric": {}, "value": [0, "100" if "avg_over_time" in q else "1"]}]
    else:
        result = [{"metric": {"job": "brt360-api", "route": "/api/x"}, "value": [0, "5"]}]
    return _Resp({"status": "success", "data": {"resultType": "vector", "result": result}})


def test_operators_admin_sees_the_dashboards(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "prometheus_url", "http://prometheus:9090")
    monkeypatch.setattr(platform_health.httpx, "get", _fake_prometheus)
    assert client.get("/api/analytics/catalog").json()["platform_health"] is True

    out = client.get("/api/analytics/platform-health?hours=5").json()
    assert out["available"] is True and out["hours"] == 6  # snapped to a range the tab offers
    services = {s["name"]: s for s in out["services"]}
    assert services["Core"]["up"] is True and services["Core"]["p95_ms"] == 120
    assert services["BIM"]["up"] is False
    kpis = {k["key"]: k for k in out["kpis"]}
    assert kpis["services_up"]["value"] == 1 and kpis["services_up"]["tone"] == "bad" and "BIM" in kpis["services_up"]["hint"]
    assert kpis["p95"]["value"] == 120 and kpis["p95"]["fmt"] == "ms"
    assert any("BIM" in n["text"] for n in out["notes"])
    assert out["failing_routes"] == [{"method": "GET", "route": "/api/broken", "status": "500", "count": 3}]
    db = client.TestSessionLocal()
    assert out["ai_tokens"] == [{"tenant": db.get(Tenant, 1).name, "tokens": 1200}]  # the id becomes the name
    db.close()
    chart = next(c for c in out["charts"] if c["key"] == "p95")
    assert chart["series"][0]["name"] == "Core" and chart["series"][0]["values"][0] == 250  # seconds -> ms
    assert len(chart["labels"]) == len(chart["series"][0]["values"]) == 6 * 3600 // 300 + 1


def test_without_monitoring_the_tab_says_so(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "prometheus_url", "")
    out = client.get("/api/analytics/platform-health").json()
    assert out["available"] is False and "PROMETHEUS_URL" in out["reason"]

    def down(*a, **k):
        raise httpx.ConnectError("no such host")

    monkeypatch.setattr(get_settings(), "prometheus_url", "http://prometheus:9090")
    monkeypatch.setattr(platform_health.httpx, "get", down)
    out = client.get("/api/analytics/platform-health").json()
    assert out["available"] is False and "--profile monitoring" in out["reason"]


def test_other_organizations_and_non_admins_cannot_see_it(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "prometheus_url", "http://prometheus:9090")
    monkeypatch.setattr(platform_health.httpx, "get", _fake_prometheus)
    db = client.TestSessionLocal()
    default = db.query(Tenant).filter_by(slug="default").one()
    other = Tenant(name="Customer Org", slug="customer-org")
    db.add(other)
    db.flush()
    other_admin = User(tenant_id=other.id, email="admin@customer.test", name="Their Admin", password_hash="x", role=UserRole.admin)
    agent = User(tenant_id=default.id, email="agent@default.test", name="Agent", password_hash="x", role=UserRole.agent)
    db.add_all([other_admin, agent])
    db.commit()
    ids = [other_admin.id, agent.id]
    db.close()
    for uid in ids:
        h = {"X-Test-User-Id": str(uid)}
        cat = client.get("/api/analytics/catalog", headers=h)
        if cat.status_code == 200:  # the agent may not hold the Analytics page at all
            assert cat.json()["platform_health"] is False
        assert client.get("/api/analytics/platform-health", headers=h).status_code == 403

    monkeypatch.setattr(get_settings(), "platform_health_tenants", "default,customer-org")
    assert client.get("/api/analytics/platform-health", headers={"X-Test-User-Id": str(ids[0])}).status_code == 200
