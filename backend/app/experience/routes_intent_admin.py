"""Admin -> Intents: see every intent, rename/describe/recategorise it,
switch it on or off, restrict it to roles, teach it phrases, set field
defaults - and add new intents without code.

A new intent is a custom_intent flow (its behaviour, editable in the Flow
Studio) plus an IntentSetting (its form: fields, labels, roles). The
admin describes what should happen in plain words and the flow is
drafted from that, checked against the real fields, and saved.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.crm_core import lifecycle_service
from app.db import get_db
from app.intent_engine import intent_registry
from app.models.auth import UserRole
from app.models.flows import FlowDefinition
from app.models.intent_catalog import IntentSetting
from app.models.org import AppRole
from app.orchestration import flow_ai, flow_engine, flow_schema
from app.orchestration.function_map import PROCESSES
from app.schemas.intent_catalog import IntentCreateIn, IntentFieldIn, IntentUpdateIn
from app.tenancy import get_current_tenant_id
from app.licensing import entity_available, require_entity, intent_available, enabled, flow_available

router = APIRouter(prefix="/api/admin/intents", tags=["admin-intents"], dependencies=[Depends(require_roles(UserRole.admin))])

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_CUSTOM_FIELD_TYPES = [t for t in intent_registry.FIELD_TYPES if t not in ("updates", "transition")]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")[:60] or "field"


def _setting(db: Session, tenant_id: int, code: str) -> IntentSetting:
    setting = db.query(IntentSetting).filter(IntentSetting.tenant_id == tenant_id, IntentSetting.code == code).first()
    if setting is None:
        setting = IntentSetting(tenant_id=tenant_id, code=code)
        db.add(setting)
    return setting


def _check_roles(db: Session, tenant_id: int, role_ids: list[int]) -> str:
    known = {r.id for r in db.query(AppRole).filter(AppRole.tenant_id == tenant_id).all()}
    unknown = [r for r in role_ids if r not in known]
    if unknown:
        raise HTTPException(400, f"Unknown role id(s): {', '.join(map(str, unknown))}")
    return ",".join(str(r) for r in dict.fromkeys(role_ids))


def _check_category(category: str | None) -> None:
    if category is not None and category not in intent_registry.CATEGORY_KEYS:
        raise HTTPException(400, f"category must be one of: {', '.join(intent_registry.CATEGORY_KEYS)}")


def _clean_fields(fields: list[IntentFieldIn], entity_type: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in fields:
        key = f.key.strip() or _slug(f.label)
        if not _KEY.match(key):
            raise HTTPException(400, f"Field key '{key}' must be lowercase letters, digits and _")
        if any(o["key"] == key for o in out):
            raise HTTPException(400, f"Two fields are called '{key}'")
        if f.type not in _CUSTOM_FIELD_TYPES:
            raise HTTPException(400, f"Field '{f.label}': type must be one of {', '.join(_CUSTOM_FIELD_TYPES)}")
        values = [v.strip() for v in f.values if v.strip()]
        if f.type == "picklist" and not values and not f.source:
            raise HTTPException(400, f"Field '{f.label}' is a picklist - give it some values")
        if f.type == "record" and f.record_type not in lifecycle_service.ENTITY_MODELS:
            raise HTTPException(400, f"Field '{f.label}' must say which record type it looks up")
        out.append({"key": key, "label": f.label.strip(), "type": f.type, "required": f.required, "help": f.help,
                    "placeholder": f.placeholder, "values": values, "source": f.source, "record_type": f.record_type})
    # A flow acting on a record finds it through "<type>_id" - make sure the form asks for it.
    if entity_type and not any(o["key"] == f"{entity_type}_id" for o in out):
        out.insert(0, {"key": f"{entity_type}_id", "label": entity_type.replace("_", " ").capitalize(), "type": "record",
                       "required": True, "help": "", "placeholder": "", "values": [], "source": "", "record_type": entity_type})
    return out


def _required(fields: list[dict[str, Any]]) -> str | None:
    return ",".join(f["key"] for f in fields if f["required"]) or None


def _phrases(raw: list[str]) -> str:
    return ",".join(p.strip().replace(",", " ") for p in raw if p.strip())


@router.get("")
def list_intents(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    entries = intent_registry.all_intents(db, tenant_id)
    categories = {e["category"] for e in entries} | {"custom"}
    roles = db.query(AppRole).filter(AppRole.tenant_id == tenant_id).order_by(AppRole.name).all()
    return {
        "intents": entries,
        "categories": [{"key": k, "label": v} for k, v in intent_registry.CATEGORIES if k in categories],
        "roles": [{"id": r.id, "name": r.name, "is_active": r.is_active} for r in roles],
        "field_types": _CUSTOM_FIELD_TYPES,
        "field_library": [f for f in intent_registry.library() if entity_available(db, tenant_id, f.get("record_type")) and ("telecom_crm" in enabled(db, tenant_id) or not f.get("source"))],
        "entity_types": [e for e in lifecycle_service.ENTITY_MODELS if entity_available(db, tenant_id, e)],
        "processes": [{"key": p["key"], "label": p["label"], "steps": [c for c in dict.fromkeys(p["steps"]) if c in {e["code"] for e in entries}]}
                      for p in PROCESSES],
    }


@router.post("", status_code=201)
def create_intent(
    payload: IntentCreateIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    _check_category(payload.category)
    entity_type = lifecycle_service.normalize_entity_type(payload.entity_type) if payload.entity_type else None
    if payload.entity_type and entity_type is None:
        raise HTTPException(400, f"entity_type must be one of: {', '.join(lifecycle_service.ENTITY_MODELS)}")
    require_entity(db, tenant_id, entity_type)
    fields = _clean_fields(payload.fields, entity_type)
    phrases = _phrases(payload.phrases) or payload.label.strip().lower()
    role_ids = _check_roles(db, tenant_id, payload.role_ids)

    flow = FlowDefinition(
        tenant_id=tenant_id,
        name=payload.label.strip(),
        description=payload.description.strip(),
        entity_type=entity_type,
        trigger_type="custom_intent",
        timing="after",
        trigger_phrases=phrases,
        required_fields=_required(fields),
        graph_json=json.dumps({"nodes": [], "edges": []}),
        is_active=False,
    )
    db.add(flow)
    db.flush()

    notes = ""
    corrections: list[dict[str, Any]] = []
    if payload.behaviour.strip():
        catalog = flow_schema.catalog_for_flow(db, tenant_id, flow)
        draft = flow_ai.generate(db, tenant_id, payload.behaviour, entity_type, catalog)
        review = flow_schema.review_graph(draft["graph"], catalog, True)
        graph, notes, corrections = review["graph"], draft["notes"], review["corrections"]
    else:
        graph = flow_ai.layout({
            "nodes": [{"id": "n1", "type": "start", "label": "Start", "config": {}}, {"id": "n2", "type": "end", "label": "End", "config": {}}],
            "edges": [{"id": "e1", "source": "n1", "target": "n2", "label": ""}],
        })
    errors = flow_engine.validate_graph(graph)
    flow.graph_json = json.dumps(graph)
    if not flow_available(db, tenant_id, flow):
        raise HTTPException(403, "Custom intent references a module that is not selected")
    flow.is_active = payload.is_active and not errors

    setting = _setting(db, tenant_id, flow_engine.custom_intent_code(flow))
    setting.label = payload.label.strip()
    setting.description = payload.description.strip()
    setting.category = payload.category
    setting.icon = payload.icon.strip()[:16] or "✨"
    setting.is_active = payload.is_active
    setting.role_ids = role_ids
    setting.fields_json = json.dumps(fields)
    db.commit()
    return {
        "intent": intent_registry.entry(db, tenant_id, flow_engine.custom_intent_code(flow)),
        "flow_id": flow.id,
        "notes": notes,
        "corrections": corrections,
        "flow_errors": errors,
    }


@router.put("/{code}")
def update_intent(
    code: str, payload: IntentUpdateIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    current = intent_registry.entry(db, tenant_id, code)
    if current is None:
        raise HTTPException(404, "Intent not found")
    _check_category(payload.category)
    setting = _setting(db, tenant_id, code)
    if payload.label is not None:
        setting.label = payload.label.strip() or None
    if payload.description is not None:
        setting.description = payload.description.strip() or None
    if payload.category is not None:
        setting.category = payload.category
    if payload.icon is not None:
        setting.icon = payload.icon.strip()[:16] or None
    if payload.role_ids is not None:
        setting.role_ids = _check_roles(db, tenant_id, payload.role_ids)
    if payload.defaults is not None:
        keys = {f["key"] for f in current["fields"]}
        unknown = [k for k in payload.defaults if k not in keys]
        if unknown:
            raise HTTPException(400, f"No field called {', '.join(unknown)} on this intent")
        setting.defaults_json = json.dumps({k: v for k, v in payload.defaults.items() if v not in (None, "", [])})

    flow = db.get(FlowDefinition, current["flow_id"]) if current["kind"] == "custom" else None
    if payload.phrases is not None:
        phrases = _phrases(payload.phrases)
        if flow is not None:
            if not phrases:
                raise HTTPException(400, "A custom intent needs at least one phrase")
            flow.trigger_phrases = phrases
        else:
            setting.phrases = phrases
    if payload.fields is not None:
        if flow is None:
            raise HTTPException(400, "The fields of a built-in intent are fixed - set defaults instead")
        fields = _clean_fields(payload.fields, flow.entity_type)
        setting.fields_json = json.dumps(fields)
        flow.required_fields = _required(fields)
    if payload.is_active is not None:
        setting.is_active = payload.is_active
        if flow is not None:
            if payload.is_active:
                errors = flow_engine.validate_graph(flow_engine.load_graph(flow))
                if errors:
                    raise HTTPException(400, f"Its flow is not valid yet - fix it in the Flow Studio: {'; '.join(errors)}")
            flow.is_active = payload.is_active
    if flow is not None and payload.label:
        flow.name = payload.label.strip()
    db.commit()
    return intent_registry.entry(db, tenant_id, code)  # type: ignore[return-value]


class BulkRolesIn(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=500)
    role_ids: list[int] = []
    # add: also these roles - remove: no longer these roles - set: exactly these roles - everyone: no restriction
    mode: str = Field(pattern="^(add|remove|set|everyone)$")


@router.post("/bulk-roles")
def bulk_roles(payload: BulkRolesIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Give or take away access to many intents for many roles in one go.
    An intent with no roles is open to everyone, so a removal that would
    leave none is skipped and reported instead of silently opening it up."""
    if payload.mode != "everyone" and not payload.role_ids:
        raise HTTPException(400, "Pick at least one role")
    _check_roles(db, tenant_id, payload.role_ids)
    changed: list[str] = []
    skipped: list[dict[str, str]] = []
    for code in dict.fromkeys(payload.codes):
        current = intent_registry.entry(db, tenant_id, code)
        if current is None:
            skipped.append({"code": code, "reason": "not found"})
            continue
        before = [int(r) for r in current["role_ids"]]
        if payload.mode == "everyone":
            after: list[int] = []
        elif payload.mode == "set":
            after = list(payload.role_ids)
        elif payload.mode == "add":
            after = before + [r for r in payload.role_ids if r not in before]
        else:
            after = [r for r in before if r not in payload.role_ids]
            if not after and before:
                skipped.append({"code": code, "reason": "it would be left with no role, which means everyone could use it"})
                continue
        if after == before:
            continue
        _setting(db, tenant_id, code).role_ids = _check_roles(db, tenant_id, after)
        changed.append(code)
    db.commit()
    return {"changed": changed, "skipped": skipped}


@router.post("/{code}/reset")
def reset_intent(code: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Back to the built-in presentation, on, for everyone."""
    if not intent_available(db, tenant_id, code):
        raise HTTPException(404, "Intent not found")
    if code not in intent_registry.BUILTIN:
        raise HTTPException(400, "Only built-in intents can be reset")
    db.query(IntentSetting).filter(IntentSetting.tenant_id == tenant_id, IntentSetting.code == code).delete()
    db.commit()
    return intent_registry.entry(db, tenant_id, code)  # type: ignore[return-value]


@router.delete("/{code}", status_code=204)
def delete_intent(code: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    current = intent_registry.entry(db, tenant_id, code)
    if current is None:
        raise HTTPException(404, "Intent not found")
    if current["kind"] != "custom":
        raise HTTPException(400, "Built-in intents can't be deleted - switch them off instead")
    flow = db.get(FlowDefinition, current["flow_id"])
    if flow is not None:
        db.delete(flow)
    db.query(IntentSetting).filter(IntentSetting.tenant_id == tenant_id, IntentSetting.code == code).delete()
    db.commit()
