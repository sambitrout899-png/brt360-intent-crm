"""Entity lifecycle engine (Phase 7) - see app/models/lifecycle.py for the
data model and the built-in-status vs custom-sub-state semantics.

Responsibilities:
- resolve a tenant's state model for an entity type (configured, else the
  code defaults below)
- answer "can this record still be saved/modified?" (INT-29) and "which
  lifecycle actions are available from here?" (INT-31, the UI's action
  buttons)
- perform generic transitions, refusing ones a core intent owns
- assign/transfer records to users or positions, manually or via the
  admin's pre-configured AssignmentRules
- detect state entry after any intent (sync_records) so assignment rules
  and state_enter flows run regardless of which intent moved the record
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import access_service
from app.models.auth import User, UserRole
from app.models.crm import Lead, LeadStatus, Opportunity, OpportunityStage, Party, Quote, QuoteStatus
from app.models.lifecycle import (
    AssignmentRule,
    EntityAssignmentHistory,
    EntityRecordMeta,
    EntityStateHistory,
    Position,
    PositionMember,
    StateModel,
)
from app.models.orders import Order, OrderStatus

ENTITY_MODELS: dict[str, type] = {
    "party": Party,
    "lead": Lead,
    "opportunity": Opportunity,
    "quote": Quote,
    "order": Order,
}

ENTITY_TYPE_ALIASES = {
    "customer": "party",
    "prospect": "party",
    "account": "party",
    "parties": "party",
    "leads": "lead",
    "opportunities": "opportunity",
    "opp": "opportunity",
    "deal": "opportunity",
    "quotes": "quote",
    "quotation": "quote",
    "orders": "order",
}

# Which attribute holds each entity's built-in status. Party has none -
# its built-in state is derived (see built_in_status).
_STATUS_ATTR = {"lead": "status", "opportunity": "stage", "quote": "status", "order": "status"}
_STATUS_ENUM = {"lead": LeadStatus, "opportunity": OpportunityStage, "quote": QuoteStatus, "order": OrderStatus}

BUILT_IN_STATES: dict[str, list[str]] = {
    "party": ["prospect", "account", "customer"],
    "lead": [s.value for s in LeadStatus],
    "opportunity": [s.value for s in OpportunityStage],
    "quote": [s.value for s in QuoteStatus],
    "order": [s.value for s in OrderStatus],
}

# Core columns INT-29 may change in place. Anything price-bearing is
# deliberately absent - those go through the intents that recompute
# totals (INT-19 quote revise, INT-21 order revise, INT-08 change).
UPDATABLE_CORE_FIELDS: dict[str, dict[str, type]] = {
    "party": {"name": str, "email": str, "phone": str, "party_type": str},
    "lead": {"source": str, "region": str, "interested_offering_name": str},
    "opportunity": {"name": str, "offering_name": str, "quantity": int},
    "quote": {"discount_justification": str},
    "order": {"contract_months": int},
}


def _s(code: str, editable: bool = True, terminal: bool = False, built_in: bool = True, label: str | None = None) -> dict:
    return {
        "code": code,
        "label": label or code.replace("_", " ").title(),
        "editable": editable,
        "is_terminal": terminal,
        "built_in": built_in,
    }


def _t(from_state: str, to_state: str, action: str, intent_type: str | None = None, allowed_roles: str = "") -> dict:
    return {"from_state": from_state, "to_state": to_state, "action": action, "intent_type": intent_type, "allowed_roles": allowed_roles}


DEFAULT_STATE_MODELS: dict[str, dict[str, list[dict]]] = {
    "party": {
        "states": [_s("prospect"), _s("account"), _s("customer"), _s("inactive", editable=False, built_in=False)],
        "transitions": [
            _t("prospect", "inactive", "deactivate"),
            _t("customer", "inactive", "deactivate"),
            _t("inactive", "prospect", "reactivate"),
            _t("inactive", "customer", "reactivate"),
            _t("prospect", "account", "convert_to_account", "INT-35"),
            _t("account", "inactive", "deactivate"),
            _t("inactive", "account", "reactivate"),
        ],
    },
    "lead": {
        "states": [
            _s("pending_qualification"),
            _s("qualified", editable=False),
            _s("rejected", editable=False, terminal=True),
        ],
        "transitions": [
            _t("pending_qualification", "qualified", "approve", "INT-17"),
            _t("pending_qualification", "rejected", "reject", "INT-17"),
            _t("rejected", "pending_qualification", "reopen"),
        ],
    },
    "opportunity": {
        "states": [
            _s("draft"),
            _s("qualified"),
            _s("quoted"),
            _s("won", editable=False, terminal=True),
            _s("lost", editable=False, terminal=True),
            _s("configured"),
            _s("in_feasibility", label="With Presales"),
            _s("feasibility_failed", label="Not feasible"),
            _s("feasible"),
        ],
        "transitions": [
            _t("draft", "qualified", "qualify"),
            _t("draft", "quoted", "quote", "INT-05"),
            _t("qualified", "quoted", "quote", "INT-05"),
            _t("quoted", "won", "close_won"),
            _t("*", "lost", "close_lost"),
            _t("lost", "draft", "reopen"),
            # B2B Sales-to-Order: configure -> Presales feasibility -> quote
            _t("draft", "configured", "configure", "INT-37"),
            _t("qualified", "configured", "configure", "INT-37"),
            _t("feasibility_failed", "configured", "reconfigure", "INT-37"),
            _t("configured", "in_feasibility", "send_to_presales", "INT-38"),
            _t("feasibility_failed", "in_feasibility", "send_to_presales", "INT-38"),
            _t("in_feasibility", "feasible", "pass_feasibility", "INT-18"),
            _t("in_feasibility", "feasibility_failed", "fail_feasibility", "INT-18"),
            _t("feasible", "quoted", "quote", "INT-05"),
        ],
    },
    "quote": {
        "states": [
            _s("draft"),
            _s("pending_approval"),
            _s("approved", editable=False),
            _s("rejected", editable=False),
            _s("cancelled", editable=False, terminal=True),
            _s("superseded", editable=False, terminal=True),
        ],
        "transitions": [
            _t("draft", "pending_approval", "submit", "INT-06"),
            _t("pending_approval", "approved", "approve", "INT-06"),
            _t("pending_approval", "rejected", "reject", "INT-06"),
            _t("draft", "cancelled", "cancel", "INT-20"),
            _t("pending_approval", "cancelled", "cancel", "INT-20"),
            _t("approved", "superseded", "revise", "INT-30"),
            _t("rejected", "superseded", "revise", "INT-30"),
        ],
    },
    "order": {
        "states": [
            _s("created"),
            _s("activating"),
            # asset-based ordering: a fulfilled order; later changes go through its assets (INT-107)
            _s("completed", editable=False),
            _s("active", editable=False),
            _s("changed", editable=False),
            _s("suspended", editable=False),
            _s("pending_cancellation", editable=False),
            _s("cancelled", editable=False, terminal=True),
            _s("terminated", editable=False, terminal=True),
            _s("pending_approval", editable=False, label="Awaiting discount approval"),
            _s("rejected", editable=False, terminal=True),
        ],
        "transitions": [
            _t("pending_approval", "active", "approve", "INT-36"),
            _t("pending_approval", "rejected", "reject", "INT-36"),
            _t("created", "active", "approve", "INT-25"),
            _t("active", "changed", "change", "INT-08"),
            _t("active", "suspended", "suspend", "INT-09"),
            _t("active", "pending_cancellation", "cancel", "INT-09"),
            _t("changed", "pending_cancellation", "cancel", "INT-09"),
            _t("*", "terminated", "terminate", "INT-09"),
        ],
    },
}


@dataclass
class StateDef:
    code: str
    label: str
    editable: bool = True
    is_terminal: bool = False
    built_in: bool = False


@dataclass
class TransitionDef:
    from_state: str
    to_state: str
    action: str
    intent_type: str | None = None
    allowed_roles: str = ""

    def allows_role(self, role: str | None, app_roles: frozenset[str] = frozenset()) -> bool:
        """role is the built-in level (admin/agent/viewer); app_roles are the
        lower-cased names of the tenant roles the user holds (e.g. "cpmo")."""
        roles = {r.strip() for r in (self.allowed_roles or "").split(",") if r.strip()}
        if not roles:
            return role in (None, UserRole.admin.value, UserRole.agent.value)
        return role in roles or role == UserRole.admin.value or bool({r.lower() for r in roles} & app_roles)


def actor_app_roles(db: Session, user_id: int | None) -> frozenset[str]:
    """Lower-cased names of the active tenant roles a user holds - matched
    against a transition's allowed roles alongside the built-in level."""
    if not user_id:
        return frozenset()
    user = db.get(User, user_id)
    return frozenset(r.name.strip().lower() for r in access_service.user_roles(db, user)) if user else frozenset()


@dataclass
class ResolvedStateModel:
    entity_type: str
    name: str
    configured: bool
    states: dict[str, StateDef] = field(default_factory=dict)
    transitions: list[TransitionDef] = field(default_factory=list)


def normalize_entity_type(raw: Any) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower().replace(" ", "_")
    value = ENTITY_TYPE_ALIASES.get(value, value)
    return value if value in ENTITY_MODELS else None


def resolve_state_model(db: Session, tenant_id: int, entity_type: str) -> ResolvedStateModel:
    configured = (
        db.query(StateModel)
        .filter(StateModel.tenant_id == tenant_id, StateModel.entity_type == entity_type, StateModel.is_active.is_(True))
        .order_by(StateModel.id.desc())
        .first()
    )
    if configured is not None:
        resolved = ResolvedStateModel(
            entity_type=entity_type,
            name=configured.name,
            configured=True,
            states={
                s.code: StateDef(s.code, s.label, s.editable, s.is_terminal, s.built_in) for s in configured.states
            },
            transitions=[
                TransitionDef(t.from_state, t.to_state, t.action, t.intent_type, t.allowed_roles) for t in configured.transitions
            ],
        )
        _add_new_built_ins(resolved)
        return resolved
    default = DEFAULT_STATE_MODELS[entity_type]
    return ResolvedStateModel(
        entity_type=entity_type,
        name="Default",
        configured=False,
        states={s["code"]: StateDef(**s) for s in default["states"]},
        transitions=[TransitionDef(**t) for t in default["transitions"]],
    )


def _add_new_built_ins(model: ResolvedStateModel) -> None:
    """A tenant's saved state model predates any built-in state added to
    the product since (e.g. the Sales-to-Order opportunity stages): add
    those states, and the default transitions into/out of them, so the
    standard intents keep working. The tenant's own states and transitions
    are left exactly as saved."""
    default = DEFAULT_STATE_MODELS[model.entity_type]
    added = set()
    for s in default["states"]:
        if s["built_in"] and s["code"] not in model.states and s["code"] in BUILT_IN_STATES[model.entity_type]:
            model.states[s["code"]] = StateDef(**s)
            added.add(s["code"])
    # a built-in status with no default entry yet still has to be in the model
    for code in BUILT_IN_STATES[model.entity_type]:
        if code not in model.states:
            model.states[code] = StateDef(code, code.replace("_", " ").title(), False, False, True)
    for t in default["transitions"]:
        if (t["from_state"] in added or t["to_state"] in added) and (t["from_state"] == "*" or t["from_state"] in model.states):
            model.transitions.append(TransitionDef(**t))


def get_record(db: Session, tenant_id: int, entity_type: str, entity_id: Any):
    """Tenant-scoped lookup - same isolation rule as context_assembly._get_owned."""
    model = ENTITY_MODELS.get(entity_type)
    if model is None or entity_id is None:
        return None
    try:
        obj = db.get(model, int(entity_id))
    except (TypeError, ValueError):
        return None
    if obj is None or getattr(obj, "tenant_id", None) != tenant_id:
        return None
    return obj


def built_in_status(db: Session, entity_type: str, obj) -> str:
    if entity_type == "party":
        has_order = db.query(Order.id).filter(Order.party_id == obj.id).first() is not None
        if has_order:
            return "customer"
        profile = getattr(obj, "profile", None)
        return "account" if profile is not None and profile.account_status == "account" else "prospect"
    return getattr(obj, _STATUS_ATTR[entity_type]).value


def get_meta(db: Session, tenant_id: int, entity_type: str, entity_id: int, create: bool = True) -> EntityRecordMeta | None:
    meta = (
        db.query(EntityRecordMeta)
        .filter(
            EntityRecordMeta.tenant_id == tenant_id,
            EntityRecordMeta.entity_type == entity_type,
            EntityRecordMeta.entity_id == entity_id,
        )
        .first()
    )
    if meta is None and create:
        meta = EntityRecordMeta(tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id)
        db.add(meta)
        db.flush()
    return meta


def effective_state(db: Session, tenant_id: int, entity_type: str, obj, meta: EntityRecordMeta | None = None) -> str:
    """The built-in status, unless a custom sub-state was entered from
    that same built-in status (and so is still current)."""
    base = built_in_status(db, entity_type, obj)
    if meta is None:
        meta = get_meta(db, tenant_id, entity_type, obj.id, create=False)
    if meta is not None and meta.sub_state and meta.sub_state_anchor == base:
        return meta.sub_state
    return base


def state_def(model: ResolvedStateModel, code: str) -> StateDef:
    return model.states.get(code) or StateDef(code, code.replace("_", " ").title(), True, False, code in BUILT_IN_STATES.get(model.entity_type, []))


def is_editable(db: Session, tenant_id: int, entity_type: str, obj) -> tuple[bool, str]:
    state = effective_state(db, tenant_id, entity_type, obj)
    model = resolve_state_model(db, tenant_id, entity_type)
    return state_def(model, state).editable, state


def _transition_applies(t: TransitionDef, current: str, model: ResolvedStateModel) -> bool:
    if t.from_state == "*":
        return not state_def(model, current).is_terminal and t.to_state != current
    return t.from_state == current


def available_transitions(
    db: Session, tenant_id: int, entity_type: str, obj, role: str | None, user_id: int | None = None
) -> list[dict[str, Any]]:
    model = resolve_state_model(db, tenant_id, entity_type)
    current = effective_state(db, tenant_id, entity_type, obj)
    base = built_in_status(db, entity_type, obj)
    app_roles = actor_app_roles(db, user_id)
    out = []
    for t in model.transitions:
        if not _transition_applies(t, current, model) or not t.allows_role(role, app_roles):
            continue
        target = state_def(model, t.to_state)
        # Party's built-in states are derived, never set - a built-in
        # target is only reachable as "leave the custom sub-state".
        if entity_type == "party" and target.built_in and t.to_state != base:
            continue
        out.append(
            {
                "action": t.action,
                "to_state": t.to_state,
                "to_label": target.label,
                "intent_type": t.intent_type,
            }
        )
    return out


def find_transition(
    db: Session, tenant_id: int, entity_type: str, obj, action: str | None, to_state: str | None, role: str | None,
    user_id: int | None = None,
) -> tuple[TransitionDef | None, str]:
    """Returns (transition, error). Exactly one of them is meaningful."""
    model = resolve_state_model(db, tenant_id, entity_type)
    current = effective_state(db, tenant_id, entity_type, obj)
    action_norm = (action or "").strip().lower().replace(" ", "_") or None
    to_norm = (to_state or "").strip().lower().replace(" ", "_") or None
    base = built_in_status(db, entity_type, obj)
    candidates = [
        t
        for t in model.transitions
        if _transition_applies(t, current, model)
        and (action_norm is None or t.action == action_norm)
        and (to_norm is None or t.to_state == to_norm)
        and not (entity_type == "party" and state_def(model, t.to_state).built_in and t.to_state != base)
    ]
    if not candidates:
        allowed = ", ".join(f"{t['action']}->{t['to_state']}" for t in available_transitions(db, tenant_id, entity_type, obj, role, user_id))
        return None, (
            f"No transition {action_norm or ''}{'->' + to_norm if to_norm else ''} from state '{current}' "
            f"in the {entity_type} state model (available: {allowed or 'none'})"
        )
    app_roles = actor_app_roles(db, user_id)
    permitted = [t for t in candidates if t.allows_role(role, app_roles)]
    if not permitted:
        return None, f"Your role ({role}) is not allowed to perform '{candidates[0].action}' on a {entity_type} in state '{current}'"
    return permitted[0], ""


def record_state_history(
    db: Session, tenant_id: int, entity_type: str, entity_id: int, from_state: str | None, to_state: str, source: str
) -> None:
    db.add(
        EntityStateHistory(
            tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id, from_state=from_state, to_state=to_state, source=source
        )
    )


def apply_transition(
    db: Session, tenant_id: int, entity_type: str, obj, transition: TransitionDef, source: str, enforce_intent_binding: bool = True
) -> dict[str, Any]:
    if enforce_intent_binding and transition.intent_type:
        raise ValueError(owned_message(transition, entity_type))
    model = resolve_state_model(db, tenant_id, entity_type)
    meta = get_meta(db, tenant_id, entity_type, obj.id)
    from_state = effective_state(db, tenant_id, entity_type, obj, meta)
    target = state_def(model, transition.to_state)
    base = built_in_status(db, entity_type, obj)

    if target.built_in:
        if transition.to_state != base:
            if entity_type == "party":
                raise ValueError(f"A party's '{transition.to_state}' state is derived and cannot be set directly")
            enum_cls = _STATUS_ENUM[entity_type]
            setattr(obj, _STATUS_ATTR[entity_type], enum_cls(transition.to_state))
        meta.sub_state = None
        meta.sub_state_anchor = None
    else:
        meta.sub_state = transition.to_state
        meta.sub_state_anchor = base

    # State history is written by sync_records (which runs after every
    # intent and flow), so one code path records every state entry no
    # matter what caused it.
    db.flush()
    to_state = effective_state(db, tenant_id, entity_type, obj, meta)
    return {"entity_type": entity_type, "entity_id": obj.id, "from_state": from_state, "to_state": to_state, "action": transition.action}


def owned_message(transition: TransitionDef, entity_type: str) -> str:
    """Refusal for a move an intent owns (the screens show the intent's name
    in place of its code)."""
    return (
        f"'{transition.action}' on a {entity_type} is owned by {transition.intent_type} - "
        f"use that intent so its business steps run"
    )


def set_state(db: Session, tenant_id: int, entity_type: str, obj, to_state: str, source: str) -> dict[str, Any]:
    """Flow-node transition: moves to a state the model knows, honoring
    the transition graph only loosely (flows are admin-authored), but
    still never setting a party's derived built-in state."""
    transition, error = find_transition(db, tenant_id, entity_type, obj, None, to_state, UserRole.admin.value)
    if transition is None:
        model = resolve_state_model(db, tenant_id, entity_type)
        if to_state not in model.states:
            raise ValueError(error)
        transition = TransitionDef("*", to_state, "set_state")
    return apply_transition(db, tenant_id, entity_type, obj, transition, source, enforce_intent_binding=False)


# ---------- assignment / transfer ----------


def resolve_user(db: Session, tenant_id: int, ref: Any) -> User | None:
    if ref is None or ref == "":
        return None
    query = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True))
    if isinstance(ref, int) or str(ref).isdigit():
        return query.filter(User.id == int(ref)).first()
    text = str(ref).strip()
    return query.filter(User.email.ilike(text)).first() or query.filter(User.name.ilike(text)).first()


def resolve_position(db: Session, tenant_id: int, ref: Any) -> Position | None:
    if ref is None or ref == "":
        return None
    query = db.query(Position).filter(Position.tenant_id == tenant_id, Position.is_active.is_(True))
    if isinstance(ref, int) or str(ref).isdigit():
        return query.filter(Position.id == int(ref)).first()
    return query.filter(Position.name.ilike(str(ref).strip())).first()


def resolve_department(db: Session, tenant_id: int, ref: Any):
    from app.models.org import Department

    if ref is None or ref == "":
        return None
    query = db.query(Department).filter(Department.tenant_id == tenant_id, Department.is_active.is_(True))
    if isinstance(ref, int) or str(ref).isdigit():
        return query.filter(Department.id == int(ref)).first()
    return query.filter(Department.name.ilike(str(ref).strip())).first()


def department_named(db: Session, tenant_id: int, ref: Any):
    """'the Enterprise Sales department' / 'Enterprise Sales dept' -> that department."""
    if not ref or isinstance(ref, int):
        return None
    name = re.sub(r"^\s*(?:the\s+)?|\s+(?:department|dept\.?|team)\s*$", "", str(ref), flags=re.I).strip()
    return resolve_department(db, tenant_id, name) if name else None


def normalize_assignee(db: Session, tenant_id: int, entities: dict[str, Any]) -> None:
    """A 'position' that is really a department's name is the department."""
    ref = entities.get("to_position")
    if ref and not entities.get("to_department") and resolve_position(db, tenant_id, ref) is None:
        department = department_named(db, tenant_id, ref)
        if department is not None:
            entities["to_department"] = department.name
            entities.pop("to_position", None)


def assign(
    db: Session,
    tenant_id: int,
    entity_type: str,
    entity_id: int,
    user_id: int | None,
    position_id: int | None,
    reason: str,
    source: str,
    department_id: int | None = None,
) -> dict[str, Any]:
    """The record's owner becomes this user and/or position, or a whole
    department - the same way whoever assigns it (a person, a rule, a flow,
    an approval). Every change is kept in the assignment history."""
    meta = get_meta(db, tenant_id, entity_type, entity_id)
    history = EntityAssignmentHistory(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        from_user_id=meta.owner_user_id,
        from_position_id=meta.owner_position_id,
        from_department_id=meta.owner_department_id,
        to_user_id=user_id,
        to_position_id=position_id,
        to_department_id=department_id,
        reason=reason or "",
        source=source,
    )
    meta.owner_user_id = user_id
    meta.owner_position_id = position_id
    meta.owner_department_id = department_id
    db.add(history)
    db.flush()
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "owner_user_id": user_id,
        "owner_position_id": position_id,
        "owner_department_id": department_id,
        "previous_owner_user_id": history.from_user_id,
        "previous_owner_position_id": history.from_position_id,
        "previous_owner_department_id": history.from_department_id,
    }


def related_records(db: Session, tenant_id: int, entity_type: str, obj) -> list[tuple[str, int]]:
    """What moves with a record when it is handed over: a lead's opportunity,
    and an opportunity's open quotes - so whoever owns a deal owns all of it.
    A quote waiting for an approval stays with its approver."""
    from app.crm_core import approval_service

    out: list[tuple[str, int]] = []
    opportunity = obj.opportunity if entity_type == "lead" else obj if entity_type == "opportunity" else None
    if opportunity is None:
        return out
    if entity_type == "lead":
        out.append(("opportunity", opportunity.id))
    open_states = (QuoteStatus.draft, QuoteStatus.pending_approval, QuoteStatus.approved)
    for quote in db.query(Quote).filter(Quote.tenant_id == tenant_id, Quote.opportunity_id == opportunity.id, Quote.status.in_(open_states)):
        if any(i.status.value == "pending" for i in approval_service.latest_for(db, "quote", quote.id)):
            continue
        out.append(("quote", quote.id))
    return out


def assign_with_related(
    db: Session, tenant_id: int, entity_type: str, obj, user_id: int | None, position_id: int | None, department_id: int | None,
    reason: str, source: str, include_related: bool = True,
) -> dict[str, Any]:
    outcome = assign(db, tenant_id, entity_type, obj.id, user_id, position_id, reason, source, department_id)
    moved = []
    if include_related:
        for rel_type, rel_id in related_records(db, tenant_id, entity_type, obj):
            assign(db, tenant_id, rel_type, rel_id, user_id, position_id, f"With {entity_type} #{obj.id}" + (f" - {reason}" if reason else ""),
                   source, department_id)
            moved.append({"entity_type": rel_type, "entity_id": rel_id})
    return {**outcome, "related": moved}


def _round_robin_member(db: Session, rule: AssignmentRule) -> int | None:
    member_ids = sorted(
        m.user_id
        for m in db.query(PositionMember).filter(PositionMember.position_id == rule.position_id).all()
        if (u := db.get(User, m.user_id)) is not None and u.is_active
    )
    if not member_ids:
        return None
    if rule.last_assigned_user_id in member_ids:
        next_index = (member_ids.index(rule.last_assigned_user_id) + 1) % len(member_ids)
    else:
        next_index = 0
    rule.last_assigned_user_id = member_ids[next_index]
    return member_ids[next_index]


def apply_assignment_rules(
    db: Session, tenant_id: int, entity_type: str, entity_id: int, state: str | None
) -> dict[str, Any] | None:
    """state=None means "record first seen" (creation rules). The single
    highest-priority matching rule wins."""
    query = db.query(AssignmentRule).filter(
        AssignmentRule.tenant_id == tenant_id,
        AssignmentRule.entity_type == entity_type,
        AssignmentRule.is_active.is_(True),
    )
    query = query.filter(AssignmentRule.on_state.is_(None)) if state is None else query.filter(AssignmentRule.on_state == state)
    rule = query.order_by(AssignmentRule.priority, AssignmentRule.id).first()
    if rule is None:
        return None
    user_id: int | None = None
    position_id: int | None = rule.position_id
    department_id: int | None = None
    if rule.strategy == "user":
        user_id, position_id = rule.user_id, None
    elif rule.strategy == "round_robin" and rule.position_id is not None:
        user_id = _round_robin_member(db, rule)
    elif rule.strategy == "department":
        position_id, department_id = None, rule.department_id
    return assign(
        db, tenant_id, entity_type, entity_id, user_id, position_id,
        f"Assignment rule '{rule.name}'" + (f" on entering '{state}'" if state else " on creation"),
        f"rule:{rule.id}", department_id,
    )


# ---------- post-intent state-entry detection ----------

_RESULT_KEYS = {
    "party_id": "party",
    "lead_id": "lead",
    "opportunity_id": "opportunity",
    "quote_id": "quote",
    "new_quote_id": "quote",
    "order_id": "order",
}


def records_in_result(result: dict[str, Any] | None) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for key, entity_type in _RESULT_KEYS.items():
        value = (result or {}).get(key)
        if isinstance(value, int) and (entity_type, value) not in found:
            found.append((entity_type, value))
    if result and result.get("entity_type") in ENTITY_MODELS and isinstance(result.get("entity_id"), int):
        pair = (result["entity_type"], result["entity_id"])
        if pair not in found:
            found.append(pair)
    return found


def sync_records(
    db: Session, tenant_id: int, records: list[tuple[str, int]], source: str
) -> list[dict[str, Any]]:
    """For every record an intent touched: creation assignment on first
    sight, then (on any state change since last seen) state history +
    state-entry assignment. Returns the state entries so the caller can
    run state_enter flows."""
    entries: list[dict[str, Any]] = []
    for entity_type, entity_id in records:
        obj = get_record(db, tenant_id, entity_type, entity_id)
        if obj is None:
            continue
        meta = get_meta(db, tenant_id, entity_type, entity_id, create=False)
        is_new = meta is None
        if meta is None:
            meta = get_meta(db, tenant_id, entity_type, entity_id)
            # Data visibility is keyed on who created a record, as which
            # position, in which department (access_service).
            actor = access_service.current_actor.get()
            if actor:
                meta.created_by_user_id = actor["user_id"]
                meta.created_by_position_id = actor["position_id"]
                meta.department_id = actor["department_id"]
        state = effective_state(db, tenant_id, entity_type, obj, meta)
        if is_new and meta.owner_user_id is None and meta.owner_position_id is None:
            apply_assignment_rules(db, tenant_id, entity_type, entity_id, None)
        if meta.last_seen_state != state:
            previous = meta.last_seen_state
            record_state_history(db, tenant_id, entity_type, entity_id, previous, state, source)
            meta.last_seen_state = state
            apply_assignment_rules(db, tenant_id, entity_type, entity_id, state)
            entries.append({"entity_type": entity_type, "entity_id": entity_id, "from_state": previous, "to_state": state})
    db.flush()
    return entries
