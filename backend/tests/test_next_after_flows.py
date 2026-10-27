"""What's next after an action follows what the tenant's business rules
(Flow Studio "after" automations) already did - a step a rule ran is never
offered again."""


def _intent(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def test_auto_qualified_lead_is_not_offered_for_approval_again(client):
    party_id = _intent(client, "INT-15", name="Auto Qualify Ltd", segment="B2B")["result"]["party_id"]

    plain = _intent(client, "INT-46", party_id=party_id, source="referral")
    assert plain["status"] == "executed", plain
    assert "INT-17" in [n["code"] for n in plain["next_actions"]]  # vanilla: qualify comes next

    graph = {
        "nodes": [
            {"id": "s", "type": "start", "config": {}},
            {"id": "q", "type": "action", "label": "Auto qualify the lead", "config": {
                "intent": "INT-17", "on_error": "skip", "entities": {"lead_id": "{{result.lead_id}}", "decision": "approve"}}},
            {"id": "e", "type": "end", "config": {}},
        ],
        "edges": [{"id": "1", "source": "s", "target": "q"}, {"id": "2", "source": "q", "target": "e"}],
    }
    flow = client.post("/api/admin/flows", json={"name": "Auto qualify the lead", "entity_type": "lead", "trigger_type": "intent",
                                                 "trigger_intent_type": "INT-46", "timing": "after", "graph": graph})
    assert flow.status_code == 201, flow.text
    assert client.post(f"/api/admin/flows/{flow.json()['id']}/activate").status_code == 200

    done = _intent(client, "INT-46", party_id=party_id, source="referral")
    assert done["status"] == "executed", done
    ran = [s for run in done["result"].get("flows_run", []) for s in run["trace"] if s["type"] == "action" and not s.get("skipped")]
    assert [s["intent"] for s in ran] == ["INT-17"], done["result"].get("flows_run")
    codes = [n["code"] for n in done["next_actions"]]
    assert "INT-17" not in codes and "INT-46" not in codes, codes
