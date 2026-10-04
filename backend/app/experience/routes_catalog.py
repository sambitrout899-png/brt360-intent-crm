from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.catalog import ProductOffering
from app.models.intent_log import IntentExecutionLog
from app.schemas.catalog import AuditLogDetailOut, AuditLogOut, OfferingOut

router = APIRouter(prefix="/api", tags=["catalog", "audit"])


@router.get("/catalog", response_model=list[OfferingOut])
def list_catalog(db: Session = Depends(get_db)) -> list[ProductOffering]:
    return db.query(ProductOffering).order_by(ProductOffering.name, ProductOffering.version.desc()).all()


@router.get("/catalog/{offering_id}/versions", response_model=list[OfferingOut])
def offering_versions(offering_id: int, db: Session = Depends(get_db)) -> list[ProductOffering]:
    offering = db.get(ProductOffering, offering_id)
    if offering is None:
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


@router.get("/audit", response_model=list[AuditLogOut])
def list_audit(db: Session = Depends(get_db)) -> list[IntentExecutionLog]:
    return db.query(IntentExecutionLog).order_by(IntentExecutionLog.created_at.desc()).limit(200).all()


@router.get("/audit/{execution_id}", response_model=AuditLogDetailOut)
def audit_detail(execution_id: int, db: Session = Depends(get_db)) -> IntentExecutionLog:
    log = db.get(IntentExecutionLog, execution_id)
    if log is None:
        raise HTTPException(404, "Execution log not found")
    return log
