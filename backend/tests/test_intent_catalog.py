"""Intent catalog (admin control, roles) and the command bar copilot
(prefill, options, pre-checks, result cards, next steps)."""

from app.auth import hash_password
from app.models.auth import User, UserRole
from app.models.tenant import Tenant
from app.tenancy import DEFAULT_TENANT_SLUG


def _agent(client, name):
    db = client.TestSessionLocal()
    try:
        tenant_id = db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id
        user = User(tenant_id=tenant_id, email=f"{name}@intents.test", password_hash=hash_password("x" * 8), name=name, role=UserRole.agent)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _codes(client, headers=None):
    resp = client.get("/api/intents/catalog", headers=headers or {})
    assert resp.status_code == 200, resp.text
    return {i["code"] for i in resp.json()["intents"]}


def _run(client, text, **extra):
    resp = client.post("/api/intents", json={"text": text, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _structured(client, code, entities, headers=None):
    resp = client.post("/api/intents", json={"text": code, "structured": {"intent_type": code, "entities": entities}}, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _prepare(client, code, entities=None, text="", context=None):
    resp = client.post("/api/intents/prepare", json={"intent_type": code, "entities": entities or {}, "text": text, "context": context})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body, {f["key"]: f for f in body["fields"]}


def _launch_product(client):
    _run(client, "Launch a new bundle. name: Fiber 800, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 1000, discount: 0")


def test_catalog_describes_every_intent_for_business_users(client):
    body = client.get("/api/intents/catalog").json()
    intents = {i["code"]: i for i in body["intents"]}
    assert {f"INT-{n:02d}" for n in range(1, 33)} <= set(intents)
    quote = intents["INT-05"]
    assert quote["label"] == "Create a quote" and quote["category"] == "sales" and quote["icon"]
    fields = {f["key"]: f for f in quote["fields"]}
    assert fields["opportunity_id"]["type"] == "record" and fields["opportunity_id"]["required"]
    assert fields["requested_discount_pct"]["type"] == "percent"
    assert {c["key"] for c in body["categories"]} >= {"sales", "orders", "service", "products"}


def test_admin_can_switch_intents_off_rename_and_teach_phrases(client):
    resp = client.put("/api/admin/intents/INT-03", json={"is_active": False})
    assert resp.status_code == 200 and resp.json()["is_active"] is False
    assert "INT-03" not in _codes(client)
    retired = _run(client, "Retire this bundle. offering_name: Fiber 500 Bundle, replacement_offering_name: Fiber 1000 Elite")
    assert retired["status"] == "failed" and "switched off" in retired["rationale"]

    client.put("/api/admin/intents/INT-24", json={"label": "Raise the PO", "phrases": ["raise a purchase order"]})
    assist = client.post("/api/intents/assist", json={"text": "please raise a purchase order for quote 3"}).json()
    assert assist["intent_type"] == "INT-24" and assist["intent_label"] == "Raise the PO"

    admin_view = {i["code"]: i for i in client.get("/api/admin/intents").json()["intents"]}
    assert admin_view["INT-03"]["is_active"] is False and admin_view["INT-24"]["phrases"] == ["raise a purchase order"]
    client.post("/api/admin/intents/INT-03/reset")
    assert "INT-03" in _codes(client)


def test_intents_follow_the_users_roles(client):
    approvers = client.post("/api/admin/roles", json={"name": "Approvers", "pages": ["intent"]}).json()["id"]
    sellers = client.post("/api/admin/roles", json={"name": "Sellers", "pages": ["intent"]}).json()["id"]
    client.put("/api/admin/intents/INT-06", json={"role_ids": [approvers]})
    seller, approver, newcomer = _agent(client, "seller"), _agent(client, "approver"), _agent(client, "newcomer")
    client.put(f"/api/admin/users/{seller}/access", json={"role_ids": [sellers], "position_ids": []})
    client.put(f"/api/admin/users/{approver}/access", json={"role_ids": [approvers, sellers], "position_ids": []})

    assert "INT-06" not in _codes(client, _as(seller)) and "INT-05" in _codes(client, _as(seller))
    assert "INT-06" in _codes(client, _as(approver))
    assert "INT-06" in _codes(client, _as(newcomer))  # no roles yet -> nothing hidden
    denied = _structured(client, "INT-06", {"quote_id": 1, "decision": "approve"}, _as(seller))
    assert denied["status"] == "failed" and "not available to your roles" in denied["rationale"]


def test_admin_adds_a_new_intent_without_code(client):
    lead = _run(client, "Qualify this lead. company_name: Escalate Co, source: referral, region: North")
    opp = lead["result"]["opportunity_id"]
    resp = client.post("/api/admin/intents", json={
        "label": "Escalate a deal",
        "description": "Flag a deal for management attention",
        "behaviour": "Notify the owner that the deal was escalated",
        "category": "sales",
        "icon": "🔥",
        "entity_type": "opportunity",
        "phrases": ["escalate this deal"],
        "fields": [{"label": "Urgency", "type": "picklist", "values": ["high", "critical"], "required": True}],
    })
    assert resp.status_code == 201, resp.text
    created = resp.json()
    intent = created["intent"]
    assert intent["code"].startswith("FLOW-") and intent["is_active"] and not created["flow_errors"]
    fields = {f["key"]: f for f in intent["fields"]}
    assert fields["opportunity_id"]["type"] == "record" and fields["opportunity_id"]["required"]  # added for the record it acts on
    assert fields["urgency"]["values"] == ["high", "critical"]
    assert intent["code"] in _codes(client)

    ran = _structured(client, intent["code"], {"opportunity_id": opp, "urgency": "high"})
    assert ran["status"] == "executed", ran["rationale"]
    assert any(n["type"] == "notify" for run in ran["result"]["flows_run"] for n in run["trace"])

    assert client.put(f"/api/admin/intents/{intent['code']}", json={"is_active": False}).json()["is_active"] is False
    assert intent["code"] not in _codes(client)
    assert client.delete(f"/api/admin/intents/{intent['code']}").status_code == 204


def test_prepare_fills_what_it_can_and_says_where_from(client):
    _launch_product(client)
    lead = _run(client, "Qualify this lead. company_name: Prefill Co, source: event, region: North")
    opp = lead["result"]["opportunity_id"]

    body, fields = _prepare(client, "INT-05", text="quote Fiber 800 with 15% off", context={"entity_type": "opportunity", "entity_id": opp})
    assert fields["opportunity_id"]["value"] == opp and fields["opportunity_id"]["origin"] == "context"
    assert fields["opportunity_id"]["display"].startswith("Prefill Co")
    assert fields["offering_name"]["value"] == "Fiber 800" and fields["offering_name"]["origin"] == "understood"
    assert fields["requested_discount_pct"]["value"] == 15
    assert any(o["value"] == "Fiber 800" for o in fields["offering_name"]["options"])
    # a 15% discount makes the justification required, and says why
    assert fields["discount_justification"]["required"] and "discount_justification" in body["missing"]
    assert any("10%" in t for t in body["tips"])
    assert not body["ready"]

    # the user's habits: INT-04 with the same source twice -> remembered
    _run(client, "Qualify this lead. company_name: Habit One, source: partner, region: North")
    _run(client, "Qualify this lead. company_name: Habit Two, source: partner, region: North")
    _, fields = _prepare(client, "INT-04")
    assert fields["source"]["value"] == "partner" and fields["source"]["origin"] == "usual"


def test_prepare_prechecks_blockers_and_offers_the_fix(client):
    _launch_product(client)
    lead = _run(client, "Qualify this lead. company_name: Blocker Co, source: event, region: North")
    quote = _structured(client, "INT-05", {"opportunity_id": lead["result"]["opportunity_id"], "offering_name": "Fiber 800"})
    quote_id = quote["result"]["quote_id"]

    body, fields = _prepare(client, "INT-24", context={"entity_type": "quote", "entity_id": quote_id})
    assert fields["quote_id"]["value"] == quote_id
    assert not body["ready"] and body["blockers"]
    fix = body["blockers"][0]["fix"]
    assert fix["code"] == "INT-06" and fix["entities"] == {"quote_id": quote_id}

    # INT-06's quote: the only quote awaiting approval is guessed
    body, fields = _prepare(client, "INT-06")
    assert fields["quote_id"]["value"] == quote_id and fields["quote_id"]["origin"] == "guess"
    assert fields["decision"]["value"] == "approve"
    assert body["ready"], body


def test_results_show_records_and_next_steps(client):
    lead = _run(client, "Qualify this lead. company_name: Outcome Co, source: referral, region: North")
    assert lead["status"] == "executed"
    cards = {(r["entity_type"], r["change"]) for r in lead["records"]}
    assert ("lead", "created") in cards and ("opportunity", "created") in cards
    opp_card = next(r for r in lead["records"] if r["entity_type"] == "opportunity")
    assert any(f["key"] == "name" for f in opp_card["fields"]) and opp_card["state"]
    assert lead["summary"].startswith("Qualify a lead - done.") and "Created" in lead["summary"]
    next_codes = {n["code"]: n for n in lead["next_actions"]}
    assert next_codes["INT-18"]["entities"]["opportunity_id"] == lead["result"]["opportunity_id"]

    # a recommended step the user can't run is not recommended
    client.put("/api/admin/intents/INT-18", json={"is_active": False})
    again = _run(client, "Qualify this lead. company_name: Outcome Two, source: referral, region: North")
    assert "INT-18" not in {n["code"] for n in again["next_actions"]}

    recent = client.get("/api/intents/recent").json()
    assert recent[0]["code"] == "INT-04"


def test_opportunity_carries_a_product_into_its_quote(client):
    _launch_product(client)
    # captured when the lead is qualified
    lead = _structured(client, "INT-04", {"company_name": "Product Co", "source": "referral", "offering_name": "Fiber 800", "quantity": 3})
    assert lead["status"] == "executed", lead["rationale"]
    opp = lead["result"]["opportunity_id"]
    record = client.get(f"/api/records/opportunity/{opp}").json()
    assert record["core_fields"]["offering_name"] == "Fiber 800" and record["core_fields"]["quantity"] == 3
    assert "offering_name" in record["updatable_core_fields"] and "Fiber 800" in record["core_field_options"]["offering_name"]
    assert record["core_field_labels"]["offering_name"] == "Product / bundle"

    # the quote form starts from it
    _, fields = _prepare(client, "INT-05", context={"entity_type": "opportunity", "entity_id": opp})
    assert fields["offering_name"]["value"] == "Fiber 800" and fields["quantity"]["value"] == 3
    _, fields = _prepare(client, "INT-05", entities={"opportunity_id": opp})
    assert fields["offering_name"]["origin"] == "linked"
    # ...and so does a plain "quote opportunity N"
    quote = _structured(client, "INT-05", {"opportunity_id": opp})
    assert quote["status"] == "executed", quote["rationale"]

    # editable on the opportunity, only to a real product
    other = _structured(client, "INT-04", {"company_name": "No Product Co", "source": "referral"})["result"]["opportunity_id"]
    assert client.get(f"/api/records/opportunity/{other}").json()["core_fields"]["offering_name"] is None
    ok = _structured(client, "INT-29", {"entity_type": "opportunity", "entity_id": other, "fields": {"offering_name": "Fiber 800", "quantity": 2}})
    assert ok["status"] == "executed", ok["rationale"]
    bad = _structured(client, "INT-29", {"entity_type": "opportunity", "entity_id": other, "fields": {"offering_name": "Teleporter"}})
    assert bad["status"] == "failed" and "No active product" in bad["rationale"]
    assert client.get("/api/opportunities").json()[0]["offering_name"] is not None


def test_lookup_finds_records_by_name_or_number(client):
    lead = _run(client, "Qualify this lead. company_name: Lookup Labs, source: referral, region: North")
    opp = lead["result"]["opportunity_id"]
    by_name = client.get("/api/intents/lookup", params={"record_type": "opportunity", "q": "lookup"}).json()
    assert [o["id"] for o in by_name] == [opp]
    by_id = client.get("/api/intents/lookup", params={"record_type": "opportunity", "q": f"#{opp}"}).json()
    assert by_id[0]["id"] == opp and by_id[0]["label"].startswith("Lookup Labs")
