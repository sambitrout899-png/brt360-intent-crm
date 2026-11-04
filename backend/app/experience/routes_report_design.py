"""Admin -> Report Design: how the organization's PDF reports look (app/report_design.py).

Every organization has it, whichever modules it holds - every module produces PDFs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import report_design
from app.auth import require_roles
from app.db import get_db
from app.models.auth import UserRole
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/admin/report-design", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])


@router.get("")
def get_design(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return report_design.describe(db, tenant_id)


@router.put("")
def save_design(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db),
                tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        return report_design.save(db, tenant_id, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("")
def reset_design(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Back to the standard look (the app's logo and brand colour)."""
    return report_design.reset(db, tenant_id)


@router.post("/preview")
def preview(payload: dict[str, Any] | None = Body(None), db: Session = Depends(get_db),
            tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    """A sample report with these settings (not saved) - or the saved ones when the body is empty."""
    try:
        brand = report_design.brand(db, tenant_id, payload or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return Response(report_design.sample(brand), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="report-design-preview.pdf"'})
