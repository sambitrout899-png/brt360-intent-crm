"""The Flow Studio drafts a whole flow from a plain sentence - with no AI
key too: the trigger ("When the order gets approved ..."), passive steps
("... should be auto-assigned to the Field Service Manager"), a record that
belongs to the order (its installation work order) and the follow-up
("... who will then dispatch it to the field engineers")."""

from test_phase5_fulfillment import _generated_order
from test_sales_to_order import _user

SENTENCE = ("When Order Gets approved by Order Control Team the Work order for Installation should be auto-assigned to the "
            "Field Service Manager who will the dispatch to the field engineers")


def test_a_sentence_becomes_a_working_flow(client):
    manager_pos = client.post("/api/admin/positions", json={"name": "Field Service Manager", "data_scope": "own"}).json()["id"]
    manager = _user(client, "fsm@flows.test")
    assert client.put(f"/api/admin/users/{manager}/access", json={"role_ids": [], "position_ids": [manager_pos]}).status_code == 200

    draft = client.post("/api/admin/flows/ai-generate", json={"prompt": SENTENCE}).json()
    assert draft["trigger"]["trigger_type"] == "state_enter"
    assert draft["trigger"]["entity_type"] == "order" and draft["trigger"]["trigger_state"] == "activating"
    nodes = {n["type"]: n for n in draft["graph"]["nodes"]}
    assert nodes["assign"]["config"] == {"to_position": "Field Service Manager", "target": "installation_work_order"}
    assert nodes["notify"]["config"]["audience"] == "Field Service Manager" and "dispatch" in nodes["notify"]["config"]["message"]
    assert draft["validation_errors"] == [] and "Could not interpret" not in draft["notes"]

    flow = client.post("/api/admin/flows", json={"name": "Installation to field service", "entity_type": "order", "trigger_type": "state_enter",
                                                  "trigger_state": "activating", "graph": draft["graph"]})
    assert flow.status_code == 201, flow.text
    assert client.post(f"/api/admin/flows/{flow.json()['id']}/activate").status_code == 200

    order_id = _generated_order(client, "Flow Install Co", "Fiber 777")
    approved = client.post("/api/intents", json={"text": f"Approve this order. order_id: {order_id}"}).json()
    assert approved["status"] == "executed", approved["rationale"]
    work_order = approved["result"]["work_order_id"]

    mine = client.get("/api/records/my-work", headers={"X-Test-User-Id": str(manager)}).json()
    item = next(i for i in mine if i["entity_type"] == "work_order")
    assert item["entity_id"] == work_order and item["open_entity_type"] == "order" and item["open_entity_id"] == order_id
    assert item["via_position"] == "Field Service Manager" and item["title"].startswith("Installation work order")
    # opening it shows the order - the manager may see it though only its work order is theirs
    opened = client.get(f"/api/records/order/{order_id}", headers={"X-Test-User-Id": str(manager)})
    assert opened.status_code == 200, opened.text
    other = _generated_order(client, "Not Theirs Co", "Fiber 778")
    assert client.get(f"/api/records/order/{other}", headers={"X-Test-User-Id": str(manager)}).status_code == 404


def test_other_sentences(client):
    quote = client.post("/api/admin/flows/ai-generate", json={"prompt": "When a quote is approved, notify the account manager"}).json()
    assert quote["trigger"]["entity_type"] == "quote" and quote["trigger"]["trigger_state"] == "approved"
    assert [n["type"] for n in quote["graph"]["nodes"]] == ["start", "notify", "end"]
    # a "when" that is not a record event stays a condition, as before
    plain = client.post("/api/admin/flows/ai-generate", json={"prompt": "If total price is greater than 5000, assign to Deal Desk",
                                                               "entity_type": "quote"}).json()
    assert plain["trigger"] is None and any(n["type"] == "condition" for n in plain["graph"]["nodes"])
