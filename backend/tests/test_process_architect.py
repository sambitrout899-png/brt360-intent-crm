"""The AI solution architect: a BRD read end to end into a design for this
organization - verified against the real system (unknown actions and broken
conditions dropped, every piece designed by the rule builders, every
requirement traced), repaired once by the model when something didn't hold,
implemented into a draft map version, and written up as a Word document."""

import json

from app.orchestration import process_architect

ARCH = "/api/process-builder/architect"
MAP = "/api/admin/function-map"

BRD = """Enterprise Deal Desk - business requirements
1. Large enterprise deals need a commercial review: quotes with discount above 25% need approval from the Sales Director.
2. Referred deals that pass feasibility should skip straight to ordering - no separate quote round.
3. When a quote is approved the account manager must be told the quote is cleared.
4. The quote screen must capture why a discount was given.
5. Approved orders must be pushed to our ERP every night."""


class _Target:
    label = "external AI API"


def _design(missing_r2=False):
    return {
        "name": "Enterprise deal desk",
        "summary": "Commercial review on big discounts; referred feasible deals go straight to an order.",
        "requirements": [{"id": f"R{i}", "text": t, "quote": t, "kind": k} for i, (t, k) in enumerate([
            ("Discount above 25% needs Sales Director approval", "approval"), ("Referred feasible deals skip the quote", "rule"),
            ("Tell the account manager when a quote is approved", "notification"), ("Capture the discount reason on the quote", "field"),
            ("Push approved orders to the ERP nightly", "integration")], start=1)],
        "placement": {"mode": "append", "process_key": "b2b_sto", "why": "It changes how B2B deals are sold"},
        "record": "opportunity",
        "steps": [{"code": "INT-18", "why": "feasibility decides the road", "requirements": ["R2"]},
                  {"code": "INT-106", "why": "order without a quote", "requirements": ["R2"]},
                  {"code": "INT-999", "why": "made up", "requirements": []}],
        "links": [] if missing_r2 else [{"from": "INT-18", "to": "INT-106", "kind": "next", "replaces_existing": False, "requirements": ["R2"],
                                         "when": {"field": "parent.lead.source", "operator": "eq", "value": "referral"}, "why": "Referred - order it"}],
        "pieces": [{"kind": "approval", "text": "Quotes with discount above 25% need approval from the Sales Director", "requirements": ["R1"]},
                   {"kind": "flow", "text": "When a quote is approved, notify the account manager that the quote is cleared", "requirements": ["R3"]},
                   {"kind": "page", "text": "On the quote page add a mandatory field Discount reason", "requirements": ["R4"]}],
        "gaps": [{"requirement": "R5", "need": "ERP integration", "why": "outside the platform", "effort": "M", "workaround": "export"}],
        "assumptions": [{"text": "Referral is read from the lead's source", "requirement": "R2"}],
        "questions": [], "alternatives": [{"option": "A quote auto-approved for referrals", "why_not": "still a quote round"}],
    }


def test_the_architect_designs_verifies_and_repairs_with_the_model(client, monkeypatch):
    calls = []

    def ask(db, tenant_id, text, ctx, extra=""):
        calls.append(extra)
        assert "INT-18" in json.dumps(ctx["intents"]) and ctx["processes"]  # grounded in the organization's setup
        return _design(missing_r2=len(calls) == 1)  # the first answer forgets R2's link; the review round adds it

    monkeypatch.setattr(process_architect, "_ask_model", ask)
    monkeypatch.setattr(process_architect, "_model", lambda db, tid: _Target())
    out = client.post(ARCH, json={"text": BRD}).json()
    assert len(calls) == 2 and "INT-999" in calls[1] and out["by"] == "ai+review", out.get("notes")
    assert [s["code"] for s in out["steps"]] == ["INT-18", "INT-106"]  # the made-up action is gone
    assert any("INT-999" in n for n in out["notes"])
    link = out["links"][0]
    assert (link["from_label"], link["when"]) == ("Check feasibility", {"field": "parent.lead.source", "operator": "eq", "value": "referral"})
    assert {c["kind"] for c in out["builder"]["components"]} == {"approval", "flow", "page"}
    assert all(c["by"] for c in out["coverage"]) and out["uncovered"] == []  # every requirement carried
    assert any("Sales Director" in a["text"] for a in out["assumptions"])  # the builder's sure answer, recorded
    assert out["ready"] and out["placement"]["label"] == "B2B Sales to Order"

    version = client.post(f"{ARCH}/implement", json={"analysis": out}).json()
    assert version["version"]["status"] == "draft" and version["builds"][0]["status"] == "pending"
    links = {(e["source"], e["target"]): e for e in version["edges"] if e["kind"] == "next"}
    assert links[("INT-18", "INT-106")]["when"]["field"] == "parent.lead.source"
    sto = next(p for p in version["processes"] if p["key"] == "b2b_sto")
    assert "INT-106" in sto["steps"]

    doc = client.post(f"{ARCH}/document", json={"analysis": out})
    assert doc.status_code == 200 and doc.content[:2] == b"PK"


def test_without_a_model_the_rules_still_design_it(client):
    out = client.post(ARCH, json={"text": BRD, "name": "Deal desk"}).json()
    assert out["by"] == "rules" and out["model"] == "built-in rules"
    assert len(out["requirements"]) >= 4
    assert out["builder"]["components"]  # the sentences the rule readers know were designed
    assert any(e["need"] == "Integration with an outside system" for e in out["builder"]["engineering"])


def test_a_blocking_question_holds_the_design_and_the_answer_settles_it(client, monkeypatch):
    def ask(db, tenant_id, text, ctx, extra=""):
        d = _design()
        if "DECISIONS" not in extra:
            d["questions"] = [{"id": "Q1", "prompt": "Who approves above 25%?", "options": ["Sales Director", "CFO"], "default": None}]
        return d

    monkeypatch.setattr(process_architect, "_ask_model", ask)
    first = client.post(ARCH, json={"text": BRD}).json()
    assert not first["ready"] and [q["id"] for q in first["questions"]] == ["Q1"]
    assert client.post(f"{ARCH}/implement", json={"analysis": first}).status_code == 409  # open questions first
    settled = client.post(ARCH, json={"text": BRD, "decisions": {"Q1": "Sales Director"}}).json()
    assert settled["ready"] and not settled["questions"]


def test_a_condition_in_an_automation_sentence_is_kept_and_holds_at_run_time(client):
    """“When a lead is approved, check the Prospect “Company type” and if it is Public Limited, check the Prospect
    to an Account” (from a user's BRD): the condition used to be dropped - every approved lead converted."""
    from test_phase5_fulfillment import _launch_bundle

    from app.crm_core import custom_field_service
    from app.models.crm import Party
    from app.models.custom_fields import CustomFieldDefinition
    from app.models.tenant import Tenant

    client.get("/api/admin/pages")  # the standard page fields (Company type among them) are set up on first use
    with client.TestSessionLocal() as db:
        tid = db.query(Tenant).filter(Tenant.slug == "default").one().id
        if not db.query(CustomFieldDefinition).filter_by(tenant_id=tid, entity_type="party", key="company_type").first():
            db.add(CustomFieldDefinition(tenant_id=tid, entity_type="party", key="company_type", label="Company type", data_type="picklist",
                                         allowed_values="Private Limited,Public Limited,Government"))
            db.commit()
    _launch_bundle(client, name="Fiber 700 Bundle", price=700)
    text = "When a lead is approved, check the Prospect “Company type” and if it is Public Limited, check the Prospect to an Account"
    bp = client.post("/api/process-builder/design", json={"request": text}).json()
    flow = next(c for c in bp["components"] if c["kind"] == "flow")
    check = next(n for n in flow["spec"]["graph"]["nodes"] if n["type"] == "condition")
    assert check["config"] == {"field": "customer.company_type", "operator": "eq", "value": "Public Limited"}
    built = client.post("/api/process-builder/build", json={"request": text, "steps": bp["steps"], "answers": bp["answers"], "mode": "execute",
                                                           "graphs": {flow["id"]: flow["spec"]["graph"]}}).json()
    assert built["status"] == "built", built

    def qualified(name, company_type):
        def run(code, **entities):
            out = client.post("/api/intents", json={"text": code, "structured": {"intent_type": code, "entities": entities}}).json()
            assert out["status"] == "executed", out
            return out["result"]

        party = run("INT-15", name=name, segment="B2B")["party_id"]
        with client.TestSessionLocal() as db:
            defn = db.query(CustomFieldDefinition).filter_by(tenant_id=tid, entity_type="party", key="company_type").one()
            custom_field_service.set_value(db, tid, "party", party, defn, company_type)
            db.commit()
        lead = run("INT-46", party_id=party, products=["Fiber 700 Bundle"])["lead_id"]
        run("INT-17", lead_id=lead, decision="approve")
        with client.TestSessionLocal() as db:
            return db.get(Party, party).party_type

    assert qualified("Big Public Co", "Public Limited") != "prospect"  # converted to an account
    assert qualified("Small Private Co", "Private Limited") == "prospect"  # the condition held it back


def test_without_a_model_a_template_brd_reads_only_its_requirement_sections(client):
    """The user's BRD (on the template): its headings, name, business function and goal are no requirements -
    they made nine 'couldn't tell' questions; only the automation line is one, and it is built with its condition."""
    import base64

    from test_process_to_map import _docx

    doc = _docx(["Business Requirements - New Process",
                 "Process name", "Convert Prospect to Account if the prospect Company type as “Public Limited”",
                 "Business function", "B2B Sales to Order",
                 "Goal", "Prospect auto conversion to Account.",
                 "Stages", "Lead Approval",
                 "Approvals", "Automation",
                 "When a lead is approved, check the Prospect “Company type” and if it is Public Limited, check the Prospect to an Account",
                 "Fields and screens", "Assignment", "Integrations and other needs"])
    client.get("/api/admin/pages")  # Company type and the other standard fields
    read = client.post("/api/process-builder/brd", json={"filename": "Process1.docx", "content_base64": base64.b64encode(doc).decode()}).json()
    out = client.post(ARCH, json={"text": read["text"], "name": read["name"], "placement": read["placement"]}).json()
    assert out["by"] == "rules" and [r["text"] for r in out["requirements"]] == [
        "When a lead is approved, check the Prospect “Company type” and if it is Public Limited, check the Prospect to an Account"]
    assert out["questions"] == [] and out["ready"], out["questions"]
    flow = out["builder"]["components"][0]
    assert flow["kind"] == "flow" and any("Company type is Public Limited" in line for line in flow["lines"]), flow
    assert out["placement"]["label"] == "B2B Sales to Order" and "Prospect auto conversion" in out["summary"]


def test_a_brd_said_only_in_its_name_is_still_designed_and_plural_fields_are_separate(client):
    """The user's next BRD: 'Add two new fields to prospect page CP1 and CP2' as the process name, every requirement
    section empty - it was refused; and a page planner made ONE field named 'Two new fields CP1 and CP2'."""
    import base64

    from test_process_to_map import _docx

    doc = _docx(["Process name", "Add two new fields to prospect page CP1 and CP2", "Business function", "B2B Sales to Order",
                 "Goal", "Two new fields to be added", "Stages", "Approvals", "Automation", "Fields and screens", "Assignment"])
    client.get("/api/admin/pages")
    read = client.post("/api/process-builder/brd", json={"filename": "Process1.docx", "content_base64": base64.b64encode(doc).decode()})
    assert read.status_code == 200, read.text
    read = read.json()
    assert read["request"] == "Add two new fields to prospect page CP1 and CP2."
    out = client.post(ARCH, json={"text": read["text"], "name": read["name"], "placement": read["placement"]}).json()
    assert out["ready"] and not out["questions"], out["questions"]
    page = out["builder"]["components"][0]
    assert page["kind"] == "page" and page["lines"] == ["Add new field CP1 (text)", "Add new field CP2 (text)"]


def test_the_brd_upload_hands_the_whole_document_to_the_architect(client):
    import base64

    out = client.post("/api/process-builder/brd", json={"filename": "deal-desk.txt", "content_base64": base64.b64encode(BRD.encode()).decode()}).json()
    assert out["text"].startswith("Enterprise Deal Desk") and "ERP" in out["text"]
