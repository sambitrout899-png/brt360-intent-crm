"""Every user holds a role and a position ("Require role and position"),
approvals follow the matrix to the named approver - who then owns the
record until it is decided - nobody approves their own request ("strict
approvals"), Order Management and billing approvals use the matrix too, and
every decision is in the approval log with who asked and who decided."""

from test_communications import _live_customer
from test_order_management import _order_from, _quote, _setup
from test_sales_to_order import _feasible_opportunity, _launch, _staff, _user, run

from app.models.auth import UserRole


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _policy(client, **switches):
    resp = client.put("/api/admin/access-policy", json=switches)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _owner(client, entity_type, entity_id):
    meta = client.get(f"/api/records/{entity_type}/{entity_id}").json()
    return (meta.get("owner_user") or {}).get("name"), (meta.get("owner_position") or {}).get("name")


def test_every_user_needs_a_role_and_a_position(client):
    assert client.get("/api/admin/access-policy").json() == {"require_role_and_position": False, "strict_approvals": False}
    sales = client.post("/api/admin/roles", json={"name": "Sales", "pages": ["pipeline", "intent"]}).json()["id"]
    rep_pos = client.post("/api/admin/positions", json={"name": "Rep", "data_scope": "own"}).json()["id"]
    loner = _user(client, "loner@policy.test")
    other = run(client, "INT-04", None, company_name="Someone Else Co", source="referral", region="North")
    assert other["status"] == "executed", other["rationale"]

    # off (as deployed): no role -> default pages, no position -> the whole organization
    me = client.get("/api/access/me", headers=_as(loner)).json()
    assert "pipeline" in me["pages"] and me["max_scope"] == "org"
    assert client.get("/api/leads", headers=_as(loner)).json()
    assert client.put(f"/api/admin/users/{loner}/access", json={"role_ids": [], "position_ids": []}).status_code == 200

    _policy(client, require_role_and_position=True)
    me = client.get("/api/access/me", headers=_as(loner)).json()
    assert me["pages"] == [] and me["max_scope"] == "own"
    assert client.get("/api/leads", headers=_as(loner)).json() == []  # not the whole organization any more
    assert client.get("/api/intents/catalog", headers=_as(loner)).json()["intents"] == []
    ran = run(client, "INT-04", loner, company_name="Blocked Co", source="referral", region="North")
    assert ran["status"] == "failed" and "don't have a role" in ran["rationale"]

    refused = client.put(f"/api/admin/users/{loner}/access", json={"role_ids": [sales], "position_ids": []})
    assert refused.status_code == 400 and "a position" in refused.json()["detail"]
    refused = client.put(f"/api/admin/users/{loner}/access", json={"role_ids": [], "position_ids": [rep_pos]})
    assert refused.status_code == 400 and "a role" in refused.json()["detail"]
    assert client.put(f"/api/admin/users/{loner}/access", json={"role_ids": [sales], "position_ids": [rep_pos]}).status_code == 200
    me = client.get("/api/access/me", headers=_as(loner)).json()
    assert me["pages"] == ["pipeline", "intent"] and me["active_position_id"] == rep_pos
    # administrators keep their defaults
    boss = _user(client, "boss@policy.test", role=UserRole.admin)
    assert client.put(f"/api/admin/users/{boss}/access", json={"role_ids": [], "position_ids": []}).status_code == 200
    assert "admin" in client.get("/api/access/me", headers=_as(boss)).json()["pages"]
    assert client.put("/api/admin/access-policy", json={"nonsense": True}).status_code == 400


def test_the_record_goes_to_the_approver_and_every_decision_is_logged(client):
    _launch(client)
    rep, vp = _user(client, "rep@matrix.test"), _user(client, "vp@matrix.test")
    _staff(client, "VP Sales", vp)
    opp = _feasible_opportunity(client, "Matrix Co")
    quote = run(client, "INT-05", rep, opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=25,
                discount_justification="strategic")["result"]["quote_id"]
    submitted = run(client, "INT-06", rep, quote_id=quote, decision="approve")
    assert submitted["result"]["status"] == "pending_approval" and submitted["result"]["waiting_for"] == "VP Sales"
    assert _owner(client, "quote", quote) == ("vp", "VP Sales")  # in the approver's hands meanwhile

    decided = run(client, "INT-06", vp, quote_id=quote, decision="approve", reasoning="strategic account")
    assert decided["result"]["status"] == "approved"
    assert _owner(client, "quote", quote) == ("rep", None)  # back to the requester

    history = client.get(f"/api/approvals/quote/{quote}").json()[0]
    assert history["requested_by"] == "rep" and history["steps"][0]["decided_by"] == "vp"
    log = client.get("/api/approvals/log").json()
    entry = next(x for x in log if x["entity_type"] == "quote" and x["entity_id"] == quote)
    assert entry["decided_by"] == "vp" and entry["requested_by"] == "rep" and entry["decision"] == "approved"
    assert entry["reasoning"] == "strategic account" and entry["tier"] == "VP Sales"
    assert client.get("/api/approvals/log", headers=_as(rep)).status_code == 403  # administrators only


def test_strict_approvals_nobody_approves_their_own_request(client):
    _launch(client)
    rep, peer, vp = _user(client, "rep@strict.test"), _user(client, "peer@strict.test"), _user(client, "vp@strict.test")
    _staff(client, "VP Sales", vp)

    # off: a tier nobody holds is open to anyone - even the requester
    opp = _feasible_opportunity(client, "Loose Co")
    q = run(client, "INT-05", rep, opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=15,
            discount_justification="x")["result"]["quote_id"]
    assert run(client, "INT-06", rep, quote_id=q, decision="approve")["result"]["status"] == "approved"

    _policy(client, strict_approvals=True)
    opp = _feasible_opportunity(client, "Strict Co")
    q = run(client, "INT-05", rep, opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=15,
            discount_justification="x")["result"]["quote_id"]
    submitted = run(client, "INT-06", rep, quote_id=q, decision="approve")
    assert submitted["result"]["status"] == "pending_approval"  # submitted, not self-approved
    own = run(client, "INT-06", rep, quote_id=q, decision="approve")
    assert own["status"] == "failed" and "You asked for this approval" in own["rationale"]
    stranger = run(client, "INT-06", peer, quote_id=q, decision="approve")
    assert stranger["status"] == "failed" and "administrator" in stranger["rationale"]
    assert run(client, "INT-06", None, quote_id=q, decision="approve")["result"]["status"] == "approved"  # an administrator

    # the approver's own request goes to someone else in the position
    opp = _feasible_opportunity(client, "Self Co")
    q = run(client, "INT-05", vp, opportunity_id=opp, offering_name="Fiber 900", requested_discount_pct=25,
            discount_justification="x")["result"]["quote_id"]
    run(client, "INT-06", vp, quote_id=q, decision="approve")
    mine = run(client, "INT-06", vp, quote_id=q, decision="approve")
    assert mine["status"] == "failed" and "You asked for this approval" in mine["rationale"]


def test_order_management_discounts_follow_the_matrix(client):
    _setup(client)
    _launch(client)
    _, q = _quote(client, "Matrix OM Co")
    order_id = _order_from(client, q)
    run(client, "INT-51", order_id=order_id, product="Router CPE")
    run(client, "INT-52", order_id=order_id, line="Router CPE", discount_pct=25, discount_justification="launch customer")
    run(client, "INT-55", order_id=order_id, address="1 Main St", shipping_method="Express")
    run(client, "INT-56", order_id=order_id)
    run(client, "INT-57", order_id=order_id, payment_method="credit_card", card_last4="4242")
    assert client.post("/api/admin/approval-rules/presets/om_discount").status_code == 201
    agent, manager = _user(client, "agent@om-matrix.test"), _user(client, "manager@om-matrix.test")
    _staff(client, "Sales Manager", manager)

    waiting = run(client, "INT-59", agent, order_id=order_id)
    assert waiting["result"]["om_status"] == "awaiting_approval" and waiting["result"]["waiting_for"] == "Sales Manager", waiting
    assert ("order", order_id) in {(a["entity_type"], a["entity_id"]) for a in client.get("/api/approvals", headers=_as(manager)).json()}
    refused = run(client, "INT-59", agent, order_id=order_id, decision="approve")
    assert refused["status"] == "failed" and "Sales Manager" in refused["rationale"]
    approved = run(client, "INT-59", manager, order_id=order_id, decision="approve", reasoning="launch customer")
    assert approved["result"]["om_status"] == "awaiting_shipping", approved
    assert client.get("/api/approvals/order/" + str(order_id)).json()[0]["steps"][0]["decided_by"] == "manager"


def test_billing_adjustments_follow_the_matrix(client):
    party, _ = _live_customer(client, "Matrix Billing", "mb@example.com")
    inv = run(client, "INT-84", party_id=party)["result"]["invoice"]
    assert client.post("/api/admin/approval-rules/presets/billing_adjustment").status_code == 201
    agent, supervisor = _user(client, "agent@billing-matrix.test"), _user(client, "sup@billing-matrix.test")
    _staff(client, "Billing Supervisor", supervisor)

    big = run(client, "INT-86", agent, invoice=inv["invoice_number"], amount=round(inv["total"] * 0.9, 2), reason="goodwill")
    adjustment = big["result"]["adjustment"]
    assert adjustment["status"] == "pending_approval" and "Billing Supervisor" in adjustment["status_description"], big
    queue = client.get("/api/approvals", headers=_as(supervisor)).json()
    assert ("adjustment", adjustment["id"]) in {(a["entity_type"], a["entity_id"]) for a in queue}
    refused = run(client, "INT-86", agent, adjustment=adjustment["request_number"], decision="approve")
    assert refused["status"] == "failed" and "Billing Supervisor" in refused["rationale"]
    decided = run(client, "INT-36", supervisor, entity_type="adjustment", entity_id=adjustment["id"], decision="approve", reasoning="loyal")
    assert decided["status"] == "executed", decided["rationale"]
    assert decided["result"]["adjustment"]["status"] == "posted"
    # an agent may now ask for a write-off: it waits for the supervisor instead of being refused
    write_off = run(client, "INT-86", agent, party_id=party, amount=5, reason="write_off")
    assert write_off["result"]["adjustment"]["status"] == "pending_approval", write_off
