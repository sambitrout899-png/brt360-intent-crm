"""Custom record types (Admin -> Record Types) and their records (the Records page) - see
crm_core/custom_record_service.py and crm_core/record_designer.py.

Admin (admin role, Custom Development licence):
  GET/POST       /api/admin/record-types                 types (+ entity options for parents, lookups, relationships)
  PATCH/DELETE   /api/admin/record-types/{key}
  POST           /api/admin/record-types/design          plain English -> a draft plan (nothing saved)
  POST           /api/admin/record-types/design/build    build a plan (or a description) - type, fields, states, relationships, rules
  GET/POST       /api/admin/record-types/relationships   many-to-many relationships between record types
  PATCH/DELETE   /api/admin/record-types/relationships/{id}
  A type's fields are the ordinary custom fields (/api/admin/custom-fields, entity_type "x_<key>", data type
  "lookup" for a link to another record); its state model is /api/admin/state-models/x_<key>; its page and
  validation rules are the Page Designer's (/api/admin/pages); its automations are Flow Studio flows.

Records (the Records page; reading needs the page, writing the agent or admin level):
  GET    /api/custom-records/types | /parents/{key} | /search/{entity_type} | /related/{entity_type}/{id}
  GET    /api/custom-records/{key}                 list (?q=&state=&parent_id=&mine=)
  GET    /api/custom-records/{key}/form            its page, ready to render (?record_id=)
  POST   /api/custom-records/{key}                 create          -> INT-151
  GET    /api/custom-records/{key}/{id}            one record with its state, moves, related records, history
  PATCH  /api/custom-records/{key}/{id}            update          -> INT-152
  POST   /api/custom-records/{key}/{id}/transition move it         -> INT-153
  POST   /api/custom-records/{key}/{id}/assign     assign it       -> INT-154
  POST   /api/custom-records/{key}/{id}/links      link / unlink   -> INT-155
  DELETE /api/custom-records/{key}/{id}            delete          -> INT-157
Every write runs as an intent through the intent pipeline - the same path as the command bar and the
Flow Studio - so its before / after flows, state flows, assignment rules and audit entry always apply.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.access import get_access, require_page
from app.auth import get_current_user, require_roles
from app.crm_core import access_service, lifecycle_service, page_service
from app.crm_core import custom_record_service as svc
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.custom_records import CustomRecord, CustomRecordType, RecordRelationship
from app.schemas.custom_records import (
    AssignIn,
    CustomRecordIn,
    CustomRecordUpdate,
    DesignIn,
    LinkIn,
    RecordTypeIn,
    RecordTypeUpdate,
    RelationshipIn,
    RelationshipUpdate,
    TransitionIn,
)
from app.tenancy import get_current_tenant_id

admin_router = APIRouter(prefix="/api/admin/record-types", tags=["admin-record-types"], dependencies=[Depends(require_roles(UserRole.admin))])
router = APIRouter(prefix="/api/custom-records", tags=["custom-records"])

_PAGE = require_page("records")
_WRITER = require_roles(UserRole.admin, UserRole.agent)


def _type(db: Session, tenant_id: int, key: str, active_only: bool = False) -> CustomRecordType:
    rt = svc.get_type(db, tenant_id, key, active_only=active_only)
    if rt is None:
        raise HTTPException(404, "Record type not found")
    return rt


def _commit(db: Session, action):
    """Runs a service call; a ValueError is the user's to fix (400), nothing of it is kept."""
    try:
        out = action()
        db.commit()
        return out
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))


def _intent(db: Session, user: User, access: Access, code: str, entities: dict[str, Any], text: str) -> dict[str, Any]:
    """A write from the Records page, run as the intent it is - refused (400) with the intent's own reason."""
    from app.experience.routes_intent import _handle
    from app.schemas.intent import IntentRequest

    request = IntentRequest(text=text, channel="agent", structured={"intent_type": code, "entities": entities})
    response = _handle(request, db, access.tenant_id, user, access)
    if response.status not in ("executed", "verification_failed"):
        why = (response.error or {}).get("message") if response.error else None
        if not why and response.missing_fields:
            why = f"Fill in: {', '.join(response.missing_fields)}"
        raise HTTPException(400, why or response.summary or response.rationale)
    return response.result or {}


# ---------------------------------------------------------------- admin: types


@admin_router.get("")
def list_record_types(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return {"types": [svc.type_out(db, tenant_id, rt) for rt in svc.list_types(db, tenant_id)],
            "parent_options": svc.parent_options(db, tenant_id), "entity_options": svc.entity_options(db, tenant_id),
            "relationships": [svc.relationship_out(db, tenant_id, r) for r in svc.list_relationships(db, tenant_id)]}


@admin_router.post("", status_code=201)
def create_record_type(payload: RecordTypeIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    rt = _commit(db, lambda: svc.create_type(db, tenant_id, **payload.model_dump()))
    return svc.type_out(db, tenant_id, rt)


# ---------------------------------------------------------------- admin: AI design


@admin_router.post("/design")
def design_record_type(payload: DesignIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Plain English (or an edited plan) -> the checked plan, nothing saved - review it, then build it."""
    from app.crm_core import record_designer

    try:
        plan = record_designer.check(db, tenant_id, payload.plan) if payload.plan else record_designer.draft(db, tenant_id, payload.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        db.rollback()
    return {**plan, "summary": record_designer.summary(plan)}


@admin_router.post("/design/build", status_code=201)
def build_record_type(payload: DesignIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                      user: User = Depends(get_current_user), access: Access = Depends(get_access)) -> dict[str, Any]:
    """Builds the plan as the Design a record type intent (INT-158) - audited like any change."""
    entities: dict[str, Any] = {"design_text": payload.text or (payload.plan or {}).get("source_text") or "", "apply_design": True}
    if payload.plan:
        entities["design_plan"] = payload.plan
    if not entities["design_text"] and not payload.plan:
        raise HTTPException(400, "Describe the record type, or send the plan to build")
    if not entities["design_text"]:
        entities["design_text"] = (payload.plan.get("type") or {}).get("label", "record type")
    return _intent(db, user, access, "INT-158", entities, f"Design a record type: {entities['design_text']}"[:4000])


# ---------------------------------------------------------------- admin: relationships


def _relationship(db: Session, tenant_id: int, rel_id: int) -> RecordRelationship:
    rel = db.get(RecordRelationship, rel_id)
    if rel is None or rel.tenant_id != tenant_id:
        raise HTTPException(404, "Relationship not found")
    return rel


@admin_router.get("/relationships")
def list_relationships(entity_type: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [svc.relationship_out(db, tenant_id, r, entity_type) for r in svc.list_relationships(db, tenant_id, entity_type)]


@admin_router.post("/relationships", status_code=201)
def create_relationship(payload: RelationshipIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    rel = _commit(db, lambda: svc.create_relationship(db, tenant_id, **payload.model_dump()))
    return svc.relationship_out(db, tenant_id, rel)


@admin_router.patch("/relationships/{rel_id}")
def update_relationship(rel_id: int, payload: RelationshipUpdate, db: Session = Depends(get_db),
                        tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    rel = _relationship(db, tenant_id, rel_id)
    _commit(db, lambda: svc.update_relationship(db, tenant_id, rel, payload.model_dump(exclude_unset=True)))
    return svc.relationship_out(db, tenant_id, rel)


@admin_router.delete("/relationships/{rel_id}")
def delete_relationship(rel_id: int, force: bool = False, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    rel = _relationship(db, tenant_id, rel_id)
    return {"links_removed": _commit(db, lambda: svc.delete_relationship(db, tenant_id, rel, force))}


@admin_router.patch("/{key}")
def update_record_type(
    key: str, payload: RecordTypeUpdate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    rt = _type(db, tenant_id, key)
    _commit(db, lambda: svc.update_type(db, tenant_id, rt, payload.model_dump(exclude_unset=True)))
    return svc.type_out(db, tenant_id, rt)


@admin_router.delete("/{key}", status_code=204)
def delete_record_type(key: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    rt = _type(db, tenant_id, key)
    _commit(db, lambda: svc.delete_type(db, tenant_id, rt))


# ---------------------------------------------------------------- records: reading


@router.get("/types")
def active_types(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> list[dict[str, Any]]:
    return [svc.type_out(db, access.tenant_id, rt) for rt in svc.list_types(db, access.tenant_id, active_only=True)]


@router.get("/parents/{key}")
def parent_choices(key: str, q: str = "", db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> list[dict[str, Any]]:
    """What a record of this type can hang off - for the form's picker."""
    return svc.search_parents(db, access, _type(db, access.tenant_id, key, active_only=True), q)


@router.get("/search/{entity_type}")
def search_records(entity_type: str, q: str = "", db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> list[dict[str, Any]]:
    """Records of any type the user may see - the picker of a lookup field or a link."""
    return svc.search(db, access, entity_type, q)


@router.get("/related/{entity_type}/{record_id}")
def related_records(entity_type: str, record_id: int, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> list[dict[str, Any]]:
    """Records of the tenant's types linked to any record - a customer's site surveys, an order's inspections."""
    entity_type = lifecycle_service.normalize_entity_type(entity_type) or ""
    if lifecycle_service.get_record(db, access.tenant_id, entity_type, record_id) is None or not access_service.can_see(db, access, entity_type, record_id):
        raise HTTPException(404, "Record not found")
    return svc.related(db, access, entity_type, record_id)


@router.get("/{key}")
def list_records(
    key: str, q: str = "", state: str = "", parent_id: int | None = None, mine: bool = False, limit: int = 50, offset: int = 0,
    db: Session = Depends(get_db), access: Access = Depends(_PAGE),
) -> dict[str, Any]:
    rt = _type(db, access.tenant_id, key, active_only=True)
    query = svc.visible_query(db, access, rt)
    if parent_id is not None:
        query = query.filter(CustomRecord.parent_id == parent_id)
    if q.strip():
        query = query.filter(or_(CustomRecord.name.ilike(f"%{q.strip()}%"), CustomRecord.number.ilike(f"%{q.strip()}%")))
    if state.strip():
        query = query.filter(CustomRecord.state == state.strip())
    if mine:
        from app.models.lifecycle import EntityRecordMeta

        owned = [eid for (eid,) in db.query(EntityRecordMeta.entity_id).filter(
            EntityRecordMeta.tenant_id == access.tenant_id, EntityRecordMeta.entity_type == rt.entity_type,
            EntityRecordMeta.owner_user_id == access.user.id)]
        query = query.filter(or_(CustomRecord.id.in_(owned or [-1]), CustomRecord.sys_created_by == access.user.id))
    total = query.count()
    rows = query.order_by(CustomRecord.id.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
    counts = _state_counts(svc.visible_query(db, access, rt))
    return {"type": svc.type_out(db, access.tenant_id, rt), "total": total, "state_counts": counts,
            "items": [svc.record_out(db, access.tenant_id, rt, r) for r in rows]}


def _state_counts(query) -> dict[str, int]:
    from sqlalchemy import func

    return {state or "": n for state, n in query.with_entities(CustomRecord.state, func.count(CustomRecord.id)).group_by(CustomRecord.state)}


@router.get("/{key}/form")
def record_form(key: str, record_id: int | None = None, page_key: str | None = None, db: Session = Depends(get_db),
                access: Access = Depends(_PAGE)) -> dict[str, Any]:
    """The type's page from the Page Designer - sections, fields (lookups with their target), mandatory flags,
    the simple rules the browser checks as you type, its buttons - with the record's values when given."""
    rt = _type(db, access.tenant_id, key, active_only=True)
    values: dict[str, Any] = {}
    if record_id is not None:
        _, rec = _visible_record(db, access, key, record_id)
        values = page_service.record_values(db, access.tenant_id, rt.entity_type, rec)
    role_ids = {r.id for r in access_service.user_roles(db, access.user)}
    return page_service.form(db, access.tenant_id, rt.entity_type, None, page_key, values, role_ids)


def _visible_record(db: Session, access: Access, key: str, record_id: int) -> tuple[CustomRecordType, CustomRecord]:
    rt = _type(db, access.tenant_id, key, active_only=True)
    rec = svc.get_record(db, access.tenant_id, rt, record_id)
    if rec is None or not svc.can_see(db, access, rt, rec):
        raise HTTPException(404, "Record not found")
    return rt, rec


@router.get("/{key}/{record_id}")
def get_record(key: str, record_id: int, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    rt, rec = _visible_record(db, access, key, record_id)
    return {"type": svc.type_out(db, access.tenant_id, rt),
            "record": svc.record_out(db, access.tenant_id, rt, rec, detail=True, access=access)}


# ---------------------------------------------------------------- records: writing - each an intent


def _parent_visible(db: Session, access: Access, rt: CustomRecordType, parent_ref: Any) -> None:
    """A record can only be hung off a parent the user can see."""
    if parent_ref in (None, "") or not rt.parent_entity_type:
        return
    try:
        parent_id = svc.resolve_reference(db, access.tenant_id, rt.parent_entity_type, parent_ref, "the parent")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not access_service.can_see(db, access, rt.parent_entity_type, parent_id):
        raise HTTPException(400, f"No {svc.label_of(db, access.tenant_id, rt.parent_entity_type)} #{parent_ref}")


@router.post("/{key}", status_code=201)
def create_record(
    key: str, payload: CustomRecordIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE), user: User = Depends(_WRITER),
) -> dict[str, Any]:
    rt = _type(db, access.tenant_id, key, active_only=True)
    _parent_visible(db, access, rt, payload.parent_id)
    entities = {"record_type": rt.entity_type, "record_name": payload.name, "parent_ref": payload.parent_id, "fields": payload.fields,
                **({"page_key": payload.page_key} if payload.page_key else {})}
    out = _intent(db, user, access, "INT-151", entities, f"Create a {rt.label}: {payload.name}")
    rec = svc.get_record(db, access.tenant_id, rt, out["entity_id"])
    return svc.record_out(db, access.tenant_id, rt, rec, detail=True, access=access)


@router.patch("/{key}/{record_id}")
def update_record(
    key: str, record_id: int, payload: CustomRecordUpdate, db: Session = Depends(get_db), access: Access = Depends(_PAGE),
    user: User = Depends(_WRITER),
) -> dict[str, Any]:
    rt, rec = _visible_record(db, access, key, record_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("parent_id") not in (None, ""):
        _parent_visible(db, access, rt, changes["parent_id"])
    entities: dict[str, Any] = {"entity_type": rt.entity_type, "entity_id": rec.id, "fields": changes.get("fields") or {}}
    if "name" in changes:
        entities["record_name"] = changes["name"]
    if "parent_id" in changes:
        entities["parent_ref"] = changes["parent_id"]
    if changes.get("page_key"):
        entities["page_key"] = changes["page_key"]
    _intent(db, user, access, "INT-152", entities, f"Update {rt.label} {rec.number}")
    db.refresh(rec)
    return svc.record_out(db, access.tenant_id, rt, rec, detail=True, access=access)


@router.post("/{key}/{record_id}/transition")
def move_record(key: str, record_id: int, payload: TransitionIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE),
                user: User = Depends(_WRITER)) -> dict[str, Any]:
    rt, rec = _visible_record(db, access, key, record_id)
    out = _intent(db, user, access, "INT-153", {"entity_type": rt.entity_type, "entity_id": rec.id, "record_state": payload.state,
                                                 "reasoning": payload.reason}, f"Move {rt.label} {rec.number} to {payload.state}")
    db.refresh(rec)
    return {"outcome": {k: out.get(k) for k in ("from_state", "to_state", "action", "approval_request_id") if out.get(k) is not None},
            "record": svc.record_out(db, access.tenant_id, rt, rec, detail=True, access=access)}


@router.post("/{key}/{record_id}/assign")
def assign_record(key: str, record_id: int, payload: AssignIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE),
                  user: User = Depends(_WRITER)) -> dict[str, Any]:
    rt, rec = _visible_record(db, access, key, record_id)
    _intent(db, user, access, "INT-154", {"entity_type": rt.entity_type, "entity_id": rec.id,
                                          **{k: v for k, v in payload.model_dump().items() if v}}, f"Assign {rt.label} {rec.number}")
    return svc.record_out(db, access.tenant_id, rt, rec, detail=True, access=access)


@router.post("/{key}/{record_id}/links")
def link_records(key: str, record_id: int, payload: LinkIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE),
                 user: User = Depends(_WRITER)) -> dict[str, Any]:
    rt, rec = _visible_record(db, access, key, record_id)
    _intent(db, user, access, "INT-155", {"entity_type": rt.entity_type, "entity_id": rec.id, "relationship": payload.relationship,
                                          "related_ref": payload.related, "unlink": payload.unlink, "link_note": payload.note},
            f"{'Unlink' if payload.unlink else 'Link'} {rt.label} {rec.number}")
    return svc.record_out(db, access.tenant_id, rt, rec, detail=True, access=access)


@router.delete("/{key}/{record_id}", status_code=204)
def delete_record(
    key: str, record_id: int, db: Session = Depends(get_db), access: Access = Depends(_PAGE), user: User = Depends(_WRITER),
) -> None:
    rt, rec = _visible_record(db, access, key, record_id)
    _intent(db, user, access, "INT-157", {"entity_type": rt.entity_type, "entity_id": rec.id}, f"Delete {rt.label} {rec.number}")
