"""B2C direct order - the vanilla B2C New Connect journey.

    B2C prospect (INT-15 segment B2C, or a consumer INT-41)
      -> Place a B2C order (INT-104)              no lead, no opportunity, no quote
           the Order Module runs every check on the order itself:
             service feasibility at the installation address (the same
               configured feasibility check types Presales uses for B2B)
             product eligibility for the customer
             equipment availability (available-to-promise)
             credit, when the customer has a credit line
             verification (lines, prices, tax, payment)
           feasible -> submitted and booked; not -> held with the reason
      -> fulfilment (INT-25 onwards: installation, testing, acceptance, billing)

No Presales hand-off (INT-38) and no opportunity feasibility (INT-18). B2C
orders arrive on many channels - IVR, a retail sales point, a store visit,
WhatsApp, SMS, USSD, web, app, call centre - and one order can start on one
and continue on another: every touch is kept on the order's timeline
(OmChannelInteraction, INT-105).

Per tenant switch (cm_settings "b2c_journey"): direct_order defaults ON - the
vanilla process; switched off, B2C prospects may take the lead path again.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.comms import CmSetting
from app.timeutils import utcnow

KEY = "b2c_journey"
CHANNELS: dict[str, str] = {
    "ivr": "IVR", "retail_pos": "Retail sales point", "store": "Store visit", "chat": "Chat message", "sms": "SMS",
    "ussd": "USSD", "web": "Web", "app": "Mobile app", "call_center": "Call centre", "field_agent": "Field agent", "email": "Email",
}
ALIASES = {"retail": "retail_pos", "pos": "retail_pos", "retail sales point": "retail_pos", "retail point": "retail_pos",
           "shop": "store", "walk-in": "store", "walk in": "store", "store visit": "store", "direct store visit": "store",
           # what people type for a messaging-app chat (stored as "chat" since b8d2f4a6c1e3)
           "whatsapp": "chat", "whats app": "chat", "wa": "chat", "chat message": "chat", "text": "sms", "phone": "call_center",
           "call centre": "call_center", "call center": "call_center", "contact centre": "call_center", "website": "web",
           "online": "web", "mobile app": "app", "self-care app": "app", "agent": "field_agent", "door to door": "field_agent"}
DEFAULTS: dict[str, Any] = {"direct_order": True, "auto_submit": True, "channels": list(CHANNELS),
                            "require_installation_address": True}


# ---------------------------------------------------------------- settings


def _row(db: Session, tenant_id: int) -> CmSetting | None:
    return db.query(CmSetting).filter(CmSetting.tenant_id == tenant_id).first()


def settings(db: Session, tenant_id: int) -> dict[str, Any]:
    row = _row(db, tenant_id)
    try:
        stored = json.loads(row.data_json or "{}").get(KEY) if row else None
    except ValueError:
        stored = None
    out = dict(DEFAULTS)
    if isinstance(stored, dict):
        out.update({k: v for k, v in stored.items() if k in DEFAULTS})
    out["channels"] = [c for c in (ALIASES.get(c, c) for c in out["channels"]) if c in CHANNELS] or list(CHANNELS)
    return out


def save(db: Session, tenant_id: int, data: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in data if k not in DEFAULTS]
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")
    if "channels" in data:
        bad = [c for c in data["channels"] or [] if c not in CHANNELS]
        if bad or not data["channels"]:
            raise ValueError(f"Channels must be some of: {', '.join(CHANNELS)}")
    row = _row(db, tenant_id)
    if row is None:
        row = CmSetting(tenant_id=tenant_id, data_json="{}")
        db.add(row)
    try:
        current = json.loads(row.data_json or "{}")
    except ValueError:
        current = {}
    current[KEY] = {**settings(db, tenant_id), **data}
    row.data_json = json.dumps(current)
    db.flush()
    return settings(db, tenant_id)


def direct(db: Session, tenant_id: int) -> bool:
    return bool(settings(db, tenant_id)["direct_order"])


# ---------------------------------------------------------------- who is B2C


def is_b2c(party) -> bool:
    from app.crm_core import process_service

    if party is None:
        return False
    segment = process_service.party_segment(party)
    return segment == "B2C" or (segment is None and party.party_type == "individual")


def guard(db: Session, tenant_id: int, party, step: str) -> None:
    """The lead / opportunity / quote / Presales steps are not for B2C."""
    if party is not None and is_b2c(party) and direct(db, tenant_id):
        raise ValueError(f"{party.name} is a B2C customer - B2C goes straight to an order (Place a B2C order, INT-104), "
                         f"so there is no {step}. An administrator can switch the B2C lead path back on under "
                         f"Admin -> B2C order journey.")


def channel_of(value: Any, db: Session | None = None, tenant_id: int | None = None) -> str:
    text = str(value or "").strip().lower().replace("-", "_") if value else ""
    key = text if text in CHANNELS else ALIASES.get(text.replace("_", " "), ALIASES.get(text, ""))
    if not key:
        raise ValueError(f"Which channel did the order come in on? One of: {', '.join(CHANNELS.values())}")
    if db is not None and tenant_id is not None and key not in settings(db, tenant_id)["channels"]:
        raise ValueError(f"{CHANNELS[key]} is not an order channel for your organization (Admin -> B2C order journey)")
    return key


# ---------------------------------------------------------------- the omnichannel timeline


def record(db: Session, tenant_id: int, channel: str, kind: str, party_id: int | None = None, order_id: int | None = None,
           reference: str = "", note: str = "", user_id: int | None = None):
    from app.models.order_mgmt import OmChannelInteraction

    row = OmChannelInteraction(tenant_id=tenant_id, party_id=party_id, order_id=order_id, channel=channel, kind=kind[:30],
                               reference=str(reference or "")[:100], note=str(note or "")[:1000], user_id=user_id, at=utcnow())
    db.add(row)
    db.flush()
    return row


def timeline(db: Session, tenant_id: int, order_id: int | None = None, party_id: int | None = None) -> list[dict[str, Any]]:
    from app.models.order_mgmt import OmChannelInteraction

    q = db.query(OmChannelInteraction).filter(OmChannelInteraction.tenant_id == tenant_id)
    q = q.filter(OmChannelInteraction.order_id == order_id) if order_id else q.filter(OmChannelInteraction.party_id == party_id)
    return [{"at": r.at.isoformat() + "Z", "channel": r.channel, "channel_label": CHANNELS.get(r.channel, r.channel), "kind": r.kind,
             "reference": r.reference, "note": r.note, "order_id": r.order_id} for r in q.order_by(OmChannelInteraction.at, OmChannelInteraction.id)]


# ---------------------------------------------------------------- feasibility in the Order Module


def order_feasibility(db: Session, tenant_id: int, order, party, address: str) -> dict[str, Any]:
    """Every check the B2C order needs, run by the Order Module on the order:
    service feasibility, eligibility, availability, credit and verification."""
    from app.crm_core import feasibility_service, om_service as om, product_model_service
    from app.models.feasibility import FeasibilityOutcome

    checks: list[dict[str, Any]] = []
    assessment = feasibility_service.run_assessment(db, tenant_id, None, address, order_id=order.id)
    for r in assessment.results:
        checks.append({"check": r.check_type_name, "area": "network", "ok": r.outcome == FeasibilityOutcome.passed,
                       "pending": r.outcome == FeasibilityOutcome.pending, "detail": r.detail})
    rows = om.lines(db, tenant_id, "order", order, include_deleted=False)
    services = [r.product_name for r in rows if r.product_offering_id]
    _, why = product_model_service.eligible(db, tenant_id, services, party)
    checks.append({"check": "Product eligibility", "area": "eligibility", "ok": not why,
                   "detail": "; ".join(why.values()) if why else f"{party.name} can buy {', '.join(services)}"})
    for a in om.check_atp(db, tenant_id, "order", order):
        if a["atp_status"] == "not_tracked":
            continue
        checks.append({"check": f"Availability: {a['product_name']}", "area": "availability", "ok": a["atp_status"] == "available",
                       "warning": a["atp_status"] in ("partial", "backorder"), "detail": a["detail"]})
    if om.credit_profile(db, tenant_id, party.id) is not None:
        due = om.totals(db, tenant_id, "order", order)
        credit = om.credit_check(db, tenant_id, party.id, due["due_now"] + due["recurring"], "order", order.id)
        checks.append({"check": "Credit", "area": "credit", "ok": credit.result != "declined", "warning": credit.result == "review",
                       "detail": credit.reason})
    blocking = [c for c in checks if not c["ok"] and not c.get("warning") and not c.get("pending")]
    pending = [c for c in checks if c.get("pending")]
    outcome = "failed" if blocking else "pending" if pending else "passed"
    return {"outcome": outcome, "assessment_id": assessment.id, "checks": checks,
            "reasons": [f"{c['check']}: {c['detail']}" for c in blocking or pending]}


# ---------------------------------------------------------------- INT-104


def _products(entities: dict[str, Any]) -> list[tuple[str, int]]:
    from app.orchestration.actions_prospect import normalize_products

    raw = entities.get("products")
    if not raw and entities.get("offering_name"):
        raw = [{"offering_name": entities["offering_name"], "quantity": entities.get("quantity") or 1}]
    return normalize_products(raw)


def place_order(db: Session, tenant_id: int, entities: dict[str, Any], execution_id: int | None = None) -> dict[str, Any]:
    from app.crm_core import account_service, om_service as om, process_service, product_config_service as product_config
    from app.crm_core import product_model_service as pm
    from app.integration import order_adapter
    from app.models.crm import Party
    from app.models.orders import OrderType
    from app.orchestration.actions_prospect import check_products

    conf = settings(db, tenant_id)
    try:
        party = db.get(Party, int(entities.get("party_id")))
    except (TypeError, ValueError):
        party = None
    if party is None or party.tenant_id != tenant_id:
        raise ValueError("Pick the B2C customer the order is for")
    if not is_b2c(party):
        raise ValueError(f"{party.name} is not a B2C customer - business customers go through lead, opportunity and quote")
    channel = channel_of(entities.get("order_channel") or entities.get("channel_name") or "store", db, tenant_id)
    address = str(entities.get("installation_address") or entities.get("address") or "").strip()
    if not address and conf["require_installation_address"]:
        raise ValueError("Give the installation address - the Order Module checks service feasibility there")
    wanted = _products(entities)
    if not wanted:
        raise ValueError("Which product(s) does the customer want?")
    services = [(n, q) for n, q in wanted if pm.offering_of(db, tenant_id, n) is not None]
    others = [(n, q) for n, q in wanted if (n, q) not in services]
    if not services:
        raise ValueError("Add at least one service from the catalog (equipment such as a router can come with it)")
    services = check_products(db, tenant_id, party, services)  # sold to B2C, and the customer is eligible

    main_name, main_qty = services[0]
    offering = pm.offering_of(db, tenant_id, main_name)
    price_list = product_config.price_list_for(db, tenant_id, party)
    base, _ = product_config.base_price(db, tenant_id, price_list, offering, main_qty)
    billing_account = order_adapter.get_or_create_billing_account(db, tenant_id, party.id)
    service_account = account_service.get_or_create_service_account(db, tenant_id, billing_account.id, f"{offering.name} Service")
    order = order_adapter.create_order(db, tenant_id=tenant_id, party_id=party.id, service_account_id=service_account.id, quote_id=None,
                                       order_type=OrderType.new, monthly_price=round(base * main_qty, 2))
    order_adapter.add_order_item(db, order.id, offering.id, main_qty)
    profile = process_service.order_profile(order, create=True)
    profile.segment = "B2C"
    d = om.order_detail(db, tenant_id, order)
    d.channel, d.channel_reference, d.installation_address = channel, str(entities.get("channel_reference") or "")[:100] or None, address or None
    for name, qty in services[1:] + others:
        om.add_line(db, tenant_id, "order", order, name, qty)
    if others:
        d.ship_to_address = d.ship_to_address or address[:400]
    db.flush()
    actor = int(entities["_actor_user_id"]) if entities.get("_actor_user_id") else None
    record(db, tenant_id, channel, "order_started", party.id, order.id, entities.get("channel_reference") or "",
           f"Order {d.order_number} started on {CHANNELS[channel]}", actor)

    feas = order_feasibility(db, tenant_id, order, party, address)
    out: dict[str, Any] = {"order_id": order.id, "order_number": d.order_number, "party_id": party.id, "channel": channel,
                           "channel_label": CHANNELS[channel], "feasibility": feas["outcome"], "checks": feas["checks"],
                           "service_account_id": service_account.id}
    if feas["outcome"] != "passed":
        why = "; ".join(feas["reasons"])[:200]
        om.hold(db, tenant_id, order, "hold", ("Not feasible: " if feas["outcome"] == "failed" else "Waiting for feasibility: ") + why)
        record(db, tenant_id, channel, "feasibility_" + feas["outcome"], party.id, order.id, note=why, user_id=actor)
        out.update(om_status=om.order_detail(db, tenant_id, order).om_status, held=True, note=f"Order {d.order_number} is on hold - {why}")
        return out

    if entities.get("payment_method"):
        out["payment"] = om.add_payment(db, tenant_id, order, entities["payment_method"], entities.get("amount"),
                                        entities.get("card_last4") or "", entities.get("card_brand") or "", reference=entities.get("channel_reference") or "")["payment"]
    elif not d.payment_terms:
        d.payment_terms = "Billed on first invoice"  # consumer orders pay on their first bill unless paid now
    if conf["auto_submit"] and str(entities.get("submit", True)).lower() not in ("false", "0", "no"):
        submitted = om.submit(db, tenant_id, order, actor_user_id=actor)
        out.update(om_status=submitted["om_status"], approval_needed=submitted.get("approval_needed", False),
                   acknowledgement=submitted.get("acknowledgement"), warnings=submitted.get("warnings", []))
        record(db, tenant_id, channel, "order_placed", party.id, order.id, entities.get("channel_reference") or "",
               f"Order {d.order_number} {submitted['om_status'].replace('_', ' ')}", actor)
    else:
        out["om_status"] = d.om_status
    out["totals"] = om.totals(db, tenant_id, "order", order)
    return out


def log_interaction(db: Session, tenant_id: int, entities: dict[str, Any]) -> dict[str, Any]:
    """INT-105: a touch on any channel - the order's status comes back with it,
    so the agent on WhatsApp, at the store or in the IVR sees the same order."""
    from app.crm_core import om_service as om
    from app.models.crm import Party

    channel = channel_of(entities.get("order_channel") or entities.get("channel_name"), db, tenant_id)
    order = om.order_of(db, tenant_id, entities["order_id"]) if entities.get("order_id") else None
    party_id = order.party_id if order else (int(entities["party_id"]) if entities.get("party_id") else None)
    if party_id is None:
        raise ValueError("Pick the order (or the customer) this interaction is about")
    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id:
        raise ValueError("Customer not found")
    kind = str(entities.get("interaction") or "enquiry").strip().lower().replace(" ", "_")[:30]
    actor = int(entities["_actor_user_id"]) if entities.get("_actor_user_id") else None
    row = record(db, tenant_id, channel, kind, party_id, order.id if order else None, entities.get("channel_reference") or "",
                 entities.get("notes") or "", actor)
    out: dict[str, Any] = {"interaction_id": row.id, "channel": channel, "channel_label": CHANNELS[channel], "kind": kind, "party_id": party_id}
    if order is not None:
        d = om.order_detail(db, tenant_id, order)
        out.update(order_id=order.id, order_number=d.order_number, om_status=d.om_status, order_status=order.status.value,
                   started_on=CHANNELS.get(d.channel or "", d.channel), timeline=timeline(db, tenant_id, order_id=order.id))
    return out
