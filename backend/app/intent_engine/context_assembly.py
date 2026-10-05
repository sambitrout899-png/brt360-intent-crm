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

    elif intent_type in ("INT-19", "INT-20"):
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

    elif intent_type == "INT-11":
        ticket = _get_owned(db, TroubleTicket, tenant_id, entities.get("ticket_id"))
        context["ticket"] = {"id": ticket.id, "status": ticket.status, "severity": ticket.severity} if ticket else None

    elif intent_type == "INT-13":
        party = _get_owned(db, Party, tenant_id, entities.get("party_id"))
        context["party"] = {"id": party.id, "name": party.name} if party else None
        tenant = db.get(Tenant, tenant_id)
        if tenant is not None and tenant.vertical == TenantVertical.b2b:
            context["vertical_blocked"] = (
                f"INT-13 (identity verification) is a B2C process-pack feature; tenant '{tenant.slug}' is "
                f"configured for B2B only."
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
                f"INT-14 (multi-site provisioning) is a B2B process-pack feature; tenant '{tenant.slug}' is "
                f"configured for B2C only."
            )

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
