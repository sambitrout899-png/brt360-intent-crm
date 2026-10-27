"""Flow Studio field intelligence: the field catalog (fields, types,
picklist values, states, users, positions, recently used values) and the
reviewer that auto-corrects sure mistakes and suggests the rest."""

import pytest


@pytest.fixture()
def setup(client):
    client.post(
        "/api/admin/custom-fields",
        json={"entity_type": "quote", "key": "contract_term", "label": "Contract term", "data_type": "picklist", "allowed_values": "12m,24m,36m"},
    )
    client.post("/api/admin/custom-fields", json={"entity_type": "quote", "key": "strategic", "label": "Strategic deal", "data_type": "boolean"})
    client.post("/api/admin/positions", json={"name": "Deal Desk"})
    client.post("/api/intents", json={"text": "Launch a new bundle. name: Fiber 800, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 1000, discount: 0"})
    return client


def _catalog(client, **params):
    return client.get("/api/admin/flows/field-catalog", params={"entity_type": "quote", "trigger_intent_type": "INT-05", **params}).json()


def _review(client, *nodes, **extra):
    graph = {"nodes": [{"id": f"n{i}", "type": t, "label": t, "config": c} for i, (t, c) in enumerate(nodes)], "edges": []}
    body = {"entity_type": "quote", "trigger_type": "intent", "trigger_intent_type": "INT-05", "graph": graph, **extra}
    resp = client.post("/api/admin/flows/review", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_catalog_lists_every_field_with_types_and_values(setup):
    catalog = _catalog(setup)
    fields = {f["path"]: f for f in catalog["fields"]}
    assert fields["record.total_price"]["data_type"] == "number"
    assert fields["record.contract_term"]["values"] == ["12m", "24m", "36m"] and fields["record.contract_term"]["settable"]
    assert fields["record.strategic"]["data_type"] == "boolean"
    assert "approved" in fields["record.state"]["values"]
    assert fields["record.total_price"]["settable"] is False  # computed, a flow cannot overwrite it
    # what the triggering intent carries, including the tenant's offerings
    assert fields["entities.requested_discount_pct"]["data_type"] == "number"
    assert "Fiber 800" in fields["entities.offering_name"]["values"]
    assert "Deal Desk" in catalog["positions"]
    assert any(s["code"] == "pending_approval" for s in catalog["states"])
    assert catalog["users"] and "email" in catalog["channels"]


def test_sure_mistakes_are_auto_corrected(setup):
    out = _review(
        setup,
        ("condition", {"field": "Total Price", "operator": ">", "value": "5000"}),
        ("condition", {"field": "totl_price", "operator": "gt", "value": "10%"}),
        ("condition", {"field": "contract term", "operator": "eq", "value": "24M"}),
        ("condition", {"field": "requested_discount_pct", "operator": "gt", "value": "30"}),
        ("set_field", {"field": "Strategic deal", "value": "yes"}),
        ("transition", {"to_state": "Pending Approval"}),
        ("assign", {"to_position": "deal desk"}),
        ("notify", {"channel": "Email", "audience": "owner", "message": "Quote {{Total Price}} needs review"}),
    )
    cfg = [n["config"] for n in out["graph"]["nodes"]]
    assert cfg[0] == {"field": "record.total_price", "operator": "gt", "value": "5000"}
    assert cfg[1]["field"] == "record.total_price" and cfg[1]["value"] == "10"
    assert cfg[2] == {"field": "record.contract_term", "operator": "eq", "value": "24m"}
    assert cfg[3]["field"] == "record.requested_discount_pct"  # bare names resolve record-first, as at run time
    assert cfg[4] == {"field": "record.strategic", "value": "true"}
    assert cfg[5]["to_state"] == "pending_approval"
    assert cfg[6]["to_position"] == "Deal Desk"
    assert cfg[7]["channel"] == "email" and cfg[7]["message"] == "Quote {{record.total_price}} needs review"
    assert out["issues"] == []
    assert len(out["corrections"]) >= 9 and all(c["before"] != c["after"] for c in out["corrections"])


def test_without_a_record_type_fields_come_from_the_intent(setup):
    out = _review(setup, ("condition", {"field": "Requested discount %", "operator": "gt", "value": "30"}), entity_type=None)
    assert out["graph"]["nodes"][0]["config"]["field"] == "entities.requested_discount_pct"


def test_unsure_or_impossible_references_are_reported_not_guessed(setup):
    out = _review(
        setup,
        ("condition", {"field": "contract_term", "operator": "eq", "value": "48m"}),
        ("condition", {"field": "flux_capacitor", "operator": "eq", "value": "x"}),
        ("condition", {"field": "contract_term", "operator": "gt", "value": "12m"}),
        ("set_field", {"field": "total_price", "value": "1"}),
        ("transition", {"to_state": "teleported"}),
    )
    messages = [i["message"] for i in out["issues"]]
    assert any("'48m' is not a value of Contract term" in m for m in messages)
    assert any("flux_capacitor" in m for m in messages)
    assert any("needs a number" in m for m in messages)
    assert any("cannot be set by a flow" in m for m in messages)
    assert any("No state called 'teleported'" in m for m in messages)
    assert out["graph"]["nodes"][0]["config"]["value"] == "48m"  # left as typed


def test_save_applies_fixes_and_reports_them(setup):
    resp = setup.post(
        "/api/admin/flows",
        json={
            "name": "Big quotes to deal desk",
            "entity_type": "quote",
            "trigger_type": "intent",
            "trigger_intent_type": "INT-05",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start", "label": "Start", "config": {}},
                    {"id": "c", "type": "condition", "label": "Big?", "config": {"field": "Total price", "operator": "gte", "value": "5000"}},
                    {"id": "a", "type": "assign", "label": "Desk", "config": {"to_position": "DEAL DESK"}},
                    {"id": "x", "type": "condition", "label": "Term", "config": {"field": "contract_term", "operator": "eq", "value": "99m"}},
                    {"id": "e", "type": "end", "label": "End", "config": {}},
                ],
                "edges": [
                    {"id": "e1", "source": "s", "target": "c", "label": ""},
                    {"id": "e2", "source": "c", "target": "a", "label": "true"},
                    {"id": "e3", "source": "c", "target": "x", "label": "false"},
                    {"id": "e4", "source": "a", "target": "e", "label": ""},
                    {"id": "e5", "source": "x", "target": "e", "label": "true"},
                    {"id": "e6", "source": "x", "target": "e", "label": "false"},
                ],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    flow = resp.json()
    nodes = {n["id"]: n["config"] for n in flow["graph"]["nodes"]}
    assert nodes["c"]["field"] == "record.total_price" and nodes["a"]["to_position"] == "Deal Desk"
    assert {c["after"] for c in flow["corrections"]} >= {"record.total_price", "Deal Desk", "record.contract_term"}
    assert any("99m" in w for w in flow["warnings"])

    # the designer remembers values used before
    fields = {f["path"]: f for f in _catalog(setup)["fields"]}
    assert "5000" in fields["record.total_price"]["recent"]


def test_feasibility_passed_moves_opportunity_and_fires_state_flow(client):
    """The documented recipe: a custom 'feasibility_passed' state, an after-INT-18
    flow branching on result.outcome, and a state_enter flow on that state."""
    model = next(m for m in client.get("/api/admin/state-models").json() if m["entity_type"] == "opportunity")
    states = model["states"] + [{"code": "feasibility_passed", "label": "Feasibility Passed", "editable": True, "is_terminal": False}]
    transitions = model["transitions"] + [
        {"from_state": "draft", "to_state": "feasibility_passed", "action": "pass_feasibility", "intent_type": None, "allowed_roles": ""},
        {"from_state": "feasibility_passed", "to_state": "quoted", "action": "quote", "intent_type": "INT-05", "allowed_roles": ""},
    ]
    assert client.put("/api/admin/state-models/opportunity", json={"name": model["name"], "states": states, "transitions": transitions}).status_code == 200

    fields = {f["path"]: f for f in client.get("/api/admin/flows/field-catalog", params={"entity_type": "opportunity", "trigger_intent_type": "INT-18"}).json()["fields"]}
    assert fields["result.outcome"]["values"] == ["passed", "failed", "pending"]

    def flow(body, nodes, edges):
        graph = {"nodes": [{"id": i, "type": t, "label": i, "config": c} for i, t, c in nodes],
                 "edges": [{"id": f"{s}{d}", "source": s, "target": d, "label": l} for s, d, l in edges]}
        resp = client.post("/api/admin/flows", json={"entity_type": "opportunity", "graph": graph, **body})
        assert resp.status_code == 201, resp.text
        assert client.post(f"/api/admin/flows/{resp.json()['id']}/activate").status_code == 200
        return resp.json()

    a = flow(
        {"name": "Feasibility gate", "trigger_type": "intent", "trigger_intent_type": "INT-18", "timing": "after"},
        [("s", "start", {}), ("c", "condition", {"field": "Outcome", "operator": "=", "value": "Passed"}),
         ("t", "transition", {"to_state": "Feasibility Passed"}), ("e", "end", {})],
        [("s", "c", ""), ("c", "t", "true"), ("c", "e", "false"), ("t", "e", "")],
    )
    assert a["graph"]["nodes"][1]["config"] == {"field": "result.outcome", "operator": "eq", "value": "passed"} and not a["warnings"]
    flow(
        {"name": "On feasibility passed", "trigger_type": "state_enter", "trigger_state": "feasibility_passed"},
        [("s", "start", {}), ("n", "notify", {"channel": "email", "audience": "owner", "message": "{{record.name}} is feasible"}), ("e", "end", {})],
        [("s", "n", ""), ("n", "e", "")],
    )

    opp = client.post("/api/intents", json={"text": "Qualify this lead. company_name: Feasible Co, source: referral, region: North"}).json()["result"]["opportunity_id"]
    res = client.post("/api/intents", json={"text": f"Run feasibility for opportunity {opp}, address: 12 King Fahd Road, Riyadh"}).json()
    assert res["result"]["outcome"] == "passed"
    assert [r["flow_name"] for r in res["result"]["flows_run"]] == ["Feasibility gate", "On feasibility passed"]
    assert {"entity_type": "opportunity", "entity_id": opp, "from_state": "draft", "to_state": "feasibility_passed"} in res["result"]["state_entries"]


def test_typed_intent_result_label_becomes_a_result_path(setup):
    out = _review(setup, ("condition", {"field": "Intent result: Quote status", "operator": "eq", "value": "approved"}))
    assert out["graph"]["nodes"][0]["config"]["field"] == "result.quote_status" and out["issues"] == []


def test_ai_draft_is_corrected_against_real_fields(setup):
    draft = setup.post(
        "/api/admin/flows/ai-generate",
        json={"prompt": "If total price is greater than 5000, set contract term to 24M", "entity_type": "quote", "trigger_intent_type": "INT-05"},
    ).json()
    nodes = [n for n in draft["graph"]["nodes"] if n["type"] in ("condition", "set_field")]
    assert nodes[0]["config"]["field"] == "record.total_price"
    assert nodes[1]["config"] == {"field": "record.contract_term", "value": "24m"}
    assert draft["corrections"]
