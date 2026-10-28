"""Admin configuration surface (Phase 1 multi-tenant primitives). Each
tenant manages its own reference data here - starting with approval chain
templates, per the explicit choice that approval chains/SLA rules/product
attributes be admin-configurable via UI rather than only via a config file.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user, hash_password, require_roles
from app.config import get_settings
from app.crm_core import ai_settings_service, attribute_service, llm, sla_service, test_runner_service
from app.db import get_db
from app.models.approval import ApprovalChainTemplate, ApprovalChainTier
from app.models.attributes import AttributeConstraintRule, ProductAttributeDefinition, ProductAttributeValue
from app.models.auth import User, UserRole
from app.models.catalog import ProductSpecification
from app.models.feasibility import FeasibilityCheckType
from app.models.identity import IdentityVerificationProvider
from app.models.orchestration_policy import OrchestrationMode, OrchestrationPolicy
from app.models.ponr import PonrPolicy
from app.models.sla import SlaClock, SlaPolicy
from app.models.tenant import Tenant
from app.models.testing import TestRun
from app.schemas.admin import (
    AiSettingsOut,
    AiSettingsUpdate,
    AiTestIn,
    AiTestOut,
    ApprovalChainTemplateCreate,
    ApprovalChainTemplateOut,
    AttributeConstraintRuleCreate,
    AttributeConstraintRuleOut,
    FeasibilityCheckTypeCreate,
    FeasibilityCheckTypeOut,
    IdentityProviderCreate,
    IdentityProviderOut,
    OrchestrationPolicyCreate,
    OrchestrationPolicyOut,
    PonrPolicyCreate,
    PonrPolicyOut,
    ProductAttributeDefinitionCreate,
    ProductAttributeDefinitionOut,
    ProductAttributeValueOut,
    ProductAttributeValueSet,
    ProductSpecificationOut,
    SlaClockOut,
    SlaPolicyCreate,
    SlaPolicyOut,
    SlaSweepResultOut,
    TestRunOut,
    TestRunSummaryOut,
)
from app.schemas.auth import SecurityPolicy, UserCreate, UserOut
from app.tenancy import get_current_tenant_id

# Every route in this router is admin-only, within the caller's own
# tenant - a single router-level dependency instead of repeating the
# check on each function.
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])


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
    from app.crm_core import product_hub

    definition = db.get(ProductAttributeDefinition, definition_id)
    if definition is None or definition.tenant_id != tenant_id:
        raise HTTPException(404, "Attribute definition not found")
    used = product_hub.where_used(db, tenant_id, definition_id)
    if used:
        raise HTTPException(409, f"{definition.name} is still used by " + ", ".join(used) + " - remove it there first")
    db.delete(definition)
    db.commit()


@router.get("/attribute-definitions/usage")
def attribute_definitions_usage(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict]:
    from app.crm_core import product_hub

    return product_hub.attribute_usage(db, tenant_id)


@router.patch("/attribute-definitions/{definition_id}")
def update_attribute_definition(
    definition_id: int, payload: dict, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> dict:
    """Rename (every product using it follows), change its unit, or its library values."""
    from app.crm_core import product_config_service as pc

    definition = db.get(ProductAttributeDefinition, definition_id)
    if definition is None or definition.tenant_id != tenant_id:
        raise HTTPException(404, "Attribute definition not found")
    name = str(payload.get("name") or "").strip()
    if name and pc._key(name) != pc._key(definition.name):
        clash = [d for d in db.query(ProductAttributeDefinition).filter(ProductAttributeDefinition.tenant_id == tenant_id)
                 if pc._key(d.name) == pc._key(name)]
        if clash:
            raise HTTPException(400, f"There is already an attribute called {clash[0].name}")
        definition.name = name
    if "unit" in payload:
        definition.unit = (payload.get("unit") or "").strip() or None
    if "values" in payload:
        values = [str(v).strip() for v in payload.get("values") or [] if str(v).strip()]
        definition.allowed_values = ",".join(values) or None
    db.commit()
    return {"id": definition.id, "name": definition.name, "unit": definition.unit, "values": pc._split_values(definition.allowed_values)}


@router.post("/attribute-definitions/{definition_id}/tidy")
def tidy_attribute_definition(definition_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    from app.crm_core import product_hub

    definition = db.get(ProductAttributeDefinition, definition_id)
    if definition is None or definition.tenant_id != tenant_id:
        raise HTTPException(404, "Attribute definition not found")
    removed = product_hub.tidy_values(db, tenant_id, definition_id)
    db.commit()
    return {"removed": removed}


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


@router.get("/identity-providers", response_model=list[IdentityProviderOut])
def list_identity_providers(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[IdentityVerificationProvider]:
    return (
        db.query(IdentityVerificationProvider)
        .filter(IdentityVerificationProvider.tenant_id == tenant_id)
        .order_by(IdentityVerificationProvider.name)
        .all()
    )


@router.post("/identity-providers", response_model=IdentityProviderOut, status_code=201)
def create_identity_provider(
    payload: IdentityProviderCreate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> IdentityVerificationProvider:
    provider = IdentityVerificationProvider(tenant_id=tenant_id, name=payload.name)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


@router.patch("/identity-providers/{provider_id}/deactivate", response_model=IdentityProviderOut)
def deactivate_identity_provider(
    provider_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> IdentityVerificationProvider:
    provider = db.get(IdentityVerificationProvider, provider_id)
    if provider is None or provider.tenant_id != tenant_id:
        raise HTTPException(404, "Identity provider not found")
    provider.is_active = False
    db.commit()
    db.refresh(provider)
    return provider


@router.delete("/identity-providers/{provider_id}", status_code=204)
def delete_identity_provider(
    provider_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> None:
    provider = db.get(IdentityVerificationProvider, provider_id)
    if provider is None or provider.tenant_id != tenant_id:
        raise HTTPException(404, "Identity provider not found")
    db.delete(provider)
    db.commit()


@router.get("/ponr-policies", response_model=list[PonrPolicyOut])
def list_ponr_policies(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[PonrPolicy]:
    return db.query(PonrPolicy).filter(PonrPolicy.tenant_id == tenant_id).order_by(PonrPolicy.entity_type).all()


@router.post("/ponr-policies", response_model=PonrPolicyOut, status_code=201)
def create_ponr_policy(
    payload: PonrPolicyCreate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> PonrPolicy:
    if payload.entity_type not in ("quote", "order"):
        raise HTTPException(400, "entity_type must be 'quote' or 'order'")
    policy = PonrPolicy(tenant_id=tenant_id, entity_type=payload.entity_type, ponr_statuses=payload.ponr_statuses)
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/ponr-policies/{policy_id}/deactivate", response_model=PonrPolicyOut)
def deactivate_ponr_policy(
    policy_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> PonrPolicy:
    policy = db.get(PonrPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise HTTPException(404, "PONR policy not found")
    policy.is_active = False
    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/ponr-policies/{policy_id}", status_code=204)
def delete_ponr_policy(policy_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    policy = db.get(PonrPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise HTTPException(404, "PONR policy not found")
    db.delete(policy)
    db.commit()


@router.get("/feasibility-check-types", response_model=list[FeasibilityCheckTypeOut])
def list_feasibility_check_types(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[FeasibilityCheckType]:
    return (
        db.query(FeasibilityCheckType)
        .filter(FeasibilityCheckType.tenant_id == tenant_id)
        .order_by(FeasibilityCheckType.name)
        .all()
    )


@router.post("/feasibility-check-types", response_model=FeasibilityCheckTypeOut, status_code=201)
def create_feasibility_check_type(
    payload: FeasibilityCheckTypeCreate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> FeasibilityCheckType:
    check_type = FeasibilityCheckType(tenant_id=tenant_id, name=payload.name, resource_type=payload.resource_type)
    db.add(check_type)
    db.commit()
    db.refresh(check_type)
    return check_type


@router.patch("/feasibility-check-types/{check_type_id}/deactivate", response_model=FeasibilityCheckTypeOut)
def deactivate_feasibility_check_type(
    check_type_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> FeasibilityCheckType:
    check_type = db.get(FeasibilityCheckType, check_type_id)
    if check_type is None or check_type.tenant_id != tenant_id:
        raise HTTPException(404, "Feasibility check type not found")
    check_type.is_active = False
    db.commit()
    db.refresh(check_type)
    return check_type


@router.delete("/feasibility-check-types/{check_type_id}", status_code=204)
def delete_feasibility_check_type(
    check_type_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> None:
    check_type = db.get(FeasibilityCheckType, check_type_id)
    if check_type is None or check_type.tenant_id != tenant_id:
        raise HTTPException(404, "Feasibility check type not found")
    db.delete(check_type)
    db.commit()


@router.get("/orchestration-policies", response_model=list[OrchestrationPolicyOut])
def list_orchestration_policies(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[OrchestrationPolicy]:
    return (
        db.query(OrchestrationPolicy)
        .filter(OrchestrationPolicy.tenant_id == tenant_id)
        .order_by(OrchestrationPolicy.intent_type)
        .all()
    )


@router.post("/orchestration-policies", response_model=OrchestrationPolicyOut, status_code=201)
def create_orchestration_policy(
    payload: OrchestrationPolicyCreate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> OrchestrationPolicy:
    if payload.mode not in (OrchestrationMode.deterministic.value, OrchestrationMode.llm_planned.value):
        raise HTTPException(400, "mode must be 'deterministic' or 'llm_planned'")
    policy = OrchestrationPolicy(tenant_id=tenant_id, intent_type=payload.intent_type, mode=OrchestrationMode(payload.mode))
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/orchestration-policies/{policy_id}/deactivate", response_model=OrchestrationPolicyOut)
def deactivate_orchestration_policy(
    policy_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> OrchestrationPolicy:
    policy = db.get(OrchestrationPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise HTTPException(404, "Orchestration policy not found")
    policy.is_active = False
    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/orchestration-policies/{policy_id}", status_code=204)
def delete_orchestration_policy(
    policy_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> None:
    policy = db.get(OrchestrationPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise HTTPException(404, "Orchestration policy not found")
    db.delete(policy)
    db.commit()


def _ai_settings_out(db: Session, tenant_id: int, row) -> AiSettingsOut:
    settings = get_settings()
    target = llm.resolve(db, tenant_id)
    return AiSettingsOut(
        configured=bool(row.api_key),
        masked_api_key=ai_settings_service.mask_key(row.api_key),
        model=row.model,
        is_active=row.is_active,
        using_global_fallback=not bool(row.api_key),
        provider=row.provider or settings.default_llm_provider,
        effective=target.provider if target else "rules",
        effective_label=target.label if target else "built-in rules (no AI model)",
        platform_key_available=llm.platform_key_available(),
        local_model=row.local_model or settings.local_llm_model,
        local_live_assist=bool(row.local_live_assist),
    )


@router.get("/ai-settings", response_model=AiSettingsOut)
def get_ai_settings(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> AiSettingsOut:
    row = ai_settings_service.get_or_create(db, tenant_id)
    db.commit()
    return _ai_settings_out(db, tenant_id, row)


@router.put("/ai-settings", response_model=AiSettingsOut)
def update_ai_settings(
    payload: AiSettingsUpdate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> AiSettingsOut:
    row = ai_settings_service.get_or_create(db, tenant_id)
    if payload.provider is not None and payload.provider not in llm.PROVIDERS:
        raise HTTPException(400, f"provider must be one of: {', '.join(llm.PROVIDERS)}")
    if payload.api_key:
        row.api_key = payload.api_key
    row.model = payload.model
    row.is_active = payload.is_active
    if payload.provider is not None:
        row.provider = payload.provider
    if payload.local_model is not None:
        row.local_model = payload.local_model.strip() or None
    if payload.local_live_assist is not None:
        row.local_live_assist = payload.local_live_assist
    db.commit()
    db.refresh(row)
    return _ai_settings_out(db, tenant_id, row)


@router.get("/audit-chain/verify")
def verify_audit_chain(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """Is this workspace's audit trail as it was recorded? (app/crm_core/audit_chain.py)"""
    from app.crm_core import audit_chain

    return audit_chain.verify(db, tenant_id)


@router.get("/ai-settings/local-status")
def local_model_status() -> dict:
    """The model running on this server: reachable? which models are installed?"""
    return llm.local_status()


@router.post("/ai-settings/test", response_model=AiTestOut)
def test_ai(payload: AiTestIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> AiTestOut:
    """Ask the chosen model to understand one request - the real intent
    prompt - and report what it understood and how long it took."""
    from app.intent_engine import classifier

    provider = payload.provider or (ai_settings_service.get_or_create(db, tenant_id).provider or get_settings().default_llm_provider)
    if provider not in llm.PROVIDERS:
        raise HTTPException(400, f"provider must be one of: {', '.join(llm.PROVIDERS)}")
    started = time.perf_counter()
    if provider == "rules":
        result = classifier._classify_with_rules(payload.text, classifier.custom_intent_specs(db, tenant_id), None)
        return AiTestOut(ok=True, provider="rules", model=None, latency_ms=round((time.perf_counter() - started) * 1000),
                         intent_type=result.intent_type, entities=result.entities, rationale=result.rationale)
    if provider == "local":
        row = ai_settings_service.get_or_create(db, tenant_id)
        target = llm.local_target(payload.local_model or row.local_model, tenant_id)
    else:
        row = ai_settings_service.get_or_create(db, tenant_id)
        own_key = row.api_key if row.is_active else None
        if not own_key and not llm.platform_key_available():
            return AiTestOut(ok=False, provider="external", model=None, latency_ms=0, intent_type=None, entities={}, rationale="",
                             error="No API key - add your key above, or ask the platform owner to set OPENAI_API_KEY")
        target = llm.LlmTarget("external", (row.model if own_key else None) or get_settings().openai_model,
                               own_key or get_settings().openai_api_key or "", None, 30.0, tenant_id)
    try:
        result = classifier._classify_with_llm(payload.text, target, classifier.custom_intent_specs(db, tenant_id), None,
                                               classifier.prompt_entries(db, tenant_id))
        return AiTestOut(ok=result.intent_type is not None, provider=target.provider, model=target.model,
                         latency_ms=round((time.perf_counter() - started) * 1000), intent_type=result.intent_type,
                         entities=result.entities, rationale=result.rationale,
                         error=None if result.intent_type else "The model answered, but not with a known intent")
    except llm.LlmUnavailable as exc:
        return AiTestOut(ok=False, provider=target.provider, model=target.model, latency_ms=round((time.perf_counter() - started) * 1000),
                         intent_type=None, entities={}, rationale="", error=str(exc))


@router.get("/ai-settings/training-data")
def download_training_data(
    synthetic: bool = True, per_template: int = 12, style: str = "reasoned", db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id)
) -> StreamingResponse:
    """Fine-tuning file for the own model: this tenant's successful requests
    plus generated telecom examples, in chat JSONL (see deploy/llm/README.md).
    style=reasoned (default) teaches it to think first; style=plain = the original examples."""
    from app.crm_core import training_data

    per_template = max(1, min(per_template, 60))
    if style not in training_data.STYLES:
        raise HTTPException(400, f"style is one of {', '.join(training_data.STYLES)}")
    lines = list(training_data.to_jsonl(training_data.build(db, tenant_id, per_template, synthetic, style)))
    return StreamingResponse(
        iter(lines), media_type="application/jsonl",
        headers={"Content-Disposition": 'attachment; filename="brt360_training_data.jsonl"', "X-Example-Count": str(len(lines))},
    )


@router.delete("/ai-settings", status_code=204)
def clear_ai_settings(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    """Clears this tenant's own key - falls back to the global
    OPENAI_API_KEY (or, if that's unset too, the deterministic parser).
    """
    row = ai_settings_service.get_or_create(db, tenant_id)
    row.api_key = None
    db.commit()


@router.post("/test-runs", response_model=TestRunOut, status_code=201)
def trigger_test_run(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> TestRun:
    """Runs the real backend pytest suite as a subprocess and blocks
    until it finishes (typically ~10s). Rejected with 409 if another run
    is already in progress.
    """
    try:
        return test_runner_service.run_suite(db, tenant_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.get("/test-runs", response_model=list[TestRunSummaryOut])
def list_test_runs(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[TestRun]:
    return db.query(TestRun).filter(TestRun.tenant_id == tenant_id).order_by(TestRun.started_at.desc()).limit(20).all()


@router.get("/test-runs/{run_id}", response_model=TestRunOut)
def get_test_run(run_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> TestRun:
    run = db.get(TestRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(404, "Test run not found")
    return run


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[User]:
    return db.query(User).filter(User.tenant_id == tenant_id).order_by(User.email).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> User:
    if payload.role not in (UserRole.admin.value, UserRole.agent.value, UserRole.viewer.value):
        raise HTTPException(400, "role must be one of: admin, agent, viewer")
    if db.query(User).filter(User.email == payload.email).first() is not None:
        raise HTTPException(400, f"Email '{payload.email}' is already registered")

    user = User(
        tenant_id=tenant_id,
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name,
        role=UserRole(payload.role),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> User:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise HTTPException(404, "User not found")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------- sign-in security (app/crm_core/login_security.py)


def _tenant_user(db: Session, tenant_id: int, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise HTTPException(404, "User not found")
    return user


@router.post("/users/{user_id}/unlock", response_model=UserOut)
def unlock_user(user_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> User:
    """Lets a user locked out by wrong passwords or codes sign in again now."""
    from app.crm_core import login_security

    user = _tenant_user(db, tenant_id, user_id)
    login_security.unlock(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/reset-mfa", response_model=UserOut)
def reset_user_mfa(user_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> User:
    """Turns two-step sign-in off for a user who lost their phone and recovery
    codes; they set it up again (at their next sign-in, if the company requires it)."""
    from app.crm_core import login_security

    user = _tenant_user(db, tenant_id, user_id)
    login_security.disable(user)
    login_security.unlock(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/security", response_model=SecurityPolicy)
def get_security(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> SecurityPolicy:
    tenant = db.get(Tenant, tenant_id)
    return SecurityPolicy(mfa_required=bool(tenant and tenant.mfa_required),
                          idle_timeout_minutes=tenant.idle_timeout_minutes if tenant else None)


@router.put("/security", response_model=SecurityPolicy)
def set_security(payload: SecurityPolicy, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                 user: User = Depends(get_current_user)) -> SecurityPolicy:
    """Require two-step sign-in of everyone in the company (turning it on asks
    the admin to have it on first, so they can't lock themselves out of it),
    and the idle timeout. A field left out keeps its value."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    if payload.mfa_required and not tenant.mfa_required and not user.mfa_enabled:
        raise HTTPException(400, "Turn on two-step sign-in for your own account first (Account security)")
    if payload.idle_timeout_minutes is not None and 0 < payload.idle_timeout_minutes < 5:
        raise HTTPException(400, "The idle timeout must be at least 5 minutes (or off)")
    tenant.mfa_required = payload.mfa_required
    if "idle_timeout_minutes" in payload.model_fields_set:
        tenant.idle_timeout_minutes = payload.idle_timeout_minutes or None
    db.commit()
    return SecurityPolicy(mfa_required=tenant.mfa_required, idle_timeout_minutes=tenant.idle_timeout_minutes)
