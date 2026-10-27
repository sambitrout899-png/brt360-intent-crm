"""Conflicts (orchestration/conflicts.py), smarter prefill (copilot.prepare),
and building an unsupported request from scratch (orchestration/scratch.py)."""

from test_access_guards import _as, _user


def _graph(phone):
    return {"nodes": [
        {"id": "n1", "type": "start", "label": "Start", "config": {}},
        {"id": "n2", "type": "set_field", "label": "Set phone", "config": {"field": "record.phone", "value": phone}},
        {"id": "n3", "type": "end", "label": "End", "config": {}},
    ], "edges": [{"id": "e1", "source": "n1", "target": "n2", "label": ""}, {"id": "e2", "source": "n2", "target": "n3", "label": ""}]}


def _flow(client, name, phone):
    flow = client.post("/api/admin/flows", json={"name": name, "entity_type": "party", "trigger_type": "intent", "trigger_intent_type": "INT-15",
                                                  "timing": "after", "graph": _graph(phone)}).json()
    assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 200, flow
    return flow["id"]


def _prospect(client, name):
    done = client.post("/api/intents", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": name}}}).json()
    assert done["status"] == "executed", done["rationale"]
    party_id = done["result"]["party_id"]
    return party_id, client.get(f"/api/records/party/{party_id}").json()["core_fields"].get("phone"), done


def test_clashing_automations_are_found_and_the_admin_rule_is_kept(client):
    first, second = _flow(client, "Phone A", "111"), _flow(client, "Phone B", "222")
    found = client.get("/api/admin/conflicts").json()
    clash = next(c for c in found["conflicts"] if c["key"] == "intent:INT-15:after")
    assert clash["severity"] == "high" and "field" in clash["kinds"] and not clash["resolved"] and found["open"] >= 1
    assert [m["id"] for m in clash["members"]] == [first, second]

    # unsettled: both run in id order - the last one wins
    assert _prospect(client, "Order Co")[1] == "222"
    # the rehearsal reports the conflict it touches
    seen = client.post("/api/intents/rehearse", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": "Ghost Co"}}}).json()
    assert any(c["key"] == "intent:INT-15:after" for c in seen["conflicts"])

    # the admin's order: B first, then A - A wins
    out = client.post("/api/admin/conflicts/resolve", json={"key": "intent:INT-15:after", "order": [second, first], "mode": "all"})
    assert out.status_code == 200, out.text
    assert next(c for c in out.json()["conflicts"] if c["key"] == "intent:INT-15:after")["resolved"]
    assert _prospect(client, "Ordered Co")[1] == "111"

    # only the first that applies: B runs, A stands aside
    client.post("/api/admin/conflicts/resolve", json={"key": "intent:INT-15:after", "order": [second, first], "mode": "first"})
    _, phone, done = _prospect(client, "First Co")
    assert phone == "222"
    skipped = [f for f in done["result"]["flows_run"] if f.get("skipped")]
    assert [f["flow_name"] for f in skipped] == ["Phone A"] and "Phone B" in skipped[0]["skipped"]

    # ... B only for VIPs: for anyone else A runs
    client.post("/api/admin/conflicts/resolve", json={"key": "intent:INT-15:after", "order": [second, first], "mode": "first",
                                                      "conditions": {str(second): {"field": "name", "operator": "contains", "value": "VIP"}}})
    assert _prospect(client, "Plain Co")[1] == "111"
    assert _prospect(client, "VIP Co")[1] == "222"

    # bad rules are refused; clearing goes back to id order
    assert client.post("/api/admin/conflicts/resolve", json={"key": "intent:INT-15:after", "order": [first]}).status_code == 400
    assert client.delete("/api/admin/conflicts/rules/intent:INT-15:after").status_code == 200
    assert _prospect(client, "Cleared Co")[1] == "222"


def test_one_flow_per_trigger_is_no_conflict_and_non_admins_cant_settle(client):
    _flow(client, "Only one", "333")
    assert not any(c["key"] == "intent:INT-15:after" for c in client.get("/api/admin/conflicts").json()["conflicts"])
    agent = _user(client, "conflict-agent")
    assert client.get("/api/admin/conflicts", headers=_as(agent)).status_code == 403


def test_two_actions_on_one_phrase_and_the_winner_keeps_it(client):
    ids = []
    for label in ("Site survey", "Survey booking"):
        made = client.post("/api/admin/intents", json={"label": label, "phrases": ["book a survey", label.lower()], "is_active": True}).json()
        ids.append(made["flow_id"])
    clash = next(c for c in client.get("/api/admin/conflicts").json()["conflicts"] if c["key"] == "phrase:book a survey")
    assert clash["type"] == "actions" and {m["id"] for m in clash["members"]} == set(ids)
    assert client.post("/api/admin/conflicts/resolve", json={"key": "phrase:book a survey", "winner": ids[0]}).status_code == 200
    assert not any(c["key"] == "phrase:book a survey" for c in client.get("/api/admin/conflicts").json()["conflicts"])
    loser = client.get(f"/api/admin/flows/{ids[1]}").json()
    assert loser["trigger_phrases"] == "survey booking"


def test_a_plan_with_steps_that_pull_apart_is_flagged_before_it_runs(client):
    steps = [{"intent_type": "INT-17", "entities": {"lead_id": 999, "decision": "approve"}, "text": "approve lead 999"},
             {"intent_type": "INT-17", "entities": {"lead_id": 999, "decision": "reject"}, "text": "reject lead 999"}]
    run = client.post("/api/agent/run", json={"steps": steps, "mode": "rehearse"}).json()
    opposite = next(c for c in run["conflicts"] if "opposite" in c["kinds"])
    assert opposite["steps"] == [1, 2] and opposite["severity"] == "high" and run["can_settle"] is True
    plan = client.post("/api/agent/plan", json={"goal": "Add prospect Plan Co"}).json()
    assert "conflicts" in plan and "can_settle" in plan


def test_the_form_guesses_from_the_account_and_the_business(client):
    party_id, _, _ = _prospect(client, "Habit Account")
    for _ in range(2):
        done = client.post("/api/intents", json={"text": "x", "structured": {"intent_type": "INT-46", "entities": {
            "party_id": party_id, "source": "partner", "region": "South"}}}).json()
        assert done["status"] == "executed", done["rationale"]
    # this customer's usual region
    fields = {f["key"]: f for f in client.post("/api/intents/prepare", json={"intent_type": "INT-46", "entities": {"party_id": party_id}}).json()["fields"]}
    assert fields["region"]["value"] == "South" and fields["region"]["origin"] == "account"
    # someone with no history, a new customer: what the business usually picks
    other = _user(client, "habit-newcomer")
    fresh, _, _ = _prospect(client, "Fresh Account")
    prepared = client.post("/api/intents/prepare", json={"intent_type": "INT-46", "entities": {"party_id": fresh}}, headers=_as(other)).json()
    fields = {f["key"]: f for f in prepared["fields"]}
    assert fields["source"]["value"] == "partner" and fields["source"]["origin"] == "business"


def test_an_unsupported_request_is_built_from_scratch_on_consent(client):
    draft = client.post("/api/assistant/scratch/draft", json={"request": "Please book a site survey for the customer with the survey date, "
                                                                         "engineer name and mandatory notes"}).json()
    assert draft["label"].lower().startswith("book a site survey") and draft["entity_type"] == "party"
    fields = {f["key"]: f for f in draft["fields"]}
    assert set(fields) == {"survey_date", "engineer_name", "notes"}
    assert fields["survey_date"]["placeholder"] == "YYYY-MM-DD" and fields["notes"]["type"] == "textarea" and fields["notes"]["required"]
    assert draft["creates"] and draft["steps"]
    # nothing was built by the draft
    codes = {i["code"] for i in client.get("/api/admin/intents").json()["intents"]}

    built = client.post("/api/assistant/scratch/build", json={"spec": draft})
    assert built.status_code == 200, built.text
    code = built.json()["code"]
    assert code not in codes and built.json()["intent"]["is_active"]
    party_id, _, _ = _prospect(client, "Survey Co")
    done = client.post("/api/intents", json={"text": "x", "structured": {"intent_type": code, "entities": {"party_id": party_id, "notes": "gate code 42"}}}).json()
    assert done["status"] == "executed", done["rationale"]

    # an agent may not build, and an unknown request tells them to ask an administrator
    agent = _user(client, "scratch-agent")
    assert client.post("/api/assistant/scratch/build", json={"spec": draft}, headers=_as(agent)).status_code == 403
    assert client.post("/api/assistant/scratch/draft", json={"request": "anything new"}, headers=_as(agent)).status_code == 403


def test_the_assistant_offers_to_build_what_nothing_covers(client):
    out = client.post("/api/assistant/understand", json={"text": "Make everything better for the customers"}).json()
    assert out["route"] == "unknown" and "Rephrase" in out["why"]
    # the draft itself is fetched separately, so the answer doesn't wait for it
    assert out["can_build"] and "build_offer" not in out
    draft = client.post("/api/assistant/scratch/draft", json={"request": out["request"]}).json()
    assert draft["creates"]
