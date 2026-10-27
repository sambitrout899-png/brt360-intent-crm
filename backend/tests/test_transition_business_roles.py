"""State-model transitions can be limited to the tenant's own roles (e.g.
"CPMO"), picked from a dropdown, alongside the admin/agent/viewer levels."""

from test_phase7_lifecycle_and_flows import _intent, _make_user, _quote, _tenant_id

from app.crm_core.access_service import PAGE_KEYS
from app.models.org import AppRole, UserAppRole


def _role(client, name, user_ids=()):
    db = client.TestSessionLocal()
    try:
        role = AppRole(tenant_id=_tenant_id(client), name=name, pages=",".join(PAGE_KEYS))
        db.add(role)
        db.flush()
        for uid in user_ids:
            db.add(UserAppRole(user_id=uid, role_id=role.id))
        db.commit()
        return role.id
    finally:
        db.close()


def _move(client, quote_id, action, user_id):
    return client.post(
        "/api/intents",
        json={"text": f"Move this record. entity_type: quote, entity_id: {quote_id}, action: {action}"},
        headers={"X-Test-User-Id": str(user_id)},
    ).json()


def test_every_state_model_saves_back_unchanged(client):
    """What the Lifecycle page loads must save as-is - every built-in status
    (e.g. an order's "completed") is part of the model it shows."""
    for model in client.get("/api/admin/state-models").json():
        body = {"name": model["name"], "states": [{k: s[k] for k in ("code", "label", "editable", "is_terminal")} for s in model["states"]],
                "transitions": model["transitions"]}
        assert set(model["built_in_states"]) <= {s["code"] for s in model["states"]}, model["entity_type"]
        resp = client.put(f"/api/admin/state-models/{model['entity_type']}", json=body)
        assert resp.status_code == 200, (model["entity_type"], resp.text)


def test_transition_limited_to_a_tenant_role(client):
    cpmo_user = _make_user(client, "cpmo@default.local", "Cpmo User")
    other_agent = _make_user(client, "plain-agent@default.local", "Plain Agent")
    _role(client, "CPMO", [cpmo_user])

    quote_model = next(m for m in client.get("/api/admin/state-models").json() if m["entity_type"] == "quote")
    states = [{k: s[k] for k in ("code", "label", "editable", "is_terminal")} for s in quote_model["states"]]
    states.insert(1, {"code": "pmo_review", "label": "PMO Review", "editable": False, "is_terminal": False})
    transitions = quote_model["transitions"] + [
        {"from_state": "draft", "to_state": "pmo_review", "action": "send_to_pmo", "intent_type": None, "allowed_roles": ""},
        # typed in any case - saved as the role's own name
        {"from_state": "pmo_review", "to_state": "draft", "action": "pmo_cleared", "intent_type": None, "allowed_roles": "cpmo"},
    ]

    bad = [*transitions[:-1], {**transitions[-1], "allowed_roles": "CPMO,Nobody"}]
    refused = client.put("/api/admin/state-models/quote", json={"name": "Quote with PMO", "states": states, "transitions": bad})
    assert refused.status_code == 400 and "Nobody" in refused.text

    saved = client.put("/api/admin/state-models/quote", json={"name": "Quote with PMO", "states": states, "transitions": transitions})
    assert saved.status_code == 200, saved.text
    assert next(t for t in saved.json()["transitions"] if t["action"] == "pmo_cleared")["allowed_roles"] == "CPMO"

    _, quote_id = _quote(client, "PmoCo", offering="Fiber 790")
    assert _intent(client, f"Move this record. entity_type: quote, entity_id: {quote_id}, action: send_to_pmo")["status"] == "executed"

    # the action is offered and runs only for a holder of the role (admins keep access)
    offered = lambda uid: [a["action"] for a in client.get(f"/api/records/quote/{quote_id}", headers={"X-Test-User-Id": str(uid)}).json()["available_actions"]]
    assert "pmo_cleared" not in offered(other_agent)
    assert "pmo_cleared" in offered(cpmo_user)
    assert _move(client, quote_id, "pmo_cleared", other_agent)["status"] == "needs_review"
    done = _move(client, quote_id, "pmo_cleared", cpmo_user)
    assert done["status"] == "executed", done["rationale"]
