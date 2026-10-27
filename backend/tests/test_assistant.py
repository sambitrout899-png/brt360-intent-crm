"""Ask anything: any request is understood and routed to the platform's own
pillars - one action, a chain of actions, or a process to set up - recorded in
the journal, and what worked is remembered for an exact repeat."""

from test_access_guards import _as, _user
from test_agent import SALES_CHAIN
from test_phase5_fulfillment import _launch_bundle

B2C_JOURNEY = ("I want to add a new B2C process - customer->identify verification->raise order ->installation->service activation on "
               "network->connectivity testing->acceptance->start billing->installed asset")


def _ask(client, text, headers=None):
    resp = client.post("/api/assistant/understand", json={"text": text}, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_autonomous_request_consults_model_for_short_english_request(client, monkeypatch):
    from app.orchestration import agent

    calls = []

    def plan(db, tenant_id, goal, context, catalog):
        calls.append(goal)
        assert "INT-15" in catalog
        return [{"intent_type": "INT-15", "entities": {"name": "Acme"}, "text": goal, "why": "Add prospect"}]

    monkeypatch.setattr(agent, "_llm_plan", plan)
    response = client.post("/api/assistant/understand", json={"text": "Add prospect Acme", "autonomous": True})
    assert response.status_code == 200, response.text
    assert calls == ["Add prospect Acme"]
    assert response.json()["route"] == "action"


def test_one_action_is_an_action_with_its_form_prefilled(client):
    out = _ask(client, "Add prospect Solo Co")
    assert out["route"] == "action" and out["action"]["intent_type"] == "INT-15"
    assert out["action"]["entities"].get("name") == "Solo Co"


def test_a_how_do_i_question_is_answered_with_the_action_that_does_it(client):
    out = _ask(client, "How can I add a B2C customer here?")
    assert out["route"] == "answer" and "Add a consumer" in out["why"]
    assert out["answers"][0]["intent_type"] == "INT-41"
    assert _ask(client, "Where do I raise a complaint for a customer?")["answers"][0]["intent_type"]  # some action that does it


def test_the_answer_does_not_depend_on_the_model_understanding_the_question(client, monkeypatch):
    """On the server the model read the question as nothing to do - the catalog still answers it."""
    from app.intent_engine import classifier

    real = classifier.classify

    def lost(text, *a, **k):
        result = real(text, *a, **k)
        result.intent_type, result.entities = None, {}
        return result

    monkeypatch.setattr(classifier, "classify", lost)
    out = _ask(client, "How can I add a B2C customer here?")
    assert out["route"] == "answer" and out["answers"][0]["intent_type"] == "INT-41"


def test_what_can_you_do_explains_itself(client):
    out = _ask(client, "What can you do?")
    assert out["route"] == "answer" and "set up a process" in out["why"] and out["answers"] == []


def test_things_to_do_in_order_are_a_chain(client):
    out = _ask(client, SALES_CHAIN)
    assert out["route"] == "chain"
    assert [s["intent_type"] for s in out["plan"]["steps"]] == ["INT-15", "INT-46", "INT-17", "INT-37", "INT-05"]


def test_things_to_set_up_are_a_process(client):
    out = _ask(client, "Orders need CPMO approval before they become activating. When an order becomes active, notify the account manager")
    assert out["route"] == "process" and "approval rule" in out["why"]
    assert {c["kind"] for c in out["blueprint"]["components"]} >= {"approval"}


def test_a_journey_of_stages_is_a_process(client):
    out = _ask(client, B2C_JOURNEY)
    assert out["route"] == "process" and "journey" in out["why"]
    asked = {q["id"] for q in out["blueprint"]["questions"]}
    assert any(q.endswith(".record") for q in asked)  # which record holds the journey's progress


def test_what_no_engine_can_do_is_said_not_attempted(client):
    out = _ask(client, "Every night sync approved quotes with Salesforce")
    assert out["route"] == "process" and out["blueprint"]["engineering"][0]["need"] == "Integration with an outside system"


def test_what_it_cannot_tell_is_said_plainly(client):
    out = _ask(client, "Make everything better for the customers")
    assert out["route"] == "unknown" and "Rephrase" in out["why"]


def test_setting_up_a_process_is_for_administrators(client):
    rep = _user(client, "assistant-rep")
    out = _ask(client, "Orders need CPMO approval before they become activating", headers=_as(rep))
    assert out["route"] == "process" and out["needs_admin"] and "blueprint" not in out
    assert _ask(client, "Add prospect Rep Co", headers=_as(rep))["route"] == "action"  # actions stay open to them


def test_every_request_is_in_the_journal_with_its_outcome(client):
    first = _ask(client, "Add prospect Journal Co")
    _ask(client, "Make everything better for the customers")
    assert client.post(f"/api/assistant/journal/{first['journal_id']}/outcome", json={"status": "done", "summary": "Created Customer #1"}).status_code == 204
    mine = client.get("/api/assistant/journal").json()
    assert [(j["request"], j["route"]) for j in mine] == [("Make everything better for the customers", "unknown"), ("Add prospect Journal Co", "action")]
    assert mine[1]["outcome"]["status"] == "done" and mine[0]["outcome"] is None
    # someone else's journal is theirs; an administrator can see the whole tenant's
    rep = _user(client, "journal-rep")
    _ask(client, "Add prospect Rep Journal Co", headers=_as(rep))
    assert [j["request"] for j in client.get("/api/assistant/journal", headers=_as(rep)).json()] == ["Add prospect Rep Journal Co"]
    assert client.post(f"/api/assistant/journal/{first['journal_id']}/outcome", json={"status": "done"}, headers=_as(rep)).status_code == 404
    everyone = client.get("/api/assistant/journal?everyone=true").json()
    assert len(everyone) == 3 and {j["by"] for j in everyone} >= {"journal-rep"}


def test_a_chain_that_worked_is_remembered_for_an_exact_repeat(client):
    _launch_bundle(client, name="Fiber 900", price=900)
    goal = "Add prospect Memo Co, then generate a lead for them interested in Fiber 900, then approve the lead"
    plan = _ask(client, goal)["plan"]
    assert plan["planner"] == "rules"
    steps = [{"intent_type": s["intent_type"], "entities": s["entities"], "text": s["text"]} for s in plan["steps"]]
    assert client.post("/api/agent/run", json={"goal": goal, "steps": steps, "mode": "rehearse"}).json()["status"] == "rehearsed"
    assert _ask(client, goal)["plan"]["planner"] == "rules"  # a rehearsal is not remembered
    assert client.post("/api/agent/run", json={"goal": goal, "steps": steps, "mode": "execute"}).json()["status"] == "done"
    again = _ask(client, goal)
    assert again["memory"] == "chain" and again["plan"]["planner"] == "memory"
    assert [s["intent_type"] for s in again["plan"]["steps"]] == [s["intent_type"] for s in steps]
