"""Post-order scenarios on an installed service, end to end - each one as
the user would say it, as a structured intent, and on the record's buttons:
upgrade / downgrade, product change, promotion change, suspend / resume,
terminate, relocation, account transfer, MNP port-in / port-out and the
follow-on order. A completed order's service lives on as an asset, so the
older live-service intents (INT-08 / 09 / 69 / 70) work through it too."""

import json

from test_offering_order_intent import invoke

from app.models.fulfillment import Asset
from app.models.orders import Order


def say(client, text):
    return client.post("/api/intents", json={"text": text}).json()


def _product(client, name, price, bandwidth=True):
    made = invoke(client, "INT-01", name=name, composition="Fiber500", segment="B2C", channel="B2C", list_price=price, requested_discount_pct=0)
    assert made["status"] == "executed", made
    if bandwidth:
        design = say(client, f"Design {name}: Bandwidth: 100 Mbps +0, 500 Mbps +200 default 100 Mbps")
        assert design["status"] == "executed", design


def _installed(client, customer="Post order customer"):
    _product(client, "Home Fiber", 500)
    party = invoke(client, "INT-41", name=customer)["result"]["party_id"]
    placed = invoke(client, "INT-106", party_id=party, offering_name="Home Fiber", order_mode="silent")
    assert placed["status"] == "executed", placed
    with client.TestSessionLocal() as db:
        order = db.get(Order, placed["result"]["order_id"])
        assert order.status.value == "completed"
        asset = db.query(Asset).filter_by(order_item_id=order.items[0].id).one()
        return asset.id, order.id, party


def _asset(client, asset_id):
    with client.TestSessionLocal() as db:
        a = db.get(Asset, asset_id)
        return a.status.value, json.loads(a.instance_json), a.service_account_id


def _confirm(client, change):
    assert change["status"] == "executed", change
    done = invoke(client, "INT-108", order_id=change["result"]["order_id"])
    assert done["status"] == "executed", done


def test_upgrade_and_downgrade_said_plainly(client):
    asset_id, _, _ = _installed(client)
    up = say(client, f"Upgrade asset {asset_id} to 500 Mbps")
    assert up["intent_type"] == "INT-107" and up["entities"]["asset_operation"] == "modify", up
    assert up["result"]["direction"] == "upgrade" and up["result"]["new_monthly"] == 700
    assert _asset(client, asset_id)[1]["net_unit_price"] == 500  # unchanged until fulfilled
    _confirm(client, up)
    assert _asset(client, asset_id)[1]["configuration"]["pricing"]["Bandwidth"] == "500 Mbps"

    down = say(client, f"Downgrade asset {asset_id} to 100 Mbps")
    assert down["result"]["direction"] == "downgrade" and down["result"]["new_monthly"] == 500, down
    _confirm(client, down)
    assert _asset(client, asset_id)[1]["net_unit_price"] == 500


def test_the_live_service_intents_work_on_a_completed_order(client):
    asset_id, order_id, _ = _installed(client)
    changed = invoke(client, "INT-08", order_id=order_id, new_monthly_price=900)
    assert changed["status"] == "executed", changed
    assert changed["result"]["new_monthly_price"] == 900 and changed["result"]["asset_id"] == asset_id
    assert _asset(client, asset_id)[1]["net_unit_price"] == 900

    suspended = invoke(client, "INT-09", order_id=order_id, action="suspend")
    assert suspended["status"] == "executed" and suspended["result"]["status"] == "suspended", suspended
    assert _asset(client, asset_id)[0] == "suspended"
    assert invoke(client, "INT-08", order_id=order_id, new_monthly_price=950)["status"] == "failed"  # resume first
    resumed = invoke(client, "INT-70", order_id=order_id)
    assert resumed["status"] == "executed", resumed
    assert _asset(client, asset_id)[0] == "active"

    ended = invoke(client, "INT-09", order_id=order_id, action="terminate")
    assert ended["status"] == "executed" and ended["result"]["status"] == "terminated", ended
    assert _asset(client, asset_id)[0] == "decommissioned"
    with client.TestSessionLocal() as db:
        assert db.get(Order, order_id).status.value == "completed"  # the original order is never touched


def test_cancel_opens_the_retention_window_and_suspension_its_grace_clock(client):
    import time

    for clock_type, hours in (("retention_window", 0.0001), ("suspension_grace", 24.0)):
        assert client.post("/api/admin/sla-policies", json={"clock_type": clock_type, "duration_hours": hours,
                                                             "on_breach_action": "auto_cancel"}).status_code == 201
    asset_id, order_id, _ = _installed(client)
    window = invoke(client, "INT-09", order_id=order_id, action="cancel")
    assert window["result"]["status"] == "pending_cancellation" and "retention_window_deadline" in window["result"], window
    assert _asset(client, asset_id)[0] == "active"  # still live while the customer can be retained
    retained = invoke(client, "INT-09", order_id=order_id, action="cancel", decision="retain")
    assert retained["result"]["status"] == "active" and retained["result"]["retained"] is True

    suspended = invoke(client, "INT-09", order_id=order_id, action="suspend")
    assert "suspension_grace_deadline" in suspended["result"], suspended
    invoke(client, "INT-70", order_id=order_id)
    invoke(client, "INT-09", order_id=order_id, action="cancel")
    time.sleep(0.5)
    sweep = client.post("/api/admin/sla/process-due").json()
    assert [b["outcome"] for b in sweep["breached"]] == ["cancelled"], sweep  # the resumed service's grace clock was closed
    assert _asset(client, asset_id)[0] == "decommissioned"


def test_a_bundle_s_provisioning_components_do_not_block_a_change(client):
    """An order copies the bundle components that affect provisioning into its
    technical settings; changing the installed service must not re-check them
    as technical attributes (it said "'Business SIM Line' is not a technical
    attribute of this product")."""
    asset_id, _, _ = _installed(client)
    with client.TestSessionLocal() as db:
        asset = db.get(Asset, asset_id)
        data = json.loads(asset.instance_json)
        data["definition"].setdefault("components", []).append(
            {"id": 9001, "name": "Business SIM Line", "parent_id": None, "parent": None, "path": ["Business SIM Line"], "description": "",
             "min_qty": 0, "max_qty": 5, "default_qty": 1, "mandatory": False, "charge_type": "recurring", "price": 0,
             "affects_provisioning": True, "sequence": 1, "slot": None})
        data["configuration"]["pricing"]["Business SIM Line"] = "2"
        data["configuration"]["technical"]["Business SIM Line"] = "2"
        asset.instance_json = json.dumps(data)
        db.commit()
    changed = invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify", pricing_attributes={"Bandwidth": "500 Mbps"})
    assert changed["status"] == "executed", changed["rationale"]
    _confirm(client, changed)
    technical = _asset(client, asset_id)[1]["configuration"]["technical"]
    assert technical["Business SIM Line"] == "2"  # still carried for provisioning


def test_product_change(client):
    asset_id, _, _ = _installed(client)
    _product(client, "Home Fiber Max", 800)
    changed = say(client, f"Change the product of asset {asset_id} to Home Fiber Max")
    assert changed["entities"]["asset_operation"] == "product_change", changed
    assert changed["result"]["direction"] == "upgrade" and changed["result"]["new_monthly"] == 800, changed
    _confirm(client, changed)
    # as Siebel does it: the old product's asset is closed and the new product installed as a new asset
    status, old, _ = _asset(client, asset_id)
    assert status == "decommissioned" and old["offering_name"] == "Home Fiber" and old["replaced_by_offering"] == "Home Fiber Max"
    new_id = old["replaced_by_asset_id"]
    status, data, _ = _asset(client, new_id)
    assert data["offering_name"] == "Home Fiber Max" and data["previous_offering"] == "Home Fiber" and status == "active"
    assert data["replaces_asset_id"] == asset_id
    assert invoke(client, "INT-107", asset_id=new_id, asset_operation="product_change", offering_name="Home Fiber Max")["status"] == "failed"
    assert invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify")["status"] == "failed"  # the closed one


def _replacement(client, asset_id):
    return _asset(client, asset_id)[1]["replaced_by_asset_id"]


def test_a_product_change_sorts_carried_settings_by_the_new_product(client):
    """'Contract term' was technical on the old product and is a pricing
    attribute on the new one - moving must not fail with "'Contract term' is
    a pricing attribute - set it there"; a value the new product doesn't
    offer is left behind."""
    asset_id, _, _ = _installed(client)
    _product(client, "Home Fiber Max", 800)
    with client.TestSessionLocal() as db:
        asset = db.get(Asset, asset_id)
        data = json.loads(asset.instance_json)
        data["configuration"]["pricing"].pop("Bandwidth", None)
        data["configuration"]["technical"]["Bandwidth"] = "500 Mbps"  # technical here, pricing on Home Fiber Max
        data["configuration"]["technical"]["Old only setting"] = "x"
        asset.instance_json = json.dumps(data)
        db.commit()
    changed = invoke(client, "INT-107", asset_id=asset_id, asset_operation="product_change", offering_name="Home Fiber Max",
                     technical_attributes={"Bandwidth": "500 Mbps"})
    assert changed["status"] == "executed", changed["rationale"]
    _confirm(client, changed)
    asset_id = _replacement(client, asset_id)
    config = _asset(client, asset_id)[1]["configuration"]
    assert config["pricing"]["Bandwidth"] == "500 Mbps" and "Bandwidth" not in config["technical"]
    assert "Old only setting" not in config["technical"]

    # a carried value the new product doesn't offer gives way to its default
    _product(client, "Home Fiber Lite", 300)
    with client.TestSessionLocal() as db:
        asset = db.get(Asset, asset_id)
        data = json.loads(asset.instance_json)
        data["configuration"]["pricing"]["Bandwidth"] = "10 Gbps"
        asset.instance_json = json.dumps(data)
        db.commit()
    moved = invoke(client, "INT-107", asset_id=asset_id, asset_operation="product_change", offering_name="Home Fiber Lite")
    assert moved["status"] == "executed", moved["rationale"]


def test_the_product_change_form_picks_a_new_product(client):
    asset_id, _, _ = _installed(client)
    _product(client, "Home Fiber Max", 800, bandwidth=False)
    assert say(client, "Design Home Fiber Max: Speed tier: Gold +100, Silver +0 default Silver")["status"] == "executed"

    def prepare(**entities):
        body = client.post("/api/intents/prepare", json={"intent_type": "INT-107", "entities": {"asset_id": asset_id, **entities}}).json()
        return body, {f["key"]: f for f in body["fields"]}

    # any other change works on the installed product, locked
    body, fields = prepare(asset_operation="modify")
    assert body["values"]["offering_name"] == "Home Fiber" and fields["offering_name"]["locked"]
    assert fields["promotion"]["show_when"] == {"field": "asset_operation", "in": ["promotion_change"]}
    # a product change asks for the new product - never the one installed
    body, fields = prepare(asset_operation="product_change")
    assert not body["values"].get("offering_name") and "offering_name" in body["missing"] and not body["ready"]
    assert fields["offering_name"]["label"] == "New product"
    assert "Home Fiber" not in [o["value"] for o in fields["offering_name"]["options"]]
    body, _ = prepare(asset_operation="product_change", offering_name="Home Fiber")
    assert not body["values"].get("offering_name")
    # the picked product stays picked
    body, fields = prepare(asset_operation="product_change", offering_name="Home Fiber Max")
    assert body["values"]["offering_name"] == "Home Fiber Max" and body["ready"], body
    # ...and its options are the new product's, not the installed one's
    offered = [o["value"] for o in fields["pricing_attributes"]["options"]]
    assert "Speed tier" in offered and "Bandwidth" not in offered, offered
    done = invoke(client, "INT-107", **{k: v for k, v in body["values"].items() if v not in (None, "", [], {})})
    assert done["status"] == "executed" and done["result"]["new_monthly"] == 800, done


def test_a_product_change_is_a_delta_order_verified_before_submit(client):
    """Siebel's product change: the form pre-configures the new product's
    line and verifies it (it said Ready, then failed with "Upload is
    required; VLAN ID is required"); the change order deletes the old
    product's line and adds the new one's; fulfilment completes both."""
    from app.models.order_mgmt import OmLine

    asset_id, _, _ = _installed(client)
    _product(client, "Biz Broadband", 900, bandwidth=False)
    assert say(client, "Design Biz Broadband: Contract term: 12 months, 24 months -5% default 12 months; "
                       "technical: Download: 100 Mbps, 200 Mbps required default 100 Mbps; Upload: 50 Mbps, 100 Mbps, 200 Mbps required; "
                       "VLAN ID required number; if Download is 200 Mbps, Upload must be 200 Mbps")["status"] == "executed"

    def prepare(**entities):
        return client.post("/api/intents/prepare", json={"intent_type": "INT-107", "entities": {
            "asset_id": asset_id, "asset_operation": "product_change", "offering_name": "Biz Broadband", **entities}}).json()

    body = prepare()
    assert not body["ready"] and {b["message"] for b in body["blockers"]} >= {"Biz Broadband: Upload is required",
                                                                                 "Biz Broadband: VLAN ID is required"}, body
    assert body["values"]["technical_attributes"] == {"Download": "100 Mbps"}  # the new product's defaults
    assert body["values"]["pricing_attributes"] == {"Contract term": "12 months"}
    body = prepare(technical_attributes={"Download": "200 Mbps", "Upload": "100 Mbps", "VLAN ID": "12"})
    assert [b["message"] for b in body["blockers"]] == ["Biz Broadband: Upload must be 200 Mbps when Download 200 Mbps"]
    body = prepare(technical_attributes={"Download": "200 Mbps", "Upload": "200 Mbps", "VLAN ID": "12"})
    assert body["ready"], body

    done = invoke(client, "INT-107", **{k: v for k, v in body["values"].items() if v not in (None, "", [], {})})
    assert done["status"] == "executed", done["rationale"]
    assert done["result"]["lines"] == [{"action": "Delete", "product": "Home Fiber", "monthly": 500.0},
                                       {"action": "Add", "product": "Biz Broadband", "monthly": 900.0}]
    order_id = done["result"]["order_id"]

    def lines():
        with client.TestSessionLocal() as db:
            return [(r.product_name, r.action_code, r.status) for r in
                    db.query(OmLine).filter_by(context_type="order", context_id=order_id).order_by(OmLine.line_number)]

    assert lines() == [("Home Fiber", "Delete", "open"), ("Biz Broadband", "Add", "open")]
    assert _asset(client, asset_id)[1]["offering_name"] == "Home Fiber"  # unchanged until fulfilled
    _confirm(client, done)
    assert lines() == [("Home Fiber", "Delete", "complete"), ("Biz Broadband", "Add", "complete")]
    assert _asset(client, asset_id)[0] == "decommissioned"
    new_id = _replacement(client, asset_id)
    with client.TestSessionLocal() as db:  # each line points at its asset - the closed one and the new one
        assert [r.asset_id for r in db.query(OmLine).filter_by(context_type="order", context_id=order_id)
                .order_by(OmLine.line_number)] == [asset_id, new_id]
        assert db.get(Order, order_id).status.value == "completed"
    status, data, _ = _asset(client, new_id)
    assert status == "active" and data["offering_name"] == "Biz Broadband" and data["previous_offering"] == "Home Fiber"
    assert data["configuration"]["technical"] == {"Download": "200 Mbps", "Upload": "200 Mbps", "VLAN ID": "12"}


def test_promotion_change(client):
    from app.models.order_mgmt import OmPromotion

    asset_id, order_id, _ = _installed(client)
    with client.TestSessionLocal() as db:
        db.add(OmPromotion(tenant_id=1, code="SUMMER", name="Summer saver", discount_pct=10, active=True))
        db.commit()
    applied = invoke(client, "INT-69", order_id=order_id, promotion="SUMMER")
    assert applied["status"] == "executed", applied
    assert applied["result"]["new_monthly"] == 450 and _asset(client, asset_id)[1]["promotion"]["code"] == "SUMMER"
    removed = say(client, f"Remove the promotion on asset {asset_id}")
    assert removed["entities"]["asset_operation"] == "promotion_change" and removed["entities"]["promotion"] == "none", removed
    _confirm(client, removed)
    assert _asset(client, asset_id)[1]["net_unit_price"] == 500 and _asset(client, asset_id)[1]["promotion"] is None
    assert invoke(client, "INT-107", asset_id=asset_id, asset_operation="promotion_change", promotion="NOPE")["status"] == "failed"


def test_mnp_port_in_and_port_out(client):
    asset_id, _, _ = _installed(client)
    port_in = say(client, f"Port in number 9876543210 from Jio to asset {asset_id}")
    assert port_in["entities"]["asset_operation"] == "number_portability" and port_in["entities"]["donor_operator"] == "Jio", port_in
    _confirm(client, port_in)
    assert _asset(client, asset_id)[1]["portability"] == {"port_number": "9876543210", "donor_operator": "Jio", "direction": "in"}

    port_out = say(client, f"Port out number 9876543210 of asset {asset_id} to Airtel")
    assert port_out["entities"]["asset_operation"] == "port_out" and port_out["entities"]["recipient_operator"] == "Airtel", port_out
    assert port_out["result"]["new_monthly"] == 0
    _confirm(client, port_out)
    status, data, _ = _asset(client, asset_id)
    assert status == "decommissioned" and data["portability"]["direction"] == "out"


def test_relocation_and_account_transfer_make_the_destination_account(client):
    asset_id, _, party = _installed(client)
    before = _asset(client, asset_id)[2]
    moved = say(client, f"Relocate asset {asset_id} to 5 MG Road, Bengaluru")
    assert moved["entities"]["asset_operation"] == "relocate" and moved["entities"]["target_site"] == "5 MG Road, Bengaluru", moved
    # as Siebel does it: disconnect at the old site, connect at the new one
    assert [(line["action"], line["site"]) for line in moved["result"]["lines"]] == [
        ("Delete", moved["result"]["previous_site"]), ("Add", "5 MG Road, Bengaluru")], moved["result"]
    _confirm(client, moved)
    status, old, old_account = _asset(client, asset_id)
    assert status == "decommissioned" and old_account == before and old["moved_to_site"] == "5 MG Road, Bengaluru"
    asset_id = _replacement(client, asset_id)  # a new asset at the new site
    status, data, account = _asset(client, asset_id)
    assert status == "active" and account != before and data["site"] == "5 MG Road, Bengaluru" and data["party_id"] == party
    assert data["previous_service_account_id"] == before

    buyer = invoke(client, "INT-41", name="New owner")["result"]["party_id"]
    transferred = say(client, f"Transfer asset {asset_id} to customer {buyer}")
    assert transferred["entities"]["asset_operation"] == "customer_transfer", transferred
    _confirm(client, transferred)
    assert _asset(client, asset_id)[1]["party_id"] == buyer
    same = invoke(client, "INT-107", asset_id=asset_id, asset_operation="customer_transfer", target_party_id=buyer)
    assert same["status"] == "failed"


def test_an_order_number_finds_its_service_and_the_old_wording_still_routes_as_before(client):
    asset_id, order_id, _ = _installed(client)
    by_order = invoke(client, "INT-107", order_id=order_id, asset_operation="suspend")
    assert by_order["status"] == "executed" and by_order["result"]["asset_id"] == asset_id, by_order
    assert say(client, f"Upgrade this service. order_id: {order_id}, new_price: 1200")["intent_type"] == "INT-08"
    assert say(client, f"Suspend this service. order_id: {order_id}")["intent_type"] == "INT-09"


def test_buttons_on_a_completed_order_and_a_change_order(client):
    asset_id, order_id, _ = _installed(client)
    buttons = client.get(f"/api/records/order/{order_id}").json()["actions"]
    ops = {b["entities"]["asset_operation"] for b in buttons if b["intent"] == "INT-107"}
    assert {"modify", "product_change", "promotion_change", "suspend", "relocate", "customer_transfer", "number_portability",
            "port_out", "terminate"} <= ops
    change = invoke(client, "INT-107", asset_id=asset_id, asset_operation="suspend")["result"]["order_id"]
    on_change = client.get(f"/api/records/order/{change}").json()["actions"]
    assert any(b["intent"] == "INT-108" for b in on_change)


def test_follow_on_order_updates_the_installed_service(client):
    asset_id, order_id, _ = _installed(client)
    follow = invoke(client, "INT-63", order_id=order_id, changes="change Home Fiber to 3")
    assert follow["status"] == "executed", follow
    new_order = follow["result"]["order_id"]
    with client.TestSessionLocal() as db:
        from app.crm_core import abo_service
        order = db.get(Order, new_order)
        abo_service.complete(db, order)  # what fulfilment does when the follow-on order goes live
        db.commit()
    status, data, _ = _asset(client, asset_id)
    assert status == "active" and data["quantity"] == 3
