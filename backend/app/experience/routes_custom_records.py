"""Custom record types (Admin -> Record Types) and their records (the
Records page) - see crm_core/custom_record_service.py.

A type's fields are managed through the ordinary custom-field endpoints
(/api/admin/custom-fields with entity_type "x_<key>"); this router owns the
types themselves and the records. Reading needs the Records page; changing
a record needs the agent or admin level, like the other screens' writes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.access import require_page
from app.auth import require_roles
from app.crm_core import access_service
from app.crm_core import custom_record_service as svc
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.custom_records import CustomRecord, CustomRecordType
from app.schemas.custom_records import CustomRecordIn, CustomRecordUpdate, RecordTypeIn, RecordTypeUpdate
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


def _commit(db: Session, action, access: Access | None = None):
    """Runs a service call; a ValueError is the user's to fix (400), nothing of it is kept.
    With access, what it saves is stamped with the acting user, position and
    department - what the records' visibility follows - as an intent's are."""
    if access is not None:
        access_service.current_actor.set(access_service.actor_for(access))
    try:
        out = action()
        db.commit()
        return out
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- admin: types


@admin_router.get("")
def list_record_types(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return {"types": [svc.type_out(db, tenant_id, rt) for rt in svc.list_types(db, tenant_id)],
            "parent_options": svc.parent_options(db, tenant_id)}


@admin_router.post("", status_code=201)
def create_record_type(payload: RecordTypeIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    rt = _commit(db, lambda: svc.create_type(db, tenant_id, **payload.model_dump()))
    return svc.type_out(db, tenant_id, rt)


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


# ---------------------------------------------------------------- records


@router.get("/types")
def active_types(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> list[dict[str, Any]]:
    return [svc.type_out(db, access.tenant_id, rt) for rt in svc.list_types(db, access.tenant_id, active_only=True)]


@router.get("/parents/{key}")
def parent_choices(key: str, q: str = "", db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> list[dict[str, Any]]:
    """What a record of this type can hang off - for the form's picker."""
    return svc.search_parents(db, access, _type(db, access.tenant_id, key, active_only=True), q)


@router.get("/{key}")
def list_records(
    key: str, q: str = "", parent_id: int | None = None, limit: int = 50, offset: int = 0,
    db: Session = Depends(get_db), access: Access = Depends(_PAGE),
) -> dict[str, Any]:
    rt = _type(db, access.tenant_id, key, active_only=True)
    query = svc.visible_query(db, access, rt)
    if parent_id is not None:
        query = query.filter(CustomRecord.parent_id == parent_id)
    if q.strip():
        query = query.filter(or_(CustomRecord.name.ilike(f"%{q.strip()}%"), CustomRecord.number.ilike(f"%{q.strip()}%")))
    total = query.count()
    rows = query.order_by(CustomRecord.id.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
    return {"type": svc.type_out(db, access.tenant_id, rt), "total": total,
            "items": [svc.record_out(db, access.tenant_id, rt, r) for r in rows]}


@router.post("/{key}", status_code=201)
def create_record(
    key: str, payload: CustomRecordIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE), user: User = Depends(_WRITER),
) -> dict[str, Any]:
    rt = _type(db, access.tenant_id, key, active_only=True)
    if payload.parent_id is not None and not _parent_visible(db, access, rt, payload.parent_id):
        raise HTTPException(400, f"No {svc.parent_type_label(db, access.tenant_id, rt.parent_entity_type)} #{payload.parent_id}")
    rec = _commit(db, lambda: svc.create_record(db, access.tenant_id, user, rt, payload.name, payload.parent_id, payload.fields), access)
    return svc.record_out(db, access.tenant_id, rt, rec, detail=True)


def _parent_visible(db: Session, access: Access, rt: CustomRecordType, parent_id: int) -> bool:
    """A record can only be hung off a parent the user can see."""
    if not rt.parent_entity_type:
        return True  # the service refuses the link itself
    if svc.is_custom(rt.parent_entity_type):
        parent_type = svc.get_type(db, access.tenant_id, rt.parent_entity_type)
        parent = svc.get_record(db, access.tenant_id, parent_type, parent_id) if parent_type else None
        return parent is not None and svc.can_see(db, access, parent_type, parent)
    return access_service.can_see(db, access, rt.parent_entity_type, parent_id)


def _visible_record(db: Session, access: Access, key: str, record_id: int) -> tuple[CustomRecordType, CustomRecord]:
    rt = _type(db, access.tenant_id, key, active_only=True)
    rec = svc.get_record(db, access.tenant_id, rt, record_id)
    if rec is None or not svc.can_see(db, access, rt, rec):
        raise HTTPException(404, "Record not found")
    return rt, rec


@router.get("/{key}/{record_id}")
def get_record(key: str, record_id: int, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    rt, rec = _visible_record(db, access, key, record_id)
    return {"type": svc.type_out(db, access.tenant_id, rt), "record": svc.record_out(db, access.tenant_id, rt, rec, detail=True)}


@router.patch("/{key}/{record_id}")
def update_record(
    key: str, record_id: int, payload: CustomRecordUpdate, db: Session = Depends(get_db), access: Access = Depends(_PAGE),
    user: User = Depends(_WRITER),
) -> dict[str, Any]:
    rt, rec = _visible_record(db, access, key, record_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("parent_id") is not None and not _parent_visible(db, access, rt, changes["parent_id"]):
        raise HTTPException(400, f"No {svc.parent_type_label(db, access.tenant_id, rt.parent_entity_type)} #{changes['parent_id']}")
    _commit(db, lambda: svc.update_record(db, access.tenant_id, user, rt, rec, changes), access)
    return svc.record_out(db, access.tenant_id, rt, rec, detail=True)


@router.delete("/{key}/{record_id}", status_code=204)
def delete_record(
    key: str, record_id: int, db: Session = Depends(get_db), access: Access = Depends(_PAGE), user: User = Depends(_WRITER),
) -> None:
    rt, rec = _visible_record(db, access, key, record_id)
    _commit(db, lambda: svc.delete_record(db, access.tenant_id, user, rt, rec), access)
