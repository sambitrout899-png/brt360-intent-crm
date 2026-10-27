from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.auth import require_roles
from app.models.auth import UserRole
from app.db import get_db
from app.tenancy import get_current_tenant_id
from app.crm_core import inventory_sync_service as service

router = APIRouter(prefix="/api/admin/inventory-sync", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])

class Settings(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=5, le=10080)

def output(row):
    return dict(enabled=row.enabled, interval_minutes=row.interval_minutes,
        connected=bool(service.endpoint(row.tenant_id)), last_success_at=row.last_success_at,
        last_error=row.last_error)

@router.get("")
def read(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    row = service.config(db, tenant_id)
    db.commit()
    return output(row)

@router.put("")
def save(payload: Settings, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if payload.enabled and not service.endpoint(tenant_id).startswith("https://"):
        raise HTTPException(400, "Configure the tenant inventory HTTPS endpoint on the server first.")
    row = service.config(db, tenant_id)
    row.enabled, row.interval_minutes = payload.enabled, payload.interval_minutes
    db.commit()
    return output(row)

@router.post("/run")
def run(sample: bool = False, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    try:
        result = service.sync(db, tenant_id, sample)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise HTTPException(502, "Inventory sync failed. Check endpoint, credentials and response format.")
