"""Custom record types: an admin defines a type (Admin -> Record Types) and its
fields (the ordinary custom-field endpoints, entity_type "x_<key>"); users
then create, find, change and delete its records on the Records page -
validated like any custom field, numbered per type, optionally hung off a
customer, visible by data scope, tenant-isolated and audited."""

from app.auth import hash_password
from app.models.auth import User, UserRole
from app.models.crm import Party
from app.models.tenant import Tenant
from app.seed import get_or_create_tenant
from app.tenancy import DEFAULT_TENANT_SLUG


def _tenant_id(client) -> int:
    db = client.TestSessionLocal()
    try:
        return db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
    finally:
        db.close()


def _user(client, name, role=UserRole.agent, tenant_id=None):
    db = client.TestSessionLocal()
    try:
        user = User(tenant_id=tenant_id or db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id,
                    email=f"{name}@records.test", password_hash=hash_password("x" * 8), name=name, role=role)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _party(client, name="Acme Towers"):
    db = client.TestSessionLocal()
    try:
        party = Party(tenant_id=_tenant_id(client), name=name, party_type="organization")
        db.add(party)
        db.commit()
        return party.id
    finally:
        db.close()


def _field(client, key, label, data_type="text", **extra):
    body = {"entity_type": "x_site_survey", "key": key, "label": label, "data_type": data_type,
            "allowed_values": None, "required": False, "default_value": None, **extra}
    resp = client.post("/api/admin/custom-fields", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _site_survey(client, **extra):
    resp = client.post("/api/admin/record-types", json={"label": "Site Survey", "parent_entity_type": "party", **extra})
    assert resp.status_code == 201, resp.text
    _field(client, "survey_date", "Survey date", "date", required=True)
    _field(client, "outcome", "Outcome", "picklist", allowed_values="Pending,Feasible,Not feasible", default_value="Pending")
    _field(client, "floors", "Floors", "number")
    return resp.json()


def test_admin_defines_a_type_and_its_fields(client):
    rt = _site_survey(client)
    assert rt["key"] == "site_survey" and rt["entity_type"] == "x_site_survey"
    assert rt["plural_label"] == "Site Surveys" and rt["number_prefix"] == "SS" and rt["parent_label"] == "Customer"

    listed = client.get("/api/admin/record-types").json()
    (only,) = listed["types"]
    assert [f["key"] for f in only["fields"]] == ["survey_date", "outcome", "floors"]
    assert {"value": "party", "label": "Customer"} in listed["parent_options"]

    # the key is unique; a field can't take a name every record already has; fields need an existing type
    assert client.post("/api/admin/record-types", json={"label": "Site survey"}).status_code == 400
    assert client.post("/api/admin/custom-fields", json={"entity_type": "x_site_survey", "key": "name", "label": "Name"}).status_code == 400
    assert client.post("/api/admin/custom-fields", json={"entity_type": "x_nothing", "key": "a", "label": "A"}).status_code == 400
    assert client.post("/api/admin/record-types", json={"label": "Bad", "number_prefix": "no good"}).status_code == 400
    assert client.post("/api/admin/record-types", json={"label": "Bad", "parent_entity_type": "spaceship"}).status_code == 400


def test_records_are_created_validated_numbered_and_changed(client):
    _site_survey(client)
    party_id = _party(client)

    first = client.post("/api/custom-records/site_survey",
                        json={"name": "Rooftop check", "parent_id": party_id, "fields": {"survey_date": "2026-11-02", "floors": "12"}})
    assert first.status_code == 201, first.text
    rec = first.json()
    assert rec["number"] == "SS-0001"
    assert rec["values"] == {"survey_date": "2026-11-02", "outcome": "Pending", "floors": 12}  # typed, default filled in
    assert rec["parent"] == {"entity_type": "party", "id": party_id, "title": "Acme Towers", "custom_key": None}

    second = client.post("/api/custom-records/site_survey", json={"name": "Basement", "fields": {"survey_date": "2026-11-03"}})
    assert second.json()["number"] == "SS-0002"

    # what the fields allow is enforced, and nothing of a refused record is kept
    assert "Survey date" in client.post("/api/custom-records/site_survey", json={"name": "No date"}).json()["detail"]
    assert client.post("/api/custom-records/site_survey", json={"name": "x", "fields": {"survey_date": "2026-11-02", "outcome": "Maybe"}}).status_code == 400
    assert client.post("/api/custom-records/site_survey", json={"name": "x", "fields": {"survey_date": "2026-11-02", "colour": "red"}}).status_code == 400
    assert client.post("/api/custom-records/site_survey", json={"name": "x", "parent_id": 99999, "fields": {"survey_date": "2026-11-02"}}).status_code == 400
    assert client.get("/api/custom-records/site_survey").json()["total"] == 2
    assert client.post("/api/custom-records/site_survey", json={"name": "Third", "fields": {"survey_date": "2026-11-04"}}).json()["number"] == "SS-0003"

    found = client.get("/api/custom-records/site_survey", params={"q": "roof"}).json()
    assert [i["number"] for i in found["items"]] == ["SS-0001"]
    assert [i["number"] for i in client.get("/api/custom-records/site_survey", params={"parent_id": party_id}).json()["items"]] == ["SS-0001"]

    changed = client.patch(f"/api/custom-records/site_survey/{rec['id']}", json={"name": "Rooftop survey", "fields": {"outcome": "feasible"}})
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Rooftop survey" and changed.json()["values"]["outcome"] == "Feasible"
    assert client.patch(f"/api/custom-records/site_survey/{rec['id']}", json={"fields": {"survey_date": ""}}).status_code == 400  # required
    unlinked = client.patch(f"/api/custom-records/site_survey/{rec['id']}", json={"parent_id": None})
    assert unlinked.json()["parent"] is None

    detail = client.get(f"/api/custom-records/site_survey/{rec['id']}").json()
    assert detail["record"]["brt_id"] and detail["record"]["created_by"] == "Test Admin"

    assert client.delete(f"/api/custom-records/site_survey/{rec['id']}").status_code == 204
    assert client.get(f"/api/custom-records/site_survey/{rec['id']}").status_code == 404


def test_parent_rules_and_deleting_a_type(client):
    _site_survey(client, parent_required=True)
    assert "needs a Customer" in client.post("/api/custom-records/site_survey", json={"name": "x", "fields": {"survey_date": "2026-11-02"}}).json()["detail"]

    # a type can hang off another custom type, but never in a loop
    assert client.post("/api/admin/record-types", json={"label": "Survey Photo", "parent_entity_type": "x_site_survey"}).status_code == 201
    assert "loop" in client.patch("/api/admin/record-types/site_survey", json={"parent_entity_type": "x_survey_photo"}).json()["detail"]

    party_id = _party(client)
    survey = client.post("/api/custom-records/site_survey", json={"name": "Roof", "parent_id": party_id, "fields": {"survey_date": "2026-11-02"}}).json()
    photo = client.post("/api/custom-records/survey_photo", json={"name": "North face", "parent_id": survey["id"]}).json()
    assert photo["parent"]["title"] == f"{survey['number']} · Roof" and photo["parent"]["custom_key"] == "site_survey"
    assert client.get(f"/api/custom-records/site_survey/{survey['id']}").json()["record"]["children"] == [
        {"key": "survey_photo", "label": "Survey Photos", "count": 1}]

    # the parent picker offers the records it can hang off
    assert [p["title"] for p in client.get("/api/custom-records/parents/survey_photo").json()] == [f"{survey['number']} · Roof"]

    # nothing in use is deleted: records first, then the types that hang off it
    assert "hang off" in client.delete(f"/api/custom-records/site_survey/{survey['id']}").json()["detail"]
    assert client.delete("/api/admin/record-types/survey_photo").status_code == 400
    assert client.delete(f"/api/custom-records/survey_photo/{photo['id']}").status_code == 204
    assert client.delete("/api/admin/record-types/site_survey").status_code == 400  # Survey Photo hangs off it
    assert client.delete("/api/admin/record-types/survey_photo").status_code == 204
    assert client.delete(f"/api/custom-records/site_survey/{survey['id']}").status_code == 204
    assert "already exist" not in client.patch("/api/admin/record-types/site_survey", json={"parent_entity_type": None}).text
    assert client.delete("/api/admin/record-types/site_survey").status_code == 204
    assert client.get("/api/admin/custom-fields", params={"entity_type": "x_site_survey"}).status_code == 400  # its fields went with it


def test_switched_off_types_and_who_may_write(client):
    _site_survey(client)
    viewer = _user(client, "viewer", UserRole.viewer)
    assert client.get("/api/custom-records/site_survey", headers=_as(viewer)).status_code == 200
    assert client.post("/api/custom-records/site_survey", json={"name": "x"}, headers=_as(viewer)).status_code == 403
    agent = _user(client, "agent")
    assert client.post("/api/custom-records/site_survey", json={"name": "x", "fields": {"survey_date": "2026-11-02"}}, headers=_as(agent)).status_code == 201
    assert client.post("/api/admin/record-types", json={"label": "Nope"}, headers=_as(agent)).status_code == 403

    client.patch("/api/admin/record-types/site_survey", json={"is_active": False})
    assert client.get("/api/custom-records/types").json() == []
    assert client.get("/api/custom-records/site_survey").status_code == 404


def test_records_follow_the_data_scope_and_the_tenant(client):
    _site_survey(client)
    a_pos = client.post("/api/admin/positions", json={"name": "Surveyor A", "data_scope": "own"}).json()["id"]
    b_pos = client.post("/api/admin/positions", json={"name": "Surveyor B", "data_scope": "own"}).json()["id"]
    a, b = _user(client, "surveyor-a"), _user(client, "surveyor-b")
    for uid, pos in ((a, a_pos), (b, b_pos)):
        assert client.put(f"/api/admin/users/{uid}/access", json={"role_ids": [], "position_ids": [pos]}).status_code == 200
    mine = client.post("/api/custom-records/site_survey", json={"name": "A's survey", "fields": {"survey_date": "2026-11-02"}}, headers=_as(a)).json()
    assert client.get("/api/custom-records/site_survey", headers=_as(a)).json()["total"] == 1
    assert client.get("/api/custom-records/site_survey", headers=_as(b)).json()["total"] == 0
    assert client.get(f"/api/custom-records/site_survey/{mine['id']}", headers=_as(b)).status_code == 404
    assert client.patch(f"/api/custom-records/site_survey/{mine['id']}", json={"name": "taken"}, headers=_as(b)).status_code == 404

    db = client.TestSessionLocal()
    try:
        other = get_or_create_tenant(db, "other-co", "Other Co")
        db.commit()
        other_id = other.id
    finally:
        db.close()
    outsider = _user(client, "outsider", UserRole.admin, tenant_id=other_id)
    assert client.get("/api/custom-records/site_survey", headers=_as(outsider)).status_code == 404
    assert client.get("/api/admin/record-types", headers=_as(outsider)).json()["types"] == []


def test_changes_are_in_the_audit_trail(client):
    _site_survey(client)
    rec = client.post("/api/custom-records/site_survey", json={"name": "Roof", "fields": {"survey_date": "2026-11-02"}}).json()
    client.patch(f"/api/custom-records/site_survey/{rec['id']}", json={"fields": {"floors": 3}})
    client.delete(f"/api/custom-records/site_survey/{rec['id']}")
    feed = client.get("/api/audit/feed", params={"record": f"x_site_survey:{rec['id']}"}).json()
    assert [i["code"] for i in feed["items"]] == ["REC-DELETE", "REC-UPDATE", "REC-CREATE"]
    assert feed["items"][-1]["label"] == "Create a record"
    assert feed["items"][-1]["outcome"] == "Created Site Survey SS-0001"
    assert feed["items"][1]["outcome"] == "Updated Site Survey SS-0001"
