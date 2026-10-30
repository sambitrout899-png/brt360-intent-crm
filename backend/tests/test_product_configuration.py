"""Product configuration: design-time attributes (pricing + technical),
price impacts and rules; runtime selection on the opportunity and quote;
technical attributes carried to the order, work orders, provisioning and
inventory; pricelists with quote > opportunity > customer > default."""

import json

from test_sales_to_order import _launch, _opportunity, run, say

DESIGN = (
    "Bandwidth: 100 Mbps +0, 500 Mbps +800, 1 Gbps +1500 required default 100 Mbps; "
    "Contract term: 12 months, 24 months -5%, 36 months -10%; "
    "Static IPs: 50 each; "
    "technical: VLAN ID required number; CPE type: Cisco, Huawei required; IP block: /29, /30; "
    "if Bandwidth is 1 Gbps then SLA is required; "
    "SLA: 99.5%, 99.99% (+900); "
    "if Bandwidth is 1 Gbps, Contract term must not be 12 months"
)


def _designed(client, product="Fiber 900", price=1000):
    _launch(client, product, price)
    body = say(client, f"Design {product}: {DESIGN}")
    assert body["intent_type"] == "INT-42" and body["status"] == "executed", body["rationale"]
    return body


def test_design_from_plain_english(client):
    body = _designed(client)
    assert body["result"]["pricing_attributes"] == ["Bandwidth", "Contract term", "Static IPs", "SLA"]
    assert body["result"]["technical_attributes"] == ["VLAN ID", "CPE type", "IP block"]
    assert body["result"]["rules"] == 2
    design = client.get("/api/catalog/configuration/Fiber 900").json()
    bandwidth = next(a for a in design["attributes"] if a["name"] == "Bandwidth")
    assert bandwidth["values"] == ["100 Mbps", "500 Mbps", "1 Gbps"] and bandwidth["required"] and bandwidth["default"] == "100 Mbps"
    assert {"value": "1 Gbps", "type": "flat", "amount": 1500.0} in bandwidth["price_impacts"]
    ips = next(a for a in design["attributes"] if a["name"] == "IP block")
    assert ips["values"] == ["/29", "/30"] and ips["kind"] == "technical"

    # change and remove later, in the same words
    say(client, "Design Fiber 900: remove Static IPs; Bandwidth: 100 Mbps +0, 500 Mbps +900, 1 Gbps +1500")
    design = client.get("/api/catalog/configuration/Fiber 900").json()
    assert "Static IPs" not in [a["name"] for a in design["attributes"]]
    assert {"value": "500 Mbps", "type": "flat", "amount": 900.0} in next(a for a in design["attributes"] if a["name"] == "Bandwidth")["price_impacts"]


def test_price_simulator_applies_impacts_and_rules(client):
    _designed(client)
    price = lambda **body: client.post("/api/catalog/configuration/Fiber 900/price", json=body).json()  # noqa: E731
    plain = price(pricing_attributes={})
    assert plain["ok"] and plain["configured_price"] == 1000.0  # default 100 Mbps, no impact
    priced = price(pricing_attributes={"Bandwidth": "500 Mbps", "Contract term": "36 months", "Static IPs": "8"}, quantity=2)
    assert priced["ok"], priced
    assert priced["configured_price"] == 1000 + 800 - 100 + 400 and priced["total"] == 2 * 2100
    assert [line["label"] for line in priced["lines"]] == ["Bandwidth: 500 Mbps", "Contract term: 36 months", "Static IPs: 8"]
    bad = price(pricing_attributes={"Bandwidth": "2 Gbps"})
    assert not bad["ok"] and "Bandwidth can be 100 Mbps, 500 Mbps, 1 Gbps" in bad["errors"][0]
    rules = price(pricing_attributes={"Bandwidth": "1 Gbps", "Contract term": "12 months"})
    assert not rules["ok"] and any("SLA is required" in e for e in rules["errors"]) and any("12 months" in e for e in rules["errors"])


def test_pricing_attributes_flow_into_opportunity_and_quote(client):
    _designed(client)
    opp_id = _opportunity(client, "ConfigCo")
    wrong = run(client, "INT-37", opportunity_id=opp_id, offering_name="Fiber 900", pricing_attributes={"Bandwidth": "10 Gbps"})
    assert wrong["status"] == "failed" and "Bandwidth can be" in wrong["rationale"]
    configured = run(client, "INT-37", opportunity_id=opp_id, offering_name="Fiber 900",
                     pricing_attributes="Bandwidth 1 Gbps, Contract term 24 months, SLA 99.99%",
                     technical_attributes={"VLAN ID": "120"})
    assert configured["status"] == "executed", configured["rationale"]
    assert configured["result"]["configured_price"] == 1000 + 1500 - 50 + 900
    assert client.get(f"/api/opportunities/{opp_id}").json()["config"]["technical_attributes"] == {"VLAN ID": "120"}

    # quoting needs Presales first on the configured path
    run(client, "INT-38", opportunity_id=opp_id, address="12 MG Road")
    run(client, "INT-18", opportunity_id=opp_id, decision="feasible")
    quote = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900", requested_discount_pct=5, discount_justification="loyal")
    assert quote["status"] == "executed", quote["rationale"]
    assert quote["result"]["configured_price"] == 3350.0 and quote["result"]["total_price"] == round(3350 * 0.95, 2)
    quote_id = quote["result"]["quote_id"]
    shown = client.get(f"/api/quotes/{quote_id}").json()["configuration"]
    assert shown["pricing_attributes"]["Bandwidth"] == "1 Gbps" and shown["price_breakdown"]["configured"] == 3350.0

    # changing / removing a pricing attribute re-prices the draft quote
    revised = run(client, "INT-19", quote_id=quote_id, pricing_attributes={"Bandwidth": "500 Mbps", "SLA": ""})
    assert revised["status"] == "executed", revised["rationale"]
    assert revised["result"]["configured_price"] == 1000 + 800 - 50
    assert client.get(f"/api/quotes/{quote_id}").json()["total_price"] == round(1750 * 0.95, 2)


def test_pricelist_precedence_quote_over_opportunity_over_customer(client):
    _launch(client, "Fiber 900", 1000)
    client.post("/api/admin/price-lists", json={"name": "Gov", "items": []})
    added = say(client, "Put Fiber 900 on the Gov pricelist at 700")
    assert added["intent_type"] == "INT-43" and added["result"]["price"] == 700.0
    opp_id = _opportunity(client, "TierCo")
    party_id = client.get(f"/api/opportunities/{opp_id}").json()["lead_id"]
    party_id = client.get(f"/api/leads/{party_id}").json()["party_id"]

    assert run(client, "INT-44", entity_type="party", entity_id=party_id, price_list="Enterprise")["status"] == "executed"
    q1 = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900")["result"]
    assert q1["total_price"] == 950.0  # customer's pricelist
    assert run(client, "INT-44", entity_type="opportunity", entity_id=opp_id, price_list="Strategic")["status"] == "executed"
    q2 = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900")["result"]
    assert q2["total_price"] == 900.0  # the opportunity's beats the customer's
    q3 = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900", price_list="Gov")["result"]
    assert q3["total_price"] == 700.0  # the quote's own beats both
    assert client.get(f"/api/quotes/{q3['quote_id']}").json()["configuration"]["quote_price_list"] == "Gov"

    switched = say(client, f"Use the Gov pricelist for quote {q2['quote_id']}")
    assert switched["intent_type"] == "INT-44" and switched["status"] == "executed", switched["rationale"]
    assert client.get(f"/api/quotes/{q2['quote_id']}").json()["total_price"] == 700.0


def test_technical_attributes_flow_to_order_work_orders_provisioning_and_inventory(client):
    _designed(client)
    opp_id = _opportunity(client, "TechCo")
    run(client, "INT-37", opportunity_id=opp_id, offering_name="Fiber 900", pricing_attributes={"Bandwidth": "500 Mbps"},
        technical_attributes={"IP block": "/29"})
    run(client, "INT-38", opportunity_id=opp_id, address="1 Park Street", technical_attributes="CPE type Huawei")
    run(client, "INT-18", opportunity_id=opp_id, decision="feasible")
    quote_id = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900")["result"]["quote_id"]
    run(client, "INT-06", quote_id=quote_id, decision="approve")
    run(client, "INT-39", quote_id=quote_id, po_number="PO-1", po_amount=1800)
    order_id = run(client, "INT-40", quote_id=quote_id)["result"]["order_id"]

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["configuration"][0]["technical_attributes"] == {"IP block": "/29", "CPE type": "Huawei"}
    assert order["configuration"][0]["missing"] == ["VLAN ID is required"]
    blocked = run(client, "INT-25", order_id=order_id, decision="approve")
    assert blocked["status"] == "failed" and "VLAN ID is required" in blocked["rationale"]
    # the failed approval offers the step that fixes it, for this order and product
    fix = blocked["next_actions"]
    assert [(n["code"], n["entities"]) for n in fix] == [("INT-45", {"order_id": order_id, "offering_name": "Fiber 900"})]
    assert fix[0]["reason"] == "Fiber 900 still needs: VLAN ID"
    assert run(client, "INT-45", **fix[0]["entities"], technical_attributes={"VLAN ID": "119"})["status"] == "executed"

    set_attrs = say(client, f"Order {order_id}: VLAN ID 120")
    assert set_attrs["intent_type"] == "INT-45" and set_attrs["status"] == "executed", set_attrs["rationale"]
    assert set_attrs["result"]["still_missing"] == []
    wrong = run(client, "INT-45", order_id=order_id, technical_attributes={"CPE type": "Nokia"})
    assert wrong["status"] == "failed" and "CPE type can be Cisco, Huawei" in wrong["rationale"]

    approved = run(client, "INT-25", order_id=order_id, decision="approve")
    assert approved["status"] == "executed", approved["rationale"]
    wos = client.get(f"/api/orders/{order_id}").json()["fulfillment_work_orders"]
    assert wos[0]["technical_parameters"] == "IP block: /29; CPE type: Huawei; VLAN ID: 120"
    installed = run(client, "INT-26", work_order_id=approved["result"]["work_order_id"])
    assert installed["result"]["provisioning_attributes"]["VLAN ID"] == "120"
    wo3 = run(client, "INT-27", work_order_id=installed["result"]["work_order_id"], signature="ok")["result"]["work_order_id"]
    assert run(client, "INT-28", work_order_id=wo3)["status"] == "executed"

    db = client.TestSessionLocal()
    from app.models.fulfillment import Asset

    asset = db.query(Asset).one()
    assert json.loads(asset.attributes)["VLAN ID"] == "120"
    db.close()


def test_products_without_a_design_price_and_order_as_before(client):
    _launch(client, "Plain 100", 500)
    opp_id = _opportunity(client, "PlainCo")
    quote = run(client, "INT-05", opportunity_id=opp_id, offering_name="Plain 100")
    assert quote["result"]["total_price"] == 500.0 and "price_breakdown" not in quote["result"]
    assert client.get(f"/api/quotes/{quote['result']['quote_id']}").json()["configuration"] is None
    run(client, "INT-06", quote_id=quote["result"]["quote_id"], decision="approve")
    order = run(client, "INT-07", quote_id=quote["result"]["quote_id"])
    assert order["status"] == "executed" and "provisioning_attributes" not in order["result"]
    # the completed line is listed (as installed), with no design attributes
    lines = client.get(f"/api/orders/{order['result']['order_id']}").json()["configuration"]
    assert [(l["offering_name"], l["status"], l["pricing_attributes"], l["technical_attributes"]) for l in lines] == \
        [("Plain 100", "completed", {}, {})]


def test_suggest_then_edit_then_run(client):
    """The form's flow: the AI (here the built-in templates) suggests a design,
    plain English adjusts it, the user edits the cards, Run saves exactly that."""
    _launch(client, "Fiber 900", 1000)
    suggested = client.post("/api/catalog/configuration/Fiber 900/suggest").json()
    names = [op["name"] for op in suggested["ops"]]
    assert {"Bandwidth", "Contract term", "VLAN ID", "CPE type"} <= set(names) and suggested["preview"]
    tweak = client.post("/api/catalog/configuration/Fiber 900/draft", json={"instruction": "Bandwidth: 100 Mbps +0, 1 Gbps +1500"}).json()
    assert tweak["ops"][0]["price_impacts"][1] == {"value": "1 Gbps", "type": "flat", "amount": 1500.0}
    draft = [op for op in suggested["ops"] if op["name"] != "Bandwidth"] + tweak["ops"]
    draft = [op for op in draft if op["name"] != "Circuit ID"]  # removed on its card

    body = run(client, "INT-42", offering_name="Fiber 900", design=draft)
    assert body["status"] == "executed", body["rationale"]
    design = client.get("/api/catalog/configuration/Fiber 900").json()
    assert "Circuit ID" not in [a["name"] for a in design["attributes"]]
    assert {"value": "1 Gbps", "type": "flat", "amount": 1500.0} in next(a for a in design["attributes"] if a["name"] == "Bandwidth")["price_impacts"]
    # a second suggestion only offers what the product doesn't have yet
    again = client.post("/api/catalog/configuration/Fiber 900/suggest").json()
    assert "Bandwidth" not in [op["name"] for op in again["ops"]]


def test_everyday_edits_apply_to_the_unsaved_draft(client):
    _launch(client, "Fiber 900", 1000)
    suggested = client.post("/api/catalog/configuration/Fiber 900/suggest").json()["ops"]
    draft = lambda text: client.post("/api/catalog/configuration/Fiber 900/draft", json={"instruction": text, "draft": suggested}).json()  # noqa: E731
    bandwidth = draft("make 1 Gbps 1500 more")["ops"]
    assert len(bandwidth) == 1 and {"value": "1 Gbps", "type": "flat", "amount": 1500.0} in bandwidth[0]["price_impacts"]
    term = draft("make 36 months 10% cheaper and add a 60 months option to Contract term at -15%")["ops"][0]
    assert term["values"][-1] == "60 months"
    assert {"value": "36 months", "type": "percent", "amount": -10.0} in term["price_impacts"]
    assert {"value": "60 months", "type": "percent", "amount": -15.0} in term["price_impacts"]
    assert draft("make Circuit ID required")["ops"][0]["required"] is True
    assert draft("make it nicer please")["ops"] == []  # never an attribute called "make it nicer"


def test_typed_sentence_prefills_the_design_field(client):
    _launch(client, "Fiber 900", 1000)
    prepared = client.post("/api/intents/prepare", json={"intent_type": "INT-42", "entities": {}, "text": "Design Fiber 900: Static IPs: 50 each"}).json()
    fields = {f["key"]: f for f in prepared["fields"]}
    assert fields["offering_name"]["value"] == "Fiber 900" and fields["design"]["value"] == "Static IPs: 50 each"
    assert prepared["ready"]


def test_admin_plans_before_applying(client):
    _launch(client, "Fiber 900", 1000)
    plan = client.post("/api/admin/product-config/Fiber 900/plan", json={"instruction": "Bandwidth: 100 Mbps, 1 Gbps +1500; technical: VLAN ID required number"}).json()
    assert plan["engine"] == "rules" and plan["preview"] == [
        "Set pricing attribute Bandwidth, values 100 Mbps, 1 Gbps (+1500)", "Set technical attribute VLAN ID, required"]
    assert client.get("/api/catalog/configuration/Fiber 900").json()["designed"] is False  # nothing saved yet
    applied = client.post("/api/admin/product-config/Fiber 900/apply", json={"ops": plan["ops"]}).json()
    assert len(applied["design"]["attributes"]) == 2
    listing = {d["offering_name"]: d for d in client.get("/api/admin/product-config").json()}
    assert listing["Fiber 900"]["pricing"] == 1 and listing["Fiber 900"]["technical"] == 1
