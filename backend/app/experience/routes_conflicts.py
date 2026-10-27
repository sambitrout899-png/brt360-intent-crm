"""Admin -> Conflicts (orchestration/conflicts.py): automations and actions
that pull against each other, and the admin's priority and conditions for
them. Plans and rehearsals report the conflicts they touch; settling them is
for administrators."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.db import get_db
from app.models.auth import User, UserRole
from app.orchestration import conflicts
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/admin/conflicts", tags=["admin-conflicts"], dependencies=[Depends(require_roles(UserRole.admin))])


class ResolveIn(BaseModel):
    key: str
    order: list[int] | None = None
    mode: str = "all"
    # flow id -> {field, operator, value}; an empty one = always
    conditions: dict[str, dict[str, Any] | None] = {}
    # a phrase conflict: the action (flow id) that keeps the phrase
    winner: int | None = None


@router.get("")
def list_conflicts(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    found = conflicts.scan(db, tenant_id)
    return {"conflicts": found, "open": sum(1 for c in found if not c["resolved"]), "rules": conflicts.rules(db, tenant_id),
            "operators": list(conflicts._OPERATORS)}


@router.post("/resolve")
def resolve_conflict(payload: ResolveIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        rule = conflicts.resolve(db, tenant_id, user, payload.key, payload.order, payload.mode, payload.conditions, payload.winner)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"rule": rule, "conflicts": conflicts.scan(db, tenant_id)}


@router.delete("/rules/{key:path}")
def clear_rule(key: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
               user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        conflicts.clear(db, tenant_id, user, key)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"conflicts": conflicts.scan(db, tenant_id)}
