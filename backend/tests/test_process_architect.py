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


def test_the_brd_upload_hands_the_whole_document_to_the_architect(client):
    import base64

    out = client.post("/api/process-builder/brd", json={"filename": "deal-desk.txt", "content_base64": base64.b64encode(BRD.encode()).decode()}).json()
    assert out["text"].startswith("Enterprise Deal Desk") and "ERP" in out["text"]
