"""New Connect: BRT_ID on every record, B2B/B2C prospect pages (Page
Designer), leads with segment products, automatic account on activation,
and BIM loads that require BRT_IDs."""

from sqlalchemy import inspect, text

from app import brt_id
from app.models.crm import Lead, Party
from app.models.pages import LeadProduct


def _run(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def _launch(client, name, segment, price=1000):
    body = client.post("/api/intents", json={
        "text": f"Launch a new bundle. name: {name}, composition: Fiber500, segment: {segment}, channel: {segment[:3]}, price: {price}, discount: 0"
    }).json()
    assert body["status"] == "executed", body


def test_every_record_gets_a_valid_unique_brt_id_and_system_fields(client):
    body = _run(client, "INT-15", name="Brt Co", segment="B2B")
    assert body["status"] == "executed", body
    db = client.TestSessionLocal()
    party = db.get(Party, body["result"]["party_id"])
    assert brt_id.is_valid(party.brt_id, "parties") and party.brt_id.startswith("PTY-")
    assert brt_id.issued(db.connection(), party.brt_id, "parties")
    assert party.sys_created_at is not None and party.sys_created_by is not None
    assert party.profile.brt_id.startswith("PPR-")
    seen: set[str] = set()
    for table in inspect(db.bind).get_table_names():
        if table == "brt_id_counter":
            continue
        codes = [r[0] for r in db.execute(text(f"SELECT brt_id FROM {table}"))]
        assert all(brt_id.is_valid(c, table) for c in codes), table
        assert not seen & set(codes)
        seen |= set(codes)
    assert not brt_id.is_valid(party.brt_id[:-1] + ("A" if party.brt_id[-1] != "A" else "B"))  # tampered check character
    db.close()


def test_prospect_page_is_segment_specific_with_mandatory_fields_and_rules(client):
    b2b = client.get("/api/pages/party/form", params={"segment": "B2B"}).json()
    b2c = client.get("/api/pages/party/form", params={"segment": "B2C"}).json()
    b2b_keys = {f["key"] for s in b2b["sections"] for f in s["fields"]}
    b2c_keys = {f["key"] for s in b2c["sections"] for f in s["fields"]}
    assert {"tax_id", "legal_name", "contact_name", "industry"} <= b2b_keys and "date_of_birth" not in b2b_keys
    assert {"date_of_birth", "id_type", "kyc_status"} <= b2c_keys and "tax_id" not in b2c_keys
    assert {"address_line1", "postal_code"} <= b2b_keys & b2c_keys

    plan = client.post("/api/admin/pages/party.B2B/plan", json={"instruction": "make GSTIN mandatory; add Payment terms as a dropdown with values 30 days, 60 days in the Commercials section"}).json()
    assert plan["preview"][0].startswith("Make GSTIN") and plan["ops"][1]["op"] == "add_field"
    saved = client.post("/api/admin/pages/party.B2B/apply", json={"ops": plan["ops"]}).json()
    assert any(s["title"] == "Commercials" for s in saved["layout"]["sections"])
    rule = client.post("/api/admin/pages/rules", json={"entity_type": "party", "segment": "B2B", "text": "GSTIN must be a valid GSTIN"})
    assert rule.status_code == 200, rule.text

    missing = _run(client, "INT-15", name="ITC Ltd.", segment="B2B", details={"legal_name": "ITC Limited"})
    assert missing["status"] == "failed" and "GSTIN / VAT number is mandatory" in str(missing)
    bad = _run(client, "INT-15", name="ITC Ltd.", segment="B2B", details={"tax_id": "12345"})
    assert bad["status"] == "failed" and "valid 15-character GSTIN" in str(bad)
    ok = _run(client, "INT-15", name="ITC Ltd.", segment="B2B", details={"tax_id": "27AAACI1234A1Z5", "payment_terms": "30 days", "employee_count": "5000+"})
    assert ok["status"] == "executed", ok
    form = client.get("/api/pages/party/form", params={"record_id": ok["result"]["party_id"]}).json()
    values = {f["key"]: f["value"] for s in form["sections"] for f in s["fields"]}
    assert values["tax_id"] == "27AAACI1234A1Z5" and values["payment_terms"] == "30 days" and values["segment"] == "B2B"
    # B2C prospects are untouched by the B2B page and rules; a plain "add prospect" works as before
    assert _run(client, "INT-15", name="Priya Sharma", segment="B2C", details={"date_of_birth": "1990-04-01"})["status"] == "executed"
    assert client.post("/api/intents", json={"text": "Add a prospect. name: Globex, email: ops@globex.com"}).json()["status"] == "executed"


def test_lead_interest_crosses_segments_and_prospect_becomes_account_on_activation(client):
    _launch(client, "Fiber 800 Biz", "B2B-SMB")
    _launch(client, "Home Fiber 100", "B2C", 500)
    party_id = _run(client, "INT-15", name="Acme Works", segment="B2B")["result"]["party_id"]
    offered = client.get("/api/pages/offerings", params={"party_id": party_id}).json()
    assert "Fiber 800 Biz" in offered and "Home Fiber 100" not in offered

    interest = _run(client, "INT-46", party_id=party_id, products=["Home Fiber 100", "Fiber 800 Biz"])
    assert interest["status"] == "executed", interest
    assert {p["offering_name"] for p in interest["result"]["interested_products"]} == {"Home Fiber 100", "Fiber 800 Biz"}
    lead = _run(client, "INT-46", party_id=party_id, source="referral", products=[{"offering_name": "Fiber 800 Biz", "quantity": 3}],
                details={"requirement_summary": "Internet for 3 branches"})
    assert lead["status"] == "executed", lead
    lead_id = lead["result"]["lead_id"]
    assert client.get(f"/api/pages/lead/{lead_id}/products").json()[0] == {
        "offering_name": "Fiber 800 Biz", "quantity": 3, "notes": None,
        "brt_id": client.get(f"/api/pages/lead/{lead_id}/products").json()[0]["brt_id"]}

    qualified = _run(client, "INT-17", lead_id=lead_id, decision="approve")
    assert qualified["status"] == "executed", qualified
    opp_id = qualified["result"]["opportunity_id"]
    db = client.TestSessionLocal()
    assert db.query(LeadProduct).filter(LeadProduct.lead_id == lead_id).count() == 1
    db.close()
    quote = client.post("/api/intents", json={"text": f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: Fiber 800 Biz"}).json()
    quote_id = quote["result"]["quote_id"]
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}"})
    db = client.TestSessionLocal()
    assert db.get(Party, party_id).profile.account_status == "prospect"
    db.close()
    activated = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"}).json()
    assert activated["result"]["status"] == "completed", activated
    db = client.TestSessionLocal()
    party = db.get(Party, party_id)
    assert party.profile.account_status == "account" and party.party_type == "organization"
    db.close()


def test_rule_compiler_and_flow_rules(client):
    compiled = client.post("/api/admin/pages/rules/compile", json={
        "entity_type": "party", "segment": "B2B", "text": "if Employees is 5000+ then PAN is mandatory",
        "values": {"employee_count": "5000+"}}).json()
    assert compiled["explanation"] == "When Employees is 5000+: PAN / tax reference is mandatory"
    assert compiled["test"] == ["PAN / tax reference is mandatory here"]
    assert client.post("/api/admin/pages/rules/compile", json={"entity_type": "party", "text": "make it nicer"}).status_code == 400

    flow = client.post("/api/admin/flows", json={
        "name": "No competitor prospects", "entity_type": "party", "trigger_type": "validation", "is_active": True,
        "graph": {"nodes": [
            {"id": "s", "type": "start", "label": "Start", "x": 0, "y": 0, "config": {}},
            {"id": "c", "type": "condition", "label": "Competitor?", "x": 0, "y": 0, "config": {"field": "current_provider", "operator": "eq", "value": "OurRival"}},
            {"id": "r", "type": "reject", "label": "Reject", "x": 0, "y": 0, "config": {"message": "We don't sign up OurRival's staff accounts"}},
            {"id": "e", "type": "end", "label": "End", "x": 0, "y": 0, "config": {}}],
            "edges": [{"id": "1", "source": "s", "target": "c"}, {"id": "2", "source": "c", "target": "r", "label": "true"},
                      {"id": "3", "source": "c", "target": "e", "label": "false"}]},
    })
    assert flow.status_code in (200, 201), flow.text
    assert client.post(f"/api/admin/flows/{flow.json()['id']}/activate").status_code == 200
    rule = client.post("/api/admin/pages/rules", json={"entity_type": "party", "flow_id": flow.json()["id"]})
    assert rule.status_code == 200 and rule.json()["kind"] == "flow"
    blocked = _run(client, "INT-15", name="Rival Staff", segment="B2C", details={"current_provider": "OurRival"})
    assert blocked["status"] == "failed" and "OurRival's staff" in str(blocked)
    assert _run(client, "INT-15", name="Happy Home", segment="B2C", details={"current_provider": "Jio"})["status"] == "executed"


def test_bim_loads_need_issued_brt_ids(client):
    entities = {e["key"]: e for e in client.get("/api/bim/entities").json()}
    assert entities["prospect"]["columns"][0]["name"] == "brt_id" and entities["prospect"]["prefix"] == "PTY"
    ids = client.post("/api/bim/brt-ids", json={"entity": "prospect", "count": 3}).json()["brt_ids"]
    lead_ids = client.post("/api/bim/brt-ids", json={"entity": "lead", "count": 1}).json()["brt_ids"]
    forged = ids[0][:-1] + ("A" if ids[0][-1] != "A" else "B")
    csv_text = "\n".join([
        "brt_id,name,email,segment,tax_id,city",
        f"{ids[0]},Tata Steel,it@tata.example,B2B,27AAACT2727Q1ZW,Jamshedpur",
        f",No Id Co,,B2B,,",
        f"{forged},Forged Co,,B2B,,",
        f"{lead_ids[0]},Wrong Entity,,B2B,,",
        f"{ids[1]},Asha Rao,asha@example.com,B2C,,Bhubaneswar",
    ])
    dry = client.post("/api/bim/load", json={"entity": "prospect", "content": csv_text, "dry_run": True}).json()
    assert (dry["inserted"], dry["failed"]) == (2, 3) and dry["dry_run"]
    db = client.TestSessionLocal()
    assert db.query(Party).filter(Party.brt_id == ids[0]).count() == 0  # a dry run keeps nothing
    db.close()
    job = client.post("/api/bim/load", json={"entity": "prospect", "content": csv_text, "file_name": "prospects.csv"}).json()
    assert (job["inserted"], job["failed"], job["status"]) == (2, 3, "completed_with_errors")
    reasons = " ".join(" ".join(e["errors"]) for e in job["errors"])
    assert "brt_id is missing" in reasons and "is not a BRT_ID BRT 360 issued" in reasons

    loaded = client.get(f"/api/bim/records/{ids[0]}").json()
    assert loaded["name"] == "Tata Steel" and loaded["tax_id"] == "27AAACT2727Q1ZW" and loaded["segment"] == "B2B"

    # leads reference prospects by BRT_ID; the same BRT_ID again updates (upsert)
    lead_csv = f"brt_id,party_brt_id,source,products\n{lead_ids[0]},{ids[0]},event,Fiber500 x 2"
    assert client.post("/api/bim/load", json={"entity": "lead", "content": lead_csv}).json()["inserted"] == 1
    again = client.post("/api/bim/load", json={"entity": "lead", "content": lead_csv.replace("event", "partner")}).json()
    assert again["updated"] == 1
    db = client.TestSessionLocal()
    lead = db.query(Lead).filter(Lead.brt_id == lead_ids[0]).one()
    assert lead.source == "partner" and lead.party.brt_id == ids[0]
    db.close()

    # EAI: a named interface with a mapping, insert-only
    created = client.post("/api/bim/interfaces", json={"code": "crm-sync", "name": "Old CRM", "entity": "prospect", "mode": "insert",
                                                        "mapping": {"CustomerName": "name", "BRT": "brt_id"}})
    assert created.status_code == 200, created.text
    eai = client.post("/api/bim/eai/CRM-SYNC", json={"records": [{"BRT": ids[2], "CustomerName": "Via API Ltd", "segment": "B2B"},
                                                               {"BRT": ids[0], "CustomerName": "Tata Again"}]}).json()
    assert (eai["inserted"], eai["failed"]) == (1, 1) and "already loaded" in eai["results"][1]["errors"][0]


def test_full_width_fields_are_kept_and_shown(client):
    page = next(p for p in client.get("/api/admin/pages").json()["pages"] if p["page_key"] == "party.B2B")
    layout = page["layout"]
    field = layout["sections"][0]["fields"][1]
    field["span"] = "full"
    saved = client.put("/api/admin/pages/party.B2B", json={"layout": layout}).json()
    assert saved["layout"]["sections"][0]["fields"][1]["span"] == "full"
    form = client.get("/api/pages/party/form", params={"segment": "B2B"}).json()
    shown = {f["key"]: f.get("span") for s in form["sections"] for f in s["fields"]}
    assert shown[field["key"]] == "full"


def test_sales_catalog_says_why_a_product_is_not_offered(client):
    _launch(client, "Fiber 800 Biz", "B2B")
    _launch(client, "Home Fiber 100", "B2C")
    client.post("/api/admin/product-model", json={"name": "Transmission Leased Line", "segment": "B2B", "price": 5000})  # a draft
    party_id = _run(client, "INT-15", name="Wipro India", segment="B2B")["result"]["party_id"]
    hidden = {h["name"]: h for h in client.get("/api/catalog/not-offered", params={"party_id": party_id}).json()}
    assert hidden["Transmission Leased Line"]["fix"] == "publish" and "not published" in hidden["Transmission Leased Line"]["reason"]
    assert hidden["Home Fiber 100"]["fix"] == "segment" and "Fiber 800 Biz" not in hidden
    client.post("/api/admin/product-model/Transmission Leased Line/publish")
    assert "Transmission Leased Line" not in {h["name"] for h in client.get("/api/catalog/not-offered", params={"party_id": party_id}).json()}


def test_lead_selection_includes_drafts_and_other_segments(client):
    _launch(client, "Home Fiber 100", "B2C")
    created = client.post("/api/admin/product-model", json={"name": "Transmission Leased Line", "segment": "B2B", "price": 5000})
    assert created.status_code == 200, created.text
    party_id = _run(client, "INT-15", name="Wipro India", segment="B2B")["result"]["party_id"]
    prepared = client.post("/api/intents/prepare", json={"intent_type": "INT-46", "entities": {"party_id": party_id}})
    assert prepared.status_code == 200, prepared.text
    field = next(f for f in prepared.json()["fields"] if f["key"] == "products")
    assert {"Transmission Leased Line", "Home Fiber 100"} <= {o["value"] for o in field["options"]}
    selected = ["Transmission Leased Line", "Home Fiber 100"]
    lead = _run(client, "INT-46", party_id=party_id, products=selected)
    assert lead["status"] == "executed", lead
    saved = client.get(f"/api/pages/lead/{lead['result']['lead_id']}/products").json()
    assert [p["offering_name"] for p in saved] == selected
    invalid = _run(client, "INT-46", party_id=party_id, products=["Unknown product"])
    assert invalid["status"] == "failed", invalid
