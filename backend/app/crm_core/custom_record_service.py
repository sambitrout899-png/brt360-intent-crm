"""Custom record types and their records - see app/models/custom_records.py.

A tenant's own record type ("x_<key>") is a first-class record of the platform, run by the same engines as
the built-in customer / lead / opportunity / quote / order:

- fields: ordinary custom fields (custom_field_service) under the entity_type "x_<key>" - typed, defaulted,
  required - including lookups (data type "lookup") to one record of another type, built-in or custom;
- relationships: the parent a type hangs off (one-to-many), lookup fields, and many-to-many relationships
  an administrator defines between two types (RecordRelationship / RecordLink);
- lifecycle: a state model (lifecycle_service - Admin -> State models), an owner, assignment rules and
  state history; a record starts in its model's first state;
- pages and validation: the Page Designer lays out its page, and its validation rules (plain English,
  compiled by the rule compiler, or a Flow Studio flow) guard every save - including a state change, which
  is checked with the record's new state so "a completed survey needs its report" holds;
- flows: before / after flows on its intents (INT-151..157, intent_engine/record_intents.py), state-enter
  flows, Set Field / Change State / Assign steps;
- visibility: a user sees the records they (or their team / department) created or own, and every record
  hanging off a parent record they can see.

Records change through the intent pipeline - the Records page, the command bar and flows alike - so every
change is audited, guarded by flows and followed by the lifecycle hook.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.crm_core import access_service, custom_field_service, lifecycle_service
from app.crm_core.access_service import Access
from app.models.auth import User
from app.models.custom_fields import CustomFieldDefinition, CustomFieldValue
from app.models.custom_records import ENTITY_PREFIX, KEY_MAX_LENGTH, CustomRecord, CustomRecordType, RecordLink, RecordRelationship

# Built-in records a custom record can hang off, point to (lookup) or be related to.
PARENT_TYPES: dict[str, str] = {"party": "Customer", "lead": "Lead", "opportunity": "Opportunity", "quote": "Quote", "order": "Order"}
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_PREFIX = re.compile(r"^[A-Z0-9]{1,10}$")
_NUMBER = re.compile(r"^\s*([A-Za-z0-9]{1,10})-(\d{1,9})\s*$")
# names a type's fields may not take - they are the record's own columns
RESERVED_FIELD_KEYS = {"id", "name", "number", "parent_id", "parent", "entity_type", "entity_id", "state", "created_at", "updated_at",
                       "owner", "record_type"}
LIST_FIELDS_DEFAULT = 4


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


def type_named(db: Session, tenant_id: int, text: str) -> CustomRecordType | None:
    """A type by its key, label or plural ("site survey", "Site Surveys", "x_site_survey")."""
    raw = str(text or "").strip()
    if not raw:
        return None
    found = get_type(db, tenant_id, raw) or get_type(db, tenant_id, slug(raw))
    if found is not None:
        return found
    lowered = raw.lower()
    return next((rt for rt in list_types(db, tenant_id) if lowered in (rt.label.lower(), rt.plural_label.lower())), None)


def label_of(db: Session, tenant_id: int, entity_type: str | None) -> str | None:
    if not entity_type:
        return None
    if entity_type in PARENT_TYPES:
        return PARENT_TYPES[entity_type]
    rt = get_type(db, tenant_id, entity_type)
    return rt.label if rt else entity_type


parent_type_label = label_of


def entity_options(db: Session, tenant_id: int, exclude_key: str | None = None) -> list[dict[str, str]]:
    """What a type's records may hang off, point to or be related to: the built-in records, then the tenant's types."""
    return [{"value": k, "label": v} for k, v in PARENT_TYPES.items()] + [
        {"value": rt.entity_type, "label": rt.label} for rt in list_types(db, tenant_id) if rt.key != exclude_key
    ]


parent_options = entity_options


def check_target(db: Session, tenant_id: int, raw: str | None) -> str:
    """A record type something can point to - a built-in one or one of the tenant's - by key, label or name."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Say which record type it points to")
    lowered = lifecycle_service.ENTITY_TYPE_ALIASES.get(text.lower(), text.lower())
    if lowered in PARENT_TYPES:
        return lowered
    named = next((k for k, v in PARENT_TYPES.items() if v.lower() == lowered), None)
    if named:
        return named
    rt = type_named(db, tenant_id, text)
    if rt is None:
        raise ValueError(f"No record type '{text}'. Choose: {', '.join(o['label'] for o in entity_options(db, tenant_id))}")
    return rt.entity_type


def _check_parent_type(db: Session, tenant_id: int, parent_entity_type: str | None, own_key: str) -> str | None:
    if not parent_entity_type:
        return None
    if parent_entity_type in PARENT_TYPES:
        return parent_entity_type
    parent = get_type(db, tenant_id, parent_entity_type) if is_custom(parent_entity_type) else None
    if parent is None:
        raise ValueError(f"Records can hang off: {', '.join(o['value'] for o in entity_options(db, tenant_id, own_key))}")
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


def _unique_prefix(db: Session, tenant_id: int, prefix: str) -> str:
    taken = {rt.number_prefix for rt in list_types(db, tenant_id)}
    if prefix not in taken:
        return prefix
    n = 2
    while f"{prefix[:8]}{n}" in taken:
        n += 1
    return f"{prefix[:8]}{n}"


def _clean_list_fields(raw: Any) -> str:
    keys = raw if isinstance(raw, list) else str(raw or "").split(",")
    return ",".join(dict.fromkeys(k.strip().lower() for k in keys if str(k).strip()))


def create_type(
    db: Session, tenant_id: int, *, label: str, plural_label: str | None = None, key: str | None = None, description: str = "",
    icon: str | None = None, number_prefix: str | None = None, parent_entity_type: str | None = None, parent_required: bool = False,
    name_label: str | None = None, list_fields: Any = None,
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
        number_prefix=_clean_prefix(number_prefix) if number_prefix else _unique_prefix(db, tenant_id, _default_prefix(label)), next_number=1,
        parent_entity_type=parent, parent_required=bool(parent_required and parent), is_active=True,
        name_label=str(name_label or "").strip()[:100], list_fields=_clean_list_fields(list_fields),
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
    if "name_label" in changes:
        rt.name_label = str(changes["name_label"] or "").strip()[:100]
    if "list_fields" in changes:
        keys = _clean_list_fields(changes["list_fields"])
        known = {d.key for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type)} | {"state", "parent", "owner"}
        unknown = [k for k in keys.split(",") if k and k not in known]
        if unknown:
            raise ValueError(f"Not fields of {rt.label}: {', '.join(unknown)}")
        rt.list_fields = keys
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
    """Only an unused type goes: no records, no type hanging off it, no lookup field or relationship pointing
    at it from elsewhere, no switched-on flow on it. Its fields, state model, pages and rules go with it."""
    from app.models.flows import FlowDefinition
    from app.models.lifecycle import AssignmentRule, StateModel
    from app.models.pages import PageLayout, ValidationRule

    count = record_count(db, rt)
    if count:
        raise ValueError(f"{count} {rt.plural_label.lower()} exist - delete them first, or switch the type off instead")
    children = [t.label for t in list_types(db, tenant_id) if t.parent_entity_type == rt.entity_type]
    if children:
        raise ValueError(f"{', '.join(children)} hang{'s' if len(children) == 1 else ''} off {rt.label} - change that first")
    pointing = [f"{d.label} on {label_of(db, tenant_id, d.entity_type)}" for d in db.query(CustomFieldDefinition).filter(
        CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.data_type == "lookup",
        CustomFieldDefinition.allowed_values == rt.entity_type, CustomFieldDefinition.entity_type != rt.entity_type)]
    if pointing:
        raise ValueError(f"These fields point to {rt.plural_label.lower()}: {', '.join(pointing)} - remove them first")
    flows = [f.name for f in db.query(FlowDefinition).filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.entity_type == rt.entity_type,
                                                             FlowDefinition.is_active.is_(True))]
    if flows:
        raise ValueError(f"Switched-on flows work on {rt.plural_label.lower()}: {', '.join(flows)} - switch them off first")
    for rel in list_relationships(db, tenant_id, rt.entity_type):
        delete_relationship(db, tenant_id, rel, force=True)
    field_ids = [fid for (fid,) in db.query(CustomFieldDefinition.id).filter(
        CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.entity_type == rt.entity_type)]
    if field_ids:
        db.query(CustomFieldValue).filter(CustomFieldValue.field_id.in_(field_ids)).delete(synchronize_session=False)
        db.query(CustomFieldDefinition).filter(CustomFieldDefinition.id.in_(field_ids)).delete(synchronize_session=False)
    for model in db.query(StateModel).filter(StateModel.tenant_id == tenant_id, StateModel.entity_type == rt.entity_type):
        db.delete(model)
    for model in (PageLayout, ValidationRule, AssignmentRule):
        db.query(model).filter(model.tenant_id == tenant_id, model.entity_type == rt.entity_type).delete(synchronize_session=False)
    db.delete(rt)
    db.flush()


def type_out(db: Session, tenant_id: int, rt: CustomRecordType, with_fields: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": rt.id, "key": rt.key, "entity_type": rt.entity_type, "label": rt.label, "plural_label": rt.plural_label,
        "description": rt.description, "icon": rt.icon, "number_prefix": rt.number_prefix,
        "parent_entity_type": rt.parent_entity_type, "parent_label": label_of(db, tenant_id, rt.parent_entity_type),
        "parent_required": rt.parent_required, "is_active": rt.is_active, "record_count": record_count(db, rt),
        "name_label": rt.name_label or "Name", "list_fields": list_columns(db, tenant_id, rt),
    }
    if with_fields:
        out["fields"] = [field_out(db, tenant_id, d) for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type)]
        model = lifecycle_service.resolve_state_model(db, tenant_id, rt.entity_type)
        out["states"] = [{"code": s.code, "label": s.label, "editable": s.editable, "is_terminal": s.is_terminal} for s in model.states.values()]
        out["transitions"] = [{"from_state": t.from_state, "to_state": t.to_state, "action": t.action, "allowed_roles": t.allowed_roles}
                              for t in model.transitions]
        out["state_model_configured"] = model.configured
        out["relationships"] = [relationship_out(db, tenant_id, r, rt.entity_type) for r in list_relationships(db, tenant_id, rt.entity_type)]
    return out


def field_out(db: Session, tenant_id: int, d: CustomFieldDefinition) -> dict[str, Any]:
    target = custom_field_service.lookup_target(d)
    return {"id": d.id, "key": d.key, "label": d.label, "data_type": d.data_type,
            "allowed_values": [] if target else custom_field_service.allowed_values(d),
            "required": d.required, "default_value": d.default_value, "section": d.section, "help_text": d.help_text,
            "target": target, "target_label": label_of(db, tenant_id, target) if target else None}


def list_columns(db: Session, tenant_id: int, rt: CustomRecordType) -> list[str]:
    """The fields the list of records shows: the type's choice, else state and its first few fields."""
    chosen = [k for k in (rt.list_fields or "").split(",") if k]
    if chosen:
        return chosen
    keys = [d.key for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type) if d.data_type != "textarea"]
    return ["state"] + keys[:LIST_FIELDS_DEFAULT]


# ---------------------------------------------------------------- references between records


def title_of(db: Session, tenant_id: int, entity_type: str, obj) -> str:
    if isinstance(obj, CustomRecord):
        return f"{obj.number} · {obj.name}"
    if entity_type in ("party", "opportunity"):
        return obj.name
    if entity_type == "lead":
        party = db.get(lifecycle_service.ENTITY_MODELS["party"], obj.party_id) if getattr(obj, "party_id", None) else None
        return f"Lead #{obj.id}" + (f" · {party.name}" if party else "")
    return f"{PARENT_TYPES.get(entity_type, entity_type)} #{obj.id}"


_record_title = title_of


def ref_out(db: Session, tenant_id: int, entity_type: str, record_id: Any) -> dict[str, Any] | None:
    if record_id in (None, ""):
        return None
    obj = lifecycle_service.get_record(db, tenant_id, entity_type, record_id)
    return {"entity_type": entity_type, "id": int(record_id), "custom_key": _key_of(entity_type) if is_custom(entity_type) else None,
            "title": title_of(db, tenant_id, entity_type, obj) if obj is not None else f"#{record_id} (no longer exists)",
            "type_label": label_of(db, tenant_id, entity_type)}


def resolve_reference(db: Session, tenant_id: int, target: str | None, raw: Any, what: str = "") -> int:
    """The id of the record of `target` type that `raw` names: its id ("12", "#12"), its number ("SS-0004")
    or its exact name. Refused when nothing - or more than one record - fits."""
    if not target:
        raise ValueError(f"{what or 'The field'} doesn't say which record type it points to")
    text = str(raw).strip()
    label = label_of(db, tenant_id, target) or target
    if isinstance(raw, dict) and raw.get("id") is not None:
        text = str(raw["id"])
    if text.lstrip("#").isdigit():
        if lifecycle_service.get_record(db, tenant_id, target, int(text.lstrip("#"))) is None:
            raise ValueError(f"No {label} #{text.lstrip('#')}")
        return int(text.lstrip("#"))
    if is_custom(target):
        rt = get_type(db, tenant_id, target)
        if rt is None:
            raise ValueError(f"No record type '{target}'")
        query = db.query(CustomRecord).filter(CustomRecord.tenant_id == tenant_id, CustomRecord.type_id == rt.id)
        found = query.filter(CustomRecord.number.ilike(text)).all() or query.filter(CustomRecord.name.ilike(text)).all()
    elif target in ("party", "opportunity"):
        model = lifecycle_service.ENTITY_MODELS[target]
        found = db.query(model).filter(model.tenant_id == tenant_id, model.name.ilike(text)).all()
    else:
        found = []
    if len(found) > 1:
        raise ValueError(f"{len(found)} {label} records are called '{text}' - give its number")
    if not found:
        raise ValueError(f"No {label} '{text}'" + (f" for {what}" if what else ""))
    return found[0].id


def find_record(db: Session, tenant_id: int, ref: Any, entity_type: str | None = None) -> tuple[CustomRecordType, CustomRecord]:
    """A custom record by its number ("SS-0004"), or by type + id / name."""
    text = str(ref if not isinstance(ref, dict) else ref.get("id", "")).strip()
    if entity_type and is_custom(entity_type):
        rt = get_type(db, tenant_id, entity_type)
        if rt is None:
            raise ValueError(f"No record type '{entity_type}'")
        rec = get_record(db, tenant_id, rt, resolve_reference(db, tenant_id, rt.entity_type, text, rt.label))
        return rt, rec
    match = _NUMBER.match(text)
    if match:
        prefix = match.group(1).upper()
        for rt in list_types(db, tenant_id):
            if rt.number_prefix == prefix or text.upper().startswith(f"{rt.number_prefix}-"):
                rec = db.query(CustomRecord).filter(CustomRecord.tenant_id == tenant_id, CustomRecord.type_id == rt.id,
                                                    CustomRecord.number.ilike(text)).first()
                if rec is not None:
                    return rt, rec
        # a record numbered under an older prefix
        rec = db.query(CustomRecord).filter(CustomRecord.tenant_id == tenant_id, CustomRecord.number.ilike(text)).first()
        if rec is not None:
            return rec.record_type, rec
    raise ValueError(f"No record '{text}' - give its number (e.g. SS-0004)")


def search(db: Session, access: Access, entity_type: str, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Records of a type the user may see - for pickers (parent, lookups, links)."""
    text = (q or "").strip()
    if is_custom(entity_type):
        rt = get_type(db, access.tenant_id, entity_type)
        if rt is None:
            return []
        query = visible_query(db, access, rt)
        if text:
            query = query.filter(or_(CustomRecord.name.ilike(f"%{text}%"), CustomRecord.number.ilike(f"%{text}%")))
        return [{"id": r.id, "title": title_of(db, access.tenant_id, entity_type, r), "hint": (r.state or "").replace("_", " ")}
                for r in query.order_by(CustomRecord.id.desc()).limit(limit)]
    model = lifecycle_service.ENTITY_MODELS.get(entity_type)
    if model is None:
        return []
    query = access_service.apply(db.query(model).filter(model.tenant_id == access.tenant_id), model, db, access, entity_type)
    if text:
        if text.lstrip("#").isdigit():
            query = query.filter(model.id == int(text.lstrip("#")))
        elif hasattr(model, "name"):
            query = query.filter(model.name.ilike(f"%{text}%"))
    return [{"id": o.id, "title": title_of(db, access.tenant_id, entity_type, o), "hint": PARENT_TYPES.get(entity_type, "")}
            for o in query.order_by(model.id.desc()).limit(limit)]


# ---------------------------------------------------------------- parents


def get_parent(db: Session, tenant_id: int, parent_entity_type: str | None, parent_id: Any):
    if not parent_entity_type or parent_id in (None, ""):
        return None
    return lifecycle_service.get_record(db, tenant_id, parent_entity_type, parent_id)


def parent_ref(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord) -> dict[str, Any] | None:
    if rec.parent_id is None or not rt.parent_entity_type:
        return None
    obj = get_parent(db, tenant_id, rt.parent_entity_type, rec.parent_id)
    return {"entity_type": rt.parent_entity_type, "id": rec.parent_id,
            "title": title_of(db, tenant_id, rt.parent_entity_type, obj) if obj is not None else f"#{rec.parent_id} (no longer exists)",
            "custom_key": _key_of(rt.parent_entity_type) if is_custom(rt.parent_entity_type) else None}


def search_parents(db: Session, access: Access, rt: CustomRecordType, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Records of the parent type the user may see, for the record form's picker."""
    if not rt.parent_entity_type:
        return []
    return [{"id": r["id"], "title": r["title"]} for r in search(db, access, rt.parent_entity_type, q, limit)]


def ancestors(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord) -> dict[str, Any]:
    """The records above this one, nearest first: {"x_site": <CustomRecord>, "party": <Party>} - what a flow
    reads as parent.<type>.<field>."""
    out: dict[str, Any] = {}
    current_type, current = rt, rec
    for _ in range(10):
        if current_type is None or not current_type.parent_entity_type or current.parent_id is None:
            break
        parent = get_parent(db, tenant_id, current_type.parent_entity_type, current.parent_id)
        if parent is None:
            break
        out[current_type.parent_entity_type] = parent
        if not isinstance(parent, CustomRecord):
            break
        current_type, current = parent.record_type, parent
    return out


def _customer_of_builtin(db: Session, entity_type: str, obj):
    """The party behind a built-in record: itself, its party, or up order/quote -> opportunity -> lead -> party."""
    models = lifecycle_service.ENTITY_MODELS
    Party, Lead, Opportunity, Quote = models["party"], models["lead"], models["opportunity"], models["quote"]
    if entity_type == "party":
        return obj
    if getattr(obj, "party_id", None):
        return db.get(Party, obj.party_id)
    if getattr(obj, "quote_id", None):
        obj = db.get(Quote, obj.quote_id)
    if obj is not None and getattr(obj, "opportunity_id", None):
        obj = db.get(Opportunity, obj.opportunity_id)
    if obj is not None and getattr(obj, "lead_id", None):
        lead = db.get(Lead, obj.lead_id)
        return db.get(Party, lead.party_id) if lead is not None else None
    return None


def customer_of(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord):
    """The customer a record belongs to: up its parents, else a lookup field pointing to a customer."""
    for entity_type, obj in ancestors(db, tenant_id, rt, rec).items():
        if not is_custom(entity_type):
            return _customer_of_builtin(db, entity_type, obj)
    for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type):
        if custom_field_service.lookup_target(d) == "party":
            party_id = custom_field_service.get_values(db, tenant_id, rt.entity_type, rec.id).get(d.key)
            if party_id:
                return lifecycle_service.get_record(db, tenant_id, "party", party_id)
    return None


# ---------------------------------------------------------------- visibility


def visible_ids(db: Session, access: Access, rt: CustomRecordType) -> set[int] | None:
    """Ids of this type's records the user may see; None = all. What they (or the users, positions and
    departments in their view) created, what is assigned to them, and what hangs off a parent they see."""
    if access.unrestricted:
        return None
    v = access.visibility
    conditions = [CustomRecord.sys_created_by == access.user.id]
    if v is not None:
        if v.user_ids:
            conditions.append(CustomRecord.sys_created_by.in_(v.user_ids))
        if v.position_ids:
            conditions.append(CustomRecord.sys_created_position_id.in_(v.position_ids))
        if v.department_ids:
            conditions.append(CustomRecord.sys_department_id.in_(v.department_ids))
    if rt.parent_entity_type:
        parents = access_service.visible_ids(db, access, rt.parent_entity_type)
        if parents:
            conditions.append(CustomRecord.parent_id.in_(parents))
    rows = db.query(CustomRecord.id).filter(CustomRecord.tenant_id == access.tenant_id, CustomRecord.type_id == rt.id, or_(*conditions))
    ids = {rid for (rid,) in rows.all()}
    if v is not None:
        # assigned to them, their position or their department (INT-154 / assignment rules)
        ids |= access_service._meta_visible_ids(db, access, rt.entity_type)
    return ids


def _visible_for_access(db: Session, access: Access, entity_type: str) -> set[int]:
    rt = get_type(db, access.tenant_id, entity_type)
    return (visible_ids(db, access, rt) or set()) if rt is not None else set()


# access_service.visible_ids / can_see on an "x_" type come here
access_service.VISIBILITY_PROVIDERS[ENTITY_PREFIX] = _visible_for_access


def visible_query(db: Session, access: Access, rt: CustomRecordType):
    query = db.query(CustomRecord).filter(CustomRecord.tenant_id == access.tenant_id, CustomRecord.type_id == rt.id)
    ids = visible_ids(db, access, rt)
    return query if ids is None else query.filter(CustomRecord.id.in_(ids or {-1}))


def can_see(db: Session, access: Access, rt: CustomRecordType, rec: CustomRecord) -> bool:
    return access_service.can_see(db, access, rt.entity_type, rec.id)


# ---------------------------------------------------------------- records


def get_record(db: Session, tenant_id: int, rt: CustomRecordType | None, record_id: Any) -> CustomRecord | None:
    if rt is None:
        return None
    try:
        rec = db.get(CustomRecord, int(record_id))
    except (TypeError, ValueError):
        return None
    if rec is None or rec.tenant_id != tenant_id or rec.type_id != rt.id:
        return None
    return rec


def parse_fields(raw: Any) -> dict[str, Any]:
    """'a=1; b = two, c: 3' (or a dict) -> {"a": "1", "b": "two", "c": "3"} - field values typed in one line."""
    if isinstance(raw, dict):
        return dict(raw)
    out: dict[str, Any] = {}
    for part in re.split(r"[;\n]|,(?=\s*[\w ]{1,40}\s*[=:])", str(raw or "")):
        if not part.strip():
            continue
        m = re.match(r"\s*([\w .'-]{1,60}?)\s*[=:]\s*(.*)$", part)
        if m:
            out[m.group(1).strip()] = m.group(2).strip().strip("'\"")
    return out


def split_changes(fields: dict[str, Any]) -> dict[str, Any]:
    """Loose "name / parent_id / field: value" updates (INT-29, a flow's Set Field) -> {name, parent_id, fields}."""
    out: dict[str, Any] = {}
    rest: dict[str, Any] = {}
    for raw_key, value in (fields or {}).items():
        key = str(raw_key).strip().lower().replace(" ", "_")
        if key in ("name", "parent_id"):
            out[key] = value
        elif key == "fields" and isinstance(value, dict):
            rest.update(value)
        elif key not in ("state", "number", "id"):
            rest[key] = value
    if rest:
        out["fields"] = rest
    return out


def _set_parent(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord, parent_ref_value: Any) -> None:
    if parent_ref_value in (None, ""):
        if rt.parent_required:
            raise ValueError(f"A {rt.label} needs a {label_of(db, tenant_id, rt.parent_entity_type)}")
        rec.parent_id = None
        return
    if not rt.parent_entity_type:
        raise ValueError(f"{rt.plural_label} don't hang off another record")
    parent_id = resolve_reference(db, tenant_id, rt.parent_entity_type, parent_ref_value, "the parent")
    parent = get_parent(db, tenant_id, rt.parent_entity_type, parent_id)
    if parent is None:
        raise ValueError(f"No {label_of(db, tenant_id, rt.parent_entity_type)} #{parent_ref_value}")
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


def _clean_fields(db: Session, tenant_id: int, rt: CustomRecordType, fields: dict[str, Any]) -> dict[str, Any]:
    """Field values by key, lookups turned into record ids; unknown fields refused."""
    defs = {d.key: d for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type)}
    by_label = {d.label.lower(): d.key for d in defs.values()}
    out: dict[str, Any] = {}
    unknown: list[str] = []
    for raw_key, value in (fields or {}).items():
        key = str(raw_key).strip().lower().replace(" ", "_")
        key = key if key in defs else by_label.get(str(raw_key).strip().lower(), key)
        defn = defs.get(key)
        if defn is None:
            unknown.append(key)
            continue
        if defn.data_type == "lookup" and value not in (None, ""):
            value = resolve_reference(db, tenant_id, custom_field_service.lookup_target(defn), value, defn.label)
        out[key] = value
    if unknown:
        raise ValueError(f"Unknown field(s) for {rt.label}: {', '.join(unknown)}. Fields: {', '.join(defs) or 'none yet'}")
    return out


def current_values(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord) -> dict[str, Any]:
    return {"name": rec.name, "parent_id": rec.parent_id, "state": rec.state,
            **custom_field_service.get_values(db, tenant_id, rt.entity_type, rec.id)}


def check(db: Session, tenant_id: int, rt: CustomRecordType, values: dict[str, Any], execution_id: int | None = None,
          page_key: str | None = None) -> None:
    """The record's page and validation rules on these values (the whole record as it would be saved) -
    mandatory fields, types, simple rules and validation flows. Refused with every problem at once."""
    from app.crm_core import page_service

    errors = page_service.validate(db, tenant_id, rt.entity_type, None, values, key=page_key, execution_id=execution_id)
    defs = custom_field_service.list_definitions(db, tenant_id, rt.entity_type)
    labels = {d.key: d.label for d in defs}
    errors += [f"{labels[d.key]} is mandatory" for d in defs if d.required and values.get(d.key) in (None, "")
               and f"{labels[d.key]} is mandatory" not in errors]
    if errors:
        raise ValueError("; ".join(dict.fromkeys(errors)))


def create_record(db: Session, tenant_id: int, rt: CustomRecordType, name: str, parent_id: Any = None,
                  fields: dict[str, Any] | None = None, execution_id: int | None = None, page_key: str | None = None) -> CustomRecord:
    if not rt.is_active:
        raise ValueError(f"{rt.plural_label} are switched off")
    name = str(name or "").strip()
    if not name:
        raise ValueError(f"A {rt.label} needs a {(rt.name_label or 'name').lower()}")
    cleaned = _clean_fields(db, tenant_id, rt, fields or {})
    rec = CustomRecord(tenant_id=tenant_id, type_id=rt.id, number="", name=name[:200], state=lifecycle_service.initial_state(db, tenant_id, rt.entity_type))
    rec.record_type = rt
    _set_parent(db, tenant_id, rt, rec, parent_id)
    defaults = {d.key: custom_field_service.typed(d, d.default_value) for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type)
                if d.default_value not in (None, "")}
    check(db, tenant_id, rt, {**defaults, **cleaned, "name": rec.name, "parent_id": rec.parent_id, "state": rec.state}, execution_id, page_key)
    rec.number = f"{rt.number_prefix}-{rt.next_number:04d}"
    rt.next_number += 1
    db.add(rec)
    db.flush()
    for key, value in cleaned.items():
        defn = custom_field_service.definition_by_key(db, tenant_id, rt.entity_type, key)
        custom_field_service.set_value(db, tenant_id, rt.entity_type, rec.id, defn, value)
    db.flush()
    return rec


def apply_changes(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord, changes: dict[str, Any],
                  enforce_editable: bool = True, execution_id: int | None = None, page_key: str | None = None) -> dict[str, Any]:
    """Changes a record's name, parent and fields - while its state allows editing - checked as a whole
    against its page and rules. Returns what was applied."""
    if enforce_editable:
        editable, state = lifecycle_service.is_editable(db, tenant_id, rt.entity_type, rec)
        if not editable:
            raise ValueError(f"{rec.number} is {state.replace('_', ' ')} and can no longer be changed - move it to another state first")
    applied: dict[str, Any] = {}
    if "name" in changes:
        name = str(changes["name"] or "").strip()
        if not name:
            raise ValueError(f"A {rt.label} needs a {(rt.name_label or 'name').lower()}")
        rec.name = name[:200]
        applied["name"] = rec.name
    if "parent_id" in changes:
        _set_parent(db, tenant_id, rt, rec, changes["parent_id"])
        applied["parent_id"] = rec.parent_id
    cleaned = _clean_fields(db, tenant_id, rt, changes.get("fields") or {})
    if not applied and not cleaned:
        raise ValueError("Nothing to change")
    check(db, tenant_id, rt, {**current_values(db, tenant_id, rt, rec), **cleaned, "name": rec.name, "parent_id": rec.parent_id},
          execution_id, page_key)
    for key, value in cleaned.items():
        defn = custom_field_service.definition_by_key(db, tenant_id, rt.entity_type, key)
        applied[key] = custom_field_service.set_value(db, tenant_id, rt.entity_type, rec.id, defn, value)
    db.flush()
    return applied


def check_state(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord, to_state: str, execution_id: int | None = None) -> None:
    """Rules that depend on the state hold before a record enters it ("a completed survey needs its report")."""
    check(db, tenant_id, rt, {**current_values(db, tenant_id, rt, rec), "state": to_state}, execution_id)


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


def _pointing_at(db: Session, tenant_id: int, entity_type: str, record_id: int) -> list[tuple[CustomFieldDefinition, list[int]]]:
    """Lookup fields (on any record type) holding this record: [(field, [ids of the records holding it])]."""
    out = []
    for d in db.query(CustomFieldDefinition).filter(CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.data_type == "lookup",
                                                    CustomFieldDefinition.allowed_values == entity_type, CustomFieldDefinition.is_active.is_(True)):
        ids = [eid for (eid,) in db.query(CustomFieldValue.entity_id).filter(CustomFieldValue.field_id == d.id,
                                                                              CustomFieldValue.value == str(record_id))]
        if ids:
            out.append((d, ids))
    return out


def delete_record(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord) -> dict[str, Any]:
    children = children_of(db, tenant_id, rt, rec)
    if children:
        what = ", ".join(f"{c['count']} {c['label'].lower()}" for c in children)
        raise ValueError(f"{what} hang off {rec.number} - delete or move them first")
    pointing = _pointing_at(db, tenant_id, rt.entity_type, rec.id)
    if pointing:
        what = ", ".join(f"{len(ids)} {label_of(db, tenant_id, d.entity_type)} record(s) ({d.label})" for d, ids in pointing)
        raise ValueError(f"{what} point to {rec.number} - change them first")
    snapshot = {"number": rec.number, "name": rec.name, "parent_id": rec.parent_id, "state": rec.state,
                **custom_field_service.get_values(db, tenant_id, rt.entity_type, rec.id)}
    rel_ids = [r.id for r in list_relationships(db, tenant_id, rt.entity_type)]
    for r in list_relationships(db, tenant_id, rt.entity_type):
        side = RecordLink.from_id if r.from_entity_type == rt.entity_type else RecordLink.to_id
        db.query(RecordLink).filter(RecordLink.relationship_id == r.id, side == rec.id).delete(synchronize_session=False)
        if r.from_entity_type == r.to_entity_type == rt.entity_type:
            db.query(RecordLink).filter(RecordLink.relationship_id == r.id, RecordLink.to_id == rec.id).delete(synchronize_session=False)
    db.query(CustomFieldValue).filter(CustomFieldValue.tenant_id == tenant_id, CustomFieldValue.entity_type == rt.entity_type,
                                      CustomFieldValue.entity_id == rec.id).delete(synchronize_session=False)
    db.delete(rec)
    db.flush()
    return {**snapshot, "links_removed_from": rel_ids}


# ---------------------------------------------------------------- relationships (many-to-many)


def list_relationships(db: Session, tenant_id: int, entity_type: str | None = None) -> list[RecordRelationship]:
    query = db.query(RecordRelationship).filter(RecordRelationship.tenant_id == tenant_id)
    if entity_type:
        query = query.filter(or_(RecordRelationship.from_entity_type == entity_type, RecordRelationship.to_entity_type == entity_type))
    return query.order_by(RecordRelationship.id).all()


def get_relationship(db: Session, tenant_id: int, ref: Any, entity_type: str | None = None) -> RecordRelationship:
    """A relationship by key, id or either of its labels (seen from entity_type)."""
    text = str(ref or "").strip()
    rels = list_relationships(db, tenant_id, entity_type)
    lowered = text.lower()
    found = next((r for r in rels if r.key == slug(text) or str(r.id) == text or lowered in (r.label.lower(), r.reverse_label.lower())), None)
    if found is None and not text and len(rels) == 1:
        found = rels[0]
    if found is None:
        names = ", ".join(f"{r.label} ({r.key})" for r in rels) or "none yet - define one in Admin -> Record Types"
        raise ValueError(f"No relationship '{text}'" + (f" on {label_of(db, tenant_id, entity_type)}" if entity_type else "") + f". Relationships: {names}")
    return found


def create_relationship(db: Session, tenant_id: int, *, from_entity_type: str, to_entity_type: str, label: str,
                        reverse_label: str | None = None, key: str | None = None, description: str = "") -> RecordRelationship:
    source = check_target(db, tenant_id, from_entity_type)
    target = check_target(db, tenant_id, to_entity_type)
    if not is_custom(source) and not is_custom(target):
        raise ValueError("A relationship needs at least one of your own record types (built-in records are already related)")
    label = str(label or "").strip() or f"{label_of(db, tenant_id, target)}s"
    reverse = str(reverse_label or "").strip() or f"{label_of(db, tenant_id, source)}s"
    key = re.sub(r"[^a-z0-9]+", "_", str(key or f"{_key_of(source)}_{label}").lower()).strip("_")[:60]
    if not key or not _KEY.fullmatch(key):
        raise ValueError("The relationship key starts with a letter")
    if db.query(RecordRelationship.id).filter(RecordRelationship.tenant_id == tenant_id, RecordRelationship.key == key).first():
        raise ValueError(f"A relationship '{key}' already exists")
    rel = RecordRelationship(tenant_id=tenant_id, key=key, from_entity_type=source, to_entity_type=target, label=label[:100],
                             reverse_label=reverse[:100], description=str(description or "").strip())
    db.add(rel)
    db.flush()
    return rel


def update_relationship(db: Session, tenant_id: int, rel: RecordRelationship, changes: dict[str, Any]) -> RecordRelationship:
    for attr in ("label", "reverse_label", "description"):
        if attr in changes and (attr == "description" or str(changes[attr] or "").strip()):
            setattr(rel, attr, str(changes[attr] or "").strip()[:100] if attr != "description" else str(changes[attr] or "").strip())
    db.flush()
    return rel


def delete_relationship(db: Session, tenant_id: int, rel: RecordRelationship, force: bool = False) -> int:
    links = db.query(RecordLink).filter(RecordLink.relationship_id == rel.id)
    count = links.count()
    if count and not force:
        raise ValueError(f"{count} records are linked by '{rel.label}' - unlink them first, or delete it with its links")
    links.delete(synchronize_session=False)
    db.delete(rel)
    db.flush()
    return count


def relationship_out(db: Session, tenant_id: int, rel: RecordRelationship, seen_from: str | None = None) -> dict[str, Any]:
    out = {"id": rel.id, "key": rel.key, "from_entity_type": rel.from_entity_type, "to_entity_type": rel.to_entity_type,
           "from_label": label_of(db, tenant_id, rel.from_entity_type), "to_label": label_of(db, tenant_id, rel.to_entity_type),
           "label": rel.label, "reverse_label": rel.reverse_label, "description": rel.description,
           "links": db.query(RecordLink.id).filter(RecordLink.relationship_id == rel.id).count()}
    if seen_from:
        forward = rel.from_entity_type == seen_from
        out.update(other_entity_type=rel.to_entity_type if forward else rel.from_entity_type,
                   other_label=out["to_label"] if forward else out["from_label"], name=rel.label if forward else rel.reverse_label)
    return out


def _sides(rel: RecordRelationship, entity_type: str) -> tuple[str, bool]:
    """(the other side's type, True when entity_type is the 'from' side)."""
    if rel.from_entity_type == entity_type:
        return rel.to_entity_type, True
    if rel.to_entity_type == entity_type:
        return rel.from_entity_type, False
    raise ValueError(f"'{rel.label}' doesn't relate {entity_type} records")


def link(db: Session, tenant_id: int, rel: RecordRelationship, entity_type: str, record_id: int, other_ref: Any,
         note: str = "") -> tuple[RecordLink, bool, str, int]:
    """Links a record to a record of the other side (by id, number or name). Returns (link, created, other type, other id)."""
    other_type, forward = _sides(rel, entity_type)
    if lifecycle_service.get_record(db, tenant_id, entity_type, record_id) is None:
        raise ValueError(f"No {label_of(db, tenant_id, entity_type)} #{record_id}")
    other_id = resolve_reference(db, tenant_id, other_type, other_ref, rel.label if forward else rel.reverse_label)
    if other_type == entity_type and other_id == record_id:
        raise ValueError("A record can't be linked to itself")
    from_id, to_id = (record_id, other_id) if forward else (other_id, record_id)
    existing = db.query(RecordLink).filter(RecordLink.relationship_id == rel.id, RecordLink.from_id == from_id, RecordLink.to_id == to_id).first()
    if existing is not None:
        return existing, False, other_type, other_id
    row = RecordLink(tenant_id=tenant_id, relationship_id=rel.id, from_id=from_id, to_id=to_id, note=str(note or "")[:300])
    db.add(row)
    db.flush()
    return row, True, other_type, other_id


def unlink(db: Session, tenant_id: int, rel: RecordRelationship, entity_type: str, record_id: int, other_ref: Any) -> tuple[str, int]:
    other_type, forward = _sides(rel, entity_type)
    other_id = resolve_reference(db, tenant_id, other_type, other_ref, rel.label if forward else rel.reverse_label)
    from_id, to_id = (record_id, other_id) if forward else (other_id, record_id)
    deleted = db.query(RecordLink).filter(RecordLink.relationship_id == rel.id, RecordLink.from_id == from_id,
                                          RecordLink.to_id == to_id).delete(synchronize_session=False)
    if not deleted:
        raise ValueError(f"Those records aren't linked by '{rel.label}'")
    db.flush()
    return other_type, other_id


def linked_ids(db: Session, rel: RecordRelationship, entity_type: str, record_id: int) -> list[int]:
    _, forward = _sides(rel, entity_type)
    if forward:
        return [i for (i,) in db.query(RecordLink.to_id).filter(RecordLink.relationship_id == rel.id, RecordLink.from_id == record_id)]
    return [i for (i,) in db.query(RecordLink.from_id).filter(RecordLink.relationship_id == rel.id, RecordLink.to_id == record_id)]


# ---------------------------------------------------------------- what a record is related to


def _items(db: Session, access: Access, entity_type: str, ids: list[int], limit: int = 25) -> list[dict[str, Any]]:
    out = []
    for record_id in ids:
        obj = lifecycle_service.get_record(db, access.tenant_id, entity_type, record_id)
        if obj is None or not access_service.can_see(db, access, entity_type, record_id):
            continue
        out.append({"id": record_id, "title": title_of(db, access.tenant_id, entity_type, obj),
                    "state": lifecycle_service.effective_state(db, access.tenant_id, entity_type, obj),
                    "custom_key": _key_of(entity_type) if is_custom(entity_type) else None})
        if len(out) >= limit:
            break
    return out


def related(db: Session, access: Access, entity_type: str, record_id: int) -> list[dict[str, Any]]:
    """Every record linked to this one (custom or built-in), by how: records hanging off it, records whose
    lookup field points to it, and many-to-many links - each section only with what the user may see."""
    tenant_id = access.tenant_id
    sections: list[dict[str, Any]] = []
    for child_type in list_types(db, tenant_id, active_only=True):
        if child_type.parent_entity_type != entity_type:
            continue
        ids = [i for (i,) in db.query(CustomRecord.id).filter(CustomRecord.type_id == child_type.id, CustomRecord.parent_id == record_id)
               .order_by(CustomRecord.id.desc())]
        sections.append({"kind": "children", "key": f"children:{child_type.key}", "label": child_type.plural_label,
                         "entity_type": child_type.entity_type, "type_label": child_type.label, "can_add": True,
                         "items": _items(db, access, child_type.entity_type, ids)})
    for d, ids in _pointing_at(db, tenant_id, entity_type, record_id):
        if is_custom(d.entity_type) and get_type(db, tenant_id, d.entity_type, active_only=True) is None:
            continue
        sections.append({"kind": "lookup", "key": f"lookup:{d.entity_type}:{d.key}", "label": f"{label_of(db, tenant_id, d.entity_type)} ({d.label})",
                         "entity_type": d.entity_type, "type_label": label_of(db, tenant_id, d.entity_type), "field": d.key,
                         "items": _items(db, access, d.entity_type, sorted(ids, reverse=True))})
    for rel in list_relationships(db, tenant_id, entity_type):
        directions = [True, False] if rel.from_entity_type == rel.to_entity_type else [rel.from_entity_type == entity_type]
        for forward in directions:
            other = rel.to_entity_type if forward else rel.from_entity_type
            if forward:
                ids = [i for (i,) in db.query(RecordLink.to_id).filter(RecordLink.relationship_id == rel.id, RecordLink.from_id == record_id)]
            else:
                ids = [i for (i,) in db.query(RecordLink.from_id).filter(RecordLink.relationship_id == rel.id, RecordLink.to_id == record_id)]
            sections.append({"kind": "link", "key": f"link:{rel.key}:{'f' if forward else 'r'}", "label": rel.label if forward else rel.reverse_label,
                             "entity_type": other, "type_label": label_of(db, tenant_id, other), "relationship": rel.key,
                             "items": _items(db, access, other, ids)})
    return sections


# ---------------------------------------------------------------- output


def record_out(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord, detail: bool = False,
               access: Access | None = None) -> dict[str, Any]:
    values = custom_field_service.get_values(db, tenant_id, rt.entity_type, rec.id)
    display: dict[str, Any] = {}
    for d in custom_field_service.list_definitions(db, tenant_id, rt.entity_type):
        target = custom_field_service.lookup_target(d)
        if target and values.get(d.key):
            display[d.key] = ref_out(db, tenant_id, target, values[d.key])
    model = lifecycle_service.resolve_state_model(db, tenant_id, rt.entity_type)
    state = lifecycle_service.effective_state(db, tenant_id, rt.entity_type, rec)
    meta = lifecycle_service.get_meta(db, tenant_id, rt.entity_type, rec.id, create=False)
    owner = None
    if meta is not None and (meta.owner_user_id or meta.owner_position_id or meta.owner_department_id):
        from app.models.lifecycle import Position
        from app.models.org import Department

        user = db.get(User, meta.owner_user_id) if meta.owner_user_id else None
        position = db.get(Position, meta.owner_position_id) if meta.owner_position_id else None
        department = db.get(Department, meta.owner_department_id) if meta.owner_department_id else None
        owner = " · ".join(x for x in [user.name or user.email if user else None, position.name if position else None,
                                        department.name if department else None] if x)
    out: dict[str, Any] = {
        "id": rec.id, "number": rec.number, "name": rec.name, "parent": parent_ref(db, tenant_id, rt, rec),
        "values": values, "display": display, "state": state, "state_label": lifecycle_service.state_def(model, state).label,
        "editable": lifecycle_service.state_def(model, state).editable, "owner": owner,
        "created_at": rec.created_at, "updated_at": rec.updated_at,
    }
    if detail:
        from app.models.lifecycle import EntityStateHistory

        creator = db.get(User, rec.sys_created_by) if rec.sys_created_by else None
        history = db.query(EntityStateHistory).filter(EntityStateHistory.tenant_id == tenant_id, EntityStateHistory.entity_type == rt.entity_type,
                                                      EntityStateHistory.entity_id == rec.id).order_by(EntityStateHistory.id.desc()).limit(20)
        out.update(brt_id=rec.brt_id, created_by=(creator.name or creator.email) if creator else None,
                   children=children_of(db, tenant_id, rt, rec),
                   history=[{"from_state": h.from_state, "to_state": h.to_state, "source": h.source, "at": h.created_at} for h in history])
        if access is not None:
            out["related"] = related(db, access, rt.entity_type, rec.id)
            out["transitions"] = lifecycle_service.available_transitions(db, tenant_id, rt.entity_type, rec, access.user.role.value, access.user.id)
    return out


def change_ref(rt: CustomRecordType, rec_id: int, number: str, change: str) -> dict[str, Any]:
    """How an intent's result names the record it changed - the audit trail reads it ("Created Site Survey SS-0004")."""
    return {"entity_type": rt.entity_type, "entity_id": rec_id, "change": change, "label": f"{rt.label} {number}"}
