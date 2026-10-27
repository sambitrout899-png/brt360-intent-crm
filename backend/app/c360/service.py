"""Storing Customer 360 layouts (versioned, with undo) and connectors."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.c360 import catalog, connectors
from app.c360.connectors import ConnectorSpec
from app.models.c360 import C360Layout, DataConnector

HISTORY_KEEP = 30


def _latest(db: Session, tenant_id: int) -> C360Layout | None:
    return db.query(C360Layout).filter(C360Layout.tenant_id == tenant_id).order_by(C360Layout.version.desc()).first()


def current(db: Session, tenant_id: int) -> tuple[dict[str, Any], int]:
    """(layout, version) - version 0 = never configured, built-in default."""
    row = _latest(db, tenant_id)
    if row is None:
        return catalog.default_layout(), 0
    try:
        return catalog.normalize_layout(json.loads(row.config_json)), row.version
    except json.JSONDecodeError:
        return catalog.default_layout(), row.version


def save(db: Session, tenant_id: int, layout: dict[str, Any], summary: str, changed_by: str) -> int:
    clean = catalog.normalize_layout(layout)
    version = (db.query(func.max(C360Layout.version)).filter(C360Layout.tenant_id == tenant_id).scalar() or 0) + 1
    db.add(C360Layout(tenant_id=tenant_id, version=version, config_json=json.dumps(clean), summary=summary[:2000], changed_by=changed_by[:200]))
    db.flush()
    stale = (
        db.query(C360Layout.id).filter(C360Layout.tenant_id == tenant_id)
        .order_by(C360Layout.version.desc()).offset(HISTORY_KEEP).all()
    )
    if stale:
        db.query(C360Layout).filter(C360Layout.id.in_([i for (i,) in stale])).delete(synchronize_session=False)
    return version


def history(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    rows = db.query(C360Layout).filter(C360Layout.tenant_id == tenant_id).order_by(C360Layout.version.desc()).limit(HISTORY_KEEP).all()
    return [{"version": r.version, "summary": r.summary, "changed_by": r.changed_by, "created_at": r.created_at} for r in rows]


def version_layout(db: Session, tenant_id: int, version: int) -> dict[str, Any] | None:
    row = db.query(C360Layout).filter(C360Layout.tenant_id == tenant_id, C360Layout.version == version).first()
    return catalog.normalize_layout(json.loads(row.config_json)) if row else None


def previous(db: Session, tenant_id: int) -> dict[str, Any] | None:
    """The layout before the live one (None = nothing to undo)."""
    rows = db.query(C360Layout).filter(C360Layout.tenant_id == tenant_id).order_by(C360Layout.version.desc()).limit(2).all()
    if not rows:
        return None
    if len(rows) == 1:
        return catalog.default_layout()
    return catalog.normalize_layout(json.loads(rows[1].config_json))


# ---------------------------------------------------------------- connectors


def connector_rows(db: Session, tenant_id: int) -> list[DataConnector]:
    return db.query(DataConnector).filter(DataConnector.tenant_id == tenant_id).order_by(DataConnector.id).all()


def specs(db: Session, tenant_id: int) -> list[ConnectorSpec]:
    return [connectors.spec_from_model(r) for r in connector_rows(db, tenant_id)]


def connector_out(row: DataConnector) -> dict[str, Any]:
    spec = connectors.spec_from_model(row)
    return {
        "id": row.id, "key": row.key, "name": row.name, "domain": row.domain, "kind": row.kind, "base_url": row.base_url,
        "auth_type": row.auth_type, "auth_header": row.auth_header, "secret_set": bool(row.secret), "secret_hint": connectors.mask(row.secret),
        "endpoints": spec.endpoints, "is_active": row.is_active, "last_test_at": row.last_test_at, "last_test_ok": row.last_test_ok,
        "last_test_message": row.last_test_message,
    }


def create_from_drafts(db: Session, tenant_id: int, drafts: list[dict[str, Any]]) -> list[DataConnector]:
    """Connectors the agent proposed. A draft whose key already exists is
    the same system - left as it is."""
    existing = {r.key for r in connector_rows(db, tenant_id)}
    created = []
    for d in drafts:
        if d["key"] in existing:
            continue
        row = DataConnector(
            tenant_id=tenant_id, key=d["key"], name=d["name"][:150], domain=d["domain"], kind=d["kind"],
            base_url=d.get("base_url", "")[:500], endpoints_json=json.dumps(connectors.clean_endpoints(d.get("endpoints", {}))),
        )
        db.add(row)
        created.append(row)
        existing.add(d["key"])
    db.flush()
    return created
