"""A record's buttons follow its state (one list for Customer 360 and the
record panel), the customer journey never goes backwards, and users are told
about approvals waiting for them - plus the running bar of key information
and administrators' broadcasts."""

from test_sales_to_order import _feasible_opportunity, _launch, _staff, _user, run

from app.models.crm import Opportunity


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _party_of(client, opp_id):
    db = client.TestSessionLocal()
    try:
        return db.get(Opportunity, opp_id).lead.party_id
    finally:
        db.close()


def _intents(actions):
    return {a["intent"] for a in actions}


def test_buttons_follow_the_state(client):
    _launch(client)
    opp = _feasible_opportunity(client, "Buttons Co")
    party = _party_of(client, opp)
    actions = client.get(f"/api/records/actions/party/{party}").json()
    feasible = _intents(actions[f"opportunity:{opp}"])
    assert "INT-05" in feasible  # quote it
    assert not feasible & {"INT-18", "INT-38"}  # feasibility already passed - no feasibility buttons
    lead = next(k for k in actions if k.startswith("lead:"))
    assert not _intents(actions[lead]) & {"INT-17", "INT-16"}  # the lead is qualified

    quote = run(client, "INT-05", opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=5)["result"]["quote_id"]
    draft = client.get(f"/api/records/actions/party/{party}").json()[f"quote:{quote}"]
    assert any(a["label"] == "Submit for approval" and a["intent"] == "INT-06" for a in draft)
    assert run(client, "INT-06", quote_id=quote, decision="approve")["result"]["status"] == "approved"
    approved = client.get(f"/api/records/actions/party/{party}").json()[f"quote:{quote}"]
    assert "INT-06" not in _intents(approved) and {"INT-22", "INT-39", "INT-07"} <= _intents(approved)
    # the record panel shows the very same buttons
    assert client.get(f"/api/records/quote/{quote}").json()["actions"] == approved


def test_buttons_for_a_list_of_records(client):
    _launch(client)
    opp = _feasible_opportunity(client, "List Co")
    quote = run(client, "INT-05", opportunity_id=opp, offering_name="Fiber 900")["result"]["quote_id"]
    out = client.post("/api/records/actions", json={"refs": [f"quote:{quote}", f"opportunity:{opp}", "quote:99999", "nonsense:1"]}).json()
    assert set(out) == {f"quote:{quote}", f"opportunity:{opp}"}  # unknown records are left out
    assert "INT-05" not in _intents(out[f"opportunity:{opp}"])  # already quoted


def test_approval_buttons_only_for_the_approver(client):
    _launch(client)
    rep, vp = _user(client, "rep@buttons.test"), _user(client, "vp@buttons.test")
    _staff(client, "VP Sales", vp)
    opp = _feasible_opportunity(client, "Approver Co")
    quote = run(client, "INT-05", rep, opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=25,
                discount_justification="x")["result"]["quote_id"]
    run(client, "INT-06", rep, quote_id=quote, decision="approve")
    rep_view = client.get(f"/api/records/quote/{quote}", headers=_as(rep)).json()["actions"]
    vp_view = client.get(f"/api/records/quote/{quote}", headers=_as(vp)).json()["actions"]
    assert not any(a["entities"].get("decision") for a in rep_view)
    assert {a["entities"].get("decision") for a in vp_view if a["intent"] in ("INT-06", "INT-36")} == {"approve", "reject"}


def test_the_journey_never_goes_backwards(client):
    _launch(client)
    opp = _feasible_opportunity(client, "Journey Co")
    party = _party_of(client, opp)
    quote = run(client, "INT-05", opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=5)["result"]["quote_id"]
    run(client, "INT-06", quote_id=quote, decision="approve")
    assert run(client, "INT-30", quote_id=quote, discount=3)["status"] == "executed"  # a revision: new draft, old superseded
    steps = {s["label"]: s["reached"] for s in client.get(f"/api/customers/{party}/overview").json()["journey"]["steps"]}
    assert steps["Approved"] is True


def test_notices_tell_the_approver_and_the_requester(client):
    _launch(client)
    rep, vp = _user(client, "rep@notice.test"), _user(client, "vp@notice.test")
    _staff(client, "VP Sales", vp)
    opp = _feasible_opportunity(client, "Notice Co")
    party = _party_of(client, opp)
    quote = run(client, "INT-05", rep, opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=25,
                discount_justification="x")["result"]["quote_id"]
    run(client, "INT-06", rep, quote_id=quote, decision="approve")

    vp_notices = client.get("/api/notices", headers=_as(vp)).json()
    assert vp_notices["approvals_waiting"] == 1 and vp_notices["approvals"][0]["entity_id"] == quote
    assert vp_notices["approvals"][0]["requested_by"] == "rep"
    assert any(t["kind"] == "approvals" for t in vp_notices["ticker"])
    assert client.get("/api/notices", headers=_as(rep)).json()["approvals_waiting"] == 0
    on_customer = client.get(f"/api/notices?place=customer&party_id={party}").json()["ticker"]
    assert any("waiting for VP Sales" in t["text"] for t in on_customer)

    run(client, "INT-06", vp, quote_id=quote, decision="approve", reasoning="ok")
    told = client.get("/api/notices", headers=_as(rep)).json()["ticker"]
    assert any(t["kind"] == "decision" and "approved by vp" in t["text"] for t in told)


def test_broadcasts(client):
    added = client.post("/api/admin/broadcasts", json={"text": "Fibre maintenance tonight 22:00", "level": "warning", "place": "dashboard"})
    assert added.status_code == 201
    assert client.post("/api/admin/broadcasts", json={"text": "x", "level": "loud"}).status_code == 400
    user = _user(client, "reader@notice.test")
    assert any(t["text"] == "Fibre maintenance tonight 22:00" for t in client.get("/api/notices", headers=_as(user)).json()["ticker"])
    assert not any(t["kind"] == "broadcast" for t in client.get("/api/notices?place=customer", headers=_as(user)).json()["ticker"])
    assert client.post("/api/admin/broadcasts", json={"text": "old", "ends_at": "2020-01-01"}).status_code == 201
    assert not any(t["text"] == "old" for t in client.get("/api/notices").json()["ticker"])  # expired
    assert client.post("/api/admin/broadcasts", json={"text": "nope"}, headers=_as(user)).status_code == 403
    assert client.delete(f"/api/admin/broadcasts/{added.json()['id']}").status_code == 204
    assert not any(t["kind"] == "broadcast" and "Fibre" in t["text"] for t in client.get("/api/notices").json()["ticker"])
