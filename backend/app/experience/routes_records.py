"""Generic record lifecycle view (Phase 7) - read-only, like routes_crm.py:
every page uses it to show a record's state, owner, custom fields and the
lifecycle actions available from here, and then offers those actions as
intents (POST /api/intents) rather than as PATCH forms.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.access import get_access
from app.auth import get_current_user
from app.crm_core import access_service, approval_service, custom_field_service, lifecycle_service, record_actions, record_service
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User
from app.models.crm import Party, Quote
from app.models.lifecycle import EntityAssignmentHistory, EntityRecordMeta, EntityStateHistory, Position
from app.schemas.studio import AssigneesOut, CustomFieldOut, MyWorkItemOut, RecordLifecycleOut
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/records", tags=["records"])


def _title(db: Session, entity_type: str, obj) -> str:
    if entity_type == "party":
        return obj.name
    if entity_type == "lead":
        return f"Lead #{obj.id} - {obj.party.name}"
    if entity_type == "opportunity":
        return obj.name
    if entity_type == "quote":
        return f"Quote #{obj.id} (rev {obj.revision_number})"
    party = db.get(Party, obj.party_id)
    return f"Order #{obj.id}" + (f" - {party.name}" if party else "")


def _ref(db: Session, model, ref_id: int | None, fields: tuple[str, ...]) -> dict | None:
    if ref_id is None:
        return None
    obj = db.get(model, ref_id)
    return {f: getattr(obj, f) for f in fields} if obj is not None else None


@router.get("/custom-fields/{entity_type}", response_model=list[CustomFieldOut])
def custom_field_definitions(
    entity_type: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
):
    normalized = lifecycle_service.normalize_entity_type(entity_type)
    if normalized is None:
        raise HTTPException(404, "Unknown entity type")
    return custom_field_service.list_definitions(db, tenant_id, normalized)


@router.get("/assignees", response_model=AssigneesOut)
def assignees(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> AssigneesOut:
    users = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True)).order_by(User.name).all()
    positions = db.query(Position).filter(Position.tenant_id == tenant_id, Position.is_active.is_(True)).order_by(Position.name).all()
    from app.models.org import Department

    departments = db.query(Department).filter(Department.tenant_id == tenant_id, Department.is_active.is_(True)).order_by(Department.name).all()
    return AssigneesOut(
        users=[{"id": u.id, "name": u.name, "email": u.email, "role": u.role.value} for u in users],
        positions=[{"id": p.id, "name": p.name, "member_count": len(p.members)} for p in positions],
        departments=[{"id": d.id, "name": d.name} for d in departments],
    )


@router.get("/my-work", response_model=list[MyWorkItemOut])
def my_work(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)
) -> list[MyWorkItemOut]:
    """Records owned by me directly, by a position I hold, or by the department of one."""
    from app.models.org import Department

    held = [position for _, position in access_service.memberships(db, user)]
    position_names = {p.id: p.name for p in held}
    department_ids = {p.department_id for p in held if p.department_id}
    department_names = {d.id: d.name for d in db.query(Department).filter(Department.id.in_(department_ids))} if department_ids else {}
    query = db.query(EntityRecordMeta).filter(EntityRecordMeta.tenant_id == tenant_id)
    metas = [
        m for m in query.all()
        if m.owner_user_id == user.id or (m.owner_user_id is None and m.owner_position_id in position_names)
        or (m.owner_user_id is None and m.owner_position_id is None and m.owner_department_id in department_names)
    ]
    items: list[MyWorkItemOut] = []
    for meta in metas:
        if meta.entity_type == "work_order":
            # a work order handed over by a flow (e.g. installation -> the Field Service Manager)
            from app.models.assurance import WorkOrder

            wo = db.get(WorkOrder, meta.entity_id)
            if wo is None or wo.tenant_id != tenant_id or wo.status == "complete":
                continue
            items.append(MyWorkItemOut(
                entity_type="work_order", entity_id=wo.id,
                title=f"{wo.kind.replace('_', ' ').capitalize()} work order #{wo.id}" + (f" - order #{wo.order_id}" if wo.order_id else ""),
                state=wo.status,
                via_position=(position_names.get(meta.owner_position_id) or department_names.get(meta.owner_department_id))
                if meta.owner_user_id is None else None,
                updated_at=meta.updated_at, open_entity_type="order" if wo.order_id else None, open_entity_id=wo.order_id,
            ))
            continue
        obj = lifecycle_service.get_record(db, tenant_id, meta.entity_type, meta.entity_id)
        if obj is None:
            continue
        state = lifecycle_service.effective_state(db, tenant_id, meta.entity_type, obj, meta)
        model = lifecycle_service.resolve_state_model(db, tenant_id, meta.entity_type)
        if lifecycle_service.state_def(model, state).is_terminal:
            continue
        items.append(
            MyWorkItemOut(
                entity_type=meta.entity_type,
                entity_id=meta.entity_id,
                title=_title(db, meta.entity_type, obj),
                state=state,
                via_position=(position_names.get(meta.owner_position_id) or department_names.get(meta.owner_department_id))
                if meta.owner_user_id is None else None,
                updated_at=meta.updated_at,
            )
        )
    return sorted(items, key=lambda i: i.updated_at, reverse=True)


class _Refs(BaseModel):
    refs: list[str] = Field(default_factory=list, max_length=500)  # "quote:12", "lead:3", ...


@router.post("/actions")
def record_buttons(
    payload: _Refs,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
    access: Access = Depends(get_access),
) -> dict[str, list[dict]]:
    """The buttons of the given records (any page listing several), keyed "type:id"."""
    out: dict[str, list[dict]] = {}
    for ref in dict.fromkeys(payload.refs):
        entity_type, _, raw_id = ref.partition(":")
        normalized = lifecycle_service.normalize_entity_type(entity_type)
        obj = lifecycle_service.get_record(db, tenant_id, normalized, raw_id) if normalized else None
        if obj is not None and access_service.can_see(db, access, normalized, obj.id):
            out[f"{normalized}:{obj.id}"] = record_actions.for_record(db, tenant_id, user, normalized, obj)
    return out


@router.get("/actions/party/{party_id}")
def party_record_actions(
    party_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
    access: Access = Depends(get_access),
) -> dict[str, list[dict]]:
    """The buttons of every lead / opportunity / quote / order of a customer, keyed "type:id"."""
    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id or not access_service.can_see(db, access, "party", party_id):
        raise HTTPException(404, "Customer not found")
    return record_actions.for_party(db, tenant_id, user, party_id, lambda et, eid: access_service.can_see(db, access, et, eid))


@router.get("/{entity_type}/{entity_id}/about")
def record_about(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
    access: Access = Depends(get_access),
) -> dict:
    """About this record: everything about it - fields, related records, its
    lines / payments / feasibility / channels / approvals, and its timeline."""
    from app.crm_core import record_about as about_service

    normalized = lifecycle_service.normalize_entity_type(entity_type)
    obj = lifecycle_service.get_record(db, tenant_id, normalized, entity_id) if normalized else None
    if obj is None or not access_service.can_see(db, access, normalized, obj.id):
        raise HTTPException(404, "Record not found")
    out = about_service.about(db, tenant_id, access, user, normalized, obj)
    db.rollback()  # read-only: nothing a view filled in on the way is kept
    return out


@router.get("/{entity_type}/{entity_id}", response_model=RecordLifecycleOut)
def record_lifecycle(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
    access: Access = Depends(get_access),
) -> RecordLifecycleOut:
    normalized = lifecycle_service.normalize_entity_type(entity_type)
    obj = lifecycle_service.get_record(db, tenant_id, normalized, entity_id) if normalized else None
    if obj is None or not access_service.can_see(db, access, normalized, obj.id):
        raise HTTPException(404, "Record not found")
    entity_type = normalized

    meta = lifecycle_service.get_meta(db, tenant_id, entity_type, obj.id, create=False)
    model = lifecycle_service.resolve_state_model(db, tenant_id, entity_type)
    state = lifecycle_service.effective_state(db, tenant_id, entity_type, obj, meta)
    definition = lifecycle_service.state_def(model, state)
    snapshot = record_service.snapshot(db, tenant_id, entity_type, obj)

    definitions = custom_field_service.list_definitions(db, tenant_id, entity_type)
    custom_values = custom_field_service.get_values(db, tenant_id, entity_type, obj.id)
    custom_fields = [
        {
            "key": d.key,
            "label": d.label,
            "data_type": d.data_type,
            "allowed_values": custom_field_service.allowed_values(d),
            "required": d.required,
            "value": custom_values.get(d.key),
        }
        for d in definitions
    ]

    users = {u.id: u for u in db.query(User).filter(User.tenant_id == tenant_id).all()}
    positions = {p.id: p for p in db.query(Position).filter(Position.tenant_id == tenant_id).all()}

    from app.models.org import Department

    departments = {d.id: d for d in db.query(Department).filter(Department.tenant_id == tenant_id).all()}

    def who(user_id: int | None, position_id: int | None, department_id: int | None = None) -> str:
        parts = []
        if user_id in users:
            parts.append(users[user_id].name)
        if position_id in positions:
            parts.append(f"[{positions[position_id].name}]")
        if department_id in departments:
            parts.append(f"{{{departments[department_id].name} department}}")
        return " ".join(parts) or "unassigned"

    history = [
        {
            "kind": "state",
            "summary": f"{h.from_state or '(new)'} → {h.to_state}",
            "source": h.source,
            "created_at": h.created_at,
        }
        for h in db.query(EntityStateHistory).filter(
            EntityStateHistory.tenant_id == tenant_id,
            EntityStateHistory.entity_type == entity_type,
            EntityStateHistory.entity_id == obj.id,
        )
    ] + [
        {
            "kind": "assignment",
            "summary": f"{who(h.from_user_id, h.from_position_id, h.from_department_id)} → "
            f"{who(h.to_user_id, h.to_position_id, h.to_department_id)}"
            + (f" ({h.reason})" if h.reason else ""),
            "source": h.source,
            "created_at": h.created_at,
        }
        for h in db.query(EntityAssignmentHistory).filter(
            EntityAssignmentHistory.tenant_id == tenant_id,
            EntityAssignmentHistory.entity_type == entity_type,
            EntityAssignmentHistory.entity_id == obj.id,
        )
    ]
    history.sort(key=lambda h: h["created_at"], reverse=True)

    revisions: list[dict] = []
    if entity_type == "quote":
        siblings = {q.id: q for q in db.query(Quote).filter(Quote.tenant_id == tenant_id, Quote.opportunity_id == obj.opportunity_id)}

        def root(q: Quote) -> int:
            while q.revision_of_quote_id in siblings:
                q = siblings[q.revision_of_quote_id]
            return q.id

        chain_root = root(obj)
        revisions = [
            {"id": q.id, "revision_number": q.revision_number, "status": q.status.value, "total_price": q.total_price}
            for q in sorted(siblings.values(), key=lambda q: q.revision_number)
            if root(q) == chain_root
        ]

    core_keys = [k for k in snapshot if k not in custom_values and k not in ("state", "built_in_status", "owner_user", "owner_position")]
    return RecordLifecycleOut(
        entity_type=entity_type,
        entity_id=obj.id,
        title=_title(db, entity_type, obj),
        state=state,
        state_label=definition.label,
        built_in_status=snapshot["built_in_status"],
        editable=definition.editable,
        is_terminal=definition.is_terminal,
        state_model_name=model.name,
        available_actions=lifecycle_service.available_transitions(db, tenant_id, entity_type, obj, user.role.value, user.id),
        actions=record_actions.for_record(db, tenant_id, user, entity_type, obj),
        approval=approval_service.waiting_on(db, tenant_id, entity_type, obj.id, user.id),
        owner_user=_ref(db, User, meta.owner_user_id if meta else None, ("id", "name", "email")),
        owner_position=_ref(db, Position, meta.owner_position_id if meta else None, ("id", "name")),
        owner_department=_ref(db, Department, meta.owner_department_id if meta else None, ("id", "name")),
        core_fields={k: snapshot[k] for k in core_keys},
        updatable_core_fields=list(lifecycle_service.UPDATABLE_CORE_FIELDS[entity_type]),
        core_field_labels={k: v for k, v in record_service.CORE_FIELD_LABELS.items() if k in snapshot},
        core_field_options=record_service.core_field_options(db, tenant_id, entity_type),
        custom_fields=custom_fields,
        history=history,
        revisions=revisions,
        brt_id=getattr(obj, "brt_id", None),
        sys_created_at=getattr(obj, "sys_created_at", None),
        sys_created_by=_ref(db, User, getattr(obj, "sys_created_by", None), ("id", "name", "email")),
    )
