"""Phase 7: save-then-modify with state-model locking, post-lock quote
revision, lifecycle transitions, assignment/transfer + pre-configured
assignment rules, custom fields, and AI Flow Studio flows (tagged behind
built-in intents, on state entry, and as tenant-defined custom intents).
"""

from app.auth import hash_password
from app.models.auth import User, UserRole
from app.tenancy import DEFAULT_TENANT_SLUG


def _intent(client, text):
    return client.post("/api/intents", json={"text": text}).json()


def _tenant_id(client):
    from app.models.tenant import Tenant

    db = client.TestSessionLocal()
    try:
        return db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
    finally:
        db.close()


def _make_user(client, email, name, role=UserRole.agent):
    db = client.TestSessionLocal()
    try:
        user = User(tenant_id=_tenant_id(client), email=email, password_hash=hash_password("password123"), name=name, role=role)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _quote(client, company, offering="Fiber 700", price=1000):
    _intent(client, f"Launch a new bundle. name: {offering}, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: {price}, discount: 0")
    opp_id = _intent(client, f"Qualify this lead. company_name: {company}, source: referral, region: North")["result"]["opportunity_id"]
    quote_id = _intent(client, f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: {offering}, quantity: 1")["result"]["quote_id"]
    return opp_id, quote_id


def _graph(*nodes, edges):
    return {
        "nodes": [{"id": n[0], "type": n[1], "label": n[0], "config": n[2] if len(n) > 2 else {}} for n in nodes],
        "edges": [{"id": f"e{i}", "source": e[0], "target": e[1], "label": e[2] if len(e) > 2 else ""} for i, e in enumerate(edges)],
    }


def _create_flow(client, **payload):
    resp = client.post("/api/admin/flows", json=payload)
    assert resp.status_code == 201, resp.text
    flow = resp.json()
    activated = client.post(f"/api/admin/flows/{flow['id']}/activate")
    assert activated.status_code == 200, activated.text
    return activated.json()


# ---------- save / modify / lock / revise ----------


def test_update_record_with_custom_field_then_locked_after_approval(client):
    field = client.post(
        "/api/admin/custom-fields",
        json={"entity_type": "quote", "key": "contract_term", "label": "Contract term", "data_type": "picklist", "allowed_values": "12m,24m,36m"},
    )
    assert field.status_code == 201, field.text
    _, quote_id = _quote(client, "EditableCo")

    body = _intent(client, f"Update this record. entity_type: quote, entity_id: {quote_id}, contract_term: 24M, discount_justification: strategic")
    assert body["intent_type"] == "INT-29"
    assert body["status"] == "executed", body["rationale"]
    assert body["result"]["updated_fields"] == {"contract_term": "24m", "discount_justification": "strategic"}

    bad = _intent(client, f"Update this record. entity_type: quote, entity_id: {quote_id}, contract_term: 99m")
    assert bad["status"] == "failed"
    assert "must be one of" in bad["rationale"]

    _intent(client, f"Approve this quote. quote_id: {quote_id}")
    locked = _intent(client, f"Update this record. entity_type: quote, entity_id: {quote_id}, contract_term: 36m")
    assert locked["status"] == "needs_review"
    assert "locked" in locked["rationale"]

    view = client.get(f"/api/records/quote/{quote_id}").json()
    assert view["state"] == "approved" and view["editable"] is False
    assert {"action": "revise", "to_state": "superseded", "to_label": "Superseded", "intent_type": "INT-30"} in view["available_actions"]
    assert next(f for f in view["custom_fields"] if f["key"] == "contract_term")["value"] == "24m"


def test_locked_quote_is_revised_not_edited(client):
    client.post("/api/admin/custom-fields", json={"entity_type": "quote", "key": "po_number", "label": "PO", "data_type": "text"})
    _, quote_id = _quote(client, "RevisionCo", offering="Fiber 701")
    _intent(client, f"Update this record. entity_type: quote, entity_id: {quote_id}, po_number: PO-77")

    early = _intent(client, f"Create a new revision of this quote. quote_id: {quote_id}")
    assert early["status"] == "needs_review"  # still editable - revise in place instead

    _intent(client, f"Approve this quote. quote_id: {quote_id}")
    body = _intent(client, f"Create a new revision of this quote. quote_id: {quote_id}, discount: 10")
    assert body["intent_type"] == "INT-30"
    assert body["status"] == "executed", body["rationale"]
    new_id = body["result"]["new_quote_id"]
    assert body["result"]["revision_number"] == 2
    assert body["result"]["total_price"] == 900.0

    assert client.get(f"/api/quotes/{quote_id}").json()["status"] == "superseded"
    new_view = client.get(f"/api/records/quote/{new_id}").json()
    assert new_view["state"] == "draft" and new_view["editable"] is True
    assert next(f for f in new_view["custom_fields"] if f["key"] == "po_number")["value"] == "PO-77"
    assert [r["revision_number"] for r in new_view["revisions"]] == [1, 2]


def test_party_update_and_custom_substate(client):
    party_id = _intent(client, "Add this prospect. name: Initech, email: a@initech.example")["result"]["party_id"]
    body = _intent(client, f"Update this record. entity_type: customer, entity_id: {party_id}, phone: 555-0100")
    assert body["status"] == "executed", body["rationale"]

    deactivated = _intent(client, f"Mark this record. entity_type: party, entity_id: {party_id}, action: deactivate")
    assert deactivated["intent_type"] == "INT-31"
    assert deactivated["status"] == "executed", deactivated["rationale"]
    assert deactivated["result"]["to_state"] == "inactive"

    locked = _intent(client, f"Update this record. entity_type: party, entity_id: {party_id}, phone: 555-0199")
    assert locked["status"] == "needs_review"

    reactivated = _intent(client, f"Mark this record. entity_type: party, entity_id: {party_id}, action: reactivate")
    assert reactivated["status"] == "executed", reactivated["rationale"]
    assert reactivated["result"]["to_state"] == "prospect"


def test_structured_intent_from_form_skips_parsing_but_keeps_gates(client):
    party_id = _intent(client, "Add this prospect. name: Hooli")["result"]["party_id"]
    resp = client.post(
        "/api/intents",
        json={
            "text": f"Update customer {party_id}",
            "structured": {"intent_type": "INT-29", "entities": {"entity_type": "party", "entity_id": party_id, "fields": {"name": "Hooli, Inc."}}},
        },
    ).json()
    assert resp["status"] == "executed", resp["rationale"]
    assert client.get(f"/api/records/party/{party_id}").json()["core_fields"]["name"] == "Hooli, Inc."

    smuggled = client.post(
        "/api/intents",
        json={"text": "x", "structured": {"intent_type": "INT-31", "entities": {"entity_type": "party", "entity_id": party_id, "action": "deactivate", "_actor_role": "admin"}}},
    ).json()
    assert smuggled["status"] == "executed"  # admin anyway; the point is the key is stripped, never trusted
    assert "_actor_role" not in smuggled["entities"]

    unknown = client.post("/api/intents", json={"text": "x", "structured": {"intent_type": "INT-99", "entities": {}}}).json()
    assert unknown["status"] == "needs_review"


# ---------- lifecycle transitions + configurable state models ----------


def test_opportunity_close_won_and_intent_owned_transition_refused(client):
    opp_id, quote_id = _quote(client, "TransitionCo", offering="Fiber 702")
    won = _intent(client, f"Move this record. entity_type: opportunity, entity_id: {opp_id}, action: close_won")
    assert won["status"] == "executed", won["rationale"]
    assert client.get(f"/api/opportunities/{opp_id}").json()["stage"] == "won"

    refused = _intent(client, f"Move this record. entity_type: quote, entity_id: {quote_id}, action: cancel")
    assert refused["status"] == "needs_review"
    assert "INT-20" in refused["rationale"]


def test_configured_state_model_adds_custom_substate_and_lock_point(client):
    model = client.get("/api/admin/state-models").json()
    quote_model = next(m for m in model if m["entity_type"] == "quote")
    states = [{k: s[k] for k in ("code", "label", "editable", "is_terminal")} for s in quote_model["states"]]
    states.insert(1, {"code": "legal_review", "label": "Legal Review", "editable": False, "is_terminal": False})
    transitions = quote_model["transitions"] + [
        {"from_state": "draft", "to_state": "legal_review", "action": "send_to_legal", "intent_type": None, "allowed_roles": ""},
        {"from_state": "legal_review", "to_state": "draft", "action": "legal_cleared", "intent_type": None, "allowed_roles": "admin"},
    ]
    resp = client.put("/api/admin/state-models/quote", json={"name": "Quote with legal", "states": states, "transitions": transitions})
    assert resp.status_code == 200, resp.text
    assert resp.json()["configured"] is True

    missing_builtin = client.put("/api/admin/state-models/quote", json={"name": "Broken", "states": states[2:], "transitions": []})
    assert missing_builtin.status_code == 400

    _, quote_id = _quote(client, "LegalCo", offering="Fiber 703")
    moved = _intent(client, f"Move this record. entity_type: quote, entity_id: {quote_id}, action: send_to_legal")
    assert moved["status"] == "executed", moved["rationale"]

    # legal_review is locked: in-place update AND the PONR-gated INT-19 revise are both blocked
    assert _intent(client, f"Update this record. entity_type: quote, entity_id: {quote_id}, discount_justification: x")["status"] == "needs_review"

    agent_id = _make_user(client, "agent-legal@default.local", "Agent Legal")
    as_agent = client.post(
        "/api/intents",
        json={"text": f"Move this record. entity_type: quote, entity_id: {quote_id}, action: legal_cleared"},
        headers={"X-Test-User-Id": str(agent_id)},
    ).json()
    assert as_agent["status"] == "needs_review"  # admin-only transition

    cleared = _intent(client, f"Move this record. entity_type: quote, entity_id: {quote_id}, action: legal_cleared")
    assert cleared["status"] == "executed", cleared["rationale"]
    history = client.get(f"/api/records/quote/{quote_id}").json()["history"]
    assert any(h["summary"] == "legal_review → draft" for h in history)


# ---------- assignment / transfer ----------


def test_assign_transfer_and_assignment_rules(client):
    rep_a = _make_user(client, "rep-a@default.local", "Rep A")
    rep_b = _make_user(client, "rep-b@default.local", "Rep B")
    position = client.post("/api/admin/positions", json={"name": "Inside Sales", "user_ids": [rep_a, rep_b]}).json()
    director = client.post("/api/admin/positions", json={"name": "Sales Director", "user_ids": []}).json()
    rr = client.post(
        "/api/admin/assignment-rules",
        json={"name": "New quotes round robin", "entity_type": "quote", "strategy": "round_robin", "position_id": position["id"]},
    )
    assert rr.status_code == 201, rr.text
    on_approve = client.post(
        "/api/admin/assignment-rules",
        json={"name": "Approved quotes to director", "entity_type": "quote", "on_state": "approved", "position_id": director["id"]},
    )
    assert on_approve.status_code == 201, on_approve.text

    _, q1 = _quote(client, "AssignCo1", offering="Fiber 704")
    _, q2 = _quote(client, "AssignCo2", offering="Fiber 705")
    owners = {client.get(f"/api/records/quote/{q}").json()["owner_user"]["id"] for q in (q1, q2)}
    assert owners == {rep_a, rep_b}

    _intent(client, f"Approve this quote. quote_id: {q1}")
    view = client.get(f"/api/records/quote/{q1}").json()
    assert view["owner_position"]["name"] == "Sales Director"

    transfer = _intent(client, f"Transfer this record. entity_type: quote, entity_id: {q2}, to_user: rep-a@default.local, reason: coverage")
    assert transfer["intent_type"] == "INT-32"
    assert transfer["status"] == "executed", transfer["rationale"]
    my_work = client.get("/api/records/my-work", headers={"X-Test-User-Id": str(rep_a)}).json()
    assert any(i["entity_type"] == "quote" and i["entity_id"] == q2 for i in my_work)

    unknown = _intent(client, f"Assign this record. entity_type: quote, entity_id: {q2}, to_user: nobody@nowhere")
    assert unknown["status"] == "needs_review"


# ---------- flows ----------


def test_before_flow_rejects_intent_and_after_flow_enriches_record(client):
    client.post("/api/admin/custom-fields", json={"entity_type": "quote", "key": "review_tier", "label": "Review tier", "data_type": "text"})
    _create_flow(
        client,
        name="Block big discounts",
        trigger_type="intent",
        trigger_intent_type="INT-05",
        timing="before",
        graph=_graph(
            ("s", "start"),
            ("c", "condition", {"field": "requested_discount_pct", "operator": "gt", "value": "30"}),
            ("r", "reject", {"message": "Discounts above 30% need a deal desk exception"}),
            ("end", "end"),
            edges=[("s", "c"), ("c", "r", "true"), ("c", "end", "false")],
        ),
    )
    _create_flow(
        client,
        name="Tag premium quotes",
        entity_type="quote",
        trigger_type="intent",
        trigger_intent_type="INT-05",
        timing="after",
        graph=_graph(
            ("s", "start"),
            ("c", "condition", {"field": "total_price", "operator": "gte", "value": "1000"}),
            ("set", "set_field", {"field": "review_tier", "value": "premium-{{record.id}}"}),
            ("n", "notify", {"audience": "deal desk", "message": "Premium quote {{record.id}}"}),
            ("end", "end"),
            edges=[("s", "c"), ("c", "set", "true"), ("set", "n"), ("n", "end"), ("c", "end", "false")],
        ),
    )
    _intent(client, "Launch a new bundle. name: Fiber 710, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 1000, discount: 0")
    opp_id = _intent(client, "Qualify this lead. company_name: FlowCo, source: referral, region: North")["result"]["opportunity_id"]

    rejected = _intent(client, f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: Fiber 710, discount: 40, justification: x")
    assert rejected["status"] == "failed"
    assert "deal desk exception" in rejected["rationale"]

    ok = _intent(client, f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: Fiber 710, quantity: 2")
    assert ok["status"] == "executed", ok["rationale"]
    quote_id = ok["result"]["quote_id"]
    assert [f["flow_name"] for f in ok["result"]["flows_run"]] == ["Block big discounts", "Tag premium quotes"]
    assert "notification.sent" in ok["events"]
    fields = client.get(f"/api/records/quote/{quote_id}").json()["custom_fields"]
    assert next(f for f in fields if f["key"] == "review_tier")["value"] == f"premium-{quote_id}"


def test_state_enter_flow_and_custom_intent_flow(client):
    _make_user(client, "kam@default.local", "Key Account Manager")
    _create_flow(
        client,
        name="Won deals go to KAM",
        entity_type="opportunity",
        trigger_type="state_enter",
        trigger_state="won",
        graph=_graph(("s", "start"), ("a", "assign", {"to_user": "kam@default.local"}), ("end", "end"), edges=[("s", "a"), ("a", "end")]),
    )
    custom = _create_flow(
        client,
        name="Escalate opportunity",
        description="Escalate a stuck deal: mark it lost if flagged, otherwise notify the director",
        entity_type="opportunity",
        trigger_type="custom_intent",
        trigger_phrases="escalate this deal, escalate opportunity",
        required_fields="opportunity_id",
        graph=_graph(
            ("s", "start"),
            ("c", "condition", {"field": "entities.give_up", "operator": "eq", "value": "yes"}),
            ("t", "transition", {"to_state": "lost"}),
            ("n", "notify", {"audience": "director", "message": "Escalated {{record.name}}"}),
            ("end", "end"),
            edges=[("s", "c"), ("c", "t", "true"), ("c", "n", "false"), ("t", "end"), ("n", "end")],
        ),
    )
    assert custom["intent_code"] == f"FLOW-{custom['id']}"

    opp_id, _ = _quote(client, "StateFlowCo", offering="Fiber 711")
    won = _intent(client, f"Move this record. entity_type: opportunity, entity_id: {opp_id}, action: close_won")
    assert won["status"] == "executed", won["rationale"]
    assert client.get(f"/api/records/opportunity/{opp_id}").json()["owner_user"]["email"] == "kam@default.local"

    opp2, _ = _quote(client, "EscalateCo", offering="Fiber 712")
    missing = _intent(client, "Escalate this deal please")
    assert missing["intent_type"] == custom["intent_code"] and missing["status"] == "needs_review"

    escalated = _intent(client, f"Escalate this deal. opportunity_id: {opp2}, give_up: yes")
    assert escalated["status"] == "executed", escalated["rationale"]
    assert client.get(f"/api/opportunities/{opp2}").json()["stage"] == "lost"

    assist = client.post("/api/intents/assist", json={"text": "escalate opportunity"}).json()
    assert assist["intent_label"] == "Escalate opportunity" and assist["missing_fields"] == ["opportunity_id"]


def test_flow_validation_simulation_and_ai_generate(client):
    bad = client.post(
        "/api/admin/flows",
        json={"name": "Broken", "trigger_type": "intent", "trigger_intent_type": "INT-05", "graph": _graph(("c", "condition", {"field": "x"}), edges=[])},
    ).json()
    assert any("exactly one Start" in e for e in bad["validation_errors"])
    assert client.post(f"/api/admin/flows/{bad['id']}/activate").status_code == 400

    client.post("/api/admin/positions", json={"name": "Deal Desk"})
    draft = client.post(
        "/api/admin/flows/ai-generate",
        json={"prompt": "If total price is greater than 5000, assign to Deal Desk. Otherwise notify the owner that it was auto-approved", "entity_type": "quote"},
    ).json()
    assert draft["source"] == "heuristic"
    assert draft["validation_errors"] == []
    types = [n["type"] for n in draft["graph"]["nodes"]]
    assert types[:3] == ["start", "condition", "assign"] and "notify" in types and types[-1] == "end"
    assert all("x" in n and "y" in n for n in draft["graph"]["nodes"])

    flow = client.post(
        "/api/admin/flows",
        json={"name": "Generated", "entity_type": "quote", "trigger_type": "intent", "trigger_intent_type": "INT-06", "graph": draft["graph"]},
    ).json()
    _, quote_id = _quote(client, "SimCo", offering="Fiber 713", price=9000)
    sim = client.post(f"/api/admin/flows/{flow['id']}/simulate", json={"entity_id": quote_id}).json()
    assert sim["ok"], sim["error"]
    assert [step["type"] for step in sim["run"]["trace"]] == ["start", "condition", "assign", "end"]
    # simulation is rolled back - the quote is still unassigned by the Deal Desk
    owner = client.get(f"/api/records/quote/{quote_id}").json()["owner_position"]
    assert owner is None or owner["name"] != "Deal Desk"
