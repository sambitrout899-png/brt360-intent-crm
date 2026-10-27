"""Who decides an approval is configured on the Approvals page ("Who decides"):
a step nobody holds goes to anyone / the administrators / a fallback position,
whether requesters may decide their own requests, and whether administrators
may decide any step. The queue only lists what its owner can decide; an
approval nobody can decide (a step called "Yes", or only the requester could)
is listed for administrators with why, and can be sent to a real position."""

from test_position_approvals import _new_order
from test_sales_to_order import _launch, _staff, _tenant_id, _user, run

from app.crm_core import approval_service


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _position(client, name):
    found = next((p["id"] for p in client.get("/api/admin/positions").json() if p["name"] == name), None)
    return found or client.post("/api/admin/positions", json={"name": name, "data_scope": "org"}).json()["id"]


def _legacy_rule(client, step="Yes"):
    """An order-control rule whose only step is a consent word - as an old wizard answer left it."""
    db = client.TestSessionLocal()
    tid = _tenant_id(client)
    position = approval_service.position_by_name(db, tid, step)
    rule = approval_service.add_rule(db, tid, name="Order check", kind="record", entity_type="order", trigger_state="activating",
                                     tiers=[(step, position.id)])
    db.commit()
    rule_id = rule.id
    db.close()
    return rule_id


def _queue(client, user_id=None):
    return {(a["entity_type"], a["entity_id"]) for a in client.get("/api/approvals", headers=_as(user_id) if user_id else {}).json()}


def test_an_approval_nobody_can_decide_is_shown_with_why_and_sent_to_a_real_position(client):
    _launch(client)
    _legacy_rule(client)
    order_id = _new_order(client)
    client.put("/api/admin/access-policy", json={"strict_approvals": True})
    opened = run(client, "INT-25", order_id=order_id, decision="approve")  # the admin submits it: waits "as Yes"
    assert opened["result"]["waiting_for"] == "Yes"

    # the admin asked, so it is not in the admin's queue to approve (that only failed before) ...
    assert ("order", order_id) not in _queue(client)
    failed = run(client, "INT-36", entity_type="order", entity_id=order_id, decision="approve")
    assert failed["status"] == "failed" and "You asked for this approval" in failed["summary"]
    # ... it is listed for administrators as stuck, with why
    waiting = next(w for w in client.get("/api/admin/approvals/waiting").json() if w["entity_id"] == order_id)
    assert waiting["waiting_for"] == "Yes" and "not an approver" in waiting["stuck"]
    rules = client.get("/api/admin/approval-rules").json()
    assert next(r for r in rules if r["name"] == "Order check")["tiers"][0]["not_an_approver"] is True

    cpmo_pos, cpmo = _position(client, "CPMO"), _user(client, "cpmo@policy.test")
    _staff(client, "CPMO", cpmo)
    routed = client.post(f"/api/admin/approvals/{waiting['id']}/route", json={"position_id": cpmo_pos}).json()
    assert routed["waiting_for"] == "CPMO" and routed["stuck"] is None and "was Yes" in routed["change"]
    assert ("order", order_id) in _queue(client, cpmo)
    approved = run(client, "INT-36", cpmo, entity_type="order", entity_id=order_id, decision="approve")
    assert approved["status"] == "executed", approved["rationale"]
    assert client.get(f"/api/records/order/{order_id}").json()["built_in_status"] == "activating"


def test_a_step_nobody_holds_goes_to_the_fallback_position_not_the_administrators(client):
    _launch(client)
    order_id = _new_order(client)
    head = _position(client, "Order Desk")  # nobody holds it
    rule = client.post("/api/admin/approval-rules", json={"name": "Order desk check", "kind": "record", "entity_type": "order",
                                                          "trigger_state": "activating", "tiers": [{"position_id": head}]}).json()
    assert rule["tiers"][0]["unstaffed_note"] == "nobody holds it - anyone allowed to approve decides it"
    duty, manager = _position(client, "Duty Manager"), _user(client, "duty@policy.test")
    _staff(client, "Duty Manager", manager)
    saved = client.put("/api/admin/approval-policy", json={"unstaffed": "position", "fallback_position_id": duty, "self_approval": False}).json()
    assert saved["configured"] and saved["unstaffed"] == "position" and saved["fallback_position_id"] == duty
    assert client.get("/api/admin/access-policy").json()["strict_approvals"] is True  # the same thing in one word
    assert client.get("/api/admin/approval-rules").json()[-1]["tiers"][0]["unstaffed_note"] == "nobody holds it - goes to Duty Manager"

    assert run(client, "INT-25", order_id=order_id, decision="approve")["result"]["waiting_for"] == "Order Desk"
    assert ("order", order_id) not in _queue(client)  # not the administrators' any more
    assert ("order", order_id) in _queue(client, manager)
    clerk = _user(client, "clerk@policy.test")
    refused = run(client, "INT-36", clerk, entity_type="order", entity_id=order_id, decision="approve")
    assert refused["status"] == "failed" and "it goes to Duty Manager" in refused["rationale"]
    assert run(client, "INT-36", manager, entity_type="order", entity_id=order_id, decision="approve")["status"] == "executed"

    bad = client.put("/api/admin/approval-policy", json={"unstaffed": "position", "fallback_position_id": None})
    assert bad.status_code == 400 and "fallback position" in bad.json()["detail"]
    # the Roles & Access switch still works, and the policy follows it
    client.put("/api/admin/access-policy", json={"strict_approvals": False})
    after = client.get("/api/admin/approval-policy").json()
    assert (after["unstaffed"], after["self_approval"]) == ("anyone", True)


def test_editing_a_rule_moves_the_approvals_under_way_and_presets_use_the_chosen_positions(client):
    _launch(client)
    rule_id = _legacy_rule(client)
    order_id = _new_order(client)
    client.put("/api/admin/access-policy", json={"strict_approvals": True})
    run(client, "INT-25", order_id=order_id, decision="approve")
    cpmo_pos, cpmo = _position(client, "CPMO"), _user(client, "cpmo2@policy.test")
    _staff(client, "CPMO", cpmo)

    yes_pos = next(p["id"] for p in client.get("/api/admin/positions").json() if p["name"] == "Yes")
    body = {"name": "Order check", "kind": "record", "entity_type": "order", "trigger_state": "activating"}
    refused = client.put(f"/api/admin/approval-rules/{rule_id}", json={**body, "tiers": [{"position_id": yes_pos}]})
    assert refused.status_code == 400 and "is not an approver" in refused.json()["detail"]
    edited = client.put(f"/api/admin/approval-rules/{rule_id}", json={**body, "tiers": [{"position_id": cpmo_pos}]}).json()
    assert edited["rerouted"] == 1 and edited["tiers"][0]["position_name"] == "CPMO"
    assert ("order", order_id) in _queue(client, cpmo)

    # a ready-made rule takes the positions picked for it - no empty "Sales Director" appears
    sales_head = _position(client, "Head of Sales")
    lead_rule = client.post("/api/admin/approval-rules/presets/lead_qualification", json={"position_ids": [sales_head]}).json()
    assert lead_rule["tiers"][0]["position_name"] == "Head of Sales"
    assert "Sales Director" not in {p["name"] for p in client.get("/api/admin/positions").json()}


def test_segments_and_admin_override_are_settings(client):
    saved = client.put("/api/admin/approval-policy", json={"segments": ["Enterprise", "SME", "Enterprise"], "admins_decide_any": False}).json()
    assert saved["segments"] == ["Enterprise", "SME"] and saved["admins_decide_any"] is False
    assert client.get("/api/admin/approval-rules/options").json()["segments"] == ["Enterprise", "SME"]
    assert client.put("/api/admin/approval-policy", json={"unstaffed": "everyone"}).status_code == 400
