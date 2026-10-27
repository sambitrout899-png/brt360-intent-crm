"""Flow Studio error handling: a step's "error" output, Catch errors and
Raise error steps, retries, the flow's policy for errors it doesn't catch,
"on error" flows of an intent, and classified errors in every response."""

from app.models.crm import Party
from app.models.intent_log import IntentExecutionLog


def intent(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def chain(*nodes):
    """Edges linking the nodes in order (a Catch errors step is never linked into)."""
    linked = [n for n in nodes if n["type"] != "catch"]
    return [{"id": f"e{i}", "source": a["id"], "target": b["id"], "label": ""} for i, (a, b) in enumerate(zip(linked, linked[1:]))]


def make_flow(client, name, nodes, edges, activate=True, **settings):
    body = {"name": name, "trigger_type": "intent", "trigger_intent_type": "INT-41", "timing": "after",
            "graph": {"nodes": nodes, "edges": edges}, **settings}
    response = client.post("/api/admin/flows", json=body)
    assert response.status_code == 201, response.text
    flow = response.json()
    if activate:
        assert flow["validation_errors"] == [], flow["validation_errors"]
        assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 200
    return flow


def party_named(client, name):
    db = client.TestSessionLocal()
    try:
        return db.query(Party).filter(Party.name == name).first()
    finally:
        db.close()


START = {"id": "s", "type": "start", "config": {}}
END = {"id": "end", "type": "end", "config": {}}
FAILING_ACTION = {"id": "a", "type": "action", "label": "Cancel a missing quote", "config": {"intent": "INT-20", "entities": {"quote_id": 999999}}}


def test_error_output_catches_a_failed_step_and_the_flow_carries_on(client):
    recover = {"id": "r", "type": "map", "label": "Note the failure", "config": {"values": {"code": "{{error.code}}", "what": "{{error.message}}",
                                                                                          "where": "{{error.node}}"}}}
    after = {"id": "ok", "type": "map", "label": "Normal path", "config": {"values": {"ran": "yes"}}}
    edges = [{"id": "1", "source": "s", "target": "a", "label": ""}, {"id": "2", "source": "a", "target": "ok", "label": ""},
             {"id": "3", "source": "a", "target": "r", "label": "error"}, {"id": "4", "source": "r", "target": "end", "label": ""},
             {"id": "5", "source": "ok", "target": "end", "label": ""}]
    make_flow(client, "Error output", [START, FAILING_ACTION, recover, after, END], edges)

    done = intent(client, "INT-41", name="Error Output Co", email="eo@example.test")
    assert done["status"] == "executed", done
    run = done["result"]["flows_run"][0]
    assert run["outcome"] == "handled" and run["errors"][0]["code"] == "ACTION_FAILED"
    failed = next(t for t in run["trace"] if t["node_id"] == "a")
    assert failed["status"] == "caught" and failed["error"]["intent"] == "INT-20"
    noted = run["steps"]["r"]["output"]
    assert noted["code"] == "ACTION_FAILED" and "999999" in noted["what"] and noted["where"] == "Cancel a missing quote"
    assert "ok" not in run["steps"]  # the error path replaced the normal one
    assert party_named(client, "Error Output Co") is not None


def test_the_error_output_is_never_taken_on_success(client):
    fine = {"id": "a", "type": "map", "label": "Works", "config": {"values": {"x": "1"}}}
    oops = {"id": "r", "type": "map", "label": "Error path", "config": {"values": {"y": "2"}}}
    # the error edge listed first - success must still take the normal output
    edges = [{"id": "1", "source": "s", "target": "a", "label": ""}, {"id": "2", "source": "a", "target": "r", "label": "error"},
             {"id": "3", "source": "a", "target": "end", "label": ""}]
    make_flow(client, "Success path", [START, fine, oops, END], edges)
    run = intent(client, "INT-41", name="Success Path Co")["result"]["flows_run"][0]
    assert run["outcome"] == "completed" and "r" not in run["steps"]


def test_catch_errors_handles_a_raised_error_by_code(client):
    throw = {"id": "t", "type": "throw", "label": "Credit hold", "config": {"code": "CREDIT_HOLD", "message": "{{trigger.input.name}} is on credit hold",
                                                                             "category": "business"}}
    catch = {"id": "c", "type": "catch", "label": "Handle credit hold", "config": {"codes": "CREDIT_HOLD"}}
    log = {"id": "m", "type": "map", "label": "Log it", "config": {"values": {"code": "{{error.code}}", "message": "{{error.message}}"}}}
    edges = chain(START, throw) + [{"id": "c1", "source": "c", "target": "m", "label": ""}, {"id": "c2", "source": "m", "target": "end", "label": ""}]
    make_flow(client, "Catch by code", [START, throw, catch, log, END], edges)

    done = intent(client, "INT-41", name="Credit Hold Co")
    assert done["status"] == "executed", done
    run = done["result"]["flows_run"][0]
    assert run["outcome"] == "handled"
    assert run["steps"]["m"]["output"] == {"code": "CREDIT_HOLD", "message": "Credit Hold Co is on credit hold"}
    assert next(t for t in run["trace"] if t["node_id"] == "t")["caught_by"] == "c"


def test_an_error_no_catch_matches_fails_the_intent_with_a_classified_error(client):
    throw = {"id": "t", "type": "throw", "label": "Fraud stop", "config": {"code": "FRAUD_SUSPECTED", "message": "Possible fraud"}}
    catch = {"id": "c", "type": "catch", "label": "Only validation", "config": {"codes": "validation"}}
    make_flow(client, "Uncaught", [START, throw, catch, END], chain(START, throw) + [{"id": "c1", "source": "c", "target": "end", "label": ""}])

    done = intent(client, "INT-41", name="Fraud Co")
    assert done["status"] == "failed", done
    error = done["error"]
    assert (error["code"], error["category"], error["node_id"], error["flow"]) == ("FRAUD_SUSPECTED", "business", "t", "Uncaught")
    assert done["summary"] == "Nothing was changed: Flow 'Uncaught': Possible fraud"
    assert party_named(client, "Fraud Co") is None  # the intent was undone with its flow
    # the failure is audited - the execution's own row survives the rollback
    db = client.TestSessionLocal()
    try:
        row = db.get(IntentExecutionLog, done["intent_execution_id"])
        assert row is not None and row.status == "failed"
    finally:
        db.close()


def test_catch_by_category_and_its_own_failure_is_not_caught_again(client):
    action = dict(FAILING_ACTION, config={**FAILING_ACTION["config"], "on_error": "stop"})
    catch = {"id": "c", "type": "catch", "config": {"codes": "business"}}
    rethrow = {"id": "t", "type": "throw", "label": "Translate", "config": {"code": "QUOTE_GONE", "message": "Tell sales: {{error.message}}",
                                                                              "category": "validation"}}
    other_catch = {"id": "c2", "type": "catch", "config": {}}  # would catch anything - but not a failure inside a handler
    edges = chain(START, action, END) + [{"id": "c1", "source": "c", "target": "t", "label": ""}, {"id": "c3", "source": "c2", "target": "end", "label": ""}]
    make_flow(client, "Translate errors", [START, action, catch, rethrow, other_catch, END], edges)

    done = intent(client, "INT-41", name="Translate Co")
    assert done["status"] == "failed"
    assert done["error"]["code"] == "QUOTE_GONE" and done["error"]["category"] == "validation"
    assert done["error"]["message"].startswith("Tell sales: ") and "999999" in done["error"]["message"]


def test_continue_policy_undoes_only_the_flow_and_lets_the_intent_complete(client):
    side = {"id": "p", "type": "action", "label": "Add a side customer", "config": {"intent": "INT-41", "on_error": "stop",
                                                                                     "entities": {"name": "Side Customer Ltd"}}}
    throw = {"id": "t", "type": "throw", "label": "Then fail", "config": {"code": "LATE_FAILURE", "message": "Something later went wrong"}}
    make_flow(client, "Carry on", [START, side, throw], chain(START, side, throw), on_error="continue")

    done = intent(client, "INT-41", name="Main Customer Ltd")
    assert done["status"] == "executed", done
    assert party_named(client, "Main Customer Ltd") is not None
    assert party_named(client, "Side Customer Ltd") is None  # the flow's own work was undone
    failed = done["result"]["flows_failed"][0]
    assert failed["flow_name"] == "Carry on" and failed["error"]["code"] == "LATE_FAILURE"
    assert "Not done (automation failed and was undone): Carry on: Something later went wrong" in done["summary"]
    assert "flow.failed" in done["events"]


def test_a_reject_still_turns_the_intent_down_under_continue(client):
    reject = {"id": "r", "type": "reject", "config": {"message": "Blocked by policy"}}
    make_flow(client, "Reject anyway", [START, reject], chain(START, reject), on_error="continue", timing="before")
    done = intent(client, "INT-41", name="Rejected Co")
    assert done["status"] == "failed" and done["error"]["code"] == "REJECTED"
    assert party_named(client, "Rejected Co") is None


def test_on_error_flows_run_when_the_intent_fails(client):
    from tests.test_quote_price_override import _draft_quote

    quote_id = _draft_quote(client, offering="Err Flow Fiber", company="ErrFlowCo")
    log_it = {"id": "l", "type": "action", "label": "Log the failure", "config": {
        "intent": "INT-41", "on_error": "stop", "entities": {"name": "Failure logged: {{error.code}} on {{trigger.input.quote_id}}"}}}
    make_flow(client, "Log failed overrides", [START, log_it, END], chain(START, log_it, END), trigger_intent_type="INT-116", timing="error")
    broken = {"id": "t", "type": "throw", "config": {"code": "HANDLER_BROKE", "message": "the handler itself failed"}}
    make_flow(client, "Broken handler", [START, broken], chain(START, broken), trigger_intent_type="INT-116", timing="error")

    done = intent(client, "INT-116", quote_id=quote_id, override_price=-5, reason="test")
    assert done["status"] == "failed"
    assert done["error"]["code"] == "INVALID"  # the original failure - never replaced by a handler's
    handlers = {h["flow_name"]: h for h in done["error"]["flows_run"]}
    assert handlers["Log failed overrides"]["outcome"] == "completed"
    assert handlers["Broken handler"]["outcome"] == "failed" and handlers["Broken handler"]["error"]["code"] == "HANDLER_BROKE"
    assert party_named(client, f"Failure logged: INVALID on {quote_id}") is not None  # kept, though the intent was undone
    assert "handled by: Log failed overrides" in done["summary"]


def test_retryable_errors_are_retried_and_system_errors_are_never_shown_raw(client, monkeypatch):
    from app.orchestration import flow_engine

    calls = {"n": 0}
    real = flow_engine.run_action

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection reset by peer at 10.0.0.7")
        return real(*args, **kwargs)

    monkeypatch.setattr(flow_engine, "run_action", flaky)
    action = {"id": "a", "type": "action", "label": "Add contact", "config": {"intent": "INT-41", "on_error": "stop", "retries": "2",
                                                                              "entities": {"name": "Retried Contact"}}}
    make_flow(client, "Retry", [START, action, END], chain(START, action, END))
    run = intent(client, "INT-41", name="Retry Parent")["result"]["flows_run"][0]
    step = next(t for t in run["trace"] if t["node_id"] == "a")
    assert step["status"] == "ok" and step["attempts"] == 2 and calls["n"] == 2

    # without retries: a system error fails with a reference, not the raw exception
    monkeypatch.setattr(flow_engine, "run_action", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("password=hunter2 in DSN")))
    client.post(f"/api/admin/flows/{run['flow_id']}/deactivate")
    make_flow(client, "No retry", [START, dict(action, config={**action["config"], "retries": ""}), END], chain(START, action, END))
    done = intent(client, "INT-41", name="System Error Parent")
    assert done["status"] == "failed" and done["error"]["code"] == "SYSTEM" and done["error"]["ref"]
    assert "hunter2" not in done["summary"] and done["error"]["ref"] in done["summary"]


def test_actions_keep_skipping_by_default_unless_the_flow_has_a_catch(client):
    make_flow(client, "Legacy skip", [START, FAILING_ACTION, END], chain(START, FAILING_ACTION, END))
    run = intent(client, "INT-41", name="Legacy Co")["result"]["flows_run"][0]
    step = next(t for t in run["trace"] if t["node_id"] == "a")
    assert step["status"] == "skipped" and "999999" in step["skipped"] and run["outcome"] == "completed"

    client.post(f"/api/admin/flows/{run['flow_id']}/deactivate")
    catch = {"id": "c", "type": "catch", "config": {}}
    make_flow(client, "With catch", [START, FAILING_ACTION, catch, END],
              chain(START, FAILING_ACTION, END) + [{"id": "c1", "source": "c", "target": "end", "label": ""}])
    run = intent(client, "INT-41", name="Caught Co")["result"]["flows_run"][0]
    assert next(t for t in run["trace"] if t["node_id"] == "a")["caught_by"] == "c" and run["outcome"] == "handled"


def test_invalid_error_handling_is_reported_before_activation(client):
    bad_throw = {"id": "t", "type": "throw", "config": {"code": "lower case", "message": ""}}
    catch = {"id": "c", "type": "catch", "config": {"codes": "NOT A CODE"}}
    retry = {"id": "a", "type": "action", "config": {"intent": "INT-41", "retries": "7"}}
    edges = chain(START, retry, bad_throw) + [{"id": "x", "source": "s", "target": "c", "label": ""}]
    flow = make_flow(client, "Invalid", [START, retry, bad_throw, catch], edges, activate=False)
    errors = " | ".join(client.get(f"/api/admin/flows/{flow['id']}").json()["validation_errors"])
    assert "needs a code in CAPITALS" in errors and "needs a message" in errors
    assert "nothing can link into it" in errors and "unknown error code(s) NOT A CODE" in errors
    assert "retries must be a whole number from 0 to 3" in errors
    assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 400


def test_simulation_shows_the_trace_up_to_the_failure(client):
    action = dict(FAILING_ACTION, config={**FAILING_ACTION["config"], "on_error": "stop"})
    flow = make_flow(client, "Simulate failure", [START, action, END], chain(START, action, END), activate=False)
    result = client.post(f"/api/admin/flows/{flow['id']}/simulate", json={}).json()
    assert result["ok"] is False
    assert result["error_detail"]["code"] == "ACTION_FAILED" and result["error_detail"]["node_id"] == "a"
    assert [t["node_id"] for t in result["run"]["trace"]] == ["s", "a"] and result["run"]["trace"][-1]["status"] == "failed"
    assert result["run"]["outcome"] == "failed"


def test_catalogs_offer_error_handling(client):
    catalog = client.get("/api/admin/flows/catalog").json()
    types = {t["type"]: t for t in catalog["node_types"]}
    assert {"catch", "throw"} <= set(types) and "error" in types["action"]["outputs"]
    assert "error" in catalog["timings"] and catalog["error_policies"] == ["fail", "continue"]
    codes = {c["value"] for c in catalog["error_codes"]}
    assert {"ACTION_FAILED", "NOT_FOUND", "business", "validation"} <= codes and "REJECTED" not in codes
    fields = {f["path"] for f in client.get("/api/admin/flows/field-catalog", params={"trigger_intent_type": "INT-41"}).json()["fields"]}
    assert {"error.code", "error.message", "error.category", "error.node"} <= fields
