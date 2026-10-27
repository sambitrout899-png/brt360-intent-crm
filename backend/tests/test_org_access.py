"""Org structure -> data visibility, and roles -> pages.

Org used throughout:

  Sales (dept)                     Support (dept)
    Sales Head      [department_tree]  Support Agent [own]
      Team Lead     [team]
        Exec A      [own]
      Exec B        [own]
    Sales North (sub-dept)
      Exec C        [own], reports to Sales Head

Each user qualifies one lead (party + lead + opportunity) as themselves.
"""

import pytest

from app.auth import hash_password
from app.models.auth import User, UserRole
from app.tenancy import DEFAULT_TENANT_SLUG


def _tenant_id(client):
    from app.models.tenant import Tenant

    db = client.TestSessionLocal()
    try:
        return db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
    finally:
        db.close()


def _user(client, name, role=UserRole.agent):
    db = client.TestSessionLocal()
    try:
        user = User(tenant_id=_tenant_id(client), email=f"{name.lower().replace(' ', '.')}@org.test", password_hash=hash_password("x" * 8), name=name, role=role)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _post(client, path, body):
    resp = client.post(path, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _as(user_id, **extra):
    return {"X-Test-User-Id": str(user_id), **{k.replace("_", "-"): str(v) for k, v in extra.items()}}


def _lead_ids(client, user_id, **extra):
    resp = client.get("/api/leads", headers=_as(user_id, **extra))
    assert resp.status_code == 200, resp.text
    return {lead["id"] for lead in resp.json()}


@pytest.fixture()
def org(client):
    sales = _post(client, "/api/admin/departments", {"name": "Sales"})
    north = _post(client, "/api/admin/departments", {"name": "Sales North", "parent_department_id": sales})
    support = _post(client, "/api/admin/departments", {"name": "Support"})

    def position(name, dept, scope, parent=None):
        return _post(client, "/api/admin/positions", {"name": name, "department_id": dept, "data_scope": scope, "parent_position_id": parent})

    head_p = position("Sales Head", sales, "department_tree")
    lead_p = position("Team Lead", sales, "team", head_p)
    a_p = position("Exec A", sales, "own", lead_p)
    b_p = position("Exec B", sales, "own", head_p)
    c_p = position("Exec C", north, "own", head_p)
    support_p = position("Support Agent", support, "own")

    users = {name: _user(client, name) for name in ("Head", "Lead", "A", "B", "C", "Supp")}
    for name, pos in (("Head", head_p), ("Lead", lead_p), ("A", a_p), ("B", b_p), ("C", c_p), ("Supp", support_p)):
        resp = client.put(f"/api/admin/users/{users[name]}/access", json={"position_ids": [pos]})
        assert resp.status_code == 200, resp.text

    leads = {}
    for name, user_id in users.items():
        body = client.post(
            "/api/intents", json={"text": f"Qualify this lead. company_name: {name} Corp, source: referral, region: North"}, headers=_as(user_id)
        ).json()
        assert body["status"] == "executed", body["rationale"]
        leads[name] = body["result"]["lead_id"]
    return {"users": users, "leads": leads, "positions": {"head": head_p, "a": a_p, "support": support_p}}


def test_each_position_sees_its_own_slice(client, org):
    u, L = org["users"], org["leads"]
    assert _lead_ids(client, u["A"]) == {L["A"]}  # own
    assert _lead_ids(client, u["B"]) == {L["B"]}
    assert _lead_ids(client, u["Lead"]) == {L["Lead"], L["A"]}  # team: self + reports
    assert _lead_ids(client, u["Head"]) == {L["Head"], L["Lead"], L["A"], L["B"], L["C"]}  # dept + sub-dept
    assert _lead_ids(client, u["Supp"]) == {L["Supp"]}
    assert set(L.values()) <= _lead_ids(client, client.get("/api/access/me").json()["user"]["id"])  # admin: org


def test_scope_can_be_narrowed_but_never_widened(client, org):
    u, L = org["users"], org["leads"]
    assert _lead_ids(client, u["Head"], x_data_scope="department") == {L["Head"], L["Lead"], L["A"], L["B"]}  # no sub-dept
    assert _lead_ids(client, u["Head"], x_data_scope="team") == {L["Head"], L["Lead"], L["A"], L["B"], L["C"]}  # everyone reports to Head
    assert _lead_ids(client, u["Head"], x_data_scope="own") == {L["Head"]}
    assert _lead_ids(client, u["A"], x_data_scope="org") == {L["A"]}  # clamped to the position's max

    me = client.get("/api/access/me", headers=_as(u["A"], x_data_scope="org")).json()
    assert me["scope"] == "own" and me["max_scope"] == "own"
    assert [s["key"] for s in me["available_scopes"]] == ["own"]


def test_secondary_position_assignments_open_from_my_work(client, org):
    from app.models.lifecycle import EntityRecordMeta, Position

    user_id = org["users"]["A"]
    position_id = org["positions"]["support"]
    lead_id = org["leads"]["B"]
    response = client.put(f"/api/admin/users/{user_id}/access", json={
        "position_ids": [org["positions"]["a"], position_id],
        "primary_position_id": org["positions"]["a"],
    })
    assert response.status_code == 200, response.text
    with client.TestSessionLocal() as db:
        meta = db.query(EntityRecordMeta).filter_by(entity_type="lead", entity_id=lead_id).one()
        meta.owner_user_id = None
        meta.owner_position_id = position_id
        meta.owner_department_id = None
        db.commit()

    headers = _as(user_id, x_data_scope="own")
    work = client.get("/api/records/my-work", headers=headers).json()
    assert any(item["entity_type"] == "lead" and item["entity_id"] == lead_id for item in work)
    assert client.get(f"/api/records/lead/{lead_id}", headers=headers).status_code == 200
    assert client.get(f"/api/records/lead/{lead_id}/about", headers=headers).status_code == 200
    assert lead_id in _lead_ids(client, user_id)
    assert client.get(f"/api/records/lead/{lead_id}", headers=_as(org["users"]["C"])).status_code == 404
    assert org["leads"]["Supp"] not in _lead_ids(client, user_id)

    with client.TestSessionLocal() as db:
        db.get(Position, position_id).is_active = False
        db.commit()
    work = client.get("/api/records/my-work", headers=headers).json()
    assert not any(item["entity_type"] == "lead" and item["entity_id"] == lead_id for item in work)
    assert client.get(f"/api/records/lead/{lead_id}", headers=headers).status_code == 404


def test_hidden_records_404_and_cannot_be_acted_on(client, org):
    u, L = org["users"], org["leads"]
    assert client.get(f"/api/leads/{L['A']}", headers=_as(u["B"])).status_code == 404
    assert client.get(f"/api/records/lead/{L['A']}", headers=_as(u["B"])).status_code == 404
    assert client.get(f"/api/records/lead/{L['A']}", headers=_as(u["Lead"])).status_code == 200

    blocked = client.post(
        "/api/intents", json={"text": f"Update this record. entity_type: lead, entity_id: {L['A']}, source: stolen"}, headers=_as(u["B"])
    ).json()
    assert blocked["status"] == "failed"
    assert "outside the data your position" in blocked["rationale"]


def test_order_owned_by_secondary_position_is_accessible(client, org):
    from app.crm_core.account_service import get_or_create_service_account
    from app.models.crm import Lead
    from app.models.lifecycle import EntityRecordMeta
    from app.models.orders import BillingAccount, Order

    user_id = org["users"]["A"]
    position_id = org["positions"]["support"]
    response = client.put(f"/api/admin/users/{user_id}/access", json={
        "position_ids": [org["positions"]["a"], position_id],
        "primary_position_id": org["positions"]["a"],
    })
    assert response.status_code == 200, response.text
    with client.TestSessionLocal() as db:
        lead = db.get(Lead, org["leads"]["B"])
        billing = BillingAccount(tenant_id=lead.tenant_id, party_id=lead.party_id)
        db.add(billing)
        db.flush()
        account = get_or_create_service_account(db, lead.tenant_id, billing.id, "CPMO order")
        order = Order(tenant_id=lead.tenant_id, party_id=lead.party_id, service_account_id=account.id)
        db.add(order)
        db.flush()
        order_id = order.id
        db.add(EntityRecordMeta(tenant_id=lead.tenant_id, entity_type="order", entity_id=order_id,
                                owner_position_id=position_id))
        db.commit()

    work = client.get("/api/records/my-work", headers=_as(user_id)).json()
    assert any(item["entity_type"] == "order" and item["entity_id"] == order_id for item in work)
    for suffix in ("", "/about"):
        assert client.get(f"/api/records/order/{order_id}{suffix}", headers=_as(user_id)).status_code == 200
        assert client.get(f"/api/records/order/{order_id}{suffix}", headers=_as(org["users"]["C"])).status_code == 404


def test_pages_customer_360_and_dashboard_follow_visibility(client, org):
    u = org["users"]
    parties = {p["name"] for p in client.get("/api/parties", headers=_as(u["A"])).json()}
    assert parties == {"A Corp"}
    other = next(p for p in client.get("/api/parties").json() if p["name"] == "B Corp")
    assert client.get(f"/api/parties/{other['id']}/360", headers=_as(u["A"])).status_code == 404

    a_dash = client.get("/api/dashboard/summary", headers=_as(u["A"])).json()
    head_dash = client.get("/api/dashboard/summary", headers=_as(u["Head"])).json()
    assert a_dash["total_parties"] == 1 and head_dash["total_parties"] == 5


def test_multiple_positions_primary_and_switching(client, org):
    u, L, P = org["users"], org["leads"], org["positions"]
    # A also covers Support, but works as Exec A by default (primary).
    resp = client.put(
        f"/api/admin/users/{u['A']}/access", json={"position_ids": [P["a"], P["support"]], "primary_position_id": P["a"]}
    )
    assert resp.status_code == 200 and resp.json()["primary_position_id"] == P["a"]

    me = client.get("/api/access/me", headers=_as(u["A"])).json()
    assert me["active_position_id"] == P["a"] and len(me["positions"]) == 2
    assert _lead_ids(client, u["A"]) == {L["A"]}
    # As Support Agent: own scope - records owned by that position too
    assert client.get("/api/access/me", headers=_as(u["A"], x_position_id=P["support"])).json()["active_position_id"] == P["support"]
    # a position the user does not hold falls back to the primary, never escalates
    assert client.get("/api/access/me", headers=_as(u["A"], x_position_id=P["head"])).json()["active_position_id"] == P["a"]

    # what A creates while working as Support Agent belongs to Support
    body = client.post(
        "/api/intents", json={"text": "Qualify this lead. company_name: Switch Corp, source: web, region: North"},
        headers=_as(u["A"], x_position_id=P["support"]),
    ).json()
    new_lead = body["result"]["lead_id"]
    assert new_lead in _lead_ids(client, u["Supp"])  # the Support Agent seat sees it
    assert new_lead in _lead_ids(client, u["A"], x_position_id=P["support"])
    assert new_lead not in _lead_ids(client, u["B"])


def test_roles_union_of_pages(client, org):
    u = org["users"]
    sales_role = _post(client, "/api/admin/roles", {"name": "Sales Exec", "pages": ["dashboard", "pipeline", "customer"]})
    support_role = _post(client, "/api/admin/roles", {"name": "Support", "pages": ["tickets", "dashboard"]})
    assert client.post("/api/admin/roles", json={"name": "Bad", "pages": ["nope"]}).status_code == 400

    # no roles yet -> built-in defaults (agent: everything except Admin)
    assert "admin" not in client.get("/api/access/me", headers=_as(u["A"])).json()["pages"]

    client.put(f"/api/admin/users/{u['A']}/access", json={"role_ids": [sales_role, support_role], "position_ids": [org["positions"]["a"]]})
    pages = client.get("/api/access/me", headers=_as(u["A"])).json()["pages"]
    assert pages == ["dashboard", "customer", "pipeline", "tickets"]  # union, in menu order

    # an admin given a narrow role still keeps the Admin page
    admin_id = client.get("/api/access/me").json()["user"]["id"]
    client.put(f"/api/admin/users/{admin_id}/access", json={"role_ids": [support_role]})
    assert "admin" in client.get("/api/access/me").json()["pages"]


def test_users_without_a_position_are_not_restricted(client, org):
    loose = _user(client, "Unplaced")
    assert set(org["leads"].values()) <= _lead_ids(client, loose)


def test_org_validation(client, org):
    P = org["positions"]
    sales = next(d for d in client.get("/api/admin/departments").json() if d["name"] == "Sales")
    north = next(d for d in client.get("/api/admin/departments").json() if d["name"] == "Sales North")
    # no cycles in departments or reporting lines
    assert client.put(f"/api/admin/departments/{sales['id']}", json={"name": "Sales", "parent_department_id": north["id"]}).status_code == 400
    assert client.put(
        f"/api/admin/positions/{P['head']}", json={"name": "Sales Head", "department_id": sales["id"], "data_scope": "department_tree", "parent_position_id": P["a"]}
    ).status_code == 400
    assert client.post("/api/admin/positions", json={"name": "X", "data_scope": "galaxy"}).status_code == 400
    # a department with positions cannot be removed
    assert client.delete(f"/api/admin/departments/{sales['id']}").status_code == 400
