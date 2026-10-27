"""INT-14 - Provision this site (Phase 3: B2B multi-site process pack).

Each site under a multi-site order (see actions_o2a.py's INT-07, which
creates one ServiceOrder per site when given a "sites" entity) is
provisioned independently by this intent - matching the BRD's IPLC
A-End/Z-End pattern, where each end's feasibility and installation
genuinely happens on its own schedule, not atomically with the other.
Once every site under an order is provisioned, the order itself goes
active and billing starts - mirroring INT-07's single-site finale, just
triggered by whichever site call happens to finish last instead of all
at once in one call.

Gated to b2b/both tenants - see app/intent_engine/reasoning.py.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import order_service
from app.data_intelligence import event_bus
from app.integration import order_adapter
from app.models.catalog import ProductOffering
from app.models.orders import Order, OrderItem, OrderStatus, ServiceOrder, ServiceOrderStatus
from app.orchestration.orchestrator import SagaStep


def build_int14_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    order_id = entities["order_id"]
    site_label = entities["site_label"]
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        order = db.get(Order, order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        so = (
            db.query(ServiceOrder)
            .filter(ServiceOrder.order_id == order_id, ServiceOrder.site_label == site_label)
            .first()
        )
        if so is None:
            raise ValueError(f"No site '{site_label}' found under order {order_id}")
        if so.status == ServiceOrderStatus.active:
            raise ValueError(f"Site '{site_label}' is already provisioned")
        state["order"] = order
        state["service_order"] = so
        return {"order_id": order.id, "service_order_id": so.id, "site_label": site_label}

    def step2() -> dict[str, Any]:
        order = state["order"]
        so = state["service_order"]
        item = db.query(OrderItem).filter(OrderItem.order_id == order.id).first()
        offering = db.get(ProductOffering, item.product_offering_id)
        tasks = order_service.decompose_tasks(offering.composition)
        if "network" in tasks:
            order_adapter.add_resource(db, so.id, "network_path", order_service.reserve_network_path())
        if "mobile_core" in tasks:
            order_adapter.add_resource(db, so.id, "sim_profile", order_service.allocate_sim_profile())
        order_adapter.add_resource(db, so.id, "ip_block", order_service.allocate_ip_block())
        order_adapter.activate_service_order(db, so.id)
        state["offering"] = offering
        return {"service_order_id": so.id, "site_label": site_label, "status": "active"}

    def step3() -> dict[str, Any]:
        order = state["order"]
        remaining = (
            db.query(ServiceOrder)
            .filter(ServiceOrder.order_id == order.id, ServiceOrder.status != ServiceOrderStatus.active)
            .count()
        )
        if remaining == 0:
            account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
            order_adapter.set_billing_account_status(db, account.id, "active", rate_plan=state["offering"].name)
            order_adapter.set_order_status(db, order.id, OrderStatus.active)
            event_bus.publish(db, execution_id, "order.activated", {"order_id": order.id, "all_sites_provisioned": True})
            return {
                "order_id": order.id,
                "site_label": site_label,
                "order_status": order.status.value,
                "all_sites_provisioned": True,
            }

        event_bus.publish(
            db, execution_id, "site.provisioned", {"order_id": order.id, "site_label": site_label, "remaining_sites": remaining}
        )
        return {
            "order_id": order.id,
            "site_label": site_label,
            "order_status": order.status.value,
            "all_sites_provisioned": False,
            "remaining_sites": remaining,
        }

    return [
        SagaStep("resolve_site", step1),
        SagaStep("provision_site", step2),
        SagaStep("check_order_completion", step3),
    ]
