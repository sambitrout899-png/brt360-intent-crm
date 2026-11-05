from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.timeutils import utcnow
from app.models.catalog import ProductOffering
from app.models.intent_log import IntentExecutionLog
from app.schemas.catalog import AuditLogDetailOut, AuditLogOut, OfferingOut
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api", tags=["catalog", "audit"])


@router.get("/catalog", response_model=list[OfferingOut])
def list_catalog(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[ProductOffering]:
    return (
        db.query(ProductOffering)
        .filter(ProductOffering.tenant_id == tenant_id)
        .order_by(ProductOffering.name, ProductOffering.version.desc())
        .all()
    )


@router.get("/catalog/{offering_id}/versions", response_model=list[OfferingOut])
def offering_versions(
    offering_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[ProductOffering]:
    offering = db.get(ProductOffering, offering_id)
    if offering is None or offering.tenant_id != tenant_id:
        raise HTTPException(404, "Offering not found")
    # Walk both directions of the version chain.
    chain = [offering]
    cursor = offering
    while cursor.parent_offering_id is not None:
        cursor = db.get(ProductOffering, cursor.parent_offering_id)
        if cursor is None:
            break
        chain.append(cursor)
    child = db.query(ProductOffering).filter(ProductOffering.parent_offering_id == offering.id).first()
    while child is not None:
        chain.insert(0, child)
        child = db.query(ProductOffering).filter(ProductOffering.parent_offering_id == child.id).first()
    return sorted(chain, key=lambda o: o.version)


@router.get("/audit/feed")
def audit_feed(
    q: str | None = None, status: str | None = None, code: str | None = None, user_id: int | None = None, channel: str | None = None,
    days: int | None = 30, record: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
) -> dict:
    """The audit trail, filtered, with counts and one readable outcome per execution."""
    from app.crm_core import audit_service

    return audit_service.feed(db, tenant_id, q=q, status=status, code=code, user_id=user_id, channel=channel,
                              days=days if days and days > 0 else None, record=record, limit=max(1, min(limit, 200)), offset=max(0, offset))


@router.get("/audit/feed/export")
def audit_export(
    format: str = "pdf", q: str | None = None, status: str | None = None, code: str | None = None, user_id: int | None = None,
    channel: str | None = None, days: int | None = 30, record: str | None = None,
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
) -> Response:
    """The filtered audit trail as a PDF report (or a formatted Excel sheet) - up to 2,000 entries."""
    from app.crm_core import audit_service
    from app.documents import MEDIA, STATUS, Pdf, xlsx

    feed = audit_service.feed(db, tenant_id, q=q, status=status, code=code, user_id=user_id, channel=channel,
                              days=days if days and days > 0 else None, record=record, limit=2000, offset=0)
    items = feed.get("items") or []
    headers = ["When", "Action", "Code", "Who", "Channel", "Status", "Outcome"]
    rows = [[str(i.get("at") or "")[:16].replace("T", " "), i.get("label") or "", i.get("code") or "", (i.get("user") or {}).get("name") or "",
             i.get("channel") or "", i.get("status") or "", i.get("outcome") or ""] for i in items]
    stamp = utcnow().strftime("%Y-%m-%d")
    if format == "xlsx":
        content, media = xlsx([("Audit trail", [headers, *rows])]), MEDIA["xlsx"]
    else:
        filters = ", ".join(f"{k}: {v}" for k, v in (("search", q), ("status", status), ("action", code), ("channel", channel), ("record", record),
                                                     ("last days", days)) if v)
        from app.report_design import brand

        doc = Pdf("Audit trail", f"{len(rows)} entries · {filters or 'no filters'} · exported {stamp}", brand(db, tenant_id))
        counts = {s: sum(1 for r in rows if r[5] == s) for s in ("done", "failed", "review")}
        doc.tiles([("Entries", len(rows), None), ("Done", counts["done"], STATUS["passed"]), ("Failed", counts["failed"], STATUS["failed"]),
                   ("Review", counts["review"], STATUS["skipped"])])
        doc.table(headers, rows, widths=[1.3, 1.8, 0.8, 1.2, 0.8, 0.7, 3.2], size=7.5)
        content, media = doc.bytes("BRT 360 · Audit trail"), MEDIA["pdf"]
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="audit-trail-{stamp}.{format if format == "xlsx" else "pdf"}"'})


@router.get("/audit/{execution_id}/story")
def audit_story(execution_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """One execution, step by step, in plain words."""
    from app.crm_core import audit_service

    log = db.get(IntentExecutionLog, execution_id)
    if log is None or log.tenant_id != tenant_id:
        raise HTTPException(404, "Execution log not found")
    return audit_service.story(db, tenant_id, log)


@router.get("/audit", response_model=list[AuditLogOut])
def list_audit(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[IntentExecutionLog]:
    return (
        db.query(IntentExecutionLog)
        .filter(IntentExecutionLog.tenant_id == tenant_id)
        .order_by(IntentExecutionLog.created_at.desc())
        .limit(200)
        .all()
    )


@router.get("/audit/{execution_id}", response_model=AuditLogDetailOut)
def audit_detail(
    execution_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> IntentExecutionLog:
    log = db.get(IntentExecutionLog, execution_id)
    if log is None or log.tenant_id != tenant_id:
        raise HTTPException(404, "Execution log not found")
    return log
