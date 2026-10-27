"""Inventory - the network record and its link to Fulfillment:
schema rules (containment, unique identifiers and attributes, lifecycle transitions), SOM
allocation from the location serving the order (network resources only there, stock from
the nearest store), pools, activation into a service record, impact, fallout when nothing
is free and retry once it is, release, reconciliation with source precedence, the
inventory intents (vanilla, so flows and custom intents can run them too), and the AI
reader for questions and item types."""

from types import SimpleNamespace

from tests.test_fulfillment_orchestration import approve_order, close_next_field_job, intent, standard_pattern, tasks_of
from tests.test_phase5_fulfillment import _generated_order

from app.crm_core import inventory_service as inv
from app.models.crm import OpportunityConfig, Quote
from app.models.inventory import InvItem, InvRelationship
from app.models.orders import Order

ADDRESS = "12 80 Feet Road, Koramangala, Bangalore 560034"


def sample(client):
    out = client.post("/api/inventory/sample")
    assert out.status_code == 200, out.text
    return out.json()


def item(client, ref):
    db = client.TestSessionLocal()
    try:
        row = db.query(InvItem).filter((InvItem.identifier == ref) | (InvItem.name == ref)).first()
        return row.id if row else None
    finally:
        db.close()


def at_address(client, order_id, address=ADDRESS):
    """The order's site address - where Inventory finds the location that serves it."""
    db = client.TestSessionLocal()
    try:
        order = db.get(Order, order_id)
        quote = db.get(Quote, order.quote_id)
        config = db.query(OpportunityConfig).filter(OpportunityConfig.opportunity_id == quote.opportunity_id).first()
        if config is None:
            config = OpportunityConfig(opportunity_id=quote.opportunity_id)
            db.add(config)
        config.site_address = address
        db.commit()
    finally:
        db.close()


def held(client, order_id):
    return {i["type_code"]: i for i in client.get(f"/api/inventory/orders/{order_id}").json()}


def test_schema_rules(client):
    loaded = sample(client)
    assert loaded["items"] > 60 and loaded["pools"] == 7
    assert client.post("/api/inventory/sample").status_code == 400  # once
    types = {t["code"]: t for t in client.get("/api/inventory/types").json()}
    assert {"exchange", "olt", "port", "ont", "vlan", "service"} <= set(types)
    assert {a["key"] for a in types["olt"]["all_attributes"]} >= {"management_ip", "software", "slots"}  # inherited from network device

    store = item(client, "BLR-STORE")
    bad = client.post("/api/inventory/items", json={"type_code": "port", "identifier": "P-1", "parent": store})
    assert bad.status_code == 400 and "can't sit in" in bad.json()["detail"]
    dup = client.post("/api/inventory/items", json={"type_code": "ont", "identifier": "ONT-BLR-0001", "parent": store})
    assert dup.status_code == 400 and "already recorded" in dup.json()["detail"]
    wrong = client.post("/api/inventory/items", json={"type_code": "splitter", "parent": item(client, "BLR-CAB-17"), "attributes": {"ratio": "1:3"}})
    assert wrong.status_code == 400 and "Split ratio must be one of" in wrong.json()["detail"]
    named = client.post("/api/inventory/items", json={"type_code": "splitter", "parent": item(client, "BLR-CAB-17"), "attributes": {"ratio": "1:16"}})
    assert named.status_code == 201 and named.json()["item"]["identifier"].startswith("BLR-CAB-17-SPL-")  # the type's naming rule
    mac = client.post("/api/inventory/items", json={"type_code": "ont", "identifier": "ONT-X", "parent": store,
                                                     "attributes": {"mac": client.get(f"/api/inventory/items/{item(client, 'ONT-BLR-0001')}").json()["item"]["attributes"]["mac"]}})
    assert mac.status_code == 400 and "already recorded on another ONT" in mac.json()["detail"]

    olt = item(client, "BLR-EX-01-OLT-01")
    retire = client.post(f"/api/inventory/items/{olt}/lifecycle", json={"to": "retired"})
    assert retire.status_code == 400 and "still holds" in retire.json()["detail"]
    planned = client.post(f"/api/inventory/items/{item(client, 'ONT-BLR-0002')}/lifecycle", json={"to": "planned"})
    assert planned.status_code == 400 and "can move to" in planned.json()["detail"]
    detail = client.get(f"/api/inventory/items/{olt}").json()
    assert [p["identifier"] for p in detail["path"]] == ["BLR", "BLR-EX-01"] and len(detail["children"]) == 2
    assert detail["history"][0]["action"] == "created"

    overview = client.get("/api/inventory/overview").json()
    assert overview["health"]["in_use"] and overview["health"]["score"] > 80
    ports = next(r for r in overview["capacity"]["equipment"] if r["resource_type"] == "port")
    assert ports["total"] == 32 and ports["available"] == 32


def test_fulfillment_allocates_from_inventory(client):
    sample(client)
    standard_pattern(client, products="Inv Fiber 1G")
    order_id = _generated_order(client, "Inventory Customer Ltd", "Inv Fiber 1G")
    at_address(client, order_id)
    approve_order(client, order_id)

    som = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]["children"][0]
    design, assign, reserve = (t["response"] for t in som["tasks"][:3])
    assert design["system"] == "BRT 360 Inventory" and design["location"] == "BLR-EX-01"
    by_type = {r["type"]: r for r in design["resources"] + assign["resources"] + reserve["resources"]}
    assert by_type["service_id"]["identifier"] == "SVC-000001" and by_type["circuit_id"]["identifier"] == "CKT/BLR/00001"
    assert by_type["vlan"]["identifier"] == "100" and by_type["vlan"]["location"] == "BLR-EX-01"  # the exchange's own VLAN pool
    assert by_type["ip_block"]["identifier"] == "10.20.0.0/29"
    assert by_type["port"]["identifier"].startswith("BLR-EX-01-OLT-01/")  # a port at the exchange serving the address
    assert by_type["ont"]["identifier"] == "ONT-BLR-0001" and by_type["ont"]["location"] == "BLR-STORE"  # stock from the store above it

    # SOM activated: everything the order holds is in use, on its service record
    holding = held(client, order_id)
    assert {k: v["lifecycle"] for k, v in holding.items()} == {"service": "in_use", "circuit_id": "in_use", "vlan": "in_use", "ip_block": "in_use",
                                                              "port": "in_use", "ont": "in_use"}
    db = client.TestSessionLocal()
    try:
        service = db.get(InvItem, holding["service"]["id"])
        used = {r.target_id for r in db.query(InvRelationship).filter(InvRelationship.source_id == service.id, InvRelationship.rel_type == "uses")}
        assert used == {v["id"] for k, v in holding.items() if k != "service"}
    finally:
        db.close()

    # the OLT going down takes this customer with it
    hit = client.get(f"/api/inventory/impact/{item(client, 'BLR-EX-01-OLT-01')}").json()
    assert [s["identifier"] for s in hit["services"]] == ["SVC-000001"] and hit["customers"][0]["name"] == "Inventory Customer Ltd"
    assert hit["orders"] == [order_id]
    other = client.get(f"/api/inventory/impact/{item(client, 'BLR-EX-02-OLT-01')}").json()
    assert other["services"] == []

    # maintenance on equipment a service uses needs a reason
    olt = item(client, "BLR-EX-01-OLT-01")
    assert client.post(f"/api/inventory/items/{olt}/lifecycle", json={"to": "maintenance"}).status_code == 400
    assert client.post(f"/api/inventory/items/{olt}/lifecycle", json={"to": "maintenance", "note": "firmware"}).status_code == 200

    # the field closes the TOM jobs; COM finishes and the service record learns its CRM assets
    for _ in range(3):
        close_next_field_job(client, order_id)
    assert tasks_of(client, "COM", order_id)[0] == "completed"
    db = client.TestSessionLocal()
    try:
        assert db.get(InvItem, holding["service"]["id"]).attributes_json.count("crm_assets") == 1
    finally:
        db.close()


def test_no_free_port_is_fallout_until_there_is_one(client):
    sample(client)
    standard_pattern(client, products="Inv Waiting Fiber")
    olt = item(client, "BLR-EX-01-OLT-01")
    assert client.post(f"/api/inventory/items/{olt}/lifecycle", json={"to": "maintenance", "note": "rebuild"}).status_code == 200
    order_id = _generated_order(client, "Waiting Port Co", "Inv Waiting Fiber")
    at_address(client, order_id)
    approve_order(client, order_id)
    status, som = tasks_of(client, "SOM", order_id)
    assert status == "fallout" and som["s3"] == "failed"  # reserve physical: the only OLT serving the address is in maintenance
    task = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]["children"][0]["tasks"][2]
    assert "no free port at BLR-EX-01" in task["error"]["message"]  # never a port from the other exchange
    assert "port" not in held(client, order_id)  # the failed step's allocations rolled back

    assert client.post(f"/api/inventory/items/{olt}/lifecycle", json={"to": "available"}).status_code == 200
    retried = intent(client, "INT-118", order_id=order_id)
    assert retried["status"] == "executed", retried
    assert tasks_of(client, "SOM", order_id)[1]["s3"] == "completed"
    assert held(client, order_id)["port"]["lifecycle"] == "in_use"


def test_release_returns_everything_and_pools_refill(client):
    sample(client)
    standard_pattern(client, products="Inv Release Fiber")
    order_id = _generated_order(client, "Release Co", "Inv Release Fiber")
    at_address(client, order_id)
    approve_order(client, order_id)
    vlans = next(p for p in client.get("/api/inventory/pools").json() if p["name"] == "VLANs BLR-EX-01")
    assert vlans["used"] == 1
    port_id = held(client, order_id)["port"]["id"]

    released = intent(client, "INT-124", order_id=order_id, note="order cancelled")
    assert released["status"] == "executed" and released["result"]["count"] == 6, released
    assert held(client, order_id) == {}
    assert next(p for p in client.get("/api/inventory/pools").json() if p["name"] == "VLANs BLR-EX-01")["used"] == 0
    port = client.get(f"/api/inventory/items/{port_id}").json()
    assert port["item"]["lifecycle"] == "available" and port["history"][0]["action"] == "released"
    assert intent(client, "INT-124", order_id=order_id)["status"] != "executed"  # nothing left to release


def test_reservations_expire_unless_an_order_still_needs_them(client):
    import datetime as dt

    sample(client)
    order_id = _generated_order(client, "Expiring Co", "Inv Expiring Fiber")
    at_address(client, order_id)
    reserved = intent(client, "INT-123", order_id=order_id, resources=["port", "vlan"])
    assert reserved["status"] == "executed" and reserved["result"]["location"] == "BLR-EX-01", reserved
    db = client.TestSessionLocal()
    try:
        assert inv.expire_reservations(db) == 0  # still within their time
        later = dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(days=inv.RESERVATION_DAYS + 1)
        assert inv.expire_reservations(db, later) == 2  # no open fulfillment holds them
        db.commit()
    finally:
        db.close()
    assert held(client, order_id) == {}


def test_inventory_intents(client):
    sample(client)
    found = client.post("/api/intents", json={"text": "Free GPON ports at BLR-EX-02"}).json()
    assert found["intent_type"] == "INT-120" and found["status"] == "executed", found
    assert found["result"]["count"] == 16 and found["result"]["filter"] == {"lifecycle": "available", "type": "port", "location": "BLR-EX-02",
                                                                           "attributes": {"speed": "GPON"}}
    stock = client.post("/api/intents", json={"text": "Which ONTs are in stock in the Bangalore store?"}).json()
    assert stock["result"]["count"] == 12 and stock["result"]["filter"]["location"] == "BLR-STORE"

    received = client.post("/api/intents", json={"text": "Receive 3 Nokia SIMs into BLR-STORE"}).json()
    assert received["intent_type"] == "INT-122" and received["result"]["received"] == 3, received
    capacity = client.post("/api/intents", json={"text": "Check capacity at BLR-STORE"}).json()
    assert "sim: 3 free of 3" in capacity["result"]["summary"], capacity

    pool = client.post("/api/intents", json={"text": "Create a VLAN pool 1000-1099 at BLR-EX-02"}).json()
    assert pool["intent_type"] == "INT-127" and pool["result"]["capacity"] == 100, pool
    ip = client.post("/api/intents", json={"text": "Create an IP pool 10.30.0.0/24 in /29 blocks"}).json()
    assert ip["result"]["capacity"] == 32, ip

    down = client.post("/api/intents", json={"text": "Mark BLR-EX-02-OLT-01 down"}).json()
    assert down["intent_type"] == "INT-125" and down["result"]["oper_status"] == "down", down
    impact = client.post("/api/intents", json={"text": "What is affected if BLR-EX-01 goes down?"}).json()
    assert impact["intent_type"] == "INT-126" and impact["result"]["affected_items"] == 30, impact  # exchange, OLT, 2 cards, 16 ports, cabinet, splitter, 8 legs

    designed = client.post("/api/intents", json={"text": "Design an item type: a DSLAM is a network device that sits in an exchange or cabinet, "
                                                         "with a port count (required), firmware and a line type (ADSL, VDSL)"}).json()
    assert designed["intent_type"] == "INT-128" and designed["status"] == "executed", designed
    dslam = next(t for t in client.get("/api/inventory/types").json() if t["code"] == "dslam")
    assert dslam["parent_code"] == "network_device" and dslam["contained_in"] == ["exchange", "cabinet"]
    attrs = {a["key"]: a for a in dslam["attributes"]}
    assert attrs["port_count"]["type"] == "number" and attrs["port_count"]["required"] and attrs["line_type"]["values"] == ["ADSL", "VDSL"]


def test_reconciliation_dry_run_precedence_and_missing(client):
    sample(client)
    ont = item(client, "ONT-BLR-0003")
    assert client.put(f"/api/inventory/items/{ont}", json={"attributes": {"mac": "aa:aa:aa:aa:aa:aa"}}).status_code == 200  # fixed by hand
    report = {"source": "nms-scan", "full_scan_of": "BLR-EX-02", "items": [
        {"type": "card", "identifier": "BLR-EX-02-OLT-01/3", "parent": "BLR-EX-02-OLT-01", "model": "GPHF", "oper_status": "up"},
        {"type": "port", "identifier": "BLR-EX-02-OLT-01/3/1", "parent": "BLR-EX-02-OLT-01/3", "attributes": {"speed": "XGS-PON"}},
        {"type": "olt", "identifier": "BLR-EX-02-OLT-01", "attributes": {"software": "R022"}, "oper_status": "degraded"},
        {"type": "ont", "identifier": "ONT-BLR-0003", "attributes": {"mac": "bb:bb:bb:bb:bb:bb"}},
        {"type": "toaster", "identifier": "T-1"},
    ]}
    dry = client.post("/api/inventory/reconcile", json=report).json()
    assert dry["dry_run"] and dry["summary"]["created"] == 2 and dry["summary"]["rejected"] == 1
    assert item(client, "BLR-EX-02-OLT-01/3") is None  # a dry run changes nothing

    done = client.post("/api/inventory/reconcile", json={**report, "apply": True}).json()
    summary = done["summary"]
    assert (summary["created"], summary["updated"], summary["rejected"]) == (2, 1, 1), done
    assert summary["missing"] == 18  # the 2 recorded cards and their 16 ports the scan of BLR-EX-02 no longer reports
    olt = client.get(f"/api/inventory/items/{item(client, 'BLR-EX-02-OLT-01')}").json()["item"]
    assert olt["attributes"]["software"] == "R022" and olt["oper_status"] == "degraded" and olt["sources"]["software"] == "discovery"
    kept = client.get(f"/api/inventory/items/{ont}").json()["item"]
    assert kept["attributes"]["mac"] == "aa:aa:aa:aa:aa:aa"  # discovery never overwrites a value set by hand
    assert any(r["source"] == "nms-scan" and not r["dry_run"] for r in client.get("/api/inventory/reconcile/runs").json())


def test_the_model_answers_within_the_schema(client, monkeypatch):
    from app.crm_core import llm

    sample(client)
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: SimpleNamespace(label="own model (test)"))
    monkeypatch.setattr(llm, "chat_json", lambda target, system, user: {"type": "ont", "lifecycle": "available", "location": "Atlantis",
                                                                       "read_as": "ONTs in stock"})
    asked = client.post("/api/inventory/ask", json={"question": "spare ONTs please"}).json()
    assert asked["source"] == "llm" and asked["filter"] == {"type": "ont", "lifecycle": "available"}  # an invented location is dropped
    assert len(asked["results"]) == 12
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"code": "bng", "name": "BNG", "kind": "physical", "parent_code": "nonsense",
                                                           "attributes": [{"key": "sessions", "type": "number"}, {"key": "tier", "type": "picklist"}],
                                                           "contained_in": ["exchange", "moon"]})
    drafted = client.post("/api/inventory/types/draft", json={"description": "a broadband network gateway in an exchange"}).json()
    assert drafted["source"] == "llm" and drafted["type"]["parent_code"] == "" and drafted["type"]["contained_in"] == ["exchange"]
    assert [a["type"] for a in drafted["type"]["attributes"]] == ["number", "text"]  # a picklist without values becomes text


def test_tenants_without_inventory_keep_the_stand_in(client):
    standard_pattern(client, products="Inv None Fiber")
    order_id = _generated_order(client, "No Inventory Co", "Inv None Fiber")
    approve_order(client, order_id)
    som = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]["children"][0]
    assert "system" not in som["tasks"][0]["response"] and som["tasks"][2]["status"] == "completed"
    assert held(client, order_id) == {}
