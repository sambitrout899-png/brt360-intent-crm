"""Bulk orders API (orchestration/bulk_orders.py). Needs the Bulk Orders page (Admin -> Roles); creating and
moving requests needs an admin or agent. Rows run as the person who submitted the request.

  GET  /api/bulk-orders/processes        the processes, their columns and CSV headers (templates)
  GET  /api/bulk-orders                  requests, newest first
  POST /api/bulk-orders                  create (and start): {process, name, rows | csv, defaults, auto_approve, send_to_fulfillment, start}
  POST /api/bulk-orders/preview          an uploaded CSV file (the request body, text/csv): its rows checked, nothing created
  POST /api/bulk-orders/upload           an uploaded CSV file -> a bulk order (?process=&name=&file_name=&defaults=&auto_approve=&start=)
  GET  /api/bulk-orders/{id}             a request with its rows (?stage=)
  POST /api/bulk-orders/{id}/process     move it along now (place up to `limit` rows, follow the rest)
  POST /api/bulk-orders/{id}/start | /retry | /cancel
  GET  /api/bulk-orders/{id}/results.csv every row with its orders, assets, billing and stage
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.access import get_access, require_page
from app.auth import get_current_user, require_roles
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User, UserRole
from app.orchestration import bulk_orders as bulk
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/bulk-orders", tags=["bulk-orders"], dependencies=[Depends(require_page("bulk_orders"))])
_write = Depends(require_roles(UserRole.admin, UserRole.agent))
Payload = dict[str, Any]


def _do(db: Session, fn: Callable[[], Any]) -> Any:
    try:
        result = fn()
        db.commit()
        return result
    except (ValueError, TypeError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc).strip("'\"") or "That can't be done") from exc


@router.get("/processes")
def processes() -> list[Payload]:
    return bulk.processes()


@router.get("")
def listing(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    return bulk.listing(db, tenant_id)


@router.post("", dependencies=[_write])
def create(data: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
           user: User = Depends(get_current_user), access: Access = Depends(get_access)) -> Payload:
    made = _do(db, lambda: bulk.create(db, tenant_id, user, access, data))
    return bulk.detail(db, made, limit=200)


async def _file_text(request: Request) -> str:
    """The uploaded CSV file - sent as the request body (Content-Type text/csv or application/octet-stream),
    or {"csv": "..."} as JSON."""
    raw = await request.body()
    if "json" in (request.headers.get("content-type") or ""):
        import json

        try:
            return str(json.loads(raw or b"{}").get("csv") or "")
        except ValueError as exc:
            raise HTTPException(400, "Send the CSV file, or JSON {\"csv\": ...}") from exc
    try:
        return bulk.decode_file(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/preview", dependencies=[_write])
async def preview(request: Request, process: str, defaults: str = "", db: Session = Depends(get_db),
                  tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    """What an uploaded CSV file would make - row by row, before anything runs."""
    text = await _file_text(request)
    try:
        return bulk.preview(db, tenant_id, process, text, bulk._pairs(defaults))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/upload", dependencies=[_write])
async def upload(request: Request, process: str, name: str = "", file_name: str = "", defaults: str = "", auto_approve: bool = True,
                 send_to_fulfillment: bool = True, start: bool = True, db: Session = Depends(get_db),
                 tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user),
                 access: Access = Depends(get_access)) -> Payload:
    """A bulk order request from an uploaded CSV file - created, and started unless start=false (a draft to start later)."""
    text = await _file_text(request)
    title = name or (f"{file_name.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]}" if file_name else "")
    made = _do(db, lambda: bulk.create(db, tenant_id, user, access, {
        "process": process, "name": title[:200], "csv": text, "defaults": bulk._pairs(defaults), "auto_approve": auto_approve,
        "send_to_fulfillment": send_to_fulfillment, "start": start, "source": "csv"}))
    return bulk.detail(db, made, limit=200)


@router.get("/{bulk_id}")
def detail(bulk_id: int, stage: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    try:
        return bulk.detail(db, bulk.of(db, tenant_id, bulk_id), stage)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{bulk_id}/process", dependencies=[_write])
def process_now(bulk_id: int, limit: int = bulk.ROWS_PER_TICK, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    found = _do(db, lambda: bulk.of(db, tenant_id, bulk_id))
    try:
        return bulk.process(db, found.id, max(1, min(limit, 200)))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/{bulk_id}/start", dependencies=[_write])
def start(bulk_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: bulk.summary(db, bulk.start(db, bulk.of(db, tenant_id, bulk_id))))


@router.post("/{bulk_id}/retry", dependencies=[_write])
def retry(bulk_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: {"requeued": bulk.retry(db, bulk.of(db, tenant_id, bulk_id))})


@router.post("/{bulk_id}/cancel", dependencies=[_write])
def cancel(bulk_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: {"cancelled": bulk.cancel(db, bulk.of(db, tenant_id, bulk_id))})


@router.get("/{bulk_id}/results.csv", response_class=PlainTextResponse)
def results(bulk_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> PlainTextResponse:
    found = bulk.of(db, tenant_id, bulk_id)
    return PlainTextResponse(bulk.results_csv(db, found), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{found.number}-results.csv"'})
