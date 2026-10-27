"""Assignment works the same for users, positions and departments - and a
deal moves as a whole; the Page Designer is a full per-tenant RAD: buttons
that run an intent or a Flow Studio flow, text, page events that start a
flow, copies, switching on/off and giving a page to roles - all of it also
said in plain English through "Customize a page" (INT-103)."""

from test_sales_to_order import _feasible_opportunity, _launch, _user, run

from app.models.auth import UserRole
from app.models.tenant import Tenant, TenantVertical
from app.seed import get_or_create_user, seed_reference_data


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _post(client, url, body):
    resp = client.post(url, json=body)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _owner(client, entity_type, entity_id, user=None):
    return client.get(f"/api/records/{entity_type}/{entity_id}", headers=_as(user) if user else {}).json()


def test_assign_to_a_department_moves_the_deal_and_reaches_its_members(client):
    _launch(client)
    ent = _post(client, "/api/admin/departments", {"name": "Enterprise Sales"})["id"]
    other_dept = _post(client, "/api/admin/departments", {"name": "Retail"})["id"]
    ent_pos = _post(client, "/api/admin/positions", {"name": "Enterprise Rep", "department_id": ent, "data_scope": "own"})["id"]
    retail_pos = _post(client, "/api/admin/positions", {"name": "Retail Rep", "department_id": other_dept, "data_scope": "own"})["id"]
    rep, outsider = _user(client, "ent@assign.test"), _user(client, "retail@assign.test")
    assert client.put(f"/api/admin/users/{rep}/access", json={"role_ids": [], "position_ids": [ent_pos]}).status_code == 200
    assert client.put(f"/api/admin/users/{outsider}/access", json={"role_ids": [], "position_ids": [retail_pos]}).status_code == 200

    opp = _feasible_opportunity(client, "Dept Co")
    quote = run(client, "INT-05", opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=5)["result"]["quote_id"]
    done = run(client, "INT-32", entity_type="opportunity", entity_id=opp, to_department="Enterprise Sales", reason="big account")
    assert done["status"] == "executed", done["rationale"]
    assert {"entity_type": "quote", "entity_id": quote} in done["result"]["related"]  # the open quote moved with it
    for entity_type, entity_id in (("opportunity", opp), ("quote", quote)):
        record = _owner(client, entity_type, entity_id)
        assert record["owner_department"]["name"] == "Enterprise Sales" and record["owner_user"] is None
    assert any("Enterprise Sales department" in h["summary"] for h in _owner(client, "opportunity", opp)["history"])

    mine = {(i["entity_type"], i["entity_id"]): i for i in client.get("/api/records/my-work", headers=_as(rep)).json()}
    assert mine[("opportunity", opp)]["via_position"] == "Enterprise Sales" and ("quote", quote) in mine
    assert client.get(f"/api/records/opportunity/{opp}", headers=_as(rep)).status_code == 200  # a member sees it
    assert client.get(f"/api/records/opportunity/{opp}", headers=_as(outsider)).status_code == 404

    # "the X department" said as a position is the department
    again = client.post("/api/intents", json={"text": f"Assign this record. entity_type: opportunity, entity_id: {opp}, "
                                                      "to_position: Retail department, include_related: no"}).json()
    assert again["status"] == "executed", again["rationale"]
    assert _owner(client, "opportunity", opp)["owner_department"]["name"] == "Retail"
    assert _owner(client, "quote", quote)["owner_department"]["name"] == "Enterprise Sales"  # include_related: no

    # a user who owns the opportunity sees its quotes too
    seller = _user(client, "seller@assign.test")
    assert client.put(f"/api/admin/users/{seller}/access", json={"role_ids": [], "position_ids": [retail_pos]}).status_code == 200
    run(client, "INT-32", entity_type="opportunity", entity_id=opp, to_user="seller@assign.test", include_related=False)
    assert client.get(f"/api/records/quote/{quote}", headers=_as(seller)).status_code == 200

    # assignment rules can target a department
    rule = client.post("/api/admin/assignment-rules", json={"name": "Leads to Enterprise", "entity_type": "lead", "strategy": "department",
                                                             "department_id": ent})
    assert rule.status_code == 201 and rule.json()["department_id"] == ent
    assert client.post("/api/admin/assignment-rules", json={"name": "x", "entity_type": "lead", "strategy": "department"}).status_code == 400
    lead = run(client, "INT-04", company_name="Rule Co", source="referral", region="North")["result"]["lead_id"]
    assert _owner(client, "lead", lead)["owner_department"]["name"] == "Enterprise Sales"


def _welcome_flow(client):
    flow = _post(client, "/api/admin/flows", {
        "name": "Welcome kit", "entity_type": "party", "trigger_type": "custom_intent", "trigger_phrases": "send the welcome kit",
        "graph": {"nodes": [{"id": "s", "type": "start", "label": "Start", "config": {}},
                            {"id": "n", "type": "notify", "label": "Tell", "config": {"audience": "owner", "message": "Welcome kit for {{record.name}}"}},
                            {"id": "e", "type": "end", "label": "End", "config": {}}],
                  "edges": [{"id": "1", "source": "s", "target": "n", "label": ""}, {"id": "2", "source": "n", "target": "e", "label": ""}]}})
    assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 200
    return f"FLOW-{flow['id']}"


def test_page_designer_buttons_events_copies_roles_and_intent(client):
    code = _welcome_flow(client)
    role = _post(client, "/api/admin/roles", {"name": "Enterprise Sales", "pages": ["pipeline", "customer", "intent"]})["id"]
    other_role = _post(client, "/api/admin/roles", {"name": "Retail", "pages": ["pipeline", "customer", "intent"]})["id"]
    ent, retail = _user(client, "ent@pages.test"), _user(client, "retail@pages.test")
    client.put(f"/api/admin/users/{ent}/access", json={"role_ids": [role], "position_ids": []})
    client.put(f"/api/admin/users/{retail}/access", json={"role_ids": [other_role], "position_ids": []})

    copied = _post(client, "/api/admin/pages/party.B2B/copy", {"name": "Enterprise onboarding"})
    key = copied["page_key"]
    assert key == "party.enterprise_onboarding" and copied["is_active"] is False and copied["segment"] == "B2B"
    # a copy that is off changes nothing for anyone
    assert client.get("/api/pages/party/form?segment=B2B", headers=_as(ent)).json()["page_key"] == "party.B2B"

    said = client.post("/api/intents", json={"text": "On the Enterprise onboarding page add a button 'Send welcome kit' that runs the flow "
                                                     "Welcome kit; add a note: Check the GSTIN before saving; when saved run the flow Welcome kit; "
                                                     "make GSTIN mandatory; give it to the Enterprise Sales role; activate"}).json()
    assert said["intent_type"] == "INT-103" and said["status"] == "executed", said["rationale"]
    result = said["result"]
    assert result["page_key"] == key and result["is_active"] and result["role_ids"] == [role] and result["events"] == {"after_save": code}
    assert any("button 'Send welcome kit'" in c for c in result["changes"])

    form = client.get("/api/pages/party/form?segment=B2B", headers=_as(ent)).json()
    assert form["page_key"] == key and form["events"] == {"after_save": code}
    items = [f for s in form["sections"] for f in s["fields"]]
    assert {"kind": "button", "label": "Send welcome kit", "intent": code}.items() <= next(f for f in items if f.get("kind") == "button").items()
    assert any(f.get("kind") == "text" and "GSTIN" in f["text"] for f in items)
    assert next(f for f in items if f["key"] == "tax_id")["mandatory"]
    # other roles keep the default page
    assert client.get("/api/pages/party/form?segment=B2B", headers=_as(retail)).json()["page_key"] == "party.B2B"

    # saving through the page checks that page and starts its after-save flow
    refused = run(client, "INT-15", ent, name="Mega Corp", segment="B2B", details={"_page_key": key})
    assert refused["status"] == "failed" and "GSTIN" in refused["rationale"]
    saved = run(client, "INT-15", ent, name="Mega Corp", segment="B2B", details={"_page_key": key, "tax_id": "27AAPFU0939F1ZV"})
    assert saved["status"] == "executed", saved["rationale"]
    assert saved["result"]["page_flows"] == [{"event": "after_save", "page": key, "flow": "Welcome kit"}]

    # switch off: everyone is back on the default page
    assert client.patch(f"/api/admin/pages/{key}/active", json={"is_active": False}).json()["is_active"] is False
    assert client.get("/api/pages/party/form?segment=B2B", headers=_as(ent)).json()["page_key"] == "party.B2B"

    # a button must run something that exists; page events only start flows
    bad = client.put(f"/api/admin/pages/{key}", json={"layout": {"sections": [{"title": "A", "fields": [
        {"kind": "button", "label": "Nope", "intent": "Does not exist"}]}]}})
    assert bad.status_code == 400 and "No intent or Flow Studio flow" in bad.json()["detail"]
    bad = client.put(f"/api/admin/pages/{key}", json={"layout": {"sections": [], "events": {"after_save": "INT-75"}}})
    assert bad.status_code == 400 and "Flow Studio flow" in bad.json()["detail"]
    # only administrators customise pages
    assert run(client, "INT-103", ent, page="Enterprise onboarding", instruction="activate")["status"] == "failed"


def test_customisations_stay_with_their_tenant(client):
    db = client.TestSessionLocal()
    other = Tenant(name="Other Telco", slug="other-telco", vertical=TenantVertical("both"))
    db.add(other)
    db.flush()
    seed_reference_data(db, other.id)
    other_admin = get_or_create_user(db, tenant_id=other.id, email="admin@other.test", password="x" * 10, name="Other Admin", role=UserRole.admin)
    db.commit()
    other_admin_id = other_admin.id
    db.close()

    _post(client, "/api/admin/pages/party.B2B/copy", {"name": "Enterprise onboarding"})
    said = client.post("/api/intents", json={"text": "Customize the B2B prospect page: make GSTIN mandatory"}).json()
    assert said["status"] == "executed", said["rationale"]

    theirs = client.get("/api/admin/pages", headers=_as(other_admin_id)).json()["pages"]
    assert not any(p["name"] == "Enterprise onboarding" for p in theirs)
    b2b = next(p for p in theirs if p["page_key"] == "party.B2B")
    assert b2b["customized"] is False  # the base build, untouched
    assert client.patch("/api/admin/pages/party.enterprise_onboarding/active", json={"is_active": True},
                        headers=_as(other_admin_id)).status_code == 404
