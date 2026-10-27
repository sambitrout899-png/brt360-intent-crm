import pytest
from sqlalchemy import select, func
from app.models.tenant import Tenant
from app.models.orders import Order, OrderItem
from app.orchestration import flow_ai, flow_records


@pytest.mark.parametrize("prompt", [
    "Create a flow to enter an Order and Order item record",
    "Create a flow to enter an Order and Order item record.",
    "Build a flow to create an order with line items",
])
def test_record_recipe(prompt):
    draft = flow_ai.heuristic_generate(prompt, [])
    assert [n["type"] for n in draft["nodes"]].count("record") == 6
    assert draft["nodes"][-2]["config"]["values"]["order_id"] == "{{steps.order.output.id}}"
    assert "monthly_price" in draft["notes"]


def test_recipe_does_not_ignore_extra_conditions():
    assert flow_ai._order_record_draft("Create a flow to enter an Order and Order item record only if approved") is None


def test_generated_order_flow_simulation(client, monkeypatch):
    from app.crm_core import llm
    monkeypatch.setattr(llm, "resolve", lambda *args, **kwargs: None)
    db = client.TestSessionLocal()
    tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == "default"))
    party = flow_records.execute(db, tenant_id, {"entity": "parties", "operation": "insert", "values": {"name": "Generated flow customer"}})
    product = flow_records.execute(db, tenant_id, {"entity": "product_offerings", "operation": "insert", "values": {
        "name": "Generated fiber", "segment": "B2C", "price": 500, "composition": "Fiber500"}})
    db.commit()
    draft = client.post("/api/admin/flows/ai-generate", json={"prompt": "Create a flow to enter an Order and Order item record"}).json()
    assert draft["source"] == "heuristic"
    assert draft["validation_errors"] == [], draft
    flow = client.post("/api/admin/flows", json={"name": "Generated order", "trigger_type": "custom_intent",
        "trigger_phrases": "insert order record", "required_fields": "party_id,product_offering_id,quantity,monthly_price", "graph": draft["graph"]}).json()
    assert "id" in flow, flow
    counts = (db.scalar(select(func.count()).select_from(Order)), db.scalar(select(func.count()).select_from(OrderItem)))
    simulation = client.post(f"/api/admin/flows/{flow['id']}/simulate", json={"entities": {
        "party_id": party["id"], "product_offering_id": product["id"], "quantity": 2, "monthly_price": 1000}}).json()
    assert simulation["ok"], simulation
    steps = simulation["run"]["steps"]
    assert steps["line"]["output"]["record"]["order_id"] == steps["order"]["output"]["id"]
    assert steps["line"]["output"]["record"]["quantity"] == 2
    assert (db.scalar(select(func.count()).select_from(Order)), db.scalar(select(func.count()).select_from(OrderItem))) == counts
    db.close()
