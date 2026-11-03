"""The record type builder: an admin's own record types run on the same engines as the built-in records -
relationships (parent, lookup fields, many-to-many links), a state model with role-free moves, Page Designer
pages and plain-English validation rules (state-dependent ones included), Flow Studio flows on their intents
and states, assignment and data visibility, the intents INT-151..158 in plain English, and the AI designer."""

from tests.test_custom_record_types import _as, _party, _user


def _type(client, label, prefix, **extra):
    out = client.post("/api/admin/record-types", json={"label": label, "number_prefix": prefix, **extra})
    assert out.status_code == 201, out.text
    return out.json()


def _field(client, entity_type, key, label, data_type="text", **extra):
    body = {"entity_type": entity_type, "key": key, "label": label, "data_type": data_type, "allowed_values": None, "required": False,
            "default_value": None, **extra}
    out = client.post("/api/admin/custom-fields", json=body)
    assert out.status_code == 201, out.text
    return out.json()


def _create(client, key, name, **body):
    out = client.post(f"/api/custom-records/{key}", json={"name": name, **body})
    assert out.status_code == 201, out.text
    return out.json()


def _say(client, text):
    return client.post("/api/intents", json={"text": text}).json()


SURVEY_STATES = {
    "name": "Survey lifecycle",
    "states": [{"code": "scheduled", "label": "Scheduled"}, {"code": "in_progress", "label": "In progress"},
               {"code": "completed", "label": "Completed", "editable": False},
               {"code": "cancelled", "label": "Cancelled", "editable": False, "is_terminal": True}],
    "transitions": [{"from_state": "scheduled", "to_state": "in_progress", "action": "start"},
                    {"from_state": "in_progress", "to_state": "completed", "action": "complete"},
                    {"from_state": "*", "to_state": "cancelled", "action": "cancel"},
                    {"from_state": "cancelled", "to_state": "scheduled", "action": "reopen"}],
}


def test_relationships_lookups_and_related_records(client):
    party_id = _party(client, "Acme Towers")
    _type(client, "Site", "ST", parent_entity_type="party", parent_required=True)
    _type(client, "Contact", "CT")
    _type(client, "Site Survey", "SS", parent_entity_type="x_site")
    _field(client, "x_contact", "email", "Email", "email")
    inspector = _field(client, "x_site_survey", "inspector", "Inspector", "lookup", allowed_values="Contact")  # by label
    assert inspector["allowed_values"] == "x_contact"
    assert client.post("/api/admin/custom-fields", json={"entity_type": "x_site_survey", "key": "bad", "label": "Bad", "data_type": "lookup",
                                                         "allowed_values": "Spaceship"}).status_code == 400
    rel = client.post("/api/admin/record-types/relationships", json={"from_entity_type": "x_site", "to_entity_type": "Contact",
                                                                    "label": "Contacts", "reverse_label": "Sites"})
    assert rel.status_code == 201, rel.text
    assert client.post("/api/admin/record-types/relationships", json={"from_entity_type": "party", "to_entity_type": "order",
                                                                     "label": "x"}).status_code == 400  # built-ins are related already

    site = _create(client, "site", "Head office", parent_id="Acme Towers")  # the parent by its name
    assert site["number"] == "ST-0001" and site["parent"]["id"] == party_id and site["state"] == "open"
    contact = _create(client, "contact", "Priya", fields={"email": "priya@acme.test"})
    assert client.post("/api/custom-records/contact", json={"name": "Bad", "fields": {"email": "not-an-email"}}).status_code == 400
    survey = _create(client, "site_survey", "Roof", parent_id="ST-0001", fields={"inspector": "CT-0001"})  # lookups by number
    assert survey["values"]["inspector"] == contact["id"] and survey["display"]["inspector"]["title"] == "CT-0001 · Priya"
    assert "No Contact" in client.post("/api/custom-records/site_survey", json={"name": "x", "fields": {"inspector": "CT-0099"}}).json()["detail"]

    linked = client.post(f"/api/custom-records/site/{site['id']}/links", json={"relationship": "contacts", "related": "Priya"})
    assert linked.status_code == 200, linked.text
    sections = {s["label"]: s for s in linked.json()["related"]}
    assert [i["title"] for i in sections["Contacts"]["items"]] == ["CT-0001 · Priya"]
    assert [i["title"] for i in sections["Site Surveys"]["items"]] == [f"{survey['number']} · Roof"]

    # seen from the contact: the sites it's linked to, and the surveys whose Inspector it is
    seen = {s["label"]: s for s in client.get(f"/api/custom-records/contact/{contact['id']}").json()["record"]["related"]}
    assert [i["title"] for i in seen["Sites"]["items"]] == ["ST-0001 · Head office"]
    assert [i["title"] for i in seen["Site Survey (Inspector)"]["items"]] == [f"{survey['number']} · Roof"]
    # and from the customer: its sites
    on_party = {s["label"]: s for s in client.get(f"/api/custom-records/related/party/{party_id}").json()}
    assert [i["title"] for i in on_party["Sites"]["items"]] == ["ST-0001 · Head office"]

    # nothing pointed at is deleted; unlinking works through the same intent
    assert "point to CT-0001" in client.delete(f"/api/custom-records/contact/{contact['id']}").json()["detail"]
    unlinked = client.post(f"/api/custom-records/site/{site['id']}/links", json={"relationship": "Contacts", "related": "CT-0001", "unlink": True})
    assert not {s["label"]: s for s in unlinked.json()["related"]}["Contacts"]["items"]
    assert client.delete("/api/admin/record-types/contact").status_code == 400  # Inspector on Site Survey points to it


def test_state_model_page_rules_and_flows(client):
    _type(client, "Site Survey", "SS")
    _field(client, "x_site_survey", "survey_date", "Survey date", "date", required=True)
    _field(client, "x_site_survey", "report", "Report", "textarea")
    _field(client, "x_site_survey", "result", "Result")
    _field(client, "x_site_survey", "notes", "Notes")
    saved = client.put("/api/admin/state-models/x_site_survey", json=SURVEY_STATES)
    assert saved.status_code == 200, saved.text
    assert "x_site_survey" in [m["entity_type"] for m in client.get("/api/admin/state-models").json()]

    # the Page Designer has its page; state is shown read-only, and rules can depend on it
    pages = {p["page_key"]: p for p in client.get("/api/admin/pages").json()["pages"]}
    assert "x_site_survey" in pages
    rule = client.post("/api/admin/pages/rules", json={"entity_type": "x_site_survey", "text": "if State is completed then Report is mandatory"})
    assert rule.status_code == 200, rule.text
    form = client.get("/api/custom-records/site_survey/form").json()
    state_field = next(f for s in form["sections"] for f in s["fields"] if f["key"] == "state")
    assert state_field["readonly"] and not state_field["mandatory"]

    # automations: an "after" flow on Create a record, and a state flow when a survey is completed
    def flow(body, steps):
        graph = {"nodes": [{"id": "s", "type": "start", "config": {}}] + steps + [{"id": "e", "type": "end", "config": {}}], "edges": []}
        ids = [n["id"] for n in graph["nodes"]]
        graph["edges"] = [{"id": f"x{i}", "source": a, "target": b, "label": ""} for i, (a, b) in enumerate(zip(ids, ids[1:]))]
        made = client.post("/api/admin/flows", json={"name": body.pop("name"), "entity_type": "x_site_survey", "graph": graph, **body})
        assert made.status_code == 201, made.text
        assert client.post(f"/api/admin/flows/{made.json()['id']}/activate").status_code == 200, made.json()
    flow({"name": "Note the booking", "trigger_type": "intent", "trigger_intent_type": "INT-151", "timing": "after"},
         [{"id": "n", "type": "set_field", "config": {"field": "record.notes", "value": "Booked by {{entities.record_name}}"}}])
    flow({"name": "Sign off", "trigger_type": "state_enter", "trigger_state": "completed"},
         [{"id": "n", "type": "set_field", "config": {"field": "record.result", "value": "Signed off"}}])

    survey = _create(client, "site_survey", "Roof", fields={"survey_date": "2026-11-20"})
    assert survey["state"] == "scheduled" and survey["values"]["notes"] == "Booked by Roof"  # first state; the after flow ran
    assert [t["to_state"] for t in survey["transitions"]] == ["in_progress", "cancelled"]
    url = f"/api/custom-records/site_survey/{survey['id']}"

    assert "It can go to: In progress" in client.post(f"{url}/transition", json={"state": "completed"}).json()["detail"]
    assert client.post(f"{url}/transition", json={"state": "start"}).json()["record"]["state"] == "in_progress"  # by the move's name
    refused = client.post(f"{url}/transition", json={"state": "Completed"})
    assert refused.status_code == 400 and "Report is mandatory" in refused.json()["detail"]  # the rule for the new state
    assert client.patch(url, json={"fields": {"report": "All good"}}).status_code == 200
    done = client.post(f"{url}/transition", json={"state": "Completed"}).json()["record"]
    assert done["state"] == "completed" and done["values"]["result"] == "Signed off"  # the state flow ran
    assert [h["to_state"] for h in done["history"]][:2] == ["completed", "in_progress"]
    assert "can no longer be changed" in client.patch(url, json={"fields": {"notes": "late"}}).json()["detail"]  # locked
    listed = client.get("/api/custom-records/site_survey", params={"state": "completed"}).json()
    assert listed["total"] == 1 and listed["state_counts"] == {"completed": 1}
    # a state records sit in can't be taken out of the model
    fewer = {**SURVEY_STATES, "states": SURVEY_STATES["states"][:2], "transitions": SURVEY_STATES["transitions"][:1]}
    assert "completed" in client.put("/api/admin/state-models/x_site_survey", json=fewer).json()["detail"]


def test_assignment_gives_the_record_to_another_position(client):
    _type(client, "Site Survey", "SS")
    a_pos = client.post("/api/admin/positions", json={"name": "Surveyor A", "data_scope": "own"}).json()["id"]
    b_pos = client.post("/api/admin/positions", json={"name": "Surveyor B", "data_scope": "own"}).json()["id"]
    a, b = _user(client, "surv-a"), _user(client, "surv-b")
    for uid, pos in ((a, a_pos), (b, b_pos)):
        assert client.put(f"/api/admin/users/{uid}/access", json={"role_ids": [], "position_ids": [pos]}).status_code == 200
    mine = client.post("/api/custom-records/site_survey", json={"name": "A's survey"}, headers=_as(a)).json()
    assert client.get("/api/custom-records/site_survey", headers=_as(b)).json()["total"] == 0
    given = client.post(f"/api/custom-records/site_survey/{mine['id']}/assign", json={"to_position": "Surveyor B"}, headers=_as(a))
    assert given.status_code == 200, given.text
    assert given.json()["owner"] == "Surveyor B"
    assert client.get("/api/custom-records/site_survey", headers=_as(b)).json()["total"] == 1  # B now sees what it owns


def test_records_in_plain_english(client):
    _party(client, "Acme Towers")
    _type(client, "Site Survey", "SS", parent_entity_type="party")
    _field(client, "x_site_survey", "survey_date", "Survey date", "date")
    _field(client, "x_site_survey", "floors", "Floors", "number")
    client.put("/api/admin/state-models/x_site_survey", json=SURVEY_STATES)

    made = _say(client, "Create a site survey for Acme Towers with survey date 2026-11-20 and floors 12")
    assert made["intent_type"] == "INT-151" and made["status"] == "executed", made
    assert made["result"]["number"] == "SS-0001" and made["result"]["values"] == {"survey_date": "2026-11-20", "floors": 12}
    assert any(r.get("number") == "SS-0001" for r in made["records"])  # the outcome card

    updated = _say(client, "Update SS-0001 floors=14")
    assert updated["intent_type"] == "INT-152" and updated["status"] == "executed", updated
    moved = _say(client, "Move SS-0001 to in progress")
    assert moved["intent_type"] == "INT-153" and moved["result"]["to_state"] == "in_progress", moved
    found = _say(client, "Show in progress site surveys")
    assert found["intent_type"] == "INT-156" and found["result"]["total"] == 1 and found["result"]["records"][0]["floors"] == 14, found
    client.post("/api/admin/positions", json={"name": "Survey Team", "data_scope": "own"})
    assigned = _say(client, "Assign SS-0001 to Survey Team")
    assert assigned["intent_type"] == "INT-154" and assigned["status"] == "executed", assigned


def test_the_ai_designer_drafts_and_builds_a_record_type(client):
    _type(client, "Contact", "CT")
    text = ("Site Survey for a customer (required): survey date (date, required), floors (number), outcome (Pending, Feasible, Not feasible), "
            "inspector (lookup to Contact), report (long text). States: scheduled -> in progress -> completed, cancelled. "
            "Rules: if State is completed then Report is mandatory. Related to Contacts as 'Survey contacts'.")
    draft = client.post("/api/admin/record-types/design", json={"text": text})
    assert draft.status_code == 200, draft.text
    plan = draft.json()
    assert plan["type"]["label"] == "Site Survey" and plan["type"]["parent_entity_type"] == "party" and plan["type"]["parent_required"]
    fields = {f["key"]: f for f in plan["fields"]}
    assert fields["survey_date"]["data_type"] == "date" and fields["survey_date"]["required"]
    assert fields["floors"]["data_type"] == "number" and fields["report"]["data_type"] == "textarea"
    assert fields["outcome"]["values"] == ["Pending", "Feasible", "Not feasible"]
    assert fields["inspector"]["data_type"] == "lookup" and fields["inspector"]["target"] == "x_contact"
    assert [s["code"] for s in plan["states"]] == ["scheduled", "in_progress", "completed", "cancelled"]
    assert {"from_state": "*", "to_state": "cancelled", "action": "cancel", "allowed_roles": ""} in plan["transitions"]
    assert plan["relationships"][0]["to"] == "x_contact" and plan["relationships"][0]["label"] == "Survey contacts"
    assert client.get("/api/admin/record-types").json()["types"][0]["key"] == "contact"  # a draft saves nothing

    built = client.post("/api/admin/record-types/design/build", json={"plan": plan})
    assert built.status_code == 201, built.text
    assert built.json()["applied"] and built.json()["rules"] == ["if State is completed then Report is mandatory"], built.json()["notes"]
    rt = next(t for t in client.get("/api/admin/record-types").json()["types"] if t["key"] == "site_survey")
    assert [f["key"] for f in rt["fields"]] == ["survey_date", "floors", "outcome", "inspector", "report"]
    assert [s["code"] for s in rt["states"]] == ["scheduled", "in_progress", "completed", "cancelled"]
    assert rt["relationships"][0]["name"] == "Survey contacts"

    # it works straight away - and the command bar can design one too (administrators)
    party_id = _party(client, "Globex")
    survey = _create(client, "site_survey", "Lobby", parent_id=party_id, fields={"survey_date": "2026-12-01"})
    assert survey["state"] == "scheduled" and survey["values"]["outcome"] is None
    asked = _say(client, "Design a record type: Asset Inspection for an order with inspected on (date) and passed (yes/no)")
    assert asked["intent_type"] == "INT-158" and asked["status"] == "executed", asked
    inspection = next(t for t in client.get("/api/admin/record-types").json()["types"] if t["key"] == "asset_inspection")
    assert inspection["parent_entity_type"] == "order" and {f["key"]: f["data_type"] for f in inspection["fields"]} == {
        "inspected_on": "date", "passed": "boolean"}
