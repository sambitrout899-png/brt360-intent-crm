"""The B2B lead -> opportunity -> Presales journey, as business users see it:
- only the products a prospect may buy are offered to them (administrators
  still see the whole catalog, drafts included);
- the product picked on the lead is carried to the opportunity and its quote -
  locked in the forms, never picked again;
- the Account Manager configures the pricing attributes; the technical ones are
  added and changed when it goes to Presales and during their check;
- Presales sees the pricing attributes but can't change them."""

from test_new_connect import _launch, _run
from test_sales_to_order import _staff, _user


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _prepare(client, code, user_id=None, **entities):
    body = client.post("/api/intents/prepare", json={"intent_type": code, "entities": entities}, headers=_as(user_id) if user_id else {})
    assert body.status_code == 200, body.text
    return {f["key"]: f for f in body.json()["fields"]}


def _run_as(client, user_id, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}, headers=_as(user_id)).json()


def _setup(client):
    _launch(client, "Fiber 800 Biz", "B2B")
    _launch(client, "Home Fiber 100", "B2C", 500)
    client.post("/api/admin/product-model", json={"name": "Transmission Leased Line", "segment": "B2B", "price": 5000})  # a draft
    return _run(client, "INT-15", name="Journey Co", segment="B2B")["result"]["party_id"]


def test_business_users_see_only_eligible_products(client):
    party_id = _setup(client)
    seller = _user(client, "seller@journey.test")
    sold = {o["value"] for o in _prepare(client, "INT-46", seller, party_id=party_id)["products"]["options"]}
    assert sold == {"Fiber 800 Biz"}  # no draft, no consumer product
    everything = {o["value"] for o in _prepare(client, "INT-46", party_id=party_id)["products"]["options"]}
    assert {"Fiber 800 Biz", "Home Fiber 100", "Transmission Leased Line"} <= everything  # the administrator's view


def test_the_lead_product_is_carried_and_attributes_split_between_am_and_presales(client):
    party_id = _setup(client)
    lead = _run(client, "INT-46", party_id=party_id, products=[{"offering_name": "Fiber 800 Biz", "quantity": 2}])["result"]["lead_id"]
    opp = _run(client, "INT-17", lead_id=lead, decision="approve")["result"]["opportunity_id"]

    configure = _prepare(client, "INT-37", opportunity_id=opp)
    assert configure["offering_name"]["value"] == "Fiber 800 Biz" and configure["offering_name"]["locked"]
    assert "technical_attributes" not in configure  # added on the way to Presales, not here
    configured = _run(client, "INT-37", opportunity_id=opp, pricing_attributes={"Bandwidth": "100 Mbps"})  # no product given
    assert configured["status"] == "executed" and configured["result"]["offering_name"] == "Fiber 800 Biz", configured
    assert _prepare(client, "INT-37", opportunity_id=opp)["pricing_attributes"]["value"] == {"Bandwidth": "100 Mbps"}

    to_presales = _prepare(client, "INT-38", opportunity_id=opp)
    assert to_presales["pricing_attributes"]["readonly"] and to_presales["pricing_attributes"]["value"] == {"Bandwidth": "100 Mbps"}
    assert not to_presales["technical_attributes"].get("readonly")
    sent = _run(client, "INT-38", opportunity_id=opp, address="1 MG Road", technical_attributes={"VLAN ID": "120"})
    assert sent["status"] == "executed", sent

    presales = _user(client, "presales@journey.test")
    _staff(client, "Presales", presales)
    check = _prepare(client, "INT-18", presales, opportunity_id=opp)
    assert check["pricing_attributes"]["readonly"] and check["technical_attributes"]["value"] == {"VLAN ID": "120"}
    refused = _run_as(client, presales, "INT-37", opportunity_id=opp, pricing_attributes={"Bandwidth": "1 Gbps"})
    assert refused["status"] != "executed" and "Presales sees the pricing attributes" in str(refused)
    assert _prepare(client, "INT-37", presales, opportunity_id=opp)["pricing_attributes"]["readonly"]

    done = _run_as(client, presales, "INT-18", opportunity_id=opp, decision="feasible", technical_attributes={"CPE type": "Cisco"})
    assert done["status"] == "executed", done
    assert done["result"]["technical_attributes"] == {"VLAN ID": "120", "CPE type": "Cisco"}

    quote = _prepare(client, "INT-05", opportunity_id=opp)
    assert quote["offering_name"]["value"] == "Fiber 800 Biz" and quote["offering_name"]["locked"]
    assert _run(client, "INT-05", opportunity_id=opp)["status"] == "executed"  # the product is not asked for again
