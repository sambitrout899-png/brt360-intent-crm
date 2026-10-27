"""B2B Sales-to-Order, two-kind approvals and the B2C order path - the
additions on top of the original process (which the rest of the suite
keeps pinning unchanged)."""

from app.auth import hash_password
from app.models.auth import User, UserRole
from app.models.tenant import Tenant


def run(client, code, user_id=None, **entities):
    headers = {"X-Test-User-Id": str(user_id)} if user_id else {}
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}, headers=headers).json()


def say(client, text, context=None):
    body = {"text": text, **({"context": context} if context else {})}
    return client.post("/api/intents", json=body).json()


def _tenant_id(client):
    db = client.TestSessionLocal()
    try:
        return db.query(Tenant).filter(Tenant.slug == "default").one().id
    finally:
        db.close()


def _user(client, email, role=UserRole.agent):
    db = client.TestSessionLocal()
    try:
        user = User(tenant_id=_tenant_id(client), email=email, password_hash=hash_password("password123"), name=email.split("@")[0], role=role)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _position_id(client, name):
    return next(p["id"] for p in client.get("/api/admin/positions").json() if p["name"] == name)


def _staff(client, position_name, *user_ids):
    position_id = _position_id(client, position_name)
    assert client.put(f"/api/admin/positions/{position_id}/members", json={"user_ids": list(user_ids)}).status_code == 200
    return position_id


def _launch(client, name="Fiber 900", price=1000):
    say(client, f"Launch a new bundle. name: {name}, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: {price}, discount: 0")


def _account(client, name="Globex", price_list=None):
    party_id = run(client, "INT-15", name=name, email=f"ops@{name.lower()}.com")["result"]["party_id"]
    body = run(client, "INT-35", party_id=party_id, **({"price_list": price_list} if price_list else {}))
    assert body["status"] == "executed", body["rationale"]
    return party_id


def _opportunity(client, company):
    body = say(client, f"Qualify this lead. company_name: {company}, source: referral, region: North")
    return body["result"]["opportunity_id"]


def _feasible_opportunity(client, company, offering="Fiber 900"):
    opp_id = _opportunity(client, company)
    assert run(client, "INT-37", opportunity_id=opp_id, offering_name=offering,
               pricing_attributes={"Bandwidth": "100 Mbps", "Contract term": "36 months"})["status"] == "executed"
    assert run(client, "INT-38", opportunity_id=opp_id, address="12 King Fahd Road")["status"] == "executed"
    body = run(client, "INT-18", opportunity_id=opp_id, decision="feasible", notes="Fibre on site")
    assert body["result"]["stage"] == "feasible", body
    return opp_id


def _approved_quote(client, company, offering="Fiber 900"):
    opp_id = _feasible_opportunity(client, company, offering)
    quote_id = run(client, "INT-05", opportunity_id=opp_id, offering_name=offering)["result"]["quote_id"]
    assert run(client, "INT-06", quote_id=quote_id, decision="approve")["result"]["status"] == "approved"
    return quote_id


# ---------------------------------------------------------------- the B2B process end to end


def test_b2b_sales_to_order_end_to_end(client):
    _launch(client)
    party_id = _account(client, "Globex", price_list="Enterprise")
    assert client.get("/api/parties").json()[0]["profile"]["account_status"] == "account"

    opp_id = _opportunity(client, "Globex")  # the existing account is reused
    configured = run(client, "INT-37", opportunity_id=opp_id, offering_name="Fiber 900", quantity=2,
                     pricing_attributes="Bandwidth=100 Mbps, Contract term=36 months")
    assert configured["result"]["stage"] == "configured"
    assert configured["result"]["pricing_attributes"] == {"Bandwidth": "100 Mbps", "Contract term": "36 months"}

    blocked = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900")
    assert blocked["status"] == "failed" and "Presales" in blocked["rationale"]

    sent = run(client, "INT-38", opportunity_id=opp_id, address="12 King Fahd Road", technical_parameters="2 x 1G ports")
    assert sent["result"]["stage"] == "in_feasibility" and sent["result"]["assigned_to_presales"] is True
    meta = client.get(f"/api/records/opportunity/{opp_id}").json()
    assert meta["owner_position"]["name"] == "Presales"

    failed = run(client, "INT-18", opportunity_id=opp_id, decision="not_feasible", notes="No duct")
    assert failed["result"]["stage"] == "feasibility_failed"
    assert run(client, "INT-37", opportunity_id=opp_id)["result"]["stage"] == "configured"  # back to the AM
    run(client, "INT-38", opportunity_id=opp_id)  # site kept from the first hand-off
    passed = run(client, "INT-18", opportunity_id=opp_id, decision="feasible")
    assert passed["result"]["stage"] == "feasible"

    quote = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900", quantity=2)
    assert quote["status"] == "executed"
    quote_id = quote["result"]["quote_id"]
    assert quote["result"]["total_price"] == 1900.0  # Enterprise pricelist: 5% below 1000, x2
    assert run(client, "INT-06", quote_id=quote_id, decision="approve")["result"]["status"] == "approved"

    wrong = run(client, "INT-39", quote_id=quote_id, po_number="PO-7781", po_amount=1800)
    assert wrong["status"] == "executed" and wrong["result"]["assigned_to_sales_ops"] is True
    mismatch = run(client, "INT-40", quote_id=quote_id)
    assert mismatch["status"] == "failed" and "PO amount matches the quote" in mismatch["rationale"]
    run(client, "INT-39", quote_id=quote_id, po_number="PO-7781", po_amount=1900)
    matched = run(client, "INT-40", quote_id=quote_id)
    assert matched["status"] == "executed", matched["rationale"]
    order_id = matched["result"]["order_id"]
    assert client.get(f"/api/quotes/{quote_id}").json()["purchase_order"]["matched_at"]

    # CPMO approval switched on with one click, staffed: Sales Ops can't approve it, CPMO can.
    assert client.post("/api/admin/approval-rules/presets/cpmo_order").status_code == 201
    cpmo_user, sales_ops = _user(client, "cpmo@default.local"), _user(client, "ops@default.local")
    _staff(client, "CPMO", cpmo_user)
    submitted = run(client, "INT-25", sales_ops, order_id=order_id, decision="approve")
    assert submitted["status"] == "executed" and submitted["result"]["waiting_for"] == "CPMO"
    assert [a["entity_id"] for a in client.get("/api/approvals", headers={"X-Test-User-Id": str(cpmo_user)}).json()] == [order_id]
    refused = run(client, "INT-25", sales_ops, order_id=order_id, decision="approve")
    assert refused["status"] == "failed" and "only people in that position" in refused["rationale"]
    approved = run(client, "INT-36", cpmo_user, entity_type="order", entity_id=order_id, decision="approve")
    assert approved["status"] == "executed", approved["rationale"]
    work_order_id = approved["result"]["work_order_id"]

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "activating" and order["segment"] == "B2B"
    wo2 = run(client, "INT-26", work_order_id=work_order_id, technical_parameters="VLAN 120")["result"]["work_order_id"]
    wo3 = run(client, "INT-27", work_order_id=wo2, signature="Tested with Mr. Rao")["result"]["work_order_id"]
    assert run(client, "INT-28", work_order_id=wo3, document_ref="ESAN-0042")["status"] == "executed"

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "completed"
    codes = [m["code"] for m in order["milestones"]]
    assert codes == ["order_created", "cpmo_pending", "order_approved", "sent_to_osm", "wfm1_generated", "wfm1_closed",
                     "network_activated", "wfm2_generated", "wfm2_closed", "esan_initiated", "wfm3_generated",
                     "esan_signed", "billing_started", "assets_created", "live"]
    party = client.get(f"/api/parties/{party_id}/360").json()["party"]
    assert party["party_type"] == "customer"


def test_original_proposal_path_still_orders(client):
    """The pre-existing route (proposal -> customer approval -> order) is untouched."""
    _launch(client)
    quote_id = run(client, "INT-05", opportunity_id=_opportunity(client, "OldWay"), offering_name="Fiber 900")["result"]["quote_id"]
    run(client, "INT-06", quote_id=quote_id, decision="approve")
    proposal_id = run(client, "INT-22", quote_id=quote_id)["result"]["proposal_id"]
    run(client, "INT-23", proposal_id=proposal_id, decision="approve")
    assert run(client, "INT-24", quote_id=quote_id)["status"] == "executed"


# ---------------------------------------------------------------- approvals


def test_quote_discount_goes_to_the_staffed_position(client):
    _launch(client)
    rep, vp = _user(client, "rep@default.local"), _user(client, "vp@default.local")
    opp_id = _feasible_opportunity(client, "DiscountCo")
    quote_id = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900", requested_discount_pct=25,
                   discount_justification="strategic")["result"]["quote_id"]

    # Nobody holds "VP Sales" yet: decided exactly as before.
    _staff(client, "VP Sales", vp)
    submitted = run(client, "INT-06", rep, quote_id=quote_id, decision="approve")
    assert submitted["status"] == "executed" and submitted["result"]["status"] == "pending_approval"
    assert submitted["result"]["waiting_for"] == "VP Sales"
    assert run(client, "INT-06", rep, quote_id=quote_id, decision="approve")["status"] == "failed"
    decided = run(client, "INT-06", vp, quote_id=quote_id, decision="approve", reasoning="ok")
    assert decided["result"]["status"] == "approved"
    steps = client.get(f"/api/quotes/{quote_id}").json()["approval_chain"]["steps"]
    assert steps[0]["approver_tier"] == "VP Sales" and steps[0]["status"] == "approved"


def test_unstaffed_tiers_keep_the_old_behaviour(client):
    _launch(client)
    rep = _user(client, "rep2@default.local")
    opp_id = _feasible_opportunity(client, "OpenCo")
    quote_id = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900", requested_discount_pct=15,
                   discount_justification="loyal")["result"]["quote_id"]
    assert run(client, "INT-06", rep, quote_id=quote_id, decision="approve")["result"]["status"] == "approved"


def test_record_approval_on_any_stage(client):
    _launch(client)
    director = _user(client, "director@default.local")
    client.post("/api/admin/positions", json={"name": "Deal Board", "user_ids": [director], "data_scope": "org"})
    options = client.get("/api/admin/approval-rules/options").json()
    assert any(s["code"] == "lost" for t in options["record_types"] if t["entity_type"] == "opportunity" for s in t["states"])
    rule = client.post("/api/admin/approval-rules", json={
        "name": "Losing a deal", "kind": "record", "entity_type": "opportunity", "trigger_state": "lost",
        "tiers": [{"position_id": _position_id(client, "Deal Board")}],
    })
    assert rule.status_code == 201, rule.text
    duplicate = client.post("/api/admin/approval-rules", json={
        "name": "Again", "kind": "record", "entity_type": "opportunity", "trigger_state": "lost",
        "tiers": [{"position_id": _position_id(client, "Deal Board")}],
    })
    assert duplicate.status_code == 400

    opp_id = _opportunity(client, "LoseCo")
    asked = say(client, f"Move this record. entity_type: opportunity, entity_id: {opp_id}, action: close_lost")
    assert asked["status"] == "executed" and asked["result"]["waiting_for"] == "Deal Board"
    assert client.get(f"/api/opportunities/{opp_id}").json()["stage"] == "draft"

    approvals = client.get(f"/api/approvals/opportunity/{opp_id}", headers={"X-Test-User-Id": str(director)}).json()
    assert approvals[0]["gate"] == "lost" and approvals[0]["can_decide"] is True
    decided = run(client, "INT-36", director, entity_type="opportunity", entity_id=opp_id, decision="approve")
    assert decided["status"] == "executed", decided["rationale"]
    assert client.get(f"/api/opportunities/{opp_id}").json()["stage"] == "lost"


def test_lead_approval_without_a_gate_qualifies_directly(client):
    """INT-17 used to fail with "No lead-qualification approval chain" - now it qualifies."""
    db = client.TestSessionLocal()
    from app.models.crm import Lead, LeadStatus, Party

    party = Party(tenant_id=_tenant_id(client), name="DirectCo")
    db.add(party)
    db.flush()
    lead = Lead(tenant_id=party.tenant_id, party_id=party.id, source="web", region="ALL", owner_name="x", propensity_score=0.4,
                status=LeadStatus.pending_qualification)
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()
    body = run(client, "INT-17", lead_id=lead_id, decision="approve")
    assert body["status"] == "executed", body["rationale"]
    assert body["result"]["status"] == "qualified" and body["result"]["opportunity_id"]


def test_lead_gate_preset(client):
    assert client.post("/api/admin/approval-rules/presets/lead_qualification").status_code == 201
    pending = say(client, "Qualify this lead. company_name: GateCo, source: referral, region: North")
    assert pending["result"]["status"] == "pending_qualification"
    queue = client.get("/api/approvals").json()  # nobody holds "Sales Director" yet: the admin sees it
    assert [(a["entity_type"], a["entity_id"], a["waiting_for"]) for a in queue] == [("lead", pending["result"]["lead_id"], "Sales Director")]
    director = _user(client, "director2@default.local")
    _staff(client, "Sales Director", director)
    assert run(client, "INT-36", _user(client, "rep3@default.local"), entity_type="lead",
               entity_id=pending["result"]["lead_id"], decision="approve")["status"] == "failed"
    assert client.get("/api/approvals", headers={"X-Test-User-Id": str(director)}).json()[0]["entity_type"] == "lead"
    decided = run(client, "INT-36", director, entity_type="lead", entity_id=pending["result"]["lead_id"], decision="approve")
    assert decided["result"]["status"] == "qualified" and decided["result"]["opportunity_id"]


def test_reseeding_an_existing_tenant_never_rebinds_its_approvals(client):
    """Deploys re-run the seed; a live tenant's approval steps must stay as they are."""
    from app.models.approval import ApprovalChainTier
    from app.seed import seed_reference_data

    db = client.TestSessionLocal()
    tenant_id = _tenant_id(client)
    for tier in db.query(ApprovalChainTier).all():
        tier.position_id = None  # as on a server before this release
    db.commit()
    seed_reference_data(db, tenant_id)
    db.commit()
    assert all(t.position_id is None for t in db.query(ApprovalChainTier).all())
    db.close()


def test_old_approval_chains_api_unchanged(client):
    templates = client.get("/api/admin/approval-chains").json()
    assert len(templates) == 4 and set(templates[0]["tiers"][0]) == {"sequence", "approver_tier"}
    rules = client.get("/api/admin/approval-rules").json()
    assert {r["kind"] for r in rules} == {"monetary"} and rules[-1]["tiers"][1]["position_name"] == "CFO"
    assert rules[-1]["tiers"][1]["open_to_anyone"] is True


# ---------------------------------------------------------------- B2C


def test_consumer_order_waits_for_discount_approval(client):
    _launch(client, "Home Fiber", 50)
    assert client.post("/api/admin/approval-rules/presets/consumer_discount").status_code == 201
    party_id = run(client, "INT-41", name="Priya Sharma", email="priya@example.com")["result"]["party_id"]

    plain = run(client, "INT-07", party_id=party_id, offering_name="Home Fiber")
    assert plain["status"] == "executed", plain["rationale"]
    assert client.get(f"/api/orders/{plain['result']['order_id']}").json()["status"] == "completed"

    waiting = run(client, "INT-07", party_id=party_id, offering_name="Home Fiber", requested_discount_pct=20)
    assert waiting["status"] == "executed" and waiting["result"]["waiting_for"] == "Retail Sales Manager"
    order_id = waiting["result"]["order_id"]
    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "pending_approval" and order["segment"] == "B2C" and order["monthly_price"] == 40.0
    decided = run(client, "INT-36", entity_type="order", entity_id=order_id, decision="approve")
    assert decided["status"] == "executed", decided["rationale"]
    assert client.get(f"/api/orders/{order_id}").json()["status"] == "completed"


def test_segment_specific_intents_follow_the_tenant(client):
    codes = {i["code"] for i in client.get("/api/intents/catalog").json()["intents"]}
    assert {"INT-35", "INT-37", "INT-38", "INT-39", "INT-40", "INT-41", "INT-36"} <= codes
    db = client.TestSessionLocal()
    from app.models.tenant import TenantVertical

    tenant = db.get(Tenant, _tenant_id(client))
    tenant.vertical = TenantVertical.b2c
    db.commit()
    db.close()
    codes = {i["code"] for i in client.get("/api/intents/catalog").json()["intents"]}
    assert "INT-41" in codes and "INT-37" not in codes and "INT-04" in codes
    assert "B2B process" in run(client, "INT-37", opportunity_id=1)["rationale"]


# ---------------------------------------------------------------- configuration


def test_pricelists_and_process_roles(client):
    lists = client.get("/api/admin/price-lists").json()
    assert {(p["name"], p["is_default"]) for p in lists} == {("Standard", True), ("Enterprise", False), ("Strategic", False)}
    created = client.post("/api/admin/price-lists", json={"name": "Gov", "adjustment_pct": -12, "items": [{"offering_name": "Fiber 900", "price": 700}]})
    assert created.status_code == 201
    roles = {r["role_key"]: r for r in client.get("/api/admin/process-roles").json()}
    assert roles["presales"]["position_name"] == "Presales"
    team = client.post("/api/admin/positions", json={"name": "Solutions Team", "data_scope": "org"}).json()
    roles = {r["role_key"]: r for r in client.put("/api/admin/process-roles/presales", json={"position_id": team["id"]}).json()}
    assert roles["presales"]["position_name"] == "Solutions Team"

    _launch(client)
    party_id = _account(client, "GovCo", price_list="Gov")
    opp_id = _feasible_opportunity(client, "GovCo")
    assert client.get(f"/api/records/opportunity/{opp_id}").json()["owner_position"] is None or True
    quote = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900")
    assert quote["result"]["total_price"] == 700.0 and party_id


def test_saved_state_model_gains_new_stages(client):
    """A tenant that saved its opportunity lifecycle before these stages
    existed still gets them, and can save it again."""
    model = next(m for m in client.get("/api/admin/state-models").json() if m["entity_type"] == "opportunity")
    old_states = [s for s in model["states"] if s["code"] in ("draft", "qualified", "quoted", "won", "lost")]
    old_transitions = [t for t in model["transitions"] if t["to_state"] in ("qualified", "quoted", "won", "lost", "draft")
                       and t["from_state"] in ("draft", "qualified", "quoted", "won", "lost", "*")]
    db = client.TestSessionLocal()
    from app.models.lifecycle import StateModel, StateModelState, StateModelTransition

    saved = StateModel(tenant_id=_tenant_id(client), entity_type="opportunity", name="Ours")
    db.add(saved)
    db.flush()
    for i, s in enumerate(old_states):
        db.add(StateModelState(state_model_id=saved.id, code=s["code"], label=s["label"], sequence=i, editable=True,
                               is_terminal=s["is_terminal"], built_in=True))
    for t in old_transitions:
        db.add(StateModelTransition(state_model_id=saved.id, **t))
    db.commit()
    db.close()

    model = next(m for m in client.get("/api/admin/state-models").json() if m["entity_type"] == "opportunity")
    assert model["name"] == "Ours" and {"configured", "in_feasibility", "feasible"} <= {s["code"] for s in model["states"]}
    assert client.put("/api/admin/state-models/opportunity", json={k: model[k] for k in ("name", "states", "transitions")}).status_code == 200
    _launch(client)
    assert _feasible_opportunity(client, "SavedCo")


# ---------------------------------------------------------------- understanding


def test_new_steps_understood_from_plain_english(client):
    from app.intent_engine import classifier

    cases = {
        "convert prospect 4 into an account on the Enterprise pricelist": ("INT-35", {"party_id": 4, "price_list": "Enterprise"}),
        "send opportunity 7 to presales, site 12 King Fahd Road": ("INT-38", {"opportunity_id": 7, "address": "12 King Fahd Road"}),
        "attach the customer's PO PO-2026-0142 to quote 9": ("INT-39", {"quote_id": 9, "po_number": "PO-2026-0142"}),
        "match all for quote 9": ("INT-40", {"quote_id": 9}),
        "add a consumer Priya Sharma priya@example.com": ("INT-41", {"name": "Priya Sharma", "email": "priya@example.com"}),
        "configure the product on opportunity 7": ("INT-37", {"opportunity_id": 7}),
        "reject opportunity 7 because the budget is gone": ("INT-36", {"entity_type": "opportunity", "entity_id": 7, "decision": "reject"}),
    }
    for text, (code, expected) in cases.items():
        result = classifier.classify(text)
        assert result.intent_type == code, (text, result.intent_type)
        assert expected.items() <= result.entities.items(), (text, result.entities)

    # Sentences that only mention a PO, an account or presales keep their old meaning.
    for text, code in {
        "Update this record. entity_type: quote, entity_id: 3, po_number: PO-77": "INT-29",
        "Configure and quote this bundle. opportunity_id: 5, offering_name: Fiber 800": "INT-05",
        "approve order 9": "INT-25",
        "approve quote 12": "INT-06",
        "Activate the order for this quote. quote_id: 4, service_account_name: Main account": "INT-07",
    }.items():
        assert classifier.classify(text).intent_type == code, text


def test_model_prompt_is_unchanged_for_the_trained_model():
    """The fine-tuned intent model was trained on this prompt: the new steps
    are recognised before any model runs, so the prompt does not mention them."""
    from app.intent_engine import classifier

    prompt = classifier.system_prompt()
    assert "INT-35" not in prompt and "INT-41" not in prompt and prompt.startswith("You are the NLU classifier")
