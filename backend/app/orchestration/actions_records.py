"""The tenant's own record types' intents INT-151..158 (intent_engine/record_intents.py) - the steps run inside
the intent pipeline's saga, so before / after flows, the lifecycle hook (state history, assignment rules,
state-enter flows) and the audit trail apply to a custom record exactly as to a built-in one. The work itself
is crm_core/custom_record_service.py (records) and crm_core/record_designer.py (INT-158)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.crm_core import access_service, custom_field_service, lifecycle_service
from app.crm_core import custom_record_service as records
from app.data_intelligence import event_bus
from app.models.auth import User
from app.models.custom_fields import CustomFieldValue
from app.models.custom_records import CustomRecord, CustomRecordType
from app.orchestration.orchestrator import SagaStep


def _access(db: Session, entities: dict[str, Any]):
    """The person running the intent, as their working position sees the data (None = a flow / the system)."""
    user = db.get(User, entities.get("_actor_user_id")) if entities.get("_actor_user_id") else None
    return access_service.resolve(db, user) if user is not None else None


def _visible(db: Session, entities: dict[str, Any], entity_type: str, record_id: int) -> bool:
    access = _access(db, entities)
    return access is None or access_service.can_see(db, access, entity_type, record_id)


def _target(db: Session, tenant_id: int, entities: dict[str, Any]) -> tuple[CustomRecordType, CustomRecord]:
    """The record an intent acts on: entity_type + entity_id (its page), or its number (record_ref)."""
    entity_type = lifecycle_service.normalize_entity_type(entities.get("entity_type"))
    if entity_type and entities.get("entity_id") not in (None, ""):
        if not records.is_custom(entity_type):
            raise ValueError("These actions are for your own record types - use the record's own actions for a built-in record")
        rt = records.get_type(db, tenant_id, entity_type)
        rec = records.get_record(db, tenant_id, rt, entities["entity_id"])
        if rec is None:
            raise ValueError(f"No {records.label_of(db, tenant_id, entity_type)} #{entities['entity_id']}")
    elif entities.get("record_ref"):
        rt, rec = records.find_record(db, tenant_id, entities["record_ref"],
                                      records.type_named(db, tenant_id, entities["record_type"]).entity_type
                                      if entities.get("record_type") and records.type_named(db, tenant_id, entities["record_type"]) else None)
    else:
        raise ValueError("Say which record - its number (e.g. SS-0004)")
    if not rt.is_active:
        raise ValueError(f"{rt.plural_label} are switched off")
    if not _visible(db, entities, rt.entity_type, rec.id):
        raise ValueError(f"{rec.number} is outside the data your position can see")
    return rt, rec


def _type(db: Session, tenant_id: int, entities: dict[str, Any]) -> CustomRecordType:
    raw = entities.get("record_type") or entities.get("entity_type")
    rt = records.type_named(db, tenant_id, str(raw or ""))
    if rt is None:
        names = ", ".join(t.label for t in records.list_types(db, tenant_id, active_only=True)) or "none yet - create one in Admin -> Record Types"
        raise ValueError(f"No record type '{raw or ''}'. Your record types: {names}")
    if not rt.is_active:
        raise ValueError(f"{rt.plural_label} are switched off")
    return rt


def _fields(entities: dict[str, Any]) -> dict[str, Any]:
    out = records.parse_fields(entities.get("record_fields")) if entities.get("record_fields") not in (None, "") else {}
    if isinstance(entities.get("fields"), dict):
        out.update(entities["fields"])
    return out


def _out(rt: CustomRecordType, rec_id: int, number: str, change: str, **extra: Any) -> dict[str, Any]:
    return {"entity_type": rt.entity_type, "entity_id": rec_id, "number": number, "record_type": rt.label,
            "custom_records": [records.change_ref(rt, rec_id, number, change)], **extra}


def build_int151_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-151 - Create a record of one of the tenant's types."""
    state: dict[str, Any] = {}

    def check() -> dict[str, Any]:
        rt = _type(db, tenant_id, entities)
        parent = entities.get("parent_ref") if entities.get("parent_ref") not in (None, "") else entities.get("parent_id")
        if parent not in (None, "") and rt.parent_entity_type:
            parent_id = records.resolve_reference(db, tenant_id, rt.parent_entity_type, parent, "the parent")
            if not _visible(db, entities, rt.parent_entity_type, parent_id):
                raise ValueError(f"{records.label_of(db, tenant_id, rt.parent_entity_type)} #{parent_id} is outside the data your position can see")
            parent = parent_id
        state.update(rt=rt, parent=parent)
        return {"record_type": rt.label, "entity_type": rt.entity_type, "parent_id": parent if isinstance(parent, int) else None}

    def create() -> dict[str, Any]:
        rt = state["rt"]
        rec = records.create_record(db, tenant_id, rt, entities.get("record_name") or entities.get("name") or "", state["parent"],
                                    _fields(entities), execution_id, entities.get("page_key"))
        event_bus.publish(db, execution_id, "custom_record.created", {"record_type": rt.key, "id": rec.id, "number": rec.number})
        return _out(rt, rec.id, rec.number, "created", name=rec.name, state=rec.state,
                    values=custom_field_service.get_values(db, tenant_id, rt.entity_type, rec.id))

    return [SagaStep("check_record_type", check), SagaStep("create_record", create)]


def build_int152_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-152 - Update a record: name, parent, fields - while its state allows editing."""

    def update() -> dict[str, Any]:
        rt, rec = _target(db, tenant_id, entities)
        changes: dict[str, Any] = {}
        if entities.get("record_name") not in (None, ""):
            changes["name"] = entities["record_name"]
        if "parent_ref" in entities or "parent_id" in entities:
            parent = entities.get("parent_ref", entities.get("parent_id"))
            if parent not in (None, "") and rt.parent_entity_type:
                parent = records.resolve_reference(db, tenant_id, rt.parent_entity_type, parent, "the parent")
                if not _visible(db, entities, rt.parent_entity_type, parent):
                    raise ValueError(f"{records.label_of(db, tenant_id, rt.parent_entity_type)} #{parent} is outside the data your position can see")
            changes["parent_id"] = parent
        loose = records.split_changes(_fields(entities))
        changes.update({k: v for k, v in loose.items() if k != "fields"})
        if loose.get("fields"):
            changes["fields"] = loose["fields"]
        applied = records.apply_changes(db, tenant_id, rt, rec, changes, execution_id=execution_id, page_key=entities.get("page_key"))
        event_bus.publish(db, execution_id, "custom_record.updated", {"record_type": rt.key, "id": rec.id, "number": rec.number, "fields": list(applied)})
        return _out(rt, rec.id, rec.number, "updated", updated_fields=applied)

    return [SagaStep("update_record", update)]


def _transition(db: Session, tenant_id: int, rt: CustomRecordType, rec: CustomRecord, wanted: str, role: str | None, user_id: int | None):
    """A move by its target state (code or label) or its action's name."""
    model = lifecycle_service.resolve_state_model(db, tenant_id, rt.entity_type)
    text = str(wanted or "").strip()
    code = records.slug(text)
    by_label = next((s.code for s in model.states.values() if s.label.lower() == text.lower()), None)
    to_state = code if code in model.states else by_label
    if to_state:
        found, error = lifecycle_service.find_transition(db, tenant_id, rt.entity_type, rec, None, to_state, role, user_id)
    else:
        found, error = lifecycle_service.find_transition(db, tenant_id, rt.entity_type, rec, code, None, role, user_id)
    if found is None:
        moves = lifecycle_service.available_transitions(db, tenant_id, rt.entity_type, rec, role, user_id)
        current = lifecycle_service.state_def(model, lifecycle_service.effective_state(db, tenant_id, rt.entity_type, rec)).label
        options = ", ".join(f"{m['to_label']} ({m['action'].replace('_', ' ')})" for m in moves) or "none"
        raise ValueError(f"{rec.number} is {current} - it can't go to '{text}'. It can go to: {options}" if "role" not in error else error)
    return found


def build_int153_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-153 - Move a record to a state of its state model. Rules for the new state hold first; a record
    approval configured for that state is asked for instead of moving (the approval moves it)."""
    state: dict[str, Any] = {}

    def check() -> dict[str, Any]:
        rt, rec = _target(db, tenant_id, entities)
        wanted = entities.get("record_state") or entities.get("to_state") or entities.get("action")
        found = _transition(db, tenant_id, rt, rec, wanted, entities.get("_actor_role"), entities.get("_actor_user_id"))
        records.check_state(db, tenant_id, rt, rec, found.to_state, execution_id)
        state.update(rt=rt, rec=rec, transition=found)
        return {"entity_type": rt.entity_type, "entity_id": rec.id, "action": found.action, "to_state": found.to_state}

    def move() -> dict[str, Any]:
        from app.orchestration import actions_sales

        rt, rec, found = state["rt"], state["rec"], state["transition"]
        request = actions_sales.gate(db, tenant_id, rt.entity_type, rec.id, found.to_state,
                                     f"Move {rt.label} {rec.number} to {found.to_state.replace('_', ' ')}"
                                     + (f": {entities['reasoning']}" if entities.get("reasoning") else ""), actions_sales.actor(entities))
        if request is not None:
            return {**_out(rt, rec.id, rec.number, "approval requested"), "to_state": found.to_state,
                    "from_state": lifecycle_service.effective_state(db, tenant_id, rt.entity_type, rec), **actions_sales.waiting(request)}
        outcome = lifecycle_service.apply_transition(db, tenant_id, rt.entity_type, rec, found, "transition")
        event_bus.publish(db, execution_id, f"{rt.entity_type}.state_changed", {**outcome, "reasoning": entities.get("reasoning") or ""})
        return {**_out(rt, rec.id, rec.number, f"{outcome['from_state']} → {outcome['to_state']}"),
                "from_state": outcome["from_state"], "to_state": outcome["to_state"], "action": outcome["action"]}

    return [SagaStep("validate_transition", check), SagaStep("apply_transition", move)]


def build_int154_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-154 - Assign a record to a person, a position or a department (INT-32's steps on it)."""
    from app.orchestration import actions_lifecycle

    def assign() -> dict[str, Any]:
        rt, rec = _target(db, tenant_id, entities)
        out: dict[str, Any] = {}
        inner = {**entities, "entity_type": rt.entity_type, "entity_id": rec.id, "include_related": False}
        for step in actions_lifecycle.build_int32_steps(db, tenant_id, execution_id, inner):
            out.update(step.action() or {})
        return {**out, **_out(rt, rec.id, rec.number, "assigned")}

    return [SagaStep("assign_record", assign)]


def build_int155_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-155 - Link two records through a relationship the admin defined, or unlink them."""

    def link() -> dict[str, Any]:
        rt, rec = _target(db, tenant_id, entities)
        rel = records.get_relationship(db, tenant_id, entities.get("relationship") or "", rt.entity_type)
        unlink = str(entities.get("unlink") or "").strip().lower() in ("true", "yes", "1", "on")
        other_type, _ = records._sides(rel, rt.entity_type)
        other_id = records.resolve_reference(db, tenant_id, other_type, entities.get("related_ref"), rel.label)
        if not _visible(db, entities, other_type, other_id):
            raise ValueError(f"{records.label_of(db, tenant_id, other_type)} #{other_id} is outside the data your position can see")
        if unlink:
            records.unlink(db, tenant_id, rel, rt.entity_type, rec.id, other_id)
            created = False
        else:
            _, created, _, _ = records.link(db, tenant_id, rel, rt.entity_type, rec.id, other_id, entities.get("link_note") or "")
        event_bus.publish(db, execution_id, "custom_record.unlinked" if unlink else "custom_record.linked",
                          {"relationship": rel.key, "entity_type": rt.entity_type, "id": rec.id, "related_type": other_type, "related_id": other_id})
        other = lifecycle_service.get_record(db, tenant_id, other_type, other_id)
        return {**_out(rt, rec.id, rec.number, "unlinked" if unlink else "linked"), "relationship": rel.key, "related_type": other_type,
                "related_id": other_id, "related": records.title_of(db, tenant_id, other_type, other), "linked": not unlink,
                "already_linked": not unlink and not created}

    return [SagaStep("link_records", link)]


def build_int156_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-156 - Find records of a type the person may see: words, state, parent, field values."""

    def find() -> dict[str, Any]:
        rt = _type(db, tenant_id, entities)
        access = _access(db, entities)
        query = records.visible_query(db, access, rt) if access is not None else db.query(CustomRecord).filter(CustomRecord.type_id == rt.id)
        words = str(entities.get("record_query") or "").strip()
        if words:
            query = query.filter(or_(CustomRecord.name.ilike(f"%{words}%"), CustomRecord.number.ilike(f"%{words}%")))
        if entities.get("record_state"):
            model = lifecycle_service.resolve_state_model(db, tenant_id, rt.entity_type)
            wanted = str(entities["record_state"]).strip()
            code = next((s.code for s in model.states.values() if wanted.lower() in (s.code, s.label.lower())), records.slug(wanted))
            query = query.filter(CustomRecord.state == code)
        if entities.get("parent_ref") not in (None, "") and rt.parent_entity_type:
            query = query.filter(CustomRecord.parent_id == records.resolve_reference(db, tenant_id, rt.parent_entity_type, entities["parent_ref"], "the parent"))
        for key, value in _fields(entities).items():
            defn = custom_field_service.definition_by_key(db, tenant_id, rt.entity_type, records.slug(key) or key)
            if defn is None:
                raise ValueError(f"{rt.label} has no field '{key}'")
            if custom_field_service.lookup_target(defn):
                value = records.resolve_reference(db, tenant_id, custom_field_service.lookup_target(defn), value, defn.label)
            stored = custom_field_service.coerce(defn, value)
            query = query.filter(CustomRecord.id.in_(db.query(CustomFieldValue.entity_id).filter(
                and_(CustomFieldValue.field_id == defn.id, CustomFieldValue.value.ilike(str(stored))))))
        total = query.count()
        rows = query.order_by(CustomRecord.id.desc()).limit(25).all()
        found = [{"id": r.id, "number": r.number, "name": r.name, "state": r.state,
                  **{k: v for k, v in custom_field_service.get_values(db, tenant_id, rt.entity_type, r.id).items() if v not in (None, "")}}
                 for r in rows]
        return {"entity_type": rt.entity_type, "record_type": rt.plural_label, "total": total, "records": found}

    return [SagaStep("find_records", find)]


def build_int157_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-157 - Delete a record - once nothing hangs off it or points to it."""

    def delete() -> dict[str, Any]:
        rt, rec = _target(db, tenant_id, entities)
        rec_id, number = rec.id, rec.number
        snapshot = records.delete_record(db, tenant_id, rt, rec)
        event_bus.publish(db, execution_id, "custom_record.deleted", {"record_type": rt.key, "id": rec_id, "number": number})
        out = _out(rt, rec_id, number, "deleted", deleted=snapshot)
        out.pop("entity_id")  # it's gone - nothing for the lifecycle hook or the outcome cards to open
        out.pop("entity_type")
        out["deleted_record"] = {"entity_type": rt.entity_type, "id": rec_id}
        return out

    return [SagaStep("delete_record", delete)]


def build_int158_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-158 - Design a record type from plain English (administrators): draft it, and build it unless asked only for the draft."""
    from app.crm_core import record_designer

    def design() -> dict[str, Any]:
        if entities.get("_actor_role") not in (None, "admin"):
            raise ValueError("Designing record types is for administrators")
        plan = entities.get("design_plan") if isinstance(entities.get("design_plan"), dict) else record_designer.draft(db, tenant_id, entities.get("design_text") or "")
        apply = str(entities.get("apply_design", True)).strip().lower() not in ("false", "no", "0", "off")
        if not apply:
            return {"applied": False, "plan": plan, "summary": record_designer.summary(plan)}
        report = record_designer.build(db, tenant_id, plan)
        event_bus.publish(db, execution_id, "record_type.designed", {"record_type": report["type"]["key"], "created": report["created_type"]})
        return {"applied": True, "record_type": report["type"]["label"], "record_type_key": report["type"]["key"],
                "fields": report["fields"], "states": report["states"], "relationships": report["relationships"],
                "rules": [r["text"] for r in report["rules"]], "notes": report["notes"], "summary": record_designer.summary(plan),
                "engine": plan.get("engine")}

    return [SagaStep("design_record_type", design)]


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-151": build_int151_steps,
    "INT-152": build_int152_steps,
    "INT-153": build_int153_steps,
    "INT-154": build_int154_steps,
    "INT-155": build_int155_steps,
    "INT-156": build_int156_steps,
    "INT-157": build_int157_steps,
    "INT-158": build_int158_steps,
}
