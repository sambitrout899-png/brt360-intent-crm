"""B2C direct order (vanilla): a B2C prospect goes straight to an order -
no lead, opportunity, quote or Presales; the Order Module checks
feasibility; orders come in on any channel and continue on another."""

from app.models.crm import Lead, Opportunity, Quote
from app.models.feasibility import FeasibilityAssessment, FeasibilityCheckType
from app.models.order_mgmt import OmChannelInteraction


def _run(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def _launch(client, name, segment, price=500):
    body = client.post("/api/intents", json={
        "text": f"Launch a new bundle. name: {name}, composition: Fiber500, segment: {segment}, channel: {segment[:3]}, price: {price}, discount: 0"
    }).json()
    assert body["status"] == "executed", body


def _setup(client):
    _launch(client, "Home Fiber 100", "B2C")
    _launch(client, "Fiber 800 Biz", "B2B")
    assert client.post("/api/field-service/setup/sample", json={"timezone": "UTC"}).status_code == 200
    assert client.post("/api/admin/order-management/setup/sample").status_code == 200


def _counts(client):
    db = client.TestSessionLocal()
    try:
        return db.query(Lead).count(), db.query(Opportunity).count(), db.query(Quote).count()
    finally:
        db.close()


def test_b2c_prospect_goes_straight_to_an_order_checked_by_the_order_module(client):
    _setup(client)
    before = _counts(client)
    out = _run(client, "INT-15", name="Priya Sharma", segment="B2C", products=["Home Fiber 100", "Router CPE"],
               order_channel="whatsapp", channel_reference="WA-77812", installation_address="4 Park Street, Kolkata")
    assert out["status"] == "executed", out
    r = out["result"]
    assert r["order_placed"] and r["channel"] == "whatsapp" and r["feasibility"] == "passed"
    assert r["om_status"] in ("booked", "awaiting_shipping")
    areas = {c["area"] for c in r["checks"]}
    assert {"network", "eligibility", "availability"} <= areas  # all from the Order Module
    assert _counts(client) == before  # no lead, no opportunity, no quote
    view = client.get(f"/api/order-management/orders/{r['order_id']}").json()
    assert view["channel"] == "whatsapp" and view["installation_address"] == "4 Park Street, Kolkata"
    assert view["feasibility"]["outcome"] == "passed" and [t["kind"] for t in view["channel_timeline"]] == ["order_started", "order_placed"]
    assert {line["product_name"] for line in view["lines"]} == {"Home Fiber 100", "Router CPE"}
    db = client.TestSessionLocal()
    assessment = db.query(FeasibilityAssessment).filter_by(order_id=r["order_id"]).one()
    assert assessment.opportunity_id is None  # feasibility on the order, not an opportunity
    db.close()
    nxt = [a["code"] for a in out["next_actions"]]
    assert "INT-46" not in nxt and nxt[0] == "INT-25"  # straight on to fulfilment


def test_without_products_the_next_step_is_the_order_never_a_lead(client):
    _setup(client)
    out = _run(client, "INT-15", name="Ravi Kumar", segment="B2C")
    assert out["status"] == "executed"
    nxt = [a["code"] for a in out["next_actions"]]
    assert nxt[0] == "INT-104" and "INT-46" not in nxt
    assert out["next_actions"][0]["entities"]["party_id"] == out["result"]["party_id"]
    b2b = _run(client, "INT-15", name="Acme Works", segment="B2B")
    assert "INT-46" in [a["code"] for a in b2b["next_actions"]]  # B2B keeps its lead path


def test_lead_opportunity_quote_and_presales_are_refused_for_b2c(client):
    _setup(client)
    party = _run(client, "INT-15", name="Home Person", segment="B2C")["result"]["party_id"]
    lead = _run(client, "INT-46", party_id=party, products=["Home Fiber 100"])
    assert lead["status"] == "failed" and "straight to an order" in str(lead)
    # B2B is untouched
    biz = _run(client, "INT-15", name="Wipro India", segment="B2B")["result"]["party_id"]
    assert _run(client, "INT-46", party_id=biz, products=["Fiber 800 Biz"])["status"] == "executed"


def test_switching_the_b2c_lead_path_back_on(client):
    _setup(client)
    assert client.get("/api/admin/order-management/b2c-journey").json()["settings"]["direct_order"] is True
    saved = client.put("/api/admin/order-management/b2c-journey", json={"direct_order": False}).json()
    assert saved["settings"]["direct_order"] is False
    party = _run(client, "INT-15", name="Old Way", segment="B2C")["result"]["party_id"]
    assert _run(client, "INT-46", party_id=party, products=["Home Fiber 100"])["status"] == "executed"
    assert client.put("/api/admin/order-management/b2c-journey", json={"channels": ["fax"]}).status_code == 400


def test_not_feasible_order_is_held_with_the_reason(client):
    _setup(client)
    db = client.TestSessionLocal()
    from app.models.tenant import Tenant

    db.add(FeasibilityCheckType(tenant_id=db.query(Tenant).first().id, name="Fiber Route Availability", resource_type="fiber"))
    db.commit()
    db.close()
    party = _run(client, "INT-41", name="Far Away")["result"]["party_id"]
    out = _run(client, "INT-104", party_id=party, products=["Home Fiber 100"], order_channel="ivr", installation_address="9 NOCOVERAGE Lane")
    assert out["status"] == "executed", out
    assert out["result"]["feasibility"] == "failed" and out["result"]["held"]
    assert "No fiber route" in out["result"]["note"]
    assert [a["code"] for a in out["next_actions"]][:2] == ["INT-60", "INT-61"]
    view = client.get(f"/api/order-management/orders/{out['result']['order_id']}").json()
    assert view["hold"] and view["om_status"] == "pending"


def test_one_order_across_channels(client):
    _setup(client)
    party = _run(client, "INT-41", name="Asha Rao", products=["Home Fiber 100"], order_channel="ussd",
                 installation_address="12 MG Road, Bengaluru")
    assert party["status"] == "executed", party
    order_id = party["result"]["order_id"]
    assert party["result"]["channel"] == "ussd"
    said = client.post("/api/intents", json={"text": f"Customer asked about order {order_id} on WhatsApp"}).json()
    assert said["intent_type"] == "INT-105" and said["status"] == "executed", said
    assert said["result"]["started_on"] == "USSD" and said["result"]["channel"] == "whatsapp"
    store = _run(client, "INT-105", order_id=order_id, order_channel="store", interaction="payment", notes="paid the installation fee")
    assert store["status"] == "executed"
    paid = _run(client, "INT-57", order_id=order_id, payment_method="cash", order_channel="retail_pos", channel_reference="POS-12")
    assert paid["status"] == "executed", paid
    timeline = client.get(f"/api/order-management/orders/{order_id}/channels").json()
    assert [t["channel"] for t in timeline] == ["ussd", "ussd", "whatsapp", "store", "retail_pos"]


def test_b2c_order_from_natural_language_and_channel_validation(client):
    _setup(client)
    party = _run(client, "INT-41", name="Meera Nair")["result"]["party_id"]
    said = client.post("/api/intents", json={"text": f"Place a B2C order for customer {party}: Home Fiber 100 at 7 Lake Road, Kochi via IVR"}).json()
    assert said["intent_type"] == "INT-104", said
    assert said["entities"]["order_channel"] == "ivr" and said["entities"]["installation_address"] == "7 Lake Road, Kochi"
    assert said["status"] == "executed", said
    biz = _run(client, "INT-15", name="Big Co", segment="B2B")["result"]["party_id"]
    refused = _run(client, "INT-104", party_id=biz, products=["Fiber 800 Biz"], order_channel="store", installation_address="1 Main St")
    assert refused["status"] == "failed" and "not a B2C customer" in str(refused)
    bad = _run(client, "INT-104", party_id=party, products=["Home Fiber 100"], order_channel="carrier pigeon", installation_address="1 Main St")
    assert bad["status"] == "failed" and "Which channel" in str(bad)
    db = client.TestSessionLocal()
    assert db.query(OmChannelInteraction).filter_by(channel="ivr").count() == 2  # started + placed
    db.close()


def test_customer_360_offers_the_order_not_a_lead_for_b2c(client):
    _setup(client)
    party = _run(client, "INT-15", name="Test Company1", segment="B2C")["result"]["party_id"]
    o = client.get(f"/api/customers/{party}/overview").json()
    assert [s["label"] for s in o["journey"]["steps"]] == ["Prospect", "Order placed", "Feasible", "Booked", "In fulfilment", "Live"]
    codes = [a["code"] for a in o["next_actions"]]
    assert codes[0] == "INT-104" and "INT-46" not in codes
    _run(client, "INT-104", party_id=party, products=["Home Fiber 100"], order_channel="sms", installation_address="5 Lake View, Pune")
    o = client.get(f"/api/customers/{party}/overview").json()
    reached = [s["label"] for s in o["journey"]["steps"] if s["reached"]]
    assert reached == ["Prospect", "Order placed", "Feasible", "Booked"] and o["next_actions"][0]["code"] == "INT-25"
    assert any(e["title"].startswith("SMS:") for e in o["timeline"])
    biz = _run(client, "INT-15", name="Acme B2B", segment="B2B")["result"]["party_id"]
    b = client.get(f"/api/customers/{biz}/overview").json()
    assert "Lead" in [s["label"] for s in b["journey"]["steps"]] and "INT-46" in [a["code"] for a in b["next_actions"]]


def test_vanilla_b2c_process_has_no_lead_or_quote_steps(client):
    from app.orchestration.function_map import PROCESSES

    b2c = next(p for p in PROCESSES if p["key"] == "b2c")["steps"]
    assert "INT-104" in b2c and not {"INT-46", "INT-17", "INT-05", "INT-38", "INT-18"} & set(b2c)


def _follow(client, body, order_id, hops=8):
    """The guided next steps after an order (approve, install, test, accept), as the result screen offers them."""
    for _ in range(hops):
        options = body.get("next_actions") or []
        if not options:
            return body
        nxt = options[0]
        fill = {"order_id": order_id, "decision": "approve", "document_ref": "ACCEPT.pdf", "signature": "Customer", **(nxt.get("entities") or {})}
        body = _run(client, nxt["code"], **fill)
        assert body["status"] == "executed", body
    return body


def _assets(client, party):
    import json

    from app.models.accounts import ServiceAccount
    from app.models.fulfillment import Asset

    db = client.TestSessionLocal()
    try:
        return sorted((json.loads(a.instance_json or "{}").get("offering_name"), a.status.value, json.loads(a.instance_json or "{}").get("net_unit_price"))
                      for a in db.query(Asset).join(ServiceAccount, ServiceAccount.id == Asset.service_account_id)
                      .filter(ServiceAccount.party_id == party, Asset.superseded_by_asset_id.is_(None)))
    finally:
        db.close()


def test_every_service_of_a_b2c_order_is_installed_billed_and_suspended_together(client):
    """A fibre plan with an add-on: both installed as services, both priced and billed, the order's lines closed on
    fulfilment, and suspend / resume act on the whole subscription."""
    _setup(client)
    _launch(client, "Mesh Wi-Fi Plus", "B2C", price=199)
    party = _run(client, "INT-41", name="Two Services")["result"]["party_id"]
    placed = _run(client, "INT-104", party_id=party, products=["Home Fiber 100", "Mesh Wi-Fi Plus"], order_channel="web",
                  installation_address="12 MG Road, Bengaluru", payment_method="upi")
    assert placed["status"] == "executed", placed
    order = placed["result"]["order_id"]
    assert placed["result"]["payment"]["status"] == "authorized"  # UPI: an instant payment
    _follow(client, placed, order)
    assert _assets(client, party) == [("Home Fiber 100", "active", 500.0), ("Mesh Wi-Fi Plus", "active", 199.0)]
    view = client.get(f"/api/order-management/orders/{order}").json()
    assert view["status"] == "completed" and {l["status"] for l in view["lines"] if l["main"]} == {"complete"}
    billed = _run(client, "INT-84", party_id=party)["result"]["invoice"]["lines"]
    assert all(any(name in l["description"] for l in billed) for name in ("Home Fiber 100", "Mesh Wi-Fi Plus")), billed
    assert _run(client, "INT-85", party_id=party, payment_method="upi", reference="UPI-1")["status"] == "executed"
    assert _run(client, "INT-09", order_id=order, action="suspend")["status"] == "executed"
    assert {s for _, s, _ in _assets(client, party)} == {"suspended"}
    assert _run(client, "INT-70", order_id=order)["status"] == "executed"
    assert {s for _, s, _ in _assets(client, party)} == {"active"}


def test_an_asset_change_offers_to_confirm_its_fulfilment(client):
    _setup(client)
    _launch(client, "Home Fiber 300", "B2C", price=900)
    party = _run(client, "INT-41", name="Upgrader")["result"]["party_id"]
    placed = _run(client, "INT-104", party_id=party, products=["Home Fiber 100"], order_channel="web", installation_address="1 Lake Road")
    _follow(client, placed, placed["result"]["order_id"])
    from app.models.accounts import ServiceAccount
    from app.models.fulfillment import Asset

    db = client.TestSessionLocal()
    asset_id = db.query(Asset.id).join(ServiceAccount, ServiceAccount.id == Asset.service_account_id).filter(ServiceAccount.party_id == party).scalar()
    db.close()
    change = _run(client, "INT-107", asset_id=asset_id, asset_operation="modify", offering_name="Home Fiber 300")
    assert change["status"] == "executed" and change["next_actions"][0]["code"] == "INT-108"
    done = _run(client, "INT-108", **{"order_id": change["result"]["order_id"], **change["next_actions"][0]["entities"]})
    assert done["status"] == "executed" and ("Home Fiber 300", "active", 900.0) in _assets(client, party)


def test_a_mobile_plan_on_a_b2c_order_is_activated_not_installed(client):
    _setup(client)
    assert client.post("/api/mobile/sample-catalog").status_code == 200
    party = _run(client, "INT-41", name="Mobile Buyer")["result"]["party_id"]
    out = _run(client, "INT-104", party_id=party, products=["Postpaid 399"], order_channel="store", installation_address="2 Hill Road")
    assert out["status"] == "executed", out
    r = out["result"]
    assert r["mobile_only"] and r["msisdn"] and r["payment_type"] == "postpaid"
    from app.models.assurance import WorkOrder

    db = client.TestSessionLocal()
    assert db.query(WorkOrder).filter(WorkOrder.order_id.in_([o["order_id"] for o in r["orders"]])).count() == 0  # no installation visit
    db.close()
    view = client.get(f"/api/order-management/orders/{r['orders'][0]['order_id']}").json()
    assert view["status"] == "completed" and view["om_status"] == "fulfilled"  # the Order Module sees the activation done
    codes = [a["code"] for a in out["next_actions"]]
    assert "INT-25" not in codes and "INT-130" in codes
    if r["lines"][0]["status"] == "pending":  # no SIM in stock: handing one over is the first step offered
        assert codes[0] == "INT-137"
    prepaid = _run(client, "INT-104", party_id=party, products=["Prepaid Smart 28"], order_channel="store", installation_address="2 Hill Road")
    assert prepaid["status"] == "failed" and "Activate mobile lines" in str(prepaid)


def test_indian_identity_documents_are_accepted(client):
    party = _run(client, "INT-41", name="KYC Person")["result"]["party_id"]
    for kind in ("aadhaar", "PAN", "driving licence", "voter_id"):
        assert _run(client, "INT-13", party_id=party, id_number="123412341234", id_type=kind)["status"] == "executed", kind
    assert "Unsupported identity document type" in str(_run(client, "INT-13", party_id=party, id_number="1", id_type="library card"))


def test_b2c_only_prospect_fields_are_hidden_for_b2b(client):
    """The Add prospect form shows the direct-order fields only for a B2C prospect,
    and a B2B prospect never places an order even if such values arrive."""
    from app.intent_engine import intent_registry

    fields = {f["key"]: f for f in intent_registry._builtin_entry(intent_registry.BUILTIN["INT-15"], None)["fields"]}
    for key in ("products", "order_channel", "installation_address"):
        assert fields[key]["show_when"] == {"field": "segment", "in": ["B2C"]}
    assert fields["name"]["show_when"] == {} and fields["segment"]["show_when"] == {}
    out = _run(client, "INT-15", name="Fieldcheck B2B", segment="B2B", products=["Home Fiber 100"], order_channel="store")
    assert out["status"] == "executed" and not out["result"].get("order_id")
