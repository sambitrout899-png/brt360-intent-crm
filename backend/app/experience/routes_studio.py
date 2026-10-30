"""Phase 7 admin configuration: entity state models, positions,
pre-configured assignment rules, custom fields, and the AI Flow Studio.
Admin-only and tenant-scoped, same conventions as routes_admin.py.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import access_service, lifecycle_service
from app.data_intelligence import audit
from app.db import get_db
from app.intent_engine.classifier import INTENT_LABELS as TELECOM_INTENT_LABELS
from app.crm_core.training_service import INTENTS as TRAINING_INTENTS
INTENT_LABELS = {**TELECOM_INTENT_LABELS, **{i["code"]: i["label"] for i in TRAINING_INTENTS}}
from app.models.auth import User, UserRole
from app.models.custom_fields import CUSTOM_FIELD_DATA_TYPES, CustomFieldDefinition, CustomFieldValue
from app.models.flows import FLOW_ERROR_POLICIES, FLOW_TIMINGS, FLOW_TRIGGER_TYPES, FlowDefinition
from app.models.lifecycle import (
    AssignmentRule,
    Position,
    PositionMember,
    StateModel,
    StateModelState,
    StateModelTransition,
)
from app.models.change_control import Workspace
from app.models.org import AppRole, Department
from app.orchestration import change_control, flow_ai, flow_engine, flow_errors, flow_schema
from app.schemas.studio import (
    AssignmentRuleIn,
    AssignmentRuleOut,
    CustomFieldIn,
    CustomFieldOut,
    FlowDecisionIn,
    FlowGenerateIn,
    FlowGenerateOut,
    FlowIn,
    FlowOut,
    FlowReviewIn,
    FlowReviewOut,
    FlowSimulateIn,
    FlowSimulateOut,
    PositionIn,
    PositionMembersIn,
    PositionOut,
    PositionUpdate,
    StateModelIn,
    StateModelOut,
)
from app.tenancy import get_current_tenant_id
from app.licensing import entity_available, require_entity, intent_available, flow_available

router = APIRouter(prefix="/api/admin", tags=["admin-studio"], dependencies=[Depends(require_roles(UserRole.admin))])


def _entity_type(raw: str | None, allow_none: bool = False) -> str | None:
    if raw in (None, "") and allow_none:
        return None
    entity_type = lifecycle_service.normalize_entity_type(raw)
    if entity_type is None:
        raise HTTPException(400, f"entity_type must be one of: {', '.join(lifecycle_service.ENTITY_MODELS)}")
    return entity_type


# ---------- state models ----------


def _state_model_out(db: Session, tenant_id: int, entity_type: str) -> StateModelOut:
    model = lifecycle_service.resolve_state_model(db, tenant_id, entity_type)
    return StateModelOut(
        entity_type=entity_type,
        name=model.name,
        configured=model.configured,
        built_in_states=lifecycle_service.BUILT_IN_STATES[entity_type],
        states=[
            {"code": s.code, "label": s.label, "editable": s.editable, "is_terminal": s.is_terminal, "built_in": s.built_in}
            for s in model.states.values()
        ],
        transitions=[
            {
                "from_state": t.from_state,
                "to_state": t.to_state,
                "action": t.action,
                "intent_type": t.intent_type,
                "allowed_roles": t.allowed_roles,
            }
            for t in model.transitions
        ],
    )


@router.get("/state-models", response_model=list[StateModelOut])
def list_state_models(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[StateModelOut]:
    return [_state_model_out(db, tenant_id, entity_type) for entity_type in lifecycle_service.ENTITY_MODELS]


@router.put("/state-models/{entity_type}", response_model=StateModelOut)
def save_state_model(
    entity_type: str, payload: StateModelIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> StateModelOut:
    entity_type = _entity_type(entity_type)
    built_ins = lifecycle_service.BUILT_IN_STATES[entity_type]
    codes = [s.code for s in payload.states]
    if len(set(codes)) != len(codes):
        raise HTTPException(400, "State codes must be unique")
    missing = [b for b in built_ins if b not in codes]
    if missing:
        raise HTTPException(
            400, f"Built-in {entity_type} states cannot be removed (they are what the core intents set): {', '.join(missing)}"
        )
    # allowed roles: the built-in levels or the tenant's own roles (stored by name, canonical case)
    known_roles = {r: r for r in ("admin", "agent", "viewer")}
    known_roles.update({r.name.strip().lower(): r.name.strip() for r in db.query(AppRole).filter(AppRole.tenant_id == tenant_id).all()})
    for t in payload.transitions:
        picked = [r.strip() for r in t.allowed_roles.split(",") if r.strip()]
        bad_roles = {r for r in picked if r.lower() not in known_roles}
        if bad_roles:
            raise HTTPException(400, f"Unknown role(s) on transition '{t.action}': {', '.join(sorted(bad_roles))}")
        t.allowed_roles = ",".join(dict.fromkeys(known_roles[r.lower()] for r in picked))
        if t.from_state != "*" and t.from_state not in codes:
            raise HTTPException(400, f"Transition '{t.action}' starts from unknown state '{t.from_state}'")
        if t.to_state not in codes:
            raise HTTPException(400, f"Transition '{t.action}' targets unknown state '{t.to_state}'")
        if t.intent_type and t.intent_type not in INTENT_LABELS:
            raise HTTPException(400, f"Transition '{t.action}' is bound to unknown intent '{t.intent_type}'")

    for old in db.query(StateModel).filter(StateModel.tenant_id == tenant_id, StateModel.entity_type == entity_type).all():
        db.delete(old)
    db.flush()
    model = StateModel(tenant_id=tenant_id, entity_type=entity_type, name=payload.name)
    db.add(model)
    db.flush()
    for i, s in enumerate(payload.states):
        db.add(
            StateModelState(
                state_model_id=model.id,
                code=s.code,
                label=s.label or s.code.replace("_", " ").title(),
                sequence=i,
                editable=s.editable,
                is_terminal=s.is_terminal,
                built_in=s.code in built_ins,
            )
        )
    for t in payload.transitions:
        db.add(
            StateModelTransition(
                state_model_id=model.id,
                from_state=t.from_state,
                to_state=t.to_state,
                action=t.action,
                intent_type=t.intent_type or None,
                allowed_roles=t.allowed_roles,
            )
        )
    db.commit()
    return _state_model_out(db, tenant_id, entity_type)


@router.delete("/state-models/{entity_type}", response_model=StateModelOut)
def reset_state_model(entity_type: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> StateModelOut:
    """Back to the built-in default for this entity type."""
    entity_type = _entity_type(entity_type)
    for old in db.query(StateModel).filter(StateModel.tenant_id == tenant_id, StateModel.entity_type == entity_type).all():
        db.delete(old)
    db.commit()
    return _state_model_out(db, tenant_id, entity_type)


# ---------- positions ----------


def _position_out(db: Session, position: Position) -> PositionOut:
    members = []
    for m in position.members:
        user = db.get(User, m.user_id)
        if user is not None:
            members.append({"id": user.id, "name": user.name, "email": user.email, "is_primary": m.is_primary})
    return PositionOut(
        id=position.id,
        name=position.name,
        parent_position_id=position.parent_position_id,
        department_id=position.department_id,
        data_scope=position.data_scope,
        is_active=position.is_active,
        members=members,
    )


def _check_position_fields(db: Session, tenant_id: int, payload, position_id: int | None = None) -> None:
    if payload.data_scope not in access_service.SCOPE_KEYS:
        raise HTTPException(400, f"data_scope must be one of: {', '.join(access_service.SCOPE_KEYS)}")
    if payload.department_id is not None:
        department = db.get(Department, payload.department_id)
        if department is None or department.tenant_id != tenant_id:
            raise HTTPException(400, "Department not found")
    if payload.parent_position_id is not None:
        _owned_position(db, tenant_id, payload.parent_position_id)
        # descendant_positions includes the position itself
        if position_id is not None and payload.parent_position_id in access_service.descendant_positions(db, tenant_id, position_id):
            raise HTTPException(400, "A position cannot report to itself or to one of its own reports")


def _owned_position(db: Session, tenant_id: int, position_id: int) -> Position:
    position = db.get(Position, position_id)
    if position is None or position.tenant_id != tenant_id:
        raise HTTPException(404, "Position not found")
    return position


def _set_members(db: Session, tenant_id: int, position: Position, user_ids: list[int]) -> None:
    for user_id in user_ids:
        user = db.get(User, user_id)
        if user is None or user.tenant_id != tenant_id:
            raise HTTPException(400, f"User {user_id} not found in this tenant")
    primaries = {m.user_id for m in position.members if m.is_primary}
    position.members.clear()
    db.flush()
    for user_id in dict.fromkeys(user_ids):
        position.members.append(PositionMember(user_id=user_id, is_primary=user_id in primaries))


@router.get("/positions", response_model=list[PositionOut])
def list_positions(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[PositionOut]:
    positions = db.query(Position).filter(Position.tenant_id == tenant_id).order_by(Position.name).all()
    return [_position_out(db, p) for p in positions]


@router.post("/positions", response_model=PositionOut, status_code=201)
def create_position(payload: PositionIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> PositionOut:
    if db.query(Position).filter(Position.tenant_id == tenant_id, Position.name == payload.name).first():
        raise HTTPException(400, f"Position '{payload.name}' already exists")
    _check_position_fields(db, tenant_id, payload)
    position = Position(
        tenant_id=tenant_id,
        name=payload.name,
        parent_position_id=payload.parent_position_id,
        department_id=payload.department_id,
        data_scope=payload.data_scope,
    )
    db.add(position)
    db.flush()
    _set_members(db, tenant_id, position, payload.user_ids)
    db.commit()
    db.refresh(position)
    return _position_out(db, position)


@router.put("/positions/{position_id}", response_model=PositionOut)
def update_position(
    position_id: int, payload: PositionUpdate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> PositionOut:
    position = _owned_position(db, tenant_id, position_id)
    clash = db.query(Position).filter(Position.tenant_id == tenant_id, Position.name == payload.name, Position.id != position_id).first()
    if clash:
        raise HTTPException(400, f"Position '{payload.name}' already exists")
    _check_position_fields(db, tenant_id, payload, position_id)
    position.name = payload.name
    position.parent_position_id = payload.parent_position_id
    position.department_id = payload.department_id
    position.data_scope = payload.data_scope
    db.commit()
    db.refresh(position)
    return _position_out(db, position)


@router.put("/positions/{position_id}/members", response_model=PositionOut)
def set_position_members(
    position_id: int, payload: PositionMembersIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> PositionOut:
    position = _owned_position(db, tenant_id, position_id)
    _set_members(db, tenant_id, position, payload.user_ids)
    db.commit()
    db.refresh(position)
    return _position_out(db, position)


@router.delete("/positions/{position_id}", status_code=204)
def delete_position(position_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    position = _owned_position(db, tenant_id, position_id)
    if db.query(AssignmentRule).filter(AssignmentRule.position_id == position.id).first():
        raise HTTPException(400, "Position is used by an assignment rule - remove the rule first")
    # Records still owned by it keep the id in their history; the live
    # owner pointer is deactivated rather than dangling.
    position.is_active = False
    position.members.clear()
    db.commit()


# ---------- assignment rules ----------


@router.get("/assignment-rules", response_model=list[AssignmentRuleOut])
def list_assignment_rules(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[AssignmentRule]:
    return (
        db.query(AssignmentRule)
        .filter(AssignmentRule.tenant_id == tenant_id)
        .order_by(AssignmentRule.entity_type, AssignmentRule.priority, AssignmentRule.id)
        .all()
    )


@router.post("/assignment-rules", response_model=AssignmentRuleOut, status_code=201)
def create_assignment_rule(
    payload: AssignmentRuleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> AssignmentRule:
    entity_type = _entity_type(payload.entity_type)
    if payload.strategy not in ("position", "round_robin", "user", "department"):
        raise HTTPException(400, "strategy must be 'position', 'round_robin', 'user' or 'department'")
    if payload.strategy == "user":
        user = db.get(User, payload.user_id) if payload.user_id else None
        if user is None or user.tenant_id != tenant_id:
            raise HTTPException(400, "strategy 'user' needs a user_id from this tenant")
    elif payload.strategy == "department":
        from app.models.org import Department

        department = db.get(Department, payload.department_id) if payload.department_id else None
        if department is None or department.tenant_id != tenant_id:
            raise HTTPException(400, "strategy 'department' needs a department_id from this tenant")
    else:
        if payload.position_id is None:
            raise HTTPException(400, f"strategy '{payload.strategy}' needs a position_id")
        _owned_position(db, tenant_id, payload.position_id)
    if payload.on_state:
        model = lifecycle_service.resolve_state_model(db, tenant_id, entity_type)
        if payload.on_state not in model.states:
            raise HTTPException(400, f"'{payload.on_state}' is not a state in the {entity_type} state model")
    rule = AssignmentRule(
        tenant_id=tenant_id,
        name=payload.name,
        entity_type=entity_type,
        on_state=payload.on_state or None,
        strategy=payload.strategy,
        position_id=payload.position_id if payload.strategy not in ("user", "department") else None,
        user_id=payload.user_id if payload.strategy == "user" else None,
        department_id=payload.department_id if payload.strategy == "department" else None,
        priority=payload.priority,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def _owned_rule(db: Session, tenant_id: int, rule_id: int) -> AssignmentRule:
    rule = db.get(AssignmentRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise HTTPException(404, "Assignment rule not found")
    return rule


@router.patch("/assignment-rules/{rule_id}/deactivate", response_model=AssignmentRuleOut)
def deactivate_assignment_rule(rule_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> AssignmentRule:
    rule = _owned_rule(db, tenant_id, rule_id)
    rule.is_active = False
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/assignment-rules/{rule_id}", status_code=204)
def delete_assignment_rule(rule_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    db.delete(_owned_rule(db, tenant_id, rule_id))
    db.commit()


# ---------- custom fields ----------


@router.get("/custom-fields", response_model=list[CustomFieldOut])
def list_custom_fields(
    entity_type: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[CustomFieldDefinition]:
    query = db.query(CustomFieldDefinition).filter(CustomFieldDefinition.tenant_id == tenant_id)
    if entity_type:
        query = query.filter(CustomFieldDefinition.entity_type == _field_entity_type(db, tenant_id, entity_type))
    return query.order_by(CustomFieldDefinition.entity_type, CustomFieldDefinition.id).all()


def _field_entity_type(db: Session, tenant_id: int, raw: str | None) -> str:
    """What custom fields can sit on: a built-in record, a training program, or one of the tenant's own record types ("x_<key>")."""
    from app.crm_core import custom_record_service

    if raw == "training_program":
        return raw
    if custom_record_service.is_custom(raw):
        rt = custom_record_service.get_type(db, tenant_id, raw)
        if rt is None:
            raise HTTPException(400, f"No record type '{raw}' - create it in Admin -> Record Types first")
        return rt.entity_type
    return _entity_type(raw)


@router.post("/custom-fields", response_model=CustomFieldOut, status_code=201)
def create_custom_field(
    payload: CustomFieldIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> CustomFieldDefinition:
    from app.crm_core import custom_record_service

    entity_type = _field_entity_type(db, tenant_id, payload.entity_type)
    if payload.data_type not in CUSTOM_FIELD_DATA_TYPES:
        raise HTTPException(400, f"data_type must be one of: {', '.join(CUSTOM_FIELD_DATA_TYPES)}")
    if custom_record_service.is_custom(entity_type) and payload.key in custom_record_service.RESERVED_FIELD_KEYS:
        raise HTTPException(400, f"'{payload.key}' is a field every record has - choose another key")
    if payload.key in lifecycle_service.UPDATABLE_CORE_FIELDS.get(entity_type, {}) or payload.key in ("state", "id", "entity_id", "entity_type"):
        raise HTTPException(400, f"'{payload.key}' is a built-in {entity_type} field name - choose another key")
    if payload.data_type == "picklist" and not (payload.allowed_values or "").strip():
        raise HTTPException(400, "A picklist field needs allowed_values (comma-separated)")
    if db.query(CustomFieldDefinition).filter(
        CustomFieldDefinition.tenant_id == tenant_id,
        CustomFieldDefinition.entity_type == entity_type,
        CustomFieldDefinition.key == payload.key,
    ).first():
        raise HTTPException(400, f"A {entity_type} field with key '{payload.key}' already exists")
    defn = CustomFieldDefinition(
        tenant_id=tenant_id,
        entity_type=entity_type,
        key=payload.key,
        label=payload.label,
        data_type=payload.data_type,
        allowed_values=payload.allowed_values if payload.data_type == "picklist" else None,
        required=payload.required,
        default_value=None,
    )
    if payload.default_value not in (None, ""):
        from app.crm_core import custom_field_service

        try:
            defn.default_value = custom_field_service.coerce(defn, payload.default_value)
        except ValueError as exc:
            raise HTTPException(400, f"Invalid default value: {exc}")
    db.add(defn)
    db.commit()
    db.refresh(defn)
    return defn


def _owned_field(db: Session, tenant_id: int, field_id: int) -> CustomFieldDefinition:
    defn = db.get(CustomFieldDefinition, field_id)
    if defn is None or defn.tenant_id != tenant_id:
        raise HTTPException(404, "Custom field not found")
    return defn


@router.patch("/custom-fields/{field_id}/deactivate", response_model=CustomFieldOut)
def deactivate_custom_field(field_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> CustomFieldDefinition:
    defn = _owned_field(db, tenant_id, field_id)
    defn.is_active = False
    db.commit()
    db.refresh(defn)
    return defn


@router.delete("/custom-fields/{field_id}", status_code=204)
def delete_custom_field(field_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    defn = _owned_field(db, tenant_id, field_id)
    db.query(CustomFieldValue).filter(CustomFieldValue.field_id == defn.id).delete()
    db.delete(defn)
    db.commit()


# ---------- AI Flow Studio ----------
#
# In a workspace (X-BRT-Workspace) a flow's changes are drafts of it there
# (orchestration/change_control.py) - live is untouched until a release; a
# flow made in a workspace shows with a negative id. Outside one the flow is
# changed live - unless someone's workspace holds it.


from app.experience.routes_change import current_workspace as workspace  # noqa: E402 - the X-BRT-Workspace dependency


def _guard(fn, *args):
    try:
        return fn(*args)
    except change_control.ChangeError as exc:
        raise HTTPException(409, str(exc)) from exc


def _flow_out(db: Session, flow: FlowDefinition, corrections: list[dict] | None = None, draft: bool = False) -> FlowOut:
    graph = flow_engine.load_graph(flow)
    review = flow_schema.review_graph(graph, flow_schema.catalog_for_flow(db, flow.tenant_id, flow), flow.trigger_type == "custom_intent")
    lock = change_control.lock_out(db, change_control.lock_of(db, flow.tenant_id, "flow", change_control.key_of(flow.id)))
    return FlowOut(
        draft=draft,
        locked_by=lock,
        id=flow.id,
        name=flow.name,
        description=flow.description,
        entity_type=flow.entity_type,
        trigger_type=flow.trigger_type,
        trigger_intent_type=flow.trigger_intent_type,
        timing=flow.timing,
        trigger_state=flow.trigger_state,
        trigger_phrases=flow.trigger_phrases,
        required_fields=flow.required_fields,
        graph=graph,
        on_error=flow.on_error or "fail",
        version=flow.version,
        is_active=flow.is_active,
        intent_code=flow_engine.custom_intent_code(flow) if flow.trigger_type == "custom_intent" else None,
        validation_errors=flow_engine.validate_graph(graph, flow),
        warnings=[f"{i['node']}: {i['message']}" for i in review["issues"]],
        corrections=corrections or [],
        updated_at=flow.updated_at,
    )


def _owned_flow(db: Session, tenant_id: int, flow_id: int) -> FlowDefinition:
    flow = db.get(FlowDefinition, flow_id)
    if flow is None or flow.tenant_id != tenant_id:
        raise HTTPException(404, "Flow not found")
    if not flow_available(db, tenant_id, flow):
        raise HTTPException(404, "Flow not found in the selected modules")
    return flow


def _apply_flow_payload(db: Session, tenant_id: int, flow: FlowDefinition, payload: FlowIn) -> list[dict]:
    if payload.trigger_type not in FLOW_TRIGGER_TYPES:
        raise HTTPException(400, f"trigger_type must be one of: {', '.join(FLOW_TRIGGER_TYPES)}")
    if payload.timing not in FLOW_TIMINGS:
        raise HTTPException(400, f"timing must be one of: {', '.join(FLOW_TIMINGS)}")
    if payload.on_error not in FLOW_ERROR_POLICIES:
        raise HTTPException(400, f"on_error must be one of: {', '.join(FLOW_ERROR_POLICIES)}")
    entity_type = _entity_type(payload.entity_type, allow_none=True)
    if payload.trigger_type == "intent":
        if payload.trigger_intent_type not in INTENT_LABELS:
            raise HTTPException(400, "An intent-triggered flow needs trigger_intent_type set to a built-in intent (e.g. INT-05)")
    if payload.trigger_type == "state_enter":
        if entity_type is None or not payload.trigger_state:
            raise HTTPException(400, "A state_enter flow needs entity_type and trigger_state")
        if payload.trigger_state not in lifecycle_service.resolve_state_model(db, tenant_id, entity_type).states:
            raise HTTPException(400, f"'{payload.trigger_state}' is not a state in the {entity_type} state model")
    if payload.trigger_type == "custom_intent" and not (payload.trigger_phrases or "").strip():
        raise HTTPException(400, "A custom intent flow needs at least one trigger phrase")
    require_entity(db, tenant_id, entity_type)
    if payload.trigger_intent_type and not intent_available(db, tenant_id, payload.trigger_intent_type):
        raise HTTPException(403, "Intent is not available in the selected modules")
    flow.name = payload.name
    flow.description = payload.description
    flow.entity_type = entity_type
    flow.trigger_type = payload.trigger_type
    flow.trigger_intent_type = payload.trigger_intent_type if payload.trigger_type == "intent" else None
    flow.timing = payload.timing if payload.trigger_type == "intent" else "after"
    flow.trigger_state = payload.trigger_state if payload.trigger_type == "state_enter" else None
    flow.trigger_phrases = payload.trigger_phrases if payload.trigger_type == "custom_intent" else None
    flow.required_fields = payload.required_fields if payload.trigger_type == "custom_intent" else None
    # an "error" flow runs after the failed intent was undone - there is nothing left for it to fail
    flow.on_error = "fail" if flow.timing == "error" else payload.on_error
    # Unambiguous fixes (field/label/case/typo, picklist values, states,
    # positions, users) are applied on save and reported back.
    review = flow_schema.review_graph(payload.graph, flow_schema.catalog_for_flow(db, tenant_id, flow), flow.trigger_type == "custom_intent")
    flow.graph_json = json.dumps(review["graph"])
    if not flow_available(db, tenant_id, flow):
        raise HTTPException(403, "Flow references a module that is not selected")
    return review["corrections"]


@router.get("/flows/catalog")
def flow_catalog(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """Everything the Flow Studio palette and trigger pickers need."""
    from app.orchestration import flow_records
    return {
        "record_entities": flow_records.catalog(db, tenant_id),
        "node_types": [{"type": k, **v} for k, v in flow_engine.NODE_TYPES.items()],
        "operators": list(flow_engine.OPERATORS),
        "entity_types": [e for e in lifecycle_service.ENTITY_MODELS if entity_available(db, tenant_id, e)],
        "trigger_types": list(FLOW_TRIGGER_TYPES),
        "timings": list(FLOW_TIMINGS),
        "error_policies": list(FLOW_ERROR_POLICIES),
        # what a Catch errors step can be limited to: each error code, then each category
        "error_codes": flow_errors.catalog(),
        "intents": [{"code": k, "label": v} for k, v in INTENT_LABELS.items() if intent_available(db, tenant_id, k)],
    }


def _entity_or_none(raw: str | None) -> str | None:
    return _entity_type(raw, allow_none=True) if raw else None


@router.get("/flows/field-catalog")
def flow_field_catalog(
    entity_type: str | None = None,
    trigger_type: str = "intent",
    trigger_intent_type: str | None = None,
    required_fields: str | None = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> dict:
    """Every field, picklist value and option a flow for this record type +
    trigger can reference - drives the Studio's autocomplete."""
    return flow_schema.field_catalog(
        db, tenant_id, _entity_or_none(entity_type),
        trigger_intent_type if trigger_type == "intent" else None,
        required_fields if trigger_type == "custom_intent" else None,
    )


@router.post("/flows/review", response_model=FlowReviewOut)
def review_flow(payload: FlowReviewIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> FlowReviewOut:
    """Live check while designing: fixes it is sure of (returned applied in
    `graph`), suggestions it is not, and references it cannot place."""
    catalog = flow_schema.field_catalog(
        db, tenant_id, _entity_or_none(payload.entity_type),
        payload.trigger_intent_type if payload.trigger_type == "intent" else None,
        payload.required_fields if payload.trigger_type == "custom_intent" else None,
    )
    return FlowReviewOut(**flow_schema.review_graph(payload.graph, catalog, payload.trigger_type == "custom_intent"))


def _editable(db: Session, tenant_id: int, ws: Workspace | None, flow_id: int) -> tuple[FlowDefinition, bool]:
    """The flow as the caller works on it: their workspace's draft, else live.
    In a workspace a live flow comes back as a copy - never the live row."""
    key = change_control.key_of(flow_id)
    snap = change_control.draft(db, tenant_id, ws, "flow", key)
    if snap is not None:
        if snap.get("deleted"):
            raise HTTPException(404, "Flow deleted in this workspace")
        return change_control.FlowAdapter.materialize(tenant_id, key, snap), True
    if flow_id < 0:
        raise HTTPException(404, "Flow not found")
    live = _owned_flow(db, tenant_id, flow_id)
    if ws is None:
        return live, False
    return change_control.FlowAdapter.materialize(tenant_id, key, change_control.FlowAdapter.snapshot(live)), True


def _save_draft(db: Session, tenant_id: int, user: User, ws: Workspace, flow: FlowDefinition, key: str | None,
                corrections: list[dict] | None = None) -> FlowOut:
    snap = change_control.FlowAdapter.snapshot(flow)
    key = _guard(change_control.save, db, tenant_id, user, ws, "flow", key, snap, flow.name)
    shown = change_control.FlowAdapter.materialize(tenant_id, key, snap)
    return _flow_out(db, shown, corrections, draft=True)


def _check_live(db: Session, tenant_id: int, user: User, flow: FlowDefinition) -> None:
    _guard(change_control.check_free, db, tenant_id, "flow", str(flow.id), user, None, f"Flow “{flow.name}”")


@router.get("/flows", response_model=list[FlowOut])
def list_flows(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
               ws: Workspace | None = Depends(workspace)) -> list[FlowOut]:
    flows = db.query(FlowDefinition).filter(FlowDefinition.tenant_id == tenant_id).order_by(FlowDefinition.name).all()
    drafts = change_control.drafts(db, tenant_id, ws, "flow")
    out = []
    for f in flows:
        snap = drafts.pop(str(f.id), None)
        if snap is not None and not snap.get("deleted"):
            out.append(_flow_out(db, change_control.FlowAdapter.materialize(tenant_id, str(f.id), snap), draft=True))
        elif snap is None and flow_available(db, tenant_id, f):
            out.append(_flow_out(db, f))
    out += [_flow_out(db, change_control.FlowAdapter.materialize(tenant_id, key, snap), draft=True)
            for key, snap in drafts.items() if key.startswith("new-") and not snap.get("deleted")]
    return sorted(out, key=lambda f: f.name.lower())


@router.get("/flows/{flow_id}", response_model=FlowOut)
def get_flow(flow_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             ws: Workspace | None = Depends(workspace)) -> FlowOut:
    flow, draft = _editable(db, tenant_id, ws, flow_id)
    return _flow_out(db, flow, draft=draft)


@router.get("/flows/{flow_id}/history")
def flow_history(flow_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """Every revision of the flow made in workspaces, and who holds it now."""
    key = change_control.key_of(flow_id)
    return {"revisions": change_control.history(db, tenant_id, "flow", key),
            "locked_by": change_control.lock_out(db, change_control.lock_of(db, tenant_id, "flow", key))}


@router.post("/flows", response_model=FlowOut, status_code=201)
def create_flow(payload: FlowIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                ws: Workspace | None = Depends(workspace), user: User = Depends(get_current_user)) -> FlowOut:
    flow = FlowDefinition(tenant_id=tenant_id, is_active=False)
    corrections = _apply_flow_payload(db, tenant_id, flow, payload)
    if ws is not None:
        flow.version = 1
        return _save_draft(db, tenant_id, user, ws, flow, None, corrections)
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return _flow_out(db, flow, corrections)


@router.put("/flows/{flow_id}", response_model=FlowOut)
def update_flow(flow_id: int, payload: FlowIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                ws: Workspace | None = Depends(workspace), user: User = Depends(get_current_user)) -> FlowOut:
    """Saving a draft is always allowed; an ACTIVE flow must stay valid -
    an edit that breaks it is rejected rather than silently deactivating."""
    flow, _draft = _editable(db, tenant_id, ws, flow_id)
    if ws is None:
        _check_live(db, tenant_id, user, flow)
    corrections = _apply_flow_payload(db, tenant_id, flow, payload)
    if flow.is_active:
        errors = flow_engine.validate_graph(flow_engine.load_graph(flow), flow)
        if errors:
            raise HTTPException(400, f"Active flow would become invalid: {'; '.join(errors)} - deactivate it first to save a draft")
    flow.version = (flow.version or 0) + 1
    if ws is not None:
        return _save_draft(db, tenant_id, user, ws, flow, change_control.key_of(flow_id), corrections)
    db.commit()
    db.refresh(flow)
    return _flow_out(db, flow, corrections)


def _switch(db: Session, tenant_id: int, ws: Workspace | None, user: User, flow_id: int, on: bool) -> FlowOut:
    flow, _draft = _editable(db, tenant_id, ws, flow_id)
    if ws is None:
        _check_live(db, tenant_id, user, flow)
    if on:
        errors = flow_engine.validate_graph(flow_engine.load_graph(flow), flow)
        if errors:
            raise HTTPException(400, f"Cannot activate an invalid flow: {'; '.join(errors)}")
    flow.is_active = on
    if ws is not None:
        return _save_draft(db, tenant_id, user, ws, flow, change_control.key_of(flow_id))
    db.commit()
    db.refresh(flow)
    return _flow_out(db, flow)


@router.post("/flows/{flow_id}/activate", response_model=FlowOut)
def activate_flow(flow_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                  ws: Workspace | None = Depends(workspace), user: User = Depends(get_current_user)) -> FlowOut:
    return _switch(db, tenant_id, ws, user, flow_id, True)


@router.post("/flows/{flow_id}/deactivate", response_model=FlowOut)
def deactivate_flow(flow_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                    ws: Workspace | None = Depends(workspace), user: User = Depends(get_current_user)) -> FlowOut:
    return _switch(db, tenant_id, ws, user, flow_id, False)


@router.delete("/flows/{flow_id}", status_code=204)
def delete_flow(flow_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                ws: Workspace | None = Depends(workspace), user: User = Depends(get_current_user)) -> None:
    flow, _draft = _editable(db, tenant_id, ws, flow_id)
    if ws is not None:
        _guard(change_control.save, db, tenant_id, user, ws, "flow", change_control.key_of(flow_id), {"deleted": True}, flow.name)
        return
    _check_live(db, tenant_id, user, flow)
    db.delete(flow)
    db.commit()


@router.post("/flows/ai-generate", response_model=FlowGenerateOut)
def ai_generate_flow(payload: FlowGenerateIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> FlowGenerateOut:
    entity_type = _entity_type(payload.entity_type, allow_none=True)
    # the record type the description itself names ("When the order ...") decides which fields the draft may use
    said, _ = flow_ai.detect_trigger(payload.prompt)
    if said and not entity_type:
        entity_type = said["entity_type"]
    catalog = flow_schema.field_catalog(
        db, tenant_id, entity_type,
        payload.trigger_intent_type if payload.trigger_type == "intent" else None,
        payload.required_fields if payload.trigger_type == "custom_intent" else None,
    )
    draft = flow_ai.generate(db, tenant_id, payload.prompt, entity_type, catalog)
    # The draft - LLM or rule parser - is checked against the real fields and
    # values like anything typed by hand, and the sure fixes applied.
    review = flow_schema.review_graph(draft["graph"], catalog, payload.trigger_type == "custom_intent")
    return FlowGenerateOut(
        graph=review["graph"], source=draft["source"], notes=draft["notes"], trigger=draft.get("trigger"),
        validation_errors=flow_engine.validate_graph(review["graph"]),
        corrections=review["corrections"], issues=review["issues"],
    )


@router.post("/flows/{flow_id}/simulate", response_model=FlowSimulateOut)
def simulate_flow(
    flow_id: int, payload: FlowSimulateIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
    ws: Workspace | None = Depends(workspace),
) -> FlowSimulateOut:
    """Dry run: executes the flow for real against a record, reports the
    trace, then rolls everything back - nothing it does is kept. In a
    workspace it is the workspace's draft of the flow that runs."""
    flow, _draft = _editable(db, tenant_id, ws, flow_id)
    log = audit.start_execution(db, tenant_id, f"[flow simulation] {flow.name}", channel="agent")
    ctx = {"entities": payload.entities, "result": payload.result,
           "trigger": {"intent": flow.trigger_intent_type, "input": payload.entities, "output": payload.result}}
    if flow.entity_type and payload.entity_id is not None:
        ctx.update(entity_type=flow.entity_type, entity_id=payload.entity_id)
    if flow.timing == "error":  # what the failed intent would hand over - editable as trigger inputs
        ctx["error"] = payload.result.get("error") if isinstance(payload.result.get("error"), dict) else {
            "code": "ACTION_FAILED", "category": "business", "message": "Simulated failure of the intent", "intent": flow.trigger_intent_type}
    try:
        run = flow_engine.run_flow(db, tenant_id, log.id, flow, ctx)
        return FlowSimulateOut(ok=True, error=None, run=run)
    except Exception as exc:  # noqa: BLE001 - surface any flow failure as the simulation result, with the trace up to it
        err = flow_errors.classify(exc, flow)
        return FlowSimulateOut(ok=False, error=str(err), run=err.run, error_detail=err.as_dict())
    finally:
        db.rollback()


# ---------- waiting flow runs (orchestration/flow_runs.py) ----------


@router.get("/flow-runs")
def list_flow_runs(status: str = "waiting", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict]:
    """Runs paused at a Wait or Wait for approval step - or, with status, how they ended."""
    from app.models.flows import FlowRun
    from app.orchestration import flow_runs

    runs = db.query(FlowRun).filter(FlowRun.tenant_id == tenant_id, FlowRun.status == status).order_by(FlowRun.id.desc()).limit(200).all()
    return [flow_runs.view(db, r) for r in runs]


@router.post("/flow-runs/{run_id}/cancel")
def cancel_flow_run(run_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                    user: User = Depends(get_current_user)) -> dict:
    from app.models.flows import FlowRun
    from app.orchestration import flow_runs

    run = db.get(FlowRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(404, "Flow run not found")
    try:
        flow_runs.cancel(db, run, user)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return flow_runs.view(db, run)


# Anyone signed in: the approval requests flows are waiting on them for (My Work).
approvals_router = APIRouter(prefix="/api/flow-approvals", tags=["flow-approvals"])


@approvals_router.get("")
def my_flow_approvals(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                      user: User = Depends(get_current_user)) -> list[dict]:
    from app.orchestration import flow_runs

    return [flow_runs.view(db, r) for r in flow_runs.approvals_for(db, tenant_id, user)]


@approvals_router.post("/{run_id}/decide")
def decide_flow_approval(run_id: int, payload: FlowDecisionIn, db: Session = Depends(get_db),
                         tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> dict:
    """Approve or reject: the flow continues at once, down that output."""
    from app.models.flows import FlowRun
    from app.orchestration import flow_runs

    run = db.get(FlowRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(404, "Approval request not found")
    try:
        flow_runs.decide(db, run, user, payload.approve, payload.note)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return flow_runs.view(db, run)
