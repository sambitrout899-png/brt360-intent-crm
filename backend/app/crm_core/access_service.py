"""Who sees what: pages from roles, data from positions.

Pages: the union of the pages of every active AppRole the user holds. A
user with no roles keeps the defaults for their built-in role (admin: all
pages, agent/viewer: all but Admin), so a tenant that hasn't set up roles
behaves exactly as before. A built-in admin always keeps the Admin page.

Data: the user works as one position at a time (their primary position,
or another one they hold, picked per request via X-Position-Id). That
position's data_scope is the widest view allowed; the user may narrow it
(X-Data-Scope) but never widen it:

  own              records I created or own, or that are owned by my position
  team             ...plus those of every position reporting into mine (any depth)
  department       ...everything created in or owned within my department
  department_tree  ...my department and all its sub-departments
  org              everything in the organization

Records are matched through EntityRecordMeta (created_by_* / owner_* /
department_id) - see records_filter. Users who hold no position yet, and
built-in admins, are not restricted (org) - so data never disappears
before the org structure is set up.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.auth import User, UserRole
from app.licensing import licensed_pages
from app.models.crm import Lead, Opportunity, Quote
from app.models.lifecycle import EntityRecordMeta, Position, PositionMember
from app.models.org import AppRole, Department, UserAppRole
from app.models.orders import Order

PAGES: list[tuple[str, str]] = [
    ("training", "Learning home (+ every Learning page unless specific ones are ticked)"),
    ("training_mine", "Learning · My trainings"),
    ("training_sessions", "Learning · Sessions"),
    ("training_programs", "Learning · Programs"),
    ("training_assessments", "Learning · Assessments"),
    ("training_people", "Learning · People & places"),
    ("training_facilitators", "Learning · Facilitators"),
    ("training_outcomes", "Learning · Outcomes"),
    ("training_messages", "Learning · Messages"),
    ("training_settings", "Learning · Settings"),
    ("training_audit", "Learning · Audit log"),
    ("dashboard", "Dashboard"),
    ("mywork", "My Work"),
    ("customer", "Customer 360"),
    ("pipeline", "Sales Pipeline"),
    ("orders", "Orders & Billing"),
    ("tickets", "Tickets"),
    ("field", "Field Service"),
    ("inventory", "Inventory"),
    ("catalog", "Catalog"),
    ("analytics", "Analytics & Reports"),
    ("records", "Custom Records"),
    ("intent", "Intent Command Bar"),
    ("audit", "Audit Log"),
    ("admin", "Admin"),
]
PAGE_KEYS = [k for k, _ in PAGES]
TRAINING_PAGES = [k for k in PAGE_KEYS if k.startswith("training_")]


def role_pages(raw: str | None) -> list[str]:
    """A role's pages. A role that grants the Learning home but none of the
    individual Learning pages (every role saved before the pages were split)
    keeps the whole module, as it had before."""
    pages = split_pages(raw)
    if "training" in pages and not any(p in TRAINING_PAGES for p in pages):
        pages += TRAINING_PAGES
    return pages

DATA_SCOPES: list[tuple[str, str]] = [
    ("own", "My data"),
    ("team", "My team's data"),
    ("department", "My department's data"),
    ("department_tree", "All departments (mine + sub-departments)"),
    ("org", "Whole organization"),
]
SCOPE_KEYS = [k for k, _ in DATA_SCOPES]


@dataclass
class Visibility:
    """None-valued => unrestricted (org)."""

    user_ids: set[int] = field(default_factory=set)
    position_ids: set[int] = field(default_factory=set)
    department_ids: set[int] = field(default_factory=set)
    # The departments of the user's own positions: records assigned to one of
    # them are theirs to see, whatever the width of their view.
    member_department_ids: set[int] = field(default_factory=set)


@dataclass
class Access:
    user: User
    tenant_id: int
    pages: list[str]
    roles: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    active_position: Position | None
    max_scope: str
    scope: str
    visibility: Visibility | None  # None = whole org

    @property
    def unrestricted(self) -> bool:
        return self.visibility is None


# The acting user for the current request - lets the lifecycle hook stamp
# created_by_* on records an intent creates without threading it through
# every step builder.
current_actor: contextvars.ContextVar[dict[str, int | None] | None] = contextvars.ContextVar("current_actor", default=None)


# ---------------------------------------------------------------- pages


def default_pages(user: User) -> list[str]:
    return list(PAGE_KEYS) if user.role == UserRole.admin else [k for k in PAGE_KEYS if k != "admin"]


def user_roles(db: Session, user: User) -> list[AppRole]:
    return (
        db.query(AppRole)
        .join(UserAppRole, UserAppRole.role_id == AppRole.id)
        .filter(UserAppRole.user_id == user.id, AppRole.is_active.is_(True), AppRole.tenant_id == user.tenant_id)
        .order_by(AppRole.name)
        .all()
    )


def has_assigned_roles(db: Session, user: User) -> bool:
    """Whether the user holds any role at all - switched-off ones included.
    Only a user who was never given a role keeps the open defaults; one whose
    roles are all switched off gets what those roles grant: nothing."""
    return (
        db.query(UserAppRole.id)
        .join(AppRole, AppRole.id == UserAppRole.role_id)
        .filter(UserAppRole.user_id == user.id, AppRole.tenant_id == user.tenant_id)
        .first()
        is not None
    )


def split_pages(raw: str | None) -> list[str]:
    return [p for p in (s.strip() for s in (raw or "").split(",")) if p in PAGE_KEYS]


def strict_access(db: Session, user: User) -> bool:
    """"Require role and position" is on and applies to this user (admins keep their defaults)."""
    from app.crm_core import org_policy

    return user.role != UserRole.admin and org_policy.is_on(db, user.tenant_id, "require_role_and_position")


def lacks_role(db: Session, user: User) -> bool:
    """A non-admin without any role while "Require role and position" is on:
    no pages and no intents until an administrator gives them one."""
    return strict_access(db, user) and not has_assigned_roles(db, user)


def pages_for(db: Session, user: User, roles: list[AppRole] | None = None) -> list[str]:
    roles = user_roles(db, user) if roles is None else roles
    if not roles and not has_assigned_roles(db, user):
        return [] if strict_access(db, user) else default_pages(user)
    granted = {p for r in roles for p in role_pages(r.pages)}
    if user.role == UserRole.admin:
        granted.add("admin")  # an admin can never lock themselves out of setup
        granted.add("inventory")  # nor out of the network record they set up (types, pools, reconciliation)
        granted.add("analytics")  # nor out of reporting - other roles get it in Admin -> Roles
        granted.add("records")  # nor out of the record types they define
    return [k for k in PAGE_KEYS if k in granted]


# ---------------------------------------------------------------- positions


def memberships(db: Session, user: User) -> list[tuple[PositionMember, Position]]:
    return (
        db.query(PositionMember, Position)
        .join(Position, Position.id == PositionMember.position_id)
        .filter(PositionMember.user_id == user.id, Position.is_active.is_(True), Position.tenant_id == user.tenant_id)
        .order_by(PositionMember.is_primary.desc(), Position.name)
        .all()
    )


def descendant_positions(db: Session, tenant_id: int, root_id: int) -> set[int]:
    children: dict[int | None, list[int]] = {}
    for pid, parent in db.query(Position.id, Position.parent_position_id).filter(Position.tenant_id == tenant_id).all():
        children.setdefault(parent, []).append(pid)
    found, stack = {root_id}, [root_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def descendant_departments(db: Session, tenant_id: int, root_id: int) -> set[int]:
    children: dict[int | None, list[int]] = {}
    for did, parent in db.query(Department.id, Department.parent_department_id).filter(Department.tenant_id == tenant_id).all():
        children.setdefault(parent, []).append(did)
    found, stack = {root_id}, [root_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def _members_of(db: Session, position_ids: set[int]) -> set[int]:
    if not position_ids:
        return set()
    return {uid for (uid,) in db.query(PositionMember.user_id).filter(PositionMember.position_id.in_(position_ids)).all()}


def visibility_for(db: Session, tenant_id: int, user: User, position: Position | None, scope: str) -> Visibility | None:
    if scope == "org" or position is None:
        return None
    if scope in ("department", "department_tree") and position.department_id is None:
        scope = "team"  # a position outside any department sees its team at most
    if scope == "own":
        return Visibility({user.id}, {position.id}, set())
    if scope == "team":
        positions = descendant_positions(db, tenant_id, position.id)
        return Visibility(_members_of(db, positions) | {user.id}, positions, set())
    departments = (
        descendant_departments(db, tenant_id, position.department_id)
        if scope == "department_tree"
        else {position.department_id}
    )
    positions = {
        pid for (pid,) in db.query(Position.id).filter(Position.tenant_id == tenant_id, Position.department_id.in_(departments)).all()
    }
    return Visibility(_members_of(db, positions) | {user.id}, positions | {position.id}, departments)


def clamp_scope(requested: str | None, max_scope: str) -> str:
    if requested not in SCOPE_KEYS:
        return max_scope
    return requested if SCOPE_KEYS.index(requested) <= SCOPE_KEYS.index(max_scope) else max_scope


def resolve(db: Session, user: User, requested_position_id: int | None = None, requested_scope: str | None = None) -> Access:
    roles = user_roles(db, user)
    held = memberships(db, user)
    positions = {p.id: p for _, p in held}
    # An unknown/foreign position id falls back to the primary - never an error
    # and never an escalation (it can only ever pick a position the user holds).
    active = positions.get(requested_position_id) if requested_position_id else None
    if active is None and held:
        active = held[0][1]  # primary first (memberships() ordering)

    held_back = active is None and strict_access(db, user)
    if held_back:
        max_scope = "own"  # no position yet: their own records, never the whole organization
    elif user.role == UserRole.admin or active is None:
        max_scope = "org"
    else:
        max_scope = active.data_scope if active.data_scope in SCOPE_KEYS else "own"
    default_scope = (active.data_scope if active and active.data_scope in SCOPE_KEYS else "org") if user.role == UserRole.admin else max_scope
    scope = clamp_scope(requested_scope or default_scope, max_scope)

    departments = {d.id: d.name for d in db.query(Department).filter(Department.tenant_id == user.tenant_id).all()}
    return Access(
        user=user,
        tenant_id=user.tenant_id,
        pages=licensed_pages(db, user, pages_for(db, user, roles)),
        roles=[{"id": r.id, "name": r.name} for r in roles],
        positions=[
            {
                "id": p.id,
                "name": p.name,
                "department_id": p.department_id,
                "department_name": departments.get(p.department_id),
                "data_scope": p.data_scope,
                "is_primary": m.is_primary,
            }
            for m, p in held
        ],
        active_position=active,
        max_scope=max_scope,
        scope=scope,
        visibility=_with_member_departments(
            Visibility({user.id}, set(), set()) if held_back else visibility_for(db, user.tenant_id, user, active, scope),
            {p.department_id for _, p in held if p.department_id},
        ),
    )


def _with_member_departments(v: Visibility | None, departments: set[int]) -> Visibility | None:
    if v is not None:
        v.member_department_ids = set(departments)
    return v


# ---------------------------------------------------------------- records


def _meta_condition(v: Visibility):
    conditions = []
    if v.user_ids:
        conditions += [EntityRecordMeta.created_by_user_id.in_(v.user_ids), EntityRecordMeta.owner_user_id.in_(v.user_ids)]
    if v.position_ids:
        conditions += [EntityRecordMeta.created_by_position_id.in_(v.position_ids), EntityRecordMeta.owner_position_id.in_(v.position_ids)]
    if v.department_ids:
        conditions.append(EntityRecordMeta.department_id.in_(v.department_ids))
    owner_departments = v.department_ids | v.member_department_ids
    if owner_departments:
        conditions.append(EntityRecordMeta.owner_department_id.in_(owner_departments))
    return or_(*conditions) if conditions else None


def _meta_visible_ids(db: Session, access: Access, entity_type: str) -> set[int]:
    condition = _meta_condition(access.visibility)  # type: ignore[arg-type]
    held = {position["id"] for position in access.positions}
    if held:
        owned = EntityRecordMeta.owner_user_id.is_(None) & EntityRecordMeta.owner_position_id.in_(held)
        condition = or_(condition, owned) if condition is not None else owned
    if condition is None:
        return set()
    rows = db.query(EntityRecordMeta.entity_id).filter(
        EntityRecordMeta.tenant_id == access.tenant_id, EntityRecordMeta.entity_type == entity_type, condition
    )
    return {eid for (eid,) in rows.all()} | _created_visible_ids(db, access, entity_type)


def _work_order_orders(db: Session, access: Access) -> set[int]:
    """Orders whose work order is assigned to the user, a position they hold or their department."""
    from app.models.assurance import WorkOrder

    v = access.visibility
    conditions = []
    if v.user_ids:
        conditions.append(EntityRecordMeta.owner_user_id.in_(v.user_ids))
    if v.position_ids:
        conditions.append(EntityRecordMeta.owner_position_id.in_(v.position_ids))
    held = {p["id"] for p in access.positions}
    if held:
        conditions.append(EntityRecordMeta.owner_position_id.in_(held))
    if v.department_ids | v.member_department_ids:
        conditions.append(EntityRecordMeta.owner_department_id.in_(v.department_ids | v.member_department_ids))
    conditions.append(EntityRecordMeta.owner_user_id == access.user.id)
    work_orders = [wid for (wid,) in db.query(EntityRecordMeta.entity_id).filter(
        EntityRecordMeta.tenant_id == access.tenant_id, EntityRecordMeta.entity_type == "work_order", or_(*conditions))]
    if not work_orders:
        return set()
    return {oid for (oid,) in db.query(WorkOrder.order_id).filter(WorkOrder.id.in_(work_orders), WorkOrder.order_id.isnot(None))}


def _created_visible_ids(db: Session, access: Access, entity_type: str) -> set[int]:
    """Records whose own system fields (who created them, as which position,
    in which department) fall inside the user's view - e.g. records loaded
    through BIM or the API, which have no ownership entry of their own."""
    from app.models.crm import Party

    model = {"party": Party, "lead": Lead, "opportunity": Opportunity, "quote": Quote, "order": Order}.get(entity_type)
    v = access.visibility
    if model is None or v is None:
        return set()
    conditions = []
    if v.user_ids:
        conditions.append(model.sys_created_by.in_(v.user_ids))
    if v.position_ids:
        conditions.append(model.sys_created_position_id.in_(v.position_ids))
    if v.department_ids:
        conditions.append(model.sys_department_id.in_(v.department_ids))
    if not conditions:
        return set()
    return {rid for (rid,) in db.query(model.id).filter(model.tenant_id == access.tenant_id, or_(*conditions)).all()}


def visible_ids(db: Session, access: Access, entity_type: str) -> set[int] | None:
    """Ids of the records of this type the user may see; None = all.

    A customer/prospect (party) is visible when its own record is, or when
    any visible lead/opportunity/quote/order belongs to it - so Customer 360
    opens for the customers whose deals the user works on."""
    if access.unrestricted:
        return None
    cache: dict[str, set[int]] = getattr(access, "_cache", {})
    access._cache = cache  # type: ignore[attr-defined]
    if entity_type in cache:
        return cache[entity_type]
    ids = _meta_visible_ids(db, access, entity_type)
    # A deal is seen whole: a visible opportunity's quotes, and the orders of a visible quote.
    if entity_type == "quote":
        opps = visible_ids(db, access, "opportunity") or set()
        if opps:
            ids |= {qid for (qid,) in db.query(Quote.id).filter(Quote.opportunity_id.in_(opps)).all()}
    if entity_type == "order":
        quotes = visible_ids(db, access, "quote") or set()
        if quotes:
            ids |= {oid for (oid,) in db.query(Order.id).filter(Order.quote_id.in_(quotes)).all()}
        # whoever holds one of its work orders (e.g. the Field Service Manager given the installation) sees the order
        ids |= _work_order_orders(db, access)
    if entity_type == "party":
        leads = visible_ids(db, access, "lead") or set()
        opps = visible_ids(db, access, "opportunity") or set()
        quotes = visible_ids(db, access, "quote") or set()
        orders = visible_ids(db, access, "order") or set()
        if quotes:
            opps = opps | {oid for (oid,) in db.query(Quote.opportunity_id).filter(Quote.id.in_(quotes)).all()}
        if opps:
            leads = leads | {lid for (lid,) in db.query(Opportunity.lead_id).filter(Opportunity.id.in_(opps)).all() if lid}
        if leads:
            ids |= {pid for (pid,) in db.query(Lead.party_id).filter(Lead.id.in_(leads)).all()}
        if orders:
            ids |= {pid for (pid,) in db.query(Order.party_id).filter(Order.id.in_(orders)).all()}
    cache[entity_type] = ids
    return ids


def can_see(db: Session, access: Access, entity_type: str, entity_id: int) -> bool:
    ids = visible_ids(db, access, entity_type)
    return ids is None or entity_id in ids


def apply(query, model, db: Session, access: Access, entity_type: str):
    """Restricts a query on `model` to the records the user may see."""
    ids = visible_ids(db, access, entity_type)
    return query if ids is None else query.filter(model.id.in_(ids or {-1}))


def actor_for(access: Access) -> dict[str, int | None]:
    position = access.active_position
    return {
        "user_id": access.user.id,
        "position_id": position.id if position else None,
        "department_id": position.department_id if position else None,
    }
