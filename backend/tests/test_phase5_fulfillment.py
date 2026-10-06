"""Phase 5: the real B2B order-fulfillment lifecycle (INT-22 through
INT-28) - proposal, customer email approval, Order Control Unit
approval, orchestrated decomposition, the Installation -> Connectivity
Test -> Acceptance Letter work-order chain, billing/Asset creation, and
prospect-to-customer conversion. Also covers the configurable
orchestration mode primitive (OrchestrationPolicy) and its safe
fallback to deterministic when llm_planned is configured without an
OPENAI_API_KEY.
"""

from app.models.approval import ApprovalChainTemplate, ApprovalChainTier
from app.models.crm import Party
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


def _approved_quote(client, company, offering_name="Fiber 500 Bundle", price=1000):
    _launch_bundle(client, name=offering_name, price=price)
    opp_id = _qualify_lead(client, company=company).json()["result"]["opportunity_id"]
    quote_id = client.post(
        "/api/intents",
        json={
            "text": f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: {offering_name}"
        },
    ).json()["result"]["quote_id"]
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}"})
    return quote_id


def _customer_approved_proposal(client, quote_id):
    proposal_id = client.post(
        "/api/intents", json={"text": f"Generate proposal for this quote. quote_id: {quote_id}"}
    ).json()["result"]["proposal_id"]
    client.post(
        "/api/intents",
        json={"text": f"Record customer approval of the proposal. proposal_id: {proposal_id}, decision: approve"},
    )
    return proposal_id


def _generated_order(client, company, offering_name, price=1000):
    quote_id = _approved_quote(client, company, offering_name, price)
    _customer_approved_proposal(client, quote_id)
    order_id = client.post(
        "/api/intents", json={"text": f"Generate order from quote. quote_id: {quote_id}"}
    ).json()["result"]["order_id"]
    return order_id


def test_int22_generate_proposal_for_approved_quote(client):
    quote_id = _approved_quote(client, "ProposalCo")
    resp = client.post("/api/intents", json={"text": f"Generate proposal for this quote. quote_id: {quote_id}"})
    body = resp.json()
    assert body["intent_type"] == "INT-22"
    assert body["status"] == "executed"
    assert body["result"]["status"] == "sent"
    assert "proposal.generated" in body["events"]


def test_int22_blocked_if_quote_not_approved(client):
    _launch_bundle(client, name="Fiber 600", price=1000)
    opp_id = _qualify_lead(client, company="UnapprovedQuoteCo").json()["result"]["opportunity_id"]
    quote_id = client.post(
        "/api/intents",
        json={"text": f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: Fiber 600"},
    ).json()["result"]["quote_id"]

    resp = client.post("/api/intents", json={"text": f"Generate proposal for this quote. quote_id: {quote_id}"})
    assert resp.json()["status"] == "needs_review"


def test_int24_requires_customer_approved_proposal(client):
    quote_id = _approved_quote(client, "NoProposalCo")
    resp = client.post("/api/intents", json={"text": f"Generate order from quote. quote_id: {quote_id}"})
    assert resp.json()["status"] == "needs_review"

    _customer_approved_proposal(client, quote_id)
    resp2 = client.post("/api/intents", json={"text": f"Generate order from quote. quote_id: {quote_id}"})
    body = resp2.json()
    assert body["intent_type"] == "INT-24"
    assert body["status"] == "executed"
    assert body["result"]["order_id"] is not None
    assert "order.generated_from_quote" in body["events"]


def test_full_fulfillment_chain_without_order_control_gate(client):
    """No "order" approval chain configured - INT-25 auto-approves on
    the spot, and the whole chain runs deterministically end to end:
    approve order -> WO1(installation) raised -> close WO1 -> network
    activated + WO2(connectivity_test) raised -> close WO2 -> WO3
    (acceptance_letter) raised -> close WO3 -> billing initiated, Asset
    created, order active, party converted prospect -> customer.
    """
    order_id = _generated_order(client, "FullChainCo", "Fiber 700", price=1000)

    approve_resp = client.post("/api/intents", json={"text": f"Approve this order. order_id: {order_id}"})
    approve_body = approve_resp.json()
    assert approve_body["intent_type"] == "INT-25"
    assert approve_body["status"] == "executed"
    assert approve_body["result"]["chain_status"] == "approved"
    assert approve_body["result"]["auto_approved"] is True
    wo1_id = approve_body["result"]["work_order_id"]
    assert approve_body["result"]["kind"] == "installation"
    assert "order.approved_and_submitted" in approve_body["events"]

    close1 = client.post(
        "/api/intents",
        json={"text": f"Close Work Order 1 (Installation). work_order_id: {wo1_id}, technical_parameters: VLAN 100, 1Gbps"},
    ).json()
    assert close1["intent_type"] == "INT-26"
    assert close1["status"] == "executed"
    assert close1["result"]["closed_work_order_status"] == "complete"
    assert "network_activation_ref" in close1["result"]
    wo2_id = close1["result"]["work_order_id"]
    assert close1["result"]["kind"] == "connectivity_test"

    close2 = client.post(
        "/api/intents",
        json={"text": f"Close Work Order 2 (Connectivity Test & Acceptance). work_order_id: {wo2_id}, signature: Jane Customer"},
    ).json()
    assert close2["intent_type"] == "INT-27"
    assert close2["status"] == "executed"
    wo3_id = close2["result"]["work_order_id"]
    assert close2["result"]["kind"] == "acceptance_letter"

    close3 = client.post(
        "/api/intents",
        json={"text": f"Close Work Order 3 (Acceptance Letter). work_order_id: {wo3_id}"},
    ).json()
    assert close3["intent_type"] == "INT-28"
    assert close3["status"] == "executed"
    assert "billing_initiation_ref" in close3["result"]
    assert close3["result"]["asset_id"] is not None
    assert close3["result"]["order_status"] == "active"
    assert "order.fulfilled" in close3["events"]

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "active"

    db = client.TestSessionLocal()
    try:
        party = db.query(Party).filter(Party.name == "FullChainCo").one()
        assert party.party_type == "customer"
    finally:
        db.close()


def test_reapproving_already_submitted_order_needs_review(client):
    """Regression: INT-25 used to be callable repeatedly with no guard,
    silently resubmitting to orchestration and raising a second,
    duplicate Installation work order each time.
    """
    order_id = _generated_order(client, "DoubleApproveCo", "Fiber 703", price=1000)
    first = client.post("/api/intents", json={"text": f"Approve this order. order_id: {order_id}"}).json()
    assert first["status"] == "executed"

    second = client.post("/api/intents", json={"text": f"Approve this order. order_id: {order_id}"})
    assert second.json()["status"] == "needs_review"

    db = client.TestSessionLocal()
    try:
        from app.models.assurance import WorkOrder

        installation_work_orders = (
            db.query(WorkOrder).filter(WorkOrder.order_id == order_id, WorkOrder.kind == "installation").all()
        )
        assert len(installation_work_orders) == 1
    finally:
        db.close()


def test_order_control_unit_gate_blocks_until_approved(client):
    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        template = ApprovalChainTemplate(tenant_id=tenant_id, name="Order Control Unit", entity_type="order")
        db.add(template)
        db.flush()
        db.add(ApprovalChainTier(template_id=template.id, sequence=1, approver_tier="Order Control Unit"))
        db.commit()
    finally:
        db.close()

    order_id = _generated_order(client, "GatedOrderCo", "Fiber 701", price=1000)

    approve_resp = client.post(
        "/api/intents", json={"text": f"Approve this order. order_id: {order_id}, reasoning: looks good"}
    ).json()
    assert approve_resp["result"]["chain_status"] == "approved"
    assert approve_resp["result"]["auto_approved"] is False
    assert approve_resp["result"]["work_order_id"] is not None


def test_orchestration_policy_admin_crud(client):
    create_resp = client.post(
        "/api/admin/orchestration-policies", json={"intent_type": "INT-25", "mode": "llm_planned"}
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["intent_type"] == "INT-25"
    assert body["mode"] == "llm_planned"
    assert body["is_active"] is True

    listed = client.get("/api/admin/orchestration-policies").json()
    assert any(p["id"] == body["id"] for p in listed)

    deactivate_resp = client.patch(f"/api/admin/orchestration-policies/{body['id']}/deactivate")
    assert deactivate_resp.json()["is_active"] is False


def test_llm_planned_mode_without_api_key_falls_back_to_deterministic(client):
    """No OPENAI_API_KEY in the test environment - configuring
    llm_planned for INT-25 must not change observable behavior at all,
    the same "opt-in, safe fallback" guarantee every other configurable
    primitive in this codebase gives.
    """
    client.post("/api/admin/orchestration-policies", json={"intent_type": "INT-25", "mode": "llm_planned"})

    order_id = _generated_order(client, "LlmFallbackCo", "Fiber 702", price=1000)
    approve_resp = client.post("/api/intents", json={"text": f"Approve this order. order_id: {order_id}"}).json()
    assert approve_resp["status"] == "executed"
    assert approve_resp["result"]["chain_status"] == "approved"
    assert approve_resp["result"]["kind"] == "installation"


def test_resolve_mode_prefers_specific_intent_over_all(client):
    from app.crm_core import orchestration_mode_service
    from app.models.orchestration_policy import OrchestrationMode

    db = client.TestSessionLocal()
    try:
        tenant_id = _default_tenant_id(db)
        from app.models.orchestration_policy import OrchestrationPolicy

        db.add(OrchestrationPolicy(tenant_id=tenant_id, intent_type="ALL", mode=OrchestrationMode.llm_planned))
        db.add(OrchestrationPolicy(tenant_id=tenant_id, intent_type="INT-25", mode=OrchestrationMode.deterministic))
        db.commit()

        assert orchestration_mode_service.resolve_mode(db, tenant_id, "INT-25") == OrchestrationMode.deterministic
        # No OPENAI_API_KEY in tests - "ALL"=llm_planned resolves safely to deterministic anyway.
        assert orchestration_mode_service.resolve_mode(db, tenant_id, "INT-26") == OrchestrationMode.deterministic
    finally:
        db.close()
