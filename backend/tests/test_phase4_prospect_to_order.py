"""Phase 4: Prospect -> Lead -> Opportunity -> Quote -> Order journey
extensions (INT-15 through INT-21) - prospect capture, product-interest
on a lead, the opt-in Sales Director qualification gate, configurable
multi-resource feasibility assessment, and PONR-gated quote/order
revision and cancellation.
"""

from app.models.approval import ApprovalChainTemplate, ApprovalChainTier
from app.tenancy import DEFAULT_TENANT_SLUG


def _default_tenant_id(db):
    from app.models.tenant import Tenant

    return db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id


def _launch_bundle(client, name="Fiber 500 Bundle", price=1000):
    return client.post(
        "/api/intents",
        json={
            "text": f"Launch a new bundle. name: {name}, composition: Fiber500, "
            f"segment: B2B-SMB, channel: B2B, price: {price}, discount: 0"
        },
    )


def _qualify_lead(client, company="Acme Telecom"):
    return client.post(
        "/api/intents",
        json={"text": f"Qualify this lead. company_name: {company}, source: referral, region: North"},
    )


def _quoted_opportunity(client, company, offering_name="Fiber 500 Bundle", price=1000):
    _launch_bundle(client, name=offering_name, price=price)
    return _qualify_lead(client, company=company).json()["result"]["opportunity_id"]


def test_int15_add_prospect_creates_party(client):
    resp = client.post(
        "/api/intents",
        json={"text": "Add this prospect. name: Globex Corp, email: contact@globex.example"},
    )
    body = resp.json()
    assert body["intent_type"] == "INT-15"
    assert body["status"] == "executed"
    assert body["result"]["party_type"] == "prospect"
    assert "prospect.added" in body["events"]


def test_int16_add_product_to_lead(client):
    opp_resp = _qualify_lead(client, company="ProductInterestCo")
    lead_id = opp_resp.json()["result"]["lead_id"]
    _launch_bundle(client, name="Fiber 200 Lite", price=500)

    resp = client.post(
        "/api/intents",
        json={"text": f"Add this product to the lead. lead_id: {lead_id}, offering_name: Fiber 200 Lite"},
    )
    body = resp.json()
    assert body["intent_type"] == "INT-16"
    assert body["status"] == "executed"
    assert body["result"]["offering_name"] == "Fiber 200 Lite"

    lead = client.get(f"/api/leads/{lead_id}").json()
    assert lead["interested_offering_name"] == "Fiber 200 Lite"


def _configure_lead_gate(client, tenant_id_fn=_default_tenant_id):
    db = client.TestSessionLocal()
    try:
        tenant_id = tenant_id_fn(db)
        template = ApprovalChainTemplate(tenant_id=tenant_id, name="Sales Director Gate", entity_type="lead")
        db.add(template)
        db.flush()
        db.add(ApprovalChainTier(template_id=template.id, sequence=1, approver_tier="Sales Director"))
        db.commit()
    finally:
        db.close()


def test_int17_gated_lead_awaits_approval_then_qualifies(client):
    _configure_lead_gate(client)

    qualify_resp = _qualify_lead(client, company="GatedCo")
    body = qualify_resp.json()
    assert body["status"] == "executed"
    assert body["result"]["status"] == "pending_qualification"
    assert "opportunity_id" not in body["result"]
    lead_id = body["result"]["lead_id"]

    lead_mid = client.get(f"/api/leads/{lead_id}").json()
    assert lead_mid["status"] == "pending_qualification"
    assert lead_mid["approval_chain"] is None  # not yet instantiated

    approve_resp = client.post(
        "/api/intents",
        json={"text": f"Approve this lead. lead_id: {lead_id}, reasoning: strong fit"},
    )
    approve_body = approve_resp.json()
    assert approve_body["intent_type"] == "INT-17"
    assert approve_body["status"] == "executed"
    assert approve_body["result"]["status"] == "qualified"
    assert approve_body["result"]["opportunity_id"] is not None
    assert "lead.qualified" in approve_body["events"]

    lead_final = client.get(f"/api/leads/{lead_id}").json()
    assert lead_final["status"] == "qualified"
    assert lead_final["approval_chain"]["status"] == "approved"


def test_int17_gated_lead_rejected_gets_no_opportunity(client):
    _configure_lead_gate(client)

    lead_id = _qualify_lead(client, company="RejectedGateCo").json()["result"]["lead_id"]
    reject_resp = client.post(
        "/api/intents",
        json={"text": f"Reject this lead. lead_id: {lead_id}, reasoning: not a fit"},
    )
    body = reject_resp.json()
    assert body["result"]["status"] == "rejected"
    assert "lead.rejected" in body["events"]

    lead_final = client.get(f"/api/leads/{lead_id}").json()
    assert lead_final["status"] == "rejected"


def test_int04_without_gate_still_auto_qualifies(client):
    # No "lead" template configured for this tenant - old behavior unchanged.
    resp = _qualify_lead(client, company="UngatedCo")
    body = resp.json()
    assert body["result"]["status"] == "qualified"
    assert body["result"]["opportunity_id"] is not None


def test_int18_feasibility_no_check_types_runs_address_only(client):
    opp_id = _quoted_opportunity(client, company="FeasibilityDefaultCo")
    resp = client.post(
        "/api/intents",
        json={"text": f"Run feasibility assessment. opportunity_id: {opp_id}, address: 123 Main St"},
    )
    body = resp.json()
    assert body["intent_type"] == "INT-18"
    assert body["status"] == "executed"
    assert body["result"]["outcome"] == "passed"
    assert body["result"]["results"][0]["check_type_name"] == "Address Validation"


def test_int18_feasibility_with_configured_checks_fails_on_no_coverage(client):
    create_resp = client.post(
        "/api/admin/feasibility-check-types", json={"name": "Fiber Route Availability", "resource_type": "fiber"}
    )
    assert create_resp.status_code == 201

    opp_id = _quoted_opportunity(client, company="FeasibilityFailCo")
    resp = client.post(
        "/api/intents",
        json={"text": f"Run feasibility assessment. opportunity_id: {opp_id}, address: 9 NOCOVERAGE Ave"},
    )
    body = resp.json()
    assert body["result"]["outcome"] == "failed"
    assert body["result"]["results"][0]["check_type_name"] == "Fiber Route Availability"

    opportunity = client.get(f"/api/opportunities/{opp_id}").json()
    assert opportunity["feasibility_assessments"][0]["outcome"] == "failed"


def _quote_for(client, opp_id, offering_name, discount=0):
    return client.post(
        "/api/intents",
        json={
            "text": f"Configure and quote this bundle. opportunity_id: {opp_id}, "
            f"offering_name: {offering_name}, discount: {discount}"
        },
    ).json()["result"]["quote_id"]


def test_int19_revise_quote_before_ponr(client):
    opp_id = _quoted_opportunity(client, company="ReviseQuoteCo", offering_name="Fiber 300", price=1000)
    quote_id = _quote_for(client, opp_id, "Fiber 300")

    resp = client.post(
        "/api/intents",
        json={"text": f"Revise this quote. quote_id: {quote_id}, discount: 10"},
    )
    body = resp.json()
    assert body["intent_type"] == "INT-19"
    assert body["status"] == "executed"
    assert body["result"]["total_price"] == 900.0


def test_int19_revise_quote_blocked_once_approved(client):
    opp_id = _quoted_opportunity(client, company="ReviseBlockedCo", offering_name="Fiber 301", price=1000)
    quote_id = _quote_for(client, opp_id, "Fiber 301")
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}"})

    resp = client.post(
        "/api/intents",
        json={"text": f"Revise this quote. quote_id: {quote_id}, discount: 10"},
    )
    assert resp.json()["status"] == "needs_review"


def test_int20_cancel_quote_before_ponr(client):
    opp_id = _quoted_opportunity(client, company="CancelQuoteCo", offering_name="Fiber 302", price=1000)
    quote_id = _quote_for(client, opp_id, "Fiber 302")

    resp = client.post(
        "/api/intents",
        json={"text": f"Cancel this quote. quote_id: {quote_id}, reasoning: customer withdrew"},
    )
    body = resp.json()
    assert body["intent_type"] == "INT-20"
    assert body["status"] == "executed"
    assert body["result"]["status"] == "cancelled"

    quote = client.get(f"/api/quotes/{quote_id}").json()
    assert quote["status"] == "cancelled"


def test_custom_ponr_policy_blocks_draft_quote_revision(client):
    policy_resp = client.post(
        "/api/admin/ponr-policies", json={"entity_type": "quote", "ponr_statuses": "draft,pending_approval,approved"}
    )
    assert policy_resp.status_code == 201

    opp_id = _quoted_opportunity(client, company="StrictPonrCo", offering_name="Fiber 303", price=1000)
    quote_id = _quote_for(client, opp_id, "Fiber 303")

    resp = client.post(
        "/api/intents",
        json={"text": f"Revise this quote. quote_id: {quote_id}, discount: 5"},
    )
    assert resp.json()["status"] == "needs_review"


def _active_order(client, offering_name="Fiber 400", price=1000, company="OrderCo"):
    opp_id = _quoted_opportunity(client, company=company, offering_name=offering_name, price=price)
    quote_id = _quote_for(client, opp_id, offering_name)
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}"})
    return client.post(
        "/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"}
    ).json()["result"]["order_id"]


def test_int21_revise_order_blocked_once_completed(client):
    order_id = _active_order(client, company="ReviseActiveBlockedCo")

    resp = client.post(
        "/api/intents",
        json={"text": f"Revise this order. order_id: {order_id}, new_price: 1200"},
    )
    # a completed order is read-only - the change goes through its installed asset
    body = resp.json()
    assert body["status"] == "failed"
    assert "change order from the installed asset" in body["error"]["message"]


def test_ponr_defaults_allow_pre_activation_order_statuses(client):
    from app.crm_core import ponr_service

    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        assert ponr_service.has_reached_ponr(db, tenant_id, "order", "created") is False
        assert ponr_service.has_reached_ponr(db, tenant_id, "order", "activating") is False
        assert ponr_service.has_reached_ponr(db, tenant_id, "order", "active") is True
        assert ponr_service.has_reached_ponr(db, tenant_id, "quote", "draft") is False
        assert ponr_service.has_reached_ponr(db, tenant_id, "quote", "approved") is True
    finally:
        db.close()
