"""What a signed-in user should be told: approvals waiting for them (shown
when they log in), what happened to the requests they made, and a running
bar of key information - administrators' broadcast messages plus facts
worked out from the data (on Customer 360: that customer's own).

Broadcast messages live in the tenant's settings row (cm_settings.data_json,
key "broadcasts") - no table of their own.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import access_service, approval_service
from app.crm_core.access_service import Access
from app.models.approval import ApprovalChainInstance
from app.models.comms import CmSetting
from app.timeutils import utcnow

KEY = "broadcasts"
LEVELS = ("info", "success", "warning", "urgent")
PLACES = ("all", "dashboard", "customer", "mywork")


# ---------------------------------------------------------------- broadcasts


def _load(db: Session, tenant_id: int) -> tuple[CmSetting | None, dict[str, Any]]:
    row = db.query(CmSetting).filter(CmSetting.tenant_id == tenant_id).first()
    try:
        data = json.loads(row.data_json or "{}") if row else {}
    except ValueError:
        data = {}
    return row, data


def broadcasts(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    return list(_load(db, tenant_id)[1].get(KEY) or [])


def add_broadcast(db: Session, tenant_id: int, user_id: int, text: str, level: str = "info", place: str = "all",
                  ends_at: str | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Write the message")
    if level not in LEVELS:
        raise ValueError(f"Level is one of: {', '.join(LEVELS)}")
    if place not in PLACES:
        raise ValueError(f"Show it on: {', '.join(PLACES)}")
    if ends_at:
        try:
            dt.datetime.fromisoformat(ends_at)
        except ValueError:
            raise ValueError("The end date isn't a date") from None
    row, data = _load(db, tenant_id)
    if row is None:
        row = CmSetting(tenant_id=tenant_id, data_json="{}")
        db.add(row)
    items = list(data.get(KEY) or [])
    item = {"id": max((b["id"] for b in items), default=0) + 1, "text": text[:300], "level": level, "place": place,
            "ends_at": ends_at or None, "created_by": user_id, "created_at": utcnow().isoformat(timespec="seconds")}
    items.append(item)
    data[KEY] = items
    row.data_json = json.dumps(data)
    db.flush()
    return item


def remove_broadcast(db: Session, tenant_id: int, broadcast_id: int) -> None:
    row, data = _load(db, tenant_id)
    items = [b for b in (data.get(KEY) or []) if b.get("id") != broadcast_id]
    if row is None or len(items) == len(data.get(KEY) or []):
        raise ValueError("Message not found")
    data[KEY] = items
    row.data_json = json.dumps(data)
    db.flush()


def _live(b: dict[str, Any], place: str) -> bool:
    if b.get("place", "all") not in ("all", place):
        return False
    if b.get("ends_at"):
        try:
            end = dt.datetime.fromisoformat(b["ends_at"])
        except ValueError:
            return True
        if len(b["ends_at"]) <= 10:
            end += dt.timedelta(days=1)  # a date: the whole day
        return utcnow() < end
    return True


# ---------------------------------------------------------------- per user


def _label(db: Session, tenant_id: int, instance: ApprovalChainInstance) -> str:
    return instance.summary or f"{instance.entity_type.title()} #{instance.entity_id}"


def for_user(db: Session, tenant_id: int, access: Access, place: str = "dashboard", party_id: int | None = None) -> dict[str, Any]:
    user = access.user
    waiting = approval_service.pending_for_user(db, tenant_id, user.id)
    waiting_classic = approval_service.classic_waiting_for_user(db, tenant_id, user.id)
    approvals = [{"entity_type": i.entity_type, "entity_id": i.entity_id, "summary": _label(db, tenant_id, i),
                  "waiting_for": approval_service.waiting_for(i), "requested_at": i.created_at,
                  "requested_by": approval_service.describe(db, i).get("requested_by")} for i in waiting]
    approvals += [{"entity_type": c["entity_type"], "entity_id": c["entity_id"],
                   "summary": f"{c['entity_type'].title()} #{c['entity_id']} - {c['rule']}", "waiting_for": c["waiting_for"],
                   "requested_at": c["requested_at"], "requested_by": c.get("requested_by")} for c in waiting_classic]

    ticker: list[dict[str, Any]] = [
        {"text": b["text"], "level": b.get("level", "info"), "kind": "broadcast"}
        for b in broadcasts(db, tenant_id) if _live(b, place)
    ]
    if approvals:
        ticker.append({"text": f"{len(approvals)} approval(s) waiting for you - open My Work", "level": "warning",
                       "kind": "approvals", "link": "mywork"})

    # what happened to my own requests in the last 3 days
    since = utcnow() - dt.timedelta(days=3)
    mine = (
        db.query(ApprovalChainInstance)
        .filter(ApprovalChainInstance.tenant_id == tenant_id, ApprovalChainInstance.requested_by_user_id == user.id,
                ApprovalChainInstance.decided_at.isnot(None), ApprovalChainInstance.decided_at >= since)
        .order_by(ApprovalChainInstance.decided_at.desc())
        .limit(5)
        .all()
    )
    for i in mine:
        who = next((s.get("decided_by") for s in reversed(approval_service.describe(db, i)["steps"]) if s.get("decided_by")), None)
        ticker.append({"text": f"{_label(db, tenant_id, i)}: {i.status.value}{f' by {who}' if who else ''}",
                       "level": "success" if i.status.value == "approved" else "urgent", "kind": "decision",
                       "entity_type": i.entity_type, "entity_id": i.entity_id})

    if party_id is not None and access_service.can_see(db, access, "party", party_id):
        ticker += _customer_facts(db, tenant_id, party_id)
    elif place == "dashboard":
        ticker += _workspace_facts(db, tenant_id, access)
    return {"approvals_waiting": len(approvals), "approvals": approvals[:10], "ticker": ticker}


# ---------------------------------------------------------------- message boxes (Flow Studio: Notify -> "popup")
#
# A flow's Notify step with the "popup" channel shows a message box on the
# screen of the people it names. Each box is a domain event (the outbox the
# flow already writes to, so it commits or rolls back with the flow); which
# boxes a user has dismissed is one number per user in the tenant settings
# ("popup_seen": {user_id: last event id}) - no table of its own.

POPUP_EVENT = "notification.popup"
SEEN_KEY = "popup_seen"
POPUP_DAYS = 7
_ME = {"", "me", "user", "the user", "current user", "actor", "requester", "whoever did it", "the person who did it", "initiator"}


def popup_recipients(db: Session, tenant_id: int, audience: str, entity_type: str | None = None, record: Any = None) -> list[int]:
    """Who sees the box: the person whose action started the flow ("me", the default), a user (email or
    name), the members of a position, or the record's owner ("owner")."""
    from app.models.auth import User
    from app.models.lifecycle import EntityRecordMeta, Position, PositionMember

    actor = (access_service.current_actor.get() or {}).get("user_id")
    wanted = (audience or "").strip()
    key = wanted.lower()
    if key in _ME:
        return [actor] if actor else []
    if key in ("owner", "record owner", "the owner", "assignee"):
        meta = db.query(EntityRecordMeta).filter(EntityRecordMeta.tenant_id == tenant_id, EntityRecordMeta.entity_type == entity_type,
                                                 EntityRecordMeta.entity_id == getattr(record, "id", None)).first() if record is not None else None
        if meta is not None and meta.owner_user_id:
            return [meta.owner_user_id]
        if meta is not None and meta.owner_position_id:
            return sorted({m.user_id for m in db.query(PositionMember).filter(PositionMember.position_id == meta.owner_position_id)})
        return [actor] if actor else []
    users: set[int] = set()
    for part in [p.strip() for p in wanted.replace(";", ",").split(",") if p.strip()]:
        user = db.query(User).filter(User.tenant_id == tenant_id, (User.email.ilike(part)) | (User.name.ilike(part))).first()
        if user is not None:
            users.add(user.id)
            continue
        position = db.query(Position).filter(Position.tenant_id == tenant_id, Position.name.ilike(part)).first()
        if position is not None:
            users |= {m.user_id for m in db.query(PositionMember).filter(PositionMember.position_id == position.id)}
    return sorted(users) if users else ([actor] if actor else [])


def popups_for_user(db: Session, tenant_id: int, user_id: int) -> list[dict[str, Any]]:
    """Message boxes waiting for this user (the last week's, not yet dismissed), oldest first."""
    from app.models.intent_log import DomainEvent, IntentExecutionLog

    seen = int((_load(db, tenant_id)[1].get(SEEN_KEY) or {}).get(str(user_id), 0))
    since = utcnow() - dt.timedelta(days=POPUP_DAYS)
    rows = (
        db.query(DomainEvent)
        .join(IntentExecutionLog, IntentExecutionLog.id == DomainEvent.correlation_id)
        .filter(IntentExecutionLog.tenant_id == tenant_id, DomainEvent.event_type == POPUP_EVENT, DomainEvent.id > seen,
                DomainEvent.created_at >= since)
        .order_by(DomainEvent.id)
        .limit(200)
        .all()
    )
    out = []
    for row in rows:
        try:
            data = json.loads(row.payload_json or "{}")
        except ValueError:
            continue
        if user_id in (data.get("user_ids") or []):
            out.append({"id": row.id, "title": data.get("title") or "Message", "message": data.get("message") or "",
                        "level": data.get("level") or "info", "entity_type": data.get("entity_type"), "entity_id": data.get("entity_id"),
                        "at": row.created_at.isoformat(timespec="seconds")})
    return out[-20:]


def mark_popups_seen(db: Session, tenant_id: int, user_id: int, up_to: int) -> None:
    row, data = _load(db, tenant_id)
    if row is None:
        row = CmSetting(tenant_id=tenant_id, data_json="{}")
        db.add(row)
    seen = dict(data.get(SEEN_KEY) or {})
    seen[str(user_id)] = max(int(seen.get(str(user_id), 0)), int(up_to))
    data[SEEN_KEY] = seen
    row.data_json = json.dumps(data)
    db.flush()


def _customer_facts(db: Session, tenant_id: int, party_id: int) -> list[dict[str, Any]]:
    from app.models.assurance import TroubleTicket
    from app.models.comms import CmInvoice
    from app.models.crm import Lead, Quote

    out: list[dict[str, Any]] = []
    leads = db.query(Lead).filter(Lead.tenant_id == tenant_id, Lead.party_id == party_id).all()
    opp_ids = [lead.opportunity.id for lead in leads if lead.opportunity is not None]
    quote_ids = [q.id for q in db.query(Quote.id).filter(Quote.opportunity_id.in_(opp_ids or [-1]))]
    refs = [("lead", x.id) for x in leads] + [("opportunity", x) for x in opp_ids] + [("quote", x) for x in quote_ids]
    for et, eid in refs:
        for i in approval_service.latest_for(db, et, eid):
            if i.status.value == "pending" and i.template and i.template.tenant_id == tenant_id:
                out.append({"text": f"{_label(db, tenant_id, i)} - waiting for {approval_service.waiting_for(i)}", "level": "warning",
                            "kind": "approval", "entity_type": et, "entity_id": eid})
    urgent = db.query(TroubleTicket).filter(TroubleTicket.party_id == party_id, TroubleTicket.status != "resolved",
                                            TroubleTicket.severity.in_(("critical", "high"))).count()
    if urgent:
        out.append({"text": f"{urgent} urgent ticket(s) open", "level": "urgent", "kind": "tickets"})
    overdue = db.query(CmInvoice).filter(CmInvoice.tenant_id == tenant_id, CmInvoice.party_id == party_id,
                                         CmInvoice.due_date < dt.date.today(),
                                         CmInvoice.status.notin_(("paid", "written_off", "cancelled"))).count()
    if overdue:
        out.append({"text": f"{overdue} overdue bill(s)", "level": "urgent", "kind": "billing"})
    return out


def _workspace_facts(db: Session, tenant_id: int, access: Access) -> list[dict[str, Any]]:
    from app.models.lifecycle import EntityRecordMeta, PositionMember

    user = access.user
    positions = {m.position_id for m in db.query(PositionMember).filter(PositionMember.user_id == user.id)}
    mine = db.query(EntityRecordMeta).filter(EntityRecordMeta.tenant_id == tenant_id).filter(
        (EntityRecordMeta.owner_user_id == user.id)
        | (EntityRecordMeta.owner_user_id.is_(None) & EntityRecordMeta.owner_position_id.in_(positions or {-1}))
    ).count()
    return [{"text": f"{mine} record(s) assigned to you", "level": "info", "kind": "mywork", "link": "mywork"}] if mine else []
