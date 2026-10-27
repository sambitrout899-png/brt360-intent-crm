"""The position an approval names decides it: its holders get the Approve /
Reject buttons on the record (even when their roles don't include the
approving intent), everyone else sees who it waits for and can't decide it,
and approving moves the record on to its next stage. A consent word ("yes")
is never taken for an approver's position."""

from test_sales_to_order import _approved_quote, _launch, _staff, _user, run

from app.models.auth import UserRole


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _role(client, name):
    return client.post("/api/admin/roles", json={"name": name, "pages": ["intent", "orders", "pipeline"]}).json()["id"]


def _new_order(client):
    quote_id = _approved_quote(client, "Position Co")
    run(client, "INT-39", quote_id=quote_id, po_number="PO-1", po_amount=1000)
    matched = run(client, "INT-40", quote_id=quote_id)
    assert matched["status"] == "executed", matched["rationale"]
    return matched["result"]["order_id"]


def test_only_the_approving_position_gets_the_buttons_and_approval_moves_the_order_on(client):
    _launch(client)
    order_id = _new_order(client)
    assert client.post("/api/admin/approval-rules/presets/cpmo_order").status_code == 201

    planners, approvers = _role(client, "Planners"), _role(client, "Approvers")
    for code in ("INT-25", "INT-36"):  # neither user's roles may run the approving intents
        assert client.put(f"/api/admin/intents/{code}", json={"role_ids": [approvers]}).status_code == 200
    cpmo, other = _user(client, "cpmo@pos.test", UserRole.agent), _user(client, "pm@pos.test", UserRole.agent)
    for user in (cpmo, other):
        client.put(f"/api/admin/users/{user}/access", json={"role_ids": [planners], "position_ids": []})
    _staff(client, "CPMO", cpmo)

    mine = client.get(f"/api/records/order/{order_id}", headers=_as(cpmo)).json()
    assert mine["approval"]["waiting_for"] == "CPMO" and mine["approval"]["can_decide"] is True
    assert {a["entities"].get("decision") for a in mine["actions"] if a["entities"].get("decision")} == {"approve", "reject"}

    theirs = client.get(f"/api/records/order/{order_id}", headers=_as(other)).json()
    assert theirs["approval"]["can_decide"] is False and "only people in that position" in theirs["approval"]["why"]
    assert not any(a["entities"].get("decision") for a in theirs["actions"])
    refused = run(client, "INT-36", other, entity_type="order", entity_id=order_id, decision="approve")
    assert refused["status"] == "failed"

    form = client.post("/api/intents/prepare", json={"intent_type": "INT-36",
                       "entities": {"entity_type": "order", "entity_id": order_id, "decision": "approve"}}, headers=_as(cpmo))
    assert form.status_code == 200
    approved = run(client, "INT-36", cpmo, entity_type="order", entity_id=order_id, decision="approve")
    assert approved["status"] == "executed", approved["rationale"]
    after = client.get(f"/api/records/order/{order_id}", headers=_as(cpmo)).json()
    assert after["built_in_status"] == "activating" and after["approval"] is None  # on to the next stage


def test_a_consent_word_is_not_an_approver():
    from app.orchestration import process_builder

    assert process_builder._NOT_A_NAME.match("Yes") and process_builder._NOT_A_NAME.match(" ok. ")
    assert not process_builder._NOT_A_NAME.match("CPMO") and not process_builder._NOT_A_NAME.match("Yes Bank Head")


def test_a_rule_step_needs_a_real_position(client):
    body = {"name": "Odd", "kind": "record", "entity_type": "order", "trigger_state": "suspended", "tiers": [{"approver_tier": "Yes"}]}
    assert client.post("/api/admin/approval-rules", json=body).status_code == 400
