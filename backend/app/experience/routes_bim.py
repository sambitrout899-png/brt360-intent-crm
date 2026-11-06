"""BIM - BRT Integration Manager (crm_core/bim_service.py).

Batch loads (CSV / JSON files) and the EAI API both require each record's
BRT_ID - reserve them first (POST /api/bim/brt-ids), or give the interface a
key field and BRT 360 issues them. Administrators only: another system calls
the gateway instead, with a key of its own (experience/routes_gateway.py).
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import bim_service
from app.db import get_db
from app.hub import mapping
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
def template(entity: str, rows: int = 0, format: str = "csv", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    """A file to fill in: the entity's columns, and (rows > 0) that many lines with fresh BRT_IDs. format=xlsx: an Excel
    workbook - a coloured header row to fill in (loaded back as it is) and a guide to every column."""
    try:
        columns = bim_service.template_columns(db, tenant_id, entity)
        ids = bim_service.reserve_ids(db, entity, min(rows, 1000)) if rows > 0 else []
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    if format == "xlsx":
        from fastapi.responses import Response

        from app.documents import MEDIA, xlsx

        spec = next((e for e in bim_service.entities() if e["key"] == entity), {"label": entity, "columns": []})
        helps = {c["name"]: c for c in spec["columns"]}
        guide = [["Column", "Required", "What to put in it"]] + [
            [c, "Yes" if helps.get(c, {}).get("required") else "", helps.get(c, {}).get("help") or "Your own field"] for c in columns]
        data = [columns] + [[code] + [""] * (len(columns) - 1) for code in ids]
        return Response(xlsx([(spec["label"][:31], data), ("Guide", guide)]), media_type=MEDIA["xlsx"],
                        headers={"Content-Disposition": f'attachment; filename="bim-{entity}-template.xlsx"'})
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
    format: str = "csv"  # csv | json | xlsx (the Excel template, base64-encoded)
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
def jobs(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    rows = db.query(BimJob).filter(BimJob.tenant_id == tenant_id).order_by(BimJob.id.desc()).limit(min(limit, 200))
    return [bim_service.job_out(j) for j in rows]


@router.get("/jobs/{job_id}")
def job(job_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = db.get(BimJob, job_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Job not found")
    return bim_service.job_out(row)


def _own_job(db: Session, tenant_id: int, job_id: int) -> BimJob:
    row = db.get(BimJob, job_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Job not found")
    return row


@router.get("/jobs/{job_id}/errors.csv", response_class=PlainTextResponse)
def job_errors(job_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    """The refused rows and why, as a file to fix and load again."""
    return PlainTextResponse(bim_service.errors_csv(_own_job(db, tenant_id, job_id)), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="bim-job-{job_id}-errors.csv"'})


class ReprocessIn(BaseModel):
    rows: list[dict[str, Any]] | None = None  # corrected rows; blank: the refused rows as they were
    dry_run: bool = False


@router.post("/jobs/{job_id}/reprocess")
def reprocess_job(job_id: int, payload: ReprocessIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                  user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Loads the refused rows again, after the data, the mapping or the record they depend on has been fixed."""
    job = _own_job(db, tenant_id, job_id)
    try:
        fresh, results = bim_service.reprocess(db, tenant_id, job, payload.rows, payload.dry_run, user.id)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return {**bim_service.job_out(fresh), "results": results[:2000]}


# ---------------------------------------------------------------- interfaces


class InterfaceIn(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    entity: str
    mode: str = "upsert"
    mapping: Any = {}  # {source: target}, or a list of rules with transforms (hub/mapping.py)
    description: str = ""
    is_active: bool = True
    ignore_unmapped: bool = False
    source_system: str = Field(default="", max_length=60)
    key_field: str = Field(default="", max_length=200)
    ack_operation_id: int | None = None
    pull_operation_id: int | None = None
    pull_every_minutes: int = Field(default=0, ge=0, le=525600)
    pull_records_path: str = Field(default="", max_length=200)


def _operation_id(db: Session, tenant_id: int, operation_id: int | None, what: str) -> int | None:
    from app.models.integration_hub import HubOperation

    if operation_id is None:
        return None
    op = db.get(HubOperation, operation_id)
    if op is None or op.tenant_id != tenant_id:
        raise HTTPException(400, f"{what}: no such operation")
    return op.id


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
    try:
        rules = mapping.valid_mapping(payload.mapping or {})
    except ValueError as exc:
        raise _bad(exc)
    if payload.pull_every_minutes and not payload.pull_operation_id:
        raise HTTPException(400, "A scheduled pull needs the operation that fetches the records")
    row.code, row.name, row.entity, row.mode = code, payload.name, payload.entity, payload.mode
    row.mapping_json = json.dumps(rules)
    row.description, row.is_active = payload.description, payload.is_active
    row.ignore_unmapped, row.key_field = payload.ignore_unmapped, payload.key_field.strip()
    row.source_system = re.sub(r"[^A-Za-z0-9_-]", "_", payload.source_system.strip()).upper()
    row.ack_operation_id = _operation_id(db, tenant_id, payload.ack_operation_id, "Acknowledgement")
    row.pull_operation_id = _operation_id(db, tenant_id, payload.pull_operation_id, "Scheduled pull")
    row.pull_every_minutes, row.pull_records_path = payload.pull_every_minutes, payload.pull_records_path.strip()
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
