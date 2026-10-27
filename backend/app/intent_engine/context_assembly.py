"""Context Assembly Service (HLD 4.2).

Fans out to the systems of record relevant to the resolved intent and
returns one normalized context object, before any plan is proposed. HLD
specifies a parallel-fetch aggregator with a 60-120s short-lived cache to
avoid redundant calls within one intent execution; Sprint 1's "systems of
record" are all local-DB reads (no external OSS/BRM yet), so the cache here
is a simple in-process TTL dict - the interface is what matters for later
sprints, when some of these fetches become real outbound calls.

Every direct `db.get(Model, id)` below is followed by a tenant_id check - a
raw primary key lookup bypasses any query-level tenant filter, so without
this check tenant B could read tenant A's record just by guessing its id.
This is the actual data-isolation boundary, not just the list endpoints.
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.crm_core import ponr_service
from app.integration import tmf620_adapter
from app.models.assurance import TroubleTicket, WorkOrder
from app.models.catalog import BundleCompatibilityRule, ProductOffering
from app.models.crm import Lead, Opportunity, Party, Quote, TerritoryRule
from app.models.fulfillment import Proposal, ProposalStatus
from app.models.orders import Order, ServiceOrder
from app.models.tenant import Tenant, TenantVertical

_TTL_SECONDS = 90
_cache: dict[str, tuple[float, Any]] = {}

T = TypeVar("T")


def _cached(key: str, fetch) -> Any:
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _TTL_SECONDS:
        return hit[1]
    value = fetch()
    _cache[key] = (now, value)
    return value


def _get_owned(db: Session, model: type[T], tenant_id: int, obj_id: Any) -> T | None:
    """db.get() scoped to the caller's tenant - returns None (not someone
    else's row) if the id exists but belongs to a different tenant."""
    if obj_id is None:
        return None
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "tenant_id", None) != tenant_id:
        return None
    return obj


def assemble_context(db: Session, tenant_id: int, intent_type: str, entities: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {"intent_type": intent_type}

    if intent_type == "INT-01":
        segment = entities.get("segment", "B2C")
        context["compatibility_rules"] = _cached(
            f"compat_rules:{tenant_id}",
            lambda: [
                {"primary": r.primary_product_line, "addon": r.addon_product_line, "max_addons": r.max_addons}
                for r in db.query(BundleCompatibilityRule).filter(BundleCompatibilityRule.tenant_id == tenant_id).all()
            ],
        )
        context["existing_offerings_in_segment"] = [
            o.name
            for o in db.query(ProductOffering)
            .filter(ProductOffering.tenant_id == tenant_id, ProductOffering.segment == segment)
            .all()
        ]

    elif intent_type in ("INT-02", "INT-03"):
        offering_name = entities.get("offering_name", "")
        current = tmf620_adapter.get_active_offering_by_name(db, tenant_id, offering_name) if offering_name else None
        context["current_offering"] = (
            {
                "id": current.id,
                "version": current.version,
                "price": current.price,
                "segment": current.segment,
                "composition": current.composition,
            }
            if current
            else None
        )
        if intent_type == "INT-03":
            replacement_name = entities.get("replacement_offering_name")
            replacement = (
                tmf620_adapter.get_active_offering_by_name(db, tenant_id, replacement_name) if replacement_name else None
            )
            context["replacement_offering"] = {"id": replacement.id, "name": replacement.name} if replacement else None

    elif intent_type == "INT-04":
        region = entities.get("region", "ALL")
        rule = db.query(TerritoryRule).filter(TerritoryRule.tenant_id == tenant_id, TerritoryRule.region == region).first()
        context["territory_owner"] = rule.owner_name if rule else None

    elif intent_type == "INT-05":
        opportunity = _get_owned(db, Opportunity, tenant_id, entities.get("opportunity_id"))
        context["opportunity"] = {"id": opportunity.id, "stage": opportunity.stage.value} if opportunity else None
        offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, entities.get("offering_name", ""))
        context["offering"] = (
            {"id": offering.id, "price": offering.price, "price_floor": offering.price_floor} if offering else None
        )

    elif intent_type in ("INT-06", "INT-07"):
        quote = _get_owned(db, Quote, tenant_id, entities.get("quote_id"))
        context["quote"] = (
            {"id": quote.id, "status": quote.status.value, "total_price": quote.total_price, "discount_pct": quote.requested_discount_pct}
            if quote
            else None
        )

    elif intent_type in ("INT-19", "INT-20", "INT-116"):
        quote = _get_owned(db, Quote, tenant_id, entities.get("quote_id"))
        context["quote"] = (
            {"id": quote.id, "status": quote.status.value, "total_price": quote.total_price, "discount_pct": quote.requested_discount_pct}
            if quote
            else None
        )
        if quote is not None:
            context["ponr_reached"] = ponr_service.has_reached_ponr(db, tenant_id, "quote", quote.status.value)

    elif intent_type in ("INT-08", "INT-09"):
        order = _get_owned(db, Order, tenant_id, entities.get("order_id"))
        context["order"] = (
            {"id": order.id, "status": order.status.value, "monthly_price": order.monthly_price, "contract_months": order.contract_months}
            if order
            else None
        )

    elif intent_type == "INT-21":
        order = _get_owned(db, Order, tenant_id, entities.get("order_id"))
        context["order"] = (
            {"id": order.id, "status": order.status.value, "monthly_price": order.monthly_price, "contract_months": order.contract_months}
            if order
            else None
        )
        if order is not None:
            context["ponr_reached"] = ponr_service.has_reached_ponr(db, tenant_id, "order", order.status.value)

    elif intent_type == "INT-16":
        lead = _get_owned(db, Lead, tenant_id, entities.get("lead_id"))
        context["lead"] = {"id": lead.id, "status": lead.status.value} if lead else None
        offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, entities.get("offering_name", ""))
        context["offering"] = {"id": offering.id, "name": offering.name} if offering else None

    elif intent_type == "INT-17":
        lead = _get_owned(db, Lead, tenant_id, entities.get("lead_id"))
        context["lead"] = (
            {"id": lead.id, "status": lead.status.value, "interested_offering_name": lead.interested_offering_name}
            if lead
            else None
        )
        if lead is not None and lead.chain_instance is not None:
            context["chain_status"] = lead.chain_instance.status.value

    elif intent_type == "INT-18":
        opportunity = _get_owned(db, Opportunity, tenant_id, entities.get("opportunity_id"))
        context["opportunity"] = {"id": opportunity.id, "stage": opportunity.stage.value} if opportunity else None

    elif intent_type == "INT-22":
        quote = _get_owned(db, Quote, tenant_id, entities.get("quote_id"))
        context["quote"] = {"id": quote.id, "status": quote.status.value} if quote else None

    elif intent_type == "INT-23":
        proposal = _get_owned(db, Proposal, tenant_id, entities.get("proposal_id"))
        context["proposal"] = {"id": proposal.id, "status": proposal.status.value} if proposal else None

    elif intent_type == "INT-24":
        quote = _get_owned(db, Quote, tenant_id, entities.get("quote_id"))
        context["quote"] = {"id": quote.id, "status": quote.status.value} if quote else None
        if quote is not None:
            approved_proposal = (
                db.query(Proposal)
                .filter(Proposal.quote_id == quote.id, Proposal.status == ProposalStatus.customer_approved)
                .first()
            )
            context["customer_approved_proposal"] = {"id": approved_proposal.id} if approved_proposal else None
            context["po_matched"] = quote.purchase_order is not None and quote.purchase_order.matched_at is not None

    elif intent_type == "INT-25":
        order = _get_owned(db, Order, tenant_id, entities.get("order_id"))
        context["order"] = {"id": order.id, "status": order.status.value} if order else None
        if order is not None and order.chain_instance is not None:
            context["chain_status"] = order.chain_instance.status.value

    elif intent_type in ("INT-26", "INT-27", "INT-28"):
        work_order = _get_owned(db, WorkOrder, tenant_id, entities.get("work_order_id"))
        context["work_order"] = (
            {"id": work_order.id, "kind": work_order.kind, "status": work_order.status, "order_id": work_order.order_id}
            if work_order
            else None
        )

    elif intent_type == "INT-11":
        ticket = _get_owned(db, TroubleTicket, tenant_id, entities.get("ticket_id"))
        context["ticket"] = {"id": ticket.id, "status": ticket.status, "severity": ticket.severity} if ticket else None

    elif intent_type == "INT-13":
        party = _get_owned(db, Party, tenant_id, entities.get("party_id"))
        context["party"] = {"id": party.id, "name": party.name} if party else None
        tenant = db.get(Tenant, tenant_id)
        # A B2B-only tenant verifies businesses - a commercial registration
        # (checked with WATHQ) - not people's personal IDs.
        business_id = str(entities.get("id_type") or "").strip().lower().replace(" ", "_") == "commercial_registration"
        from app.crm_core.b2c_order_service import is_b2c
        if party is not None and is_b2c(party) and (business_id or str(entities.get("provider") or "").upper() == "WATHQ"):
            context["vertical_blocked"] = "B2C customers need a personal identity document and provider; commercial registration and WATHQ are for businesses."
        if tenant is not None and tenant.vertical == TenantVertical.b2b and not business_id:
            context["vertical_blocked"] = (
                "Personal ID checks (national ID, passport, iqama) are switched off - this company is set up for "
                "B2B only. To verify a business customer, choose ID type “commercial registration”."
            )

    elif intent_type == "INT-14":
        order = _get_owned(db, Order, tenant_id, entities.get("order_id"))
        context["order"] = {"id": order.id, "status": order.status.value} if order else None
        if order is not None:
            context["order_site_labels"] = [
                so.site_label for so in db.query(ServiceOrder).filter(ServiceOrder.order_id == order.id).all()
            ]
        tenant = db.get(Tenant, tenant_id)
        if tenant is not None and tenant.vertical == TenantVertical.b2c:
            context["vertical_blocked"] = (
                "Provisioning an order site by site is for business customers - this company is set up for "
                "B2C only, so its orders are provisioned in one go when they're activated."
            )

    elif intent_type in ("INT-29", "INT-31", "INT-32"):
        from app.crm_core import lifecycle_service

        entity_type = lifecycle_service.normalize_entity_type(entities.get("entity_type"))
        obj = lifecycle_service.get_record(db, tenant_id, entity_type, entities.get("entity_id")) if entity_type else None
        if obj is None:
            context["record"] = None
        else:
            editable, state = lifecycle_service.is_editable(db, tenant_id, entity_type, obj)
            context["record"] = {"entity_type": entity_type, "id": obj.id, "state": state}
            context["editable"] = editable
            if intent_type == "INT-31":
                transition, error = lifecycle_service.find_transition(
                    db, tenant_id, entity_type, obj, entities.get("action"), entities.get("to_state"), entities.get("_actor_role"),
                    entities.get("_actor_user_id"),
                )
                if transition is None:
                    context["transition_error"] = error
                elif transition.intent_type:
                    context["transition_error"] = (
                        f"'{transition.action}' is owned by {transition.intent_type} - use that intent instead"
                    )
            if intent_type == "INT-32":
                lifecycle_service.normalize_assignee(db, tenant_id, entities)
                if entities.get("to_department") and lifecycle_service.resolve_department(db, tenant_id, entities["to_department"]) is None \
                        and lifecycle_service.department_named(db, tenant_id, entities["to_department"]) is None:
                    context["unknown_position"] = entities["to_department"]
                if entities.get("to_user") and lifecycle_service.resolve_user(db, tenant_id, entities["to_user"]) is None:
                    context["unknown_user"] = entities["to_user"]
                if entities.get("to_position") and lifecycle_service.resolve_position(db, tenant_id, entities["to_position"]) is None:
                    context["unknown_position"] = entities["to_position"]

    elif intent_type == "INT-30":
        from app.crm_core import lifecycle_service

        quote = _get_owned(db, Quote, tenant_id, entities.get("quote_id"))
        context["quote"] = {"id": quote.id, "status": quote.status.value, "revision_number": quote.revision_number} if quote else None
        if quote is not None:
            context["quote_editable"] = lifecycle_service.is_editable(db, tenant_id, "quote", quote)[0]

    elif intent_type == "INT-35":
        party = _get_owned(db, Party, tenant_id, entities.get("party_id"))
        context["party"] = {"id": party.id, "name": party.name} if party else None

    elif intent_type in ("INT-37", "INT-38"):
        opportunity = _get_owned(db, Opportunity, tenant_id, entities.get("opportunity_id"))
        context["opportunity"] = {"id": opportunity.id, "stage": opportunity.stage.value} if opportunity else None

    elif intent_type in ("INT-39", "INT-40", "INT-109"):
        quote = _get_owned(db, Quote, tenant_id, entities.get("quote_id"))
        context["quote"] = {"id": quote.id, "status": quote.status.value} if quote else None

    elif intent_type in ("INT-42", "INT-43", "INT-44", "INT-45"):
        from app.crm_core import lifecycle_service, process_service

        if intent_type in ("INT-42", "INT-43"):
            from app.crm_core import product_model_service
            from app.models.catalog import OfferingStatus

            # on sale, or a draft being designed / priced before launch
            offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, entities.get("offering_name", "") or "") or \
                product_model_service.offering_of(db, tenant_id, entities.get("offering_name", "") or "")
            if offering is not None and offering.status == OfferingStatus.retired:
                offering = None
            context["offering"] = {"id": offering.id, "name": offering.name} if offering else None
        if intent_type in ("INT-43", "INT-44"):
            price_list = process_service.find_price_list(db, tenant_id, entities.get("price_list"))
            context["price_list"] = {"id": price_list.id, "name": price_list.name} if price_list else None
        if intent_type == "INT-44":
            entity_type = lifecycle_service.normalize_entity_type(entities.get("entity_type"))
            obj = lifecycle_service.get_record(db, tenant_id, entity_type, entities.get("entity_id")) if entity_type else None
            context["record"] = {"entity_type": entity_type, "id": obj.id} if obj is not None else None
        if intent_type == "INT-45":
            order = _get_owned(db, Order, tenant_id, entities.get("order_id"))
            context["order"] = {"id": order.id, "status": order.status.value} if order else None

    elif intent_type == "INT-36":
        from app.crm_core import approval_service, lifecycle_service
        from app.models.crm import ApprovalStatus

        entity_type = lifecycle_service.normalize_entity_type(entities.get("entity_type"))
        obj = lifecycle_service.get_record(db, tenant_id, entity_type, entities.get("entity_id")) if entity_type else None
        context["record"] = {"entity_type": entity_type, "id": obj.id} if obj is not None else None
        embedded = {rt: et for rt, (et, _, _) in approval_service.EMBEDDED.items() if et not in lifecycle_service.ENTITY_MODELS}
        raw_type = str(entities.get("entity_type") or "").strip().lower()
        if obj is None and raw_type in embedded:
            # bill adjustments / payment arrangements: known through their approval
            try:
                rows = [i for i in approval_service.latest_for(db, raw_type, int(entities.get("entity_id")))
                        if i.template and i.template.tenant_id == tenant_id]
            except (TypeError, ValueError):
                rows = []
            if rows:
                context["record"] = {"entity_type": raw_type, "id": rows[0].entity_id}
                context["approval_pending"] = any(i.status == ApprovalStatus.pending for i in rows)
        if obj is not None:
            context["approval_pending"] = any(
                i.status == ApprovalStatus.pending for i in approval_service.latest_for(db, entity_type, obj.id)
            ) or approval_service.classic_waiting(db, tenant_id, entity_type, obj.id) is not None

    elif intent_type == "INT-12":
        if entities.get("work_order_id"):
            wo = _get_owned(db, WorkOrder, tenant_id, entities.get("work_order_id"))
            context["work_order"] = {"id": wo.id, "status": wo.status} if wo else None
        else:
            ticket = _get_owned(db, TroubleTicket, tenant_id, entities.get("ticket_id"))
            order = _get_owned(db, Order, tenant_id, entities.get("order_id"))
            context["ticket"] = {"id": ticket.id, "product_line": ticket.product_line} if ticket else None
            context["order"] = {"id": order.id} if order else None

    return context
