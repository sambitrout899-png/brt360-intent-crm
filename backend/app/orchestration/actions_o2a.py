"""Per-intent orchestration step sequences for INT-07/08/09 (Sprint 3,
Order-to-Activate) - directly from HLD Section 7's "Orchestration Steps".
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import abo_service, account_service, order_service, ponr_service, sla_service
from app.crm_core import product_config_service as product_config
from app.data_intelligence import event_bus
from app.integration import order_adapter
from app.models.catalog import ProductOffering
from app.models.crm import Opportunity, Quote, QuoteItem
from app.models.orders import Order, OrderItem, OrderStatus, OrderType
from app.models.sla import SlaClock
from app.orchestration.orchestrator import SagaStep
from app.timeutils import utcnow


def _consumer_order(db: Session, tenant_id: int, entities: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """INT-07 without a quote: a consumer order, created here (or, with
    _approved_order_id, the one whose discount was just approved) - fills
    `state` the way the quote path does, so the same activation runs."""
    from app.crm_core import approval_service, catalog_service, process_service
    from app.integration import tmf620_adapter
    from app.models.crm import Party

    approved_id = entities.get("_approved_order_id")
    if approved_id:
        order = db.get(Order, int(approved_id))
        item = order.items[0]
        state.update(order=order, offering=db.get(ProductOffering, item.product_offering_id), party_id=order.party_id,
                     billing_account_id=order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id).id,
                     sites=["Primary"])
        return {"order_id": order.id, "service_account_id": order.service_account_id}

    try:
        party = db.get(Party, int(entities.get("party_id")))
    except (TypeError, ValueError):
        party = None
    if party is None or party.tenant_id != tenant_id:
        raise ValueError("Pick the customer (or the approved quote) to place the order for")
    offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, entities.get("offering_name") or "")
    if offering is None:
        raise ValueError(tmf620_adapter.not_sellable(db, tenant_id, entities.get("offering_name") or ""))
    quantity = int(entities.get("quantity") or 1)
    discount = float(entities.get("requested_discount_pct") or 0)
    price_list = product_config.price_list_for(db, tenant_id, party, explicit=entities.get("price_list"))
    base_price, _ = product_config.base_price(db, tenant_id, price_list, offering, quantity)
    configured = product_config.configure(db, tenant_id, offering.name, entities.get("pricing_attributes"),
                                          entities.get("technical_attributes"), base_price, party=party, price_list=price_list)
    ceiling = offering.price_plan.discount_ceiling_pct if offering.price_plan else 100.0
    unit_price = catalog_service.compute_price(configured["configured"], discount, ceiling, offering.price_floor)

    billing_account = order_adapter.get_or_create_billing_account(db, tenant_id, party.id)
    if entities.get("service_account_id"):
        from app.crm_core.abo_service import account
        service_account, _ = account(db, tenant_id, int(entities["service_account_id"]), party.id)
    else:
        service_account = account_service.get_or_create_service_account(
            db, tenant_id, billing_account.id, entities.get("service_account_name") or f"{offering.name} Service")
    order = order_adapter.create_order(
        db, tenant_id=tenant_id, party_id=party.id, service_account_id=service_account.id, quote_id=None,
        order_type=OrderType.new, monthly_price=round(unit_price * quantity, 2),
    )
    segment = process_service.party_segment(party) or "B2C"
    profile = process_service.order_profile(order, create=True)
    profile.segment, profile.requested_discount_pct = segment, discount
    order_item = order_adapter.add_order_item(db, order.id, offering.id, quantity)
    order_item.unit_price, order_item.discount_pct, order_item.net_unit_price = configured["configured"], discount, unit_price
    if configured["designed"] or configured["pricing"] or configured["technical"]:
        from app.crm_core import instance_service
        from app.models.product_config import OrderItemConfig

        db.add(OrderItemConfig(order_item_id=order_item.id, pricing_attributes_json=json.dumps(configured["pricing"]),
                               technical_attributes_json=json.dumps(configured["technical"])))
        instance_service.record(instance_service.take(db, tenant_id, "order", order.id, offering.name, item_id=order_item.id), configured, quantity)
        db.flush()
    state.update(order=order, offering=offering, party_id=party.id, billing_account_id=billing_account.id,
                 service_account=service_account, sites=["Primary"])
    out = {"order_id": order.id, "party_id": party.id, "service_account_id": service_account.id,
           "service_account_name": service_account.name, "monthly_price": order.monthly_price}

    rule = approval_service.resolve_monetary(db, tenant_id, "order", discount, segment, order.monthly_price)
    if rule is not None and discount > 0:
        order.status = OrderStatus.pending_approval
        instance = approval_service.instantiate_chain(
            db, "order", order.id, rule, gate="discount",
            summary=f"Order #{order.id}: {discount:g}% discount, {order.monthly_price:,.2f}/month",
            requested_by_user_id=int(entities["_actor_user_id"]) if entities.get("_actor_user_id") else None,
        )
        db.flush()
        state["waiting"] = True
        out.update(status=order.status.value, approval_id=instance.id, waiting_for=approval_service.waiting_for(instance))
    return out


def build_int07_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-07 - Activate this order end-to-end.

    Default (no "sites", or exactly one): single ServiceOrder, provisioned
    and activated in this one call - unchanged from before. Multi-site
    (two or more "sites", e.g. an IPLC circuit's A-End/Z-End - Phase 3 B2B
    process pack): creates one ServiceOrder per site, all left pending -
    the order stays "activating" and billing stays inactive until every
    site is independently provisioned via INT-14 ("Provision this site").

    Consumer (B2C) order, no quote: a customer (party_id) and a product
    (offering_name) place the order directly. When the discount asked for
    falls under a monetary approval rule on orders, the order waits
    (pending_approval) and activates once approved (see actions_sales).
    """

    quote_id = entities.get("quote_id")
    sites = entities.get("sites") or []
    multi_site = len(sites) > 1
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        if not quote_id:
            return _consumer_order(db, tenant_id, entities, state)
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
        if entities.get("service_account_id"):
            from app.crm_core.abo_service import account
            service_account, _ = account(db, tenant_id, int(entities["service_account_id"]), party_id)

        order = order_adapter.create_order(
            db,
            tenant_id=tenant_id,
            party_id=party_id,
            service_account_id=service_account.id,
            quote_id=quote.id,
            order_type=OrderType.new,
            monthly_price=quote.total_price,
        )
        order_item = order_adapter.add_order_item(db, order.id, offering.id, item.quantity)
        product_config.carry_to_order(db, tenant_id, item, order_item, opportunity)
        state["order"] = order
        state["offering"] = offering
        state["party_id"] = party_id
        state["billing_account_id"] = billing_account.id
        state["service_account"] = service_account
        state["sites"] = sites if multi_site else ["Primary"]
        return {
            "order_id": order.id,
            "service_account_id": service_account.id,
            "service_account_name": service_account.name,
        }

    def step2() -> dict[str, Any]:
        if state.get("waiting"):
            return {}
        tasks = order_service.decompose_tasks(state["offering"].composition)
        state["tasks"] = tasks
        return {"tasks": tasks}

    def step3() -> dict[str, Any]:
        if state.get("waiting"):
            return {}
        service_order_ids = []
        for site in state["sites"]:
            so = order_adapter.create_service_order(db, state["order"].id, site_label=site)
            service_order_ids.append(so.id)
            if not multi_site and "network" in state["tasks"]:
                order_adapter.add_resource(db, so.id, "network_path", order_service.reserve_network_path())
        state["service_order_ids"] = service_order_ids
        return {"service_order_ids": service_order_ids, "sites": state["sites"]}

    def step4() -> dict[str, Any]:
        if state.get("waiting"):
            return {}
        if multi_site:
            # Each site is provisioned independently, later, via INT-14 -
            # not all at once here.
            return {"provisioned": False, "note": "Multi-site order - provision each site independently via INT-14."}
        so_id = state["service_order_ids"][0]
        if "mobile_core" in state["tasks"]:
            order_adapter.add_resource(db, so_id, "sim_profile", order_service.allocate_sim_profile())
        order_adapter.add_resource(db, so_id, "ip_block", order_service.allocate_ip_block())
        order_adapter.activate_service_order(db, so_id)
        out: dict[str, Any] = {"provisioned": True}
        attrs = product_config.order_item_attributes(db, state["order"].items[0].id if state["order"].items else None)
        if attrs is not None and attrs[1]:
            out["provisioning_attributes"] = attrs[1]  # what the service was provisioned with
        return out

    def step5() -> dict[str, Any]:
        if state.get("waiting"):
            return {}
        if multi_site:
            return {"billing_account_id": state["billing_account_id"], "billing_activated": False}
        # Billing account was already resolved/created in step1 - just flip
        # it active now that provisioning has actually completed.
        order_adapter.set_billing_account_status(db, state["billing_account_id"], "active", rate_plan=state["offering"].name)
        return {"billing_account_id": state["billing_account_id"], "billing_activated": True}

    def step6() -> dict[str, Any]:
        from app.integration import erp_adapter, orchestration_adapter, provisioning_adapter, ticket_adapter

        if state.get("waiting"):
            return {}
        if multi_site:
            order_adapter.set_order_status(db, state["order"].id, OrderStatus.activating)
            event_bus.publish(
                db,
                execution_id,
                "order.multi_site_provisioning_started",
                {"order_id": state["order"].id, "sites": state["sites"]},
            )
            return {
                "order_id": state["order"].id,
                "status": OrderStatus.activating.value,
                "sites": state["sites"],
                "service_order_ids": state["service_order_ids"],
            }

        if "field_service" in state["tasks"]:
            ticket_adapter.create_work_order(
                db, tenant_id=tenant_id, ticket_id=None, order_id=state["order"].id, scheduled_window="next business day"
            )
        # External system touch points (Phase 4): a real deployment hands
        # the order to an Order Orchestration system and gets a
        # provisioning confirmation back asynchronously, rather than this
        # saga doing the work itself - and syncs the newly paying
        # customer to the ERP for financial/revenue recognition purposes,
        # separate from this platform's own operational BillingAccount.
        orchestration_ref = orchestration_adapter.submit_order_for_orchestration(state["order"].id)
        provisioning_ref = provisioning_adapter.confirm_provisioning(state["service_order_ids"][0])
        erp_ref = erp_adapter.sync_customer(state["offering"].name)

        order_adapter.set_order_status(db, state["order"].id, OrderStatus.active)
        event_bus.publish(
            db,
            execution_id,
            "order.activated",
            {
                "order_id": state["order"].id,
                "orchestration_ref": orchestration_ref,
                "provisioning_ref": provisioning_ref,
                "erp_ref": erp_ref,
            },
        )
        return {
            "order_id": state["order"].id,
            "status": state["order"].status.value,
            "orchestration_ref": orchestration_ref,
            "provisioning_ref": provisioning_ref,
            "erp_ref": erp_ref,
        }

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
        order = state["order"]
        if abo_service.installed(order):
            # the service lives on as an asset: a change order from it, fulfilled now
            change = abo_service.live_change(db, tenant_id, order, "modify", {"new_monthly_price": new_monthly_price})
            state["old_price"], state["change"] = change["current_monthly"], change
            state["delta"] = round(new_monthly_price - change["current_monthly"], 2)
            return {"delta": state["delta"], "change_order_id": change["order_id"], "asset_id": change["asset_id"],
                    "direction": change.get("direction")}
        delta = round(new_monthly_price - state["old_price"], 2)
        state["delta"] = delta
        order.monthly_price = new_monthly_price
        order.status = OrderStatus.changed
        db.flush()
        return {"delta": delta}

    def step4() -> dict[str, Any]:
        if "change" in state:
            return {"provisioning_delta_applied": True, "change_order_status": state["change"]["status"]}
        service_orders = state["order"].service_orders
        if service_orders:
            for resource in list(service_orders[0].resources):
                if resource.resource_type == "network_path":
                    resource.identifier = order_service.reserve_network_path()
        db.flush()
        return {"provisioning_delta_applied": True}

    def step5() -> dict[str, Any]:
        account = order_adapter.get_or_create_billing_account(db, tenant_id, state["order"].party_id)
        if "change" in state:
            change = state["change"]
            order_adapter.apply_billing_adjustment(db, account.id, change["order_id"], state["delta"], "Service change proration")
            event_bus.publish(db, execution_id, "order.change_requested", {"order_id": order_id, "delta": state["delta"],
                                                                           "change_order_id": change["order_id"]})
            return {"order_id": order_id, "new_monthly_price": new_monthly_price, "status": OrderStatus.changed.value,
                    "change_order_id": change["order_id"], "asset_id": change["asset_id"], "direction": change.get("direction")}
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
    if abo_service.installed(order):
        try:
            result = _live_lifecycle(db, clock.tenant_id, correlation_id, order, "cancel", now=True)
        except ValueError as exc:
            return {"outcome": "skipped", "detail": str(exc)}
        return {"outcome": "cancelled", "detail": f"Retention window lapsed with no offer accepted - service of order {order.id} "
                                                  f"cancelled (fee {result['early_termination_fee']})."}
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
    if abo_service.installed(order):
        try:
            asset = abo_service.asset_of_order(db, clock.tenant_id, order.id)
        except ValueError:
            asset = None
        if asset is None or asset.status.value != "suspended":
            return {"outcome": "skipped", "detail": "The service is no longer suspended - nothing to do."}
        result = _live_lifecycle(db, clock.tenant_id, correlation_id, order, "terminate")
        return {"outcome": "terminated", "detail": f"Suspension grace period lapsed - service of order {order.id} terminated "
                                                   f"(fee {result['early_termination_fee']})."}
    if order is None or order.status != OrderStatus.suspended:
        return {"outcome": "skipped", "detail": "Order is no longer suspended - nothing to do."}
    result = _escalate_suspension(db, clock.tenant_id, correlation_id, order.id)
    return {
        "outcome": "terminated",
        "detail": f"Suspension grace period lapsed - order {order.id} terminated (fee {result['early_termination_fee']}).",
    }


sla_service.register_breach_handler("retention_window", _handle_retention_breach)
sla_service.register_breach_handler("suspension_grace", _handle_suspension_breach)


def _running_clock(db: Session, tenant_id: int, clock_type: str, order_id: int) -> SlaClock | None:
    from app.models.sla import SlaClockStatus

    return db.query(SlaClock).filter(SlaClock.tenant_id == tenant_id, SlaClock.clock_type == clock_type, SlaClock.entity_type == "order",
                                     SlaClock.entity_id == order_id, SlaClock.status == SlaClockStatus.running).first()


def _live_lifecycle(db: Session, tenant_id: int, correlation_id: int, order: Order, action: str, decision: str | None = None,
                    now: bool = False) -> dict[str, Any]:
    """Suspend / cancel / terminate the service a completed order installed:
    a change order from its asset, fulfilled now. Ending it early charges the
    early termination fee on the contract months left, as before. A cancel
    opens the tenant's retention window first (the service stays live until
    it lapses; "retain" closes it), and a suspension starts the grace clock."""
    operation = {"suspend": "suspend", "terminate": "terminate", "cancel": "disconnect", "resume": "resume"}.get(action)
    if operation is None:
        raise ValueError("The action is suspend, cancel or terminate")
    if operation == "disconnect" and not now:
        window = _running_clock(db, tenant_id, "retention_window", order.id)
        if window is not None:
            if decision == "retain":
                sla_service.complete_clock(db, tenant_id, "retention_window", "order", order.id)
                event_bus.publish(db, correlation_id, "order.retained", {"order_id": order.id})
                return {"order_id": order.id, "status": OrderStatus.active.value, "retained": True}
            return {"order_id": order.id, "status": OrderStatus.pending_cancellation.value,
                    "retention_window_deadline": window.deadline.isoformat()}
        abo_service.asset_of_order(db, tenant_id, order.id)  # a service to cancel, before a window opens for it
        clock = sla_service.start_clock(db, tenant_id, "retention_window", "order", order.id)
        if clock is not None:
            event_bus.publish(db, correlation_id, "order.retention_window_opened", {"order_id": order.id, "deadline": clock.deadline.isoformat()})
            return {"order_id": order.id, "status": OrderStatus.pending_cancellation.value, "retention_window_deadline": clock.deadline.isoformat()}
    change = abo_service.live_change(db, tenant_id, order, operation)
    account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
    fee = 0.0
    if operation in ("terminate", "disconnect"):
        months_elapsed = max(0, (utcnow() - order.created_at).days // 30)
        fee = order_service.calculate_early_termination_fee(change["current_monthly"], order.contract_months, months_elapsed)
        if fee > 0:
            order_adapter.apply_billing_adjustment(db, account.id, change["order_id"], fee, f"Early termination fee ({action})")
    status = {"suspend": "suspended", "terminate": "terminated", "disconnect": "cancelled", "resume": "active"}[operation]
    out = {"order_id": order.id, "status": status, "early_termination_fee": fee, "asset_id": change["asset_id"],
           "asset_status": change.get("asset_status"), "change_order_id": change["order_id"]}
    if operation == "suspend" and (clock := sla_service.start_clock(db, tenant_id, "suspension_grace", "order", order.id)) is not None:
        out["suspension_grace_deadline"] = clock.deadline.isoformat()
    if operation in ("terminate", "disconnect", "resume"):
        sla_service.complete_clock(db, tenant_id, "suspension_grace", "order", order.id)
    event_bus.publish(db, correlation_id, f"order.{status}", {"order_id": order.id, "asset_id": change["asset_id"],
                                                              "change_order_id": change["order_id"]})
    return out


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

        if abo_service.installed(order):
            return _live_lifecycle(db, tenant_id, execution_id, order, action, decision)

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


def build_int21_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-21 - Revise this order, before the PONR (point 5 of the Phase 4
    spec). Distinct from INT-08 "Change this live service": INT-08 is for
    an already-active order and always runs a billing proration delta;
    this intent is a plain in-place edit (no proration, no re-provisioning)
    allowed only while the order has not yet reached its configured PONR -
    by default "created"/"activating", i.e. nothing has gone live yet. An
    order past PONR must go through INT-08 or INT-09 instead.
    """

    order_id = entities["order_id"]
    new_monthly_price = entities.get("new_monthly_price")
    new_quantity = entities.get("new_quantity")

    def step1() -> dict[str, Any]:
        order = db.get(Order, order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        if ponr_service.has_reached_ponr(db, tenant_id, "order", order.status.value):
            raise ValueError(f"Order {order_id} has passed the point of no return ({order.status.value}) - cannot revise.")
        return {"order_id": order.id, "status": order.status.value}

    def step2() -> dict[str, Any]:
        order = db.get(Order, order_id)
        if new_monthly_price is not None:
            order.monthly_price = float(new_monthly_price)
        if new_quantity is not None:
            item = db.query(OrderItem).filter(OrderItem.order_id == order.id).first()
            if item is not None:
                item.quantity = int(new_quantity)
        db.flush()
        event_bus.publish(
            db, execution_id, "order.revised", {"order_id": order.id, "monthly_price": order.monthly_price}
        )
        return {"order_id": order.id, "monthly_price": order.monthly_price, "status": order.status.value}

    return [
        SagaStep("check_ponr", step1),
        SagaStep("apply_revision", step2),
    ]
