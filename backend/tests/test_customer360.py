"""Configurable Customer 360: layouts, the Configure 360 agent and intent
(INT-33), external systems (demo + REST with schema mapping), undo."""

import pytest

from app.auth import hash_password
from app.c360 import agent, catalog, connectors
from app.models.auth import User, UserRole
from app.models.tenant import Tenant
from app.tenancy import DEFAULT_TENANT_SLUG


def _structured(client, code, entities, headers=None):
    resp = client.post("/api/intents", json={"text": code, "structured": {"intent_type": code, "entities": entities}}, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _customer(client, name="Acme 360"):
    launch = client.post("/api/intents", json={
        "text": "Launch a new bundle. name: Fiber 800, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 1000, discount: 0"})
    assert launch.json()["status"] == "executed", launch.text
    lead =_structured(client, "INT-04", {"company_name": name, "source": "referral", "offering_name": "Fiber 800", "quantity": 2})
    assert lead["status"] == "executed", lead["rationale"]
    quote = _structured(client, "INT-05", {"opportunity_id": lead["result"]["opportunity_id"]})
    assert quote["status"] == "executed", quote["rationale"]
    party = next(p for p in client.get("/api/parties").json() if p["name"] == name)
    return party["id"]


def _agent_user(client):
    db = client.TestSessionLocal()
    try:
        tenant_id = db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
        user = User(tenant_id=tenant_id, email="rep@c360.test", password_hash=hash_password("x" * 8), name="Rep", role=UserRole.agent)
        db.add(user)
        db.commit()
        return {"X-Test-User-Id": str(user.id)}
    finally:
        db.close()


def _widgets(view):
    return {w["recipe"] or w["title"]: w for p in view["panes"] for w in p["widgets"]}


def test_customer_audience_matrix_live_preview_and_history(client):
    from app.models.crm import Party, PartyProfile

    customers = []
    with client.TestSessionLocal() as db:
        tenant_id = db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
        for status in ("account", "prospect"):
            for segment in ("B2B", "B2C"):
                # Organization deliberately used for B2C too: the profile is authoritative.
                party = Party(tenant_id=tenant_id, name=f"{status} {segment}", party_type="organization",
                              profile=PartyProfile(account_status=status, segment=segment))
                db.add(party)
                db.flush()
                customers.append((party.id, status, segment))
        db.commit()

    def audience(types=None, segments=None):
        return catalog.normalize_audience({"customer_types": types, "segments": segments})

    layout = catalog.normalize_layout({"panes": [{"id": "shared", "domain": "custom", "widgets": [
        {"id": "common", "recipe": "sales.mrr"},
        {"id": "account", "recipe": "sales.mrr", "audience": audience(["account"])},
    ]}, {"id": "consumer", "domain": "custom", "audience": audience(segments=["B2C"]), "widgets": [
        {"id": "consumer-prospect", "recipe": "sales.mrr", "audience": audience(["prospect"])},
    ]}], "header": {"kpis": ["sales.mrr"], "show_health": True}, "component_audiences": {
        "orders": audience(["account"]), "service_accounts": audience(segments=["B2B"]),
        "health": audience(["account"]), "kpi:sales.mrr": audience(segments=["B2B"]),
    }})
    saved = client.put("/api/admin/c360/layout", json={"layout": layout})
    assert saved.status_code == 200, saved.text
    for party_id, status, segment in customers:
        preview = client.post(f"/api/admin/c360/preview/{party_id}", json={"layout": layout})
        live = client.get(f"/api/c360/parties/{party_id}/view")
        assert preview.status_code == live.status_code == 200
        expected = {"common"} | ({"account"} if status == "account" else set())
        if status == "prospect" and segment == "B2C":
            expected.add("consumer-prospect")
        for view in (preview.json(), live.json()):
            assert view["audience"] == {"customer_type": status, "segment": segment}
            assert {w["id"] for p in view["panes"] for w in p["widgets"]} == expected
            assert all(p["widgets"] for p in view["panes"])
            assert view["components"]["orders"] is (status == "account")
            assert view["components"]["service_accounts"] is (segment == "B2B")
            assert bool(view["header"]["kpis"]) is (segment == "B2B")
            assert (view["header"]["health"] is not None) is (status == "account")
            assert view["layout"] == layout  # rendering must not destroy the authoring layout

    # Audience changes survive publish, undo and explicit version restoration.
    changed = {**layout, "component_audiences": {"orders": {"enabled": False}}}
    assert client.put("/api/admin/c360/layout", json={"layout": changed}).status_code == 200
    undone = client.post("/api/admin/c360/undo").json()
    assert undone["layout"] == layout
    assert client.get("/api/admin/c360").json()["layout"] == layout
    assert client.post("/api/admin/c360/restore/2").json()["layout"] == catalog.normalize_layout(changed)
    assert client.post("/api/admin/c360/restore/1").json()["layout"] == layout

    # Reclassifying the same customer must change the rendered audience on reload.
    party_id = customers[-1][0]
    with client.TestSessionLocal() as db:
        profile = db.get(Party, party_id).profile
        profile.account_status, profile.segment = "account", "B2B"
        db.commit()
    refreshed = client.get(f"/api/c360/parties/{party_id}/view").json()
    assert refreshed["audience"] == {"customer_type": "account", "segment": "B2B"}
    assert refreshed["components"]["orders"] and refreshed["components"]["service_accounts"]


def test_audience_normalization_legacy_unknown_segment_and_disabled_data_fetch(client, monkeypatch):
    from app.c360 import data
    from app.models.crm import Party

    context = {"customer_type": "account", "segment": None}
    assert catalog.audience_matches(None, context)  # old layouts include everyone
    assert not catalog.audience_matches({"segments": ["B2C"]}, context)
    assert not catalog.audience_matches({"segments": []}, context)
    assert not catalog.audience_matches({"segments": "B2B"}, context)
    assert not catalog.audience_matches({"enabled": False}, context)
    assert catalog.normalize_audience({"segments": ["B2C", "bad", "B2C"]})["segments"] == ["B2C"]
    assert catalog.normalize_layout({"component_audiences": "invalid"})["component_audiences"] == {}

    with client.TestSessionLocal() as db:
        tenant_id = db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
        party = Party(tenant_id=tenant_id, name="Unclassified 360", party_type="prospect")
        db.add(party)
        db.flush()
        layout = catalog.normalize_layout({"header": {"kpis": [], "show_health": False},
            "component_audiences": {"insights": {"enabled": False}},
            "panes": [{"id": "excluded", "audience": {"segments": ["B2C"]}, "widgets": [{"recipe": "billing.revenue_trend"}]},
                      {"id": "excluded-widget", "widgets": [{"recipe": "billing.revenue_trend", "audience": {"enabled": False}}]}]})
        def no_load(*args, **kwargs):
            pytest.fail("Excluded widget attempted to load data")
        monkeypatch.setattr(data.Sources, "load", no_load)
        view = data.build_view(db, tenant_id, None, party, layout, [])
        assert view["panes"] == [] and view["sources"] == []
        assert view["audience"] == {"customer_type": "prospect", "segment": None}


def test_configure_agent_audience_edits_preserve_existing_rules():
    layout = catalog.normalize_layout({"panes": [{"id": "pane", "widgets": [{"id": "widget", "recipe": "sales.mrr"}]}]})
    account_b2b = catalog.normalize_audience({"customer_types": ["account"], "segments": ["B2B"]})
    result = agent.apply_ops(layout, [
        {"op": "update_pane", "pane": "pane", "audience": account_b2b},
        {"op": "update_widget", "widget": "widget", "audience": {"customer_types": ["account"]}},
        {"op": "set_component_audience", "component": "billing", "audience": account_b2b},
        {"op": "set_theme", "preset": "aurora"},
    ], [])
    assert result["layout"]["panes"][0]["audience"] == account_b2b
    assert result["layout"]["panes"][0]["widgets"][0]["audience"]["customer_types"] == ["account"]
    assert result["layout"]["component_audiences"]["billing"] == account_b2b
    assert layout["panes"][0]["audience"] != account_b2b  # source draft unchanged


def test_default_view_computes_internal_widgets_and_flags_missing_systems(client):
    party_id = _customer(client)
    view = client.get(f"/api/c360/parties/{party_id}/view").json()
    assert view["version"] == 0 and view["party"]["name"] == "Acme 360"
    widgets = _widgets(view)
    stages = widgets["sales.pipeline_by_stage"]
    assert stages["status"] == "ok" and stages["source_label"] == "BRT 360"
    assert stages["data"]["kind"] == "categories" and stages["data"]["total"] == 1
    assert widgets["billing.revenue_trend"]["status"] == "not_connected"
    kpis = {k["recipe"]: k for k in view["header"]["kpis"]}
    assert kpis["sales.pipeline_value"]["data"]["value"] > 0
    assert any(i["action"] and "quote_id" in i["action"]["text"] for i in view["insights"])  # draft quote -> approve it
    assert view["header"]["health"]["score"] <= 100


def test_agent_previews_then_studio_saves_with_demo_systems(client):
    party_id = _customer(client)
    plan = client.post("/api/admin/c360/agent", json={"instruction": "connect all the respective systems and make it compact with Vi red"}).json()
    assert plan["understood"] and {c["domain"] for c in plan["connectors"]} == {"billing", "service", "network", "inventory"}
    assert plan["layout"]["theme"] == {**plan["layout"]["theme"], "accent": "#e60000", "density": "compact"}
    assert client.get("/api/admin/c360/connectors").json() == []  # nothing saved by a preview

    preview = client.post(f"/api/admin/c360/preview/{party_id}", json={"layout": plan["layout"], "connectors": plan["connectors"]}).json()
    revenue = _widgets(preview)["billing.revenue_trend"]
    assert revenue["status"] == "ok" and revenue["source_label"] == "Billing system (demo)"
    assert len(revenue["data"]["points"]) == 12

    saved = client.put("/api/admin/c360/layout", json={"layout": plan["layout"], "connectors": plan["connectors"], "summary": "demo"}).json()
    assert saved["version"] == 1 and len(saved["connectors_created"]) == 4
    view = client.get(f"/api/c360/parties/{party_id}/view").json()
    elements = _widgets(view)["network.elements_table"]  # the connected (demo) network system answers network health
    assert elements["status"] == "ok" and elements["source_label"] == "Network OSS (demo)" and elements["data"]["rows"]
    # the same customer always tells the same story
    again = client.get(f"/api/c360/parties/{party_id}/view").json()
    assert _widgets(again)["billing.revenue_trend"]["data"] == _widgets(view)["billing.revenue_trend"]["data"]


def test_configure_360_intent_from_plain_words_then_undo(client):
    ran = client.post("/api/intents", json={"text": "Configure 360: add usage trend to the billing pane and switch to a light theme"}).json()
    assert ran["intent_type"] == "INT-33" and ran["status"] == "executed", ran["rationale"]
    assert ran["result"]["c360_version"] == 1
    layout = client.get("/api/c360/layout").json()
    assert layout["layout"]["theme"]["preset"] == "daylight"
    billing = next(p for p in layout["layout"]["panes"] if p["domain"] == "billing")
    assert any(w["recipe"] == "billing.usage_trend" for w in billing["widgets"])

    undone = client.post("/api/admin/c360/undo").json()
    assert undone["version"] == 2 and undone["layout"]["theme"]["preset"] == "midnight"
    history = client.get("/api/admin/c360").json()["history"]
    assert [h["version"] for h in history] == [2, 1]


def test_configure_360_is_admin_only(client):
    rep = _agent_user(client)
    codes = {i["code"] for i in client.get("/api/intents/catalog", headers=rep).json()["intents"]}
    assert "INT-33" not in codes and "INT-33" in {i["code"] for i in client.get("/api/intents/catalog").json()["intents"]}
    denied = _structured(client, "INT-33", {"instruction": "remove the sales pane"}, rep)
    assert denied["status"] == "failed" and "administrator" in denied["rationale"]
    assert client.get("/api/admin/c360", headers=rep).status_code == 403
    assert client.get("/api/c360/layout", headers=rep).status_code == 200  # but everyone sees the page


def test_rest_system_with_its_own_schema_is_mapped_onto_ours(client, monkeypatch):
    party_id = _customer(client)
    calls = []

    def fake_get(url, headers):
        calls.append((url, headers))
        return {"meta": {"count": 2}, "data": {"items": [
            {"InvoiceNo": "A-1", "BillDate": "2026-08-01", "TotalAmount": 1200.5, "PaymentStatus": "paid"},
            {"InvoiceNo": "A-2", "BillDate": "2026-09-01", "TotalAmount": 1300, "PaymentStatus": "overdue"},
        ]}}

    monkeypatch.setattr(connectors, "http_get", fake_get)
    created = client.post("/api/admin/c360/connectors", json={
        "name": "Vi BSS", "domain": "billing", "kind": "rest", "base_url": "http://127.0.0.1:9", "auth_type": "bearer", "secret": "s3cr3t-token",
        "endpoints": {"invoices": {"path": "/v2/accounts/{party_id}/bills"}},
    }).json()
    assert created["key"] == "vi-bss" and created["secret_set"] and "s3cr3t" not in str(created)

    tested = client.post(f"/api/admin/c360/connectors/{created['id']}/test", json={"party_id": party_id}).json()
    assert tested["ok"] and tested["records_found"] == 2
    url, headers = calls[-1]
    assert url == f"http://127.0.0.1:9/v2/accounts/{party_id}/bills" and headers["Authorization"] == "Bearer s3cr3t-token"
    suggestion = tested["suggestion"]
    assert suggestion["records_path"] == "data.items"
    assert suggestion["field_map"] == {"invoice_id": "InvoiceNo", "period": "BillDate", "amount": "TotalAmount", "status": "PaymentStatus"}

    client.put(f"/api/admin/c360/connectors/{created['id']}", json={
        "name": "Vi BSS", "domain": "billing", "kind": "rest", "base_url": "http://127.0.0.1:9", "auth_type": "bearer",
        "endpoints": {"invoices": {"path": "/v2/accounts/{party_id}/bills", "records_path": "data.items", "field_map": suggestion["field_map"]}},
    })
    view = client.get(f"/api/c360/parties/{party_id}/view").json()
    revenue = _widgets(view)["billing.revenue_trend"]
    assert revenue["source_label"] == "Vi BSS" and revenue["data"]["total"] == 2500.5
    assert any("overdue" in i["title"] for i in view["insights"])
    assert calls[-1][1]["Authorization"] == "Bearer s3cr3t-token"  # secret kept when not resent

    def broken(url, headers):
        raise connectors.ConnectorError("The system answered 503 Service Unavailable")

    monkeypatch.setattr(connectors, "http_get", broken)
    view = client.get(f"/api/c360/parties/{party_id}/view").json()
    revenue = _widgets(view)["billing.revenue_trend"]
    assert revenue["status"] == "error" and "503" in revenue["message"]
    assert _widgets(view)["sales.pipeline_by_stage"]["status"] == "ok"  # one broken system never breaks the page


def test_mapping_from_a_pasted_sample(client):
    resp = client.post("/api/admin/c360/connectors/map", json={"dataset": "network_health", "sample": '{"nodes": [{"hostname": "PE-1", '
                       '"location": "Mumbai", "operState": "up", "cpu": 41, "rtt": 12, "uptime": 99.98}]}'}).json()
    assert resp["records_path"] == "nodes"
    assert resp["field_map"]["element"] == "hostname" and resp["field_map"]["site"] == "location"
    assert resp["field_map"]["latency_ms"] == "rtt" and resp["field_map"]["availability_pct"] == "uptime"
    assert client.post("/api/admin/c360/connectors/map", json={"dataset": "invoices", "sample": "{not json"}).status_code == 400


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://169.254.169.254/latest/meta-data", "ftp://example.com/x"])
def test_unsafe_addresses_are_refused(url):
    with pytest.raises(connectors.ConnectorError):
        connectors.check_url(url)


def test_layout_validation_drops_anything_malformed():
    clean = catalog.normalize_layout({
        "theme": {"preset": "nope", "accent": "red; background:url(x)"},
        "panes": [{"title": "<b>x</b>" * 50, "domain": "billing", "width": "huge",
                   "widgets": [{"type": "script"}, {"recipe": "billing.revenue_trend", "source": "../../etc"}, "junk"]}] * 20,
    })
    assert clean["theme"]["preset"] == "midnight" and clean["theme"]["accent"] is None
    assert len(clean["panes"]) == catalog.MAX_PANES and len({p["id"] for p in clean["panes"]}) == catalog.MAX_PANES
    pane = clean["panes"][0]
    assert pane["width"] == "half" and len(pane["title"]) <= 60
    assert [w["recipe"] for w in pane["widgets"]] == ["billing.revenue_trend"] and pane["widgets"][0]["source"] is None


def test_agent_understands_everyday_requests():
    lay = catalog.default_layout()
    cases = {
        "show tickets by priority as a bar chart": "Tickets by priority is now a bar",
        "move billing to the top and make it full width": "Moved Billing to position 1",
        "rename the network pane to Network & NOC": "Renamed Network to Network & NOC",
        "remove the inventory pane": "Removed the Inventory pane",
        "use the customer care layout": "Customer care",
        "connect our network system at https://oss.vi.example.com/api/v1": "Connected Network system at https://oss.vi.example.com/api/v1",
    }
    for text, expected in cases.items():
        result = agent.plan(text, lay, [])
        assert any(expected in c for c in result["changes"]), (text, result["changes"], result["notes"])
    unclear = agent.plan("blah blah", lay, [])
    assert not unclear["understood"] and unclear["layout"] == lay


def test_pane_tab_placement_is_optional_and_validated(client):
    """A pane may name the page tab it sits on; unknown tabs fall back to the
    tab of its domain (stored as None), and older layouts stay unchanged."""
    layout = catalog.normalize_layout({"panes": [
        {"id": "p1", "domain": "sales", "tab": "orders", "widgets": [{"recipe": "sales.records"}]},
        {"id": "p2", "domain": "billing", "tab": "nowhere", "widgets": [{"recipe": "billing.balance"}]},
        {"id": "p3", "domain": "service", "widgets": [{"recipe": "service.records"}]},
    ]})
    assert [p["tab"] for p in layout["panes"]] == ["orders", None, None]
    assert "tab=orders" in catalog.describe(layout)
    assert "orders" in catalog.public_catalog()["pane_tabs"]

    party_id = _customer(client, "Tabbed 360")
    saved = client.put("/api/admin/c360/layout", json={"layout": layout})
    assert saved.status_code == 200, saved.text
    view = client.get(f"/api/c360/parties/{party_id}/view").json()
    assert {p["id"]: p["tab"] for p in view["panes"]}["p1"] == "orders"
