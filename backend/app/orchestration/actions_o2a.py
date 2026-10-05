"""Per-intent orchestration step sequences for INT-07/08/09 (Sprint 3,
Order-to-Activate) - directly from HLD Section 7's "Orchestration Steps".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import order_service
from app.data_intelligence import event_bus
from app.integration import order_adapter
from app.models.catalog import ProductOffering
from app.models.crm import Opportunity, Quote, QuoteItem
from app.models.orders import Order, OrderStatus, OrderType
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

        order = order_adapter.create_order(
            db,
            tenant_id=tenant_id,
            party_id=lead.party_id,
            quote_id=quote.id,
            order_type=OrderType.new,
            monthly_price=quote.total_price,
        )
        order_adapter.add_order_item(db, order.id, offering.id, item.quantity)
        state["order"] = order
        state["offering"] = offering
        state["party_id"] = lead.party_id
        return {"order_id": order.id}

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
        account = order_adapter.get_or_create_billing_account(db, tenant_id, state["party_id"])
        order_adapter.set_billing_account_status(db, account.id, "active", rate_plan=state["offering"].name)
        state["billing_account_id"] = account.id
        return {"billing_account_id": account.id}

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


def build_int09_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-09 - Suspend, cancel or terminate."""

    order_id = entities["order_id"]
    action = entities.get("action", "cancel")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        order = db.get(Order, order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        state["order"] = order
        return {"order_id": order.id, "action": action}

    def step2() -> dict[str, Any]:
        order = state["order"]
        if action in ("cancel", "terminate"):
            months_elapsed = max(0, (utcnow() - order.created_at).days // 30)
            fee = order_service.calculate_early_termination_fee(order.monthly_price, order.contract_months, months_elapsed)
        else:
            fee = 0.0
        state["fee"] = fee
        return {"early_termination_fee": fee}

    def step3() -> dict[str, Any]:
        order = state["order"]
        if action in ("cancel", "terminate"):
            for so in order.service_orders:
                order_adapter.release_service_order(db, so.id)
        return {"deprovisioned": action in ("cancel", "terminate")}

    def step4() -> dict[str, Any]:
        order = state["order"]
        account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
        if state["fee"] > 0:
            order_adapter.apply_billing_adjustment(db, account.id, order_id, state["fee"], f"Early termination fee ({action})")
        new_status = {"suspend": OrderStatus.suspended, "cancel": OrderStatus.cancelled, "terminate": OrderStatus.terminated}[action]
        order_adapter.set_order_status(db, order.id, new_status)
        order_adapter.set_billing_account_status(db, account.id, "suspended" if action == "suspend" else "closed")
        event_bus.publish(db, execution_id, "order.cancelled", {"order_id": order.id, "action": action})
        return {"order_id": order.id, "status": new_status.value, "early_termination_fee": state["fee"]}

    return [
        SagaStep("validate_contract_and_notice_period", step1),
        SagaStep("calculate_termination_fee", step2),
        SagaStep("deprovision_resources", step3),
        SagaStep("update_billing_and_inventory", step4),
    ]
