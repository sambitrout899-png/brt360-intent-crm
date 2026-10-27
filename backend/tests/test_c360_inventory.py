"""Customer 360 x Network Inventory: a customer's 360 shows the network their services really run on - the
service records, their paths and resources, the elements serving them and their live state - insights and
the health score react to equipment in trouble, each installed asset knows its service, an external network
system still wins where one is connected, and tenants without inventory see what they saw before."""

from tests.test_fulfillment_orchestration import approve_order, close_next_field_job, standard_pattern
from tests.test_inventory import at_address, item, sample
from tests.test_phase5_fulfillment import _generated_order

from app.intent_engine import classifier
from app.models.orders import Order


def _party(client, order_id):
    db = client.TestSessionLocal()
    try:
        return db.get(Order, order_id).party_id
    finally:
        db.close()


def _widgets(view):
    return {w["recipe"]: w for p in view["panes"] for w in p["widgets"] if w.get("recipe")}


def _fulfilled(client, product, company):
    sample(client)
    standard_pattern(client, products=product)
    order_id = _generated_order(client, company, product)
    at_address(client, order_id)
    approve_order(client, order_id)
    return order_id, _party(client, order_id)


def test_the_360_shows_the_network_the_customer_runs_on(client):
    order_id, party_id = _fulfilled(client, "C360 Inv Fiber", "Network View Ltd")
    view = client.get(f"/api/c360/parties/{party_id}/view").json()
    w = _widgets(view)

    services = w["network.services"]
    assert services["status"] == "ok" and services["source_label"] == "BRT 360 Network Inventory"
    row = services["data"]["rows"][0]
    assert row["service"] == "SVC-000001" and row["oper_status"] == "up" and row["location"] == "BLR-EX-01"
    assert row["path"].startswith("BLR-EX-01-OLT-01 › BLR-EX-01-OLT-01/")  # OLT › card › port

    elements = w["network.elements_table"]
    assert elements["status"] == "ok" and elements["source_label"] == "BRT 360 Network Inventory"  # no connector needed any more
    assert {"BLR-EX-01-OLT-01"} <= {r["element"] for r in elements["data"]["rows"]}
    assert w["network.services_at_risk"]["data"]["value"] == 0  # every element on the path is up

    network = client.get(f"/api/c360/parties/{party_id}/network").json()
    assert network["in_use"] and network["at_risk"] == []
    uses = {r["resource_type"]: r for r in network["services"][0]["uses"]}
    assert {"circuit_id", "vlan", "ip_block", "port", "ont"} <= set(uses)
    assert uses["ont"]["location_path"] == "BLR › BLR-STORE" and uses["port"]["status"] == "in_use"
    assert [d["device_id"] for d in network["devices"]] == ["ONT-BLR-0001"]
    # the resources widget reads Inventory live, not the copy on the order
    resources = client.post("/api/admin/c360/preview/%d" % party_id, json={"layout": {"panes": [{"domain": "network", "widgets": [
        {"recipe": "network.resources"}, {"recipe": "inventory.devices"}]}]}}).json()
    rw = {x["recipe"]: x for p in resources["panes"] for x in p["widgets"]}
    assert {r["identifier"] for r in rw["network.resources"]["data"]["rows"]} >= {"ONT-BLR-0001", "100"}
    assert rw["inventory.devices"]["data"]["rows"][0]["device_id"] == "ONT-BLR-0001"

    # the field closes the TOM jobs: the installed asset knows its service record
    for _ in range(3):
        close_next_field_job(client, order_id)
    network = client.get(f"/api/c360/parties/{party_id}/network").json()
    assets = client.get(f"/api/parties/{party_id}/360").json()["service_accounts"][0]["assets"]
    assert assets and set(network["services"][0]["crm_assets"]) == {a["id"] for a in assets}


def test_equipment_in_trouble_reaches_the_customer(client):
    order_id, party_id = _fulfilled(client, "C360 Risk Fiber", "Risky Network Co")
    before = client.get(f"/api/c360/parties/{party_id}/view").json()
    olt = item(client, "BLR-EX-01-OLT-01")
    assert client.post(f"/api/inventory/items/{olt}/lifecycle", json={"to": "maintenance", "note": "line card swap"}).status_code == 200

    view = client.get(f"/api/c360/parties/{party_id}/view").json()
    risk = next(i for i in view["insights"] if i["title"].startswith("Service SVC-000001 at risk"))
    assert "BLR-EX-01-OLT-01 is in maintenance" in risk["detail"]
    said = classifier._process_step(risk["action"]["text"], None)  # the insight's action is an intent
    assert said.intent_type == "INT-126" and said.entities["item"] == "BLR-EX-01-OLT-01"
    assert view["header"]["health"]["score"] < before["header"]["health"]["score"]
    assert "1 service on equipment in trouble" in view["header"]["health"]["reasons"]
    row = _widgets(view)["network.services"]["data"]["rows"][0]
    assert row["oper_status"] == "degraded"
    at_risk = client.post(f"/api/admin/c360/preview/{party_id}", json={"layout": {"panes": [{"domain": "network", "widgets": [
        {"recipe": "network.services_at_risk"}]}]}}).json()
    assert at_risk["panes"][0]["widgets"][0]["data"]["value"] == 1

    assert client.post(f"/api/inventory/items/{item(client, 'BLR-EX-01-OLT-01/1')}/oper-status", json={"status": "down"}).status_code == 200
    network = client.get(f"/api/c360/parties/{party_id}/network").json()
    reasons = network["at_risk"][0]["reasons"]
    assert any("is down" in r for r in reasons) and network["services"][0]["oper_status"] in ("down", "degraded")


def test_a_connected_network_system_still_wins(client):
    _order_id, party_id = _fulfilled(client, "C360 Conn Fiber", "Connected Network Co")
    created = client.post("/api/admin/c360/connectors", json={"name": "Demo NOC", "domain": "network", "kind": "demo"})
    assert created.status_code in (200, 201), created.text
    view = client.get(f"/api/c360/parties/{party_id}/view").json()
    w = _widgets(view)
    assert w["network.elements_table"]["source_label"] == "Demo NOC"  # the operator's own monitoring answers network health
    assert w["network.services"]["source_label"] == "BRT 360 Network Inventory"  # service paths stay inventory's


def test_without_inventory_nothing_changes(client):
    standard_pattern(client, products="C360 None Fiber")
    order_id = _generated_order(client, "No Inventory View Co", "C360 None Fiber")
    approve_order(client, order_id)
    party_id = _party(client, order_id)
    w = _widgets(client.get(f"/api/c360/parties/{party_id}/view").json())
    assert w["network.elements_table"]["status"] == "not_connected"
    assert w["network.services"]["status"] == "empty" and w["network.services"]["source_label"] == "BRT 360"
    assert client.get(f"/api/c360/parties/{party_id}/network").json() == {"in_use": False, "services": [], "resources": [], "elements": [],
                                                                         "devices": [], "at_risk": []}
