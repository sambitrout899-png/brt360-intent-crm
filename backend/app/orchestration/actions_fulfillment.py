"""Phase 5 - the real B2B order-fulfillment lifecycle (INT-22 through
INT-28), additive alongside the existing immediate-activation path
(INT-07, still the simple/direct route for tenants that don't need this
ceremony): Proposal -> customer email approval -> Order generation ->
Order Control Unit approval -> orchestrated decomposition -> a
sequential Installation -> Connectivity Test -> Acceptance Letter work
order chain -> billing + Asset (CRM-local CMDB copy) + prospect-to-
customer conversion.

Recording what actually happened (a decision, a closed work order, an
attached document) is always deterministic - that's the real business
transaction, never delegated to an LLM. What happens *next* as a
consequence of that fact (submit to orchestration? create the next work
order? initiate billing?) is the one part that respects each tenant's
configured OrchestrationPolicy: "deterministic" (default, a fixed
sequence - see fulfillment_ops.py) or "llm_planned" (an LLM chooses
which pre-vetted operations to run next, from a short explicit
allow-list - see llm_planner.py). Either way the actual operations are
the exact same tested functions; only who sequences them differs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import orchestration_mode_service, process_service
from app.data_intelligence import event_bus
from app.models.catalog import ProductOffering
from app.models.orchestration_policy import OrchestrationMode
from app.models.orders import Order, OrderItem
from app.orchestration import actions_sales, fulfillment_ops, llm_planner
from app.orchestration.orchestrator import SagaStep


def _run_consequence(
    db: Session,
    tenant_id: int,
    intent_type: str,
    goal_description: str,
    entities_for_planner: dict[str, Any],
    allowed_operations: list[str],
    deterministic_steps: list[SagaStep],
) -> dict[str, Any]:
    mode = orchestration_mode_service.resolve_mode(db, tenant_id, intent_type)
    steps: list[SagaStep] | None = None
    if mode == OrchestrationMode.llm_planned:
        try:
            steps = llm_planner.plan_steps(db, tenant_id, goal_description, entities_for_planner, allowed_operations)
        except llm_planner.PlanningUnavailable:
            steps = None
    if steps is None:
        steps = deterministic_steps
    merged: dict[str, Any] = {}
    for step in steps:
        merged.update(step.action() or {})
    return merged


def build_int22_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-22 - Generate proposal for this quote."""

    quote_id = entities["quote_id"]

    def step1() -> dict[str, Any]:
        result = fulfillment_ops.op_generate_proposal(db, tenant_id, quote_id)
        event_bus.publish(db, execution_id, "proposal.generated", result)
        return result

    return [SagaStep("generate_proposal", step1)]


def build_int23_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-23 - Record customer approval of the proposal (the customer's
    decision normally arrives over email, outside this system - this is
    a human recording what the customer said).
    """

    proposal_id = entities["proposal_id"]
    decision = entities.get("decision", "approve")

    def step1() -> dict[str, Any]:
        result = fulfillment_ops.op_record_customer_decision(db, tenant_id, proposal_id, decision)
        event_bus.publish(db, execution_id, "proposal.customer_decided", result)
        return result

    return [SagaStep("record_customer_decision", step1)]


def build_int24_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-24 - Generate order from quote. Gated on the quote being
    approved AND having a customer-approved proposal on file - this is
    the stricter B2B gate; INT-07 remains the direct immediate-activation
    path for anything that doesn't need a proposal/customer sign-off.
    """

    quote_id = entities["quote_id"]

    def step1() -> dict[str, Any]:
        result = fulfillment_ops.op_generate_order_from_quote(db, tenant_id, quote_id)
        actions_sales.milestone(db, result["order_id"], "order_created", "Order created from the quote")
        event_bus.publish(db, execution_id, "order.generated_from_quote", result)
        return result

    return [SagaStep("generate_order_from_quote", step1)]


def build_int25_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-25 - Approve this order (Order Control Unit). Opt-in gate, same
    shape as the lead-qualification gate: no "order" approval chain
    template configured auto-approves immediately. Once approved (on the
    spot, or after however many tiers a configured chain needs), submits
    the order to orchestration and raises the first (installation) work
    order - the one part of this intent that respects OrchestrationPolicy.
    """

    order_id = entities["order_id"]
    decision = entities.get("decision", "approve")
    reasoning = entities.get("reasoning", "")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        user_id = int(entities["_actor_user_id"]) if entities.get("_actor_user_id") else None
        result = fulfillment_ops.op_approve_order(db, tenant_id, order_id, decision, reasoning, user_id)
        state["approve_result"] = result
        return result

    def step2() -> dict[str, Any]:
        if state["approve_result"]["chain_status"] != "approved":
            if state["approve_result"]["chain_status"] == "rejected":
                actions_sales.milestone(db, order_id, "order_rejected", "Order approval refused", reasoning)
            event_bus.publish(db, execution_id, "order.control_unit_decision_recorded", state["approve_result"])
            return {}

        order = db.get(Order, order_id)
        first_item = order.items[0] if order.items else None

        # A tenant-designed fulfillment pattern (Admin -> Fulfillment) takes the
        # order from here: COM -> SOM -> TOM, instead of the fixed work-order chain.
        from app.orchestration import fulfillment_engine

        pattern = fulfillment_engine.matching_pattern(db, tenant_id, order, fulfillment_engine.APPROVAL_INTENT)
        if pattern is not None:
            approved_by = "auto-approved" if state["approve_result"].get("auto_approved") else state["approve_result"].get("decided_tier") or ""
            actions_sales.milestone(db, order_id, "order_approved", "Order approved", approved_by)
            com = fulfillment_engine.receive(db, tenant_id, order, execution_id, pattern)
            result = {"order_id": order.id, "order_status": order.status.value, "fulfillment_order": com.number, "fulfillment_status": com.status,
                      "fulfillment_pattern": pattern.name, "planned_by": com.planned_by}
            event_bus.publish(db, execution_id, "order.sent_to_orchestration", result)
            return result

        def deterministic() -> list[SagaStep]:
            def sub1() -> dict[str, Any]:
                return fulfillment_ops.op_submit_order_to_orchestration(db, tenant_id, order_id)

            def sub2() -> dict[str, Any]:
                return fulfillment_ops.op_create_work_order(
                    db, tenant_id, order_id, "installation", first_item.id if first_item else None
                )

            return [SagaStep("submit_order_to_orchestration", sub1), SagaStep("create_installation_work_order", sub2)]

        result = _run_consequence(
            db,
            tenant_id,
            "INT-25",
            "The order has been approved by the Order Control Unit. Submit it to the downstream Order "
            "Orchestration system, then create the first work order (kind='installation') against it.",
            {"order_id": order_id, "order_item_id": first_item.id if first_item else None, **state["approve_result"]},
            ["submit_order_to_orchestration", "create_work_order"],
            deterministic(),
        )
        approved_by = "auto-approved" if state["approve_result"].get("auto_approved") else state["approve_result"].get("decided_tier") or ""
        actions_sales.milestone(db, order_id, "order_approved", "Order approved", approved_by)
        actions_sales.milestone(db, order_id, "sent_to_osm", "Sent to order fulfillment", str(result.get("orchestration_ref", "")))
        actions_sales.milestone(db, order_id, "wfm1_generated", "WFM1 (installation) raised")
        if result.get("work_order_id"):
            process_service.hand_to_role(db, tenant_id, "order", order_id, "bsd", "Installation (WFM1)", "process")
        event_bus.publish(db, execution_id, "order.approved_and_submitted", result)
        return result

    return [SagaStep("approve_order", step1), SagaStep("submit_and_raise_installation", step2)]


def build_int26_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-26 - Close Work Order 1 (Installation). Records the finalized
    technical parameters (checked at both field and core-network level),
    closes the work order, then - respecting OrchestrationPolicy -
    issues the network activation command and raises Work Order 2
    (connectivity_test).
    """

    work_order_id = entities["work_order_id"]
    technical_parameters = entities.get("technical_parameters", "")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        if technical_parameters:
            fulfillment_ops.op_record_technical_parameters(db, tenant_id, work_order_id, technical_parameters)
        result = fulfillment_ops.op_close_work_order(db, tenant_id, work_order_id)
        state["close_result"] = result
        return result

    def step2() -> dict[str, Any]:
        close_result = state["close_result"]
        order_id = close_result["order_id"]
        order_item_id = close_result["order_item_id"]

        def deterministic() -> list[SagaStep]:
            def sub1() -> dict[str, Any]:
                return fulfillment_ops.op_activate_network(db, tenant_id, order_item_id)

            def sub2() -> dict[str, Any]:
                return fulfillment_ops.op_create_work_order(db, tenant_id, order_id, "connectivity_test", order_item_id)

            return [SagaStep("activate_network", sub1), SagaStep("create_connectivity_test_work_order", sub2)]

        result = _run_consequence(
            db,
            tenant_id,
            "INT-26",
            "Work order 1 (installation) has closed with its technical parameters finalized. Issue the "
            "network activation command for this order item, then create the next work order "
            "(kind='connectivity_test').",
            {**close_result},
            ["activate_network", "create_work_order"],
            deterministic(),
        )
        actions_sales.milestone(db, order_id, "wfm1_closed", "WFM1 closed - installation complete")
        actions_sales.milestone(db, order_id, "network_activated", "Network provisioning done", str(result.get("network_activation_ref", "")))
        actions_sales.milestone(db, order_id, "wfm2_generated", "WFM2 (testing) raised")
        event_bus.publish(db, execution_id, "work_order.installation_closed", result)
        return result

    return [SagaStep("close_installation_work_order", step1), SagaStep("activate_network_and_raise_connectivity_test", step2)]


def build_int27_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-27 - Close Work Order 2 (Connectivity Test & Acceptance).
    Records the customer's signed acceptance of the tested connection,
    closes the work order, then raises Work Order 3 (acceptance_letter).
    """

    work_order_id = entities["work_order_id"]
    signature = entities.get("signature", "")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        fulfillment_ops.op_record_connectivity_acceptance(db, tenant_id, work_order_id, signature)
        result = fulfillment_ops.op_close_work_order(db, tenant_id, work_order_id)
        state["close_result"] = result
        return result

    def step2() -> dict[str, Any]:
        close_result = state["close_result"]
        order_id = close_result["order_id"]
        order_item_id = close_result["order_item_id"]

        def deterministic() -> list[SagaStep]:
            def sub1() -> dict[str, Any]:
                return fulfillment_ops.op_create_work_order(db, tenant_id, order_id, "acceptance_letter", order_item_id)

            return [SagaStep("create_acceptance_letter_work_order", sub1)]

        result = _run_consequence(
            db,
            tenant_id,
            "INT-27",
            "Work order 2 (connectivity test & acceptance) has closed with the customer's signed "
            "acceptance on file. Create the next work order (kind='acceptance_letter').",
            {**close_result},
            ["create_work_order"],
            deterministic(),
        )
        actions_sales.milestone(db, order_id, "wfm2_closed", "WFM2 closed - service tested with the customer", signature)
        actions_sales.milestone(db, order_id, "esan_initiated", "ESAN initiated")
        actions_sales.milestone(db, order_id, "wfm3_generated", "WFM3 (ESAN / acceptance letter) raised")
        event_bus.publish(db, execution_id, "work_order.connectivity_test_closed", result)
        return result

    return [SagaStep("close_connectivity_test_work_order", step1), SagaStep("raise_acceptance_letter_work_order", step2)]


def build_int28_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-28 - Close Work Order 3 (Acceptance Letter). Attaches the
    physical copy of the signed acceptance letter, closes the work
    order, then - fulfillment complete - initiates billing, creates the
    Asset (this platform's own CMDB-style copy, synced to the external
    inventory system), activates the order, and converts the customer's
    Party record from prospect to customer.
    """

    work_order_id = entities["work_order_id"]
    document_ref = entities.get("document_ref")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        fulfillment_ops.op_attach_acceptance_letter(db, tenant_id, work_order_id, document_ref)
        result = fulfillment_ops.op_close_work_order(db, tenant_id, work_order_id)
        state["close_result"] = result
        return result

    def step2() -> dict[str, Any]:
        close_result = state["close_result"]
        order_id = close_result["order_id"]
        order_item_id = close_result["order_item_id"]
        order = db.get(Order, order_id)
        order_item = db.get(OrderItem, order_item_id) if order_item_id else None
        offering = db.get(ProductOffering, order_item.product_offering_id) if order_item else None
        asset_type = offering.name if offering else "Service"
        identifier = f"ORDER-{order_id}-ITEM-{order_item_id or 0}"

        def deterministic() -> list[SagaStep]:
            def sub1() -> dict[str, Any]:
                return fulfillment_ops.op_initiate_billing(db, tenant_id, order.service_account_id)

            def sub2() -> dict[str, Any]:
                return fulfillment_ops.op_create_asset(db, tenant_id, order.service_account_id, order_item_id, asset_type, identifier)

            def sub3() -> dict[str, Any]:
                return fulfillment_ops.op_activate_order(db, tenant_id, order_id)

            def sub4() -> dict[str, Any]:
                return fulfillment_ops.op_convert_prospect_to_customer(db, tenant_id, order.party_id)

            return [
                SagaStep("initiate_billing", sub1),
                SagaStep("create_asset", sub2),
                SagaStep("activate_order", sub3),
                SagaStep("convert_prospect_to_customer", sub4),
            ]

        result = _run_consequence(
            db,
            tenant_id,
            "INT-28",
            "Work order 3 (acceptance letter) has closed - fulfillment is complete. Initiate billing for "
            f"service account {order.service_account_id}, create an Asset record (asset_type='{asset_type}', "
            f"identifier='{identifier}') under it, activate the order, and convert party {order.party_id} "
            "from prospect to customer.",
            {**close_result, "service_account_id": order.service_account_id, "party_id": order.party_id,
             "asset_type": asset_type, "identifier": identifier},
            ["initiate_billing", "create_asset", "activate_order", "convert_prospect_to_customer"],
            deterministic(),
        )
        actions_sales.milestone(db, order_id, "esan_signed", "ESAN signed - WFM3 closed", document_ref or "")
        actions_sales.milestone(db, order_id, "billing_started", "Billing started", str(result.get("billing_initiation_ref", "")))
        actions_sales.milestone(db, order_id, "assets_created", "Assets created", str(result.get("inventory_system_ref", "")))
        actions_sales.milestone(db, order_id, "live", "Service live")
        event_bus.publish(db, execution_id, "order.fulfilled", result)
        return result

    return [SagaStep("close_acceptance_letter_work_order", step1), SagaStep("finalize_fulfillment", step2)]
