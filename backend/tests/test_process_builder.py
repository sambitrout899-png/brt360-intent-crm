"""The Process Builder: a process said in plain English becomes approval
rules, Flow Studio flows, Page Designer changes and assignment rules; what
it can't tell is asked, what no engine can do is reported as an engineering
change, a rehearsal keeps nothing, and a build is all-or-nothing and proven
live afterwards."""

from test_access_guards import _as, _user

JOURNEY = ("I want to add a new B2C process - customer->identity verification->raise order ->installation"
           "->service activation on network->connectivity testing->acceptance->start billing->installed asset")


def test_arrow_journey_compiles_pages_fields_and_guarded_flows(client):
    from app.models.crm import Party
    from app.models.flows import FlowDefinition
    from app.orchestration import flow_engine
    from app.crm_core import custom_field_service
    from app.data_intelligence import audit
    import pytest
    with client.TestSessionLocal() as db:
        party = Party(tenant_id=1, name="Journey test customer", party_type="individual")
        db.add(party)
        from app.crm_core import process_service
        process_service.party_profile(party, create=True).segment = "B2C"
        db.commit()
        party_id = party.id
    first = _design(client, JOURNEY)
    assert first["steps"][0]["kind"] == "journey"
    assert [q["id"] for q in first["questions"]] == ["c1.record"]
    bp = _design(client, JOURNEY, steps=first["steps"], answers={"c1.record": "party"})
    assert bp["ready"] and not bp["engineering"]
    assert len(bp["components"]) == 10
    for component in bp["components"]:
        if component["kind"] == "flow":
            assert all(isinstance(n.get("label"), str) and n["label"]
                       for n in component["spec"]["graph"]["nodes"])
    page = next(c for c in bp["components"] if c["kind"] == "page")
    assert len(page["spec"]["ops"][0]["values"]) == 9
    payload = {"request": JOURNEY, "steps": bp["steps"], "answers": {"c1.record": "party"}}
    before = client.get("/api/admin/flows").json()
    result = client.post("/api/process-builder/build", json={**payload, "mode": "rehearse"}).json()
    assert result["status"] == "rehearsed", result
    assert client.get("/api/admin/flows").json() == before
    result = client.post("/api/process-builder/build", json={**payload, "mode": "execute"}).json()
    assert result["status"] == "built", result
    assert all(c["ok"] for c in result["checks"]), result["checks"]
    assert result["memory_id"]
    remembered = _design(client, JOURNEY)
    assert remembered["planner"] == "memory" and remembered["ready"]
    assert remembered["answers"]["c1.record"] == "party"
    recipes = client.get("/api/process-builder/recipes").json()
    assert recipes[0]["request"] == JOURNEY
    # Simulate an already-deployed journey from before node labels were generated.
    import json
    with client.TestSessionLocal() as db:
        for flow in db.query(FlowDefinition).all():
            graph = json.loads(flow.graph_json)
            for node in graph["nodes"]:
                node.pop("label", None)
            flow.graph_json = json.dumps(graph)
        db.commit()
    count = len(client.get("/api/admin/flows").json())
    repeated = client.post("/api/process-builder/build", json={"request": JOURNEY, "mode": "execute"}).json()
    assert repeated["status"] == "built", repeated
    assert len(client.get("/api/admin/flows").json()) == count
    with client.TestSessionLocal() as db:
        flows = [db.get(FlowDefinition, m["id"]) for m in result["made"] if m["kind"] == "flow"]
        ctx = {"entities": {"party_id": party_id}}
        with pytest.raises(flow_engine.FlowRejected):
            flow_engine.run_flow(db, 1, None, flows[1], ctx)
        db.rollback()
        log = audit.start_execution(db, 1, "Journey runtime test")
        for flow in flows:
            flow_engine.run_flow(db, 1, log.id, flow, ctx)
        values = custom_field_service.get_values(db, 1, "party", party_id)
        assert values[page["spec"]["ops"][0]["key"]] == "installed asset"
        with pytest.raises(flow_engine.FlowRejected):
            flow_engine.run_flow(db, 1, log.id, flows[-1], ctx)
        db.rollback()


def test_process_memory_is_tenant_scoped_and_only_saved_on_execute(client):
    from app.orchestration import process_memory
    from app.data_intelligence import audit
    with client.TestSessionLocal() as db:
        # A rehearsal or failed recipe must never be recalled.
        row = audit.start_execution(db, 1, "example", channel="process_memory")
        row.intent_type = process_memory.KIND
        row.status = "failed"
        row.context_json = '{"version":1,"request":"example","steps":[]}'
        db.commit()
        assert process_memory.recall(db, 1, "example") is None
        row.status = "executed"
        db.commit()
        assert process_memory.recall(db, 1, "example")
        assert process_memory.recall(db, 999, "example") is None

REQUEST = ("Set up a discount approval process. "
           "Quotes with discount above 20% need approval from the Regional Sales Head, then the Finance Controller. "
           "Once approved, notify the account manager that the quote is cleared. "
           "On the quote page add a mandatory field Discount reason. "
           "Assign all new leads to Inside Sales round robin. "
           "Orders need CPMO approval before they become activating. "
           "Every night push approved quotes to SAP.")


def _design(client, request=REQUEST, **extra):
    resp = client.post("/api/process-builder/design", json={"request": request, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _defaults(blueprint):
    return {q["id"]: q["default"] for q in blueprint["questions"] if q.get("default")}


def _settled(client, request=REQUEST, answers=None):
    """Design, answer every question with its default, design again."""
    first = _design(client, request)
    answers = {**_defaults(first), **(answers or {})}
    for _ in range(4):
        again = _design(client, request, steps=first["steps"], answers=answers)
        if not again["questions"]:
            return first["steps"], answers, again
        answers.update(_defaults(again))
    raise AssertionError(again["questions"])


def _build(client, steps, answers, blueprint, mode):
    graphs = {c["id"]: c["spec"]["graph"] for c in blueprint["components"] if c["kind"] == "flow"}
    resp = client.post("/api/process-builder/build", json={"request": REQUEST, "steps": steps, "answers": answers, "graphs": graphs, "mode": mode})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_process_is_split_across_the_engines_and_unclear_parts_are_asked(client):
    bp = _design(client)
    assert bp["planner"] == "rules"
    assert bp["name"] == "Discount Approval Process"  # the intro names the process, it isn't a piece of it
    kinds = {c["id"]: c["kind"] for c in bp["components"]}
    assert kinds.get("c4") == "page" and kinds.get("c6") == "approval"
    # SAP is no engine's job - it is evaluated as an engineering change, not attempted
    assert [e["need"] for e in bp["engineering"]] == ["Integration with an outside system"]
    asked = {q["id"]: q for q in bp["questions"]}
    # positions that don't exist yet are a choice, defaulting to creating them
    assert asked["c2.approver.1"]["default"] == "new:Finance Controller"
    assert asked["c5.position"]["default"] == "new:Inside Sales"
    # "once approved" waits for the approval it follows instead of asking its own question
    assert "c3.trigger" not in asked and "c3" not in kinds
    assert not bp["ready"]


def test_answers_settle_the_design_and_the_flow_follows_the_approval(client):
    _steps, answers, bp = _settled(client)
    assert bp["ready"]
    by_id = {c["id"]: c for c in bp["components"]}
    assert by_id["c2"]["spec"]["kind"] == "monetary" and by_id["c2"]["spec"]["min_discount_pct"] == 20.01
    assert [t[0] for t in by_id["c2"]["spec"]["tiers"]] == ["Regional Sales Head", "Finance Controller"]
    assert by_id["c3"]["kind"] == "flow"
    assert by_id["c3"]["spec"]["trigger"] == {"trigger_type": "state_enter", "entity_type": "quote", "trigger_state": "approved",
                                              "summary": "When a quote is approved"}
    assert by_id["c5"]["spec"]["strategy"] == "round_robin"
    assert by_id["c6"]["spec"]["trigger_state"] == "activating"
    assert "Finance Controller" in bp["new_positions"] and "Inside Sales" in bp["new_positions"]


def test_rehearse_keeps_nothing_then_build_makes_it_live(client):
    steps, answers, bp = _settled(client)
    rules_before = client.get("/api/admin/approval-rules").json()
    flows_before = client.get("/api/admin/flows").json()

    seen = _build(client, steps, answers, bp, "rehearse")
    assert seen["status"] == "rehearsed", seen
    assert len(seen["made"]) == 5 and seen["impact"]["records_created"]
    assert client.get("/api/admin/approval-rules").json() == rules_before
    assert client.get("/api/admin/flows").json() == flows_before

    done = _build(client, steps, answers, bp, "execute")
    assert done["status"] == "built", done
    assert all(c["ok"] for c in done["checks"]), done["checks"]
    rules = {r["name"]: r for r in client.get("/api/admin/approval-rules").json()}
    quote_rule = rules["Quotes (B2B) - discount above 20% approval"]
    assert [t["approver_tier"] for t in quote_rule["tiers"]] == ["Regional Sales Head", "Finance Controller"] and quote_rule["is_active"]
    # an overlapping older rule was switched off (the default answer), so the new one is what the engine picks
    assert all(not r["is_active"] for r in rules.values() if r["id"] in by_spec_ids(bp))
    flows = [f for f in client.get("/api/admin/flows").json() if f["trigger_state"] == "approved"]
    assert flows and flows[0]["is_active"] and flows[0]["entity_type"] == "quote"


def by_spec_ids(bp):
    return {i for c in bp["components"] for i in (c["spec"].get("replace_ids") or [])}


def test_a_stage_already_guarded_is_a_choice(client):
    request = "Orders need CPMO approval before they become activating"
    steps, answers, bp = _settled(client, request)
    graphs: dict = {}
    built = client.post("/api/process-builder/build", json={"request": request, "steps": steps, "answers": answers, "graphs": graphs,
                                                             "mode": "execute"}).json()
    assert built["status"] == "built", built
    again = _design(client, "Orders need Sales Director approval before they become activating")
    clash = next(q for q in again["questions"] if q["id"].endswith(".clash"))
    assert {o["value"] for o in clash["options"]} == {"replace", "keep"}
    kept = _design(client, "Orders need Sales Director approval before they become activating", steps=again["steps"],
                   answers={**_defaults(again), clash["id"]: "keep"})
    assert kept["components"] == [] and kept["skipped"]


def test_an_unclear_sentence_is_asked_and_can_be_rephrased_or_left_out(client):
    request = "Make everything better for the customers"
    bp = _design(client, request)
    question = bp["questions"][0]
    assert question["id"] == "c1.unclear" and question["allow_text"]
    skipped = _design(client, request, steps=bp["steps"], answers={"c1.unclear": "skip"})
    assert skipped["components"] == [] and skipped["skipped"] == [request] and not skipped["questions"]
    rephrased = _design(client, request, steps=bp["steps"], answers={"c1.unclear": "When an order becomes active, notify the account manager"})
    assert [c["kind"] for c in rephrased["components"]] == ["flow"]


def test_engineering_only_requests_build_nothing(client):
    request = "Every night sync approved quotes with Salesforce. Send WhatsApp messages to customers."
    bp = _design(client, request)
    assert [e["need"] for e in bp["engineering"]] == ["Integration with an outside system", "A new notification channel"]
    out = client.post("/api/process-builder/build", json={"request": request, "steps": bp["steps"], "mode": "execute"}).json()
    assert out["status"] == "nothing" and "engineering" in out["message"]


def test_the_ai_rewrites_a_loose_request_but_the_engines_still_check_it(client, monkeypatch):
    from app.crm_core import llm

    def fake(target, system, user, temperature=0.0):
        if "builder that uses ONLY these engines" not in system:
            return {}  # the flow / page planners' own calls: fall back to their rule readers
        return {"name": "Big deal control", "steps": [
            {"kind": "approval", "text": "Orders need approval from the CPMO before they become activating"},
            {"kind": "engineering", "text": "Score every deal with a churn model"},
            {"kind": "magic", "text": "Make it all faster"},  # not an engine - dropped
        ]}

    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    monkeypatch.setattr(llm, "chat_json", fake)
    bp = _design(client, "Big orders: CPMO clearance before activating. Deals scored by a churn model. Faster please")
    assert bp["planner"] == "ai" and bp["name"] == "Big deal control"
    assert [s["kind"] for s in bp["steps"]] == ["approval", "engineering"]
    assert [c["kind"] for c in bp["components"]] == ["approval"]
    assert bp["components"][0]["spec"]["trigger_state"] == "activating"
    assert bp["engineering"][0]["need"] == "A predictive model"


def test_a_chain_of_actions_is_handed_to_the_agent(client):
    """Nothing to configure - it is things to DO, in order: the agent's plan comes back."""
    from test_agent import SALES_CHAIN

    bp = _design(client, SALES_CHAIN)
    assert bp["mode"] == "actions", bp
    assert [s["intent_type"] for s in bp["actions"]["steps"]] == ["INT-15", "INT-46", "INT-17", "INT-37", "INT-05"]
    assert not bp["questions"] and not bp["engineering"]


AUTOMATIONS = ("When a lead becomes qualified, send it to Presales for check. Once qualified, auto qualify the lead. "
               "Once passed feasibility, auto create a quote.")


def _intent(client, code, entities):
    out = client.post("/api/intents", json={"text": code, "structured": {"intent_type": code, "entities": entities}}).json()
    assert out["status"] == "executed", out
    return out


def test_automations_run_the_tenants_own_actions_end_to_end(client):
    """"When ..., send it to Presales" / "auto create a quote" - flows whose step runs an action.
    A rule that would trigger itself is asked about; a step that needs an earlier stage offers it."""
    from test_phase5_fulfillment import _launch_bundle

    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    first = _design(client, AUTOMATIONS)
    asked = {q["id"]: q for q in first["questions"]}
    # "Once qualified, qualify the lead" would do nothing - when should it happen? (suggests: when the lead is created)
    assert asked["c2.trigger"]["default"] == "state:lead:pending_qualification"
    # Send to Presales needs a configured product - offered first
    assert asked["c1.prereq"]["default"] == "yes"
    steps, answers, bp = _settled(client, AUTOMATIONS)
    flows = {c["id"]: [n["config"].get("intent") for n in c["spec"]["graph"]["nodes"] if n["type"] == "action"] for c in bp["components"]}
    assert flows == {"c1": ["INT-37", "INT-38"], "c2": ["INT-17"], "c3": ["INT-05"]}
    built = client.post("/api/process-builder/build", json={"request": AUTOMATIONS, "steps": steps, "answers": answers,
                                                             "graphs": {c["id"]: c["spec"]["graph"] for c in bp["components"]}, "mode": "execute"}).json()
    assert built["status"] == "built" and all(c["ok"] for c in built["checks"]), built

    # live: a new lead is qualified, configured and sent to Presales by itself - the customer's address is the site
    party = _intent(client, "INT-15", {"name": "AutoCo", "details": {"address_line1": "12 Ring Road"}})["result"]["party_id"]
    lead = _intent(client, "INT-46", {"party_id": party, "products": ["Fiber 500 Bundle"]})
    ran = {f["flow_name"]: [t for t in f["trace"] if t["type"] == "action"] for f in lead["result"]["flows_run"]}
    assert [t["intent"] for t in ran["Auto qualify the lead"]] == ["INT-17"]
    presales = ran["Send it to Presales for check"]
    assert [t["intent"] for t in presales] == ["INT-37", "INT-38"] and not any(t.get("skipped") for t in presales)
    opportunity = presales[0]["result_ids"]["opportunity_id"]
    # Presales finds it feasible -> the quote is created automatically
    feasible = _intent(client, "INT-18", {"opportunity_id": opportunity, "decision": "feasible"})
    quote_flow = next(f for f in feasible["result"]["flows_run"] if f["flow_name"] == "Auto create a quote")
    made = next(t for t in quote_flow["trace"] if t["type"] == "action")
    assert made["intent"] == "INT-05" and made["result_ids"].get("quote_id"), made


def test_the_agent_works_alongside_the_automations(client):
    """Steps an automation already did are done, not failed; what it created is passed on; the quote it
    made is revised with the asked discount instead of a second quote being made."""
    from test_agent import PROSPECT_TO_ORDER, _plan
    from test_phase5_fulfillment import _launch_bundle

    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    steps, answers, bp = _settled(client, AUTOMATIONS)
    client.post("/api/process-builder/build", json={"request": AUTOMATIONS, "steps": steps, "answers": answers,
                                                     "graphs": {c["id"]: c["spec"]["graph"] for c in bp["components"]}, "mode": "execute"})
    goal = PROSPECT_TO_ORDER.replace('Prospect"GGG"', 'Prospect "bbb" with address "5 MG Road"')
    run = [{"intent_type": s["intent_type"], "entities": s["entities"], "text": s["text"]} for s in _plan(client, goal)["steps"]]
    out = client.post("/api/agent/run", json={"goal": goal, "steps": run, "mode": "rehearse"}).json()
    fix = out["stopped"]["repairs"][0]  # the customer's acceptance before the order
    run = run[:fix["at"] - 1] + fix["insert"] + run[fix["at"] - 1:]
    done = client.post("/api/agent/run", json={"goal": goal, "steps": run, "mode": "execute"}).json()
    assert done["status"] == "done", done
    summaries = [s["summary"] for s in done["steps"]]
    assert all(summaries[i].startswith("Already done") for i in (2, 3, 4)), summaries  # qualify, configure, send to presales
    assert done["steps"][6]["intent_type"] == "INT-19" and "revised it" in summaries[6]
    quote_id = done["memory"]["quote_id"]
    quotes = [r for r in done["impact"]["records_created"] if r["entity"] == "quote"]
    assert len(quotes) == 1 and done["memory"].get("order_id")
    record = client.get(f"/api/records/quote/{quote_id}").json()
    assert "10" in str(record)  # the 10% discount is on the one quote


def test_a_failing_automation_never_undoes_the_users_action(client):
    """No address for the site: Send to Presales is skipped with the reason - the lead is still created."""
    from test_phase5_fulfillment import _launch_bundle

    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    steps, answers, bp = _settled(client, AUTOMATIONS)
    client.post("/api/process-builder/build", json={"request": AUTOMATIONS, "steps": steps, "answers": answers,
                                                     "graphs": {c["id"]: c["spec"]["graph"] for c in bp["components"]}, "mode": "execute"})
    party = _intent(client, "INT-15", {"name": "NoAddressCo"})["result"]["party_id"]
    lead = _intent(client, "INT-46", {"party_id": party, "products": ["Fiber 500 Bundle"]})
    presales = next(f for f in lead["result"]["flows_run"] if f["flow_name"] == "Send it to Presales for check")
    send = next(t for t in presales["trace"] if t.get("intent") == "INT-38")
    assert "address" in send["skipped"].lower()


def test_the_ai_cannot_answer_with_something_the_user_never_asked_for(client, monkeypatch):
    """A small model may answer with the examples from its own instructions - those sentences are dropped."""
    from app.crm_core import llm

    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    monkeypatch.setattr(llm, "chat_json", lambda target, system, user, temperature=0.0: {"name": "Sales Process", "steps": [
        {"kind": "approval", "text": "Quotes with discount above 20% need approval from the Regional Sales Head, then the Finance Controller"},
        {"kind": "page", "text": "On the quote page add a mandatory field Discount reason"},
    ]} if "builder that uses ONLY these engines" in system else {})
    bp = _design(client, "make onboarding smoother for wholesale partners")
    assert bp["components"] == [] and bp["planner"] == "rules"


def test_a_page_change_on_a_field_that_does_not_exist_creates_it(client):
    """An AI page plan may say "make discount_reason mandatory" without adding it first - the field is created, no error."""
    from app.crm_core import page_service
    from app.models.custom_fields import CustomFieldDefinition

    db = client.TestSessionLocal()
    try:
        tenant_id = db.query(CustomFieldDefinition.tenant_id).first()
        from app.models.tenant import Tenant
        from app.tenancy import DEFAULT_TENANT_SLUG

        tenant_id = db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
        page_service.seed_defaults(db, tenant_id)
        found = page_service.find_page(db, tenant_id, "quote")
        page_service.apply_page_ops(db, tenant_id, found["entity_type"], found["segment"],
                                    [{"op": "mandatory", "key": "discount_reason"}], found["page_key"])
        db.commit()
        field = db.query(CustomFieldDefinition).filter(CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.key == "discount_reason").one()
        assert field.label == "Discount reason" and field.entity_type == "quote"
        placed = [f for s in page_service.page_by_key(db, tenant_id, found["page_key"])["layout"]["sections"] for f in s["fields"]
                  if f.get("key") == "discount_reason"]
        assert placed and placed[0]["mandatory"]
    finally:
        db.close()


def test_a_chain_of_things_to_do_never_reaches_the_ai_rewrite(client, monkeypatch):
    """A sentence of actions goes to the agent before any AI rewrite could replace it with something else."""
    from app.crm_core import llm
    from test_agent import PROSPECT_TO_ORDER

    calls: list = []
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    def unavailable(target, system, user, temperature=0.0):
        calls.append(system)
        raise llm.LlmUnavailable("model down")  # every caller then uses its rules

    monkeypatch.setattr(llm, "chat_json", unavailable)
    bp = _design(client, PROSPECT_TO_ORDER)
    assert bp["mode"] == "actions" and len(bp["actions"]["steps"]) == 9
    assert not any("builder that uses ONLY these engines" in s for s in calls)


def test_only_administrators_build_processes(client):
    agent = _user(client, "builder-agent")
    resp = client.post("/api/process-builder/design", json={"request": REQUEST}, headers=_as(agent))
    assert resp.status_code == 403
    assert client.get("/api/process-builder/recipes", headers=_as(agent)).status_code == 403
