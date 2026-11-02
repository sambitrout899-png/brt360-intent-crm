"""Mobile subscribers, integrated with the platform (INT-129..148, crm_core/comm_mobile.py):

- plans and packs are catalog products: their product line says they are mobile, their product model's
  attributes (as configured when sold) say what they give;
- lines are sold on orders, priced from the customer's pricelist, fulfilled and installed as assets - the
  line comes to life with its asset; postpaid plans are billed by the bill run, prepaid ones from the wallet;
- change orders on the asset (product change, suspend, resume, terminate, port-in) move the line;
- the prepaid / postpaid / SIM / wholesale operations refuse what the line or the policy doesn't allow."""

import datetime as dt
import json

from app.crm_core import comm_billing as cb, comm_mobile as mb, product_config_service as pc
from app.models.fulfillment import Asset
from app.models.mobile import MbLine


def run(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def ok(body):
    assert body["status"] == "executed", {k: body.get(k) for k in ("intent_type", "status", "error", "result", "missing_fields", "rationale")}
    return body["result"]


def refused(body, words):
    assert body["status"] != "executed", body
    assert words.lower() in str(body).lower(), body
    return body


def catalog(client):
    out = client.post("/api/mobile/sample-catalog")
    assert out.status_code == 200, out.text
    return out.json()


def consumer(client, name):
    return ok(run(client, "INT-41", name=name))["party_id"]


def business(client, name):
    return ok(run(client, "INT-15", segment="B2B", name=name, email=f"{name.split()[0].lower()}@example.com"))["party_id"]


def db_of(client):
    return client.TestSessionLocal()


def line(client, msisdn):
    db = db_of(client)
    try:
        return mb.line_out(db, db.query(MbLine).filter(MbLine.msisdn == msisdn).order_by(MbLine.id.desc()).first(), full=True)
    finally:
        db.close()


def asset(client, asset_id):
    db = db_of(client)
    try:
        a = db.get(Asset, asset_id)
        return {"status": a.status.value, "offering": json.loads(a.instance_json or "{}").get("offering_name"), "superseded": a.superseded_by_asset_id}
    finally:
        db.close()


def age(client, msisdn, days):
    db = db_of(client)
    try:
        row = db.query(MbLine).filter(MbLine.msisdn == msisdn).one()
        row.activated_at -= dt.timedelta(days=days)
        db.commit()
    finally:
        db.close()


def recurring(client, party_id):
    db = db_of(client)
    try:
        return cb.unbilled(db, 1, party_id)["items"]["recurring"]
    finally:
        db.close()


def owed(client, party_id):
    db = db_of(client)
    try:
        return cb.balance(db, 1, party_id)
    finally:
        db.close()


def test_plans_and_packs_are_catalog_products_modelled_in_the_product_modeller(client):
    loaded = catalog(client)
    plans = {p["name"]: p for p in loaded["plans"]}
    packs = {p["name"]: p for p in loaded["packs"]}
    smart = plans["Prepaid Smart 28"]
    assert smart["payment_type"] == "prepaid" and smart["validity_days"] == 28 and smart["price"] == 299
    assert smart["allowance"] == {"voice": 1000, "data": 30720, "sms": 300}
    assert plans["Business Shared Pool 1499"]["shared"] and plans["Postpaid 399"]["payment_type"] == "postpaid"
    assert packs["Roaming 7 Days"]["roaming"] and packs["Caller Tune"]["kind"] == "vas" and packs["Data 10 GB Booster"]["allowance"] == {"data": 10240}
    assert client.post("/api/mobile/sample-catalog").json()["created"] == []  # once

    # what the plan gives is its product model: change the attribute, the plan changes
    db = db_of(client)
    try:
        pc.apply_design(db, 1, "Prepaid Smart 28", [{"op": "upsert_attribute", "name": "Data allowance", "kind": "technical", "data_type": "string",
                                                     "default": "50 GB"}])
        db.commit()
    finally:
        db.close()
    again = {p["name"]: p for p in client.get("/api/mobile/catalog").json()["plans"]}
    assert again["Prepaid Smart 28"]["allowance"]["data"] == 51200
    # the policy holds no plans any more
    assert client.put("/api/mobile/policy", json={"plans": []}).status_code == 400
    assert client.put("/api/mobile/policy", json={"transfer_max": 250}).json()["transfer_max"] == 250
    # the Product Modeller's built-in suggestion for a mobile plan proposes the mobile attributes
    db = db_of(client)
    try:
        names = {o["name"] for o in pc._templates(mb.offering_of(db, 1, "Postpaid 399"), {"mobile"})}
        assert {"Payment type", "Validity", "Data allowance", "SIM type"} <= names
    finally:
        db.close()


def test_a_prepaid_line_is_sold_on_an_order_and_lives_as_an_asset(client):
    catalog(client)
    party = consumer(client, "Priya Prepaid")
    refused(run(client, "INT-136", party_id=party, offering_name="Prepaid Smart 28", initial_recharge=100), "at least")
    refused(run(client, "INT-136", party_id=party, offering_name="Business Postpaid 599", sim_type="esim"), "isn't an active product sold to b2c")
    refused(run(client, "INT-136", party_id=party, offering_name="Data 10 GB Booster"), "not a mobile plan")
    made = ok(run(client, "INT-136", party_id=party, offering_name="Prepaid Smart 28", initial_recharge=400, sim_type="esim"))
    msisdn = made["msisdn"]
    assert made["orders"][0]["status"] == "completed" and made["esim_activation_codes"][msisdn].startswith("LPA:1$")
    view = line(client, msisdn)
    assert view["order_id"] == made["orders"][0]["order_id"] and view["asset_id"] and view["plan_charge"] == "wallet"
    assert asset(client, view["asset_id"])["offering"] == "Prepaid Smart 28"
    assert view["balance"] == 101 and view["remaining"] == {"voice": 1000, "data": 30720, "sms": 300} and view["valid_until"]
    assert recurring(client, party) == []  # paid from the wallet - the bill run doesn't bill it again

    # usage: packs first, international from the wallet, roaming refused while off
    call = ok(run(client, "INT-144", msisdn=msisdn, event_type="voice", quantity=10))
    assert call["granted"] and call["charged"] == 0 and call["from_packs"] == {"Prepaid Smart 28": 10}
    intl = ok(run(client, "INT-144", msisdn=msisdn, event_type="voice", quantity=5, international=True))
    assert intl["charged"] == 10.0 and intl["balance"] == 91
    assert "roaming is off" in ok(run(client, "INT-144", msisdn=msisdn, event_type="data", quantity=10, roaming=True))["reason"]

    # packs are catalog products, priced from the customer's pricelist
    refused(run(client, "INT-129", msisdn=msisdn, amount=5), "a recharge is")
    assert ok(run(client, "INT-129", msisdn=msisdn, amount=500, payment_method="upi"))["balance"] == 591
    pack = ok(run(client, "INT-130", msisdn=msisdn, pack="Data 10 GB Booster"))
    assert pack["paid_from"] == "balance" and pack["balance"] == 392
    refused(run(client, "INT-130", msisdn=msisdn, pack="Postpaid 399"), "not a mobile pack")
    refused(run(client, "INT-130", msisdn=msisdn, pack="Roaming 7 Days"), "turn roaming on")
    assert ok(run(client, "INT-130", msisdn=msisdn, pack="Data 10 GB Booster", action="remove"))["removed"] == 1

    # emergency credit and transfers between prepaid lines
    second = ok(run(client, "INT-136", party_id=party, offering_name="Prepaid Pay-as-you-go", sim_type="esim"))["msisdn"]
    refused(run(client, "INT-130", msisdn=second, pack="Data 1 GB Day Pass"), "recharge first")
    refused(run(client, "INT-133", msisdn=second), "on the network")
    age(client, second, 40)
    assert ok(run(client, "INT-133", msisdn=second))["owed"] == 12
    assert ok(run(client, "INT-129", msisdn=second, amount=20, payment_method="cash"))["recovered"] == 12
    sent = ok(run(client, "INT-132", msisdn=msisdn, to_msisdn=second, amount=50))
    assert sent["fee"] == 1 and sent["to_balance"] == 68
    refuse = ok(run(client, "INT-144", msisdn=second, event_type="voice", quantity=60, international=True))
    assert refuse["granted"] is False and "insufficient balance" in refuse["reason"]
    spent = ok(run(client, "INT-144", msisdn=second, event_type="voice", quantity=136))
    assert spent["balance"] == 0 and "no_balance" in line(client, second)["barrings"]
    ok(run(client, "INT-129", msisdn=second, amount=10, payment_method="cash"))
    assert "no_balance" not in line(client, second)["barrings"]
    assert "prepaid" in ok(run(client, "INT-131", msisdn=msisdn))["summary"]


def test_postpaid_lines_for_an_sme_are_billed_on_their_assets(client):
    catalog(client)
    sme = business(client, "Acme SME")
    made = ok(run(client, "INT-136", party_id=sme, offering_name="Business Postpaid 599", quantity=3, user_name="Asha, Ben, Chen", credit_limit=900))
    assert made["count"] == 3 and len(made["orders"]) == 3 and [l["user_name"] for l in made["lines"]] == ["Asha", "Ben", "Chen"]
    assert all(l["plan_charge"] == "asset" and l["asset_id"] and l["sim_type"] == "esim" for l in made["lines"])  # the plan's SIM type
    assert len({l["asset_id"] for l in made["lines"]}) == 3  # each line is an installed service of its own
    billed = recurring(client, sme)
    assert len(billed) == 3 and all("Business Postpaid 599" in r["description"] for r in billed)
    assert owed(client, sme) == 0  # nothing posted by the mobile engine - the bill run bills the assets

    # the customer's choices in the product model set price and allowance
    home = consumer(client, "Ravi Postpaid")
    unlimited = ok(run(client, "INT-136", party_id=home, offering_name="Postpaid 399", pricing_attributes={"Data allowance": "Unlimited"},
                       sim_type="esim"))
    assert unlimited["monthly_price"] == 699 and line(client, unlimited["msisdn"])["remaining"]["data"] >= mb.UNLIMITED

    # packs on postpaid go on the bill; spend alert, then the credit limit bars outgoing use
    first = made["lines"][0]["msisdn"]
    pack = ok(run(client, "INT-130", msisdn=first, pack="Voice 100 Minutes"))
    assert pack["paid_from"] == "bill" and pack["cycle_spend"] == 49 and owed(client, sme) == 49
    ok(run(client, "INT-135", msisdn=first, spend_alert_pct=50))
    big = ok(run(client, "INT-144", msisdn=first, event_type="voice", quantity=500, international=True))  # 1000 > 900 - 49
    assert big["granted"] is False and "credit limit" in big["reason"] and "credit_limit" in line(client, first)["barrings"]
    assert ok(run(client, "INT-135", msisdn=first, credit_limit=5000))["barred"] is False

    # a shared pool: members draw on the owner's allowance
    pool = ok(run(client, "INT-136", party_id=sme, offering_name="Business Shared Pool 1499"))["msisdn"]
    second = made["lines"][1]["msisdn"]
    refused(run(client, "INT-141", msisdn=second, member_msisdn=pool), "not a shared plan")
    assert ok(run(client, "INT-141", msisdn=pool, member_msisdn=second))["members"] == [second]
    used = ok(run(client, "INT-144", msisdn=second, event_type="data", quantity=76800 + 100))
    assert used["from_packs"] == {"Business Postpaid 599": 76800, "Business Shared Pool 1499": 100} and used["charged"] == 0

    # deposits
    assert ok(run(client, "INT-148", msisdn=first, action="take", amount=500))["deposit"] == 500
    refused(run(client, "INT-148", msisdn=first, action="refund"), "owes")


def test_change_orders_on_the_asset_move_the_line(client):
    catalog(client)
    party = consumer(client, "Chandra Change")
    made = ok(run(client, "INT-136", party_id=party, offering_name="Postpaid 399", sim_type="esim"))
    msisdn, first_asset = made["msisdn"], made["lines"][0]["asset_id"]

    # a plan change is a product change order: a new asset, the line moves onto it with the new plan
    refused(run(client, "INT-142", msisdn=msisdn, plan="Prepaid Smart 28"), "switch it to prepaid")
    db = db_of(client)
    try:  # a second postpaid consumer plan to move to, designed in the Product Modeller
        from app.integration import tmf620_adapter
        from app.models.catalog import BundleEligibilityRule
        o = tmf620_adapter.create_offering_version(db, tenant_id=1, name="Postpaid 799", segment="B2C", composition="MobileLine", list_price=799,
                                           price_floor=0, discount_ceiling_pct=20)
        db.add(BundleEligibilityRule(product_offering_id=o.id, segment="B2C", channel="B2C", region="ALL"))
        tmf620_adapter.publish_offering(db, o.id)
        pc.apply_design(db, 1, "Postpaid 799", [mb._t("Payment type", "Postpaid"), mb._t("Data allowance", "Unlimited"),
                                                mb._t("Validity", 30, data_type="number", unit="days")])
        db.commit()
    finally:
        db.close()
    changed = ok(run(client, "INT-142", msisdn=msisdn, plan="Postpaid 799"))
    view = line(client, msisdn)
    assert changed["change_order_id"] and changed["operation"] == "product_change" and view["plan"] == "Postpaid 799"
    assert view["asset_id"] != first_asset and asset(client, first_asset)["status"] == "decommissioned"
    assert asset(client, view["asset_id"])["offering"] == "Postpaid 799" and view["remaining"]["data"] >= mb.UNLIMITED

    # suspend / resume / terminate are change orders too - asset and line move together
    ok(run(client, "INT-143", msisdn=msisdn, action="suspend", reason="travelling"))
    assert line(client, msisdn)["status"] == "suspended" and asset(client, view["asset_id"])["status"] == "suspended"
    assert ok(run(client, "INT-144", msisdn=msisdn, event_type="sms", quantity=1))["granted"] is False
    ok(run(client, "INT-143", msisdn=msisdn, action="resume"))
    assert line(client, msisdn)["status"] == "active" and asset(client, view["asset_id"])["status"] == "active"

    # a change order raised on the asset itself (Change an asset, INT-107) - here an MNP port-in - moves the line
    db = db_of(client)
    try:
        from app.crm_core import abo_service
        abo_service.submit(db, 1, view["asset_id"], "number_portability", {"port_number": "9876543210", "donor_operator": "OtherTel", "fulfil_now": True})
        db.commit()
    finally:
        db.close()
    assert line(client, "9876543210")["id"] == view["id"]

    gone = ok(run(client, "INT-143", msisdn="9876543210", action="terminate", reason="moving abroad"))
    assert gone["status"] == "terminated" and gone["change_order_id"] and asset(client, view["asset_id"])["status"] == "decommissioned"
    other = consumer(client, "Next Person")
    refused(run(client, "INT-136", party_id=other, offering_name="Prepaid Pay-as-you-go", msisdn="9876543210", sim_type="esim"), "quarantined")


def test_prepaid_to_postpaid_is_a_product_change_and_billing_follows(client):
    catalog(client)
    party = consumer(client, "Mira Move")
    msisdn = ok(run(client, "INT-136", party_id=party, offering_name="Prepaid Smart 28", initial_recharge=500, sim_type="esim"))["msisdn"]
    assert recurring(client, party) == []
    moved = ok(run(client, "INT-134", msisdn=msisdn, to_payment_type="postpaid", plan="Postpaid 399", credit_limit=1500))
    view = line(client, msisdn)
    assert moved["change_order_id"] and view["payment_type"] == "postpaid" and view["plan_charge"] == "asset" and view["plan"] == "Postpaid 399"
    assert moved["carried_over"] == 201 and owed(client, party) == -201  # the prepaid balance is a credit on the bill
    assert view["credit_limit"] == 1500 and view["balance"] == 0
    assert any("Postpaid 399" in r["description"] for r in recurring(client, party))  # now billed on its asset


def test_a_mobile_fulfilment_pattern_takes_the_number_and_sim_from_inventory(client):
    catalog(client)
    assert client.post("/api/inventory/sample").status_code == 200
    assert ok(run(client, "INT-122", item_type="sim", quantity=2, location="BLR-STORE"))["received"] == 2
    pattern = client.post("/api/mobile/fulfillment-pattern")
    assert pattern.status_code == 200 and "Prepaid Smart 28" in pattern.json()["products"], pattern.text
    party = consumer(client, "Pat Pattern")
    made = ok(run(client, "INT-136", party_id=party, offering_name="Prepaid Smart 28", initial_recharge=299))
    view = made["lines"][0]
    assert view["msisdn"].startswith("918041000") and view["iccid"] and view["status"] == "active", view
    db = db_of(client)
    try:
        from app.crm_core import inventory_service as inv
        held = {i.identifier: i.lifecycle for i in inv.held_by_order(db, 1, view["order_id"], "sim")}
        assert held == {view["iccid"]: "in_use"}
    finally:
        db.close()


def test_sim_number_barring_roaming_vouchers_and_wholesale(client):
    catalog(client)
    party = consumer(client, "Sam Swap")
    # an order line without a SIM (no stock, no ICCID) activates the line pending; its first SIM makes it live
    made = ok(run(client, "INT-136", party_id=party, offering_name="Prepaid Pay-as-you-go", initial_recharge=200))
    msisdn = made["msisdn"]
    assert line(client, msisdn)["status"] == "pending"
    first = ok(run(client, "INT-137", msisdn=msisdn, new_iccid="8991000000000000001"))
    assert first["status"] == "active" and first["fee"] == 0
    refused(run(client, "INT-137", msisdn=msisdn, reason="lost"), "verify the customer's identity")
    swapped = ok(run(client, "INT-137", msisdn=msisdn, reason="lost", identity_verified=True, sim_type="esim"))
    assert swapped["fee"] == 25 and swapped["esim_activation_code"]
    assert "just swapped" in ok(run(client, "INT-144", msisdn=msisdn, event_type="voice", quantity=1, international=True))["reason"]
    ok(run(client, "INT-139", msisdn=msisdn, action="bar", barring="lost_stolen", reason="phone stolen"))
    assert "lost_stolen" in ok(run(client, "INT-144", msisdn=msisdn, event_type="sms", quantity=1))["reason"]
    refused(run(client, "INT-139", msisdn=msisdn, action="unbar", barring="lost_stolen"), "verify")
    ok(run(client, "INT-139", msisdn=msisdn, action="unbar", barring="lost_stolen", identity_verified=True))
    changed = ok(run(client, "INT-138", msisdn=msisdn, new_msisdn="9100055555"))
    assert changed["old_msisdn"] == msisdn
    db = db_of(client)
    try:
        db.query(MbLine).filter(MbLine.msisdn == "9100055555").update({MbLine.swap_cooldown_until: None})
        db.commit()
    finally:
        db.close()
    ok(run(client, "INT-129", msisdn="9100055555", amount=600, payment_method="card", card_last4="4242"))
    assert ok(run(client, "INT-140", msisdn="9100055555", action="on", pack="Roaming 7 Days"))["pack"]["paid_from"] == "balance"
    abroad = ok(run(client, "INT-144", msisdn="9100055555", event_type="data", quantity=100, roaming=True))
    assert abroad["from_packs"] == {"Roaming 7 Days": 100} and abroad["charged"] == 0

    # vouchers: PINs once, wrong PINs counted
    made = ok(run(client, "INT-145", count=2, face_value=100))
    pins = [r.split(",")[1] for r in client.get(made["download"]).text.strip().splitlines()[1:]]
    assert client.get(made["download"]).status_code == 400
    assert ok(run(client, "INT-129", msisdn="9100055555", voucher_pin="99999999999999"))["recharged"] is False
    assert ok(run(client, "INT-129", msisdn="9100055555", voucher_pin=pins[0]))["amount"] == 100

    # wholesale: an MVNO's lines take its number range; settlement at its wholesale rates
    ok(run(client, "INT-146", wholesale_partner="Zoom Mobile", partner_kind="mvno", wholesale_rates="voice 0.2, sms 0.05, data 0.01",
           number_range="9180000000-9180000009"))
    zoom = ok(run(client, "INT-136", party_id=party, offering_name="Prepaid Pay-as-you-go", wholesale_partner="Zoom Mobile",
                  initial_recharge=100, sim_type="esim"))
    assert zoom["msisdn"] == "9180000000"
    ok(run(client, "INT-144", msisdn="9180000000", event_type="voice", quantity=30))
    today = dt.date.today()
    st = ok(run(client, "INT-147", wholesale_partner="Zoom Mobile", period_start=today.replace(day=1).isoformat(), period_end=today.isoformat()))
    assert st["wholesale_due"] == 6.0


def test_plain_english_and_the_mobile_page(client):
    catalog(client)
    sme = business(client, "Nita Natural")
    said = client.post("/api/intents", json={"text": f"Activate 2 postpaid lines on Business Postpaid 599 for customer {sme} as eSIMs"}).json()
    assert said["intent_type"] == "INT-136" and said["status"] == "executed", said
    first = said["result"]["lines"][0]["msisdn"]
    pack = client.post("/api/intents", json={"text": f"Add the Data 10 GB Booster pack to {first}"}).json()
    assert pack["intent_type"] == "INT-130", pack
    balance = client.post("/api/intents", json={"text": f"What's the balance on {first}?"}).json()
    assert balance["intent_type"] == "INT-131" and balance["status"] == "executed", balance
    assert client.get("/api/mobile/overview").json()["postpaid"] == 2
    found = client.get("/api/mobile/lines", params={"q": "Nita"}).json()
    assert len(found) == 2 and client.get(f"/api/mobile/lines/{found[0]['id']}").json()["asset_id"]
    assert {f"INT-{n}" for n in range(129, 149)} <= {i["code"] for i in client.get("/api/intents/catalog").json()["intents"]}
