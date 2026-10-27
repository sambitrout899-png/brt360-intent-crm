"""The existing product intents working on a modelled, released product."""

import json

from test_configurator import _setup
from test_product_hierarchy import BBI, _opportunity, _run

from app.models.orders import Order
from app.models.product_config import QuoteItemConfig

GOOD = {"Contention Ratio": "1:10", "Access Medium": "Fiber", "Bandwidth": "2 Mbps", "CPE": "Yes", "CPE Type": "Provider"}


def test_existing_product_intents_follow_the_released_model(client):
    _setup(client)
    assert _run(client, "INT-48", offering_name=BBI)["result"]["model_version"] == 1

    # INT-42 on a released product: saved to the workspace, selling unchanged until released
    designed = _run(client, "INT-42", offering_name=BBI, instruction="Static IPs: 50 each")
    assert designed["status"] == "executed" and "workspace" in designed["result"]["note"]
    assert any(a["code"] == "INT-48" for a in designed["next_actions"])
    assert "Static IPs" not in {v["name"] for v in client.post(f"/api/configurator/{BBI}/session", json={"picks": {}}).json()["variables"]}
    assert _run(client, "INT-48", offering_name=BBI)["result"]["model_version"] == 2
    assert "Static IPs" in {v["name"] for v in client.post(f"/api/configurator/{BBI}/session", json={"picks": {}}).json()["variables"]}

    # INT-43 pricelist price is the configurator's base
    _run(client, "INT-43", price_list="Enterprise", offering_name=BBI, price=100)
    party_id, opp = _opportunity(client)
    _run(client, "INT-44", entity_type="party", entity_id=party_id, price_list="Enterprise")
    session = client.post(f"/api/configurator/{BBI}/session", json={"picks": GOOD, "party_id": party_id}).json()
    assert session["price"]["base"] == 100 and session["valid"]

    # INT-37 configure -> Presales -> INT-05 quote records the model version
    configured = _run(client, "INT-37", opportunity_id=opp, offering_name=BBI, pricing_attributes=GOOD)
    assert configured["status"] == "executed", configured
    _run(client, "INT-38", opportunity_id=opp, address="12 Ring Road")
    feasible = _run(client, "INT-18", opportunity_id=opp, decision="feasible")
    assert feasible["status"] == "executed", feasible
    quote = _run(client, "INT-05", opportunity_id=opp, offering_name=BBI)
    assert quote["status"] == "executed", quote
    db = client.TestSessionLocal()
    breakdown = json.loads(db.query(QuoteItemConfig).order_by(QuoteItemConfig.id.desc()).first().price_breakdown_json)
    assert breakdown["model_version"] == 2
    db.close()

    # INT-19 revising against the rules
    refused = _run(client, "INT-19", quote_id=quote["result"]["quote_id"], pricing_attributes={"Access Medium": "DSL", "Bandwidth": "4 Mbps"})
    assert refused["status"] == "failed" and "at most 2048" in str(refused)

    # order, then INT-45 technical attributes (inventory references) go through
    _run(client, "INT-06", quote_id=quote["result"]["quote_id"], decision="approve")
    _run(client, "INT-39", quote_id=quote["result"]["quote_id"], po_number="PO-1")
    matched = _run(client, "INT-40", quote_id=quote["result"]["quote_id"])
    order_id = matched["result"].get("order_id")
    if order_id is None:
        db = client.TestSessionLocal()
        order_id = db.query(Order).filter(Order.quote_id == quote["result"]["quote_id"]).one().id
        db.close()
    tech = _run(client, "INT-45", order_id=order_id, technical_attributes={"POP Location": "Muscat-POP-2", "Access Switch": "SW-14", "Access Port": "ge-0/0/7"})
    assert tech["status"] == "executed", tech

    # INT-02 change the product: a new catalog version under the same name keeps its model
    changed = _run(client, "INT-02", offering_name=BBI, new_list_price=50)
    assert changed["status"] == "executed", changed
    assert client.post(f"/api/configurator/{BBI}/session", json={"picks": GOOD}).json()["version"] == 2

    # INT-07 consumer order is held to the product's eligibility
    client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "eligibility", "text": "only for B2B customers"})
    consumer = _run(client, "INT-41", name="Asha Home")["result"]["party_id"]
    order = _run(client, "INT-07", party_id=consumer, offering_name=BBI, pricing_attributes=GOOD)
    assert order["status"] == "failed" and "not eligible" in str(order)
