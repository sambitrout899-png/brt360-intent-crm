"""Rehearsal: an intent runs exactly as for real - approvals, flows, the
state cascade, assignment, notifications - then everything is taken back,
and the consequences come back as `impact`. Nothing is kept: not the
records, not the state, not the audit log."""

from test_flow_ai_generation import SENTENCE
from test_phase5_fulfillment import _generated_order
from test_sales_to_order import _user


def _flow_setup(client):
    pos = client.post("/api/admin/positions", json={"name": "Field Service Manager", "data_scope": "own"}).json()["id"]
    manager = _user(client, "fsm@rehearse.test")
    client.put(f"/api/admin/users/{manager}/access", json={"role_ids": [], "position_ids": [pos]})
    draft = client.post("/api/admin/flows/ai-generate", json={"prompt": SENTENCE}).json()
    flow = client.post("/api/admin/flows", json={"name": "Work Order Assignment", "entity_type": "order", "trigger_type": "state_enter",
                                                 "trigger_state": "activating", "graph": draft["graph"]}).json()
    client.post(f"/api/admin/flows/{flow['id']}/activate")
    return manager


def test_rehearsal_shows_the_future_and_keeps_nothing(client):
    manager = _flow_setup(client)
    order_id = _generated_order(client, "Rehearse Co", "Fiber 900")
    before_order = client.get(f"/api/records/order/{order_id}").json()
    before_logs = len(client.get("/api/admin/intent-logs").json()) if client.get("/api/admin/intent-logs").status_code == 200 else None

    text = f"Approve this order. order_id: {order_id}"
    seen = client.post("/api/intents/rehearse", json={"text": text})
    assert seen.status_code == 200, seen.text
    seen = seen.json()
    assert seen["rehearsal"] is True and seen["status"] == "rehearsed", seen["rationale"]
    assert seen["intent_execution_id"] == 0
    impact = seen["impact"]
    assert impact["headline"].startswith("This would ")
    assert any(m["entity_type"] == "order" and m["entity_id"] == order_id for m in impact["state_moves"])
    assert any(f["name"] for f in impact["flows"]) or impact["flows"]
    assert any("Field Service Manager" in a["to"] for a in impact["assignments"])
    assert any("dispatch" in n["message"] for n in impact["notifications"])
    assert any(r["entity"] == "work order" for r in impact["records_created"])
    assert impact["blast_radius"] >= 2

    # ... and none of it happened
    assert client.get(f"/api/records/order/{order_id}").json() == before_order
    mine = client.get("/api/records/my-work", headers={"X-Test-User-Id": str(manager)}).json()
    assert not any(i["entity_type"] == "work_order" for i in mine)
    if before_logs is not None:
        assert len(client.get("/api/admin/intent-logs").json()) == before_logs

    # the real run does what the rehearsal said
    done = client.post("/api/intents", json={"text": text}).json()
    assert done["status"] == "executed" and done["rehearsal"] is False and done["impact"] is None
    mine = client.get("/api/records/my-work", headers={"X-Test-User-Id": str(manager)}).json()
    assert any(i["entity_type"] == "work_order" for i in mine)


def test_a_blocked_or_incomplete_intent_rehearses_without_a_trace(client):
    incomplete = client.post("/api/intents/rehearse", json={"text": "Approve this order"}).json()
    assert incomplete["rehearsal"] is True and incomplete["status"] in ("needs_review", "failed") and incomplete["impact"] is None
    # a structured rehearsal (from an in-page form) goes the same way as a structured run
    order_id = _generated_order(client, "Structured Co", "Fiber 901")
    before = client.get(f"/api/records/order/{order_id}").json()
    seen = client.post("/api/intents/rehearse", json={"text": "Approve the order", "structured": {
        "intent_type": "INT-25", "entities": {"order_id": order_id, "decision": "approve"}}}).json()
    assert seen["rehearsal"] is True and seen["status"] == "rehearsed", seen["rationale"]
    assert client.get(f"/api/records/order/{order_id}").json() == before
