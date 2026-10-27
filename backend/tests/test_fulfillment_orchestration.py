"""Fulfillment - layered order orchestration: a tenant-designed fulfillment pattern takes
an approved CRM order through COM (billing registration) -> SOM (inventory
technical actions, logical + physical resources) -> TOM (Field Service work
orders) and back up: billing starts, installed assets, order complete."""

from tests.test_phase5_fulfillment import _generated_order

from app.models.assurance import Technician, WorkOrder
from app.models.comms import CmBillingProfile
from app.models.field_service import FsActivity
from app.models.fulfillment import Asset
from app.models.orders import Order, Resource, ServiceOrder
from app.models.fulfillment_orders import FulfillmentOrder, FulfillmentTask


def intent(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def standard_pattern(client, products="", **changes):
    pattern = client.post("/api/admin/fulfillment/patterns/template", json={"products": products}).json()
    if changes:
        body = {k: pattern[k] for k in ("name", "description", "products", "segment", "order_type", "priority", "mode", "graph")}
        body.update(changes)
        pattern = client.put(f"/api/admin/fulfillment/patterns/{pattern['id']}", json=body).json()
    assert pattern["validation_errors"] == [], pattern["validation_errors"]
    assert client.post(f"/api/admin/fulfillment/patterns/{pattern['id']}/activate").status_code == 200
    return pattern


def approve_order(client, order_id):
    done = intent(client, "INT-25", order_id=order_id, decision="approve")
    assert done["status"] == "executed", done
    return done


def tasks_of(client, number_prefix, order_id):
    db = client.TestSessionLocal()
    try:
        f_order = db.query(FulfillmentOrder).filter(FulfillmentOrder.crm_order_id == order_id, FulfillmentOrder.layer == number_prefix).one()
        return f_order.status, {t.node_id: t.status for t in db.query(FulfillmentTask).filter(FulfillmentTask.fulfillment_order_id == f_order.id)}
    finally:
        db.close()


def close_next_field_job(client, order_id, signature="Signed by the customer"):
    """An engineer is booked on the order's open TOM work order and closes it in Field Service."""
    db = client.TestSessionLocal()
    try:
        wo = db.query(WorkOrder).filter(WorkOrder.order_id == order_id, WorkOrder.kind == "tom", WorkOrder.status != "complete").one()
        act = db.query(FsActivity).filter(FsActivity.work_order_id == wo.id).one()
        from app.integration import ticket_adapter

        ticket_adapter.assign_technician(db, wo.id, db.query(Technician).first().id)
        db.commit()
        activity_id, subject, sheet = act.id, act.subject, act.instructions
    finally:
        db.close()
    response = client.post(f"/api/field-service/activities/{activity_id}/complete", json={"resolution_code": "done", "signature": signature})
    assert response.status_code == 200, response.text
    return subject, sheet


def test_an_order_runs_com_som_tom_and_back(client):
    standard_pattern(client, products="Fulfil Fiber 1G")
    order_id = _generated_order(client, "Fulfil Customer Ltd", "Fulfil Fiber 1G")
    approved = approve_order(client, order_id)
    assert approved["result"]["fulfillment_order"].startswith("COM-") and approved["result"]["fulfillment_pattern"]

    # COM registered billing and is waiting on SOM; SOM got its inventory answers and waits on TOM; TOM's first job is out
    status, com = tasks_of(client, "COM", order_id)
    assert com == {"c1": "completed", "c2": "completed", "c3": "completed", "c4": "waiting", "c5": "pending", "c6": "pending", "c7": "pending"}
    status, som = tasks_of(client, "SOM", order_id)
    assert som == {"s1": "completed", "s2": "completed", "s3": "completed", "s4": "completed", "s5": "waiting"}
    status, tom = tasks_of(client, "TOM", order_id)
    assert (status, tom) == ("waiting", {"t1": "waiting", "t2": "pending", "t3": "pending"})

    db = client.TestSessionLocal()
    try:
        order = db.get(Order, order_id)
        assert order.status.value == "activating"
        resources = {r.resource_type: r for so in db.query(ServiceOrder).filter(ServiceOrder.order_id == order_id) for r in so.resources}
        assert {"service_id", "circuit_id", "ip_block", "vlan", "port", "ont"} <= set(resources)
        assert all(r.status.value == "active" for r in resources.values())  # activated by SOM
        profile = db.query(CmBillingProfile).filter(CmBillingProfile.party_id == order.party_id).one()
        assert profile.billing_account_id is not None and profile.is_primary
    finally:
        db.close()

    # the field closes installation -> connectivity test -> acceptance, in that order
    subject, sheet = close_next_field_job(client, order_id)
    assert subject.startswith("Installation") and "physical port" in sheet and "logical vlan" in sheet  # the job carries the reserved resources
    assert tasks_of(client, "TOM", order_id)[1] == {"t1": "completed", "t2": "waiting", "t3": "pending"}
    assert close_next_field_job(client, order_id)[0].startswith("Connectivity test")
    assert close_next_field_job(client, order_id)[0].startswith("Customer acceptance signing")

    # TOM -> SOM -> COM: billing started, assets created, order complete
    for layer in ("TOM", "SOM", "COM"):
        status, tasks = tasks_of(client, layer, order_id)
        assert status == "completed" and set(tasks.values()) == {"completed"}, (layer, tasks)
    db = client.TestSessionLocal()
    try:
        order = db.get(Order, order_id)
        assert order.status.value == "completed"
        assert db.query(Asset).filter(Asset.service_account_id == order.service_account_id).count() == len(order.items)
        codes = [m.code for m in order.milestones]
        for code in ("fulfillment_com_opened", "fulfillment_som_opened", "fulfillment_tom_opened", "network_activated", "wo_installation_raised",
                     "wo_acceptance_closed", "billing_started", "assets_created", "live", "fulfillment_com_completed"):
            assert code in codes, (code, codes)
        com_view = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]
        assert com_view["billing"]["customer_ref"].startswith("BCUST-") and com_view["billing"]["billing_profile_ref"].startswith("BPROF-")
        assert {r["kind"] for r in com_view["resources"]} == {"logical", "physical"}
        assert set(com_view["work"]) == {"installation", "connectivity_test", "acceptance"}
        assert com_view["work"]["acceptance"]["signature"] == "Signed by the customer"
    finally:
        db.close()


def test_orders_without_a_matching_pattern_keep_the_classic_chain(client):
    standard_pattern(client, products="Some Other Product")
    order_id = _generated_order(client, "Classic Co", "Classic Fiber")
    approved = approve_order(client, order_id)
    assert "fulfillment_order" not in approved["result"] and approved["result"].get("work_order_id")
    db = client.TestSessionLocal()
    try:
        assert db.query(FulfillmentOrder).filter(FulfillmentOrder.crm_order_id == order_id).count() == 0
        assert db.query(WorkOrder).filter(WorkOrder.order_id == order_id, WorkOrder.kind == "installation").count() == 1
    finally:
        db.close()


def test_inventory_fallout_is_retried_after_the_cause_is_fixed(client):
    standard_pattern(client, products="Fulfil Port Fiber")
    order_id = _generated_order(client, "Port Shortage Ltd", "Fulfil Port Fiber")
    db = client.TestSessionLocal()
    try:  # the site has no free ports
        from app.models.order_mgmt import OmOrderDetail
        from app.crm_core import om_service

        detail = om_service.order_detail(db, db.get(Order, order_id).tenant_id, db.get(Order, order_id))
        detail.ship_to_address = "9 NOPORTS Street"
        db.commit()
    finally:
        db.close()
    approve_order(client, order_id)
    status, som = tasks_of(client, "SOM", order_id)
    assert status == "fallout" and som["s3"] == "failed" and som["s4"] == "pending"
    view = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]
    som_view = view["children"][0]
    failed = next(t for t in som_view["tasks"] if t["node_id"] == "s3")
    assert failed["error"]["code"] in ("NOT_FOUND", "INVALID", "MISSING_INPUT") and "no free port" in failed["error"]["message"]
    assert client.get("/api/fulfillment/orders", params={"status": "fallout"}).json()[0]["crm_order_id"] == order_id

    retried = client.post(f"/api/fulfillment/tasks/{failed['id']}/resolve", json={"action": "retry"})
    assert retried.status_code == 200  # still no ports - still fallout
    assert tasks_of(client, "SOM", order_id)[1]["s3"] == "failed"

    db = client.TestSessionLocal()
    try:
        detail = db.query(OmOrderDetail).filter(OmOrderDetail.order_id == order_id).one()
        detail.ship_to_address = "9 Free Port Street"
        db.commit()
    finally:
        db.close()
    assert client.post(f"/api/fulfillment/tasks/{failed['id']}/resolve", json={"action": "retry"}).status_code == 200
    status, som = tasks_of(client, "SOM", order_id)
    assert som["s3"] == "completed" and som["s5"] == "waiting" and status == "waiting"


def test_async_inventory_waits_for_the_callback(client):
    template = client.post("/api/admin/fulfillment/patterns/template", json={"products": "Fulfil Async Fiber"}).json()
    graph = template["graph"]
    next(n for n in graph["nodes"] if n["id"] == "s3")["config"]["response"] = "async"
    body = {k: template[k] for k in ("name", "description", "products", "segment", "order_type", "priority", "mode")}
    pattern = client.put(f"/api/admin/fulfillment/patterns/{template['id']}", json={**body, "graph": graph}).json()
    client.post(f"/api/admin/fulfillment/patterns/{pattern['id']}/activate")
    order_id = _generated_order(client, "Async Co", "Fulfil Async Fiber")
    approve_order(client, order_id)
    som = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]["children"][0]
    waiting = next(t for t in som["tasks"] if t["node_id"] == "s3")
    assert waiting["status"] == "waiting" and waiting["external_ref"].startswith("INVREQ-")
    answered = client.post("/api/fulfillment/inventory/callback", json={
        "request_id": waiting["external_ref"], "status": "completed",
        "resources": [{"type": "port", "identifier": "OLT-07/1/3/12"}, {"type": "ont", "identifier": "ZTEG12345678"}]})
    assert answered.status_code == 200, answered.text
    assert tasks_of(client, "SOM", order_id)[1]["s3"] == "completed"
    db = client.TestSessionLocal()
    try:
        ids = {r.identifier for so in db.query(ServiceOrder).filter(ServiceOrder.order_id == order_id) for r in so.resources}
        assert {"OLT-07/1/3/12", "ZTEG12345678"} <= ids
    finally:
        db.close()


def test_conditions_skip_steps_an_order_doesnt_need(client):
    template = client.post("/api/admin/fulfillment/patterns/template", json={"products": "Fulfil Cond Fiber"}).json()
    graph = template["graph"]
    survey = next(n for n in graph["nodes"] if n["id"] == "t1")
    survey["config"].update(when_field="order.monthly_price", when_operator="gt", when_value="999999")  # never for this order
    body = {k: template[k] for k in ("name", "description", "products", "segment", "order_type", "priority", "mode")}
    pattern = client.put(f"/api/admin/fulfillment/patterns/{template['id']}", json={**body, "graph": graph}).json()
    client.post(f"/api/admin/fulfillment/patterns/{pattern['id']}/activate")
    order_id = _generated_order(client, "Cond Co", "Fulfil Cond Fiber")
    preview = client.post(f"/api/admin/fulfillment/patterns/{pattern['id']}/preview", json={"order_id": order_id}).json()
    tom = next(l for l in preview["layers"] if l["layer"] == "TOM")
    assert [s["runs"] for s in tom["steps"]] == [False, True, True]
    approve_order(client, order_id)
    assert tasks_of(client, "TOM", order_id)[1] == {"t1": "skipped", "t2": "waiting", "t3": "pending"}


def test_pattern_validation(client):
    bad = {"name": "Broken", "graph": {"nodes": [
        {"id": "a", "layer": "SOM", "function": "sync_customer", "config": {}},
        {"id": "b", "layer": "SOM", "function": "route_to_tom", "config": {}},
        {"id": "c", "layer": "TOM", "function": "field_work_order", "config": {"kind": "teleport"}},
        {"id": "d", "layer": "SOM", "function": "assign_logical", "config": {"resources": "vlan, unicorn"}},
    ], "edges": [{"id": "1", "source": "b", "target": "c"}, {"id": "2", "source": "a", "target": "d"}, {"id": "3", "source": "d", "target": "a"}]}}
    pattern = client.post("/api/admin/fulfillment/patterns", json=bad).json()
    errors = " | ".join(pattern["validation_errors"])
    for expected in ("belongs in the COM lane", "crosses lanes", "go round in a circle", "COM lane needs at least one step",
                     "work order kind must be one of", "unknown resource type(s) unicorn"):
        assert expected in errors, (expected, errors)
    assert client.post(f"/api/admin/fulfillment/patterns/{pattern['id']}/activate").status_code == 400


def test_llm_mode_falls_back_to_the_designed_plan_without_a_model(client):
    standard_pattern(client, products="Fulfil AI Fiber", mode="llm")
    order_id = _generated_order(client, "AI Co", "Fulfil AI Fiber")
    approve_order(client, order_id)
    com = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]
    assert com["planned_by"] == "llm_fallback" and "no AI model configured" in com["plan_note"]
    assert [t["node_id"] for t in com["tasks"]] == ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]


def test_llm_plan_is_validated(client, monkeypatch):
    from app.crm_core import llm
    from app.orchestration import fulfillment_engine

    answers = {"COM": {"include": ["c1", "c2", "c4", "c7"], "depends_on": {"c2": ["c1"], "c4": ["c2"], "c7": ["c4"]},
                       "note": "billing profile exists already; assets and billing handled elsewhere"},
               "SOM": {"include": ["s1"], "note": "dropped the route"},  # invalid: a mandatory route left out
               "TOM": {"include": ["t1", "t3"], "note": "no separate test"}}
    standard_pattern(client, products="Fulfil Planned Fiber", mode="llm")
    order_id = _generated_order(client, "Planned Co", "Fulfil Planned Fiber")
    # a model is available from here on (the setup above ran on the rules)
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: object())
    monkeypatch.setattr(llm, "chat_json", lambda target, system, user: answers[__import__("json").loads(user)["layer"]])
    approve_order(client, order_id)
    com = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]
    assert com["planned_by"] == "llm" and [t["node_id"] for t in com["tasks"]] == ["c1", "c2", "c4", "c7"]
    som = com["children"][0]
    assert som["planned_by"] == "llm_fallback" and "mandatory" in som["plan_note"] and len(som["tasks"]) == 5
    tom = som["children"][0]
    assert tom["planned_by"] == "llm" and [t["node_id"] for t in tom["tasks"]] == ["t1", "t3"]
    assert next(t for t in tom["tasks"] if t["node_id"] == "t3")["depends_on"] == ["t1"]  # t2's dependency carried over
    assert fulfillment_engine.validate_graph(fulfillment_engine.standard_graph()) == []


def test_a_cancelled_field_job_is_fallout_and_can_be_completed_by_hand(client):
    standard_pattern(client, products="Fulfil Cancel Fiber")
    order_id = _generated_order(client, "Cancel Co", "Fulfil Cancel Fiber")
    approve_order(client, order_id)
    db = client.TestSessionLocal()
    try:
        wo = db.query(WorkOrder).filter(WorkOrder.order_id == order_id, WorkOrder.kind == "tom").one()
        activity_id = db.query(FsActivity).filter(FsActivity.work_order_id == wo.id).one().id
    finally:
        db.close()
    assert client.post(f"/api/field-service/activities/{activity_id}/status", json={"status": "cancelled"}).status_code == 200
    tom = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]["children"][0]["children"][0]
    assert tom["status"] == "fallout"
    install = next(t for t in tom["tasks"] if t["node_id"] == "t1")
    assert install["status"] == "failed" and "cancelled" in install["error"]["message"]
    done = client.post(f"/api/fulfillment/tasks/{install['id']}/resolve", json={"action": "complete", "note": "installed by partner",
                                                                       "response": {"technician": "Partner crew"}})
    assert done.status_code == 200, done.text
    assert tasks_of(client, "TOM", order_id)[1] == {"t1": "completed", "t2": "waiting", "t3": "pending"}
    # a TOM job can't be closed through the classic fulfillment intents
    db = client.TestSessionLocal()
    try:
        next_wo = db.query(WorkOrder).filter(WorkOrder.order_id == order_id, WorkOrder.kind == "tom", WorkOrder.status != "complete",
                                             WorkOrder.status != "cancelled").one().id
    finally:
        db.close()
    refused = intent(client, "INT-26", work_order_id=next_wo)
    assert refused["status"] == "failed" and "Field Service" in refused["summary"]


def test_orchestration_intents(client):
    pattern = standard_pattern(client, products="Fulfil Intent Fiber")
    client.post(f"/api/admin/fulfillment/patterns/{pattern['id']}/deactivate")  # so approval keeps the classic chain ...
    order_id = _generated_order(client, "Intent Route Ltd", "Fulfil Intent Fiber")
    db = client.TestSessionLocal()
    try:  # ... and the order is sent by hand instead, after fixing nothing: its site has no ports
        from app.crm_core import om_service

        order = db.get(Order, order_id)
        om_service.order_detail(db, order.tenant_id, order).ship_to_address = "1 NOPORTS Road"
        db.commit()
    finally:
        db.close()
    client.post(f"/api/admin/fulfillment/patterns/{pattern['id']}/activate")
    said = client.post("/api/intents/rehearse", json={"text": f"Send order {order_id} to orchestration"}).json()
    assert said["intent_type"] == "INT-117" and said["entities"]["order_id"] == order_id
    sent = intent(client, "INT-117", order_id=order_id, fulfillment_pattern=pattern["name"])
    assert sent["status"] == "executed" and sent["result"]["fulfillment_order"].startswith("COM-"), sent
    assert intent(client, "INT-117", order_id=order_id)["status"] == "failed"  # already being fulfilled

    said = client.post("/api/intents/rehearse", json={"text": f"Skip the failed orchestration step on order {order_id} - done by the partner"}).json()
    assert said["intent_type"] == "INT-118" and said["entities"]["fallout_action"] == "skip" and said["entities"]["note"] == "done by the partner"
    resolved = intent(client, "INT-118", order_id=order_id, fallout_action="skip", note="partner reserved the port")
    assert resolved["status"] == "executed", resolved
    assert resolved["result"]["step"] == "Reserve physical resources" and resolved["result"]["step_status"] == "skipped"
    assert tasks_of(client, "SOM", order_id)[1]["s5"] == "waiting"  # on to TOM
    assert intent(client, "INT-118", order_id=order_id)["status"] == "failed"  # no fallout left


def test_designer_offers_only_products_on_sale_once_each(client):
    from app.models.catalog import OfferingStatus, ProductOffering

    assert intent(client, "INT-01", name="Versioned Fiber", composition="Fiber500", segment="B2B", channel="B2B", list_price=500,
                  requested_discount_pct=0)["status"] == "executed"
    db = client.TestSessionLocal()
    try:  # an older superseded version, a draft and a retired product beside the live one
        live = db.query(ProductOffering).filter(ProductOffering.name == "Versioned Fiber").one()
        for name, status, version in (("Versioned Fiber", OfferingStatus.superseded, 0), ("Draft Fiber", OfferingStatus.draft, 1),
                                      ("Retired Fiber", OfferingStatus.retired, 1)):
            db.add(ProductOffering(tenant_id=live.tenant_id, name=name, status=status, version=version, price=100, segment="B2B", composition="Fiber500"))
        db.commit()
    finally:
        db.close()
    products = client.get("/api/admin/fulfillment/catalog").json()["products"]
    assert products.count("Versioned Fiber") == 1 and "Draft Fiber" not in products and "Retired Fiber" not in products
    picklist = next(f for f in client.get("/api/admin/flows/field-catalog", params={"trigger_intent_type": "INT-05"}).json()["fields"]
                    if f["path"] == "entities.offering_name")
    assert picklist["values"].count("Versioned Fiber") == 1 and "Retired Fiber" not in picklist["values"]
