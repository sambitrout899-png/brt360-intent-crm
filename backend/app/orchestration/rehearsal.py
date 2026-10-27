"""Rehearsal - see the future before you commit.

An intent can be rehearsed instead of run: it goes through exactly the real
path (classification, availability and visibility checks, approvals, the
saga, before/after flows, state-entry flow cascades, assignment rules,
notifications), every row the run writes is recorded, and then the whole
transaction is rolled back. Nothing persists - no record, no audit log, no
event, no notification, no BRT_ID number (the register is updated on the same
connection).

This is safe because every side effect a step can have is a row in this
database (orchestrator.py; integration/notification_adapter.py records a
notification as a domain event). What comes back is the run's consequences in
business terms: the records it creates, the states it moves, who gets the
work, who is told, which approvals open, which flows fire, the money that
changes - and warnings for anything that is hard to take back.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

# Bookkeeping rows - reported through what they mean, never as raw rows.
_BOOKKEEPING = {
    "intent_execution_log", "domain_events", "entity_record_meta", "entity_state_history",
    "entity_assignment_history", "approval_step_instances", "approval_chain_instances", "brt_id_counter",
}
_SYSTEM = {"id", "tenant_id", "brt_id", "created_at", "updated_at"}
_MONEY = ("price", "amount", "total", "balance", "discount", "charge", "revenue", "value", "cost", "fee", "credit")
_FINAL_STATES = {"cancelled", "canceled", "closed", "lost", "rejected", "terminated", "completed", "disconnected", "void", "written_off"}
_NAME_FIELDS = ("name", "title", "number", "order_number", "quote_number", "label", "subject", "code")


@contextmanager
def capture(db: Session) -> Iterator[list[tuple[str, Any, dict]]]:
    """Records every row written while the block runs: (op, object, fields).
    Created rows carry their values; changed rows carry {field: (old, new)}."""
    rows: list[tuple[str, Any, dict]] = []

    def after_flush(session: Session, _ctx) -> None:
        for obj in session.new:
            rows.append(("create", obj, _values(obj)))
        for obj in session.dirty:
            changes = _changes(obj)
            if changes:
                rows.append(("update", obj, changes))
        for obj in session.deleted:
            rows.append(("delete", obj, _values(obj)))

    event.listen(db, "after_flush", after_flush)
    try:
        yield rows
    finally:
        event.remove(db, "after_flush", after_flush)


def _values(obj: Any) -> dict:
    return {a.key: getattr(obj, a.key, None) for a in inspect(obj).mapper.column_attrs}


def _changes(obj: Any) -> dict:
    state = inspect(obj)
    out = {}
    for attr in state.mapper.column_attrs:
        hist = state.attrs[attr.key].history
        if hist.added:
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0]
            if old != new:
                out[attr.key] = (old, new)
    return out


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return getattr(value, "value", None) or str(value)


def _entity(table: str) -> str:
    name = table[3:] if table.startswith(("cm_", "om_", "fs_")) else table
    name = name[:-3] + "y" if name.endswith("ies") else name[:-1] if name.endswith("s") and not name.endswith("ss") else name
    return name.replace("_", " ")


def _label(table: str, values: dict, pk: Any) -> str:
    name = next((values.get(f) for f in _NAME_FIELDS if values.get(f) not in (None, "")), None)
    base = f"{_entity(table).capitalize()} #{pk}"
    return f"{base} ({name})" if name else base


def _visible(key: str) -> bool:
    return key not in _SYSTEM and not key.startswith("sys_") and not key.endswith("_json")


def _is_money(key: str, value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and any(w in key for w in _MONEY) and not key.endswith("_id")


def _names(db: Session, model, ids: set[int]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {r.id: getattr(r, "name", None) or getattr(r, "email", None) or f"#{r.id}" for r in db.query(model).filter(model.id.in_(ids))}


def summarize(db: Session, rows: list[tuple[str, Any, dict]], collected: dict | None = None) -> dict:
    """Turns the captured rows into the consequences a person cares about.
    Called before the rollback, while the rows are still readable."""
    from app.models.auth import User
    from app.models.lifecycle import Position
    from app.models.org import Department

    # One entry per row: a row created and then changed is reported once, as created.
    created: dict[int, tuple[Any, dict]] = {}
    updated: dict[int, tuple[Any, dict]] = {}
    deleted: list[tuple[Any, dict]] = []
    for op, obj, fields in rows:
        key = id(obj)
        if op == "create":
            created[key] = (obj, dict(fields))
        elif op == "update" and key in created:
            created[key][1].update({k: new for k, (_old, new) in fields.items()})
        elif op == "update":
            prior = updated.get(key, (obj, {}))[1]
            for k, (old, new) in fields.items():
                prior[k] = (prior[k][0] if k in prior else old, new)
            updated[key] = (obj, prior)
        elif op == "delete":
            created.pop(key, None)
            updated.pop(key, None)
            deleted.append((obj, fields))

    moves, assignments, notifications, other_events, approvals = [], [], [], [], []
    records_created, records_changed, records_deleted, money, warnings = [], [], [], [], []
    assign_rows = []

    for obj, values in created.values():
        table = obj.__table__.name
        if table == "entity_state_history":
            moves.append({"record": f"{_entity(values['entity_type']).capitalize()} #{values['entity_id']}",
                          "entity_type": values["entity_type"], "entity_id": values["entity_id"],
                          "from": values.get("from_state"), "to": values.get("to_state"), "by": values.get("source") or ""})
        elif table == "entity_assignment_history":
            assign_rows.append(values)
        elif table == "domain_events":
            try:
                payload = json.loads(values.get("payload_json") or "{}")
            except (TypeError, ValueError):
                payload = {}
            if values.get("event_type") == "notification.sent":
                notifications.append({"channel": payload.get("channel") or "", "audience": payload.get("audience") or "",
                                      "message": payload.get("message") or ""})
            else:
                other_events.append(values.get("event_type") or "")
        elif table == "approval_chain_instances":
            approvals.append({"record": f"{_entity(values['entity_type']).capitalize()} #{values['entity_id']}",
                              "gate": values.get("gate") or "", "summary": values.get("summary") or "",
                              "status": _plain(values.get("status")), "tiers": [], "_id": values.get("id")})
        elif table not in _BOOKKEEPING:
            pk = values.get("id")
            records_created.append({"entity": _entity(table), "id": pk, "label": _label(table, values, pk)})
            for k, v in values.items():
                if _visible(k) and _is_money(k, v) and v:
                    money.append({"record": _label(table, values, pk), "field": k.replace("_", " "), "from": None, "to": v})

    for obj, values in created.values():
        if obj.__table__.name == "approval_step_instances":
            for a in approvals:
                if a["_id"] == values.get("chain_instance_id"):
                    a["tiers"].append(values.get("approver_tier") or "")

    for obj, changes in updated.values():
        table = obj.__table__.name
        shown = {k: (_plain(o), _plain(n)) for k, (o, n) in changes.items() if _visible(k)}
        if table in ("approval_chain_instances", "approval_step_instances"):
            status = shown.get("status")
            if status and table == "approval_chain_instances":
                approvals.append({"record": f"{_entity(obj.entity_type).capitalize()} #{obj.entity_id}", "gate": obj.gate or "",
                                  "summary": obj.summary or "", "status": status[1], "tiers": [], "_id": obj.id})
            continue
        if table in _BOOKKEEPING or not shown:
            continue
        values = _values(obj)
        label = _label(table, values, values.get("id"))
        records_changed.append({"entity": _entity(table), "id": values.get("id"), "label": label,
                                "fields": [{"field": k.replace("_", " "), "from": o, "to": n} for k, (o, n) in shown.items()]})
        for k, (o, n) in shown.items():
            if _is_money(k, n) or _is_money(k, o):
                money.append({"record": label, "field": k.replace("_", " "), "from": o, "to": n})

    for obj, values in deleted:
        table = obj.__table__.name
        if table not in _BOOKKEEPING:
            records_deleted.append({"entity": _entity(table), "id": values.get("id"), "label": _label(table, values, values.get("id"))})

    # who gets the work - names resolved now, while everything is readable
    users = _names(db, User, {a.get("to_user_id") for a in assign_rows})
    positions = _names(db, Position, {a.get("to_position_id") for a in assign_rows})
    departments = _names(db, Department, {a.get("to_department_id") for a in assign_rows})
    for a in assign_rows:
        to = [n for n in (users.get(a.get("to_user_id")), positions.get(a.get("to_position_id")), departments.get(a.get("to_department_id"))) if n]
        assignments.append({"record": f"{_entity(a['entity_type']).capitalize()} #{a['entity_id']}", "to": " / ".join(to) or "nobody",
                            "by": a.get("source") or ""})

    for a in approvals:
        a.pop("_id", None)
    flows = [{"name": f.get("flow_name") or f"Flow #{f.get('flow_id')}", "steps": len(f.get("trace") or [])}
             for f in (collected or {}).get("flows_run", []) if not f.get("skipped")]

    for m in moves:
        if (m["to"] or "").lower() in _FINAL_STATES:
            warnings.append(f"{m['record']} ends up {m['to']} - a final state that is hard to undo.")
    if records_deleted:
        warnings.append(f"{len(records_deleted)} record{'s' if len(records_deleted) > 1 else ''} would be deleted.")
    told_customer = [n for n in notifications if any(w in (n["audience"] or "").lower() for w in ("customer", "client", "contact", "@"))]
    if told_customer:
        warnings.append(f"{len(told_customer)} message{'s' if len(told_customer) > 1 else ''} would go to the customer - those can't be unsent.")
    cascade = [m for m in moves if m["by"].startswith("flow")]
    if cascade:
        warnings.append(f"Automations would move {len(cascade)} more record{'s' if len(cascade) > 1 else ''} on their own (a chain reaction).")

    impact = {
        "records_created": records_created,
        "records_changed": records_changed,
        "records_deleted": records_deleted,
        "state_moves": moves,
        "assignments": assignments,
        "approvals": approvals,
        "notifications": notifications,
        "flows": flows,
        "money": money,
        "events": sorted(set(other_events)),
        "warnings": warnings,
    }
    impact["headline"] = headline(impact)
    impact["blast_radius"] = len({r["label"] for r in records_created + records_changed + records_deleted} | {m["record"] for m in moves})
    return impact


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def headline(impact: dict) -> str:
    """One sentence a person can read in two seconds."""
    parts = []
    if impact["records_created"]:
        kinds: dict[str, int] = {}
        for r in impact["records_created"]:
            kinds[r["entity"]] = kinds.get(r["entity"], 0) + 1
        parts.append("create " + ", ".join(_count(n, k) for k, n in list(kinds.items())[:4]))
    if impact["state_moves"]:
        first = impact["state_moves"][0]
        more = len(impact["state_moves"]) - 1
        parts.append(f"move {first['record']} to {first['to']}" + (f" (+{more} more)" if more else ""))
    # a record whose only change is the state move above is not counted twice
    moved = {m["record"] for m in impact["state_moves"]}
    changed = [r for r in impact["records_changed"]
               if not (r["label"].split(" (")[0] in moved and {f["field"] for f in r["fields"]} <= {"status", "state"})]
    if changed:
        parts.append("update " + _count(len(changed), "other record"))
    if impact["assignments"]:
        parts.append("hand work to " + ", ".join(sorted({a["to"] for a in impact["assignments"]})))
    if impact["approvals"]:
        parts.append("open " + _count(len(impact["approvals"]), "approval"))
    if impact["flows"]:
        parts.append("run " + _count(len(impact["flows"]), "automation"))
    if impact["notifications"]:
        parts.append("send " + _count(len(impact["notifications"]), "notification"))
    if not parts:
        return "This would change nothing."
    text = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    return "This would " + text + "."
