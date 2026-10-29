"""BIM - BRT Integration Manager (crm_core/bim_service.py).

Batch loads (CSV / JSON files) and the EAI API both require each record's
BRT_ID - reserve them first (POST /api/bim/brt-ids). Administrators only:
an integration signs in as an administrator account of its organization.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import bim_service
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.bim import BimInterface, BimJob
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/bim", tags=["bim"], dependencies=[Depends(require_roles(UserRole.admin))])


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/entities")
def entities() -> list[dict[str, Any]]:
    return bim_service.entities()


class ReserveIn(BaseModel):
    entity: str
    count: int = Field(default=1, ge=1, le=bim_service.MAX_RESERVE)


@router.post("/brt-ids")
def reserve(payload: ReserveIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """BRT_IDs for records you are about to load - each is issued once."""
    try:
        ids = bim_service.reserve_ids(db, payload.entity, payload.count)
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return {"entity": payload.entity, "brt_ids": ids}


@router.get("/template/{entity}", response_class=PlainTextResponse)
def template(entity: str, rows: int = 0, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> str:
    """A CSV to fill in: the entity's columns, and (rows > 0) that many lines with fresh BRT_IDs."""
    try:
        columns = bim_service.template_columns(db, tenant_id, entity)
        ids = bim_service.reserve_ids(db, entity, min(rows, 1000)) if rows > 0 else []
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for code in ids:
        writer.writerow([code] + [""] * (len(columns) - 1))
    return out.getvalue()


class LoadIn(BaseModel):
    entity: str | None = None
    interface_id: int | None = None
    content: str = Field(min_length=1)
    format: str = "csv"  # csv | json
    file_name: str | None = None
    dry_run: bool = False
    apply_page_rules: bool = True


def _interface(db: Session, tenant_id: int, interface_id: int | None = None, code: str | None = None) -> BimInterface | None:
    if interface_id is None and code is None:
        return None
    query = db.query(BimInterface).filter(BimInterface.tenant_id == tenant_id)
    row = query.filter(BimInterface.id == interface_id).first() if interface_id is not None else query.filter(BimInterface.code == code).first()
    if row is None:
        raise HTTPException(404, "Interface not found")
    if not row.is_active:
        raise HTTPException(400, f"Interface {row.code} is switched off")
    return row


@router.post("/load")
def load(payload: LoadIn, background: BackgroundTasks, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
         user: User = Depends(get_current_user)) -> dict[str, Any]:
    """A batch load. Every row needs its BRT_ID; bad rows are reported and skipped.
    Up to bim_service.SYNC_ROWS rows (and every dry run) answer with the result;
    a larger load answers at once with its queued job - read GET /jobs/{id} for progress."""
    interface = _interface(db, tenant_id, payload.interface_id)
    entity = interface.entity if interface else payload.entity
    if not entity:
        raise HTTPException(400, "Say which entity the file loads, or pick an interface")
    try:
        rows = bim_service.parse(payload.content, payload.format)
        if len(rows) > bim_service.SYNC_ROWS and not payload.dry_run:
            job = bim_service.new_job(db, tenant_id, entity, rows, "batch", interface, payload.file_name)
            job.status = "queued"
            db.commit()
            background.add_task(bim_service.load_in_background, job.id, tenant_id, entity, rows,
                                interface.id if interface else None, payload.apply_page_rules, user.id)
            return {**bim_service.job_out(job), "results": []}
        job, results = bim_service.run(db, tenant_id, entity, rows, "batch", interface, payload.file_name, payload.dry_run,
                                       payload.apply_page_rules, user.id)
    except (ValueError, json.JSONDecodeError, csv.Error) as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return {**bim_service.job_out(job), "results": results[:2000]}


@router.get("/jobs")
def jobs(limit: int = 50, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    rows = db.query(BimJob).filter(BimJob.tenant_id == tenant_id).order_by(BimJob.id.desc()).limit(min(limit, 200))
    return [bim_service.job_out(j) for j in rows]


@router.get("/jobs/{job_id}")
def job(job_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = db.get(BimJob, job_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Job not found")
    return bim_service.job_out(row)


# ---------------------------------------------------------------- interfaces


class InterfaceIn(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    entity: str
    mode: str = "upsert"
    mapping: dict[str, str] = {}
    description: str = ""
    is_active: bool = True


def _save_interface(db: Session, tenant_id: int, row: BimInterface, payload: InterfaceIn) -> dict[str, Any]:
    code = re.sub(r"[^A-Za-z0-9_-]", "_", payload.code).upper()
    clash = db.query(BimInterface.id).filter(BimInterface.tenant_id == tenant_id, BimInterface.code == code, BimInterface.id != (row.id or 0)).first()
    if clash:
        raise HTTPException(400, f"There is already an interface {code}")
    try:
        bim_service.spec_of(payload.entity)
    except ValueError as exc:
        raise _bad(exc)
    if payload.mode not in ("upsert", "insert"):
        raise HTTPException(400, "mode is upsert or insert")
    row.code, row.name, row.entity, row.mode = code, payload.name, payload.entity, payload.mode
    row.mapping_json = json.dumps({str(k).strip(): str(v).strip() for k, v in payload.mapping.items() if str(k).strip() and str(v).strip()})
    row.description, row.is_active = payload.description, payload.is_active
    db.commit()
    return bim_service.interface_out(row)


@router.get("/interfaces")
def interfaces(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    rows = db.query(BimInterface).filter(BimInterface.tenant_id == tenant_id).order_by(BimInterface.code)
    return [bim_service.interface_out(r) for r in rows]


@router.post("/interfaces")
def add_interface(payload: InterfaceIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = BimInterface(tenant_id=tenant_id)
    db.add(row)
    return _save_interface(db, tenant_id, row, payload)


@router.put("/interfaces/{interface_id}")
def update_interface(interface_id: int, payload: InterfaceIn, db: Session = Depends(get_db),
                     tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = db.get(BimInterface, interface_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Interface not found")
    return _save_interface(db, tenant_id, row, payload)


# ---------------------------------------------------------------- EAI


@router.post("/eai/{interface_code}")
def eai_load(interface_code: str, body: Any = Body(...), dry_run: bool = False, db: Session = Depends(get_db),
             tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Real-time load over the API: one record, a list, or {"records": [...]} -
    through a named interface (its entity, mapping and mode)."""
    interface = _interface(db, tenant_id, code=interface_code.upper())
    records = body.get("records") if isinstance(body, dict) and "records" in body else body
    records = [records] if isinstance(records, dict) else records
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise HTTPException(400, "Send a record, a list of records, or {\"records\": [...]}")
    try:
        job, results = bim_service.run(db, tenant_id, interface.entity, records, "api", interface, None, dry_run, True, user.id)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return {"job_id": job.id, "status": job.status, "inserted": job.inserted, "updated": job.updated, "failed": job.failed, "results": results}


@router.get("/records/{code}")
def record(code: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Any loadable record by its BRT_ID."""
    found = bim_service.record_by_brt_id(db, tenant_id, code)
    if found is None:
        raise HTTPException(404, "No record with that BRT_ID")
    return found
