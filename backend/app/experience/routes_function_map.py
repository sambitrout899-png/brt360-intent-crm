"""Admin -> Functional Map (orchestration/function_map.py): BRT's vanilla map,
read-only; the tenant's own map; and saving a changed picture, which first
shows what it changes in the engines and then applies it. Administrators only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.db import get_db
from app.models.auth import User, UserRole
from app.orchestration import function_map
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/admin/function-map", tags=["function-map"], dependencies=[Depends(require_roles(UserRole.admin))])


class MapIn(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    edges: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)


@router.get("/vanilla")
def vanilla(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """BRT's own map - the same for every tenant, never changed."""
    return function_map.vanilla(db, tenant_id)


@router.get("")
def mine(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """This tenant's map: the engines as they run now, arranged as its administrators left it."""
    return function_map.current(db, tenant_id)


@router.get("/restore")
def restore(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """The vanilla map as a draft of this tenant's map - nothing changes until it is saved."""
    return function_map.restore_draft(db, tenant_id)


@router.post("/plan")
def plan(payload: MapIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """What saving this picture would change - nothing is changed."""
    return function_map.plan(db, tenant_id, payload.model_dump())


@router.post("/apply")
def apply(payload: MapIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
          user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Re-orchestrates the engines to the picture (all together) and records it."""
    return function_map.apply(db, tenant_id, user.id, payload.model_dump())
