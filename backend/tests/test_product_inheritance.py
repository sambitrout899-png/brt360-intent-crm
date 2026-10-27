"""Attribute library -> class -> product inheritance (object-oriented: a
level narrows what it inherits, never widens it), and product instances: the
definition live when a product is picked is frozen into the opportunity /
quote / order and configured there - the catalog stays untouched."""

from test_product_configuration import _designed
from test_sales_to_order import _launch, _opportunity, run

ADMIN = "/api/admin/product-model"


def _class_attr(client, cls, **body):
    return client.post(f"/api/admin/product-classes/{cls}/attributes", json=body)


def test_class_narrows_library_and_product_narrows_class(client):
    _launch(client, "Office Fiber", 1000)
    _launch(client, "Home Fiber", 500)
    # the library attribute offers every speed; the class picks the business ones
    client.post(f"{ADMIN}/Home Fiber/attributes", json={"name": "Speed", "values": ["50 Mbps", "100 Mbps", "500 Mbps", "1 Gbps"], "affects_price": True})
    client.post("/api/admin/product-classes", json={"name": "Business Broadband", "members": []})
    r = _class_attr(client, "Business Broadband", name="Speed", values=["100 Mbps", "500 Mbps", "1 Gbps"], affects_price=True, required=True,
                    default="100 Mbps", price_impacts=[{"value": "500 Mbps", "type": "flat", "amount": 700}, {"value": "1 Gbps", "type": "flat", "amount": 1400}])
    assert r.status_code == 200, r.text

    # binding a product to the class: it inherits Speed, as the class sees it
    bound = client.put(f"{ADMIN}/Office Fiber/class", json={"class_name": "Business Broadband"}).json()
    speed = next(a for a in bound["attributes"] if a["name"] == "Speed")
    assert bound["class"] == "Business Broadband" and speed["source"] == "class" and speed["values"] == ["100 Mbps", "500 Mbps", "1 Gbps"]
    assert speed["library_values"] == ["50 Mbps", "100 Mbps", "500 Mbps", "1 Gbps"] and speed["required"]

    # the product may narrow (drop 1 Gbps) and override a price; it keeps the class's other prices
    ok = client.post(f"{ADMIN}/Office Fiber/attributes", json={"name": "Speed", "values": ["100 Mbps", "500 Mbps"], "affects_price": True,
                                                                "price_impacts": [{"value": "500 Mbps", "type": "flat", "amount": 600}]})
    assert ok.status_code == 200, ok.text
    design = client.get("/api/catalog/configuration/Office Fiber").json()
    speed = next(a for a in design["attributes"] if a["name"] == "Speed")
    assert speed["values"] == ["100 Mbps", "500 Mbps"] and speed["required"] and speed["overrides"] == "Business Broadband"
    assert {"value": "500 Mbps", "type": "flat", "amount": 600.0} in speed["price_impacts"]
    assert not any(i["value"] == "1 Gbps" for i in speed["price_impacts"])

    # ...but never widen it, or make a required attribute optional
    wide = client.post(f"{ADMIN}/Office Fiber/attributes", json={"name": "Speed", "values": ["50 Mbps", "100 Mbps"], "affects_price": True})
    assert wide.status_code == 400 and "not offered by class Business Broadband" in wide.json()["detail"]
    client.post(f"{ADMIN}/Office Fiber/attributes", json={"name": "Speed", "affects_price": True, "required": False})
    assert next(a for a in client.get("/api/catalog/configuration/Office Fiber").json()["attributes"] if a["name"] == "Speed")["required"]

    # the class narrowing later reaches every product bound to it
    _class_attr(client, "Business Broadband", name="Speed", values=["500 Mbps", "1 Gbps"], affects_price=True, default="500 Mbps")
    speed = next(a for a in client.get("/api/catalog/configuration/Office Fiber").json()["attributes"] if a["name"] == "Speed")
    assert speed["values"] == ["500 Mbps"]

    # reset to class: removing the product's own override brings the class's back
    client.delete(f"{ADMIN}/Office Fiber/attributes/Speed")
    speed = next(a for a in client.get(f"{ADMIN}/Office Fiber/inheritance").json()["attributes"] if a["name"] == "Speed")
    assert speed["source"] == "class" and speed["values"] == ["500 Mbps", "1 Gbps"]

    # a product binds to one class - binding it elsewhere moves it
    client.post("/api/admin/product-classes", json={"name": "Premium Broadband", "parent": "Business Broadband", "members": ["Office Fiber"]})
    chain = client.get(f"{ADMIN}/Office Fiber/inheritance").json()
    assert chain["chain"] == ["Business Broadband", "Premium Broadband"] and chain["class"] == "Premium Broadband"
    classes = {c["name"]: c["members"] for c in client.get("/api/admin/product-classes").json()}
    assert classes["Business Broadband"] == [] and classes["Premium Broadband"] == ["Office Fiber"]
    # a sub-class narrows its parent the same way
    sub = _class_attr(client, "Premium Broadband", name="Speed", values=["1 Gbps", "10 Gbps"], affects_price=True)
    assert sub.status_code == 400 and "10 Gbps" in sub.json()["detail"]


def test_instance_freezes_the_definition_for_the_deal(client):
    _designed(client)  # Fiber 900: Bandwidth 100 Mbps +0 / 500 Mbps +800 / 1 Gbps +1500 ...
    opp_id = _opportunity(client, "SnapCo")
    configured = run(client, "INT-37", opportunity_id=opp_id, offering_name="Fiber 900", pricing_attributes={"Bandwidth": "500 Mbps"})
    assert configured["status"] == "executed", configured["rationale"]
    [opp_inst] = client.get("/api/configurator/instances", params={"context_type": "opportunity", "context_id": opp_id}).json()
    assert opp_inst["offering_name"] == "Fiber 900" and opp_inst["configuration"]["pricing"]["Bandwidth"] == "500 Mbps" and not opp_inst["stale"]

    # the catalog changes after the product was picked: 500 Mbps now costs 2000 more, and 10 Gbps exists
    client.post(f"{ADMIN}/Fiber 900/attributes", json={
        "name": "Bandwidth", "values": ["100 Mbps", "500 Mbps", "1 Gbps", "10 Gbps"], "affects_price": True, "required": True,
        "price_impacts": [{"value": "100 Mbps", "type": "flat", "amount": 0}, {"value": "500 Mbps", "type": "flat", "amount": 2000},
                          {"value": "1 Gbps", "type": "flat", "amount": 1500}, {"value": "10 Gbps", "type": "flat", "amount": 9000}]})
    assert client.get("/api/configurator/instances", params={"context_type": "opportunity", "context_id": opp_id}).json()[0]["stale"]

    # ...the deal keeps the definition it was sold on
    run(client, "INT-38", opportunity_id=opp_id, address="1 Snap Street")
    run(client, "INT-18", opportunity_id=opp_id, decision="feasible")
    quote = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900")
    assert quote["status"] == "executed" and quote["result"]["configured_price"] == 1000 + 800, quote["result"]
    quote_id = quote["result"]["quote_id"]
    [q_inst] = client.get("/api/configurator/instances", params={"context_type": "quote", "context_id": quote_id}).json()
    assert q_inst["source_instance_id"] == opp_inst["id"]
    # the frozen copy doesn't know 10 Gbps; the catalog does
    revised = run(client, "INT-19", quote_id=quote_id, pricing_attributes={"Bandwidth": "10 Gbps"})
    assert revised["status"] == "failed" and "Bandwidth can be 100 Mbps, 500 Mbps, 1 Gbps" in revised["rationale"]
    # configuring in the quote: change the speed, re-priced on the frozen copy
    assert run(client, "INT-19", quote_id=quote_id, pricing_attributes={"Bandwidth": "100 Mbps"})["result"]["configured_price"] == 1000
    # the configurator in the quote's context works on the same copy
    session = client.post("/api/configurator/Fiber 900/session", json={"picks": {"Bandwidth": "1 Gbps"}, "quote_id": quote_id}).json()
    assert "10 Gbps" not in [v["value"] for v in next(v for v in session["variables"] if v["name"] == "Bandwidth")["values"]]

    # an explicit refresh takes the new definition into the (still open) quote
    fresh = client.post(f"/api/configurator/instances/{q_inst['id']}/refresh").json()
    assert not fresh["stale"]
    assert run(client, "INT-19", quote_id=quote_id, pricing_attributes={"Bandwidth": "10 Gbps"})["result"]["configured_price"] == 10000
    # the catalog definition itself was never written by any of this
    bandwidth = next(a for a in client.get("/api/catalog/configuration/Fiber 900").json()["attributes"] if a["name"] == "Bandwidth")
    assert bandwidth["values"] == ["100 Mbps", "500 Mbps", "1 Gbps", "10 Gbps"]
