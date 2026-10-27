"""A rule said in plain English - "if the Company type is PSU, convert the
prospect into an account automatically ... called after the Add prospect
intent" - is built as an automation on that intent with the condition, not
read as "your vanilla process already converts prospects"; remarks about the
rule ("this is not related to any particular product") are never offered as
new actions to build."""

from test_sales_to_order import run

from app.crm_core import process_service
from app.models.crm import Party

ASKED = ("Create a new process that will convert a Prospect to an Account by checking the Company type. If the Company type is PSU, "
         "then convert the prospect into an account automatically. This is not related to any particular product or prospect - should be  "
         "a generic process called after the Add prospect intent")


def _status(client, party_id):
    db = client.TestSessionLocal()
    try:
        return process_service.account_status(db.get(Party, party_id))
    finally:
        db.close()


def test_a_conditional_rule_after_an_intent_is_built_and_runs(client):
    out = client.post("/api/assistant/understand", json={"text": ASKED}).json()
    assert out["route"] == "process", out
    fit = out["fit"]
    assert not fit["new_steps"], fit["items"]  # the remark is not a new action
    rules = [i for i in fit["items"] if i["kind"] == "rule"]
    assert len(rules) == 1 and rules[0]["status"] == "gap", fit["items"]
    assert fit["build_steps"] == [{"kind": "variant", "text": "Variant: after Add a prospect (INT-15), when the prospect's Company type "
                                                             "[company_type] is PSU, run Convert to an account (INT-35)"}]
    assert "Nothing to build" not in out["why"]
    flow = out["blueprint"]["components"]
    assert [c["kind"] for c in flow] == ["flow"], out["blueprint"]
    assert flow[0]["lines"][0] == "After “Add a prospect”, only when Company type is PSU"

    built = client.post("/api/process-builder/build", json={"request": ASKED, "steps": fit["build_steps"], "answers": {}, "graphs": {}, "mode": "execute"})
    assert built.status_code == 200, built.text

    psu = run(client, "INT-15", name="Bharat Heavy Electricals", segment="B2B", details={"company_type": "PSU"})
    assert psu["status"] == "executed", psu
    assert _status(client, psu["result"]["party_id"]) == "account", psu["result"].get("flows_run")
    other = run(client, "INT-15", name="Private Co", segment="B2B", details={"company_type": "Private Limited"})
    assert _status(client, other["result"]["party_id"]) != "account"


def test_the_rule_alone_is_built_after_add_prospect(client):
    out = client.post("/api/assistant/understand", json={"text": "If the Company type is PSU, then convert the prospect into an account automatically"}).json()
    assert out["route"] == "process", out
    comps = out["blueprint"]["components"]
    assert [c["kind"] for c in comps] == ["flow"] and comps[0]["lines"][0] == "After “Add a prospect”, only when Company type is PSU", out["blueprint"]


def _ngo_flow(client):
    """A custom flow left by an earlier request - its name shares every word with "convert the prospect into an account"."""
    flow = client.post("/api/admin/flows", json={
        "name": "NGO Prospect to Account Conversion", "entity_type": "party", "trigger_type": "custom_intent",
        "trigger_phrases": "ngo prospect to account conversion",
        "graph": {"nodes": [{"id": "s", "type": "start", "label": "Start", "config": {}},
                            {"id": "c", "type": "action", "label": "Convert", "config": {"intent": "INT-35", "entities": {}}},
                            {"id": "e", "type": "end", "label": "End", "config": {}}],
                  "edges": [{"id": "1", "source": "s", "target": "c", "label": ""}, {"id": "2", "source": "c", "target": "e", "label": ""}]}}).json()
    assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 200
    return f"FLOW-{flow['id']}"


def test_the_built_in_convert_wins_over_a_look_alike_custom_flow(client):
    _ngo_flow(client)
    out = client.post("/api/assistant/understand", json={"text": ASKED}).json()
    assert out["fit"]["build_steps"] == [{"kind": "variant", "text": "Variant: after Add a prospect (INT-15), when the prospect's Company type "
                                                                    "[company_type] is PSU, run Convert to an account (INT-35)"}], out["fit"]


def test_a_variant_may_run_a_custom_flow(client):
    code = _ngo_flow(client)
    built = client.post("/api/process-builder/build", json={
        "request": "NGO prospects become accounts", "answers": {}, "graphs": {}, "mode": "rehearse",
        "steps": [{"kind": "variant", "text": f"Variant: after Add a prospect (INT-15), when the prospect's Company type [company_type] is NGO, "
                                              f"run NGO Prospect to Account Conversion ({code})"}]})
    assert built.status_code == 200, built.text
    assert "runs no step" not in built.text, built.text
    assert f'"intent": "{code}"' in built.text.replace('":"', '": "'), built.text[:2000]


def test_a_state_trigger_is_not_mistaken_for_a_field_rule(client):
    from app.orchestration import process_fit

    db = client.TestSessionLocal()
    try:
        assert process_fit.rule_variant(db, 1, "When an order is approved, notify the Sales Manager") is None
    finally:
        db.close()


STUDIO = ('Create a process for any B2B prospect, if the Company type is "PSU", then the Prospect should be auto-converted to Account '
          "after the Prospect is saved")


def test_flow_studio_drafts_the_rule_from_plain_english(client):
    """Flow Studio -> Describe the flow -> Generate, with no AI model: the built-in reader drafts it all."""
    draft = client.post("/api/admin/flows/ai-generate", json={"prompt": STUDIO, "trigger_type": "intent", "entity_type": "party"}).json()
    assert "Could not interpret" not in (draft.get("notes") or ""), draft.get("notes")
    trigger = draft["trigger"]
    assert trigger["trigger_type"] == "intent" and trigger["trigger_intent_type"] == "INT-15" and trigger["timing"] == "after", trigger
    kinds = {n["type"]: n for n in draft["graph"]["nodes"]}
    assert kinds["condition"]["config"]["value"] == "PSU" and "company_type" in kinds["condition"]["config"]["field"]
    assert kinds["action"]["config"]["intent"] == "INT-35"
    assert not draft["validation_errors"], draft["validation_errors"]

    flow = client.post("/api/admin/flows", json={"name": "PSU prospects become accounts", "entity_type": "party", "trigger_type": "intent",
                                                 "trigger_intent_type": "INT-15", "timing": "after", "graph": draft["graph"]})
    assert flow.status_code in (200, 201), flow.text
    activated = client.post(f"/api/admin/flows/{flow.json()['id']}/activate")  # the Studio's Activate button
    assert activated.status_code == 200, activated.text
    psu = run(client, "INT-15", name="Oil India", segment="B2B", details={"company_type": "PSU"})
    assert _status(client, psu["result"]["party_id"]) == "account", psu["result"].get("flows_run")
    other = run(client, "INT-15", name="Small Traders", segment="B2B", details={"company_type": "Partnership"})
    assert _status(client, other["result"]["party_id"]) != "account"
