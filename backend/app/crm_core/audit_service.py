"""Audit trail - every intent run, readable.

feed():  the executions, filtered (text, status, action, person, channel,
         period, record) with counts, facets and a one-line outcome each.
story(): one execution told step by step - what was asked, how it was
         understood, what was checked, why it ran (or didn't), each step
         and its result, the records it touched and the events it emitted.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.intent_log import DomainEvent, IntentExecutionLog
from app.timeutils import utcnow

ID_KEYS = {"party_id": "party", "lead_id": "lead", "opportunity_id": "opportunity", "quote_id": "quote", "new_quote_id": "quote",
           "order_id": "order", "ticket_id": "ticket", "work_order_id": "work_order", "proposal_id": "proposal",
           "product_offering_id": "product", "offering_id": "product"}
ENTITY_LABEL = {"party": "Customer", "lead": "Lead", "opportunity": "Opportunity", "quote": "Quote", "order": "Order", "ticket": "Ticket",
                "work_order": "Work order", "proposal": "Proposal", "product": "Product"}
STATUS = {"executed": "done", "failed": "failed", "needs_review": "review", "pending": "review"}
# changes made on a screen rather than by an intent (custom records) - code -> (label, icon)
SCREEN_ACTIONS = {"REC-CREATE": ("Create a record", "➕"), "REC-UPDATE": ("Update a record", "✏️"), "REC-DELETE": ("Delete a record", "🗑️")}


def _loads(raw: str | None, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except json.JSONDecodeError:
        return default


def _human(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


class _Names:
    """Intent labels / icons and field labels, looked up once per request."""

    def __init__(self, db: Session, tenant_id: int):
        from app.intent_engine import intent_registry

        self.db, self.tenant_id, self.reg = db, tenant_id, intent_registry
        self._entries: dict[str, dict[str, Any] | None] = {}
        self.users = {u.id: u for u in db.query(User).filter(User.tenant_id == tenant_id)}

    def entry(self, code: str | None) -> dict[str, Any] | None:
        if not code:
            return None
        if code not in self._entries:
            self._entries[code] = self.reg.entry(self.db, self.tenant_id, code)
        return self._entries[code]

    def label(self, code: str | None) -> str:
        if code in SCREEN_ACTIONS:
            return SCREEN_ACTIONS[code][0]
        e = self.entry(code)
        return e["label"] if e else ("Not understood" if not code else code)

    def icon(self, code: str | None) -> str:
        if code in SCREEN_ACTIONS:
            return SCREEN_ACTIONS[code][1]
        e = self.entry(code)
        return e["icon"] if e else "❔"

    def field(self, code: str | None, key: str) -> str:
        e = self.entry(code)
        spec = next((f for f in (e or {}).get("fields", []) if f["key"] == key), None)
        return spec["label"] if spec else self.reg.field_spec(key).label

    def user(self, uid: int | None) -> dict[str, Any] | None:
        u = self.users.get(uid) if uid else None
        return {"id": u.id, "name": u.name or u.email, "email": u.email} if u else None


def records_of(log: IntentExecutionLog) -> list[dict[str, Any]]:
    """Records an execution touched: ids in its steps' results and state changes."""
    found: dict[tuple[str, int], dict[str, Any]] = {}

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ID_KEYS and isinstance(x, int):
                    found.setdefault((ID_KEYS[k], x), {"entity_type": ID_KEYS[k], "id": x, "change": None})
                if k == "state_entries" and isinstance(x, list):
                    for s in x:
                        if isinstance(s, dict) and isinstance(s.get("entity_id"), int):
                            key = (s.get("entity_type"), s["entity_id"])
                            found[key] = {"entity_type": s.get("entity_type"), "id": s["entity_id"],
                                          "change": "created" if not s.get("from_state") else f"{s['from_state']} → {s.get('to_state')}"}
                if k == "custom_records" and isinstance(x, list):
                    # records of a tenant-defined type carry their own label ("Site Survey SS-0004")
                    for c in x:
                        if isinstance(c, dict) and isinstance(c.get("entity_id"), int):
                            found[(c.get("entity_type"), c["entity_id"])] = {"entity_type": c.get("entity_type"), "id": c["entity_id"],
                                                                            "change": c.get("change"), "label": c.get("label")}
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(_loads(log.plan_json, []))
    entities = _loads(log.entities_json, {})
    for k, x in entities.items() if isinstance(entities, dict) else []:
        if k in ID_KEYS and isinstance(x, int):
            found.setdefault((ID_KEYS[k], x), {"entity_type": ID_KEYS[k], "id": x, "change": None})
    out = list(found.values())
    for r in out:
        r["label"] = r.get("label") or f"{ENTITY_LABEL.get(r['entity_type'], r['entity_type'])} #{r['id']}"
    return out


def _outcome(log: IntentExecutionLog, records: list[dict[str, Any]]) -> str:
    plan = _loads(log.plan_json, [])
    if log.status == "executed":
        created = [r["label"] for r in records if r["change"] == "created"]
        updated = [r["label"] for r in records if r["change"] == "updated"]
        deleted = [r["label"] for r in records if r["change"] == "deleted"]
        moved = [f"{r['label']} {r['change']}" for r in records if r["change"] and r["change"] not in ("created", "updated", "deleted")]
        parts = ([f"Created {', '.join(created)}"] if created else []) + ([f"Updated {', '.join(updated)}"] if updated else []) \
            + ([f"Deleted {', '.join(deleted)}"] if deleted else []) + ([f"Moved {', '.join(moved)}"] if moved else [])
        if parts:
            return "; ".join(parts)
        return f"Done in {len(plan)} step{'s' if len(plan) != 1 else ''}" if plan else "Done"
    if log.status == "failed":
        bad = next((s for s in plan if isinstance(s, dict) and s.get("status") not in ("ok", "compensated")), None)
        why = (bad or {}).get("error") or (bad or {}).get("detail") or log.rationale
        return f"Failed at {_human(str((bad or {}).get('action', 'a step')))}: {str(why)[:140]}"
    m = re.search(r"Missing/unresolved: (.+?)(?: - routed| -|$)", log.rationale or "")
    return f"Needs review - missing {m.group(1)}" if m else "Waiting for review"


def _row(log: IntentExecutionLog, names: _Names) -> dict[str, Any]:
    records = records_of(log)
    duration = (log.completed_at - log.created_at).total_seconds() if log.completed_at and log.created_at else None
    return {
        "id": log.id, "brt_id": log.brt_id, "at": log.created_at, "code": log.intent_type, "label": names.label(log.intent_type),
        "icon": names.icon(log.intent_type), "status": STATUS.get(log.status, log.status), "raw_status": log.status,
        "user": names.user(log.user_id), "channel": log.channel, "confidence": round(log.confidence or 0, 2), "text": log.raw_text,
        "outcome": _outcome(log, records), "records": records[:6], "duration_ms": round(duration * 1000) if duration is not None else None,
        "overridden": bool(log.human_override_json),
    }


def feed(db: Session, tenant_id: int, *, q: str | None = None, status: str | None = None, code: str | None = None, user_id: int | None = None,
         channel: str | None = None, days: int | None = 30, record: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    names = _Names(db, tenant_id)
    query = db.query(IntentExecutionLog).filter(IntentExecutionLog.tenant_id == tenant_id)
    if days:
        query = query.filter(IntentExecutionLog.created_at >= utcnow() - dt.timedelta(days=days))
    logs = query.order_by(IntentExecutionLog.id.desc()).limit(5000).all()

    # facets and counts over the period (before the narrower filters)
    stats = Counter(STATUS.get(log.status, log.status) for log in logs)
    durations = [(log.completed_at - log.created_at).total_seconds() for log in logs if log.completed_at and log.created_at]
    facets = {
        "actions": [{"code": c, "label": names.label(c), "icon": names.icon(c), "count": n} for c, n in Counter(log.intent_type for log in logs).most_common()],
        "users": [{"id": u, "name": (names.user(u) or {}).get("name", "System"), "count": n}
                  for u, n in Counter(log.user_id for log in logs).most_common() if u is not None],
        "channels": [{"channel": c, "count": n} for c, n in Counter(log.channel for log in logs).most_common()],
    }

    if status:
        wanted = {k for k, v in STATUS.items() if v == status} | {status}
        logs = [log for log in logs if log.status in wanted]
    if code:
        logs = [log for log in logs if log.intent_type == code]
    if user_id:
        logs = [log for log in logs if log.user_id == user_id]
    if channel:
        logs = [log for log in logs if log.channel == channel]
    if record:
        etype, _, rid = record.partition(":")
        logs = [log for log in logs if any(r["entity_type"] == etype and str(r["id"]) == rid for r in records_of(log))]
    if q:
        t = q.strip().lower()
        m = re.fullmatch(r"#?(\d+)", t)
        logs = [log for log in logs if (m and log.id == int(m.group(1))) or t in (log.raw_text or "").lower() or t in names.label(log.intent_type).lower()
                or t in ((names.user(log.user_id) or {}).get("name") or "").lower() or t in (log.entities_json or "").lower()]

    total = sum(stats.values())
    return {
        "total": len(logs), "items": [_row(log, names) for log in logs[offset:offset + limit]],
        "stats": {"total": total, "done": stats.get("done", 0), "review": stats.get("review", 0), "failed": stats.get("failed", 0),
                  "automation": round(stats.get("done", 0) / total * 100, 1) if total else None,
                  "avg_ms": round(sum(durations) / len(durations) * 1000) if durations else None},
        "facets": facets,
    }


def _pairs(d: Any, names: _Names, code: str | None, skip: set[str] | None = None) -> list[dict[str, Any]]:
    if not isinstance(d, dict):
        return []
    out = []
    for k, v in d.items():
        if skip and k in skip:
            continue
        if isinstance(v, (dict, list)):
            text = ", ".join(f"{a}: {b}" for a, b in v.items()) if isinstance(v, dict) and all(not isinstance(x, (dict, list)) for x in v.values()) \
                else ", ".join(str(x) for x in v) if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v) else None
            out.append({"key": k, "label": names.field(code, k), "value": text if text is not None else json.dumps(v, default=str)[:300], "complex": text is None})
        else:
            out.append({"key": k, "label": names.field(code, k), "value": "—" if v in (None, "") else v, "complex": False})
    return out


def story(db: Session, tenant_id: int, log: IntentExecutionLog) -> dict[str, Any]:
    names = _Names(db, tenant_id)
    row = _row(log, names)
    entry = names.entry(log.intent_type)
    plan = _loads(log.plan_json, [])
    context = _loads(log.context_json, {})
    checks = []
    for k, v in (context.items() if isinstance(context, dict) else []):
        if k == "intent_type":
            continue
        if v is None:
            checks.append({"label": _human(k), "ok": False, "value": "not found"})
        elif isinstance(v, dict):
            checks.append({"label": _human(k), "ok": True, "value": ", ".join(f"{a}: {b}" for a, b in list(v.items())[:4] if not isinstance(b, (dict, list)))})
        elif isinstance(v, list):
            checks.append({"label": _human(k), "ok": True, "value": f"{len(v)} item(s)"})
        else:
            checks.append({"label": _human(k), "ok": bool(v) or v == 0, "value": v})
    steps = []
    for s in plan if isinstance(plan, list) else []:
        if not isinstance(s, dict):
            continue
        detail = s.get("detail") if isinstance(s.get("detail"), dict) else {}
        steps.append({
            "n": s.get("step"), "action": _human(str(s.get("action", "step")).replace("flow:", "Flow: ")), "status": s.get("status"),
            "error": s.get("error") or (s.get("detail") if isinstance(s.get("detail"), str) else None),
            "detail": _pairs({k: v for k, v in detail.items() if k != "state_entries"}, names, log.intent_type),
        })
    events = [{"type": e.event_type, "label": _human(e.event_type.replace(".", " ")), "at": e.created_at, "payload": _loads(e.payload_json, {})}
              for e in db.query(DomainEvent).filter(DomainEvent.correlation_id == log.id).order_by(DomainEvent.id)]
    rationale = log.rationale or ""
    engine = "AI model" if re.search(r"\bLLM\b|model", rationale) else "Phrase rules" if re.search(r"Matched|phrase|keyword", rationale, re.I) \
        else "Form" if "Structured" in rationale else "—"
    return {
        **row, "description": entry["description"] if entry else "", "completed_at": log.completed_at, "engine": engine,
        "understood": _pairs(_loads(log.entities_json, {}), names, log.intent_type), "checks": checks, "rationale": rationale,
        "steps": steps, "records": records_of(log), "events": events, "override": _loads(log.human_override_json, None),
        "rerun": {"code": log.intent_type, "entities": _loads(log.entities_json, {})} if log.intent_type and log.status != "executed" else None,
        "raw": {"entities": _loads(log.entities_json, {}), "context": context, "plan": plan},
    }
