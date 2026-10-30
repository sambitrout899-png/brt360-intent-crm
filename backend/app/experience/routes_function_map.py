"""Admin -> Functional Map (orchestration/function_map.py). BRT's original map
and the map the tenant runs are read-only: a tenant changes its map only by
making versions, working on drafts and activating one - activating first
shows what it changes in the engines. Administrators only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.db import get_db
from app.models.auth import User, UserRole
from app.orchestration import function_map
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/admin/function-map", tags=["function-map"], dependencies=[Depends(require_roles(UserRole.admin))])


class VersionIn(BaseModel):
    name: str = Field(default="", max_length=150)
    # "original" (BRT's), "active" (the map in use) or a version's id
    source: str | int = "active"


class VersionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    notes: str | None = Field(default=None, max_length=2000)
    nodes: list[dict[str, Any]] | None = Field(default=None, max_length=2000)
    edges: list[dict[str, Any]] | None = Field(default=None, max_length=5000)


def _run(fn, *args):
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except function_map.VersionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/vanilla")
def vanilla(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """BRT's original map - the same for every tenant, never changed by one."""
    return function_map.vanilla(db, tenant_id)


@router.get("")
def mine(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """The map this tenant runs: the engines as they run now - read-only."""
    return {**function_map.current(db, tenant_id), **function_map.versions(db, tenant_id)}


@router.get("/versions")
def list_versions(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return function_map.versions(db, tenant_id)


@router.post("/versions")
def create_version(payload: VersionIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   user: User = Depends(get_current_user)) -> dict[str, Any]:
    """A new draft, copied from BRT's original, the map in use or another version."""
    return _run(function_map.create_version, db, tenant_id, user.id, payload.name, payload.source)


@router.get("/versions/{version_id}")
def get_version(version_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return _run(function_map.version_map, db, tenant_id, version_id)


@router.put("/versions/{version_id}")
def save_version(version_id: int, payload: VersionUpdate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                 user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Saves a draft - nothing changes in the engines until it is activated."""
    return _run(function_map.save_version, db, tenant_id, version_id, user.id, payload.model_dump())


@router.delete("/versions/{version_id}", status_code=204)
def delete_version(version_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   user: User = Depends(get_current_user)) -> None:
    _run(function_map.delete_version, db, tenant_id, version_id, user.id)


@router.post("/versions/{version_id}/revise")
def revise_version(version_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   user: User = Depends(get_current_user)) -> dict[str, Any]:
    """The next revision of a version (v3 -> v3.1): a draft to work on, keeping its number."""
    return _run(function_map.revise_version, db, tenant_id, version_id, user.id)


def _root(db: Session, user: User) -> None:
    from app.orchestration import change_control

    try:
        change_control.require_root(db, user, "activate a version of the map")
    except change_control.NotRoot as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/versions/{version_id}/plan")
def plan_version(version_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """What activating this version would change - nothing is changed."""
    return _run(function_map.plan_activation, db, tenant_id, version_id)


@router.post("/versions/{version_id}/simulate")
def simulate_version(version_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """The version played through - its processes built and their flows run on
    sample records - then everything rolled back."""
    from app.crm_core import llm

    with llm.budget(45.0):
        return _run(function_map.simulate, db, tenant_id, version_id)


@router.post("/versions/{version_id}/activate")
def activate_version(version_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Re-orchestrates the engines to this version; the one it replaces is retired.
    Only the organization's root administrator activates."""
    _root(db, user)
    return _run(function_map.activate, db, tenant_id, version_id, user.id)


@router.post("/original/plan")
def plan_original(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """What going back to BRT's original map would change - nothing is changed."""
    return function_map.plan_activation(db, tenant_id, None)


@router.post("/original/activate")
def activate_original(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                      user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Runs BRT's original map again; the active version is retired. Only the root administrator."""
    _root(db, user)
    return function_map.activate(db, tenant_id, None, user.id)
