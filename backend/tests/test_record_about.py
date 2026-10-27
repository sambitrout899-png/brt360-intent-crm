"""About this record: everything about a customer, lead, opportunity, quote or order in one call."""

from app.models.auth import User, UserRole
from app.models.tenant import Tenant


def _run(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def _launch(client, name, segment, price=500):
    body = client.post("/api/intents", json={
        "text": f"Launch a new bundle. name: {name}, composition: Fiber500, segment: {segment}, channel: {segment[:3]}, price: {price}, discount: 0"
    }).json()
    assert body["status"] == "executed", body


def _b2b_chain(client):
    _launch(client, "Fiber 800 Biz", "B2B-SMB", 1000)
    party = _run(client, "INT-15", name="Acme Works", segment="B2B")["result"]["party_id"]
    lead = _run(client, "INT-46", party_id=party, products=["Fiber 800 Biz"])["result"]["lead_id"]
    opp = _run(client, "INT-17", lead_id=lead, decision="approve")["result"]["opportunity_id"]
    quote = client.post("/api/intents", json={"text": f"Configure and quote this bundle. opportunity_id: {opp}, offering_name: Fiber 800 Biz"}).json()
    quote_id = quote["result"]["quote_id"]
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}"})
    order = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"}).json()
    return party, lead, opp, quote_id, order["result"]["order_id"]


def test_about_every_record_type_shows_its_family(client):
    party, lead, opp, quote, order = _b2b_chain(client)
    for entity_type, record_id, must_relate in [("party", party, {"lead", "opportunity", "quote", "order"}),
                                                 ("lead", lead, {"party", "opportunity", "quote", "order"}),
                                                 ("opportunity", opp, {"party", "lead", "quote", "order"}),
                                                 ("quote", quote, {"party", "lead", "opportunity", "order"}),
                                                 ("order", order, {"party", "lead", "opportunity", "quote"})]:
        r = client.get(f"/api/records/{entity_type}/{record_id}/about")
        assert r.status_code == 200, (entity_type, r.text)
        a = r.json()
        assert a["header"]["entity_type"] == entity_type and a["header"]["brt_id"]
        assert {x["entity_type"] for x in a["related"]} >= must_relate, (entity_type, a["related"])
        assert a["fields"] and a["summary"] and a["timeline"]
        assert all(not (x["entity_type"] == entity_type and x["entity_id"] == record_id) for x in a["related"])
    a = client.get(f"/api/records/order/{order}/about").json()
    keys = {s["key"] for s in a["sections"]}
    assert {"lines", "sales_order", "fulfilment"} <= keys
    assert any(e["title"] for e in a["timeline"] if e["icon"] == "⚡")  # the intents that touched it
    assert a["header"]["customer"]["id"] == party


def test_about_customer_counts_the_service_a_completed_order_installed(client):
    party, _, _, _, order = _b2b_chain(client)
    assert client.get(f"/api/orders/{order}").json()["status"] == "completed"
    summary = {s["label"]: s["value"] for s in client.get(f"/api/records/party/{party}/about").json()["summary"]}
    # the service lives on as an asset - it is live and billing, though its order is completed
    assert summary["Live services"] == 1 and summary["Monthly revenue"] == 1000


def test_about_b2c_order_shows_channels_and_order_module_feasibility(client):
    _launch(client, "Home Fiber 100", "B2C")
    out = _run(client, "INT-15", name="Priya Sharma", segment="B2C", products=["Home Fiber 100"], order_channel="whatsapp",
               installation_address="4 Park Street, Kolkata")
    order = out["result"]["order_id"]
    a = client.get(f"/api/records/order/{order}/about").json()
    keys = {s["key"] for s in a["sections"]}
    assert {"channels", "feasibility", "lines"} <= keys
    assert any(e["icon"] == "📲" for e in a["timeline"])
    p = client.get(f"/api/records/party/{out['result'].get('party_id') or a['header']['customer']['id']}/about").json()
    assert any(x["entity_type"] == "order" for x in p["related"])


def test_about_respects_tenant_and_unknown_records(client):
    party, *_ = _b2b_chain(client)
    with client.TestSessionLocal() as db:
        other = Tenant(name="Other", slug="other-about")
        db.add(other)
        db.flush()
        outsider = User(tenant_id=other.id, name="Out", email="out@about.test", password_hash="x", role=UserRole.admin)
        db.add(outsider)
        db.commit()
        oid = outsider.id
    assert client.get(f"/api/records/party/{party}/about", headers={"X-Test-User-Id": str(oid)}).status_code == 404
    assert client.get("/api/records/order/999999/about").status_code == 404
    assert client.get("/api/records/spaceship/1/about").status_code == 404
