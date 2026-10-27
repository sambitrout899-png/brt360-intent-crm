"""Order Management intents INT-51..71 (crm_core/om_service.py does the work).

Each intent is one checked step: the service refuses anything the order or
quote's state does not allow (with the step to use instead), so a failed
run leaves nothing half done.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.crm_core import om_service as om
from app.data_intelligence import event_bus
from app.orchestration.orchestrator import SagaStep

Builder = Callable[[Session, int, int, dict[str, Any]], list[SagaStep]]


def _step(name: str, event: str, work: Callable[[Session, int, dict[str, Any]], dict[str, Any]]) -> Builder:
    def build(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
        def run() -> dict[str, Any]:
            out = work(db, tenant_id, entities)
            event_bus.publish(db, execution_id, event, {k: v for k, v in out.items() if isinstance(v, (int, float, str, bool)) or v is None})
            if entities.get("order_channel") and (out.get("order_id") or entities.get("order_id")):
                # omnichannel: this step happened on a channel - it goes on the order's timeline
                from app.crm_core import b2c_order_service as b2c

                order = om.order_of(db, tenant_id, out.get("order_id") or entities.get("order_id"))
                b2c.record(db, tenant_id, b2c.channel_of(entities["order_channel"]), name, order.party_id, order.id,
                           entities.get("channel_reference") or "", event.replace(".", " ").replace("_", " "),
                           int(entities["_actor_user_id"]) if entities.get("_actor_user_id") else None)
            return out

        return [SagaStep(name, run)]

    return build


def _ctx(db: Session, tenant_id: int, e: dict[str, Any]) -> tuple[str, Any, dict[str, Any]]:
    kind, rec = om.context(db, tenant_id, e)
    return kind, rec, {f"{kind}_id": rec.id}


def _add_lines(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    kind, rec, ref = _ctx(db, tenant_id, e)
    if e.get("favorite"):
        added = om.add_favorite(db, tenant_id, kind, rec, e["favorite"])
    elif e.get("product"):
        added = [om.add_line(db, tenant_id, kind, rec, e["product"], e.get("quantity") or 1, e.get("unit_price"), e.get("price_type"),
                             e.get("requested_date"))]
    else:
        raise ValueError("Which product (or favorite) to add?")
    return {**ref, "added": [om.line_out(x) for x in added], "totals": om.totals(db, tenant_id, kind, rec),
            "recommended": om.recommendations(db, tenant_id, kind, rec)}


def _discount(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    kind, rec, ref = _ctx(db, tenant_id, e)
    line = om.discount_line(db, tenant_id, kind, rec, e.get("line"), e.get("discount_pct"), e.get("discount_amount"),
                            e.get("discount_justification") or e.get("reason") or "")
    return {**ref, "line": om.line_out(line), "totals": om.totals(db, tenant_id, kind, rec),
            "approval_needed": line.discount_pct > om.APPROVAL_DISCOUNT_PCT}


def _verify(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    kind, rec, ref = _ctx(db, tenant_id, e)
    out = om.verify(db, tenant_id, kind, rec)
    return {**ref, **out, "totals": om.totals(db, tenant_id, kind, rec)}


def _atp(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    kind, rec, ref = _ctx(db, tenant_id, e)
    split = str(e.get("split")).lower() in ("true", "1", "yes")
    return {**ref, "availability": om.check_atp(db, tenant_id, kind, rec, split)}


def _shipping(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    return {"order_id": order.id, **om.set_shipping(db, tenant_id, order, str(e.get("address") or ""), str(e.get("shipping_method") or ""),
                                                   e.get("requested_date"), str(e.get("carrier") or ""), "", "", str(e.get("ship_region") or ""))}


def _tax(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    kind, rec, ref = _ctx(db, tenant_id, e)
    return {**ref, **om.calculate_tax(db, tenant_id, kind, rec, str(e.get("jurisdiction") or ""))}


def _payment(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    return {"order_id": order.id, **om.add_payment(db, tenant_id, order, str(e.get("payment_method") or ""), e.get("amount"),
                                                   str(e.get("card_last4") or ""), str(e.get("card_brand") or ""), str(e.get("po_number") or ""),
                                                   str(e.get("payment_terms") or ""))}


def _credit(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    if e.get("order_id"):
        order = om.order_of(db, tenant_id, e["order_id"])
        amount = float(e["amount"]) if e.get("amount") not in (None, "") else om.totals(db, tenant_id, "order", order)["due_now"]
        check = om.credit_check(db, tenant_id, order.party_id, amount, "order", order.id)
        return {"order_id": order.id, "party_id": order.party_id, **om.credit_out(check)}
    from app.models.crm import Party

    party = db.get(Party, int(e.get("party_id") or 0))
    if party is None or party.tenant_id != tenant_id:
        raise ValueError("Pick the customer or the order")
    check = om.credit_check(db, tenant_id, party.id, float(e.get("amount") or 0), "party", None)
    return {"party_id": party.id, **om.credit_out(check)}


def _submit(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    actor = int(e["_actor_user_id"]) if e.get("_actor_user_id") else None
    return {"order_id": order.id, **om.submit(db, tenant_id, order, e.get("decision"), e.get("_actor_role"), actor,
                                              str(e.get("reasoning") or e.get("comments") or ""))}


def _hold(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    return {"order_id": order.id, **om.hold(db, tenant_id, order, str(e.get("hold_action") or "hold"), str(e.get("reason") or ""))}


def _cancel(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    from app.crm_core import abo_service
    order = om.order_of(db, tenant_id, e.get("order_id"))
    if abo_service.change_of_order(db, tenant_id, order) is not None:  # an asset change order: the asset is released as it is
        return abo_service.cancel(db, tenant_id, order, str(e.get("reason") or ""))
    return {"order_id": order.id, **om.cancel(db, tenant_id, order, str(e.get("reason") or ""))}


def _supplemental(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    from app.crm_core import abo_service
    order = om.order_of(db, tenant_id, e.get("order_id"))
    if abo_service.change_of_order(db, tenant_id, order) is not None:
        raise ValueError(f"Order {order.id} is an asset change order - revise it with 'Revise an asset change' (INT-111)")
    return {"order_id": order.id, **om.supplemental(db, tenant_id, order, e.get("changes"), str(e.get("reason") or ""))}


def _follow_on(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    out = om.follow_on(db, tenant_id, order, e.get("changes"), e.get("requested_date"))
    return {"source_order_id": order.id, **out}


def _ship(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    if str(e.get("ship_action") or "ship") == "deliver":
        return {"order_id": order.id, **om.deliver(db, tenant_id, order, str(e.get("tracking_number") or ""))}
    return {"order_id": order.id, **om.ship(db, tenant_id, order, str(e.get("location") or ""), str(e.get("carrier") or ""),
                                            str(e.get("tracking_number") or ""))}


def _agreement(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    quote = om.quote_of(db, tenant_id, e.get("quote_id"))
    return {"quote_id": quote.id, **om.to_agreement(db, tenant_id, quote, e.get("term_months"), e.get("response_hours"),
                                                    str(e.get("coverage") or ""), str(e.get("name") or ""))}


def _opportunity(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    quote = om.quote_of(db, tenant_id, e.get("quote_id"))
    return om.update_opportunity(db, tenant_id, quote)


def _return(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    refund = e.get("refund")
    refund = True if refund in (None, "") else str(refund).lower() not in ("false", "0", "no")
    return {"order_id": order.id, **om.return_items(db, tenant_id, order, str(e.get("product") or ""), e.get("quantity") or 1,
                                                    str(e.get("reason") or ""), refund, str(e.get("location") or ""))}


def _activity_plan(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    return {"order_id": order.id, **om.activity_plan(db, tenant_id, order, str(e.get("template") or ""), e.get("line"))}


def _promotion(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    from app.crm_core import abo_service

    kind, rec, ref = _ctx(db, tenant_id, e)
    if kind == "order" and abo_service.installed(rec):
        # a live service: the promotion changes on its asset (a change order, fulfilled now)
        change = abo_service.live_change(db, tenant_id, rec, "promotion_change", {"promotion": e.get("promotion")})
        return {**ref, "promotion": e.get("promotion"), "asset_id": change["asset_id"], "change_order_id": change["order_id"],
                "current_monthly": change["current_monthly"], "new_monthly": change["new_monthly"], "direction": change.get("direction")}
    return {**ref, **om.apply_promotion(db, tenant_id, kind, rec, str(e.get("promotion") or "")), "totals": om.totals(db, tenant_id, kind, rec)}


def _resume(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    from app.crm_core import abo_service, sla_service

    order = om.order_of(db, tenant_id, e.get("order_id"))
    if abo_service.installed(order):
        if abo_service.asset_of_order(db, tenant_id, order.id).status.value != "suspended":
            raise ValueError(f"The service of order {order.id} is not suspended - only a suspended service resumes")
        change = abo_service.live_change(db, tenant_id, order, "resume")
        sla_service.complete_clock(db, tenant_id, "suspension_grace", "order", order.id)
        return {"order_id": order.id, "status": "active", "asset_id": change["asset_id"], "change_order_id": change["order_id"]}
    return om.resume(db, tenant_id, order)


def _track(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    return {"order_id": order.id, **om.track(db, tenant_id, order)}


BUILDERS: dict[str, Builder] = {
    "INT-51": _step("add_line_items", "quote_order.lines_added", _add_lines),
    "INT-52": _step("discount_line", "quote_order.line_discounted", _discount),
    "INT-53": _step("verify", "quote_order.verified", _verify),
    "INT-54": _step("check_availability", "quote_order.availability_checked", _atp),
    "INT-55": _step("set_shipping", "order.shipping_set", _shipping),
    "INT-56": _step("calculate_tax", "quote_order.tax_calculated", _tax),
    "INT-57": _step("add_payment", "order.payment_added", _payment),
    "INT-58": _step("credit_check", "order.credit_checked", _credit),
    "INT-59": _step("submit_order", "order.submitted", _submit),
    "INT-60": _step("hold_or_release", "order.hold_changed", _hold),
    "INT-61": _step("cancel_order", "order.cancelled", _cancel),
    "INT-62": _step("supplemental_order", "order.supplemented", _supplemental),
    "INT-63": _step("follow_on_order", "order.follow_on_created", _follow_on),
    "INT-64": _step("ship_or_deliver", "order.shipment_changed", _ship),
    "INT-65": _step("quote_to_agreement", "quote.agreement_created", _agreement),
    "INT-66": _step("update_opportunity", "opportunity.revenue_updated", _opportunity),
    "INT-67": _step("return_items", "order.items_returned", _return),
    "INT-68": _step("activity_plan", "order.activities_planned", _activity_plan),
    "INT-69": _step("apply_promotion", "quote_order.promotion_applied", _promotion),
    "INT-70": _step("resume_service", "order.resumed", _resume),
    "INT-71": _step("track_order", "order.tracked", _track),
}
