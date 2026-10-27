from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
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
    days: int | None = 30, record: str | None = None, limit: int = 50, offset: int = 0,
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
) -> dict:
    """The audit trail, filtered, with counts and one readable outcome per execution."""
    from app.crm_core import audit_service

    return audit_service.feed(db, tenant_id, q=q, status=status, code=code, user_id=user_id, channel=channel,
                              days=days if days and days > 0 else None, record=record, limit=max(1, min(limit, 200)), offset=max(0, offset))


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
