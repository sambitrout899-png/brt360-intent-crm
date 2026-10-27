"""Generic record read/update across CRM entity types (Phase 7).

update_fields() is the single place a record is modified in place - by
INT-29 ("Update this record") and by flows' set_field nodes - so the
editability gate (state model) and custom-field validation can't be
bypassed by one caller and not the other.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import custom_field_service, lifecycle_service
from app.models.auth import User
from app.models.lifecycle import Position

_SNAPSHOT_FIELDS: dict[str, list[str]] = {
    "party": ["id", "name", "party_type", "email", "phone"],
    "lead": ["id", "party_id", "source", "region", "owner_name", "propensity_score", "interested_offering_name"],
    "opportunity": ["id", "lead_id", "name", "offering_name", "quantity"],
    "quote": ["id", "opportunity_id", "total_price", "requested_discount_pct", "discount_justification", "revision_number", "revision_of_quote_id"],
    "order": ["id", "party_id", "quote_id", "monthly_price", "contract_months"],
}


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def snapshot(db: Session, tenant_id: int, entity_type: str, obj) -> dict[str, Any]:
    """Flat dict a flow condition / template can address: core fields,
    custom fields (by key) and lifecycle info (state, owner)."""
    data: dict[str, Any] = {f: _plain(getattr(obj, f, None)) for f in _SNAPSHOT_FIELDS[entity_type]}
    meta = lifecycle_service.get_meta(db, tenant_id, entity_type, obj.id, create=False)
    data["state"] = lifecycle_service.effective_state(db, tenant_id, entity_type, obj, meta)
    data["built_in_status"] = lifecycle_service.built_in_status(db, entity_type, obj)
    owner = db.get(User, meta.owner_user_id) if meta and meta.owner_user_id else None
    position = db.get(Position, meta.owner_position_id) if meta and meta.owner_position_id else None
    data["owner_user"] = owner.email if owner else None
    data["owner_position"] = position.name if position else None
    data.update(custom_field_service.get_values(db, tenant_id, entity_type, obj.id))
    return data


CORE_FIELD_LABELS: dict[str, str] = {"offering_name": "Product / bundle", "interested_offering_name": "Product interest"}


def core_field_options(db: Session, tenant_id: int, entity_type: str) -> dict[str, list[str]]:
    """Choice lists for editable core fields (the active products for an
    opportunity's product/bundle)."""
    if entity_type != "opportunity":
        return {}
    from app.models.catalog import OfferingStatus, ProductOffering

    rows = db.query(ProductOffering.name).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.status == OfferingStatus.active)
    return {"offering_name": sorted({name for (name,) in rows})}


def split_update_fields(entities: dict[str, Any]) -> dict[str, Any]:
    """INT-29 accepts updates either as entities["fields"] (LLM path) or
    as loose top-level keys (fallback "field: value" parser)."""
    if isinstance(entities.get("fields"), dict):
        return dict(entities["fields"])
    reserved = {"entity_type", "entity_id", "fields", "reasoning", "reason"}
    return {k: v for k, v in entities.items() if k not in reserved and not k.startswith("_")}


def update_fields(
    db: Session, tenant_id: int, entity_type: str, obj, fields: dict[str, Any], enforce_editable: bool = True
) -> dict[str, Any]:
    if not fields:
        raise ValueError("No fields to update were given")
    if enforce_editable:
        editable, state = lifecycle_service.is_editable(db, tenant_id, entity_type, obj)
        if not editable:
            hint = "create a new revision (INT-30) or cancel it" if entity_type == "quote" else "use a lifecycle action (revise/cancel) instead"
            raise ValueError(f"{entity_type.title()} {obj.id} is locked in state '{state}' and can no longer be modified - {hint}")

    core_allowed = lifecycle_service.UPDATABLE_CORE_FIELDS[entity_type]
    applied: dict[str, Any] = {}
    unknown: list[str] = []
    for raw_key, value in fields.items():
        key = str(raw_key).strip().lower().replace(" ", "_")
        if key in core_allowed:
            caster = core_allowed[key]
            try:
                cast_value = caster(value) if value is not None and value != "" else None
            except (TypeError, ValueError):
                raise ValueError(f"Field '{key}' expects {caster.__name__}, got '{value}'")
            if cast_value is None and key in ("name", "quantity"):
                raise ValueError(f"Field '{key}' cannot be blank")
            if key == "quantity" and cast_value < 1:
                raise ValueError("Quantity must be at least 1")
            if key == "offering_name" and cast_value and cast_value not in core_field_options(db, tenant_id, entity_type).get(key, []):
                raise ValueError(f"No active product named '{cast_value}' - pick one from the catalog")
            setattr(obj, key, cast_value)
            applied[key] = cast_value
            continue
        defn = custom_field_service.definition_by_key(db, tenant_id, entity_type, key)
        if defn is not None:
            applied[key] = custom_field_service.set_value(db, tenant_id, entity_type, obj.id, defn, value)
            continue
        unknown.append(key)

    if unknown:
        custom_keys = [d.key for d in custom_field_service.list_definitions(db, tenant_id, entity_type)]
        raise ValueError(
            f"Unknown field(s) for {entity_type}: {', '.join(unknown)}. "
            f"Updatable: {', '.join(list(core_allowed) + custom_keys)}"
        )
    db.flush()
    return applied
