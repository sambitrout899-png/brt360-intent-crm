"""Configurable multi-tier approval chains (Phase 1 primitive #2).

Replaces the old flat, hardcoded DISCOUNT_MATRIX constant (HLD 5.2 Master &
Reference Data, Appendix B - flagged there as illustrative and needing a
real authority matrix per deployment). A chain is now tenant-configured
reference data: ApprovalChainTemplate rows, each covering a discount-pct
band and optionally a segment, with an ordered list of tiers
(ApprovalChainTier). Per the B2C/B2B BRD research, this needs to vary
*structurally* per tenant/segment - e.g. a large Wholesale deal might need
Regional Sales Head -> VP Sales -> CFO in sequence, while a small B2C
discount needs a single rubber-stamp tier - so template resolution
considers both the discount band and the quote's product segment.

DEFAULT_SEED_TEMPLATES is the out-of-the-box chain shape every newly
seeded tenant gets (equivalent to the old DISCOUNT_MATRIX, with one real
multi-tier band added at the top so the capability is actually exercised).
Admins edit/replace this per tenant via /api/admin/approval-chains.

Two kinds of rule (see app/models/approval.py): "monetary" (a discount /
deal-value band on a quote, or on a consumer order) and "record" (a record
may only enter a state once approved). Rules created before kinds existed
are read through kind_of/gate_state_of, so they behave exactly as before.

Who decides a tier: the holders of its position when it names one that
somebody holds; otherwise - no position, or nobody in it yet - anyone
allowed to run the approval, as it always was. An administrator may
always decide.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models.approval import ApprovalChainInstance, ApprovalChainTemplate, ApprovalChainTier, ApprovalStepInstance
from app.models.auth import User, UserRole
from app.models.crm import ApprovalStatus
from app.models.lifecycle import Position, PositionMember
from app.timeutils import utcnow

# (name, segment, min_discount_pct, max_discount_pct, [approver tiers in order])
DEFAULT_SEED_TEMPLATES: list[tuple[str, str | None, float, float, list[str]]] = [
    ("Auto-Approved", None, 0.0, 10.0, ["Auto-Approved"]),
    ("Regional Sign-off", None, 10.0, 20.0, ["Regional Sales Head"]),
    ("VP Sign-off", None, 20.0, 30.0, ["VP Sales"]),
    ("Executive Escalation", None, 30.0, 100.0, ["VP Sales", "CFO"]),
]


# The approval each record type has always had, and the gate it records:
# a quote's discount chain, the lead gate (-> qualified), the order-control
# gate (-> activating).
CLASSIC_GATES: dict[str, str] = {"quote": "discount", "lead": "qualified", "order": "activating"}
MONETARY = "monetary"
RECORD = "record"
DISCOUNT_GATE = "discount"


def kind_of(template: ApprovalChainTemplate) -> str:
    if template.kind in (MONETARY, RECORD):
        return template.kind
    return MONETARY if template.entity_type == "quote" else RECORD


def gate_state_of(template: ApprovalChainTemplate) -> str | None:
    """The state a record rule unlocks (older lead/order rows: their classic gate)."""
    if kind_of(template) != RECORD:
        return None
    return template.trigger_state or CLASSIC_GATES.get(template.entity_type)


def _active_rules(db: Session, tenant_id: int, entity_type: str) -> list[ApprovalChainTemplate]:
    return (
        db.query(ApprovalChainTemplate)
        .filter(
            ApprovalChainTemplate.tenant_id == tenant_id,
            ApprovalChainTemplate.entity_type == entity_type,
            ApprovalChainTemplate.is_active.is_(True),
        )
        .order_by(ApprovalChainTemplate.id)
        .all()
    )


def resolve_monetary(
    db: Session, tenant_id: int, entity_type: str, discount_pct: float, segment: str | None, amount: float | None = None
) -> ApprovalChainTemplate | None:
    """The monetary rule covering this discount (and deal value, when the
    rule sets a value band). A rule scoped to the exact segment wins over
    a generic (segment=None) one; ties go to the narrowest band."""

    def covers(t: ApprovalChainTemplate) -> bool:
        if kind_of(t) != MONETARY or not (t.min_discount_pct <= discount_pct <= t.max_discount_pct):
            return False
        if t.min_amount is not None and (amount is None or amount < t.min_amount):
            return False
        if t.max_amount is not None and (amount is None or amount > t.max_amount):
            return False
        return True

    candidates = [t for t in _active_rules(db, tenant_id, entity_type) if covers(t)]
    if not candidates:
        return None
    segment_matches = [t for t in candidates if t.segment == segment]
    pool = segment_matches or [t for t in candidates if t.segment is None]
    if not pool:
        return None
    return min(pool, key=lambda t: t.max_discount_pct - t.min_discount_pct)


def resolve_template(
    db: Session, tenant_id: int, discount_pct: float, segment: str | None, amount: float | None = None
) -> ApprovalChainTemplate | None:
    """Pick the quote chain template covering this discount band. A
    template scoped to the quote's exact segment wins over a generic
    (segment=None) one when both cover the discount; ties broken by the
    narrowest band.
    """
    return resolve_monetary(db, tenant_id, "quote", discount_pct, segment, amount)


def resolve_record(db: Session, tenant_id: int, entity_type: str, state: str) -> ApprovalChainTemplate | None:
    """The record rule gating entry into `state`, if the tenant has one."""
    return next(
        (t for t in _active_rules(db, tenant_id, entity_type) if kind_of(t) == RECORD and gate_state_of(t) == state and t.tiers),
        None,
    )


def resolve_simple_template(db: Session, tenant_id: int, entity_type: str) -> ApprovalChainTemplate | None:
    """A chain gate with no discount band to match on - a tenant either
    has one active chain of this entity_type configured (opt-in) or
    none, in which case the caller auto-approves immediately with no
    gate, exactly as it always has. Used for "lead" (Sales Director
    qualification) and "order" (Order Control Unit, Phase 5) gates.
    """
    state = CLASSIC_GATES.get(entity_type)
    return next(
        (t for t in _active_rules(db, tenant_id, entity_type) if kind_of(t) == RECORD and gate_state_of(t) == state),
        None,
    )


def resolve_lead_qualification_template(db: Session, tenant_id: int) -> ApprovalChainTemplate | None:
    return resolve_simple_template(db, tenant_id, "lead")


def resolve_order_control_template(db: Session, tenant_id: int) -> ApprovalChainTemplate | None:
    return resolve_simple_template(db, tenant_id, "order")


def get_chain_instance(db: Session, entity_type: str, entity_id: int, gate: str | None = None) -> ApprovalChainInstance | None:
    """The record's approval for `gate` (default: its classic one)."""
    gate = gate or CLASSIC_GATES.get(entity_type, DISCOUNT_GATE)
    return (
        db.query(ApprovalChainInstance)
        .filter(
            ApprovalChainInstance.entity_type == entity_type,
            ApprovalChainInstance.entity_id == entity_id,
            ApprovalChainInstance.gate == gate,
        )
        .first()
    )


def get_rep_authority_ceiling_pct(db: Session, tenant_id: int, segment: str | None) -> float:
    """The discount pct above which a rep cannot submit a quote without a
    written justification - the ceiling of whichever template is a single,
    auto-approved tier covering zero discount. Falls back to 0 (always
    require justification) if no such template is configured.
    """
    template = resolve_template(db, tenant_id, 0.0, segment)
    if template and len(template.tiers) == 1:
        return template.max_discount_pct
    return 0.0


def instantiate_chain(
    db: Session,
    entity_type: str,
    entity_id: int,
    template: ApprovalChainTemplate,
    gate: str | None = None,
    summary: str = "",
    requested_by_user_id: int | None = None,
) -> ApprovalChainInstance:
    instance = ApprovalChainInstance(
        entity_type=entity_type,
        entity_id=entity_id,
        template_id=template.id,
        status=ApprovalStatus.pending,
        current_sequence=1,
        gate=gate or (gate_state_of(template) if kind_of(template) == RECORD else DISCOUNT_GATE),
        tenant_id=template.tenant_id,
        summary=summary,
        requested_by_user_id=requested_by_user_id,
    )
    db.add(instance)
    db.flush()
    for tier in template.tiers:
        db.add(
            ApprovalStepInstance(
                chain_instance_id=instance.id,
                sequence=tier.sequence,
                approver_tier=tier.approver_tier,
                position_id=tier.position_id,
                status=ApprovalStatus.pending,
            )
        )
    db.flush()
    db.refresh(instance)
    hand_to_approver(db, instance)
    return instance


def record_step_decision(
    db: Session, instance: ApprovalChainInstance, decision: str, reasoning: str, user_id: int | None = None
) -> dict:
    """Decide whichever tier is currently pending. Advances the chain to
    the next tier on approval, or terminates it immediately on rejection -
    a reject at any tier rejects the whole chain, it does not fall through
    to the next one.
    """
    step = next((s for s in instance.steps if s.sequence == instance.current_sequence), None)
    if step is None:
        raise ValueError("This approval chain has no pending step left to decide")

    step.status = ApprovalStatus.approved if decision == "approve" else (
        ApprovalStatus.rejected if decision == "reject" else ApprovalStatus.conditional
    )
    step.reasoning = reasoning
    step.decided_at = utcnow()
    step.decided_by_user_id = user_id

    is_last_tier = instance.current_sequence >= len(instance.steps)
    if decision == "reject":
        instance.status = ApprovalStatus.rejected
    elif is_last_tier:
        instance.status = ApprovalStatus.approved
    else:
        instance.current_sequence += 1
        instance.status = ApprovalStatus.pending
    if instance.status != ApprovalStatus.pending:
        instance.decided_at = utcnow()

    db.flush()
    next_tier = None
    if instance.status == ApprovalStatus.pending:
        next_tier = instance.steps[instance.current_sequence - 1].approver_tier
        hand_to_approver(db, instance)
    else:
        hand_back(db, instance)
    return {
        "chain_status": instance.status.value,
        "decided_tier": step.approver_tier,
        "next_tier": next_tier,
    }


# ---------------------------------------------------------------- who decides


def current_step(instance: ApprovalChainInstance) -> ApprovalStepInstance | None:
    return next((s for s in instance.steps if s.sequence == instance.current_sequence), None)


def position_holder_ids(db: Session, position_id: int) -> set[int]:
    return {
        m.user_id
        for m in db.query(PositionMember).filter(PositionMember.position_id == position_id).all()
        if (u := db.get(User, m.user_id)) is not None and u.is_active
    }


def _strict(db: Session, tenant_id: int | None) -> bool:
    from app.crm_core import org_policy

    return tenant_id is not None and org_policy.is_on(db, tenant_id, "strict_approvals")


# ---------------------------------------------------------------- who decides: the tenant's approval policy
#
# Admin -> Approvals -> "Who decides". Stored in the tenant's settings row
# (cm_settings.data_json, key "approval_policy") - no table of its own. A
# tenant that never saved it follows the "Strict approvals" switch (Admin ->
# Roles & Access) exactly as before:
#   off: a step nobody holds is open to anyone allowed to approve, and the
#        requester may decide their own request;
#   on:  a step nobody holds waits for an administrator, and nobody decides
#        their own request.

POLICY_KEY = "approval_policy"
UNSTAFFED_MODES = ("anyone", "admins", "position")
DEFAULT_SEGMENTS = ["B2B", "B2B-SMB", "B2B-Enterprise", "B2B-Wholesale", "B2C"]
# A step label that is a consent word, never an approver ("Yes" from an old wizard answer).
CONSENT_WORDS = {"yes", "y", "no", "n", "ok", "okay", "sure", "yeah", "yep", "nope", "done", "confirm", "confirmed", "fine"}


def _settings_row(db: Session, tenant_id: int):
    from app.models.comms import CmSetting

    return db.query(CmSetting).filter(CmSetting.tenant_id == tenant_id).first()


def policy(db: Session, tenant_id: int | None) -> dict[str, Any]:
    strict = _strict(db, tenant_id)
    out: dict[str, Any] = {"unstaffed": "admins" if strict else "anyone", "fallback_position_id": None, "self_approval": not strict,
                           "admins_decide_any": True, "segments": list(DEFAULT_SEGMENTS), "configured": False}
    row = _settings_row(db, tenant_id) if tenant_id is not None else None
    try:
        stored = json.loads(row.data_json or "{}").get(POLICY_KEY) if row else None
    except ValueError:
        stored = None
    if isinstance(stored, dict):
        out["configured"] = True
        if stored.get("unstaffed") in UNSTAFFED_MODES:
            out["unstaffed"] = stored["unstaffed"]
        if isinstance(stored.get("fallback_position_id"), int):
            out["fallback_position_id"] = stored["fallback_position_id"]
        for key in ("self_approval", "admins_decide_any"):
            if isinstance(stored.get(key), bool):
                out[key] = stored[key]
        if isinstance(stored.get("segments"), list) and stored["segments"]:
            out["segments"] = [str(s) for s in stored["segments"]]
    return out


def save_policy(db: Session, tenant_id: int, data: dict[str, Any]) -> dict[str, Any]:
    from app.crm_core import org_policy
    from app.models.comms import CmSetting

    current = {k: v for k, v in policy(db, tenant_id).items() if k != "configured"}
    unknown = [k for k in data if k not in current]
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")
    merged = {**current, **data}
    if merged["unstaffed"] not in UNSTAFFED_MODES:
        raise ValueError("When nobody holds a step's position it goes to: anyone allowed to approve, administrators, or a fallback position")
    if merged["unstaffed"] == "position":
        position = db.get(Position, merged.get("fallback_position_id") or 0)
        if position is None or position.tenant_id != tenant_id:
            raise ValueError("Pick the fallback position that decides steps nobody holds")
    else:
        merged["fallback_position_id"] = None
    merged["segments"] = list(dict.fromkeys(s.strip() for s in merged.get("segments") or [] if str(s).strip())) or list(DEFAULT_SEGMENTS)
    merged["self_approval"], merged["admins_decide_any"] = bool(merged["self_approval"]), bool(merged["admins_decide_any"])
    row = _settings_row(db, tenant_id)
    if row is None:
        row = CmSetting(tenant_id=tenant_id, data_json="{}")
        db.add(row)
    try:
        stored = json.loads(row.data_json or "{}")
    except ValueError:
        stored = {}
    stored[POLICY_KEY] = merged
    # the Roles & Access switch says the same thing in one word
    stored[org_policy.KEY] = {**org_policy.get(db, tenant_id), "strict_approvals": not merged["self_approval"] and merged["unstaffed"] != "anyone"}
    row.data_json = json.dumps(stored)
    db.flush()
    return policy(db, tenant_id)


def strict_switched(db: Session, tenant_id: int, strict: bool) -> None:
    """Roles & Access' "Strict approvals" switch, flipped after the policy was saved: it sets the two
    things it has always meant - who decides a step nobody holds, and whether the requester may decide."""
    if policy(db, tenant_id)["configured"]:
        save_policy(db, tenant_id, {"unstaffed": "admins" if strict else "anyone", "self_approval": not strict})


def _is_admin(user: User | None) -> bool:
    return user is not None and getattr(user.role, "value", user.role) == UserRole.admin.value


def _fallback(db: Session, tenant_id: int | None, p: dict[str, Any]) -> tuple[Position, set[int]] | None:
    if p["unstaffed"] != "position" or not p["fallback_position_id"]:
        return None
    position = db.get(Position, p["fallback_position_id"])
    if position is None or position.tenant_id != tenant_id:
        return None
    return position, position_holder_ids(db, position.id)


def decider_check(db: Session, tenant_id: int | None, tier, user_id: int | None, requested_by: int | None = None) -> tuple[bool, str]:
    """A step (a rule's tier or a running approval's step) and this user: whether they may decide it, and why not."""
    if user_id is None:
        return True, ""
    p = policy(db, tenant_id)
    is_admin = _is_admin(db.get(User, user_id))
    label = tier.approver_tier
    if requested_by is not None and requested_by == user_id and not p["self_approval"]:
        return False, f"You asked for this approval - someone in {label} has to decide it"
    holders = position_holder_ids(db, tier.position_id) if tier.position_id else set()
    if holders:
        if user_id in holders or (is_admin and p["admins_decide_any"]):
            return True, ""
        return False, f"This step is for {label} - only people in that position can decide it"
    fallback = _fallback(db, tenant_id, p)
    if fallback is not None and fallback[1]:
        if user_id in fallback[1] or (is_admin and p["admins_decide_any"]):
            return True, ""
        return False, f"Nobody holds {label} yet - it goes to {fallback[0].name}"
    if p["unstaffed"] == "anyone" or is_admin:
        return True, ""
    if tier.position_id is None:
        return False, f"No position is set for {label} yet - an administrator has to decide this step"
    return False, f"Nobody holds {label} yet - an administrator has to decide this step"


def routed_to(db: Session, tenant_id: int | None, tier, user_id: int) -> bool:
    """Whose queue (My Work) a step waits in: its position's holders; for a step nobody holds, the
    fallback position's holders, else the administrators."""
    holders = position_holder_ids(db, tier.position_id) if tier.position_id else set()
    if holders:
        return user_id in holders
    fallback = _fallback(db, tenant_id, policy(db, tenant_id))
    if fallback is not None and fallback[1]:
        return user_id in fallback[1]
    return _is_admin(db.get(User, user_id))


def _deciders(db: Session, tenant_id: int, tier, requested_by: int | None) -> list[int]:
    """Every active user of the tenant who could decide this step now."""
    users = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True)).all()
    return [u.id for u in users if routed_to(db, tenant_id, tier, u.id) and decider_check(db, tenant_id, tier, u.id, requested_by)[0]]


def stuck_reason(db: Session, tenant_id: int, tier, requested_by: int | None) -> str | None:
    """Why nobody can decide this step (None = someone can) - shown to administrators to fix."""
    if str(tier.approver_tier or "").strip().lower() in CONSENT_WORDS:
        return f"The step is called \"{tier.approver_tier}\" - that is not an approver; send it to a real position"
    if _deciders(db, tenant_id, tier, requested_by):
        return None
    holders = position_holder_ids(db, tier.position_id) if tier.position_id else set()
    if holders:
        return f"Only the requester holds {tier.approver_tier}, and requesters may not decide their own approvals"
    return f"Nobody holds {tier.approver_tier}, and the only one who could decide it asked for it"


def can_decide(db: Session, instance: ApprovalChainInstance, user_id: int | None) -> tuple[bool, str]:
    """Whether this user may decide the current tier, and why not (see decider_check / policy)."""
    step = current_step(instance)
    if instance.status != ApprovalStatus.pending or step is None:
        return False, "This approval has already been decided"
    return decider_check(db, instance.tenant_id, step, user_id, instance.requested_by_user_id)


def _tier_open_to(db: Session, tier, user_id: int, tenant_id: int | None) -> tuple[bool, str]:
    return decider_check(db, tenant_id, tier, user_id)


def reroute(db: Session, instance: ApprovalChainInstance, position_id: int) -> str:
    """Send a running approval's current step to another position (the old step was a consent word,
    or nobody holds it) - the record goes to that position's queue."""
    position = db.get(Position, position_id)
    if position is None or position.tenant_id != instance.tenant_id:
        raise ValueError("Position not found")
    step = current_step(instance)
    if instance.status != ApprovalStatus.pending or step is None:
        raise ValueError("This approval has already been decided")
    old = step.approver_tier
    step.approver_tier, step.position_id = position.name, position.id
    db.flush()
    hand_to_approver(db, instance)
    return f"Step {step.sequence} now waits for {position.name} (was {old})"


def reroute_pending(db: Session, template: ApprovalChainTemplate) -> int:
    """After a rule's steps were edited: approvals under way take the new approvers for the steps
    they haven't decided yet. Returns how many approvals changed."""
    changed = 0
    rows = db.query(ApprovalChainInstance).filter(ApprovalChainInstance.template_id == template.id,
                                                  ApprovalChainInstance.status == ApprovalStatus.pending).all()
    tiers = {t.sequence: t for t in template.tiers}
    for instance in rows:
        moved = False
        for step in instance.steps:
            tier = tiers.get(step.sequence)
            if step.status != ApprovalStatus.pending or tier is None:
                continue
            if (step.approver_tier, step.position_id) != (tier.approver_tier, tier.position_id):
                step.approver_tier, step.position_id = tier.approver_tier, tier.position_id
                moved = True
        if moved:
            db.flush()
            hand_to_approver(db, instance)
            changed += 1
    return changed


def waiting_on(db: Session, tenant_id: int, entity_type: str, entity_id: int, user_id: int | None) -> dict[str, Any] | None:
    """The approval this record waits for - a running one, or a lead / new
    order waiting for its gate's first decision - and whether this user may
    decide it now. None when it waits for nothing."""
    pending = [i for i in latest_for(db, entity_type, entity_id)
               if i.status == ApprovalStatus.pending and i.template is not None and i.template.tenant_id == tenant_id]
    if pending:
        instance = pending[0]
        step = current_step(instance)
        if step is None:
            return None
        allowed, why = can_decide(db, instance, user_id)
        return {"rule": instance.template.name, "waiting_for": step.approver_tier, "step": step.sequence,
                "steps": len(instance.steps), "can_decide": allowed, "why": why}
    rule = classic_waiting(db, tenant_id, entity_type, entity_id)
    if rule is None or not rule.tiers:
        return None
    first = rule.tiers[0]
    allowed, why = (True, "") if user_id is None else _tier_open_to(db, first, user_id, tenant_id)
    return {"rule": rule.name, "waiting_for": first.approver_tier, "step": 1, "steps": len(rule.tiers),
            "can_decide": allowed, "why": why}


# The intents that decide an approval, and the record each one decides.
DECIDING_INTENTS: dict[str, str | None] = {"INT-06": "quote", "INT-17": "lead", "INT-25": "order", "INT-36": None}


def decides_by_position(db: Session, tenant_id: int, user_id: int | None, code: str | None, entities: dict[str, Any] | None) -> bool:
    """Whether this user runs `code` as the approver the record waits for -
    the approval's position, not the user's roles, says who decides it."""
    if user_id is None or code not in DECIDING_INTENTS or not entities:
        return False
    from app.crm_core import lifecycle_service

    entity_type = DECIDING_INTENTS[code] or lifecycle_service.normalize_entity_type(entities.get("entity_type"))
    raw = entities.get(f"{entity_type}_id") if DECIDING_INTENTS[code] else entities.get("entity_id")
    try:
        entity_id = int(raw)
    except (TypeError, ValueError):
        return False
    if entity_type is None:
        return False
    info = waiting_on(db, tenant_id, entity_type, entity_id, user_id)
    return info is not None and info["can_decide"]


def waiting_for(instance: ApprovalChainInstance) -> str | None:
    step = current_step(instance)
    return step.approver_tier if step is not None and instance.status == ApprovalStatus.pending else None


# ---------------------------------------------------------------- who owns it meanwhile

ASSIGN_SOURCE = "approval"


def _tag(instance: ApprovalChainInstance) -> str:
    return f"[approval #{instance.id}]"


def hand_to_approver(db: Session, instance: ApprovalChainInstance) -> None:
    """While a tier is pending the record belongs to that tier's position
    (and to its holder, when exactly one person holds it) - so it shows in
    the approver's My Work and their data view. Logged in the assignment
    history; a tier with no position leaves the owner as it is."""
    from app.crm_core import lifecycle_service

    step = current_step(instance)
    if instance.tenant_id is None or step is None or step.position_id is None or instance.status != ApprovalStatus.pending:
        return
    holders = position_holder_ids(db, step.position_id)
    user_id = next(iter(holders)) if len(holders) == 1 else None
    meta = lifecycle_service.get_meta(db, instance.tenant_id, instance.entity_type, instance.entity_id)
    if (meta.owner_user_id, meta.owner_position_id) == (user_id, step.position_id):
        return
    lifecycle_service.assign(
        db, instance.tenant_id, instance.entity_type, instance.entity_id, user_id, step.position_id,
        f"Waiting for {step.approver_tier} approval {_tag(instance)}", ASSIGN_SOURCE,
    )


def hand_back(db: Session, instance: ApprovalChainInstance) -> None:
    """Once decided the record goes back to whoever owned it before the
    approval took it (the requester, when it had no owner)."""
    from app.crm_core import lifecycle_service
    from app.models.lifecycle import EntityAssignmentHistory

    if instance.tenant_id is None:
        return
    taken = (
        db.query(EntityAssignmentHistory)
        .filter(
            EntityAssignmentHistory.tenant_id == instance.tenant_id,
            EntityAssignmentHistory.entity_type == instance.entity_type,
            EntityAssignmentHistory.entity_id == instance.entity_id,
            EntityAssignmentHistory.source == ASSIGN_SOURCE,
            EntityAssignmentHistory.reason.like(f"%{_tag(instance)}"),
        )
        .order_by(EntityAssignmentHistory.id)
        .first()
    )
    if taken is None:
        return  # the approval never took it over
    user_id, position_id = taken.from_user_id, taken.from_position_id
    if user_id is None and position_id is None:
        user_id = instance.requested_by_user_id
    lifecycle_service.assign(
        db, instance.tenant_id, instance.entity_type, instance.entity_id, user_id, position_id,
        f"Approval {instance.status.value} - back to the owner {_tag(instance)}", ASSIGN_SOURCE,
    )


def _user_name(db: Session, user_id: int | None, cache: dict[int, str]) -> str | None:
    if user_id is None:
        return None
    if user_id not in cache:
        user = db.get(User, user_id)
        cache[user_id] = user.name if user else f"user {user_id}"
    return cache[user_id]


# Consequences of a decided approval made outside the intent that owns its
# record's step (e.g. "Approve or reject" on a stage approval): handlers
# keyed (entity_type, gate), "*" for any record type.
Handler = Callable[[Session, int, ApprovalChainInstance, int | None], dict[str, Any]]
_ON_DECIDED: dict[tuple[str, str], Handler] = {}


def on_decided(entity_type: str, gate: str, handler: Handler) -> None:
    _ON_DECIDED[(entity_type, gate)] = handler


def decided_handler(instance: ApprovalChainInstance) -> Handler | None:
    return _ON_DECIDED.get((instance.entity_type, instance.gate)) or _ON_DECIDED.get(("*", "*"))


def pending_for_user(db: Session, tenant_id: int, user_id: int) -> list[ApprovalChainInstance]:
    """Approvals waiting on a step this user may decide - their queue.
    A step goes to its position's holders; one nobody holds to the fallback
    position (Admin -> Approvals) or else the administrators - so the queue
    stays personal. Nothing the user may not decide (their own request,
    when requesters may not decide) is listed."""
    rows = (
        db.query(ApprovalChainInstance)
        .join(ApprovalChainTemplate, ApprovalChainTemplate.id == ApprovalChainInstance.template_id)
        .filter(ApprovalChainTemplate.tenant_id == tenant_id, ApprovalChainInstance.status == ApprovalStatus.pending)
        .order_by(ApprovalChainInstance.id)
        .all()
    )
    out = []
    for row in rows:
        step = current_step(row)
        if step is None:
            continue
        if routed_to(db, tenant_id, step, user_id) and can_decide(db, row, user_id)[0]:
            out.append(row)
    return out


def stuck(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Approvals nobody can decide as things stand, with why - for administrators to send on."""
    out = []
    rows = (db.query(ApprovalChainInstance)
            .join(ApprovalChainTemplate, ApprovalChainTemplate.id == ApprovalChainInstance.template_id)
            .filter(ApprovalChainTemplate.tenant_id == tenant_id, ApprovalChainInstance.status == ApprovalStatus.pending)
            .order_by(ApprovalChainInstance.id).all())
    for row in rows:
        step = current_step(row)
        why = stuck_reason(db, tenant_id, step, row.requested_by_user_id) if step is not None else None
        if why:
            out.append({**describe(db, row), "stuck": why})
    return out


def classic_waiting(db: Session, tenant_id: int, entity_type: str, entity_id: int) -> ApprovalChainTemplate | None:
    """A lead pending qualification under a lead gate, or a new order under
    order control, waits for its approval even before the approval itself
    is opened (that happens on the first decision, as it always has).
    Returns the rule it waits for."""
    from app.models.crm import Lead, LeadStatus
    from app.models.orders import Order, OrderStatus

    if get_chain_instance(db, entity_type, entity_id) is not None:
        return None
    if entity_type == "lead":
        lead = db.get(Lead, entity_id)
        if lead is not None and lead.tenant_id == tenant_id and lead.status == LeadStatus.pending_qualification:
            return resolve_lead_qualification_template(db, tenant_id)
    if entity_type == "order":
        order = db.get(Order, entity_id)
        if order is not None and order.tenant_id == tenant_id and order.status == OrderStatus.created:
            return resolve_order_control_template(db, tenant_id)
    return None


def _first_tier_open_to(db: Session, template: ApprovalChainTemplate, user_id: int, requested_by: int | None = None) -> bool:
    tier = template.tiers[0] if template.tiers else None
    if tier is None:
        return False
    return routed_to(db, template.tenant_id, tier, user_id) and decider_check(db, template.tenant_id, tier, user_id, requested_by)[0]


def classic_waiting_for_user(db: Session, tenant_id: int, user_id: int) -> list[dict[str, Any]]:
    """The queue entries for classic_waiting records this user would decide."""
    from app.models.crm import Lead, LeadStatus
    from app.models.orders import Order, OrderStatus

    out: list[dict[str, Any]] = []
    for entity_type, model, status_col, status in (
        ("lead", Lead, Lead.status, LeadStatus.pending_qualification),
        ("order", Order, Order.status, OrderStatus.created),
    ):
        template = resolve_simple_template(db, tenant_id, entity_type)
        if template is None or not _first_tier_open_to(db, template, user_id):
            continue
        for obj in db.query(model).filter(model.tenant_id == tenant_id, status_col == status).order_by(model.id).all():
            if get_chain_instance(db, entity_type, obj.id) is None and _first_tier_open_to(db, template, user_id, obj.sys_created_by):
                out.append({
                    "id": None, "entity_type": entity_type, "entity_id": obj.id, "kind": RECORD, "rule": template.name,
                    "gate": CLASSIC_GATES[entity_type], "summary": "", "status": "pending",
                    "waiting_for": template.tiers[0].approver_tier, "requested_at": obj.created_at, "decided_at": None,
                    "requested_by": _user_name(db, obj.sys_created_by, {}),
                    "steps": [{"sequence": t.sequence, "approver_tier": t.approver_tier, "status": "pending", "reasoning": "",
                               "decided_at": None, "decided_by": None} for t in template.tiers],
                })
    return out


def describe(db: Session, instance: ApprovalChainInstance) -> dict[str, Any]:
    template = instance.template
    names: dict[int, str] = {}
    return {
        "id": instance.id,
        "entity_type": instance.entity_type,
        "entity_id": instance.entity_id,
        "kind": kind_of(template) if template else "",
        "rule": template.name if template else "",
        "gate": instance.gate,
        "summary": instance.summary,
        "status": instance.status.value,
        "waiting_for": waiting_for(instance),
        "requested_at": instance.created_at,
        "requested_by": _user_name(db, instance.requested_by_user_id, names),
        "decided_at": instance.decided_at,
        "steps": [
            {"sequence": s.sequence, "approver_tier": s.approver_tier, "status": s.status.value,
             "reasoning": s.reasoning, "decided_at": s.decided_at, "decided_by": _user_name(db, s.decided_by_user_id, names)}
            for s in instance.steps
        ],
    }


def audit_log(db: Session, tenant_id: int, limit: int = 500) -> list[dict[str, Any]]:
    """Every approval decision, newest first: what, which rule and tier,
    who asked, who decided, the decision, why and when."""
    rows = (
        db.query(ApprovalStepInstance, ApprovalChainInstance)
        .join(ApprovalChainInstance, ApprovalChainInstance.id == ApprovalStepInstance.chain_instance_id)
        .filter(ApprovalChainInstance.tenant_id == tenant_id, ApprovalStepInstance.decided_at.isnot(None))
        .order_by(ApprovalStepInstance.decided_at.desc(), ApprovalStepInstance.id.desc())
        .limit(limit)
        .all()
    )
    names: dict[int, str] = {}
    return [
        {
            "approval_id": inst.id, "entity_type": inst.entity_type, "entity_id": inst.entity_id, "gate": inst.gate,
            "rule": inst.template.name if inst.template else "", "summary": inst.summary,
            "tier": step.approver_tier, "sequence": step.sequence, "decision": step.status.value, "reasoning": step.reasoning,
            "decided_at": step.decided_at, "decided_by": _user_name(db, step.decided_by_user_id, names),
            "requested_by": _user_name(db, inst.requested_by_user_id, names), "requested_at": inst.created_at,
            "chain_status": inst.status.value,
        }
        for step, inst in rows
    ]


# ---------------------------------------------------------------- approvals inside other modules
#
# Order Management discounts, bill adjustments and payment arrangements keep
# their own statuses; when the tenant has a money rule for them, the matrix
# decides who approves (and the record is handed to that approver). With no
# rule they keep their built-in limits and "an administrator decides".

# rule entity_type -> (record type the approval is on, gate, label)
EMBEDDED: dict[str, tuple[str, str, str]] = {
    "om_order": ("order", "om_discount", "Order Management orders (line discount)"),
    "adjustment": ("adjustment", "adjustment", "Bill adjustments & write-offs (amount)"),
    "arrangement": ("arrangement", "arrangement", "Payment arrangements (amount owed)"),
}


def open_embedded(
    db: Session, tenant_id: int, rule_type: str, entity_id: int, *, discount_pct: float = 0.0, amount: float | None = None,
    summary: str = "", requested_by: int | None = None,
) -> ApprovalChainInstance | None:
    """Opens the approval the matrix asks for, or None when no rule covers it."""
    entity_type, gate, _ = EMBEDDED[rule_type]
    template = resolve_monetary(db, tenant_id, rule_type, discount_pct, None, amount)
    if template is None or not template.tiers:
        return None
    existing = get_chain_instance(db, entity_type, entity_id, gate)
    if existing is not None:
        if existing.status == ApprovalStatus.pending:
            return existing
        existing.gate = f"{gate}~{existing.id}"[:50]  # an earlier round stays in the audit trail
        db.flush()
    return instantiate_chain(db, entity_type, entity_id, template, gate=gate, summary=summary, requested_by_user_id=requested_by)


def pending_embedded(db: Session, rule_type: str, entity_id: int) -> ApprovalChainInstance | None:
    entity_type, gate, _ = EMBEDDED[rule_type]
    instance = get_chain_instance(db, entity_type, entity_id, gate)
    return instance if instance is not None and instance.status == ApprovalStatus.pending else None


def decide(db: Session, instance: ApprovalChainInstance, decision: str, reasoning: str, user_id: int | None) -> dict:
    """The current tier's decision by this user - refused when the matrix names someone else."""
    allowed, why = can_decide(db, instance, user_id)
    if not allowed:
        raise ValueError(why)
    return record_step_decision(db, instance, decision, reasoning, user_id)


def latest_for(db: Session, entity_type: str, entity_id: int) -> list[ApprovalChainInstance]:
    return (
        db.query(ApprovalChainInstance)
        .filter(ApprovalChainInstance.entity_type == entity_type, ApprovalChainInstance.entity_id == entity_id)
        .order_by(ApprovalChainInstance.id.desc())
        .all()
    )


# ---------------------------------------------------------------- rule setup


def position_by_name(db: Session, tenant_id: int, name: str, create: bool = True) -> Position | None:
    position = db.query(Position).filter(Position.tenant_id == tenant_id, Position.name == name).first()
    if position is None and create:
        # Whole-organization data: approvers and process roles act on
        # records others created. An admin can narrow it in Org Structure.
        position = Position(tenant_id=tenant_id, name=name, data_scope="org")
        db.add(position)
        db.flush()
    return position


def add_rule(
    db: Session,
    tenant_id: int,
    *,
    name: str,
    kind: str,
    entity_type: str,
    tiers: list[tuple[str, int | None]],
    trigger_state: str | None = None,
    segment: str | None = None,
    min_discount_pct: float = 0.0,
    max_discount_pct: float = 100.0,
    min_amount: float | None = None,
    max_amount: float | None = None,
    is_active: bool = True,
) -> ApprovalChainTemplate:
    """tiers: [(display name, position id or None), ...] in order."""
    template = ApprovalChainTemplate(
        tenant_id=tenant_id, name=name, kind=kind, entity_type=entity_type, trigger_state=trigger_state, segment=segment,
        min_discount_pct=min_discount_pct, max_discount_pct=max_discount_pct, min_amount=min_amount, max_amount=max_amount,
        is_active=is_active,
    )
    db.add(template)
    db.flush()
    for sequence, (label, position_id) in enumerate(tiers, start=1):
        db.add(ApprovalChainTier(template_id=template.id, sequence=sequence, approver_tier=label, position_id=position_id))
    db.flush()
    db.refresh(template)
    return template


# Ready-made rules an admin can switch on in one click (Admin -> Approvals).
# (key, label, kind, entity_type, trigger_state, segment, min %, max %, [tier position names])
PRESETS: list[dict[str, Any]] = [
    {"key": "cpmo_order", "name": "CPMO order approval", "kind": RECORD, "entity_type": "order", "trigger_state": "activating",
     "segment": None, "tiers": ["CPMO"], "help": "Orders go to fulfilment only after CPMO approves them"},
    {"key": "lead_qualification", "name": "Lead qualification approval", "kind": RECORD, "entity_type": "lead",
     "trigger_state": "qualified", "segment": None, "tiers": ["Sales Director"], "help": "A lead becomes an opportunity only once approved"},
    {"key": "consumer_discount", "name": "Consumer discount approval", "kind": MONETARY, "entity_type": "order", "trigger_state": None,
     "segment": None, "min_discount_pct": 15.0, "max_discount_pct": 100.0, "tiers": ["Retail Sales Manager"],
     "help": "Consumer orders with more than 15% discount wait for the Retail Sales Manager"},
    {"key": "feasibility_signoff", "name": "Feasibility sign-off", "kind": RECORD, "entity_type": "opportunity",
     "trigger_state": "feasible", "segment": None, "tiers": ["Presales Head"], "help": "Presales' 'feasible' needs a second signature"},
    {"key": "om_discount", "name": "Order discount approval", "kind": MONETARY, "entity_type": "om_order", "trigger_state": None,
     "segment": None, "min_discount_pct": 20.01, "max_discount_pct": 100.0, "tiers": ["Sales Manager"],
     "help": "Order Management orders with a line discount above 20% wait for the Sales Manager"},
    {"key": "billing_adjustment", "name": "Bill adjustment approval", "kind": MONETARY, "entity_type": "adjustment", "trigger_state": None,
     "segment": None, "min_discount_pct": 0.0, "max_discount_pct": 100.0, "tiers": ["Billing Supervisor"],
     "help": "Credits over the agent's limit, and write-offs, wait for the Billing Supervisor"},
    {"key": "payment_arrangement", "name": "Payment arrangement approval", "kind": MONETARY, "entity_type": "arrangement",
     "trigger_state": None, "segment": None, "min_discount_pct": 0.0, "max_discount_pct": 100.0, "tiers": ["Collections Supervisor"],
     "help": "Arrangements over the automatic limit wait for the Collections Supervisor"},
]


def link_tiers_to_positions(db: Session, tenant_id: int) -> int:
    """Bind tiers that only carry a name to the tenant's position of that
    name, if there is one. Nobody holding it keeps the old behaviour."""
    linked = 0
    db.flush()
    tiers = (
        db.query(ApprovalChainTier)
        .join(ApprovalChainTemplate, ApprovalChainTemplate.id == ApprovalChainTier.template_id)
        .filter(ApprovalChainTemplate.tenant_id == tenant_id, ApprovalChainTier.position_id.is_(None))
        .all()
    )
    for tier in tiers:
        position = position_by_name(db, tenant_id, tier.approver_tier, create=False)
        if position is not None:
            tier.position_id = position.id
            linked += 1
    db.flush()
    return linked
