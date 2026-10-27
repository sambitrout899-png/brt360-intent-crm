import pytest


@pytest.mark.parametrize("with_product", [True, False])
def test_flow_creates_consumer_order_without_quote(client, with_product):
    def intent(code, **entities):
        return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()

    launched = intent("INT-01", name="Flow Home Fiber", composition="Fiber500", segment="B2C", channel="B2C", list_price=500, requested_discount_pct=0)
    assert launched["status"] == "executed", launched
    customer = intent("INT-41", name="Flow Consumer")
    assert customer["status"] == "executed", customer
    party_id = customer["result"]["party_id"]
    graph = {
        "nodes": [
            {"id": "s", "type": "start", "config": {}},
            {"id": "a", "type": "action", "config": {
                "intent": "INT-07", "on_error": "stop", "entities": {
                    "party_id": "{{entities.party_id}}",
                    "offering_name": "{{entities.offering_name}}", "quantity": 2,
                }}},
            {"id": "e", "type": "end", "config": {}},
        ],
        "edges": [{"id": "1", "source": "s", "target": "a"}, {"id": "2", "source": "a", "target": "e"}],
    }
    flow = client.post("/api/admin/flows", json={"name": "B2C order regression", "entity_type": "party", "trigger_type": "intent", "trigger_intent_type": "INT-49", "graph": graph}).json()
    entities = {"party_id": party_id}
    if with_product:
        entities["offering_name"] = "Flow Home Fiber"
    sim = client.post(f"/api/admin/flows/{flow['id']}/simulate", json={"entity_id": party_id, "entities": entities}).json()
    if with_product:
        assert sim["ok"], sim
        action = next(step for step in sim["run"]["trace"] if step["type"] == "action")
        assert action["result_ids"]["order_id"]
    else:
        assert not sim["ok"], sim
        assert "offering_name" in sim["error"]
        assert "needs quote_id" not in sim["error"]
