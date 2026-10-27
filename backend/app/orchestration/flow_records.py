"""Tenant-scoped record operations for administrator-authored flows.

The model registry supplies columns, types and references. System-managed
identity, credentials and audit data are never writable through this API.
"""
from __future__ import annotations

import datetime as dt
import enum
import json
from sqlalchemy import select
from app.db import Base

SYSTEM_MODULES = {"auth", "tenant", "intent_log", "ai_settings", "flows", "licensing"}
SYSTEM_FIELDS = {"id", "tenant_id", "brt_id"}


def models():
    return {m.class_.__tablename__: m.class_ for m in Base.registry.mappers}


def writable(column):
    return column.name not in SYSTEM_FIELDS and not column.name.startswith("sys_") and not any(
        word in column.name.lower() for word in ("password", "secret", "token", "api_key"))


def scoped(model, seen=None):
    seen = set(seen or ())
    if model in seen:
        return False
    if "tenant_id" in model.__table__.c:
        return True
    seen.add(model)
    registry = models()
    return any(scoped(registry[fk.column.table.name], seen) for c in model.__table__.c
               for fk in c.foreign_keys if fk.column.table.name in registry)


def available():
    return {key: model for key, model in models().items()
            if model.__module__.split(".")[-1] not in SYSTEM_MODULES and scoped(model)
            and "id" in model.__table__.c}


def catalog(db=None, tenant_id=None):
    from app.licensing import entity_available
    return [{"name": name, "label": name.replace("_", " ").title(), "fields": [
        {"name": c.name, "type": str(c.type), "writable": writable(c),
         "required": not c.nullable and c.default is None and c.server_default is None and writable(c),
         "choices": list(getattr(c.type, "enums", []) or []),
         "references": [fk.target_fullname for fk in c.foreign_keys]}
        for c in model.__table__.c if writable(c) or c.name in ("id", "brt_id")]}
        for name, model in sorted(available().items()) if db is None or entity_available(db, tenant_id, name)]


def owned(db, tenant_id, obj, seen=None):
    if obj is None:
        return False
    if hasattr(obj, "tenant_id"):
        return obj.tenant_id == tenant_id
    seen = set(seen or ())
    identity = (type(obj), obj.id)
    if identity in seen:
        return False
    seen.add(identity)
    registry = models()
    parents = []
    for c in obj.__table__.c:
        if c.name.startswith("sys_"):
            continue
        for fk in c.foreign_keys:
            model = registry.get(fk.column.table.name)
            value = getattr(obj, c.name)
            if model is not None and value is not None and scoped(model):
                parent = db.scalar(select(model).where(getattr(model, fk.column.name) == value))
                parents.append(owned(db, tenant_id, parent, seen))
    return bool(parents) and all(parents)


def plain(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def cast(column, value):
    if value is None:
        if not column.nullable:
            raise ValueError(f"{column.name} cannot be null")
        return None
    cls = getattr(column.type, "enum_class", None)
    if cls:
        return cls(value)
    cls = column.type.python_type
    if cls is bool:
        if value not in (True, False, "true", "false", 0, 1):
            raise ValueError(f"{column.name} expects a boolean")
        return value in (True, "true", 1)
    if cls in (dt.date, dt.datetime):
        return cls.fromisoformat(value) if isinstance(value, str) else value
    if cls is str and isinstance(value, (dict, list)):
        return json.dumps(value)
    return cls(value)


def execute(db, tenant_id, config):
    name, operation = config.get("entity"), config.get("operation")
    if name in ("assets", "asset_changes", "asset_revisions") and operation != "read":
        raise ValueError("Assets and their history are read-only. Use asset-based ordering.")
    model = available().get(name)
    if model is None:
        raise ValueError("Choose a tenant-scoped application entity from the record catalog")
    from app.licensing import require_entity
    require_entity(db, tenant_id, name)
    if operation not in ("read", "insert", "update", "upsert", "delete"):
        raise ValueError("Choose read, insert, update, upsert or delete")
    values, match = config.get("values") or {}, config.get("match") or {}
    if not isinstance(values, dict) or not isinstance(match, dict):
        raise ValueError("Values and match must be JSON objects")
    columns = model.__table__.c
    if any(k not in columns or not writable(columns[k]) for k in values):
        raise ValueError("Values contain an unknown or system-managed field")
    if any(k not in columns or (not writable(columns[k]) and k not in ("id", "brt_id")) for k in match):
        raise ValueError("Match contains an unknown or protected field")
    if operation != "insert" and (not match or any(v in (None, "") for v in match.values())):
        raise ValueError("A non-empty exact match is required; bulk mutations are not allowed")
    obj = None
    with db.begin_nested():
        # Serialize upserts for a tenant, including concurrent first inserts.
        from app.models.tenant import Tenant
        db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if operation != "insert":
            query = select(model).filter_by(**{k: cast(columns[k], v) for k, v in match.items()})
            if "tenant_id" in columns:
                query = query.where(model.tenant_id == tenant_id)
            rows = [row for row in db.scalars(query).all() if owned(db, tenant_id, row)]
            if len(rows) > 1:
                raise ValueError("Match identifies more than one record; use id or BRT_ID")
            obj = rows[0] if rows else None
            if obj is None and operation != "upsert":
                raise ValueError("Record not found in this tenant")
        if obj is not None and operation in ("update", "upsert", "delete"):
            from app.crm_core import lifecycle_service
            entity = next((key for key, cls in lifecycle_service.ENTITY_MODELS.items() if cls is model), None)
            if entity:
                editable, state = lifecycle_service.is_editable(db, tenant_id, entity, obj)
                if not editable:
                    raise ValueError(f"Record is locked in state '{state}'")
        created = obj is None
        if created:
            if any(k in SYSTEM_FIELDS for k in match):
                raise ValueError("Upsert insert requires a natural-key match, not a missing system ID")
            obj = model()
            if "tenant_id" in columns:
                obj.tenant_id = tenant_id
            values = {**match, **values}
        if operation in ("insert", "update", "upsert"):
            for key, value in values.items():
                setattr(obj, key, cast(columns[key], value))
            # Validate every reference, including parent links on child records.
            registry = models()
            for c in columns:
                if not writable(c):
                    continue
                for fk in c.foreign_keys:
                    value = getattr(obj, c.name)
                    if value is None:
                        continue
                    parent_model = registry.get(fk.column.table.name)
                    if parent_model is None or not scoped(parent_model):
                        raise ValueError(f"{c.name}: reference has no tenant ownership")
                    parent = db.scalar(select(parent_model).where(getattr(parent_model, fk.column.name) == value))
                    if not owned(db, tenant_id, parent):
                        raise ValueError(f"{c.name}: referenced record not found in this tenant")
            if not owned(db, tenant_id, obj):
                raise ValueError("Record must belong to the current tenant")
            missing = [c.name for c in columns if writable(c) and not c.nullable
                       and c.default is None and c.server_default is None and getattr(obj, c.name) is None]
            if missing:
                raise ValueError("Required fields missing: " + ", ".join(missing))
            db.add(obj)
            db.flush()
        output = {c.name: plain(getattr(obj, c.name)) for c in columns
                  if writable(c) or c.name in ("id", "brt_id")}
        if operation == "delete":
            # Explicit child deletion avoids ORM cascades silently removing records.
            for table in Base.metadata.tables.values():
                for c in table.c:
                    for fk in c.foreign_keys:
                        if fk.column.table is model.__table__:
                            value = getattr(obj, fk.column.name)
                            if db.execute(select(c).where(c == value).limit(1)).first():
                                raise ValueError(f"Record is referenced by {table.name}; delete related records explicitly first")
            db.delete(obj)
            db.flush()
        return {"record": output, "id": obj.id, "brt_id": obj.brt_id,
                "operation": operation, "created": created, "deleted": operation == "delete"}
