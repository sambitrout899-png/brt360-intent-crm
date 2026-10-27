"""Flow Studio Notify -> "Message box on screen": the box reaches the person whose action
started the flow (or the user / position named), once, until they dismiss it."""

from test_access_guards import _as, _user

from app.orchestration import flow_ai


def _flow(client, audience="me", title="Prospect added", level="success"):
    graph = {"nodes": [
        {"id": "n1", "type": "start", "label": "Start", "config": {}},
        {"id": "n2", "type": "notify", "label": "Tell them", "config": {"channel": "popup", "audience": audience, "title": title, "level": level,
                                                                       "message": "{{record.name}} is now a prospect"}},
        {"id": "n3", "type": "end", "label": "End", "config": {}},
    ], "edges": [{"id": "e1", "source": "n1", "target": "n2", "label": ""}, {"id": "e2", "source": "n2", "target": "n3", "label": ""}]}
    flow = client.post("/api/admin/flows", json={"name": "Welcome box", "entity_type": "party", "trigger_type": "intent",
                                                  "trigger_intent_type": "INT-15", "timing": "after", "graph": graph}).json()
    assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 200, flow
    return flow


def test_the_person_who_did_it_gets_the_message_box_once(client):
    _flow(client)
    assert client.get("/api/notices/popups").json() == []
    done = client.post("/api/intents", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": "Boxed Co"}}}).json()
    assert done["status"] == "executed", done
    boxes = client.get("/api/notices/popups").json()
    assert [(b["title"], b["message"], b["level"]) for b in boxes] == [("Prospect added", "Boxed Co is now a prospect", "success")]
    assert client.post("/api/notices/popups/seen", json={"up_to": boxes[-1]["id"]}).status_code == 204
    assert client.get("/api/notices/popups").json() == []  # dismissed - not shown again


def test_a_box_for_a_position_reaches_its_members_only(client):
    position = client.post("/api/admin/positions", json={"name": "Desk Team", "data_scope": "org"}).json()["id"]
    member, other = _user(client, "desk-member"), _user(client, "not-on-desk")
    client.put(f"/api/admin/users/{member}/access", json={"role_ids": [], "position_ids": [position]})
    _flow(client, audience="Desk Team", level="urgent")
    client.post("/api/intents", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": "Desk Co"}}})
    assert [b["message"] for b in client.get("/api/notices/popups", headers=_as(member)).json()] == ["Desk Co is now a prospect"]
    assert client.get("/api/notices/popups", headers=_as(other)).json() == []
    assert client.get("/api/notices/popups").json() == []  # the admin who did it wasn't named


def test_a_rehearsal_leaves_no_message_box(client):
    _flow(client)
    client.post("/api/intents/rehearse", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": "Ghost Co"}}})
    assert client.get("/api/notices/popups").json() == []


def test_plain_english_drafts_a_message_box():
    draft = flow_ai.heuristic_generate("Show a message box saying the quote is cleared for sending", [])
    box = next(n for n in draft["nodes"] if n["type"] == "notify")
    assert box["config"]["channel"] == "popup" and box["config"]["audience"] == "me"
    assert box["config"]["message"] == "the quote is cleared for sending"
    draft = flow_ai.heuristic_generate("Notify the Sales Manager on screen that the deal is won", ["Sales Manager"])
    box = next(n for n in draft["nodes"] if n["type"] == "notify")
    assert box["config"]["channel"] == "popup" and box["config"]["audience"] == "Sales Manager"
