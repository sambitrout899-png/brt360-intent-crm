import json
import pytest
from sqlalchemy import select
from app.models.tenant import Tenant
from app.models.crm import Party
from app.models.flows import FlowDefinition
from app.orchestration import flow_engine, flow_records
from app.data_intelligence import audit


def setup_db(client):
    db = client.TestSessionLocal()
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "default"))
    return db, tenant.id


def test_record_crud_upsert_and_tenant_isolation(client):
    db, tenant = setup_db(client)
    def op(operation, **kwargs):
        return flow_records.execute(db, tenant, {"entity": "parties", "operation": operation, **kwargs})
    first = op("insert", values={"name": "Record ops customer"})
    assert first["created"] and first["brt_id"]
    updated = op("upsert", match={"name": "Record ops customer"}, values={"phone": "123"})
    assert not updated["created"] and updated["id"] == first["id"]
    assert op("read", match={"brt_id": first["brt_id"]})["record"]["phone"] == "123"
    op("update", match={"id": first["id"]}, values={"phone": "456"})
    second = op("upsert", match={"name": "Other customer"}, values={})
    assert second["created"]
    with pytest.raises(ValueError, match="non-empty"):
        op("delete")
    with pytest.raises(ValueError, match="system-managed"):
        op("update", match={"id": first["id"]}, values={"tenant_id": 999})
    other = Tenant(slug="flow-other", name="Other")
    db.add(other); db.flush()
    stranger = Party(tenant_id=other.id, name="Private")
    db.add(stranger); db.flush()
    with pytest.raises(ValueError, match="not found"):
        op("read", match={"id": stranger.id})
    assert op("delete", match={"id": first["id"]})["deleted"]
    db.close()


def test_step_mapping_and_transaction_rollback(client):
    db, tenant = setup_db(client)
    nodes = [
        {"id": "start", "type": "start", "config": {}},
        {"id": "create", "type": "record", "config": {"entity": "parties", "operation": "insert", "values": {"name": "{{entities.name}}"}}},
        {"id": "map", "type": "map", "config": {"values": {"customer": "{{steps.create.output.id}}", "name": "{{steps.create.input.values.name}}", "nested": ["{{steps.create.output.record.name}}"]}}},
        {"id": "update", "type": "record", "config": {"entity": "parties", "operation": "update", "match": {"id": "{{steps.map.output.customer}}"}, "values": {"phone": "12345"}}},
        {"id": "end", "type": "end", "config": {}},
    ]
    graph = {"nodes": nodes, "edges": [{"id": str(i), "source": a["id"], "target": b["id"]} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]}
    flow = FlowDefinition(tenant_id=tenant, name="CRUD custom flow", trigger_type="custom_intent", graph_json=json.dumps(graph))
    db.add(flow); db.flush()
    log = audit.start_execution(db, tenant, "CRUD test", channel="agent")
    run = flow_engine.run_flow(db, tenant, log.id, flow, {"entities": {"name": "Mapped customer"}})
    assert run["trace"][2]["output"]["nested"] == ["Mapped customer"]
    assert isinstance(run["trace"][2]["output"]["customer"], int)
    assert run["trace"][3]["output"]["record"]["phone"] == "12345"
    db.rollback()
    assert db.scalar(select(Party).where(Party.name == "Mapped customer")) is None
    db.close()


def test_parent_child_references_and_delete_guard(client):
    db, tenant = setup_db(client)
    def op(entity, operation="insert", **kwargs):
        return flow_records.execute(db, tenant, {"entity": entity, "operation": operation, **kwargs})
    party = op("parties", values={"name": "Parent"})
    lead = op("leads", values={"party_id": party["id"], "source": "flow", "owner_name": "Admin", "propensity_score": 0.5})
    with pytest.raises(ValueError, match="referenced"):
        op("parties", "delete", match={"id": party["id"]})
    with pytest.raises(ValueError, match="referenced record"):
        op("leads", values={"party_id": 987654, "source": "flow"})
    op("leads", "delete", match={"id": lead["id"]})
    op("parties", "delete", match={"id": party["id"]})
    assert "order_items" in flow_records.available()
    assert "users" not in flow_records.available()
    db.close()


def test_order_and_line_items_without_intents(client):
    db, tenant = setup_db(client)
    def insert(entity, **values):
        return flow_records.execute(db, tenant, {"entity": entity, "operation": "insert", "values": values})
    party = insert("parties", name="Direct order")
    billing = insert("billing_accounts", party_id=party["id"])
    service = insert("service_accounts", billing_account_id=billing["id"], name="Home service")
    product = insert("product_offerings", name="Flow Fiber", segment="B2C", price=500, composition="Fiber500")
    order = insert("orders", party_id=party["id"], service_account_id=service["id"], monthly_price=1000)
    item = insert("order_items", order_id=order["id"], product_offering_id=product["id"], quantity=2)
    assert item["record"]["order_id"] == order["id"]
    assert item["record"]["quantity"] == 2
    assert order["record"]["quote_id"] is None
    read = flow_records.execute(db, tenant, {"entity": "order_items", "operation": "read", "match": {"brt_id": item["brt_id"]}})
    assert read["id"] == item["id"]
    other = Tenant(slug="line-other", name="Other line tenant")
    db.add(other); db.flush()
    with pytest.raises(ValueError, match="not found"):
        flow_records.execute(db, other.id, {"entity": "order_items", "operation": "delete", "match": {"id": item["id"]}})
    db.close()


def test_missing_required_and_ambiguous_match(client):
    db, tenant = setup_db(client)
    with pytest.raises(ValueError, match="Required fields missing: name"):
        flow_records.execute(db, tenant, {"entity": "parties", "operation": "insert"})
    for _ in range(2):
        flow_records.execute(db, tenant, {"entity": "parties", "operation": "insert", "values": {"name": "Duplicate"}})
    with pytest.raises(ValueError, match="more than one"):
        flow_records.execute(db, tenant, {"entity": "parties", "operation": "delete", "match": {"name": "Duplicate"}})
    db.close()


def test_unexecuted_step_reference_fails_instead_of_writing_null():
    with pytest.raises(ValueError, match="unavailable"):
        flow_engine.render({"order_id": "{{steps.missing.output.id}}"}, {"steps": {}})
    assert flow_engine.render("{{steps.read.output.phone}}", {"steps": {"read": {"output": {"phone": None}}}}) is None


def test_custom_intent_record_insert_through_api(client):
    catalog = client.get("/api/admin/flows/catalog").json()
    assert any(e["name"] == "order_items" for e in catalog["record_entities"])
    graph = {"nodes": [
        {"id": "s", "type": "start", "config": {}},
        {"id": "insert", "type": "record", "config": {"entity": "parties", "operation": "insert", "values": {"name": "{{entities.name}}"}}},
        {"id": "e", "type": "end", "config": {}},
    ], "edges": [{"id": "1", "source": "s", "target": "insert"}, {"id": "2", "source": "insert", "target": "e"}]}
    flow = client.post("/api/admin/flows", json={"name": "Add flow customer", "trigger_type": "custom_intent", "trigger_phrases": "add flow customer", "required_fields": "name", "graph": graph}).json()
    assert "id" in flow, flow
    response = client.post(f"/api/admin/flows/{flow['id']}/activate")
    assert response.status_code == 200, response.text
    result = client.post("/api/intents", json={"text": "", "structured": {"intent_type": f"FLOW-{flow['id']}", "entities": {"name": "Custom flow customer"}}}).json()
    assert result["status"] == "executed", result
    db, _ = setup_db(client)
    assert db.scalar(select(Party).where(Party.name == "Custom flow customer")) is not None
    db.close()
