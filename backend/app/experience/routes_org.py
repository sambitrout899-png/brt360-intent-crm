"""Org structure (departments; positions live in routes_studio.py), roles
(pages per role) and per-user access (roles + positions + primary
position) - admin-only. Plus GET /api/access/me, which every signed-in
user's UI calls to learn its pages, positions and data scope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.access import get_access
from app.auth import require_roles
from app.crm_core import access_service, org_policy
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.lifecycle import Position, PositionMember
from app.models.org import AppRole, Department, UserAppRole
from app.schemas.org import (
    AccessCatalogOut,
    AppRoleIn,
    AppRoleOut,
    DepartmentIn,
    DepartmentOut,
    MyAccessOut,
    UserAccessIn,
    UserAccessOut,
)
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/admin", tags=["admin-org"], dependencies=[Depends(require_roles(UserRole.admin))])
access_router = APIRouter(prefix="/api/access", tags=["access"])


# ---------- me ----------


@access_router.get("/me", response_model=MyAccessOut)
def my_access(access: Access = Depends(get_access)) -> MyAccessOut:
    max_index = access_service.SCOPE_KEYS.index(access.max_scope)
    return MyAccessOut(
        user={"id": access.user.id, "name": access.user.name, "email": access.user.email, "role": access.user.role.value},
        pages=access.pages,
        roles=access.roles,
        positions=access.positions,
        active_position_id=access.active_position.id if access.active_position else None,
        max_scope=access.max_scope,
        scope=access.scope,
        available_scopes=[{"key": k, "label": label} for k, label in access_service.DATA_SCOPES[: max_index + 1]],
    )


@router.get("/access-catalog", response_model=AccessCatalogOut)
def access_catalog() -> AccessCatalogOut:
    return AccessCatalogOut(
        pages=[{"key": k, "label": label} for k, label in access_service.PAGES],
        data_scopes=[{"key": k, "label": label} for k, label in access_service.DATA_SCOPES],
    )


# ---------- departments ----------


def _owned_department(db: Session, tenant_id: int, department_id: int) -> Department:
    department = db.get(Department, department_id)
    if department is None or department.tenant_id != tenant_id:
        raise HTTPException(404, "Department not found")
    return department


def _check_department(db: Session, tenant_id: int, payload: DepartmentIn, department_id: int | None = None) -> None:
    clash = db.query(Department).filter(Department.tenant_id == tenant_id, Department.name == payload.name)
    if department_id is not None:
        clash = clash.filter(Department.id != department_id)
    if clash.first():
        raise HTTPException(400, f"Department '{payload.name}' already exists")
    if payload.parent_department_id is not None:
        _owned_department(db, tenant_id, payload.parent_department_id)
        if department_id is not None and payload.parent_department_id in access_service.descendant_departments(db, tenant_id, department_id):
            raise HTTPException(400, "A department cannot sit under itself or one of its own sub-departments")


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Department]:
    return db.query(Department).filter(Department.tenant_id == tenant_id).order_by(Department.name).all()


@router.post("/departments", response_model=DepartmentOut, status_code=201)
def create_department(payload: DepartmentIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Department:
    _check_department(db, tenant_id, payload)
    department = Department(tenant_id=tenant_id, name=payload.name, parent_department_id=payload.parent_department_id)
    db.add(department)
    db.commit()
    db.refresh(department)
    return department


@router.put("/departments/{department_id}", response_model=DepartmentOut)
def update_department(
    department_id: int, payload: DepartmentIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> Department:
    department = _owned_department(db, tenant_id, department_id)
    _check_department(db, tenant_id, payload, department_id)
    department.name = payload.name
    department.parent_department_id = payload.parent_department_id
    db.commit()
    db.refresh(department)
    return department


@router.delete("/departments/{department_id}", status_code=204)
def delete_department(department_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    department = _owned_department(db, tenant_id, department_id)
    if db.query(Department).filter(Department.parent_department_id == department.id).first():
        raise HTTPException(400, "Move or delete its sub-departments first")
    if db.query(Position).filter(Position.department_id == department.id, Position.is_active.is_(True)).first():
        raise HTTPException(400, "Move or delete the positions in this department first")
    # Records created in it keep the id (EntityRecordMeta.department_id) -
    # deactivate rather than delete so nothing dangles.
    department.is_active = False
    db.commit()


# ---------- roles ----------


def _role_out(db: Session, role: AppRole) -> AppRoleOut:
    count = db.query(UserAppRole).filter(UserAppRole.role_id == role.id).count()
    return AppRoleOut(
        id=role.id,
        name=role.name,
        description=role.description,
        pages=access_service.split_pages(role.pages),
        is_active=role.is_active,
        user_count=count,
    )


def _owned_role(db: Session, tenant_id: int, role_id: int) -> AppRole:
    role = db.get(AppRole, role_id)
    if role is None or role.tenant_id != tenant_id:
        raise HTTPException(404, "Role not found")
    return role


def _clean_pages(pages: list[str]) -> str:
    unknown = [p for p in pages if p not in access_service.PAGE_KEYS]
    if unknown:
        raise HTTPException(400, f"Unknown page(s): {', '.join(unknown)}")
    return ",".join(k for k in access_service.PAGE_KEYS if k in pages)


@router.get("/roles", response_model=list[AppRoleOut])
def list_roles(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[AppRoleOut]:
    return [_role_out(db, r) for r in db.query(AppRole).filter(AppRole.tenant_id == tenant_id).order_by(AppRole.name).all()]


@router.post("/roles", response_model=AppRoleOut, status_code=201)
def create_role(payload: AppRoleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> AppRoleOut:
    if db.query(AppRole).filter(AppRole.tenant_id == tenant_id, AppRole.name == payload.name).first():
        raise HTTPException(400, f"Role '{payload.name}' already exists")
    role = AppRole(tenant_id=tenant_id, name=payload.name, description=payload.description, pages=_clean_pages(payload.pages))
    db.add(role)
    db.commit()
    db.refresh(role)
    return _role_out(db, role)


@router.put("/roles/{role_id}", response_model=AppRoleOut)
def update_role(role_id: int, payload: AppRoleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> AppRoleOut:
    role = _owned_role(db, tenant_id, role_id)
    if db.query(AppRole).filter(AppRole.tenant_id == tenant_id, AppRole.name == payload.name, AppRole.id != role_id).first():
        raise HTTPException(400, f"Role '{payload.name}' already exists")
    role.name, role.description, role.pages = payload.name, payload.description, _clean_pages(payload.pages)
    db.commit()
    db.refresh(role)
    return _role_out(db, role)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(role_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    role = _owned_role(db, tenant_id, role_id)
    # A user left without any role falls back to the open defaults (every
    # page and intent) - so a role can't be deleted while it is someone's only role.
    stranded = []
    for (user_id,) in db.query(UserAppRole.user_id).filter(UserAppRole.role_id == role.id).all():
        if db.query(UserAppRole).filter(UserAppRole.user_id == user_id, UserAppRole.role_id != role.id).count() == 0:
            user = db.get(User, user_id)
            stranded.append(user.name if user else f"user {user_id}")
    if stranded:
        raise HTTPException(
            400,
            f"'{role.name}' is the only role of {', '.join(sorted(stranded))} - give them another role first, "
            "otherwise they would get every page and intent.",
        )
    db.query(UserAppRole).filter(UserAppRole.role_id == role.id).delete()
    db.delete(role)
    db.commit()


# ---------- access & approval switches ----------


@router.get("/access-policy")
def get_access_policy(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, bool]:
    return org_policy.get(db, tenant_id)


@router.put("/access-policy")
def set_access_policy(
    payload: dict[str, bool], db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict[str, bool]:
    try:
        out = org_policy.save(db, tenant_id, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return out


# ---------- per-user access ----------


def _user_access_out(db: Session, user: User) -> UserAccessOut:
    held = access_service.memberships(db, user)
    roles = access_service.user_roles(db, user)
    return UserAccessOut(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role.value,
        is_active=user.is_active,
        role_ids=[r.id for r in roles],
        position_ids=[p.id for _, p in held],
        primary_position_id=next((p.id for m, p in held if m.is_primary), None),
        pages=access_service.pages_for(db, user, roles),
    )


@router.get("/user-access", response_model=list[UserAccessOut])
def list_user_access(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[UserAccessOut]:
    users = db.query(User).filter(User.tenant_id == tenant_id).order_by(User.name).all()
    return [_user_access_out(db, u) for u in users]


@router.put("/users/{user_id}/access", response_model=UserAccessOut)
def set_user_access(
    user_id: int, payload: UserAccessIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> UserAccessOut:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise HTTPException(404, "User not found")
    for role_id in payload.role_ids:
        _owned_role(db, tenant_id, role_id)
    positions = {}
    for position_id in payload.position_ids:
        position = db.get(Position, position_id)
        if position is None or position.tenant_id != tenant_id or not position.is_active:
            raise HTTPException(400, f"Position {position_id} not found")
        positions[position_id] = position
    if (
        user.role != UserRole.admin
        and user.is_active
        and org_policy.is_on(db, tenant_id, "require_role_and_position")
        and (not payload.role_ids or not positions)
    ):
        missing = " and ".join(w for w, empty in (("a role", not payload.role_ids), ("a position", not positions)) if empty)
        raise HTTPException(400, f"{user.name} needs {missing} - every user must hold at least one role and one position.")
    primary = payload.primary_position_id
    if primary is not None and primary not in positions:
        raise HTTPException(400, "The primary position must be one of the user's positions")
    if primary is None and positions:
        primary = next(iter(positions))  # one position always has a primary

    db.query(UserAppRole).filter(UserAppRole.user_id == user.id).delete()
    for role_id in dict.fromkeys(payload.role_ids):
        db.add(UserAppRole(user_id=user.id, role_id=role_id))

    existing = {m.position_id: m for m in db.query(PositionMember).filter(PositionMember.user_id == user.id).all()}
    for position_id, member in existing.items():
        if position_id not in positions:
            db.delete(member)
    for position_id in positions:
        member = existing.get(position_id) or PositionMember(position_id=position_id, user_id=user.id)
        member.is_primary = position_id == primary
        db.add(member)
    db.commit()
    return _user_access_out(db, user)
