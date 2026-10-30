"""Change control API (orchestration/change_control.py): workspaces, locks,
releases and the organization's root administrator. Administrators only;
releases are prepared, simulated, deployed and rolled back by the root
administrator alone."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.change_control import Workspace
from app.orchestration import change_control
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/admin/change", tags=["change-control"], dependencies=[Depends(require_roles(UserRole.admin))])


def current_workspace(x_brt_workspace: int | None = Header(default=None), db: Session = Depends(get_db),
                      tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> Workspace | None:
    """The workspace a request works in (X-BRT-Workspace) - the caller's own and open - or None (live).
    Flow Studio and the Functional Map save into it."""
    try:
        return change_control.working(db, tenant_id, user, x_brt_workspace)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except change_control.ChangeError as exc:
        raise HTTPException(409, str(exc)) from exc


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)


class ReleaseIn(BaseModel):
    name: str = Field(default="", max_length=150)
    workspace_ids: list[int] = Field(default_factory=list, max_length=100)
    map_version_id: int | None = None


class LockIn(BaseModel):
    object_type: str = Field(min_length=1, max_length=40)
    object_key: str = Field(min_length=1, max_length=80)


class RootIn(BaseModel):
    user_id: int


def _run(fn, *args):
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except change_control.NotRoot as exc:
        raise HTTPException(403, str(exc)) from exc
    except (change_control.ChangeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/me")
def me(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Who deploys releases here, and whether that is the caller."""
    root = db.get(User, change_control.root_admin_id(db, tenant_id) or 0)
    return {"user_id": user.id, "is_root": change_control.is_root(db, user), "root_admin": {"id": root.id, "name": root.name} if root else None}


@router.put("/root-admin")
def set_root(payload: RootIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             user: User = Depends(get_current_user)) -> dict[str, Any]:
    """The root administrator hands the role to another active administrator."""
    from app.models.tenant import Tenant

    _run(change_control.require_root, db, user, "hand over the root administrator role")
    target = db.get(User, payload.user_id)
    if target is None or target.tenant_id != tenant_id or target.role != UserRole.admin or not target.is_active:
        raise HTTPException(409, "The root administrator must be an active administrator of this organization")
    db.get(Tenant, tenant_id).root_admin_user_id = target.id
    db.commit()
    return {"root_admin": {"id": target.id, "name": target.name}}


@router.get("/workspaces")
def list_workspaces(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return change_control.workspaces(db, tenant_id)


@router.post("/workspaces")
def create_workspace(payload: WorkspaceIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    return change_control.create_workspace(db, tenant_id, user, payload.name, payload.description)


@router.post("/workspaces/{workspace_id}/{action}")
def workspace_action(workspace_id: int, action: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    """submit (ready for a release) | reopen | discard (drafts dropped, locks freed)."""
    actions = {"submit": change_control.submit, "reopen": change_control.reopen, "discard": change_control.discard}
    if action not in actions:
        raise HTTPException(404, "Unknown workspace action")
    return _run(actions[action], db, tenant_id, user, workspace_id)


@router.post("/locks/break", status_code=204)
def break_lock(payload: LockIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
               user: User = Depends(get_current_user)) -> None:
    _run(change_control.break_lock, db, tenant_id, user, payload.object_type, payload.object_key)


@router.get("/releases")
def list_releases(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return change_control.releases(db, tenant_id)


@router.post("/releases")
def create_release(payload: ReleaseIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   user: User = Depends(get_current_user)) -> dict[str, Any]:
    return _run(change_control.create_release, db, tenant_id, user, payload.name, payload.workspace_ids, payload.map_version_id)


@router.post("/releases/{release_id}/{action}")
def release_action(release_id: int, action: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   user: User = Depends(get_current_user)) -> dict[str, Any]:
    """simulate (applied, checked, rolled back) | deploy (all or nothing) | rollback."""
    actions = {"simulate": change_control.simulate_release, "deploy": change_control.deploy_release, "rollback": change_control.rollback_release}
    if action not in actions:
        raise HTTPException(404, "Unknown release action")
    return _run(actions[action], db, tenant_id, user, release_id)
