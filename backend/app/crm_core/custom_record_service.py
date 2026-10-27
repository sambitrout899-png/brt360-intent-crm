"""Custom record types and their records - see app/models/custom_records.py.

A type's own fields are ordinary custom fields (custom_field_service) under
the entity_type "x_<key>", so validation, defaults and typed values work the
same as the extra fields of the built-in records.

Visibility follows the platform's rule for records without an ownership
entry (access_service._created_visible_ids): a user sees the records whose
creator / creating position / department fall inside their view - and, on
top of that, every record hanging off a parent record they can see (the
surveys of a customer they work on).

Every change is written to the audit trail as a screen action
(REC-CREATE / REC-UPDATE / REC-DELETE, audit_service.SCREEN_ACTIONS).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.crm_core import access_service, custom_field_service, lifecycle_service
from app.crm_core.access_service import Access
from app.data_intelligence import audit, event_bus
from app.models.auth import User
from app.models.custom_fields import CustomFieldDefinition, CustomFieldValue
from app.models.custom_records import ENTITY_PREFIX, KEY_MAX_LENGTH, CustomRecord, CustomRecordType

# Built-in records a custom record can hang off.
PARENT_TYPES: dict[str, str] = {"party": "Customer", "lead": "Lead", "opportunity": "Opportunity", "quote": "Quote", "order": "Order"}
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_PREFIX = re.compile(r"^[A-Z0-9]{1,10}$")
# names a type's fields may not take - they are the record's own columns
RESERVED_FIELD_KEYS = {"id", "name", "number", "parent_id", "parent", "entity_type", "entity_id", "state", "created_at", "updated_at"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:KEY_MAX_LENGTH].strip("_")


def is_custom(entity_type: str | None) -> bool:
    return bool(entity_type) and str(entity_type).startswith(ENTITY_PREFIX)


def _key_of(key_or_entity_type: str) -> str:
    raw = str(key_or_entity_type or "").strip().lower()
    return raw[len(ENTITY_PREFIX):] if raw.startswith(ENTITY_PREFIX) else raw


# ---------------------------------------------------------------- types


def list_types(db: Session, tenant_id: int, active_only: bool = False) -> list[CustomRecordType]:
    query = db.query(CustomRecordType).filter(CustomRecordType.tenant_id == tenant_id)
    if active_only:
        query = query.filter(CustomRecordType.is_active.is_(True))
    return query.order_by(CustomRecordType.label).all()


def get_type(db: Session, tenant_id: int, key_or_entity_type: str, active_only: bool = False) -> CustomRecordType | None:
    rt = db.query(CustomRecordType).filter(CustomRecordType.tenant_id == tenant_id, CustomRecordType.key == _key_of(key_or_entity_type)).first()
    if rt is not None and active_only and not rt.is_active:
        return None
    return rt


def parent_type_label(db: Session, tenant_id: int, entity_type: str | None) -> str | None:
    if not entity_type:
        return None
    if entity_type in PARENT_TYPES:
        return PARENT_TYPES[entity_type]
    rt = get_type(db, tenant_id, entity_type)
    return rt.label if rt else entity_type


def parent_options(db: Session, tenant_id: int, exclude_key: str | None = None) -> list[dict[str, str]]:
    """What a type's records may hang off: the built-in records, then the tenant's other types."""
    return [{"value": k, "label": v} for k, v in PARENT_TYPES.items()] + [
        {"value": rt.entity_type, "label": rt.label} for rt in list_types(db, tenant_id) if rt.key != exclude_key
    ]


def _check_parent_type(db: Session, tenant_id: int, parent_entity_type: str | None, own_key: str) -> str | None:
    if not parent_entity_type:
        return None
    if parent_entity_type in PARENT_TYPES:
        return parent_entity_type
    parent = get_type(db, tenant_id, parent_entity_type) if is_custom(parent_entity_type) else None
    if parent is None:
        raise ValueError(f"Records can hang off: {', '.join(o['value'] for o in parent_options(db, tenant_id, own_key))}")
    # no loops: walking up from the parent must never reach this type
    seen, current = {own_key}, parent
    while current is not None:
        if current.key in seen:
            raise ValueError(f"{parent.label} already hangs off this type (directly or further up) - that would make a loop")
        seen.add(current.key)
        current = get_type(db, tenant_id, current.parent_entity_type) if is_custom(current.parent_entity_type) else None
    return parent.entity_type


def _default_prefix(label: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", label)
    prefix = "".join(w[0] for w in words).upper() if len(words) > 1 else (words[0][:3].upper() if words else "REC")
    return prefix[:10] or "REC"


def _clean_prefix(raw: str) -> str:
    prefix = str(raw or "").strip().upper()
    if not _PREFIX.fullmatch(prefix):
        raise ValueError("The number prefix is 1-10 capital letters or digits (e.g. SS, which numbers records SS-0001)")
    return prefix


def create_type(
    db: Session, tenant_id: int, *, label: str, plural_label: str | None = None, key: str | None = None, description: str = "",
    icon: str | None = None, number_prefix: str | None = None, parent_entity_type: str | None = None, parent_required: bool = False,
) -> CustomRecordType:
    label = str(label or "").strip()
    if not label:
        raise ValueError("A record type needs a name")
    key = slug(key) if key else slug(label)
    if not key or not _KEY.fullmatch(key):
        raise ValueError("The key starts with a letter and has only lower-case letters, digits and _")
    if len(key) > KEY_MAX_LENGTH:
        raise ValueError(f"The key is at most {KEY_MAX_LENGTH} characters")
    if get_type(db, tenant_id, key) is not None:
        raise ValueError(f"A record type with the key '{key}' already exists")
    parent = _check_parent_type(db, tenant_id, parent_entity_type, key)
    rt = CustomRecordType(
        tenant_id=tenant_id, key=key, label=label[:100], plural_label=(str(plural_label or "").strip() or f"{label}s")[:100],
        description=str(description or "").strip(), icon=(str(icon or "").strip() or "📄")[:10],
        number_prefix=_clean_prefix(number_prefix) if number_prefix else _default_prefix(label), next_number=1,
        parent_entity_type=parent, parent_required=bool(parent_required and parent), is_active=True,
    )
    db.add(rt)
    db.flush()
    return rt


def record_count(db: Session, rt: CustomRecordType) -> int:
    return db.query(CustomRecord.id).filter(CustomRecord.type_id == rt.id).count()


def update_type(db: Session, tenant_id: int, rt: CustomRecordType, changes: dict[str, Any]) -> CustomRecordType:
    """The key never changes (fields, flows and history refer to it); the parent only while the type has no records."""
    if "label" in changes:
        label = str(changes["label"] or "").strip()
        if not label:
            raise ValueError("A record type needs a name")
        rt.label = label[:100]
    if "plural_label" in changes:
        rt.plural_label = (str(changes["plural_label"] or "").strip() or f"{rt.label}s")[:100]
    if "description" in changes:
        rt.description = str(changes["description"] or "").strip()
    if "icon" in changes:
        rt.icon = (str(changes["icon"] or "").strip() or "📄")[:10]
    if "number_prefix" in changes:
        rt.number_prefix = _clean_prefix(changes["number_prefix"])  # numbers already given keep their old prefix
    if "is_active" in changes:
        rt.is_active = bool(changes["is_active"])
    if "parent_entity_type" in changes and (changes["parent_entity_type"] or None) != rt.parent_entity_type:
        if record_count(db, rt):
            raise ValueError(f"{rt.plural_label} already exist - what they hang off can no longer change")
        rt.parent_entity_type = _check_parent_type(db, tenant_id, changes["parent_entity_type"], rt.key)
    if "parent_required" in changes:
        rt.parent_required = bool(changes["parent_required"] and rt.parent_entity_type)
    db.flush()
    return rt


def delete_type(db: Session, tenant_id: int, rt: CustomRecordType) -> None:
    count = record_count(db, rt)
    if count:
        raise ValueError(f"{count} {rt.plural_label.lower()} exist - delete them first, or switch the type off instead")
    children = [t.label for t in list_types(db, tenant_id) if t.parent_entity_type == rt.entity_type]
    if children:
        raise ValueError(f"{', '.join(children)} hang{'s' if len(children) == 1 else ''} off {rt.label} - change that first")
    field_ids = [fid for (fid,) in db.query(CustomFieldDefinition.id).filter(
        CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.entity_type == rt.entity_type)]
    if field_ids:
        db.query(CustomFieldValue).filter(CustomFieldValue.field_id.in_(field_ids)).delete(synchronize_session=False)
        db.query(CustomFieldDefinition).filter(CustomFieldDefinition.id.in_(field_ids)).delete(synchronize_session=False)
    db.delete(rt)
    db.flush()


def type_out(db: Session, tenant_id: int, rt: CustomRecordType, with_fields: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": rt.id, "key": rt.key, "entity_type": rt.entity_type, "label": rt.label, "plural_label": rt.plural_label,
        "description": rt.description, "icon": rt.icon, "number_prefix": rt.number_prefix,
        "parent_entity_type": rt.parent_entity_type, "parent_label": parent_type_label(db, tenant_id, rt.parent_entity_type),
        "parent_required": rt.parent_required, "is_active": rt.is_active, "record_count": record_count(db, rt),
    }
    if with_fields:
        out["fields"] = [field_out(d) for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type)]
    return out


def field_out(d: CustomFieldDefinition) -> dict[str, Any]:
    return {"id": d.id, "key": d.key, "label": d.label, "data_type": d.data_type, "allowed_values": custom_field_service.allowed_values(d),
            "required": d.required, "default_value": d.default_value, "section": d.section, "help_text": d.help_text}


# ---------------------------------------------------------------- parents


def _record_title(entity_type: str, obj) -> str:
    if isinstance(obj, CustomRecord):
        return f"{obj.number} · {obj.name}"
    if entity_type in ("party", "opportunity"):
        return obj.name
    return f"{PARENT_TYPES.get(entity_type, entity_type)} #{obj.id}"


def get_parent(db: Session, tenant_id: int, parent_entity_type: str | None, parent_id: Any):
    if not parent_entity_type or parent_id in (None, ""):
        return None
    if parent_entity_type in PARENT_TYPES:
        return lifecycle_service.get_record(db, tenant_id, parent_entity_type, parent_id)
    parent_type = get_type(db, tenant_id, parent_entity_type)
    return get_record(db, tenant_id, parent_type, parent_id) if parent_type else None


def parent_ref(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord) -> dict[str, Any] | None:
    if rec.parent_id is None or not rt.parent_entity_type:
        return None
    obj = get_parent(db, tenant_id, rt.parent_entity_type, rec.parent_id)
    return {"entity_type": rt.parent_entity_type, "id": rec.parent_id,
            "title": _record_title(rt.parent_entity_type, obj) if obj is not None else f"#{rec.parent_id} (no longer exists)",
            "custom_key": _key_of(rt.parent_entity_type) if is_custom(rt.parent_entity_type) else None}


def search_parents(db: Session, access: Access, rt: CustomRecordType, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Records of the parent type the user may see, for the record form's picker."""
    parent_type = rt.parent_entity_type
    if not parent_type:
        return []
    text = (q or "").strip()
    if is_custom(parent_type):
        prt = get_type(db, access.tenant_id, parent_type)
        if prt is None:
            return []
        query = visible_query(db, access, prt)
        if text:
            query = query.filter(or_(CustomRecord.name.ilike(f"%{text}%"), CustomRecord.number.ilike(f"%{text}%")))
        return [{"id": r.id, "title": _record_title(parent_type, r)} for r in query.order_by(CustomRecord.id.desc()).limit(limit)]
    model = lifecycle_service.ENTITY_MODELS[parent_type]
    query = access_service.apply(db.query(model).filter(model.tenant_id == access.tenant_id), model, db, access, parent_type)
    if text:
        if text.lstrip("#").isdigit():
            query = query.filter(model.id == int(text.lstrip("#")))
        elif hasattr(model, "name"):
            query = query.filter(model.name.ilike(f"%{text}%"))
    return [{"id": o.id, "title": _record_title(parent_type, o)} for o in query.order_by(model.id.desc()).limit(limit)]


# ---------------------------------------------------------------- visibility


def visible_ids(db: Session, access: Access, rt: CustomRecordType) -> set[int] | None:
    """Ids of this type's records the user may see; None = all."""
    if access.unrestricted:
        return None
    v = access.visibility
    conditions = [CustomRecord.sys_created_by == access.user.id]
    if v.user_ids:
        conditions.append(CustomRecord.sys_created_by.in_(v.user_ids))
    if v.position_ids:
        conditions.append(CustomRecord.sys_created_position_id.in_(v.position_ids))
    if v.department_ids:
        conditions.append(CustomRecord.sys_department_id.in_(v.department_ids))
    if rt.parent_entity_type in PARENT_TYPES:
        parents = access_service.visible_ids(db, access, rt.parent_entity_type)
        if parents:
            conditions.append(CustomRecord.parent_id.in_(parents))
    elif is_custom(rt.parent_entity_type):
        parent_type = get_type(db, access.tenant_id, rt.parent_entity_type)
        parents = visible_ids(db, access, parent_type) if parent_type else set()
        if parents:
            conditions.append(CustomRecord.parent_id.in_(parents))
    rows = db.query(CustomRecord.id).filter(CustomRecord.tenant_id == access.tenant_id, CustomRecord.type_id == rt.id, or_(*conditions))
    return {rid for (rid,) in rows.all()}


def visible_query(db: Session, access: Access, rt: CustomRecordType):
    query = db.query(CustomRecord).filter(CustomRecord.tenant_id == access.tenant_id, CustomRecord.type_id == rt.id)
    ids = visible_ids(db, access, rt)
    return query if ids is None else query.filter(CustomRecord.id.in_(ids or {-1}))


def can_see(db: Session, access: Access, rt: CustomRecordType, rec: CustomRecord) -> bool:
    ids = visible_ids(db, access, rt)
    return ids is None or rec.id in ids


# ---------------------------------------------------------------- records


def get_record(db: Session, tenant_id: int, rt: CustomRecordType, record_id: Any) -> CustomRecord | None:
    try:
        rec = db.get(CustomRecord, int(record_id))
    except (TypeError, ValueError):
        return None
    if rec is None or rec.tenant_id != tenant_id or rec.type_id != rt.id:
        return None
    return rec


def _set_parent(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord, parent_id: Any) -> None:
    if parent_id in (None, ""):
        if rt.parent_required:
            raise ValueError(f"A {rt.label} needs a {parent_type_label(db, tenant_id, rt.parent_entity_type)}")
        rec.parent_id = None
        return
    if not rt.parent_entity_type:
        raise ValueError(f"{rt.plural_label} don't hang off another record")
    parent = get_parent(db, tenant_id, rt.parent_entity_type, parent_id)
    if parent is None:
        raise ValueError(f"No {parent_type_label(db, tenant_id, rt.parent_entity_type)} #{parent_id}")
    if isinstance(parent, CustomRecord) and rec.id is not None:
        # a record can't sit under itself
        node: CustomRecord | None = parent
        while node is not None:
            if node.id == rec.id and node.type_id == rec.type_id:
                raise ValueError("A record can't hang off itself or one of the records under it")
            node_type = node.record_type
            node = get_parent(db, tenant_id, node_type.parent_entity_type, node.parent_id) if is_custom(node_type.parent_entity_type) else None
            node = node if isinstance(node, CustomRecord) else None
    rec.parent_id = parent.id


def _set_fields(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord, fields: dict[str, Any]) -> dict[str, Any]:
    applied: dict[str, Any] = {}
    unknown: list[str] = []
    for raw_key, value in (fields or {}).items():
        key = str(raw_key).strip().lower().replace(" ", "_")
        defn = custom_field_service.definition_by_key(db, tenant_id, rt.entity_type, key)
        if defn is None:
            unknown.append(key)
            continue
        applied[key] = custom_field_service.set_value(db, tenant_id, rt.entity_type, rec.id, defn, value)
    if unknown:
        known = [d.key for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type)]
        raise ValueError(f"Unknown field(s) for {rt.label}: {', '.join(unknown)}. Fields: {', '.join(known) or 'none yet'}")
    return applied


def _check_required(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord) -> None:
    missing = custom_field_service.missing_required(db, tenant_id, rt.entity_type, rec.id)
    if missing:
        labels = {d.key: d.label for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type)}
        raise ValueError(f"Fill in: {', '.join(labels.get(k, k) for k in missing)}")


def create_record(db: Session, tenant_id: int, user: User, rt: CustomRecordType, name: str, parent_id: Any = None,
                  fields: dict[str, Any] | None = None) -> CustomRecord:
    if not rt.is_active:
        raise ValueError(f"{rt.plural_label} are switched off")
    name = str(name or "").strip()
    if not name:
        raise ValueError(f"A {rt.label} needs a name")
    number = f"{rt.number_prefix}-{rt.next_number:04d}"
    rt.next_number += 1
    rec = CustomRecord(tenant_id=tenant_id, type_id=rt.id, number=number, name=name[:200])
    rec.record_type = rt
    _set_parent(db, tenant_id, rt, rec, parent_id)
    db.add(rec)
    db.flush()
    applied = _set_fields(db, tenant_id, rt, rec, fields or {})
    _check_required(db, tenant_id, rt, rec)
    _audit(db, tenant_id, user, "REC-CREATE", rt, rec, "created", {"name": rec.name, "parent_id": rec.parent_id, **applied})
    return rec


def update_record(db: Session, tenant_id: int, user: User, rt: CustomRecordType, rec: CustomRecord, changes: dict[str, Any]) -> CustomRecord:
    applied: dict[str, Any] = {}
    if "name" in changes:
        name = str(changes["name"] or "").strip()
        if not name:
            raise ValueError(f"A {rt.label} needs a name")
        rec.name = name[:200]
        applied["name"] = rec.name
    if "parent_id" in changes:
        _set_parent(db, tenant_id, rt, rec, changes["parent_id"])
        applied["parent_id"] = rec.parent_id
    applied.update(_set_fields(db, tenant_id, rt, rec, changes.get("fields") or {}))
    if not applied:
        raise ValueError("Nothing to change")
    _check_required(db, tenant_id, rt, rec)
    db.flush()
    _audit(db, tenant_id, user, "REC-UPDATE", rt, rec, "updated", applied)
    return rec


def children_of(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord) -> list[dict[str, Any]]:
    """The records of other custom types hanging off this one, by type."""
    out = []
    for child_type in list_types(db, tenant_id):
        if child_type.parent_entity_type != rt.entity_type:
            continue
        count = db.query(CustomRecord.id).filter(CustomRecord.type_id == child_type.id, CustomRecord.parent_id == rec.id).count()
        if count:
            out.append({"key": child_type.key, "label": child_type.plural_label, "count": count})
    return out


def delete_record(db: Session, tenant_id: int, user: User, rt: CustomRecordType, rec: CustomRecord) -> None:
    children = children_of(db, tenant_id, rt, rec)
    if children:
        what = ", ".join(f"{c['count']} {c['label'].lower()}" for c in children)
        raise ValueError(f"{what} hang off {rec.number} - delete or move them first")
    snapshot = {"name": rec.name, "parent_id": rec.parent_id, **custom_field_service.get_values(db, tenant_id, rt.entity_type, rec.id)}
    _audit(db, tenant_id, user, "REC-DELETE", rt, rec, "deleted", snapshot)
    db.query(CustomFieldValue).filter(CustomFieldValue.tenant_id == tenant_id, CustomFieldValue.entity_type == rt.entity_type,
                                      CustomFieldValue.entity_id == rec.id).delete(synchronize_session=False)
    db.delete(rec)
    db.flush()


def record_out(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": rec.id, "number": rec.number, "name": rec.name, "parent": parent_ref(db, tenant_id, rt, rec),
        "values": custom_field_service.get_values(db, tenant_id, rt.entity_type, rec.id),
        "created_at": rec.created_at, "updated_at": rec.updated_at,
    }
    if detail:
        creator = db.get(User, rec.sys_created_by) if rec.sys_created_by else None
        out.update(brt_id=rec.brt_id, created_by=(creator.name or creator.email) if creator else None,
                   children=children_of(db, tenant_id, rt, rec))
    return out


# ---------------------------------------------------------------- audit


def _audit(db: Session, tenant_id: int, user: User, code: str, rt: CustomRecordType, rec: CustomRecord, change: str,
           values: dict[str, Any]) -> None:
    label = f"{rt.label} {rec.number}"
    verb = {"created": "Created", "updated": "Updated", "deleted": "Deleted"}[change]
    log = audit.start_execution(db, tenant_id, f"{verb} {label} - {rec.name}"[:4000], channel="screen")
    log.user_id = user.id
    audit.record_classification(log, code, {"entity_type": rt.entity_type, "entity_id": rec.id, "record_type": rt.label,
                                            "number": rec.number, **values}, 1.0, f"{verb} on the {rt.plural_label} page")
    audit.record_context(log, {})
    audit.record_plan(log, [{"action": _ACTIONS[change], "status": "ok", "result": {"custom_records": [
        {"entity_type": rt.entity_type, "entity_id": rec.id, "change": change, "label": label}]}}])
    db.flush()
    event_bus.publish(db, log.id, f"custom_record.{change}", {"record_type": rt.key, "id": rec.id, "number": rec.number})
    audit.complete(log, "executed")


_ACTIONS = {"created": "create_custom_record", "updated": "update_custom_record", "deleted": "delete_custom_record"}
