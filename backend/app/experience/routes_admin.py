"""Admin configuration surface (Phase 1 multi-tenant primitives). Each
tenant manages its own reference data here - starting with approval chain
templates, per the explicit choice that approval chains/SLA rules/product
attributes be admin-configurable via UI rather than only via a config file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.approval import ApprovalChainTemplate, ApprovalChainTier
from app.schemas.admin import ApprovalChainTemplateCreate, ApprovalChainTemplateOut
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/approval-chains", response_model=list[ApprovalChainTemplateOut])
def list_approval_chains(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[ApprovalChainTemplate]:
    return (
        db.query(ApprovalChainTemplate)
        .filter(ApprovalChainTemplate.tenant_id == tenant_id)
        .order_by(ApprovalChainTemplate.min_discount_pct)
        .all()
    )


@router.post("/approval-chains", response_model=ApprovalChainTemplateOut, status_code=201)
def create_approval_chain(
    payload: ApprovalChainTemplateCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> ApprovalChainTemplate:
    if payload.max_discount_pct < payload.min_discount_pct:
        raise HTTPException(400, "max_discount_pct must be >= min_discount_pct")

    template = ApprovalChainTemplate(
        tenant_id=tenant_id,
        name=payload.name,
        segment=payload.segment,
        min_discount_pct=payload.min_discount_pct,
        max_discount_pct=payload.max_discount_pct,
    )
    db.add(template)
    db.flush()
    for sequence, tier in enumerate(payload.tiers, start=1):
        db.add(ApprovalChainTier(template_id=template.id, sequence=sequence, approver_tier=tier.approver_tier))
    db.commit()
    db.refresh(template)
    return template


@router.patch("/approval-chains/{template_id}/deactivate", response_model=ApprovalChainTemplateOut)
def deactivate_approval_chain(
    template_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> ApprovalChainTemplate:
    template = db.get(ApprovalChainTemplate, template_id)
    if template is None or template.tenant_id != tenant_id:
        raise HTTPException(404, "Approval chain template not found")
    template.is_active = False
    db.commit()
    db.refresh(template)
    return template


@router.delete("/approval-chains/{template_id}", status_code=204)
def delete_approval_chain(
    template_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> None:
    template = db.get(ApprovalChainTemplate, template_id)
    if template is None or template.tenant_id != tenant_id:
        raise HTTPException(404, "Approval chain template not found")
    db.delete(template)
    db.commit()
