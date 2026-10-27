"""Configurator engine, model lifecycle, TM Forum APIs and the configurator intents."""

import base64
import json

from test_product_hierarchy import BBI, SHEET, _opportunity, _run

from app.models.orders import Order


def _setup(client, publish=True):
    data = base64.b64encode(SHEET.encode()).decode()
    assert client.post("/api/admin/product-model/import/file", json={"file_name": "bbi.csv", "content_base64": data, "dry_run": False}).json()["errors"] == []
    if publish:
        client.post(f"/api/admin/product-model/{BBI}/publish")
    for text in ("if Access Medium is DSL then Bandwidth must be at most 2048", "CPE requires CPE Type", "CPE recommends BBI-NRC"):
        r = client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "constraint", "text": text})
        assert r.status_code == 200, r.text


def _session(client, picks, **extra):
    r = client.post(f"/api/configurator/{BBI}/session", json={"picks": picks, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _var(result, name):
    return next(v for v in result["variables"] if v["name"] == name)


def test_session_narrows_explains_repairs_and_completes(client):
    _setup(client)
    empty = _session(client, {})
    assert empty["valid"] and _var(empty, "Bandwidth")["range"] == {"min": 512, "max": 4096}
    assert _var(empty, "BBI-MRC")["values"] == [{"value": "1", "state": "selected"}]  # mandatory child, pre-populated

    dsl = _session(client, {"Access Medium": "DSL", "CPE": "Yes"})
    bandwidth = _var(dsl, "Bandwidth")
    assert bandwidth["range"] == {"min": 512, "max": 2048}
    excluded = next(v for v in bandwidth["values"] if v["value"] == "4096 Kbps")
    assert excluded["state"] == "excluded" and "at most 2048" in excluded["reason"] and "Access Medium = DSL" in excluded["reason"]
    assert _var(dsl, "CPE Type")["required"] is True  # CPE requires CPE Type: now mandatory
    assert any("BBI-NRC" in m for m in dsl["messages"])  # CPE recommends BBI-NRC (soft)

    clash = _session(client, {"Access Medium": "DSL", "Bandwidth": "4 Mbps"})
    assert not clash["valid"] and {p["name"] for p in clash["conflict"]["picks"]} == {"Access Medium", "Bandwidth"}
    assert clash["conflict"]["rules"][0]["label"].startswith("if Access Medium is DSL")
    assert clash["conflict"]["repairs"] and all(r["changes_count"] == 1 for r in clash["conflict"]["repairs"])

    cheapest = _session(client, {"Access Medium": "Fiber"}, objective="cost")
    assert cheapest["complete"] and cheapest["instance"]["Bandwidth"] == "512 Kbps" and cheapest["price"]["monthly"] == 1500 + 500 + 256

    why = client.post(f"/api/configurator/{BBI}/explain", json={"picks": {"Access Medium": "DSL"}, "name": "Bandwidth", "value": "4096 Kbps"}).json()
    assert not why["possible"] and "2048" in why["why"]

    ai = client.post(f"/api/configurator/{BBI}/ai", json={"text": "Fiber office line with a CPE, 2 Mbps, budget 4000"}).json()
    assert ai["valid"] and ai["instance"]["Access Medium"] == "Fiber" and ai["instance"]["CPE"] == "1" and ai["instance"]["CPE Type"]
    assert ai["price"]["monthly"] <= 4000


def test_slots_classes_resources(client):
    _setup(client)
    for name, price in (("Router Basic", 200), ("Router Pro", 450)):
        client.post("/api/intents", json={"text": f"Launch a new bundle. name: {name}, composition: Fiber500, segment: B2B, channel: B2B, price: {price}, discount: 0"})
    cls = client.post("/api/admin/product-classes", json={"name": "Routers", "members": ["Router Basic", "Router Pro"]})
    assert cls.json()["all_members"] == ["Router Basic", "Router Pro"]
    client.post("/api/admin/product-classes", json={"name": "Broadband", "members": [BBI]})
    client.post("/api/admin/product-classes/Broadband/attributes", json={"name": "Service Level", "values": ["Gold", "Silver"], "affects_price": True})
    client.post(f"/api/admin/product-model/{BBI}/components", json={"name": "Router", "mandatory": True})
    slot = client.put(f"/api/admin/product-model/{BBI}/components/Router/slot", json={"domain_type": "class", "class_name": "Routers", "default": "Router Basic"})
    assert slot.status_code == 200, slot.text
    client.post(f"/api/admin/product-model/{BBI}/components", json={"name": "Static IP Block", "max_qty": 8})
    for text in ("CPE provides 2 LAN ports", "each Static IP Block uses 1 LAN port", "if Bandwidth is more than 2048 then Router is Router Pro"):
        r = client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "constraint", "text": text})
        assert r.status_code == 200, (text, r.text)

    base = _session(client, {"Access Medium": "Fiber"})
    router = _var(base, "Router")
    assert router["kind"] == "slot" and {v["value"]: v.get("price") for v in router["values"]} == {"Router Basic": 200, "Router Pro": 450}
    assert "Service Level" in {v["name"] for v in base["variables"]}  # inherited from the class
    fast = _session(client, {"Access Medium": "Fiber", "Bandwidth": "4 Mbps"})
    assert fast["instance"]["Router"] == "Router Pro" and any(a["name"] == "Router" for a in fast["auto"])  # forced = auto-added
    over = _session(client, {"Access Medium": "Fiber", "CPE": "Yes", "Static IP Block": "3"})
    assert not over["valid"] and "LAN" in over["conflict"]["message"] or "LAN" in str(over["conflict"]["rules"])
    ok = _session(client, {"Access Medium": "Fiber", "CPE": "Yes", "CPE Type": "Customer", "Static IP Block": "2"})
    assert ok["valid"] and ok["resources"]["LAN port"] == {"provided": 2, "used": 2}


def test_release_versions_tests_and_tmf(client):
    _setup(client)
    test = client.post(f"/api/admin/product-model/{BBI}/tests", json={
        "name": "Fiber 2 Mbps office", "picks": {"Access Medium": "Fiber", "Bandwidth": "2 Mbps", "Contention Ratio": "1:10"},
        "expect": "valid", "expect_price": 1500 + 500 + 1024})
    assert test.status_code == 200
    client.post(f"/api/admin/product-model/{BBI}/tests", json={"name": "DSL can't do 4 Mbps", "picks": {"Access Medium": "DSL", "Bandwidth": "4 Mbps"}, "expect": "invalid"})
    assert all(t["passed"] for t in client.get(f"/api/admin/product-model/{BBI}/tests").json())
    released = _run(client, "INT-48", offering_name=BBI, notes="first release")
    assert released["status"] == "executed" and released["result"]["model_version"] == 1, released

    # a workspace change does not reach selling until it is released
    client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "constraint", "text": "if Access Medium is Fiber then CPE is required"})
    assert _session(client, {"Access Medium": "Fiber"})["valid"] and _session(client, {"Access Medium": "Fiber"})["instance"].get("CPE") == "0"
    assert _session(client, {"Access Medium": "Fiber"}, workspace=True)["instance"]["CPE"] == "1"
    check = client.get(f"/api/admin/product-model/{BBI}/release-check").json()
    assert check["ready"] and check["tests"][0]["passed"]
    assert client.post(f"/api/admin/product-model/{BBI}/release", json={"notes": "fiber needs a CPE"}).json()["version"] == 2
    assert _session(client, {"Access Medium": "Fiber"})["instance"]["CPE"] == "1"
    versions = client.get(f"/api/admin/product-model/{BBI}/versions").json()
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["current"]

    # a failing test blocks the release
    client.post(f"/api/admin/product-model/{BBI}/tests", json={"name": "wrong on purpose", "picks": {"Access Medium": "Fiber"}, "expect": "invalid"})
    blocked = client.post(f"/api/admin/product-model/{BBI}/release", json={})
    assert blocked.status_code == 400 and "wrong on purpose" in blocked.text
    exported = client.get(f"/api/admin/product-model/{BBI}/model.json", params={"version": 1}).json()
    assert exported["format"] == "brt360-product-model/1" and len(exported["constraints"]) == 3

    check760 = client.post("/tmf-api/productConfigurationManagement/v5/checkProductConfiguration", json={
        "checkProductConfigurationItem": [{"productConfiguration": {"productOffering": {"name": BBI},
                                                                    "productCharacteristic": [{"name": "Access Medium", "value": "DSL"},
                                                                                              {"name": "Bandwidth", "value": "4 Mbps"}]}}]}).json()
    item = check760["checkProductConfigurationItem"][0]
    assert item["state"] == "rejected" and item["alternateProductConfigurationProposal"]
    query760 = client.post("/tmf-api/productConfigurationManagement/v5/queryProductConfiguration", json={
        "requestProductConfigurationItem": [{"productConfiguration": {"productOffering": {"name": BBI},
                                                                      "productCharacteristic": [{"name": "Access Medium", "value": "DSL"}]}}]}).json()
    chars = {c["name"]: c for c in query760["computedProductConfigurationItem"][0]["productConfiguration"]["configurationCharacteristic"]}
    assert {v["value"]: v["isSelectable"] for v in chars["Bandwidth"]["configurationCharacteristicValue"]}["4096 Kbps"] is False

    client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "eligibility", "text": "only for B2B customers"})
    consumer = _run(client, "INT-15", name="Home User", segment="B2C")["result"]["party_id"]
    poq = client.post("/tmf-api/productOfferingQualification/v5/checkProductOfferingQualification", json={
        "relatedParty": [{"id": str(consumer)}], "checkProductOfferingQualificationItem": [{"productOffering": {"name": BBI}}]}).json()
    assert poq["checkProductOfferingQualificationItem"][0]["qualificationItemResult"] == "unqualified"
    qualify = _run(client, "INT-49", party_id=consumer)
    assert qualify["status"] == "executed" and BBI in qualify["result"]["not_eligible"]


def test_release_check_counts_installed_services_a_new_rule_would_break(client):
    _setup(client)
    _, opp = _opportunity(client, name="Installed Base Co")
    quote = _run(client, "INT-05", opportunity_id=opp, offering_name=BBI,
                 pricing_attributes="Contention Ratio: 1:10, Access Medium: Fiber, Bandwidth: 4 Mbps, CPE: Yes, CPE Type: Provider")
    _run(client, "INT-06", quote_id=quote["result"]["quote_id"], decision="approve")
    client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote['result']['quote_id']}"})
    with client.TestSessionLocal() as db:
        order = db.query(Order).filter(Order.quote_id == quote["result"]["quote_id"]).one()
        assert order.status.value == "completed"  # its service lives on as an asset
        order_id = order.id

    client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "constraint",
                                                              "text": "if Access Medium is Fiber then Bandwidth must be at most 2048"})
    regression = client.get(f"/api/admin/product-model/{BBI}/release-check").json()["regression"]
    assert regression["assets_checked"] >= 1
    assert [a["order_id"] for a in regression["assets_affected"] if a.get("asset_id")] == [order_id]


def test_configure_with_ai_and_reconfigure_a_live_service(client):
    _setup(client)
    party_id, opp = _opportunity(client)
    ai = _run(client, "INT-47", opportunity_id=opp, offering_name=BBI, need="Fiber line, 2 Mbps, contention 1:10, with a CPE", max_monthly=5000)
    assert ai["status"] == "executed", ai
    assert ai["result"]["configuration"]["Access Medium"] == "Fiber" and ai["result"]["configuration"]["CPE"] == "1"
    assert ai["result"]["stage"] == "configured"  # next: Presales checks feasibility, as the B2B process says
    _, opp = _opportunity(client, name="Live Service Co")
    quote = _run(client, "INT-05", opportunity_id=opp, offering_name=BBI,
                 pricing_attributes="Contention Ratio: 1:10, Access Medium: Fiber, Bandwidth: 2 Mbps, CPE: Yes, CPE Type: Provider")
    assert quote["status"] == "executed", quote
    _run(client, "INT-06", quote_id=quote["result"]["quote_id"], decision="approve")
    client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote['result']['quote_id']}"})
    db = client.TestSessionLocal()
    order = db.query(Order).filter(Order.quote_id == quote["result"]["quote_id"]).one()
    assert order.status.value == "completed"
    order_id, before = order.id, order.monthly_price
    # the service's mandatory technical data, as presales and fulfilment record them (skipped by this
    # quote-to-activation shortcut) - a change to the installed service re-checks them
    from app.crm_core import abo_service
    asset = abo_service.asset_of_order(db, order.tenant_id, order_id)
    instance = json.loads(asset.instance_json)
    instance["configuration"]["technical"].update({"Use Existing Media": "No", "POP Location": "Mumbai POP 1",
                                                   "Access Switch": "SW-01", "Access Port": "Gi0/1"})
    asset.instance_json = json.dumps(instance)
    asset_id = asset.id
    db.commit()
    db.close()

    change = _run(client, "INT-50", order_id=order_id, pricing_attributes={"Bandwidth": "4 Mbps"})
    assert change["status"] == "executed", change.get("error")
    assert change["result"]["price_change"] == 1024 and change["result"]["new_monthly_price"] == before + 1024
    delta = {d["element"]: d["action"] for d in change["result"]["provisioning_delta"]}
    assert delta == {"Bandwidth": "update"}
    # the live service (asset) changed through a change order; the order that installed it did not
    assert change["result"]["asset_id"] == asset_id and change["result"]["change_order_id"] != order_id
    db = client.TestSessionLocal()
    assert db.get(Order, order_id).status.value == "completed" and db.get(Order, order_id).monthly_price == before
    db.close()
    refused = _run(client, "INT-50", order_id=order_id, pricing_attributes={"Access Medium": "DSL"})
    assert refused["status"] == "failed" and "at most 2048" in str(refused)
