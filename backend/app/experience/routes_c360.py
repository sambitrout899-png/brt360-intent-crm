"""Customer 360 API.

Everyone in the tenant:
    GET  /api/c360/layout                    the live layout (theme + panes)
    GET  /api/c360/parties/{id}/view         one customer, every widget computed

Tenant admins ("Configure 360" studio - the INT-33 intent does the same
through the intent pipeline):
    GET  /api/admin/c360                     layout, catalog, connectors, history
    POST /api/admin/c360/agent               plain words -> proposed layout (not saved)
    POST /api/admin/c360/preview/{party_id}  render an unsaved layout for one customer
    PUT  /api/admin/c360/layout              save (and create proposed connectors)
    POST /api/admin/c360/undo | restore/{v}  history
    CRUD /api/admin/c360/connectors          external systems; /test and /map
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.access import get_access
from app.auth import get_current_user, require_roles
from app.c360 import agent, catalog, connectors, data, service
from app.c360.connectors import ConnectorError
from app.crm_core import access_service, llm
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.c360 import DataConnector
from app.models.crm import Party
from app.models.tenant import Tenant
from app.tenancy import get_current_tenant_id
from app.timeutils import utcnow

router = APIRouter(prefix="/api/c360", tags=["customer-360"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/admin/c360", tags=["customer-360-admin"], dependencies=[Depends(require_roles(UserRole.admin))])


class AgentIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    layout: dict[str, Any] | None = None  # the studio's unsaved draft; default = live layout


class PreviewIn(BaseModel):
    layout: dict[str, Any]
    connectors: list[dict[str, Any]] = []


class SaveIn(BaseModel):
    layout: dict[str, Any]
    summary: str = Field(default="Edited in the Configure 360 studio", max_length=2000)
    connectors: list[dict[str, Any]] = []


class ConnectorIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    domain: str
    kind: str = "rest"
    base_url: str = Field(default="", max_length=500)
    auth_type: str = "none"
    auth_header: str = Field(default="", max_length=100)
    secret: str | None = Field(default=None, max_length=4000)  # None = keep the stored one
    endpoints: dict[str, Any] = {}
    is_active: bool = True


class TestIn(BaseModel):
    party_id: int | None = None
    dataset: str | None = None


class MapIn(BaseModel):
    dataset: str
    sample: Any


def _party(db: Session, tenant_id: int, party_id: int, access: Access | None) -> Party:
    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id or (access is not None and not access_service.can_see(db, access, "party", party_id)):
        raise HTTPException(404, "Customer not found")
    return party


def _brand(db: Session, tenant_id: int) -> str | None:
    tenant = db.get(Tenant, tenant_id)
    return tenant.brand_color if tenant else None


def _draft_specs(drafts: list[dict[str, Any]]) -> list[connectors.ConnectorSpec]:
    out = []
    for d in drafts:
        if isinstance(d, dict) and d.get("domain") in connectors.DOMAIN_DATASETS and d.get("key"):
            out.append(connectors.ConnectorSpec(
                key=connectors.slug(str(d["key"])), name=str(d.get("name") or d["key"])[:150], domain=d["domain"],
                kind=d.get("kind") if d.get("kind") in connectors.KINDS else "demo", base_url=str(d.get("base_url") or ""),
                endpoints=connectors.clean_endpoints(d.get("endpoints") or {}),
            ))
    return out


# ---------------------------------------------------------------- everyone


@router.get("/layout")
def get_layout(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    layout, version = service.current(db, tenant_id)
    return {"layout": layout, "version": version, "themes": catalog.THEMES}


@router.get("/parties/{party_id}/view")
def party_view(
    party_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)
) -> dict[str, Any]:
    party = _party(db, tenant_id, party_id, access)
    layout, version = service.current(db, tenant_id)
    view = data.build_view(db, tenant_id, access, party, layout, service.specs(db, tenant_id))
    view["version"] = version
    return view


@router.get("/parties/{party_id}/network")
def party_network(
    party_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)
) -> dict[str, Any]:
    """The customer's network from Network Inventory: each service with the path and resources it runs on, the
    elements serving the customer, their devices, and what is at risk - for the orders this caller may see."""
    party = _party(db, tenant_id, party_id, access)
    internal = data.InternalData(db, tenant_id, access, party)
    return internal.network()


# ---------------------------------------------------------------- admin: layout


@admin_router.get("")
def studio(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    layout, version = service.current(db, tenant_id)
    target = llm.resolve(db, tenant_id)
    specs = service.specs(db, tenant_id)
    return {
        "layout": layout,
        "version": version,
        "catalog": catalog.public_catalog(),
        "connectors": [service.connector_out(r) for r in service.connector_rows(db, tenant_id)],
        "history": service.history(db, tenant_id),
        "suggestions": agent.suggest(layout, specs),
        "ai_engine": "ai" if target else "rules",
        "ai_label": target.label if target else "built-in rules",
        "brand_color": _brand(db, tenant_id),
        "template_layouts": {key: catalog.template_layout(key, theme=layout["theme"]) for key in catalog.TEMPLATES},
        "recipe_widgets": {rid: r.widget() for rid, r in catalog.RECIPES.items()},
        "domain_datasets": connectors.DOMAIN_DATASETS,
        "default_paths": connectors.DEFAULT_PATHS,
    }


@admin_router.post("/agent")
def run_agent(payload: AgentIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    live, _ = service.current(db, tenant_id)
    base = catalog.normalize_layout(payload.layout) if payload.layout else live
    result = agent.plan(payload.instruction, base, service.specs(db, tenant_id), llm.resolve(db, tenant_id), _brand(db, tenant_id))
    if result["undo"]:
        prev = service.previous(db, tenant_id)
        if prev is None:
            result.update(changes=[], understood=False, reply="There is no earlier version to go back to.")
        else:
            result["layout"] = prev
    return result


@admin_router.post("/preview/{party_id}")
def preview(
    party_id: int, payload: PreviewIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
    access: Access = Depends(get_access),
) -> dict[str, Any]:
    party = _party(db, tenant_id, party_id, access)
    layout = catalog.normalize_layout(payload.layout)
    existing = service.specs(db, tenant_id)
    drafts = [d for d in _draft_specs(payload.connectors) if d.key not in {s.key for s in existing}]
    return data.build_view(db, tenant_id, access, party, layout, existing + drafts)


@admin_router.put("/layout")
def save_layout(
    payload: SaveIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)
) -> dict[str, Any]:
    created = service.create_from_drafts(db, tenant_id, [
        {"key": s.key, "name": s.name, "domain": s.domain, "kind": s.kind, "base_url": s.base_url, "endpoints": s.endpoints}
        for s in _draft_specs(payload.connectors)
    ])
    version = service.save(db, tenant_id, payload.layout, payload.summary, user.email)
    db.commit()
    layout, _ = service.current(db, tenant_id)
    return {"layout": layout, "version": version, "connectors_created": [c.name for c in created]}


@admin_router.post("/undo")
def undo(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> dict[str, Any]:
    prev = service.previous(db, tenant_id)
    if prev is None:
        raise HTTPException(400, "Nothing to undo yet")
    version = service.save(db, tenant_id, prev, "Undo", user.email)
    db.commit()
    return {"layout": service.current(db, tenant_id)[0], "version": version}


@admin_router.post("/restore/{version}")
def restore(
    version: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)
) -> dict[str, Any]:
    old = service.version_layout(db, tenant_id, version)
    if old is None:
        raise HTTPException(404, "No such version")
    new_version = service.save(db, tenant_id, old, f"Restored version {version}", user.email)
    db.commit()
    return {"layout": service.current(db, tenant_id)[0], "version": new_version}


# ---------------------------------------------------------------- admin: connectors


def _connector(db: Session, tenant_id: int, connector_id: int) -> DataConnector:
    row = db.get(DataConnector, connector_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Data source not found")
    return row


def _apply(row: DataConnector, payload: ConnectorIn) -> None:
    if payload.domain not in connectors.DOMAIN_DATASETS:
        raise HTTPException(400, f"domain must be one of: {', '.join(connectors.DOMAIN_DATASETS)}")
    if payload.kind not in connectors.KINDS:
        raise HTTPException(400, "kind must be rest or demo")
    if payload.auth_type not in connectors.AUTH_TYPES:
        raise HTTPException(400, f"auth_type must be one of: {', '.join(connectors.AUTH_TYPES)}")
    if payload.kind == "rest" and payload.base_url and not payload.base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "The address must start with http:// or https://")
    endpoints = connectors.clean_endpoints(payload.endpoints)
    if not endpoints:
        endpoints = connectors.demo_endpoints(payload.domain) if payload.kind == "demo" else {
            d: {"path": connectors.DEFAULT_PATHS[d], "records_path": "", "field_map": {}} for d in connectors.DOMAIN_DATASETS[payload.domain]
        }
    row.name = payload.name.strip()
    row.domain = payload.domain
    row.kind = payload.kind
    row.base_url = payload.base_url.strip()
    row.auth_type = payload.auth_type
    row.auth_header = payload.auth_header.strip()
    if payload.secret is not None:
        row.secret = payload.secret
    row.endpoints_json = json.dumps(endpoints)
    row.is_active = payload.is_active


@admin_router.get("/connectors")
def list_connectors(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [service.connector_out(r) for r in service.connector_rows(db, tenant_id)]


@admin_router.post("/connectors", status_code=201)
def create_connector(payload: ConnectorIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    taken = {r.key for r in service.connector_rows(db, tenant_id)}
    key = base = connectors.slug(payload.name)
    n = 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    row = DataConnector(tenant_id=tenant_id, key=key)
    _apply(row, payload)
    db.add(row)
    db.commit()
    return service.connector_out(row)


@admin_router.put("/connectors/{connector_id}")
def update_connector(
    connector_id: int, payload: ConnectorIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    row = _connector(db, tenant_id, connector_id)
    _apply(row, payload)
    db.commit()
    return service.connector_out(row)


@admin_router.delete("/connectors/{connector_id}", status_code=204)
def delete_connector(connector_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    db.delete(_connector(db, tenant_id, connector_id))
    db.commit()


@admin_router.post("/connectors/{connector_id}/test")
def test_connector(
    connector_id: int, payload: TestIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    """Call the system for one customer and one dataset, show what came back,
    and propose how its fields map onto ours."""
    row = _connector(db, tenant_id, connector_id)
    spec = connectors.spec_from_model(row)
    dataset = payload.dataset or next(iter(spec.endpoints), None)
    if dataset not in spec.endpoints:
        raise HTTPException(400, "Pick one of the datasets this system provides")
    party = db.get(Party, payload.party_id) if payload.party_id else db.query(Party).filter(Party.tenant_id == tenant_id).order_by(Party.id).first()
    if party is None or party.tenant_id != tenant_id:
        raise HTTPException(400, "Add a customer first - the test asks the system for one customer's data")
    party_d = {"id": party.id, "name": party.name, "email": party.email}
    row.last_test_at = utcnow()
    try:
        raw, records = connectors.sample_raw(spec, dataset, party_d)
    except ConnectorError as exc:
        row.last_test_ok, row.last_test_message = False, str(exc)
        db.commit()
        return {"ok": False, "message": str(exc), "dataset": dataset, "customer": party.name}
    except Exception as exc:  # noqa: BLE001 - report, don't 500
        row.last_test_ok, row.last_test_message = False, f"Could not reach the system: {type(exc).__name__}"
        db.commit()
        return {"ok": False, "message": row.last_test_message, "dataset": dataset, "customer": party.name}
    suggestion = connectors.suggest_mapping(dataset, raw, llm.resolve(db, tenant_id)) if row.kind == "rest" else None
    cfg = spec.endpoints[dataset]
    mapped = [connectors.map_record(r, dataset, cfg.get("field_map", {})) for r in records[:5]]
    row.last_test_ok = True
    row.last_test_message = f"{len(records)} record{'s' if len(records) != 1 else ''} for {party.name}"
    db.commit()
    return {
        "ok": True, "message": row.last_test_message, "dataset": dataset, "customer": party.name,
        "records_found": len(records), "preview": mapped, "raw_sample": records[0] if records else raw, "suggestion": suggestion,
    }


@admin_router.post("/connectors/map")
def map_fields(payload: MapIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Paste one answer from the external system - get the field mapping."""
    if payload.dataset not in catalog.DATASETS:
        raise HTTPException(400, "Unknown dataset")
    sample = payload.sample
    if isinstance(sample, str):
        try:
            sample = json.loads(sample)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"That isn't valid JSON: {exc.msg} (line {exc.lineno})") from exc
    return connectors.suggest_mapping(payload.dataset, sample, llm.resolve(db, tenant_id))
