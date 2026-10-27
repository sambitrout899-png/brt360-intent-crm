"""About this record - everything about one record, whatever its type
(customer / prospect, lead, opportunity, quote, order), in one call:

  header    what it is, its state, BRT_ID, who created / owns it, when
  summary   the few numbers that matter for its type
  fields    every field it has - core columns, profile, custom fields and
            the Page Designer details - labelled
  related   the records around it, both ways along the journey (customer ->
            leads -> opportunities -> quotes -> orders -> work orders,
            tickets, billing), each openable; only what the viewer may see
  sections  what belongs to it: order lines, payments, shipments, service
            orders, feasibility, channel touches, approvals, configuration,
            lead products, identity checks, billing ...
  timeline  state changes, re-assignments, the intents that touched it and
            channel interactions, newest first
  actions   the buttons the record's page offers (record_actions)

Read-only. A section that cannot be built is skipped, never failing the rest.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.crm_core import access_service, lifecycle_service
from app.crm_core.access_service import Access
from app.models.auth import User
from app.models.crm import Lead, Opportunity, Party, Quote
from app.models.lifecycle import EntityAssignmentHistory, EntityRecordMeta, EntityStateHistory, Position
from app.models.orders import Order

log = logging.getLogger(__name__)
LABELS = {"party": "Customer / Prospect", "lead": "Lead", "opportunity": "Opportunity", "quote": "Quote", "order": "Order",
          "ticket": "Ticket", "work_order": "Work order"}
_HIDDEN = {"tenant_id", "sys_created_by", "sys_updated_by", "sys_created_position_id", "sys_department_id", "password_hash",
           "token_hash", "graph_json"}
_RECORD_KEY = {"party_id": "party", "lead_id": "lead", "opportunity_id": "opportunity", "quote_id": "quote", "order_id": "order",
               "revision_of_quote_id": "quote"}


def _plain(value: Any) -> Any:
    value = getattr(value, "value", value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 2)
    return value


def _label(key: str) -> str:
    from app.crm_core.record_service import CORE_FIELD_LABELS

    if key in CORE_FIELD_LABELS:
        return CORE_FIELD_LABELS[key]
    words = key.replace("_json", "").replace("_", " ").strip()
    return (words[:1].upper() + words[1:]).replace(" id", " #").replace(" pct", " %").replace("Brt #", "BRT_ID")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _columns(obj, skip: set[str] = frozenset()) -> list[dict[str, Any]]:
    """Every column of a row as a labelled field (system internals left out)."""
    out = []
    for column in obj.__table__.columns:
        key = column.name
        if key in _HIDDEN or key in skip or key == "id":
            continue
        value = _plain(getattr(obj, key, None))
        if isinstance(value, str) and key.endswith("_json"):
            try:
                parsed = json.loads(value or "null")
            except ValueError:
                parsed = value
            if parsed in (None, {}, []):
                continue
            value = parsed if not isinstance(parsed, (dict, list)) else json.dumps(parsed)[:400]
        out.append({"key": key, "label": _label(key), "value": value, "record": _RECORD_KEY.get(key)})
    return out


def _owner(db: Session, tenant_id: int, entity_type: str, record_id: int) -> dict[str, Any]:
    meta = db.query(EntityRecordMeta).filter(EntityRecordMeta.tenant_id == tenant_id, EntityRecordMeta.entity_type == entity_type,
                                             EntityRecordMeta.entity_id == record_id).first()
    if meta is None:
        return {}
    user = db.get(User, meta.owner_user_id) if meta.owner_user_id else None
    position = db.get(Position, meta.owner_position_id) if meta.owner_position_id else None
    out = {"user": (user.name or user.email) if user else None, "position": position.name if position else None}
    if meta.owner_department_id:
        from app.models.org import Department

        dept = db.get(Department, meta.owner_department_id)
        out["department"] = dept.name if dept else None
    return out


def _title(db: Session, entity_type: str, obj) -> str:
    if entity_type == "party":
        return obj.name
    if entity_type == "lead":
        return f"Lead #{obj.id} - {obj.party.name}" if obj.party else f"Lead #{obj.id}"
    if entity_type == "opportunity":
        return obj.name
    if entity_type == "quote":
        return f"Quote #{obj.id} (rev {obj.revision_number})"
    party = db.get(Party, obj.party_id)
    return f"Order #{obj.id}" + (f" - {party.name}" if party else "")


def _state(db: Session, tenant_id: int, entity_type: str, obj) -> str:
    try:
        meta = lifecycle_service.get_meta(db, tenant_id, entity_type, obj.id, create=False)
        return lifecycle_service.effective_state(db, tenant_id, entity_type, obj, meta)
    except Exception:  # noqa: BLE001 - a state is a nicety here
        return str(_plain(getattr(obj, "status", getattr(obj, "stage", "")))) or ""


class _Family:
    """The records around one record - collected once, filtered by access."""

    def __init__(self, db: Session, tenant_id: int, access: Access, entity_type: str, obj):
        self.db, self.tenant_id, self.access = db, tenant_id, access
        self.party: Party | None = None
        self.leads: list[Lead] = []
        self.opps: list[Opportunity] = []
        self.quotes: list[Quote] = []
        self.orders: list[Order] = []
        if entity_type == "party":
            self.party = obj
            self.leads = db.query(Lead).filter(Lead.party_id == obj.id).order_by(Lead.id).all()
            self.opps = [lead.opportunity for lead in self.leads if lead.opportunity is not None]
            ids = [o.id for o in self.opps] or [-1]
            self.quotes = db.query(Quote).filter(Quote.opportunity_id.in_(ids)).order_by(Quote.id).all()
            self.orders = db.query(Order).filter(Order.party_id == obj.id).order_by(Order.id).all()
        elif entity_type == "lead":
            self.party, self.leads = obj.party, [obj]
            self.opps = [obj.opportunity] if obj.opportunity is not None else []
            self._down_from_opps()
        elif entity_type == "opportunity":
            self.opps = [obj]
            self.leads = [obj.lead] if obj.lead is not None else []
            self.party = obj.lead.party if obj.lead is not None else None
            self._down_from_opps()
        elif entity_type == "quote":
            self.quotes = db.query(Quote).filter((Quote.id == obj.id) | (Quote.revision_of_quote_id == obj.id)
                                                 | (Quote.id == (obj.revision_of_quote_id or -1))).order_by(Quote.id).all()
            opp = db.get(Opportunity, obj.opportunity_id) if obj.opportunity_id else None
            self.opps = [opp] if opp else []
            self.leads = [opp.lead] if opp is not None and opp.lead is not None else []
            self.party = self.leads[0].party if self.leads else None
            self.orders = db.query(Order).filter(Order.quote_id.in_([q.id for q in self.quotes])).order_by(Order.id).all()
        elif entity_type == "order":
            self.orders = [obj]
            self.party = db.get(Party, obj.party_id)
            quote = db.get(Quote, obj.quote_id) if obj.quote_id else None
            self.quotes = [quote] if quote else []
            opp = db.get(Opportunity, quote.opportunity_id) if quote is not None and quote.opportunity_id else None
            self.opps = [opp] if opp else []
            self.leads = [opp.lead] if opp is not None and opp.lead is not None else []

    def _down_from_opps(self) -> None:
        ids = [o.id for o in self.opps] or [-1]
        self.quotes = self.db.query(Quote).filter(Quote.opportunity_id.in_(ids)).order_by(Quote.id).all()
        self.orders = self.db.query(Order).filter(Order.quote_id.in_([q.id for q in self.quotes] or [-1])).order_by(Order.id).all()

    def sees(self, entity_type: str, record_id: int) -> bool:
        try:
            return access_service.can_see(self.db, self.access, entity_type, record_id)
        except Exception:  # noqa: BLE001
            return False


def _related(db: Session, tenant_id: int, fam: _Family, entity_type: str, obj) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(kind: str, rec, relation: str, detail: str = "", amount: float | None = None) -> None:
        if rec is None or (kind == entity_type and rec.id == obj.id) or not fam.sees(kind, rec.id):
            return
        out.append({"entity_type": kind, "entity_id": rec.id, "type_label": LABELS[kind], "title": _title(db, kind, rec),
                    "state": _state(db, tenant_id, kind, rec), "relation": relation, "detail": detail, "amount": amount,
                    "brt_id": getattr(rec, "brt_id", None), "at": _iso(getattr(rec, "created_at", None)), "openable": True})

    add("party", fam.party, "Customer")
    for lead in fam.leads:
        add("lead", lead, "Lead", f"from {lead.source} · {lead.interested_offering_name or 'no product yet'}")
    for opp in fam.opps:
        add("opportunity", opp, "Opportunity", opp.offering_name or "")
    for q in fam.quotes:
        add("quote", q, "Quote", f"{_plain(q.status)} · rev {q.revision_number}", q.total_price)
    for o in fam.orders:
        add("order", o, "Order", f"{_plain(o.status)} · {_plain(o.order_type)}", o.monthly_price)
    return out


def _safe(name: str, build: Callable[[], dict[str, Any] | None], into: list[dict[str, Any]]) -> None:
    try:
        section = build()
    except Exception:  # noqa: BLE001 - one section never breaks the page
        log.exception("About-record section %s failed", name)
        return
    if section and (section.get("rows") or section.get("items")):
        into.append(section)


def _sections(db: Session, tenant_id: int, fam: _Family, entity_type: str, obj) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []

    def approvals():
        from app.crm_core import approval_service

        items = []
        for inst in approval_service.latest_for(db, entity_type, obj.id):
            items.append({"title": inst.template.name if inst.template else inst.gate, "state": _plain(inst.status),
                          "detail": " → ".join(f"{s.approver_tier}: {_plain(s.status)}" for s in inst.steps),
                          "at": _iso(inst.created_at)})
        return {"key": "approvals", "title": "Approvals", "icon": "✅", "items": items}

    _safe("approvals", approvals, sections)

    if entity_type == "party":
        def profile():
            return {"key": "profile", "title": "Profile", "icon": "🪪", "rows": _columns(obj.profile, {"party_id"}) if obj.profile else []}

        def billing():
            from app.models.orders import BillingAccount

            b = db.query(BillingAccount).filter(BillingAccount.party_id == obj.id).first()
            return {"key": "billing", "title": "Billing account", "icon": "💳", "rows": _columns(b, {"party_id"}) if b else []}

        def identity():
            from app.models.identity import IdentityVerification

            rows = db.query(IdentityVerification).filter(IdentityVerification.party_id == obj.id).order_by(IdentityVerification.id.desc()).all()
            return {"key": "identity", "title": "Identity checks", "icon": "🛂",
                    "items": [{"title": _plain(v.status), "detail": getattr(v, "provider", "") or "", "at": _iso(v.created_at)} for v in rows]}

        def tickets():
            from app.models.assurance import TroubleTicket

            rows = db.query(TroubleTicket).filter(TroubleTicket.party_id == obj.id).order_by(TroubleTicket.id.desc()).all()
            return {"key": "tickets", "title": "Tickets", "icon": "🆘",
                    "items": [{"title": f"Ticket #{t.id} · {t.severity}", "state": t.status, "detail": (t.description or "")[:140],
                               "at": _iso(t.created_at)} for t in rows]}

        for name, build in (("profile", profile), ("billing", billing), ("identity", identity), ("tickets", tickets)):
            _safe(name, build, sections)

    if entity_type == "lead":
        def products():
            from app.models.pages import LeadProduct

            rows = db.query(LeadProduct).filter(LeadProduct.lead_id == obj.id).order_by(LeadProduct.position).all()
            return {"key": "products", "title": "Products of interest", "icon": "🛍️",
                    "items": [{"title": r.offering_name, "detail": f"x {r.quantity}" + (f" · {r.notes}" if r.notes else "")} for r in rows]}

        _safe("products", products, sections)

    if entity_type == "opportunity":
        def config():
            from app.crm_core import process_service

            c = process_service.opportunity_config(obj, create=False)
            return {"key": "configuration", "title": "Configuration & site", "icon": "🧩", "rows": _columns(c, {"opportunity_id"}) if c else []}

        def feasibility():
            from app.models.feasibility import FeasibilityAssessment

            rows = db.query(FeasibilityAssessment).filter(FeasibilityAssessment.opportunity_id == obj.id).order_by(FeasibilityAssessment.id.desc()).all()
            return {"key": "feasibility", "title": "Feasibility", "icon": "📡",
                    "items": [{"title": f"{r.check_type_name}", "state": _plain(r.outcome), "detail": r.detail, "at": _iso(a.created_at)}
                              for a in rows for r in a.results]}

        _safe("configuration", config, sections)
        _safe("feasibility", feasibility, sections)

    if entity_type in ("quote", "order"):
        def lines():
            from app.crm_core import om_service as om

            rows = om.lines(db, tenant_id, entity_type, obj, include_deleted=False)
            return {"key": "lines", "title": "Lines", "icon": "🧾",
                    "items": [{"title": f"{r.line_number}. {r.product_name}", "state": r.status,
                               "detail": f"{r.quantity:g} x {r.net_price:,.2f} · {r.price_type.replace('_', ' ')}"
                                         + (f" · {r.discount_pct:g}% off" if r.discount_pct else "") + (f" · stock {r.atp_status}" if r.atp_status else ""),
                               "amount": round(r.net_price * r.quantity, 2)} for r in rows]}

        _safe("lines", lines, sections)

    if entity_type == "order":
        def om_detail():
            from app.crm_core import om_service as om

            d = om.order_detail(db, tenant_id, obj)
            return {"key": "sales_order", "title": "Sales order", "icon": "📦", "rows": _columns(d, {"order_id", "issues_json"})}

        def payments():
            from app.crm_core import om_service as om

            return {"key": "payments", "title": "Payments", "icon": "💰",
                    "items": [{"title": f"{p.method.replace('_', ' ')} {p.amount:,.2f}", "state": p.status,
                               "detail": " ".join(x for x in [f"•••• {p.card_last4}" if p.card_last4 else "", p.po_number, p.reference] if x),
                               "at": _iso(p.created_at)} for p in om.payments(db, tenant_id, obj)]}

        def feasibility():
            from app.models.feasibility import FeasibilityAssessment

            rows = db.query(FeasibilityAssessment).filter(FeasibilityAssessment.tenant_id == tenant_id, FeasibilityAssessment.order_id == obj.id) \
                .order_by(FeasibilityAssessment.id.desc()).all()
            return {"key": "feasibility", "title": "Feasibility (Order Module)", "icon": "📡",
                    "items": [{"title": r.check_type_name, "state": _plain(r.outcome), "detail": r.detail, "at": _iso(a.created_at)}
                              for a in rows for r in a.results]}

        def channels():
            from app.crm_core import b2c_order_service as b2c

            return {"key": "channels", "title": "Channels", "icon": "📲",
                    "items": [{"title": f"{t['channel_label']} · {t['kind'].replace('_', ' ')}", "detail": t["note"] or t["reference"], "at": t["at"]}
                              for t in b2c.timeline(db, tenant_id, order_id=obj.id)]}

        def fulfilment():
            from app.models.assurance import WorkOrder
            from app.models.orders import ServiceOrder

            items = [{"title": f"Service order #{s.id} · {s.site_label or 'Primary'}", "state": _plain(s.status), "at": _iso(getattr(s, "created_at", None))}
                     for s in db.query(ServiceOrder).filter(ServiceOrder.order_id == obj.id)]
            items += [{"title": f"{w.kind.replace('_', ' ').capitalize()} work order #{w.id}", "state": w.status, "at": _iso(getattr(w, "created_at", None))}
                      for w in db.query(WorkOrder).filter(WorkOrder.order_id == obj.id).order_by(WorkOrder.id)]
            items += [{"title": m.label, "state": "reached", "at": _iso(m.reached_at)} for m in obj.milestones]
            return {"key": "fulfilment", "title": "Fulfilment", "icon": "🛠️", "items": items}

        def shipments():
            from app.models.order_mgmt import OmShipment

            return {"key": "shipments", "title": "Shipments", "icon": "🚚",
                    "items": [{"title": s.shipment_number, "state": s.status, "detail": f"{s.carrier} {s.tracking_number}".strip(), "at": _iso(s.shipped_at)}
                              for s in db.query(OmShipment).filter(OmShipment.order_id == obj.id)]}

        def profile():
            from app.crm_core import process_service

            p = process_service.order_profile(obj, create=False)
            return {"key": "order_profile", "title": "Order profile", "icon": "🏷️", "rows": _columns(p, {"order_id"}) if p else []}

        for name, build in (("sales_order", om_detail), ("order_profile", profile), ("payments", payments), ("feasibility", feasibility),
                            ("channels", channels), ("fulfilment", fulfilment), ("shipments", shipments)):
            _safe(name, build, sections)

    if entity_type == "quote":
        def po():
            p = getattr(obj, "purchase_order", None)
            return {"key": "purchase_order", "title": "Customer PO", "icon": "📄", "rows": _columns(p, {"quote_id"}) if p else []}

        _safe("purchase_order", po, sections)
    return sections


def _timeline(db: Session, tenant_id: int, entity_type: str, obj, user_names: dict[int, str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"at": _iso(getattr(obj, "created_at", None) or obj.sys_created_at), "icon": "✨", "title": f"{LABELS[entity_type]} created",
                                     "detail": user_names.get(obj.sys_created_by or 0, "")}]
    for h in db.query(EntityStateHistory).filter(EntityStateHistory.tenant_id == tenant_id, EntityStateHistory.entity_type == entity_type,
                                                 EntityStateHistory.entity_id == obj.id).order_by(EntityStateHistory.id):
        events.append({"at": _iso(h.created_at), "icon": "➡️",
                       "title": f"{(h.from_state or 'new').replace('_', ' ')} → {h.to_state.replace('_', ' ')}",
                       "detail": getattr(h, "source", "") or ""})
    for h in db.query(EntityAssignmentHistory).filter(EntityAssignmentHistory.tenant_id == tenant_id,
                                                      EntityAssignmentHistory.entity_type == entity_type,
                                                      EntityAssignmentHistory.entity_id == obj.id).order_by(EntityAssignmentHistory.id):
        to = user_names.get(getattr(h, "to_user_id", None) or 0) or "a position / department"
        events.append({"at": _iso(h.created_at), "icon": "👤", "title": f"Assigned to {to}", "detail": getattr(h, "reason", "") or ""})
    from app.models.intent_log import IntentExecutionLog

    key = f"{entity_type}_id"
    needles = [f'"{key}": {obj.id},', f'"{key}": {obj.id}}}', f'"{key}": "{obj.id}"']
    if entity_type in ("party",):
        needles += [f'"entity_type": "party", "entity_id": {obj.id}']
    from sqlalchemy import or_

    logs = db.query(IntentExecutionLog).filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.status == "executed",
                                              or_(*[IntentExecutionLog.entities_json.contains(n) for n in needles],
                                                  *[IntentExecutionLog.plan_json.contains(n) for n in needles])) \
        .order_by(IntentExecutionLog.id.desc()).limit(60).all()
    from app.intent_engine import intent_registry

    for row in logs:
        events.append({"at": _iso(row.created_at), "icon": "⚡", "title": intent_registry.label_of(db, tenant_id, row.intent_type) or row.intent_type,
                       "detail": f"{row.intent_type} · {user_names.get(row.user_id or 0, 'system')}", "execution_id": row.id})
    if entity_type in ("order", "party"):
        from app.models.order_mgmt import OmChannelInteraction
        from app.crm_core import b2c_order_service as b2c

        q = db.query(OmChannelInteraction).filter(OmChannelInteraction.tenant_id == tenant_id)
        q = q.filter(OmChannelInteraction.order_id == obj.id) if entity_type == "order" else q.filter(OmChannelInteraction.party_id == obj.id)
        for i in q:
            events.append({"at": _iso(i.at), "icon": "📲", "title": f"{b2c.CHANNELS.get(i.channel, i.channel)}: {i.kind.replace('_', ' ')}",
                           "detail": i.note or i.reference})
    events = [e for e in events if e["at"]]
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:80]


def _summary(fam: _Family, entity_type: str, obj, related: list[dict[str, Any]]) -> list[dict[str, Any]]:
    LIVE = ("active", "changed")
    if entity_type == "party":
        # a completed order's service lives on as an asset (as the dashboard counts it); orders
        # from before asset-based ordering still count by their own status
        from app.crm_core import abo_service
        from app.models.fulfillment import AssetStatus

        live = [o for o in fam.orders if _plain(o.status) in LIVE]
        assets = [a for a in abo_service.customer_assets(fam.db, fam.tenant_id, obj.id) if a.status == AssetStatus.active]
        revenue = sum(o.monthly_price for o in live) + sum(abo_service.recurring_price(a) for a in assets)
        return [{"label": "Monthly revenue", "value": round(revenue, 2), "money": True},
                {"label": "Live services", "value": len(live) + len(assets)}, {"label": "Orders", "value": len(fam.orders)},
                {"label": "Leads", "value": len(fam.leads)}, {"label": "Quotes", "value": len(fam.quotes)},
                {"label": "Customer since", "value": _iso(obj.created_at)}]
    if entity_type == "lead":
        return [{"label": "Propensity", "value": obj.propensity_score}, {"label": "Source", "value": obj.source},
                {"label": "Region", "value": obj.region}, {"label": "Opportunities", "value": len(fam.opps)}]
    if entity_type == "opportunity":
        return [{"label": "Stage", "value": _plain(obj.stage)}, {"label": "Product", "value": obj.offering_name},
                {"label": "Quantity", "value": obj.quantity}, {"label": "Quotes", "value": len(fam.quotes)}]
    if entity_type == "quote":
        from app.crm_core import quote_override_service

        override = quote_override_service.active(fam.db, obj.id)
        negotiated = [{"label": "System price", "value": override.computed_price, "money": True},
                      {"label": "Negotiated", "value": f"{override.effective_discount_pct:g}% off list - {override.reason}"}] if override else []
        return [{"label": "Total / month", "value": obj.total_price, "money": True}, *negotiated,
                {"label": "Discount", "value": f"{obj.requested_discount_pct or 0:g}%"},
                {"label": "Revision", "value": obj.revision_number}, {"label": "Orders", "value": len(fam.orders)}]
    return [{"label": "Monthly", "value": obj.monthly_price, "money": True}, {"label": "Status", "value": _plain(obj.status)},
            {"label": "Type", "value": _plain(obj.order_type)}, {"label": "Placed", "value": _iso(obj.created_at)}]


def about(db: Session, tenant_id: int, access: Access, user: User, entity_type: str, obj) -> dict[str, Any]:
    from app.crm_core import custom_field_service, page_service, record_actions

    fam = _Family(db, tenant_id, access, entity_type, obj)
    users = {u.id: (u.name or u.email) for u in db.query(User).filter(User.tenant_id == tenant_id)}
    state = _state(db, tenant_id, entity_type, obj)
    fields = _columns(obj)
    custom = custom_field_service.list_definitions(db, tenant_id, entity_type)
    values = custom_field_service.get_values(db, tenant_id, entity_type, obj.id)
    fields += [{"key": d.key, "label": d.label, "value": values.get(d.key), "custom": True} for d in custom if values.get(d.key) not in (None, "")]
    try:  # Page Designer details not already shown
        shown = {f["key"] for f in fields}
        for key, value in page_service.record_values(db, tenant_id, entity_type, obj).items():
            if key not in shown and value not in (None, "", []):
                fields.append({"key": key, "label": _label(key), "value": _plain(value), "custom": True})
    except Exception:  # noqa: BLE001
        pass
    related = _related(db, tenant_id, fam, entity_type, obj)
    try:
        actions = record_actions.for_record(db, tenant_id, user, entity_type, obj)
    except Exception:  # noqa: BLE001
        actions = []
    return {
        "header": {"entity_type": entity_type, "entity_id": obj.id, "type_label": LABELS[entity_type], "title": _title(db, entity_type, obj),
                   "state": state, "brt_id": getattr(obj, "brt_id", None), "created_at": _iso(obj.sys_created_at or getattr(obj, "created_at", None)),
                   "created_by": users.get(obj.sys_created_by or 0), "updated_at": _iso(obj.sys_updated_at),
                   "updated_by": users.get(obj.sys_updated_by or 0), "owner": _owner(db, tenant_id, entity_type, obj.id),
                   "customer": {"id": fam.party.id, "name": fam.party.name} if fam.party is not None and fam.sees("party", fam.party.id) else None},
        "summary": _summary(fam, entity_type, obj, related),
        "fields": fields,
        "related": related,
        "sections": _sections(db, tenant_id, fam, entity_type, obj),
        "timeline": _timeline(db, tenant_id, entity_type, obj, users),
        "actions": actions,
    }
