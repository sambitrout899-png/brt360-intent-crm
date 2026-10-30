"""Functional use cases on top of BRT's Functional Map: the use case template
(Word / RTF) read into structured use cases, every step and rule mapped -
already in BRT, a delta on its process, a customization, a new requirement or
a process gap - reviewed and edited, committed into a map version, and
turned into manual and automation test cases."""

import base64
import io
import zipfile

from app.orchestration import process_brd, use_cases

UC = "/api/process-builder/use-cases"
MAP = "/api/admin/function-map"


def _docx(paragraphs):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return out.getvalue()


def _upload(client, filename, data):
    return client.post(UC, json={"filename": filename, "content_base64": base64.b64encode(data).decode()})


DOC = [
    "BRT 360 Use Cases - Enterprise deals",
    "Use case: Key account discount",
    "Business function", "B2B Sales to Order",
    "Actor", "Sales agent",
    "Preconditions", "The customer is an enterprise account",
    "Main flow",
    "1. Sales agent checks feasibility",
    "2. Sales agent generates the quote",
    "3. Sales agent collects the landlord's wayleave consent",
    "Alternate flows", "If the customer declines, the sales agent closes the opportunity",
    "Business rules", "Quotes with discount above 25% need approval from the Sales Director",
    "Data and screens", "On the quote page add a mandatory field Discount reason",
    "Integrations", "Push approved orders to the ERP every night",
    "Postconditions", "The quote is approved and sent",
    "Acceptance criteria", "A quote with a 30% discount cannot be sent before approval",
]


def test_the_template_round_trips_and_its_example_alone_is_refused(client):
    template = client.get("/api/process-builder/use-case-template")
    assert template.status_code == 200 and template.content[:2] == b"PK"
    untouched = _upload(client, "BRT360-use-case-template.docx", template.content)
    assert untouched.status_code == 422 and "only its example" in untouched.json()["detail"]
    # the template's own sections read back: the worked example is a use case like any other
    cases = use_cases.parse_template(process_brd.read("t.docx", template.content))
    example = cases[0]
    assert example["title"] == "Enterprise discount review" and example["business_function"] == "B2B Sales to Order"
    assert len(example["main_flow"]) == 6 and example["acceptance"] and example["integrations"] == ["Push approved orders to the ERP"]


def test_use_cases_are_mapped_on_the_functional_map(client):
    out = _upload(client, "deals.docx", _docx(DOC)).json()
    assert out["name"] == "Enterprise deals" and out["read_by"] == "template" and out["status"] == "draft"
    case = out["use_cases"][0]
    assert case["id"] == "UC-01" and case["actor"] == "Sales agent"
    assert case["main_flow"][0]["text"] == "Sales agent checks feasibility"  # the list numbers are gone
    m = case["mapping"]
    assert m["process"] == {"mode": "append", "key": "b2b_sto", "label": "B2B Sales to Order"}
    feas, quote, survey = m["steps"]
    assert feas["status"] == "brt" and feas["intent"] == "INT-18"  # BRT's process already does it
    assert quote["intent"] and quote["status"] in ("brt", "delta")
    assert survey["status"] == "gap" and "New requirement" in survey["how"]  # one new step: a process gap
    lines = {line["field"]: line for line in m["lines"]}
    assert lines["business_rules"]["status"] == "configure" and lines["data"]["status"] == "configure"
    assert lines["integrations"]["status"] == "gap"
    assert {g["text"] for g in out["gaps"]} >= {"Sales agent collects the landlord's wayleave consent", "Push approved orders to the ERP every night"}
    assert out["summary"]["covered"] >= 1 and out["summary"]["configure"] == 2 and out["summary"]["gap"] == 2


def test_reviewed_use_cases_are_mapped_again_and_two_new_steps_become_new_actions(client):
    out = _upload(client, "deals.docx", _docx(DOC)).json()
    case = out["use_cases"][0]
    case["main_flow"].append({"text": "Sales agent files the wayleave with the council", "intent": None, "pinned": False})
    case["main_flow"][1] = {**case["main_flow"][1], "intent": "INT-05", "pinned": True}  # the reviewer's choice is kept
    saved = client.put(f"{UC}/{out['id']}", json={"name": "Enterprise deals v2", "use_cases": [case]}).json()
    steps = saved["use_cases"][0]["mapping"]["steps"]
    assert saved["name"] == "Enterprise deals v2" and steps[1]["intent"] == "INT-05"
    assert [s["status"] for s in steps[2:]] == ["new", "new"] and "new action" in steps[2]["how"]
    design = use_cases.designs(None, 0, "x", saved["use_cases"])[0]
    journey = next(p for p in design["pieces"] if p["kind"] == "journey")
    assert journey["text"].startswith("Key account discount process: Collects the landlord")
    assert client.get(UC).json()[0]["id"] == out["id"]


def test_commit_puts_the_use_cases_into_a_draft_map_version_then_tests_are_written(client):
    out = _upload(client, "deals.docx", _docx(DOC)).json()
    assert client.post(f"{UC}/{out['id']}/tests").status_code == 409  # not before it is implemented
    done = client.post(f"{UC}/{out['id']}/commit", json={}).json()
    assert done["committed"] and done["status"] == "committed", done
    version = client.get(f"{MAP}/versions/{done['version']['id']}").json()
    assert version["version"]["status"] == "draft" and version["builds"] and version["builds"][0]["status"] == "pending"
    assert "INT-18" in next(p for p in version["processes"] if p["key"] == "b2b_sto")["steps"]

    tested = client.post(f"{UC}/{out['id']}/tests").json()
    tests = tested["test_cases"]
    kinds = {t["kind"] for t in tests}
    assert {"happy path", "alternate", "business rule", "acceptance", "gap"} <= kinds
    at = {t["data"] for t in tests if t["kind"] == "business rule"}
    assert at == {"value = 26%", "value = 25%"}  # either side of the limit
    happy = next(t for t in tests if t["kind"] == "happy path")
    assert happy["automation"] == "automated" and "INT-18" in happy["intents"] and happy["id"] == "TC-UC-01-01"

    doc = client.get(f"{UC}/{out['id']}/tests/manual.docx")
    assert doc.status_code == 200 and doc.content[:2] == b"PK"
    sheet = client.get(f"{UC}/{out['id']}/tests/manual.csv").text
    assert "TC-UC-01-01" in sheet and "Expected result" in sheet
    module = client.get(f"{UC}/{out['id']}/tests/automation.py").text
    compile(module, "generated.py", "exec")  # a valid Python module
    assert "/api/intents/rehearse" in module and f"VERSION_ID = {done['version']['id']}" in module

    # edited after commit: back to draft, its tests marked out of date
    again = client.put(f"{UC}/{out['id']}", json={"name": out["name"], "use_cases": tested["use_cases"]}).json()
    assert again["status"] == "draft" and again["tests"]["stale"]


def test_the_preview_shows_what_commit_builds_and_an_open_question_stops_the_commit(client, monkeypatch):
    from app.orchestration import process_architect

    doc = _docx(["Use case: Lead qualification sign-off", "Business function", "Lead to Quote", "Main flow", "Sales agent qualifies the lead",
                 "Business rules", "Leads need approval from the Chief Happiness Officer before they are qualified"])
    out = _upload(client, "leads.docx", doc).json()
    plan = client.post(f"{UC}/{out['id']}/preview", json={}).json()["designs"]
    assert plan[0]["process"] == "Lead to Quote" and plan[0]["changes"]
    assert plan[0]["components"][0]["kind"] == "approval"
    assert client.get(f"{UC}/{out['id']}").json()["status"] == "draft"  # a preview changes nothing

    real = process_architect.verify

    def asks(db, tenant_id, design, answers=None):  # a question the builder can't answer itself
        done = real(db, tenant_id, design, answers)
        if not answers:
            done["questions"] = [{"id": "who", "prompt": "Who approves?", "options": ["Sales Director"], "values": ["Sales Director"],
                                  "default": None, "builder": True}]
        return done

    monkeypatch.setattr(process_architect, "verify", asks)
    first = client.post(f"{UC}/{out['id']}/commit", json={}).json()
    assert not first["committed"] and first["set"]["status"] == "draft"
    assert first["questions"][0]["group"] == "G1" and first["questions"][0]["process"] == "Lead to Quote"
    done = client.post(f"{UC}/{out['id']}/commit", json={"answers": {"G1": {"who": "Sales Director"}}}).json()
    assert done["committed"] and done["status"] == "committed", done

def test_rtf_and_free_text_are_read_and_bad_files_refused(client, monkeypatch):
    rtf = (r"{\rtf1\ansi{\fonttbl{\f0 Calibri;}}{\*\generator Word;}\f0 Use case: Caf\'e9 install\par Main flow\par "
           r"1. Sales agent checks feasibility\par Business rules\par Quotes with discount above 10% need approval from the Sales Director\par}")
    assert process_brd.rtf_text(rtf.encode()).splitlines()[0] == "Use case: Café install"
    out = _upload(client, "cafe.rtf", rtf.encode()).json()
    assert out["use_cases"][0]["title"] == "Café install" and out["use_cases"][0]["mapping"]["steps"][0]["intent"] == "INT-18"

    monkeypatch.setattr(use_cases, "_ai_structure", lambda db, tid, text: None)  # no model: sentences become the main flow
    free = _upload(client, "notes.txt", b"The sales agent checks feasibility. Push the order to the ERP every night.").json()
    assert free["read_by"] == "text" and free["use_cases"][0]["integrations"] == ["Push the order to the ERP every night"]

    assert _upload(client, "old.doc", b"\xd0\xcf\x11\xe0").status_code == 422
    assert _upload(client, "fake.rtf", b"hello").status_code == 422


def test_the_model_restructures_a_free_document(client, monkeypatch):
    monkeypatch.setattr(use_cases, "_ai_structure", lambda db, tid, text: {"name": "Deal desk", "use_cases": [{
        "id": "UC-01", "title": "Deal desk", "business_function": "B2B Sales to Order", "actor": "Sales agent",
        "main_flow": [{"text": "Check the deal can be delivered", "intent": "INT-18"}, {"text": "Made-up step", "intent": "INT-999"}],
        "business_rules": [], "acceptance": ["Deals are checked first"]}]})
    out = _upload(client, "desk.txt", b"Our deal desk checks every deal can be delivered first.").json()
    steps = out["use_cases"][0]["mapping"]["steps"]
    assert out["read_by"] == "ai" and out["name"] == "Deal desk"
    assert steps[0] == {**steps[0], "intent": "INT-18", "status": "brt"}  # the model's code, checked
    assert steps[1]["intent"] != "INT-999"  # a code that doesn't exist is matched afresh, never taken


def test_only_the_root_administrator_activates_on_commit(client):
    from tests.test_change_control import _admin, _as

    out = _upload(client, "deals.docx", _docx(DOC)).json()
    other = _admin(client, "Second Admin")
    refused = client.post(f"{UC}/{out['id']}/commit", json={"activate": True}, headers=_as(other))
    assert refused.status_code == 403 and client.get(f"{UC}/{out['id']}").json()["status"] == "draft"
    done = client.post(f"{UC}/{out['id']}/commit", json={"activate": True}).json()  # the root administrator
    assert done["committed"] and done["status"] == "live" and not done["activation_error"], done
    assert client.get(f"{MAP}/versions").json()["active_id"] == done["version"]["id"]