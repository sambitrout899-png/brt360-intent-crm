"""Admin configuration surface (Phase 1 multi-tenant primitives). Each
tenant manages its own reference data here - starting with approval chain
templates, per the explicit choice that approval chains/SLA rules/product
attributes be admin-configurable via UI rather than only via a config file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.crm_core import attribute_service, sla_service
from app.db import get_db
from app.models.approval import ApprovalChainTemplate, ApprovalChainTier
from app.models.attributes import AttributeConstraintRule, ProductAttributeDefinition, ProductAttributeValue
from app.models.catalog import ProductSpecification
from app.models.sla import SlaClock, SlaPolicy
from app.schemas.admin import (
    ApprovalChainTemplateCreate,
    ApprovalChainTemplateOut,
    AttributeConstraintRuleCreate,
    AttributeConstraintRuleOut,
    ProductAttributeDefinitionCreate,
    ProductAttributeDefinitionOut,
    ProductAttributeValueOut,
    ProductAttributeValueSet,
    ProductSpecificationOut,
    SlaClockOut,
    SlaPolicyCreate,
    SlaPolicyOut,
    SlaSweepResultOut,
)
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


@router.get("/sla-policies", response_model=list[SlaPolicyOut])
def list_sla_policies(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[SlaPolicy]:
    return db.query(SlaPolicy).filter(SlaPolicy.tenant_id == tenant_id).order_by(SlaPolicy.clock_type).all()


@router.post("/sla-policies", response_model=SlaPolicyOut, status_code=201)
def create_sla_policy(
    payload: SlaPolicyCreate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> SlaPolicy:
    if payload.on_breach_action not in sla_service.KNOWN_BREACH_ACTIONS:
        raise HTTPException(400, f"on_breach_action must be one of {sla_service.KNOWN_BREACH_ACTIONS}")
    policy = SlaPolicy(
        tenant_id=tenant_id,
        clock_type=payload.clock_type,
        duration_hours=payload.duration_hours,
        on_breach_action=payload.on_breach_action,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/sla-policies/{policy_id}/deactivate", response_model=SlaPolicyOut)
def deactivate_sla_policy(
    policy_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> SlaPolicy:
    policy = db.get(SlaPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise HTTPException(404, "SLA policy not found")
    policy.is_active = False
    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/sla-policies/{policy_id}", status_code=204)
def delete_sla_policy(policy_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    policy = db.get(SlaPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise HTTPException(404, "SLA policy not found")
    db.delete(policy)
    db.commit()


@router.get("/sla-clocks", response_model=list[SlaClockOut])
def list_sla_clocks(
    status: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[SlaClock]:
    query = db.query(SlaClock).filter(SlaClock.tenant_id == tenant_id)
    if status:
        query = query.filter(SlaClock.status == status)
    return query.order_by(SlaClock.deadline).all()


@router.post("/sla/process-due", response_model=SlaSweepResultOut)
def process_due_sla_clocks(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """The sweep hook - point a cron/systemd timer at this endpoint. Not
    invoked automatically by any request path; clocks only breach when
    this is called.
    """
    result = sla_service.process_due_clocks(db, tenant_id)
    db.commit()
    return result


@router.get("/product-specifications", response_model=list[ProductSpecificationOut])
def list_product_specifications(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[ProductSpecification]:
    return (
        db.query(ProductSpecification)
        .filter(ProductSpecification.tenant_id == tenant_id)
        .order_by(ProductSpecification.name)
        .all()
    )


@router.get("/attribute-definitions", response_model=list[ProductAttributeDefinitionOut])
def list_attribute_definitions(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[ProductAttributeDefinition]:
    return (
        db.query(ProductAttributeDefinition)
        .filter(ProductAttributeDefinition.tenant_id == tenant_id)
        .order_by(ProductAttributeDefinition.name)
        .all()
    )


@router.post("/attribute-definitions", response_model=ProductAttributeDefinitionOut, status_code=201)
def create_attribute_definition(
    payload: ProductAttributeDefinitionCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> ProductAttributeDefinition:
    if payload.data_type not in ("number", "string", "boolean", "enum"):
        raise HTTPException(400, "data_type must be one of: number, string, boolean, enum")
    definition = ProductAttributeDefinition(
        tenant_id=tenant_id,
        name=payload.name,
        data_type=payload.data_type,
        unit=payload.unit,
        allowed_values=payload.allowed_values,
    )
    db.add(definition)
    db.commit()
    db.refresh(definition)
    return definition


@router.delete("/attribute-definitions/{definition_id}", status_code=204)
def delete_attribute_definition(
    definition_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> None:
    definition = db.get(ProductAttributeDefinition, definition_id)
    if definition is None or definition.tenant_id != tenant_id:
        raise HTTPException(404, "Attribute definition not found")
    db.delete(definition)
    db.commit()


def _owned_spec(db: Session, tenant_id: int, spec_id: int) -> ProductSpecification:
    spec = db.get(ProductSpecification, spec_id)
    if spec is None or spec.tenant_id != tenant_id:
        raise HTTPException(404, f"Product specification {spec_id} not found")
    return spec


def _owned_attribute(db: Session, tenant_id: int, attribute_id: int) -> ProductAttributeDefinition:
    attribute = db.get(ProductAttributeDefinition, attribute_id)
    if attribute is None or attribute.tenant_id != tenant_id:
        raise HTTPException(404, f"Attribute definition {attribute_id} not found")
    return attribute


@router.get("/attribute-values", response_model=list[ProductAttributeValueOut])
def list_attribute_values(
    product_specification_id: int | None = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> list[dict]:
    query = (
        db.query(ProductAttributeValue, ProductAttributeDefinition)
        .join(ProductAttributeDefinition, ProductAttributeValue.attribute_definition_id == ProductAttributeDefinition.id)
        .filter(ProductAttributeDefinition.tenant_id == tenant_id)
    )
    if product_specification_id is not None:
        query = query.filter(ProductAttributeValue.product_specification_id == product_specification_id)
    rows = query.all()
    return [
        {
            "id": value.id,
            "product_specification_id": value.product_specification_id,
            "attribute_definition_id": value.attribute_definition_id,
            "attribute_name": defn.name,
            "value": value.value,
        }
        for value, defn in rows
    ]


@router.put("/attribute-values", response_model=ProductAttributeValueOut)
def set_attribute_value(
    payload: ProductAttributeValueSet, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict:
    _owned_spec(db, tenant_id, payload.product_specification_id)
    attribute = _owned_attribute(db, tenant_id, payload.attribute_definition_id)
    row = attribute_service.set_attribute_value(
        db, payload.product_specification_id, payload.attribute_definition_id, payload.value
    )
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "product_specification_id": row.product_specification_id,
        "attribute_definition_id": row.attribute_definition_id,
        "attribute_name": attribute.name,
        "value": row.value,
    }


@router.get("/attribute-constraint-rules", response_model=list[AttributeConstraintRuleOut])
def list_attribute_constraint_rules(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[dict]:
    rules = db.query(AttributeConstraintRule).filter(AttributeConstraintRule.tenant_id == tenant_id).all()
    out = []
    for rule in rules:
        if_spec = db.get(ProductSpecification, rule.if_product_spec_id)
        if_attr = db.get(ProductAttributeDefinition, rule.if_attribute_id)
        then_spec = db.get(ProductSpecification, rule.then_product_spec_id)
        out.append(
            {
                "id": rule.id,
                "if_product_spec_id": rule.if_product_spec_id,
                "if_product_name": if_spec.name if if_spec else f"#{rule.if_product_spec_id}",
                "if_attribute_id": rule.if_attribute_id,
                "if_attribute_name": if_attr.name if if_attr else f"#{rule.if_attribute_id}",
                "operator": rule.operator,
                "threshold": rule.threshold,
                "rule_type": rule.rule_type,
                "then_product_spec_id": rule.then_product_spec_id,
                "then_product_name": then_spec.name if then_spec else f"#{rule.then_product_spec_id}",
                "is_active": rule.is_active,
                "created_at": rule.created_at,
            }
        )
    return out


@router.post("/attribute-constraint-rules", response_model=AttributeConstraintRuleOut, status_code=201)
def create_attribute_constraint_rule(
    payload: AttributeConstraintRuleCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> dict:
    if payload.operator not in ("eq", "ne", "gt", "gte", "lt", "lte"):
        raise HTTPException(400, "operator must be one of: eq, ne, gt, gte, lt, lte")
    if payload.rule_type not in ("requires", "excludes"):
        raise HTTPException(400, "rule_type must be 'requires' or 'excludes'")
    if_spec = _owned_spec(db, tenant_id, payload.if_product_spec_id)
    if_attr = _owned_attribute(db, tenant_id, payload.if_attribute_id)
    then_spec = _owned_spec(db, tenant_id, payload.then_product_spec_id)

    rule = AttributeConstraintRule(
        tenant_id=tenant_id,
        if_product_spec_id=payload.if_product_spec_id,
        if_attribute_id=payload.if_attribute_id,
        operator=payload.operator,
        threshold=payload.threshold,
        rule_type=payload.rule_type,
        then_product_spec_id=payload.then_product_spec_id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {
        "id": rule.id,
        "if_product_spec_id": rule.if_product_spec_id,
        "if_product_name": if_spec.name,
        "if_attribute_id": rule.if_attribute_id,
        "if_attribute_name": if_attr.name,
        "operator": rule.operator,
        "threshold": rule.threshold,
        "rule_type": rule.rule_type,
        "then_product_spec_id": rule.then_product_spec_id,
        "then_product_name": then_spec.name,
        "is_active": rule.is_active,
        "created_at": rule.created_at,
    }


@router.patch("/attribute-constraint-rules/{rule_id}/deactivate", status_code=204)
def deactivate_attribute_constraint_rule(
    rule_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> None:
    rule = db.get(AttributeConstraintRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise HTTPException(404, "Constraint rule not found")
    rule.is_active = False
    db.commit()


@router.delete("/attribute-constraint-rules/{rule_id}", status_code=204)
def delete_attribute_constraint_rule(
    rule_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> None:
    rule = db.get(AttributeConstraintRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise HTTPException(404, "Constraint rule not found")
    db.delete(rule)
    db.commit()
