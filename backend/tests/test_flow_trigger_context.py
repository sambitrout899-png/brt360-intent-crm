from app.orchestration.flow_engine import render


def test_after_intent_receives_original_full_output(client):
    nodes = [
        {"id": "s", "type": "start", "config": {}},
        {"id": "action", "type": "action", "config": {"intent": "INT-41", "on_error": "stop", "entities": {"name": "Secondary customer"}}},
        {"id": "mapping", "type": "map", "config": {"values": {
            "customer_name": "{{flow.inputs.records.customer.name}}",
            "customer_number": "{{flow.inputs.records.customer.id}}",
            "carried_email": "{{flow.inputs.parameters.email}}",
            "customer_id": "{{trigger.output.party_id}}", "name": "{{trigger.output.name}}",
            "original_email": "{{trigger.input.email}}", "full_output": "{{trigger.output}}",
            "second_customer_id": "{{steps.action.output.party_id}}"}}},
        {"id": "e", "type": "end", "config": {}},
    ]
    graph = {"nodes": nodes, "edges": [{"id": str(i), "source": a["id"], "target": b["id"]} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]}
    response = client.post("/api/admin/flows", json={"name": "Use vanilla output", "trigger_type": "intent", "trigger_intent_type": "INT-41", "timing": "after", "graph": graph})
    assert response.status_code == 201, response.text
    flow = response.json()
    assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 200
    result = client.post("/api/intents", json={"text": "", "structured": {"intent_type": "INT-41", "entities": {"name": "Original customer", "email": "original@example.test"}}}).json()
    assert result["status"] == "executed", result
    run = result["result"]["flows_run"][0]
    mapped = run["steps"]["mapping"]["output"]
    assert mapped["name"] == "Original customer"
    assert mapped["customer_name"] == "Original customer"
    assert mapped["customer_number"] == mapped["customer_id"]
    assert mapped["carried_email"] == "original@example.test"
    assert mapped["customer_id"] == result["result"]["party_id"]
    assert mapped["second_customer_id"] != mapped["customer_id"]
    assert mapped["original_email"] == "original@example.test"
    assert mapped["full_output"]["segment"] == "B2C"


def test_simulation_accepts_nested_trigger_outputs(client):
    graph = {"nodes": [{"id": "s", "type": "start", "config": {}},
        {"id": "m", "type": "map", "config": {"values": {"product": "{{trigger.output.eligible.0}}", "all": "{{trigger.output}}"}}},
        {"id": "e", "type": "end", "config": {}}],
        "edges": [{"id": "1", "source": "s", "target": "m"}, {"id": "2", "source": "m", "target": "e"}]}
    flow = client.post("/api/admin/flows", json={"name": "Trigger output simulation", "trigger_type": "intent", "trigger_intent_type": "INT-49", "graph": graph}).json()
    output = {"party_id": 42, "eligible": ["Fiber 500"], "nested": {"available": True}}
    result = client.post(f"/api/admin/flows/{flow['id']}/simulate", json={"result": output}).json()
    assert result["ok"], result
    assert result["run"]["steps"]["m"]["output"] == {"product": "Fiber 500", "all": output}
    catalog = client.get("/api/admin/flows/field-catalog", params={"trigger_intent_type": "INT-49"}).json()
    assert "trigger.output.party_id" in {f["path"] for f in catalog["fields"]}
    assert "trigger.output.eligible" in {f["path"] for f in catalog["fields"]}
    paths = {f["path"] for f in catalog["handoff"]}
    assert "flow.inputs.records.customer.name" in paths
    assert "flow.inputs.records.customer.brt_id" in paths
    assert "flow.inputs.records.product.name" in paths
    assert "flow.inputs.parameters.eligible" in paths


def test_reference_types():
    ctx = {"trigger": {"output": {"quantity": 2, "ready": True, "items": [{"id": 7}]}}}
    assert render("{{trigger.output.quantity}}", ctx) == 2
    assert render("{{trigger.output.ready}}", ctx) is True
    assert render("{{trigger.output.items.0.id}}", ctx) == 7


def test_record_details_are_tenant_scoped(client):
    from app.orchestration.flow_parameters import contract
    from app.models.tenant import Tenant
    from app.models.crm import Party
    from sqlalchemy import select
    db = client.TestSessionLocal()
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "default"))
    other = Tenant(slug="parameter-other", name="Other")
    db.add(other); db.flush()
    foreign = Party(tenant_id=other.id, name="Private customer")
    db.add(foreign); db.flush()
    result = contract(db, tenant.id, {"party_id": foreign.id}, {})
    assert "customer" not in result["records"]
    db.close()
