"""Page Designer (crm_core/page_service.py): the pages records are shown and
entered with, their fields (standard + custom), and their validation rules.

Anyone signed in reads a page's form and checks values against it; designing
pages, fields and rules is for administrators.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import lifecycle_service, page_service
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.custom_fields import CUSTOM_FIELD_DATA_TYPES, CustomFieldDefinition
from app.models.flows import FlowDefinition
from app.models.pages import ValidationRule
from app.tenancy import get_current_tenant_id
from app.licensing import entity_available, require_entity, flow_available

router = APIRouter(prefix="/api/pages", tags=["pages"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/admin/pages", tags=["admin-pages"], dependencies=[Depends(require_roles(UserRole.admin))])


def _entity(raw: str) -> str:
    try:
        return page_service.entity_type_of(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _segment(raw: str | None) -> str | None:
    return page_service.segment_of(raw) if raw else None


def _bad(exc: ValueError) -> HTTPException:
    return HTTPException(400, str(exc))


# ---------------------------------------------------------------- runtime


class ValuesIn(BaseModel):
    values: dict[str, Any] = {}
    page_key: str | None = None
    partial: bool = False


@router.get("/{entity}/form")
def form(entity: str, segment: str | None = None, page_key: str | None = None, record_id: int | None = None,
         party_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
         user: User = Depends(get_current_user)) -> dict[str, Any]:
    """party_id: the segment is that prospect's (a lead's page follows its prospect).
    A page the tenant gave to one of the user's roles wins over the default."""
    from app.crm_core import access_service

    role_ids = {r.id for r in access_service.user_roles(db, user)}
    entity_type = _entity(entity)
    from app.licensing import require_module
    require_module(db, tenant_id, 'training_management' if entity_type == 'training_program' else 'telecom_crm')
    values: dict[str, Any] = {}
    if party_id and not segment:
        from app.crm_core import process_service
        from app.models.crm import Party

        party = db.get(Party, party_id)
        if party is not None and party.tenant_id == tenant_id:
            segment = process_service.party_segment(party)
    if record_id and entity_type == "training_program":
        raise HTTPException(400, "Use the training workspace to read training records")
    if record_id:
        obj = lifecycle_service.get_record(db, tenant_id, entity_type, record_id)
        if obj is None:
            raise HTTPException(404, "Record not found")
        values = page_service.record_values(db, tenant_id, entity_type, obj)
        values["brt_id"] = obj.brt_id
        from app.orchestration.process_journey import record_segment
        segment = record_segment(obj)
    return page_service.form(db, tenant_id, entity_type, _segment(segment), page_key, values, role_ids)


@router.post("/{entity}/validate")
def validate(entity: str, payload: ValuesIn, segment: str | None = None, db: Session = Depends(get_db),
             tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Checks values against the page - mandatory fields, types and simple
    rules (flow rules run when the record is actually saved)."""
    entity_type = _entity(entity)
    from app.licensing import require_module
    require_module(db, tenant_id, 'training_management' if entity_type == 'training_program' else 'telecom_crm')
    errors = page_service.validate(db, tenant_id, entity_type, _segment(segment or payload.values.get("segment")), payload.values,
                                   payload.page_key, partial=payload.partial)
    return {"ok": not errors, "errors": errors}


@router.get("/offerings")
def offerings(segment: str | None = None, party_id: int | None = None, db: Session = Depends(get_db),
              tenant_id: int = Depends(get_current_tenant_id)) -> list[str]:
    """Products sold to a segment (or to a prospect's segment)."""
    from app.crm_core import process_service
    from app.models.crm import Party

    if party_id:
        party = db.get(Party, party_id)
        if party is not None and party.tenant_id == tenant_id:
            segment = process_service.party_segment(party) or segment
    return page_service.offerings_for_segment(db, tenant_id, _segment(segment))


@router.get("/lead/{lead_id}/products")
def lead_products(lead_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    from app.models.crm import Lead
    from app.models.pages import LeadProduct

    lead = db.get(Lead, lead_id)
    if lead is None or lead.tenant_id != tenant_id:
        raise HTTPException(404, "Lead not found")
    rows = db.query(LeadProduct).filter(LeadProduct.lead_id == lead_id).order_by(LeadProduct.position, LeadProduct.id)
    return [{"offering_name": r.offering_name, "quantity": r.quantity, "notes": r.notes, "brt_id": r.brt_id} for r in rows]


# ---------------------------------------------------------------- design: pages


class LayoutIn(BaseModel):
    layout: dict[str, Any]
    name: str | None = Field(default=None, max_length=150)
    description: str | None = None


class NewPageIn(BaseModel):
    entity_type: str
    segment: str | None = None
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    instruction: str = ""  # optional: what the AI should put on it


class InstructionIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    page_key: str | None = None


class OpsIn(BaseModel):
    ops: list[dict[str, Any]]
    page_key: str | None = None


@admin_router.get("")
def list_pages(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    page_service.seed_defaults(db, tenant_id)  # tenants made before the Page Designer get the standard fields on first visit
    db.commit()
    from app.intent_engine import intent_registry
    from app.models.org import AppRole

    return {
        "pages": page_service.pages(db, tenant_id),
        "entities": [{"key": k, "label": v} for k, v in page_service.ENTITIES.items() if entity_available(db, tenant_id, k)],
        "data_types": list(CUSTOM_FIELD_DATA_TYPES),
        "segments": list(page_service.SEGMENTS) if entity_available(db, tenant_id, "party") else [],
        # what buttons and page events can run, and who pages can be given to
        "runnable": [{"code": e["code"], "label": e["label"], "flow": e["code"].startswith("FLOW-")}
                     for e in intent_registry.all_intents(db, tenant_id) if e.get("is_active", True)],
        "roles": [{"id": r.id, "name": r.name} for r in db.query(AppRole).filter(AppRole.tenant_id == tenant_id).order_by(AppRole.name)],
        "events": list(page_service.PAGE_EVENTS),
    }


@admin_router.get("/catalog/{entity}")
def field_catalog(entity: str, segment: str | None = None, db: Session = Depends(get_db),
                  tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    require_entity(db, tenant_id, _entity(entity))
    return page_service.catalog(db, tenant_id, _entity(entity), _segment(segment), include_inactive=True)


def _page_parts(db: Session, tenant_id: int, page_key: str) -> tuple[str, str | None]:
    found = next((p for p in page_service.pages(db, tenant_id) if p["page_key"] == page_key), None)
    if found is None:
        raise HTTPException(404, "Page not found")
    return found["entity_type"], found["segment"]


@admin_router.put("/{page_key}")
def save_page(page_key: str, payload: LayoutIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    entity_type, segment = _page_parts(db, tenant_id, page_key)
    try:
        out = page_service.save_page(db, tenant_id, entity_type, segment, payload.layout, page_key, payload.name, payload.description)
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return out


class CopyIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)


class ActiveIn(BaseModel):
    is_active: bool


@admin_router.post("/{page_key}/copy")
def copy_page(page_key: str, payload: CopyIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """A new page of this tenant's own, starting as a copy - switched off until activated."""
    _page_parts(db, tenant_id, page_key)
    try:
        out = page_service.copy_page(db, tenant_id, page_key, payload.name)
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return out


@admin_router.patch("/{page_key}/active")
def set_page_active(page_key: str, payload: ActiveIn, db: Session = Depends(get_db),
                    tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    _page_parts(db, tenant_id, page_key)
    try:
        out = page_service.set_active(db, tenant_id, page_key, payload.is_active)
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return out


@admin_router.delete("/{page_key}", status_code=204)
def reset_page(page_key: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    """A segment page goes back to its default; a page of the tenant's own is removed."""
    _page_parts(db, tenant_id, page_key)
    page_service.reset_page(db, tenant_id, page_key)
    db.commit()


@admin_router.post("")
def new_page(payload: NewPageIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    entity_type = _entity(payload.entity_type)
    require_entity(db, tenant_id, entity_type)
    segment = _segment(payload.segment)
    try:
        out = page_service.new_page(db, tenant_id, entity_type, segment, payload.name, payload.description)
        if payload.instruction.strip():
            plan = page_service.plan_page(db, tenant_id, entity_type, segment, payload.instruction)
            out = page_service.apply_page_ops(db, tenant_id, entity_type, segment, plan["ops"], out["page_key"])
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return out


@admin_router.post("/{page_key}/plan")
def plan(page_key: str, payload: InstructionIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """What the AI would change on the page - nothing is saved."""
    entity_type, segment = _page_parts(db, tenant_id, page_key)
    return page_service.plan_page(db, tenant_id, entity_type, segment, payload.instruction, page_key)


@admin_router.post("/{page_key}/apply")
def apply(page_key: str, payload: OpsIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    entity_type, segment = _page_parts(db, tenant_id, page_key)
    try:
        out = page_service.apply_page_ops(db, tenant_id, entity_type, segment, payload.ops, page_key)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return out


# ---------------------------------------------------------------- design: fields


class FieldIn(BaseModel):
    entity_type: str
    key: str | None = Field(default=None, max_length=60)
    label: str = Field(min_length=1, max_length=150)
    data_type: str = "text"
    values: list[str] = []
    segment: str | None = None
    section: str | None = None
    help: str | None = None


class FieldUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=150)
    values: list[str] | None = None
    section: str | None = None
    help: str | None = None
    segment: str | None = None
    is_active: bool | None = None


def _field_of(db: Session, tenant_id: int, field_id: int) -> CustomFieldDefinition:
    row = db.get(CustomFieldDefinition, field_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Field not found")
    require_entity(db, tenant_id, row.entity_type)
    return row


@admin_router.post("/fields")
def add_field(payload: FieldIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    import re

    entity_type = _entity(payload.entity_type)
    require_entity(db, tenant_id, entity_type)
    key = re.sub(r"[^a-z0-9_]", "_", (payload.key or payload.label).lower()).strip("_")[:60]
    if not re.match(r"^[a-z][a-z0-9_]*$", key or ""):
        raise HTTPException(400, "The field name must start with a letter")
    if payload.data_type not in CUSTOM_FIELD_DATA_TYPES:
        raise HTTPException(400, f"Type must be one of: {', '.join(CUSTOM_FIELD_DATA_TYPES)}")
    values = [v.strip() for v in payload.values if v.strip()]
    if payload.data_type == "picklist" and not values:
        raise HTTPException(400, "A picklist needs its values")
    core = set(lifecycle_service.UPDATABLE_CORE_FIELDS.get(entity_type, {})) | {f["key"] for f in page_service.CORE_FIELDS.get(entity_type, [])}
    if key in core or db.query(CustomFieldDefinition.id).filter(CustomFieldDefinition.tenant_id == tenant_id,
                                                               CustomFieldDefinition.entity_type == entity_type, CustomFieldDefinition.key == key).first():
        raise HTTPException(400, f"There is already a field '{key}' - pick another name")
    row = CustomFieldDefinition(
        tenant_id=tenant_id, entity_type=entity_type, key=key, label=payload.label, data_type=payload.data_type,
        allowed_values=",".join(values) if payload.data_type == "picklist" else None, required=False, is_system=False,
        segment=_segment(payload.segment), section=(payload.section or "More details")[:60], help_text=payload.help,
    )
    db.add(row)
    db.commit()
    return page_service._def_out(row)


@admin_router.patch("/fields/{field_id}")
def update_field(field_id: int, payload: FieldUpdate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _field_of(db, tenant_id, field_id)
    if payload.label is not None:
        row.label = payload.label
    if payload.values is not None and row.data_type == "picklist":
        values = [v.strip() for v in payload.values if v.strip()]
        if not values:
            raise HTTPException(400, "A picklist needs its values")
        row.allowed_values = ",".join(values)
    if payload.section is not None:
        row.section = payload.section[:60]
    if payload.help is not None:
        row.help_text = payload.help
    if payload.segment is not None:
        row.segment = _segment(payload.segment)
    if payload.is_active is not None:
        row.is_active = payload.is_active
    db.commit()
    return page_service._def_out(row)


# ---------------------------------------------------------------- design: validation rules


class RuleIn(BaseModel):
    entity_type: str
    text: str = Field(default="", max_length=2000)
    segment: str | None = None
    name: str | None = Field(default=None, max_length=150)
    message: str = Field(default="", max_length=300)
    flow_id: int | None = None


class CompileIn(BaseModel):
    entity_type: str
    text: str = Field(min_length=1, max_length=2000)
    segment: str | None = None
    values: dict[str, Any] | None = None  # optional sample to test the rule on


@admin_router.get("/rules/all")
def list_rules(entity_type: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    query = db.query(ValidationRule).filter(ValidationRule.tenant_id == tenant_id)
    if entity_type:
        query = query.filter(ValidationRule.entity_type == _entity(entity_type))
    flows = db.query(FlowDefinition).filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.trigger_type == "validation").order_by(FlowDefinition.name)
    return {
        "rules": [page_service.rule_out(db, tenant_id, r) for r in query.order_by(ValidationRule.id) if entity_available(db, tenant_id, r.entity_type)],
        "flows": [{"id": f.id, "name": f.name, "entity_type": f.entity_type, "is_active": f.is_active} for f in flows if flow_available(db, tenant_id, f)],
        "formats": {k: v[1] for k, v in page_service.FORMATS.items()},
    }


@admin_router.post("/rules/compile")
def compile_rule(payload: CompileIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """The AI rule compiler: what the sentence becomes - nothing is saved."""
    entity_type = _entity(payload.entity_type)
    require_entity(db, tenant_id, entity_type)
    segment = _segment(payload.segment)
    try:
        out = page_service.compile_rule(db, tenant_id, entity_type, payload.text, segment)
    except ValueError as exc:
        raise _bad(exc)
    if payload.values is not None:
        labels = {f["key"]: f["label"] for f in page_service.catalog(db, tenant_id, entity_type, segment)}
        out["test"] = page_service.evaluate(out["compiled"], payload.values, labels)
    return out


@admin_router.post("/rules")
def add_rule(payload: RuleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    entity_type = _entity(payload.entity_type)
    require_entity(db, tenant_id, entity_type)
    if not payload.flow_id and not payload.text.strip():
        raise HTTPException(400, "Describe the rule, or pick a flow for a complex one")
    try:
        row = page_service.save_rule(db, tenant_id, entity_type, payload.text, _segment(payload.segment), payload.name, payload.message, payload.flow_id)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return page_service.rule_out(db, tenant_id, row)


@admin_router.put("/rules/{rule_id}")
def update_rule(rule_id: int, payload: RuleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = db.get(ValidationRule, rule_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Rule not found")
    require_entity(db, tenant_id, row.entity_type)
    try:
        row = page_service.save_rule(db, tenant_id, row.entity_type, payload.text, _segment(payload.segment), payload.name, payload.message,
                                     payload.flow_id, rule_id)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return page_service.rule_out(db, tenant_id, row)


@admin_router.patch("/rules/{rule_id}/active")
def toggle_rule(rule_id: int, active: bool, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = db.get(ValidationRule, rule_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Rule not found")
    require_entity(db, tenant_id, row.entity_type)
    row.is_active = active
    db.commit()
    return page_service.rule_out(db, tenant_id, row)


@admin_router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    row = db.get(ValidationRule, rule_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Rule not found")
    require_entity(db, tenant_id, row.entity_type)
    db.delete(row)
    db.commit()
