"""Custom fields on CRM entities (Phase 7) - see app/models/custom_fields.py.

Values are stored as text and coerced by data_type both on write
(validation - a bad value raises ValueError, which aborts the intent's
saga) and on read (typed values for flow conditions and the UI).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from app.db import cached
from app.models.custom_fields import CustomFieldDefinition, CustomFieldValue

_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}


def list_definitions(db: Session, tenant_id: int, entity_type: str, active_only: bool = True) -> list[CustomFieldDefinition]:
    def load() -> list[CustomFieldDefinition]:
        query = db.query(CustomFieldDefinition).filter(
            CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.entity_type == entity_type
        )
        if active_only:
            query = query.filter(CustomFieldDefinition.is_active.is_(True))
        return query.order_by(CustomFieldDefinition.id).all()

    # read for every record of a BIM load or a page: once per session (app/db.py cached)
    return list(cached(db, ("custom_fields", tenant_id, entity_type, active_only), CustomFieldDefinition, load))


def definition_by_key(db: Session, tenant_id: int, entity_type: str, key: str) -> CustomFieldDefinition | None:
    return (
        db.query(CustomFieldDefinition)
        .filter(
            CustomFieldDefinition.tenant_id == tenant_id,
            CustomFieldDefinition.entity_type == entity_type,
            CustomFieldDefinition.key == key,
            CustomFieldDefinition.is_active.is_(True),
        )
        .first()
    )


def allowed_values(defn: CustomFieldDefinition) -> list[str]:
    return [v.strip() for v in (defn.allowed_values or "").split(",") if v.strip()]


def coerce(defn: CustomFieldDefinition, raw: Any) -> str | None:
    """Validates raw input and returns its canonical stored text."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if defn.required:
            raise ValueError(f"Custom field '{defn.key}' is required")
        return None
    text = str(raw).strip()
    if defn.data_type == "number":
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f"Custom field '{defn.key}' must be a number, got '{text}'")
        return str(int(number)) if number.is_integer() else str(number)
    if defn.data_type == "boolean":
        lowered = text.lower()
        if lowered in _TRUE:
            return "true"
        if lowered in _FALSE:
            return "false"
        raise ValueError(f"Custom field '{defn.key}' must be true/false, got '{text}'")
    if defn.data_type == "date":
        try:
            return dt.date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            raise ValueError(f"Custom field '{defn.key}' must be a date (YYYY-MM-DD), got '{text}'")
    if defn.data_type == "picklist":
        options = allowed_values(defn)
        match = next((o for o in options if o.lower() == text.lower()), None)
        if match is None:
            raise ValueError(f"Custom field '{defn.key}' must be one of: {', '.join(options)} (got '{text}')")
        return match
    return text


def typed(defn: CustomFieldDefinition, stored: str | None) -> Any:
    if stored is None:
        return None
    if defn.data_type == "number":
        number = float(stored)
        return int(number) if number.is_integer() else number
    if defn.data_type == "boolean":
        return stored == "true"
    return stored


def get_values(db: Session, tenant_id: int, entity_type: str, entity_id: int) -> dict[str, Any]:
    definitions = list_definitions(db, tenant_id, entity_type)
    stored = {
        v.field_id: v.value
        for v in db.query(CustomFieldValue).filter(
            CustomFieldValue.tenant_id == tenant_id,
            CustomFieldValue.entity_type == entity_type,
            CustomFieldValue.entity_id == entity_id,
        )
    }
    values: dict[str, Any] = {}
    for defn in definitions:
        raw = stored.get(defn.id, defn.default_value)
        values[defn.key] = typed(defn, raw)
    return values


def set_value(db: Session, tenant_id: int, entity_type: str, entity_id: int, defn: CustomFieldDefinition, raw: Any) -> Any:
    canonical = coerce(defn, raw)
    row = (
        db.query(CustomFieldValue)
        .filter(CustomFieldValue.field_id == defn.id, CustomFieldValue.entity_id == entity_id)
        .first()
    )
    if row is None:
        row = CustomFieldValue(tenant_id=tenant_id, field_id=defn.id, entity_type=entity_type, entity_id=entity_id)
        db.add(row)
    row.value = canonical
    db.flush()
    return typed(defn, canonical)


def missing_required(db: Session, tenant_id: int, entity_type: str, entity_id: int) -> list[str]:
    values = get_values(db, tenant_id, entity_type, entity_id)
    return [d.key for d in list_definitions(db, tenant_id, entity_type) if d.required and values.get(d.key) in (None, "")]
