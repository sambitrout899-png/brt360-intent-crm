"""Sales-to-Order / approvals configuration and the approvals queue.

Admin (tenant administrators):
- /api/admin/approval-rules - the two kinds of approval rule (monetary and
  record), tiers bound to positions, one-click presets. The original
  /api/admin/approval-chains (routes_admin.py) keeps working unchanged.
- /api/admin/price-lists - what accounts pay.
- /api/admin/process-roles - which position plays Account Manager,
  Presales, Sales Ops, CPMO and the installation team.
- /api/admin/quote-document-templates - the wording of the proposal and the
  agreement PDFs a quote gets, each of which can be switched off.

Everyone:
- /api/approvals - approvals waiting on me; /api/approvals/{type}/{id} -
  every approval a record has had.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import approval_service, lifecycle_service, process_service, quote_documents
from app.db import get_db
from app.models.approval import ApprovalChainTemplate, ApprovalChainTier
from app.models.auth import User, UserRole
from app.models.crm import Quote
from app.models.lifecycle import Position
from app.models.process import PROCESS_ROLES, PriceList, PriceListItem
from app.tenancy import get_current_tenant_id

admin_router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])
router = APIRouter(prefix="/api/approvals", tags=["approvals"])

MONETARY_RECORD_TYPES = ("quote", "order", *approval_service.EMBEDDED)
MONETARY_LABELS = {"quote": "Quotes (B2B)", "order": "Consumer orders (B2C)",
                   **{k: label for k, (_, _, label) in approval_service.EMBEDDED.items()}}
SEGMENTS = approval_service.DEFAULT_SEGMENTS  # the tenant's own list: Admin -> Approvals -> Who decides


# ---------------------------------------------------------------- approval rules


class TierIn(BaseModel):
    position_id: int | None = None
    approver_tier: str | None = Field(default=None, max_length=100)


class ApprovalRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    kind: str  # monetary | record
    entity_type: str
    trigger_state: str | None = None
    segment: str | None = None
    min_discount_pct: float = Field(default=0, ge=0, le=100)
    max_discount_pct: float = Field(default=100, ge=0, le=100)
    min_amount: float | None = Field(default=None, ge=0)
    max_amount: float | None = Field(default=None, ge=0)
    tiers: list[TierIn] = Field(min_length=1)
    is_active: bool = True
    apply_to_pending: bool = True  # approvals already under way take the new approvers for steps not decided yet


class ActiveIn(BaseModel):
    is_active: bool


def _unstaffed_text(db: Session, tenant_id: int) -> str:
    p = approval_service.policy(db, tenant_id)
    if p["unstaffed"] == "position" and p["fallback_position_id"]:
        position = db.get(Position, p["fallback_position_id"])
        return f"goes to {position.name}" if position else "goes to administrators"
    return "anyone allowed to approve decides it" if p["unstaffed"] == "anyone" else "goes to administrators"


def _tier_out(db: Session, tier: ApprovalChainTier) -> dict[str, Any]:
    position = db.get(Position, tier.position_id) if tier.position_id else None
    holders = approval_service.position_holder_ids(db, tier.position_id) if tier.position_id else set()
    unstaffed = tier.position_id is None or not holders
    tenant_id = tier.template.tenant_id if tier.template is not None else None
    return {
        "sequence": tier.sequence,
        "approver_tier": tier.approver_tier,
        "position_id": tier.position_id,
        "position_name": position.name if position else None,
        "holders": len(holders),
        # Nobody named, or nobody in the position yet: what happens then is the tenant's "Who decides" setting.
        "open_to_anyone": unstaffed,
        "unstaffed_note": (f"nobody holds it - {_unstaffed_text(db, tenant_id)}" if unstaffed and tenant_id else None),
        "not_an_approver": str(tier.approver_tier or "").strip().lower() in approval_service.CONSENT_WORDS and position is None
        or (position is not None and position.name.strip().lower() in approval_service.CONSENT_WORDS),
    }


def _rule_out(db: Session, rule: ApprovalChainTemplate) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "kind": approval_service.kind_of(rule),
        "entity_type": rule.entity_type,
        "trigger_state": approval_service.gate_state_of(rule),
        "segment": rule.segment,
        "min_discount_pct": rule.min_discount_pct,
        "max_discount_pct": rule.max_discount_pct,
        "min_amount": rule.min_amount,
        "max_amount": rule.max_amount,
        "is_active": rule.is_active,
        "tiers": [_tier_out(db, t) for t in rule.tiers],
    }


def _validated(db: Session, tenant_id: int, payload: ApprovalRuleIn, rule_id: int | None = None) -> list[tuple[str, int | None]]:
    if payload.kind not in (approval_service.MONETARY, approval_service.RECORD):
        raise HTTPException(400, "kind must be 'monetary' or 'record'")
    if payload.kind == approval_service.MONETARY:
        if payload.entity_type not in MONETARY_RECORD_TYPES:
            raise HTTPException(400, "Money approvals apply to: " + ", ".join(MONETARY_LABELS.values()))
        if payload.min_discount_pct > payload.max_discount_pct:
            raise HTTPException(400, "The discount band's lower bound is above its upper bound")
        if payload.min_amount is not None and payload.max_amount is not None and payload.min_amount > payload.max_amount:
            raise HTTPException(400, "The value band's lower bound is above its upper bound")
    else:
        entity_type = lifecycle_service.normalize_entity_type(payload.entity_type)
        if entity_type is None:
            raise HTTPException(400, f"Unknown record type '{payload.entity_type}'")
        states = lifecycle_service.resolve_state_model(db, tenant_id, entity_type).states
        if not payload.trigger_state or payload.trigger_state not in states:
            raise HTTPException(400, f"Pick the {entity_type} stage this approval guards")
        clash = next((r for r in db.query(ApprovalChainTemplate).filter(
            ApprovalChainTemplate.tenant_id == tenant_id, ApprovalChainTemplate.entity_type == entity_type,
            ApprovalChainTemplate.is_active.is_(True), ApprovalChainTemplate.id != (rule_id or 0)).all()
            if approval_service.kind_of(r) == approval_service.RECORD and approval_service.gate_state_of(r) == payload.trigger_state), None)
        if clash is not None and payload.is_active:
            raise HTTPException(400, f"'{clash.name}' already guards that stage - edit it or switch it off first")
    tiers: list[tuple[str, int | None]] = []
    for tier in payload.tiers:
        position = db.get(Position, tier.position_id) if tier.position_id else None
        if tier.position_id and (position is None or position.tenant_id != tenant_id):
            raise HTTPException(400, f"Position {tier.position_id} not found")
        label = position.name if position else (tier.approver_tier or "").strip()
        if position is not None and label.lower() in approval_service.CONSENT_WORDS:
            raise HTTPException(400, f"“{label}” is not an approver - pick the position that really decides this step")
        if not label or label.lower() in approval_service.CONSENT_WORDS:
            raise HTTPException(400, "Each step needs a position")
        tiers.append((label, position.id if position else None))
    return tiers


@admin_router.get("/approval-rules")
def list_approval_rules(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    rules = db.query(ApprovalChainTemplate).filter(ApprovalChainTemplate.tenant_id == tenant_id).order_by(ApprovalChainTemplate.id).all()
    return [_rule_out(db, r) for r in rules]


@admin_router.get("/approval-rules/options")
def approval_rule_options(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    record_types = []
    for entity_type in lifecycle_service.ENTITY_MODELS:
        model = lifecycle_service.resolve_state_model(db, tenant_id, entity_type)
        record_types.append({"entity_type": entity_type,
                             "states": [{"code": s.code, "label": s.label} for s in model.states.values()]})
    positions = db.query(Position).filter(Position.tenant_id == tenant_id, Position.is_active.is_(True)).order_by(Position.name).all()
    rules = db.query(ApprovalChainTemplate).filter(ApprovalChainTemplate.tenant_id == tenant_id).all()
    applied = {p["key"] for p in approval_service.PRESETS if any(r.name == p["name"] for r in rules)}
    return {
        "record_types": record_types,
        "monetary_record_types": list(MONETARY_RECORD_TYPES),
        "monetary_labels": MONETARY_LABELS,
        "segments": approval_service.policy(db, tenant_id)["segments"],
        "positions": [{"id": p.id, "name": p.name, "holders": len(approval_service.position_holder_ids(db, p.id))} for p in positions],
        "presets": [{**p, "applied": p["key"] in applied} for p in approval_service.PRESETS],
    }


@admin_router.post("/approval-rules", status_code=201)
def create_approval_rule(payload: ApprovalRuleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    tiers = _validated(db, tenant_id, payload)
    rule = approval_service.add_rule(
        db, tenant_id, name=payload.name, kind=payload.kind,
        entity_type=lifecycle_service.normalize_entity_type(payload.entity_type) or payload.entity_type,
        tiers=tiers, trigger_state=payload.trigger_state if payload.kind == approval_service.RECORD else None,
        segment=payload.segment or None, min_discount_pct=payload.min_discount_pct, max_discount_pct=payload.max_discount_pct,
        min_amount=payload.min_amount, max_amount=payload.max_amount, is_active=payload.is_active,
    )
    db.commit()
    return _rule_out(db, rule)


@admin_router.put("/approval-rules/{rule_id}")
def update_approval_rule(
    rule_id: int, payload: ApprovalRuleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    """Approvals already under way keep the steps they started with."""
    rule = db.get(ApprovalChainTemplate, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise HTTPException(404, "Approval rule not found")
    tiers = _validated(db, tenant_id, payload, rule_id)
    rule.name, rule.kind, rule.segment = payload.name, payload.kind, payload.segment or None
    rule.entity_type = lifecycle_service.normalize_entity_type(payload.entity_type) or payload.entity_type
    rule.trigger_state = payload.trigger_state if payload.kind == approval_service.RECORD else None
    rule.min_discount_pct, rule.max_discount_pct = payload.min_discount_pct, payload.max_discount_pct
    rule.min_amount, rule.max_amount, rule.is_active = payload.min_amount, payload.max_amount, payload.is_active
    existing = list(rule.tiers)
    for sequence, (label, position_id) in enumerate(tiers, start=1):
        if sequence <= len(existing):
            existing[sequence - 1].approver_tier, existing[sequence - 1].position_id = label, position_id
        else:
            rule.tiers.append(ApprovalChainTier(sequence=sequence, approver_tier=label, position_id=position_id))
    for extra in existing[len(tiers):]:
        rule.tiers.remove(extra)
    db.flush()
    db.refresh(rule)
    rerouted = approval_service.reroute_pending(db, rule) if payload.apply_to_pending else 0
    db.commit()
    db.refresh(rule)
    return {**_rule_out(db, rule), "rerouted": rerouted}


@admin_router.patch("/approval-rules/{rule_id}/active")
def set_rule_active(rule_id: int, payload: ActiveIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    rule = db.get(ApprovalChainTemplate, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise HTTPException(404, "Approval rule not found")
    rule.is_active = payload.is_active
    db.commit()
    return _rule_out(db, rule)


class PresetIn(BaseModel):
    # the positions that decide each step, in order - left out, positions named as the preset says
    # (created empty when missing, as before)
    position_ids: list[int] | None = None


@admin_router.post("/approval-rules/presets/{key}", status_code=201)
def apply_preset(key: str, payload: PresetIn | None = None, db: Session = Depends(get_db),
                 tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    preset = next((p for p in approval_service.PRESETS if p["key"] == key), None)
    if preset is None:
        raise HTTPException(404, "Unknown preset")
    if preset["kind"] == approval_service.RECORD and approval_service.resolve_record(db, tenant_id, preset["entity_type"], preset["trigger_state"]):
        raise HTTPException(400, "An approval already guards that stage")
    if payload is not None and payload.position_ids:
        positions = [db.get(Position, pid) for pid in payload.position_ids]
        if any(p is None or p.tenant_id != tenant_id for p in positions):
            raise HTTPException(400, "Pick positions of this organization")
        if any(p.name.strip().lower() in approval_service.CONSENT_WORDS for p in positions):
            raise HTTPException(400, "Pick the positions that really decide each step")
    else:
        positions = [approval_service.position_by_name(db, tenant_id, name) for name in preset["tiers"]]
    rule = approval_service.add_rule(
        db, tenant_id, name=preset["name"], kind=preset["kind"], entity_type=preset["entity_type"],
        trigger_state=preset.get("trigger_state"), segment=preset.get("segment"),
        min_discount_pct=preset.get("min_discount_pct", 0.0), max_discount_pct=preset.get("max_discount_pct", 100.0),
        tiers=[(p.name, p.id) for p in positions],
    )
    db.commit()
    return _rule_out(db, rule)


# ---------------------------------------------------------------- who decides (approval policy) and approvals under way


class ApprovalPolicyIn(BaseModel):
    unstaffed: str | None = None  # anyone | admins | position
    fallback_position_id: int | None = None
    self_approval: bool | None = None
    admins_decide_any: bool | None = None
    segments: list[str] | None = None


@admin_router.get("/approval-policy")
def get_approval_policy(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return approval_service.policy(db, tenant_id)


@admin_router.put("/approval-policy")
def put_approval_policy(payload: ApprovalPolicyIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    # only what was sent changes; fallback_position_id may be sent as null
    data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k == "fallback_position_id"}
    try:
        out = approval_service.save_policy(db, tenant_id, data)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    db.commit()
    return out


def _waiting_out(db: Session, tenant_id: int, instance) -> dict[str, Any]:
    item = approval_service.describe(db, instance)
    obj = lifecycle_service.get_record(db, tenant_id, instance.entity_type, instance.entity_id)
    item["record_label"] = getattr(obj, "name", None) or f"{instance.entity_type.title()} #{instance.entity_id}"
    step = approval_service.current_step(instance)
    item["position_id"] = step.position_id if step else None
    item["stuck"] = approval_service.stuck_reason(db, tenant_id, step, instance.requested_by_user_id) if step else None
    return item


@admin_router.get("/approvals/waiting")
def approvals_waiting(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    """Every approval under way, who it waits for, and why nobody can decide it when that is so."""
    from app.models.approval import ApprovalChainInstance
    from app.models.crm import ApprovalStatus

    rows = (db.query(ApprovalChainInstance)
            .join(ApprovalChainTemplate, ApprovalChainTemplate.id == ApprovalChainInstance.template_id)
            .filter(ApprovalChainTemplate.tenant_id == tenant_id, ApprovalChainInstance.status == ApprovalStatus.pending)
            .order_by(ApprovalChainInstance.id).all())
    return [_waiting_out(db, tenant_id, r) for r in rows]


class RouteIn(BaseModel):
    position_id: int


@admin_router.post("/approvals/{approval_id}/route")
def route_approval(approval_id: int, payload: RouteIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Send a running approval's current step to another position."""
    from app.models.approval import ApprovalChainInstance

    instance = db.get(ApprovalChainInstance, approval_id)
    if instance is None or instance.tenant_id != tenant_id:
        raise HTTPException(404, "Approval not found")
    try:
        change = approval_service.reroute(db, instance, payload.position_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    db.commit()
    return {"change": change, **_waiting_out(db, tenant_id, instance)}


# ---------------------------------------------------------------- pricelists


class PriceListItemIn(BaseModel):
    offering_name: str = Field(min_length=1, max_length=200)
    price: float = Field(ge=0)
    min_quantity: int = Field(default=1, ge=1)  # volume tier: this price from this many units up


class PriceListIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    currency: str = Field(default="INR", max_length=10)
    adjustment_pct: float = Field(default=0, ge=-100, le=100)
    is_default: bool = False
    is_active: bool = True
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    items: list[PriceListItemIn] = []


def _price_list_out(price_list: PriceList) -> dict[str, Any]:
    return {
        "id": price_list.id, "name": price_list.name, "currency": price_list.currency,
        "adjustment_pct": price_list.adjustment_pct, "is_default": price_list.is_default, "is_active": price_list.is_active,
        "valid_from": price_list.valid_from, "valid_to": price_list.valid_to, "in_effect": price_list.in_effect(),
        "items": [{"offering_name": i.offering_name, "price": i.price, "min_quantity": i.min_quantity or 1}
                  for i in sorted(price_list.items, key=lambda i: (i.offering_name.lower(), i.min_quantity or 1))],
    }


def _save_price_list(db: Session, tenant_id: int, price_list: PriceList, payload: PriceListIn) -> PriceList:
    clash = db.query(PriceList).filter(PriceList.tenant_id == tenant_id, PriceList.name == payload.name,
                                       PriceList.id != (price_list.id or 0)).first()
    if clash is not None:
        raise HTTPException(400, f"A pricelist named '{payload.name}' already exists")
    if payload.is_default:
        for other in db.query(PriceList).filter(PriceList.tenant_id == tenant_id, PriceList.id != (price_list.id or 0)).all():
            other.is_default = False
    price_list.name, price_list.currency, price_list.adjustment_pct = payload.name, payload.currency, payload.adjustment_pct
    if payload.valid_from and payload.valid_to and payload.valid_to < payload.valid_from:
        raise HTTPException(400, "The pricelist must end on or after the day it starts")
    tiers = [(i.offering_name.strip().lower(), i.min_quantity) for i in payload.items]
    if len(set(tiers)) != len(tiers):
        raise HTTPException(400, "Each product can have one price per volume tier (minimum quantity)")
    price_list.is_default, price_list.is_active = payload.is_default, payload.is_active
    price_list.valid_from, price_list.valid_to = payload.valid_from, payload.valid_to
    price_list.items = []
    db.flush()  # the old prices go before the new ones come in - the same product and tier may be saved again
    price_list.items = [PriceListItem(offering_name=i.offering_name, price=i.price, min_quantity=i.min_quantity) for i in payload.items]
    db.flush()
    return price_list


@admin_router.get("/price-lists")
def list_price_lists(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [_price_list_out(p) for p in db.query(PriceList).filter(PriceList.tenant_id == tenant_id).order_by(PriceList.name).all()]


@admin_router.post("/price-lists", status_code=201)
def create_price_list(payload: PriceListIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    price_list = PriceList(tenant_id=tenant_id, name=payload.name)
    db.add(price_list)
    _save_price_list(db, tenant_id, price_list, payload)
    db.commit()
    return _price_list_out(price_list)


@admin_router.put("/price-lists/{price_list_id}")
def update_price_list(
    price_list_id: int, payload: PriceListIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    price_list = db.get(PriceList, price_list_id)
    if price_list is None or price_list.tenant_id != tenant_id:
        raise HTTPException(404, "Pricelist not found")
    _save_price_list(db, tenant_id, price_list, payload)
    db.commit()
    return _price_list_out(price_list)


# ---------------------------------------------------------------- process roles


class ProcessRoleIn(BaseModel):
    position_id: int | None = None


@admin_router.get("/process-roles")
def list_process_roles(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return process_service.role_map(db, tenant_id)


@admin_router.put("/process-roles/{role_key}")
def set_process_role(
    role_key: str, payload: ProcessRoleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[dict[str, Any]]:
    if role_key not in PROCESS_ROLES:
        raise HTTPException(404, "Unknown process role")
    if payload.position_id is not None:
        position = db.get(Position, payload.position_id)
        if position is None or position.tenant_id != tenant_id:
            raise HTTPException(400, "Position not found")
    process_service.set_role(db, tenant_id, role_key, payload.position_id)
    db.commit()
    return process_service.role_map(db, tenant_id)


# ---------------------------------------------------------------- quote documents (proposal, agreement)


class DocumentTemplateIn(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(default=None, max_length=200)
    intro: str | None = Field(default=None, max_length=5000)
    terms: str | None = Field(default=None, max_length=10000)
    closing: str | None = Field(default=None, max_length=3000)
    reset: bool = False


@admin_router.get("/quote-document-templates")
def list_document_templates(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return {"templates": quote_documents.templates(db, tenant_id), "placeholders": list(quote_documents.PLACEHOLDERS)}


@admin_router.put("/quote-document-templates/{kind}")
def save_document_template(kind: str, payload: DocumentTemplateIn, db: Session = Depends(get_db),
                           tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        saved = quote_documents.save_template(db, tenant_id, kind, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return saved


@admin_router.get("/quote-document-templates/{kind}/preview")
def preview_document_template(kind: str, quote_id: int | None = None, db: Session = Depends(get_db),
                              tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    """The document as it would be written now - for the given quote, else the newest quote."""
    if kind not in quote_documents.DEFAULT_TEMPLATES:
        raise HTTPException(404, "Unknown document")
    query = db.query(Quote).filter(Quote.tenant_id == tenant_id)
    quote = query.filter(Quote.id == quote_id).first() if quote_id else query.order_by(Quote.id.desc()).first()
    if quote is None:
        raise HTTPException(404, "There is no quote yet to preview it with - create one first")
    content, _ = quote_documents.render(db, tenant_id, quote, kind, {"order_id": "(order)", "agreement_number": "(agreement)"})
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{kind}-preview.pdf"',
                                                                     "Cache-Control": "no-store"})


# ---------------------------------------------------------------- approvals queue


@router.get("")
def my_approvals(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)
) -> list[dict[str, Any]]:
    """Approvals waiting on a step this user decides."""
    out = []
    for instance in approval_service.pending_for_user(db, tenant_id, user.id):
        item = approval_service.describe(db, instance)
        obj = lifecycle_service.get_record(db, tenant_id, instance.entity_type, instance.entity_id)
        if obj is None and instance.entity_type in lifecycle_service.ENTITY_MODELS:
            continue
        item["record_label"] = getattr(obj, "name", None) or f"{instance.entity_type.title()} #{instance.entity_id}"
        out.append(item)
    for item in approval_service.classic_waiting_for_user(db, tenant_id, user.id):
        item["record_label"] = f"{item['entity_type'].title()} #{item['entity_id']}"
        out.append(item)
    return out


@router.get("/log", dependencies=[Depends(require_roles(UserRole.admin))])
def approval_log(
    limit: int = 500, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[dict[str, Any]]:
    """Audit trail: every approval decision, who asked, who decided, why and when."""
    return approval_service.audit_log(db, tenant_id, max(1, min(limit, 5000)))


@router.get("/{entity_type}/{entity_id}")
def record_approvals(
    entity_type: str, entity_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    entity_type = lifecycle_service.normalize_entity_type(entity_type) or entity_type
    rows = [i for i in approval_service.latest_for(db, entity_type, entity_id) if i.template and i.template.tenant_id == tenant_id]
    out = []
    for instance in rows:
        item = approval_service.describe(db, instance)
        item["can_decide"] = instance.status.value == "pending" and approval_service.can_decide(db, instance, user.id)[0]
        out.append(item)
    return out
