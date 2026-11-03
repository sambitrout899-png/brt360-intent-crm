"""System test suite (orchestration/system_tests.py): cases generated from the tenant's own system - intents,
custom record types, flows, page rules - run in the background through the real pipeline, every case rolled
back, with step-by-step evidence and an HTML report."""

from tests.test_custom_record_types import _party
from tests.test_record_type_builder import SURVEY_STATES, _field, _type

from app.models.custom_records import CustomRecord
from app.models.intent_log import IntentExecutionLog


def _count(client, model):
    db = client.TestSessionLocal()
    try:
        return db.query(model).count()
    finally:
        db.close()


def _setup(client):
    _party(client, "Acme Towers")
    _type(client, "Site Survey", "SS", parent_entity_type="party", parent_required=True)
    _field(client, "x_site_survey", "survey_date", "Survey date", "date", required=True)
    _field(client, "x_site_survey", "floors", "Floors", "number")
    _field(client, "x_site_survey", "report", "Report", "textarea")
    assert client.put("/api/admin/state-models/x_site_survey", json=SURVEY_STATES).status_code == 200
    assert client.post("/api/admin/pages/rules", json={"entity_type": "x_site_survey", "text": "if State is completed then Report is mandatory"}).status_code == 200
    assert client.post("/api/admin/pages/rules", json={"entity_type": "x_site_survey", "text": "Floors must be at least 1"}).status_code == 200
    graph = {"nodes": [{"id": "s", "type": "start", "config": {}}, {"id": "n", "type": "set_field", "config": {"field": "record.floors", "value": "2"}},
                       {"id": "e", "type": "end", "config": {}}],
             "edges": [{"id": "a", "source": "s", "target": "n", "label": ""}, {"id": "b", "source": "n", "target": "e", "label": ""}]}
    flow = client.post("/api/admin/flows", json={"name": "Two floors", "entity_type": "x_site_survey", "trigger_type": "state_enter",
                                                 "trigger_state": "in_progress", "graph": graph}).json()
    assert client.post(f"/api/admin/flows/{flow['id']}/activate").status_code == 200


def test_a_suite_is_generated_from_the_tenants_own_system(client):
    _setup(client)
    made = client.post("/api/system-tests/suites", json={"name": "Before go-live"})
    assert made.status_code == 201, made.text
    suite = made.json()
    scope = suite["scope"]
    assert scope["record_types"] == ["Site Survey"] and scope["flows"] == 1 and scope["intents"] > 100
    assert {"understanding", "required_fields", "end_to_end", "lifecycle", "mandatory", "type_check", "rule", "flow"} <= set(scope["by_kind"])
    cases = client.get(f"/api/system-tests/suites/{suite['id']}", params={"area": "Custom: Site Survey"}).json()["cases"]
    titles = [c["title"] for c in cases]
    assert "Site Survey - created, changed, moved along its states and deleted" in titles
    assert "Site Survey - refused without Survey date" in titles and "Site Survey - refused without its Customer" in titles
    assert any(t.startswith("Site Survey - rule holds:") and "report" in t.lower() for t in titles)
    lifecycle = next(c for c in cases if c["kind"] == "lifecycle")
    assert lifecycle["steps"][0].startswith("Create a Site Survey") and any("completed" in s for s in lifecycle["steps"])
    csv = client.get(f"/api/system-tests/suites/{suite['id']}/cases.csv").text
    assert csv.splitlines()[0].startswith("code,area,kind") and "TC-0001" in csv


def test_a_run_goes_through_the_real_platform_leaves_evidence_and_keeps_nothing(client):
    _setup(client)
    suite = client.post("/api/system-tests/suites", json={}).json()
    records, logs = _count(client, CustomRecord), _count(client, IntentExecutionLog)
    started = client.post(f"/api/system-tests/suites/{suite['id']}/runs", params={"wait": True},
                          json={"areas": ["Custom: Site Survey", "Flows"]})
    assert started.status_code == 201, started.text
    run = client.get(f"/api/system-tests/runs/{started.json()['id']}").json()
    assert run["status"] in ("passed", "failed") and run["done"] == run["total"] > 5
    by_title = {r["case"]["title"]: r for r in run["results"]}
    failing = {t: (r["status"], r["message"]) for t, r in by_title.items() if r["status"] in ("failed", "error")}
    assert not failing, failing
    # no site survey exists to dry-run the flow on: skipped with the reason, not failed
    flow = next(r for t, r in by_title.items() if t.startswith("Flow “Two floors”"))
    assert flow["status"] == "skipped" and "Site Survey" in flow["message"]

    life = by_title["Site Survey - created, changed, moved along its states and deleted"]
    first = life["evidence"][0]
    assert first["request"]["intent"] == "INT-151" and first["response"]["status"] == "executed"
    assert any(k.startswith("create custom_records") for k in first["writes"])  # what it would have written
    assert all(c["passed"] for s in life["evidence"] for c in s["checks"])
    rule = next(r for t, r in by_title.items() if "report" in t.lower() and t.startswith("Site Survey - rule holds"))
    assert rule["evidence"][-1]["response"]["status"] == "failed" and "Report is mandatory" in rule["evidence"][-1]["response"]["error"]

    # nothing was kept - no record, no audit entry
    assert _count(client, CustomRecord) == records and _count(client, IntentExecutionLog) == logs
    report = client.get(f"/api/system-tests/runs/{run['id']}/report.html").text
    assert run["number"] in report and "rolled back" in report and "Site Survey" in report
    # one run at a time; a finished one can be looked up again
    assert any(r["id"] == run["id"] for r in client.get("/api/system-tests/runs").json())


def test_intent_cases_run_and_report_what_they_need(client):
    suite = client.post("/api/system-tests/suites", json={}).json()
    run = client.post(f"/api/system-tests/suites/{suite['id']}/runs", params={"wait": True}, json={"kinds": ["required_fields"]}).json()
    detail = client.get(f"/api/system-tests/runs/{run['id']}").json()
    assert detail["total"] > 20 and detail["done"] == detail["total"]
    assert detail["passed"] >= detail["total"] * 0.8, [(r["case"]["title"], r["message"]) for r in detail["results"] if r["status"] != "passed"][:10]
    # a case can be switched off, and the next run leaves it out
    case = detail["results"][0]["case"]
    assert client.patch(f"/api/system-tests/cases/{case['id']}", json={"is_active": False}).json()["is_active"] is False
    again = client.post(f"/api/system-tests/suites/{suite['id']}/runs", params={"wait": True}, json={"kinds": ["required_fields"]}).json()
    assert again["total"] == detail["total"] - 1
