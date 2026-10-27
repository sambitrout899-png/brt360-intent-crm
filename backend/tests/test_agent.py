"""The agent: one goal -> a chain of intents, each step handing what it
created to the next; rehearsed with nothing kept, then run all-or-nothing;
pausing with the question when a step can't go ahead."""

from test_phase5_fulfillment import _launch_bundle

GOAL = ("Qualify this lead. company_name: AgentCo, source: referral, region: North "
        "then Configure and quote this bundle. offering_name: Fiber 900 "
        "then Approve this quote "
        "and then Generate proposal for this quote")


def _plan(client, goal=GOAL):
    planned = client.post("/api/agent/plan", json={"goal": goal})
    assert planned.status_code == 200, planned.text
    return planned.json()


def test_a_goal_becomes_a_chain_of_intents(client):
    plan = _plan(client)
    assert [s["intent_type"] for s in plan["steps"]] == ["INT-04", "INT-05", "INT-06", "INT-22"], plan
    assert plan["planner"] == "rules"
    # the quote step needs the opportunity the first step opens - and knows it
    quote_step = plan["steps"][1]
    assert {"field": "opportunity_id", "from_step": 1} in quote_step["needs"]
    assert {"field": "quote_id", "from_step": 2} in plan["steps"][2]["needs"]


def test_rehearse_keeps_nothing_then_run_does_it_all(client):
    _launch_bundle(client, name="Fiber 900", price=900)
    steps = [{"intent_type": s["intent_type"], "entities": s["entities"], "text": s["text"]} for s in _plan(client)["steps"]]
    leads_before = client.get("/api/crm/leads").text if client.get("/api/crm/leads").status_code == 200 else None

    seen = client.post("/api/agent/run", json={"goal": GOAL, "steps": steps, "mode": "rehearse"}).json()
    assert seen["status"] == "rehearsed", seen
    assert [s["status"] for s in seen["steps"]] == ["done"] * 4
    assert "opportunity_id" in seen["steps"][1]["filled_from_memory"]
    assert "quote_id" in seen["steps"][2]["filled_from_memory"]
    assert seen["impact"]["records_created"] and seen["impact"]["headline"].startswith("This would ")
    assert seen["memory"] == {}
    if leads_before is not None:
        assert client.get("/api/crm/leads").text == leads_before

    done = client.post("/api/agent/run", json={"goal": GOAL, "steps": steps, "mode": "execute"}).json()
    assert done["status"] == "done", done
    quote_id = done["memory"]["quote_id"]
    record = client.get(f"/api/records/quote/{quote_id}")
    assert record.status_code == 200


def test_a_step_that_cant_go_ahead_pauses_and_nothing_is_kept(client):
    _launch_bundle(client, name="Fiber 901", price=900)
    steps = [
        {"intent_type": "INT-04", "entities": {"company_name": "HalfwayCo", "source": "referral", "region": "North"}},
        {"intent_type": "INT-05", "entities": {"offering_name": "Fiber 901"}},
        {"intent_type": "INT-24", "entities": {}},  # no accepted quote yet - can't create the order
    ]
    out = client.post("/api/agent/run", json={"steps": steps, "mode": "execute"}).json()
    assert out["status"] == "paused" and out["stopped"]["step"] == 3, out
    assert [s["status"] for s in out["steps"]][:2] == ["done", "done"]
    assert "Nothing was changed" in out["message"]
    # all-or-nothing: the first two steps were not kept either
    search = client.get("/api/records/my-work")
    assert "HalfwayCo" not in search.text


PROSPECT_CHAIN = [
    {"intent_type": "INT-15", "entities": {"name": "Globex", "email": "ops@globex.com"}, "text": "Add prospect Globex, ops@globex.com"},
    {"intent_type": "INT-46", "entities": {"products": ["Fiber 500 Bundle"]}, "text": "generate a lead for them interested in Fiber 500 Bundle"},
    # the model filled in a lead id the user never gave - the lead step 2 creates must win
    {"intent_type": "INT-17", "entities": {"lead_id": 12345, "decision": "approve"}, "text": "approve the lead"},
]


def test_a_made_up_id_never_beats_the_record_an_earlier_step_creates(client):
    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    out = client.post("/api/agent/run", json={"steps": PROSPECT_CHAIN, "mode": "rehearse"}).json()
    assert out["status"] == "rehearsed", out
    assert "lead_id" in out["steps"][2]["filled_from_memory"]
    assert out["steps"][2]["entities_used"]["lead_id"] != 12345


def test_the_plan_drops_made_up_ids_and_orders_steps_by_what_they_need(client, monkeypatch):
    from app.intent_engine import classifier

    real = classifier.classify

    def inventive(text, *a, **k):
        result = real(text, *a, **k)
        if result.intent_type == "INT-17" and not result.entities.get("lead_id"):
            result.entities["lead_id"] = 12345  # what a small model does with "the lead"
        return result

    monkeypatch.setattr(classifier, "classify", inventive)
    plan = _plan(client, "Approve the lead, then add prospect Zeta Corp, zeta@zeta.test, "
                         "then generate a lead for them interested in Fiber 500 Bundle")
    assert [s["intent_type"] for s in plan["steps"]] == ["INT-15", "INT-46", "INT-17"], plan
    approve = plan["steps"][2]
    assert "lead_id" not in approve["entities"]
    assert {"field": "lead_id", "from_step": 2} in approve["needs"]
    # an id the user did write is theirs - kept, and the step stays where they put it
    plan = _plan(client, "Approve lead 7, then add prospect Zeta Corp, zeta@zeta.test")
    assert plan["steps"][0]["intent_type"] == "INT-17" and plan["steps"][0]["entities"].get("lead_id") in (7, "7")


def test_a_record_that_does_not_exist_is_asked_for_by_its_field(client):
    out = client.post("/api/agent/run", json={"steps": [
        {"intent_type": "INT-17", "entities": {"lead_id": 98765, "decision": "approve"}, "text": "approve lead 98765"}], "mode": "rehearse"}).json()
    assert out["status"] == "paused" and out["stopped"]["missing_fields"] == ["lead_id"], out
    assert "which lead" in out["message"]


def test_the_ai_planner_only_picks_from_the_users_catalog(client, monkeypatch):
    from app.crm_core import llm

    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"steps": [
        {"text": "open a lead for Zeta", "intent_type": "INT-04", "entities": {"company_name": "Zeta"}, "why": "start"},
        {"text": "wipe the database", "intent_type": "INT-999", "entities": {"all": True}, "why": "made up"},
    ]})
    plan = _plan(client, "Look after Zeta for me end to end, whatever it takes, starting from the very first contact")
    assert plan["planner"] == "ai"
    assert plan["steps"][0]["intent_type"] == "INT-04" and plan["steps"][0]["entities"] == {"company_name": "Zeta"}
    assert plan["steps"][1]["intent_type"] is None and plan["steps"][1]["entities"] == {}
    assert "can't run" in plan["steps"][1]["why"]


SALES_CHAIN = ('build a process where a prospect would be created for a company called "XXX" with address "vvvvv"- auto approve and add a lead '
               'with a product "Fiber 500 Bundle"- configure the default configuration and generate a quote out of it')


def test_a_loosely_written_sales_chain_is_planned_in_order(client):
    """Dashes as separators, "and" between two actions, values in quotes, a bare
    "auto approve", and "a quote out of it" - one ordered chain."""
    plan = _plan(client, SALES_CHAIN)
    assert [s["intent_type"] for s in plan["steps"]] == ["INT-15", "INT-46", "INT-17", "INT-37", "INT-05"], plan
    assert plan["steps"][0]["entities"] == {"name": "XXX", "details": {"address_line1": "vvvvv"}}
    assert plan["steps"][1]["entities"]["products"] == ["Fiber 500 Bundle"]
    # the approval follows the lead it approves; the opportunity it opens feeds configure and quote; the product carries on
    assert {"field": "lead_id", "from_step": 2} in plan["steps"][2]["needs"]
    assert {"field": "opportunity_id", "from_step": 3} in plan["steps"][3]["needs"]
    assert {"field": "offering_name", "from_step": 4} in plan["steps"][4]["needs"]


def test_a_skipped_stage_is_offered_as_a_repair_and_then_the_chain_completes(client):
    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    steps = [{"intent_type": s["intent_type"], "entities": s["entities"], "text": s["text"]} for s in _plan(client, SALES_CHAIN)["steps"]]
    out = client.post("/api/agent/run", json={"goal": SALES_CHAIN, "steps": steps, "mode": "execute"}).json()
    # configured opportunities are quoted only once Presales finds them feasible - a choice, never done silently
    assert out["status"] == "paused" and out["stopped"]["step"] == 5, out
    fix = out["stopped"]["repairs"][0]
    assert fix["id"] == "presales" and [s["intent_type"] for s in fix["insert"]] == ["INT-38", "INT-18"]
    assert fix["insert"][0]["entities"]["address"] == "vvvvv"  # the prospect's address is the site to check
    assert "Nothing was changed" in out["message"]

    at = fix["at"] - 1
    fixed = steps[:at] + fix["insert"] + steps[at:]
    done = client.post("/api/agent/run", json={"goal": SALES_CHAIN, "steps": fixed, "mode": "execute"}).json()
    assert done["status"] == "done", done
    assert [s["status"] for s in done["steps"]] == ["done"] * 7
    assert "offering_name" in done["steps"][-1]["filled_from_memory"]
    assert client.get(f"/api/records/quote/{done['memory']['quote_id']}").status_code == 200


PROSPECT_TO_ORDER = ('Create a new Prospect"GGG", auto approve it - add a lead for a product "Fiber 500 Bundle" and auto configure with default '
                     'settings, auto qualify the lead and generate an opportunity - send it for presales check - auto feasibility pass - '
                     'generate an quote - give 10 percent discount ->auto approve the quote -> generate an order for installation')


def test_prospect_to_order_in_one_sentence(client):
    """Commas before "auto ...", "->" arrows, a discount said as its own part, steps that need no doing
    (a prospect has no approval; qualifying opens the opportunity) - planned, asked, repaired, run."""
    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    plan = _plan(client, PROSPECT_TO_ORDER)
    assert [s["intent_type"] for s in plan["steps"]] == ["INT-15", "INT-46", "INT-17", "INT-37", "INT-38", "INT-18", "INT-05", "INT-06", "INT-24"]
    assert plan["steps"][0]["entities"] == {"name": "GGG"}
    assert plan["steps"][6]["entities"]["requested_discount_pct"] == 10.0
    assert len(plan["notes"]) == 3 and any("needs no approval" in n for n in plan["notes"])

    steps = [{"intent_type": s["intent_type"], "entities": s["entities"], "text": s["text"]} for s in plan["steps"]]
    out = client.post("/api/agent/run", json={"goal": PROSPECT_TO_ORDER, "steps": steps, "mode": "rehearse"}).json()
    # a detail only the user knows is asked for as a field, not reported as a failure
    assert out["stopped"] == {**out["stopped"], "step": 5, "kind": "needs_input", "missing_fields": ["address"]}, out
    steps[4]["entities"]["address"] = "5 MG Road, Pune"
    out = client.post("/api/agent/run", json={"goal": PROSPECT_TO_ORDER, "steps": steps, "mode": "rehearse"}).json()
    fix = out["stopped"]["repairs"][0]
    assert out["stopped"]["step"] == 9 and fix["id"] == "proposal" and [s["intent_type"] for s in fix["insert"]] == ["INT-22", "INT-23"]
    steps = steps[:8] + fix["insert"] + steps[8:]
    done = client.post("/api/agent/run", json={"goal": PROSPECT_TO_ORDER, "steps": steps, "mode": "execute"}).json()
    assert done["status"] == "done" and len(done["steps"]) == 11, done
    assert done["memory"].get("order_id")


def test_records_a_run_creates_are_visible_to_its_next_steps_under_a_narrow_data_scope(client):
    """A position that sees only its own data: step 3 approves the lead step 2 created - visibility worked
    out by an earlier step (before the lead existed) must not hide it."""
    from test_access_guards import _as, _give, _user

    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    own = client.post("/api/admin/positions", json={"name": "Rep Own", "data_scope": "own"}).json()["id"]
    rep = _user(client, "rep-own")
    _give(client, rep, [], [own])
    chain = [
        {"intent_type": "INT-15", "entities": {"name": "Scoped Co"}, "text": "Add prospect Scoped Co"},
        {"intent_type": "INT-46", "entities": {"products": ["Fiber 500 Bundle"]}, "text": "add a lead for Fiber 500 Bundle"},
        {"intent_type": "INT-17", "entities": {"decision": "approve"}, "text": "auto qualify the lead"},
    ]
    out = client.post("/api/agent/run", json={"steps": chain, "mode": "rehearse"}, headers=_as(rep)).json()
    assert out["status"] == "rehearsed", out
    assert [s["status"] for s in out["steps"]] == ["done"] * 3


def test_configuring_with_defaults_never_fails_on_a_stale_default(client):
    """Access Medium's values were edited (Wimax, Microwave, Fiber, GPON) after its default was set to
    'Local': "configure with default settings" uses the value the product names - Fiber - and says so."""
    from app.models.product_config import OfferingAttribute
    from test_sales_to_order import say

    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    body = say(client, "Design Fiber 500 Bundle: Access Medium: Wimax, Microwave, Fiber, GPON required default Fiber")
    assert body["status"] == "executed", body["rationale"]
    db = client.TestSessionLocal()
    try:
        row = db.query(OfferingAttribute).filter(OfferingAttribute.offering_name == "Fiber 500 Bundle").one()
        row.default_value = "Local"  # a default left over from before the values changed
        db.commit()
    finally:
        db.close()
    chain = [
        {"intent_type": "INT-15", "entities": {"name": "Stale Default Co"}, "text": "Add prospect Stale Default Co"},
        {"intent_type": "INT-46", "entities": {"products": ["Fiber 500 Bundle"]}, "text": "add a lead for Fiber 500 Bundle"},
        {"intent_type": "INT-17", "entities": {"decision": "approve"}, "text": "auto qualify the lead"},
        {"intent_type": "INT-37", "entities": {}, "text": "auto configure with default settings"},
    ]
    out = client.post("/api/agent/run", json={"steps": chain, "mode": "execute"}).json()
    assert out["status"] == "done", out
    configure = out["steps"][3]
    assert "'Local'" in configure["summary"] and "used 'Fiber'" in configure["summary"], configure["summary"]


def test_auto_approve_the_quote_says_the_decision(client):
    plan = _plan(client, "Approve quote 5. Then auto approve the quote")
    assert all(s["entities"].get("decision") == "approve" for s in plan["steps"] if s["intent_type"] == "INT-06"), plan


def test_unknown_and_forbidden_actions_are_never_run(client):
    out = client.post("/api/agent/run", json={"steps": [{"intent_type": None, "text": "make me a sandwich"}], "mode": "execute"}).json()
    assert out["status"] == "paused" and out["stopped"]["kind"] == "unclear"
    plan = _plan(client, "make me a sandwich then launch a rocket")
    assert all(s["intent_type"] is None or s["intent_type"].startswith("INT-") for s in plan["steps"])
