"""Who may do what outside the intent pipeline: screen APIs need one of the
pages that show their data, direct changes need the intent that makes them,
customer data follows the position's data scope, and a user never ends up
with every page and intent because their roles were switched off or deleted."""

from app.auth import hash_password
from app.models.auth import User, UserRole
from app.models.org import AppRole
from app.models.tenant import Tenant
from app.tenancy import DEFAULT_TENANT_SLUG


def _user(client, name, role=UserRole.agent):
    db = client.TestSessionLocal()
    try:
        tenant_id = db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
        user = User(tenant_id=tenant_id, email=f"{name}@guards.test", password_hash=hash_password("x" * 8), name=name, role=role)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _role(client, name, pages):
    resp = client.post("/api/admin/roles", json={"name": name, "pages": pages})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _give(client, user_id, role_ids, position_ids=()):
    resp = client.put(f"/api/admin/users/{user_id}/access", json={"role_ids": list(role_ids), "position_ids": list(position_ids)})
    assert resp.status_code == 200, resp.text


def _codes(client, user_id):
    return {i["code"] for i in client.get("/api/intents/catalog", headers=_as(user_id)).json()["intents"]}


def test_screen_apis_need_a_page_that_shows_them(client):
    sales = _role(client, "Sales", ["pipeline", "intent"])
    seller = _user(client, "seller")
    _give(client, seller, [sales])
    assert client.get("/api/comms/overview", headers=_as(seller)).status_code == 403
    assert "Orders & Billing" in client.get("/api/comms/overview", headers=_as(seller)).json()["detail"]
    assert client.get("/api/field-service/overview", headers=_as(seller)).status_code == 403
    assert client.get("/api/comms/tickets", headers=_as(seller)).status_code == 403
    assert client.get("/api/order-management/catalog", headers=_as(seller)).status_code == 200  # quotes on the pipeline
    # no roles yet -> the defaults, as before
    newcomer = _user(client, "newcomer")
    assert client.get("/api/comms/overview", headers=_as(newcomer)).status_code == 200
    assert client.get("/api/field-service/overview", headers=_as(newcomer)).status_code == 200


def test_direct_changes_need_the_intent(client):
    billing = _role(client, "Billing", ["orders", "intent"])
    clerks = _role(client, "Clerks", ["orders", "intent"])
    client.put("/api/admin/intents/INT-102", json={"role_ids": [billing]})
    clerk, biller = _user(client, "clerk"), _user(client, "biller")
    _give(client, clerk, [clerks])
    _give(client, biller, [billing])
    denied = client.put("/api/comms/provider-work-orders/999", json={"status": "done"}, headers=_as(clerk))
    assert denied.status_code == 403 and "not available to your roles" in denied.json()["detail"]
    assert client.put("/api/comms/provider-work-orders/999", json={"status": "done"}, headers=_as(biller)).status_code == 400  # allowed - just no such work order
    assert client.get("/api/comms/provider-work-orders", headers=_as(clerk)).status_code == 200  # reading stays open


def test_customer_data_follows_the_position(client):
    a_pos = client.post("/api/admin/positions", json={"name": "Rep A", "data_scope": "own"}).json()["id"]
    b_pos = client.post("/api/admin/positions", json={"name": "Rep B", "data_scope": "own"}).json()["id"]
    a, b = _user(client, "rep-a"), _user(client, "rep-b")
    _give(client, a, [], [a_pos])
    _give(client, b, [], [b_pos])
    parties = {}
    for name, uid in (("A", a), ("B", b)):
        body = client.post("/api/intents", json={"text": f"Qualify this lead. company_name: Guard {name} Co, source: referral, region: North"},
                           headers=_as(uid)).json()
        assert body["status"] == "executed", body["rationale"]
        lead = next(x for x in client.get("/api/leads", headers=_as(uid)).json() if x["id"] == body["result"]["lead_id"])
        parties[name] = lead["party_id"]
    assert client.get(f"/api/comms/accounts/{parties['A']}", headers=_as(a)).status_code == 200
    assert client.get(f"/api/comms/accounts/{parties['B']}", headers=_as(a)).status_code == 404
    assert client.get(f"/api/comms/accounts/{parties['B']}").status_code == 200  # admin: whole org
    assert client.get(f"/api/comms/usage?party_id={parties['B']}", headers=_as(a)).status_code == 404


def test_switched_off_roles_do_not_open_everything(client):
    billing = _role(client, "Billing", ["orders", "intent"])
    sales = _role(client, "Sales", ["pipeline", "intent"])
    client.put("/api/admin/intents/INT-84", json={"role_ids": [billing]})
    seller = _user(client, "seller")
    _give(client, seller, [sales])
    assert "INT-84" not in _codes(client, seller)
    db = client.TestSessionLocal()
    db.get(AppRole, sales).is_active = False
    db.commit()
    db.close()
    assert "INT-84" not in _codes(client, seller)  # was: no active roles -> everything
    assert client.get("/api/access/me", headers=_as(seller)).json()["pages"] == []
    ran = client.post("/api/intents", json={"text": "INT-84", "structured": {"intent_type": "INT-84", "entities": {}}}, headers=_as(seller)).json()
    assert ran["status"] == "failed" and "not available to your roles" in ran["rationale"]


def test_a_users_only_role_cannot_be_deleted(client):
    sales = _role(client, "Sales", ["pipeline", "intent"])
    spare = _role(client, "Spare", ["dashboard"])
    seller = _user(client, "seller")
    _give(client, seller, [sales])
    refused = client.delete(f"/api/admin/roles/{sales}")
    assert refused.status_code == 400 and "only role of seller" in refused.json()["detail"]
    _give(client, seller, [sales, spare])
    assert client.delete(f"/api/admin/roles/{sales}").status_code == 204
    assert client.delete(f"/api/admin/roles/{_role(client, 'Unused', [])}").status_code == 204
