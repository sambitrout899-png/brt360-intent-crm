"""Fulfillment, built in plain English and started by intents:
- the designer drafts a whole COM / SOM / TOM pattern from a description - the tenant's
  model when one is set up (its draft normalised and validated), else the built-in reader;
- INT-119 (vanilla) does the same from the Intent Command Bar, saving a draft;
- a pattern names the intents that start it - vanilla or custom (FLOW-n)."""

from types import SimpleNamespace

from tests.test_fulfillment_orchestration import approve_order, intent, standard_pattern, tasks_of
from tests.test_phase5_fulfillment import _generated_order, _launch_bundle

from app.models.fulfillment_orders import FulfillmentOrder, FulfillmentPattern

DESCRIPTION = ("Fulfil AI Fibre 500 for business customers: register the customer in billing, create the billing account on the Gold "
               "Business rate plan and a monthly billing profile paid by credit card. Then design the service, assign a VLAN and a static IP "
               "and reserve a port and an ONT. Raise an installation work order of 3 hours, then a connectivity test and the customer signs "
               "acceptance. After that activate the service, start billing, create the installed assets and complete the order.")


def lanes(graph):
    by_id = {n["id"]: n for n in graph["nodes"]}
    out = {}
    for layer in ("COM", "SOM", "TOM"):
        out[layer] = [(n["function"], n["config"].get("kind") or n["config"].get("resources") or "") for n in
                      sorted((n for n in graph["nodes"] if n["layer"] == layer), key=lambda n: (n["x"], n["y"]))]
    deps = {}
    for e in graph["edges"]:
        deps.setdefault(by_id[e["target"]]["function"] + ":" + str(by_id[e["target"]]["config"].get("kind", "")), []).append(by_id[e["source"]]["function"])
    return out, deps


def test_the_reader_drafts_the_whole_process(client):
    _launch_bundle(client, name="AI Fibre 500", price=1000)
    draft = client.post("/api/admin/fulfillment/patterns/generate", json={"description": DESCRIPTION}).json()
    assert draft["source"] == "rules" and draft["validation_errors"] == [], draft
    pattern = draft["pattern"]
    assert (pattern["products"], pattern["segment"], pattern["order_type"], pattern["mode"]) == ("AI Fibre 500", "B2B", "new", "deterministic")
    assert pattern["name"] == "AI Fibre 500 fulfillment (B2B)"
    got, deps = lanes(pattern["graph"])
    assert got["COM"] == [("sync_customer", ""), ("create_billing_account", ""), ("create_billing_profile", ""), ("route_to_som", ""),
                          ("start_billing", ""), ("create_assets", ""), ("complete_order", "")]
    # activation was said after the field work - it runs once TOM is done
    assert got["SOM"] == [("design_service", "service_id, circuit_id"), ("assign_logical", "ip_block, vlan"), ("reserve_physical", "port, ont"),
                          ("route_to_tom", ""), ("activate_service", "")]
    assert got["TOM"] == [("field_work_order", "installation"), ("field_work_order", "connectivity_test"), ("field_work_order", "acceptance")]
    configs = {n["function"]: n["config"] for n in pattern["graph"]["nodes"]}
    assert configs["create_billing_account"] == {"rate_plan": "Gold Business"}
    assert configs["create_billing_profile"] == {"frequency": "monthly", "payment_method": "credit_card"}
    install = next(n for n in pattern["graph"]["nodes"] if n["config"].get("kind") == "installation")
    assert install["config"]["duration_minutes"] == "180"
    assert deps["start_billing:"] == ["route_to_som"] and deps["activate_service:"] == ["route_to_tom"]
    assert all(n["mandatory"] == (n["function"].startswith("route_") or n["function"] == "complete_order") for n in pattern["graph"]["nodes"])

    # saved, it is a valid draft that activates and runs like a drawn one
    saved = client.post("/api/admin/fulfillment/patterns", json=pattern).json()
    assert saved["is_active"] is False and saved["validation_errors"] == []
    assert client.post(f"/api/admin/fulfillment/patterns/{saved['id']}/activate").status_code == 200


def test_conditions_parallel_steps_and_the_missing_pieces(client):
    draft = client.post("/api/admin/fulfillment/patterns/generate", json={
        "description": "Site survey only if the order has an address, then install and test the connection in parallel. "
                       "Let the AI decide the steps per order."}).json()
    pattern = draft["pattern"]
    assert pattern["mode"] == "llm" and draft["validation_errors"] == []
    survey, install, test = (next(n for n in pattern["graph"]["nodes"] if n["config"].get("kind") == k) for k in ("site_survey", "installation", "connectivity_test"))
    assert (survey["config"]["when_field"], survey["config"]["when_operator"]) == ("order.address", "not_empty")
    edges = {(e["source"], e["target"]) for e in pattern["graph"]["edges"]}
    assert (survey["id"], install["id"]) in edges and (survey["id"], test["id"]) in edges and (install["id"], test["id"]) not in edges
    # the routes and the order completion it needs, added - and said so
    functions = [n["function"] for n in pattern["graph"]["nodes"]]
    assert {"route_to_som", "route_to_tom", "complete_order"} <= set(functions)
    assert any("Complete the customer order" in f for f in draft["fixes"])


def test_the_model_drafts_within_the_catalog(client, monkeypatch):
    from app.crm_core import llm

    _launch_bundle(client, name="AI Fibre 1G", price=1000)
    answer = {"name": "Gigabit", "products": ["ai fibre 1g", "Imaginary TV"], "segment": "B2C", "order_type": "new", "mode": "deterministic",
              "trigger_intents": ["INT-999"],
              "nodes": [{"id": "a", "layer": "COM", "function": "create_billing_account", "label": "Billing account", "config": {}},
                        {"id": "b", "layer": "COM", "function": "reserve_physical", "label": "Port", "config": {"resources": ["port", "ont"]}},
                        {"id": "c", "layer": "TOM", "function": "field_work_order", "label": "Install", "config": {"kind": "installation"}},
                        {"id": "d", "layer": "COM", "function": "launch_rocket", "label": "Rocket", "config": {}},
                        {"id": "e", "layer": "COM", "function": "start_billing", "label": "Billing", "config": {}}],
              "edges": [{"source": "a", "target": "e"}, {"source": "a", "target": "b"}], "notes": "Gigabit installs"}
    calls = []
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: SimpleNamespace(label="own model (test)"))
    monkeypatch.setattr(llm, "chat_json", lambda target, system, user, **kwargs: calls.append(system) or answer)
    draft = client.post("/api/admin/fulfillment/patterns/generate", json={"description": "Gigabit: billing account, reserve a port and ONT, install, bill"}).json()
    assert draft["source"] == "llm" and draft["model"] == "own model (test)" and draft["validation_errors"] == [], draft
    assert "AI Fibre 1G" in calls[0] and "route_to_som" in calls[0]  # the model was given the real products and the catalog
    pattern = draft["pattern"]
    assert (pattern["name"], pattern["products"], pattern["segment"], pattern["trigger_intents"]) == ("Gigabit", "AI Fibre 1G", "B2C", "")
    got, deps = lanes(pattern["graph"])
    assert [f for f, _ in got["SOM"]] == ["reserve_physical", "route_to_tom"]  # moved to its lane; the lane gets its route
    assert [f for f, _ in got["COM"]] == ["create_billing_account", "route_to_som", "start_billing", "complete_order"]
    assert deps["start_billing:"] == ["route_to_som"]
    fixes = " | ".join(draft["fixes"])
    for said in ("Moved 'Port' to the SOM lane", "'launch_rocket' is not a fulfillment function", "Left out product 'Imaginary TV'",
                 "Added 'Route to service orchestration (SOM)'", "Added 'Route to technical orchestration (TOM)'", "Added 'Complete the customer order'"):
        assert said in fixes, (said, fixes)

    # a model that fails - or answers nothing usable - leaves the built-in reader to draft it
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    draft = client.post("/api/admin/fulfillment/patterns/generate", json={"description": "Reserve a port, install, then start billing"}).json()
    assert draft["source"] == "rules" and "unavailable (TimeoutError)" in draft["notes"] and draft["validation_errors"] == []
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"nodes": [{"id": "x", "function": "nope"}]})
    draft = client.post("/api/admin/fulfillment/patterns/generate", json={"description": "Reserve a port, install, then start billing"}).json()
    assert draft["source"] == "rules" and "did not hold together" in draft["notes"]


def test_int119_designs_a_draft_pattern_from_the_command_bar(client):
    _launch_bundle(client, name="AI Fibre 2G", price=1000)
    said = client.post("/api/intents", json={"text": "Design a fulfillment process for AI Fibre 2G: register the customer in billing, "
                                                     "reserve a port and an ONT, install, test the connection, then start billing"}).json()
    assert said["intent_type"] == "INT-119" and said["status"] == "executed", said
    result = said["result"]
    assert result["pattern_status"] == "draft" and result["products"] == "AI Fibre 2G" and result["drafted_by"] == "built-in reader"
    assert result["steps"]["TOM"] == ["Installation", "Connectivity test"] and "Start billing" in result["steps"]["COM"]
    db = client.TestSessionLocal()
    try:
        pattern = db.get(FulfillmentPattern, result["fulfillment_pattern_id"])
        assert pattern.is_active is False and pattern.products == "AI Fibre 2G"  # a draft - never live on its own
    finally:
        db.close()
    too_short = intent(client, "INT-119", process_description="install")
    assert too_short["status"] != "executed"


def _order_intent(client, name):
    """A custom intent (FLOW-n) on an order - here it just records a note."""
    flow = client.post("/api/admin/flows", json={
        "name": name, "entity_type": "order", "trigger_type": "custom_intent", "trigger_phrases": name.lower(),
        "graph": {"nodes": [{"id": "s", "type": "start", "label": "Start", "config": {}},
                            {"id": "n", "type": "notify", "label": "Tell", "config": {"audience": "owner", "message": "Upgrade for order {{record.id}}"}},
                            {"id": "e", "type": "end", "label": "End", "config": {}}],
                  "edges": [{"id": "1", "source": "s", "target": "n", "label": ""}, {"id": "2", "source": "n", "target": "e", "label": ""}]}}).json()
    assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 200
    return f"FLOW-{flow['id']}"


def test_a_custom_intent_starts_its_pattern(client):
    code = _order_intent(client, "Upgrade bandwidth")
    catalog = client.get("/api/admin/fulfillment/catalog").json()
    assert {"code": code, "label": "Upgrade bandwidth", "kind": "custom", "target": "order"} in catalog["trigger_intents"]
    assert any(i["code"] == "INT-25" and i["kind"] == "builtin" for i in catalog["trigger_intents"])
    assert all(i["code"] not in ("INT-117", "INT-118", "INT-119") for i in catalog["trigger_intents"])

    pattern = standard_pattern(client, products="Fulfil Upgrade Fiber", trigger_intents=code.lower())
    assert pattern["trigger_intents"] == code and pattern["started_by"] == [code]
    bad = client.put(f"/api/admin/fulfillment/patterns/{pattern['id']}", json={**{k: pattern[k] for k in (
        "name", "description", "products", "segment", "order_type", "priority", "mode", "graph")}, "trigger_intents": "FLOW-99999"})
    assert bad.status_code == 400 and "FLOW-99999" in bad.json()["detail"]

    # Order Control approval no longer starts it - only the custom intent does
    approved_id = _generated_order(client, "Approved Upgrade Co", "Fulfil Upgrade Fiber")
    assert "fulfillment_order" not in approve_order(client, approved_id)["result"]

    order_id = _generated_order(client, "Upgrade Co", "Fulfil Upgrade Fiber")
    ran = client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": {"order_id": order_id}}}).json()
    assert ran["status"] == "executed", ran
    started = ran["result"]["fulfillment_started"]
    assert [s["order_id"] for s in started] == [order_id] and started[0]["fulfillment_order"].startswith("COM-")
    assert tasks_of(client, "COM", order_id)[1]["c4"] == "waiting"  # COM registered billing and handed over to SOM

    # running it again does not open a second fulfillment for the same order
    again = client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": {"order_id": order_id}}}).json()
    assert again["status"] == "executed" and "fulfillment_started" not in again["result"]
    db = client.TestSessionLocal()
    try:
        assert db.query(FulfillmentOrder).filter(FulfillmentOrder.crm_order_id == order_id, FulfillmentOrder.layer == "COM").count() == 1
    finally:
        db.close()


def test_approval_can_stay_one_of_the_triggers(client):
    code = _order_intent(client, "Expedite order")
    standard_pattern(client, products="Fulfil Both Fiber", trigger_intents=f"INT-25, {code}")
    order_id = _generated_order(client, "Both Ways Co", "Fulfil Both Fiber")
    assert approve_order(client, order_id)["result"]["fulfillment_order"].startswith("COM-")
