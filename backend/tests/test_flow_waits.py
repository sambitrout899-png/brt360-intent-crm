"""Durable flow runs: Wait and Wait for approval steps (app/orchestration/flow_runs.py)."""

import datetime as dt

from conftest import DEFAULT_TEST_ADMIN_EMAIL
from test_access_guards import _as, _user

from app.models.flows import FlowRun
from app.orchestration import flow_runs
from app.timeutils import utcnow


def _box(node_id, message):
    return {"id": node_id, "type": "notify", "label": message[:20],
            "config": {"channel": "popup", "audience": DEFAULT_TEST_ADMIN_EMAIL, "message": message}}


def _flow(client, middle, after, edges_after, timing="after"):
    """start -> "before" box -> middle (a wait) -> after..."""
    graph = {"nodes": [{"id": "s", "type": "start", "label": "Start", "config": {}}, _box("b", "before {{record.name}}"), middle, *after],
             "edges": [{"id": "e1", "source": "s", "target": "b", "label": ""}, {"id": "e2", "source": "b", "target": middle["id"], "label": ""},
                       *edges_after]}
    flow = client.post("/api/admin/flows", json={"name": "Waiting flow", "entity_type": "party", "trigger_type": "intent",
                                                  "trigger_intent_type": "INT-15", "timing": timing, "graph": graph}).json()
    return flow, client.post(f"/api/admin/flows/{flow['id']}/activate")


def _prospect(client, name):
    done = client.post("/api/intents", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": name}}}).json()
    assert done["status"] == "executed", done
    return done


def _boxes(client):
    return [b["message"] for b in client.get("/api/notices/popups").json()]


def _run(client):
    db = client.TestSessionLocal()
    try:
        return db.query(FlowRun).order_by(FlowRun.id.desc()).first()
    finally:
        db.close()


def test_a_wait_pauses_the_run_and_the_worker_continues_it(client):
    wait = {"id": "w", "type": "wait", "label": "Two hours", "config": {"amount": "2", "unit": "hours"}}
    flow, activated = _flow(client, wait, [_box("a", "after {{record.name}}"), {"id": "end", "type": "end", "label": "End", "config": {}}],
                            [{"id": "e3", "source": "w", "target": "a", "label": ""}, {"id": "e4", "source": "a", "target": "end", "label": ""}])
    assert activated.status_code == 200, activated.text
    _prospect(client, "Patient Co")
    assert _boxes(client) == ["before Patient Co"]  # the part before the wait committed with the intent
    run = _run(client)
    assert run.status == "waiting" and run.wait_kind == "timer"
    assert dt.timedelta(minutes=119) < run.resume_at - utcnow() <= dt.timedelta(hours=2)

    # editing the flow meanwhile doesn't change the waiting run: it keeps the graph it started with
    bare = {"nodes": [{"id": "s", "type": "start", "label": "Start", "config": {}}, {"id": "end", "type": "end", "label": "End", "config": {}}],
            "edges": [{"id": "e1", "source": "s", "target": "end", "label": ""}]}
    assert client.put(f"/api/admin/flows/{flow['id']}", json={**flow, "graph": bare}).status_code == 200

    assert flow_runs.run_due(client.TestSessionLocal) == 0  # not yet
    assert flow_runs.run_due(client.TestSessionLocal, now=utcnow() + dt.timedelta(hours=3)) == 1
    assert _boxes(client) == ["before Patient Co", "after Patient Co"]
    assert _run(client).status == "completed"
    assert flow_runs.run_due(client.TestSessionLocal, now=utcnow() + dt.timedelta(hours=4)) == 0  # once


def _approval_flow(client, approver, expire_days=""):
    gate = {"id": "g", "type": "wait_approval", "label": "Manager OK",
            "config": {"approver": approver, "subject": "Approve {{record.name}}", "message": "New prospect", "expire_days": expire_days}}
    ok, no = _box("ok", "approved by {{approval.by}}: {{approval.note}}"), _box("no", "rejected: {{approval.note}}")
    end = {"id": "end", "type": "end", "label": "End", "config": {}}
    return _flow(client, gate, [ok, no, end], [
        {"id": "e3", "source": "g", "target": "ok", "label": "approved"}, {"id": "e4", "source": "g", "target": "no", "label": "rejected"},
        {"id": "e5", "source": "ok", "target": "end", "label": ""}, {"id": "e6", "source": "no", "target": "end", "label": ""}])


def test_an_approval_waits_for_the_approver_and_goes_their_way(client):
    position = client.post("/api/admin/positions", json={"name": "Deal Desk", "data_scope": "org"}).json()["id"]
    approver, bystander = _user(client, "desk-approver"), _user(client, "bystander")
    client.put(f"/api/admin/users/{approver}/access", json={"role_ids": [], "position_ids": [position]})
    flow, activated = _approval_flow(client, "Deal Desk")
    assert activated.status_code == 200, activated.text
    assert not [w for w in flow["warnings"] if "approval" in w] and not flow["corrections"]  # {{approval.*}} known to the Studio
    assert "{{approval.note}}" in next(n for n in flow["graph"]["nodes"] if n["id"] == "ok")["config"]["message"]
    _prospect(client, "Gated Co")

    mine = client.get("/api/flow-approvals", headers=_as(approver)).json()
    assert [(r["subject"], r["wait"]["approver"]) for r in mine] == [("Approve Gated Co", "Deal Desk")]
    assert client.get("/api/flow-approvals", headers=_as(bystander)).json() == []
    run_id = mine[0]["id"]
    assert client.post(f"/api/flow-approvals/{run_id}/decide", headers=_as(bystander), json={"approve": True}).status_code == 403

    done = client.post(f"/api/flow-approvals/{run_id}/decide", headers=_as(approver), json={"approve": True, "note": "fine by me"}).json()
    assert done["status"] == "completed" and done["decision"] == "approved"
    assert _boxes(client)[-1] == "approved by desk-approver: fine by me"
    assert client.post(f"/api/flow-approvals/{run_id}/decide", headers=_as(approver), json={"approve": False}).status_code == 400  # once
    assert client.get("/api/flow-approvals", headers=_as(approver)).json() == []

    _prospect(client, "Turned Down Co")
    run_id = client.get("/api/flow-approvals", headers=_as(approver)).json()[0]["id"]
    client.post(f"/api/flow-approvals/{run_id}/decide", headers=_as(approver), json={"approve": False, "note": "no budget"})
    assert _boxes(client)[-1] == "rejected: no budget"


def test_an_unanswered_approval_expires(client):
    _approval_flow(client, DEFAULT_TEST_ADMIN_EMAIL, expire_days="1")
    _prospect(client, "Forgotten Co")
    assert flow_runs.run_due(client.TestSessionLocal, now=utcnow() + dt.timedelta(hours=23)) == 0
    assert flow_runs.run_due(client.TestSessionLocal, now=utcnow() + dt.timedelta(days=2)) == 1
    run = _run(client)
    assert (run.status, run.decision) == ("completed", "expired")  # no "expired" output linked: the flow just ends
    assert client.get("/api/flow-approvals").json() == []


def test_what_fails_after_a_wait_is_undone_and_recorded(client):
    wait = {"id": "w", "type": "wait", "label": "Later", "config": {"amount": "5", "unit": "minutes"}}
    boom = {"id": "x", "type": "throw", "label": "Boom", "config": {"code": "LATE_FAILURE", "message": "it went wrong later"}}
    _flow(client, wait, [_box("a", "after"), boom],
          [{"id": "e3", "source": "w", "target": "a", "label": ""}, {"id": "e4", "source": "a", "target": "x", "label": ""}])
    _prospect(client, "Fragile Co")
    flow_runs.run_due(client.TestSessionLocal, now=utcnow() + dt.timedelta(minutes=10))
    run = _run(client)
    assert run.status == "failed"
    assert _boxes(client) == ["before Fragile Co"]  # the box after the wait was undone with the failure
    failed = client.get("/api/admin/flow-runs", params={"status": "failed"}).json()
    assert failed[0]["error"]["code"] == "LATE_FAILURE"


def test_an_admin_can_cancel_a_waiting_run(client):
    wait = {"id": "w", "type": "wait", "label": "A day", "config": {"amount": "1", "unit": "days"}}
    _flow(client, wait, [_box("a", "after")], [{"id": "e3", "source": "w", "target": "a", "label": ""}])
    _prospect(client, "Cancelled Co")
    waiting = client.get("/api/admin/flow-runs").json()
    assert [r["wait"]["kind"] for r in waiting] == ["timer"]
    assert client.post(f"/api/admin/flow-runs/{waiting[0]['id']}/cancel").json()["status"] == "cancelled"
    assert flow_runs.run_due(client.TestSessionLocal, now=utcnow() + dt.timedelta(days=2)) == 0
    assert _boxes(client) == ["before Cancelled Co"]


def test_a_flow_that_must_answer_at_once_cannot_wait(client):
    wait = {"id": "w", "type": "wait", "label": "Hold on", "config": {"amount": "1", "unit": "hours"}}
    _, activated = _flow(client, wait, [], [], timing="before")
    assert activated.status_code == 400 and "can't wait" in activated.text
    _, bad = _flow(client, {"id": "w", "type": "wait", "label": "Forever", "config": {"amount": "999", "unit": "days"}}, [], [])
    assert bad.status_code == 400 and "365 days" in bad.text
