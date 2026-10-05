"""Per-intent orchestration step sequences for INT-07/08/09 (Sprint 3,
Order-to-Activate) - directly from HLD Section 7's "Orchestration Steps".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import account_service, order_service, sla_service
from app.data_intelligence import event_bus
from app.integration import order_adapter
from app.models.catalog import ProductOffering
from app.models.crm import Opportunity, Quote, QuoteItem
from app.models.orders import Order, OrderStatus, OrderType
from app.models.sla import SlaClock
from app.orchestration.orchestrator import SagaStep
from app.timeutils import utcnow


def build_int07_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-07 - Activate this order end-to-end."""

    quote_id = entities["quote_id"]
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        quote = db.get(Quote, quote_id)
        opportunity = db.get(Opportunity, quote.opportunity_id)
        lead = opportunity.lead
        item = db.query(QuoteItem).filter(QuoteItem.quote_id == quote.id).first()
        offering = db.get(ProductOffering, item.product_offering_id)
        party_id = lead.party_id

        # 3-tier account hierarchy (Phase 1 primitive #5): resolve/create the
        # billing account and the service account this order belongs to
        # before the order itself - a ServiceAccount id is required on
        # every Order, not assigned after the fact. Billing account
        # creation moved here from step5 (which still flips it to "active"
        # once provisioning completes); get_or_create is idempotent so this
        # is a no-op on every order after a party's first.
        billing_account = order_adapter.get_or_create_billing_account(db, tenant_id, party_id)
        service_account_name = entities.get("service_account_name") or f"{offering.name} Service"
        service_account = account_service.get_or_create_service_account(
            db, tenant_id, billing_account.id, service_account_name
        )

        order = order_adapter.create_order(
            db,
            tenant_id=tenant_id,
            party_id=party_id,
            service_account_id=service_account.id,
            quote_id=quote.id,
            order_type=OrderType.new,
            monthly_price=quote.total_price,
        )
        order_adapter.add_order_item(db, order.id, offering.id, item.quantity)
        state["order"] = order
        state["offering"] = offering
        state["party_id"] = party_id
        state["billing_account_id"] = billing_account.id
        state["service_account"] = service_account
        return {
            "order_id": order.id,
            "service_account_id": service_account.id,
            "service_account_name": service_account.name,
        }

    def step2() -> dict[str, Any]:
        tasks = order_service.decompose_tasks(state["offering"].composition)
        state["tasks"] = tasks
        return {"tasks": tasks}

    def step3() -> dict[str, Any]:
        so = order_adapter.create_service_order(db, state["order"].id)
        state["service_order_id"] = so.id
        if "network" in state["tasks"]:
            order_adapter.add_resource(db, so.id, "network_path", order_service.reserve_network_path())
        return {"service_order_id": so.id}

    def step4() -> dict[str, Any]:
        so_id = state["service_order_id"]
        if "mobile_core" in state["tasks"]:
            order_adapter.add_resource(db, so_id, "sim_profile", order_service.allocate_sim_profile())
        order_adapter.add_resource(db, so_id, "ip_block", order_service.allocate_ip_block())
        order_adapter.activate_service_order(db, so_id)
        return {"provisioned": True}

    def step5() -> dict[str, Any]:
        # Billing account was already resolved/created in step1 - just flip
        # it active now that provisioning has actually completed.
        order_adapter.set_billing_account_status(db, state["billing_account_id"], "active", rate_plan=state["offering"].name)
        return {"billing_account_id": state["billing_account_id"]}

    def step6() -> dict[str, Any]:
        from app.integration import ticket_adapter

        if "field_service" in state["tasks"]:
            ticket_adapter.create_work_order(
                db, tenant_id=tenant_id, ticket_id=None, order_id=state["order"].id, scheduled_window="next business day"
            )
        order_adapter.set_order_status(db, state["order"].id, OrderStatus.active)
        event_bus.publish(db, execution_id, "order.activated", {"order_id": state["order"].id})
        return {"order_id": state["order"].id, "status": OrderStatus.active.value}

    return [
        SagaStep("convert_quote_to_order", step1),
        SagaStep("decompose_task_plan", step2),
        SagaStep("reserve_network_capacity", step3),
        SagaStep("provision_resources", step4),
        SagaStep("activate_billing", step5),
        SagaStep("schedule_field_dispatch", step6),
    ]


def build_int08_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-08 - Change this live service."""

    order_id = entities["order_id"]
    new_monthly_price = float(entities["new_monthly_price"])
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        order = db.get(Order, order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        state["order"] = order
        state["old_price"] = order.monthly_price
        return {"order_id": order.id, "current_price": order.monthly_price}

    def step2() -> dict[str, Any]:
        # Stand-in feasibility check for the new configuration.
        return {"feasible": True}

    def step3() -> dict[str, Any]:
        delta = round(new_monthly_price - state["old_price"], 2)
        state["delta"] = delta
        order = state["order"]
        order.monthly_price = new_monthly_price
        order.status = OrderStatus.changed
        db.flush()
        return {"delta": delta}

    def step4() -> dict[str, Any]:
        service_orders = state["order"].service_orders
        if service_orders:
            for resource in list(service_orders[0].resources):
                if resource.resource_type == "network_path":
                    resource.identifier = order_service.reserve_network_path()
        db.flush()
        return {"provisioning_delta_applied": True}

    def step5() -> dict[str, Any]:
        account = order_adapter.get_or_create_billing_account(db, tenant_id, state["order"].party_id)
        order_adapter.apply_billing_adjustment(db, account.id, order_id, state["delta"], "Service change proration")
        event_bus.publish(db, execution_id, "order.change_requested", {"order_id": order_id, "delta": state["delta"]})
        return {"order_id": order_id, "new_monthly_price": new_monthly_price, "status": OrderStatus.changed.value}

    return [
        SagaStep("validate_change_against_contract", step1),
        SagaStep("feasibility_check_new_configuration", step2),
        SagaStep("generate_change_order", step3),
        SagaStep("orchestrate_provisioning_delta", step4),
        SagaStep("update_inventory_and_billing", step5),
    ]


def _finalize_cancellation(db: Session, tenant_id: int, correlation_id: int, order_id: int, *, action: str) -> dict[str, Any]:
    """Actually cancel/terminate an order: deprovision, apply the early
    termination fee, close billing. Called either directly (terminate -
    always immediate; cancel - immediately too, if no retention policy is
    configured for this tenant) or by _handle_retention_breach once a
    retention window lapses with no retention offer accepted.
    """
    order = db.get(Order, order_id)
    months_elapsed = max(0, (utcnow() - order.created_at).days // 30)
    fee = order_service.calculate_early_termination_fee(order.monthly_price, order.contract_months, months_elapsed)
    for so in order.service_orders:
        order_adapter.release_service_order(db, so.id)
    account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
    if fee > 0:
        order_adapter.apply_billing_adjustment(db, account.id, order.id, fee, f"Early termination fee ({action})")
    new_status = OrderStatus.cancelled if action == "cancel" else OrderStatus.terminated
    order_adapter.set_order_status(db, order.id, new_status)
    order_adapter.set_billing_account_status(db, account.id, "closed")
    event_bus.publish(db, correlation_id, "order.cancelled", {"order_id": order.id, "action": action})
    return {"order_id": order.id, "status": new_status.value, "early_termination_fee": fee}


def _escalate_suspension(db: Session, tenant_id: int, correlation_id: int, order_id: int) -> dict[str, Any]:
    """The hard end of the soft-suspend -> hard-suspend BRD pattern: if a
    suspension's grace period lapses with nothing resolved, the order
    terminates outright. Called by _handle_suspension_breach.
    """
    order = db.get(Order, order_id)
    for so in order.service_orders:
        order_adapter.release_service_order(db, so.id)
    account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
    months_elapsed = max(0, (utcnow() - order.created_at).days // 30)
    fee = order_service.calculate_early_termination_fee(order.monthly_price, order.contract_months, months_elapsed)
    if fee > 0:
        order_adapter.apply_billing_adjustment(db, account.id, order.id, fee, "Termination after suspension grace period lapsed")
    order_adapter.set_order_status(db, order.id, OrderStatus.terminated)
    order_adapter.set_billing_account_status(db, account.id, "closed")
    event_bus.publish(db, correlation_id, "order.terminated", {"order_id": order.id, "reason": "suspension_grace_expired"})
    return {"order_id": order.id, "status": OrderStatus.terminated.value, "early_termination_fee": fee}


def _handle_retention_breach(db: Session, clock: SlaClock, correlation_id: int) -> dict[str, Any]:
    order = db.get(Order, clock.entity_id)
    if order is None or order.status != OrderStatus.pending_cancellation:
        return {"outcome": "skipped", "detail": "Order is no longer pending cancellation - nothing to do."}
    result = _finalize_cancellation(db, clock.tenant_id, correlation_id, order.id, action="cancel")
    return {
        "outcome": "cancelled",
        "detail": f"Retention window lapsed with no offer accepted - order {order.id} cancelled "
        f"(fee {result['early_termination_fee']}).",
    }


def _handle_suspension_breach(db: Session, clock: SlaClock, correlation_id: int) -> dict[str, Any]:
    order = db.get(Order, clock.entity_id)
    if order is None or order.status != OrderStatus.suspended:
        return {"outcome": "skipped", "detail": "Order is no longer suspended - nothing to do."}
    result = _escalate_suspension(db, clock.tenant_id, correlation_id, order.id)
    return {
        "outcome": "terminated",
        "detail": f"Suspension grace period lapsed - order {order.id} terminated (fee {result['early_termination_fee']}).",
    }


sla_service.register_breach_handler("retention_window", _handle_retention_breach)
sla_service.register_breach_handler("suspension_grace", _handle_suspension_breach)


def build_int09_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-09 - Suspend, cancel or terminate.

    "terminate" (for-cause) stays immediate, same as before. "suspend"
    degrades the service immediately as before, but now also starts a
    grace-period SLA clock - if a tenant has configured one and nobody
    resolves things, _handle_suspension_breach escalates to terminated.
    "cancel" no longer cancels on the spot: if a tenant has a
    retention_window SLA policy configured, it opens that window instead
    (order -> pending_cancellation) and only actually cancels if the
    window lapses (_handle_retention_breach) or a later call passes
    decision: retain, which restores the order to active. A tenant with
    no retention policy configured keeps the old immediate-cancel
    behavior - this is purely additive.
    """

    order_id = entities["order_id"]
    action = entities.get("action", "cancel")
    decision = entities.get("decision")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        order = db.get(Order, order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        state["order"] = order
        return {"order_id": order.id, "action": action}

    def step2() -> dict[str, Any]:
        order = state["order"]

        if action == "terminate":
            return _finalize_cancellation(db, tenant_id, execution_id, order.id, action="terminate")

        if action == "cancel":
            if order.status == OrderStatus.pending_cancellation:
                if decision == "retain":
                    sla_service.complete_clock(db, tenant_id, "retention_window", "order", order.id)
                    order_adapter.set_order_status(db, order.id, OrderStatus.active)
                    event_bus.publish(db, execution_id, "order.retained", {"order_id": order.id})
                    return {"order_id": order.id, "status": OrderStatus.active.value, "retained": True}
                # Already in the window from an earlier call - no-op, don't restart the clock.
                return {"order_id": order.id, "status": OrderStatus.pending_cancellation.value}

            clock = sla_service.start_clock(db, tenant_id, "retention_window", "order", order.id)
            if clock is None:
                # No retention policy configured for this tenant - cancel immediately.
                return _finalize_cancellation(db, tenant_id, execution_id, order.id, action="cancel")
            order_adapter.set_order_status(db, order.id, OrderStatus.pending_cancellation)
            event_bus.publish(
                db, execution_id, "order.retention_window_opened", {"order_id": order.id, "deadline": clock.deadline.isoformat()}
            )
            return {
                "order_id": order.id,
                "status": OrderStatus.pending_cancellation.value,
                "retention_window_deadline": clock.deadline.isoformat(),
            }

        # suspend
        if order.status == OrderStatus.suspended:
            return {"order_id": order.id, "status": OrderStatus.suspended.value, "early_termination_fee": 0.0}
        order_adapter.set_order_status(db, order.id, OrderStatus.suspended)
        account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
        order_adapter.set_billing_account_status(db, account.id, "suspended")
        clock = sla_service.start_clock(db, tenant_id, "suspension_grace", "order", order.id)
        event_bus.publish(db, execution_id, "order.suspended", {"order_id": order.id})
        result = {"order_id": order.id, "status": OrderStatus.suspended.value, "early_termination_fee": 0.0}
        if clock is not None:
            result["suspension_grace_deadline"] = clock.deadline.isoformat()
        return result

    return [
        SagaStep("validate_contract_and_notice_period", step1),
        SagaStep("process_lifecycle_action", step2),
    ]
