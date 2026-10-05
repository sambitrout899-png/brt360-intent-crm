from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    vertical: str = Field(default="both")
    # The tenant's first admin user - created in the same call, so the
    # tenant is actually loggable-into the moment it's created, not an
    # account-less shell someone has to provision separately.
    admin_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    admin_password: str = Field(min_length=8)
    admin_name: str = Field(default="Admin", max_length=200)


class TenantOut(BaseModel):
    id: int
    name: str
    slug: str
    vertical: str
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class ApprovalChainTierIn(BaseModel):
    approver_tier: str = Field(min_length=1, max_length=100)


class ApprovalChainTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    segment: str | None = None
    min_discount_pct: float = Field(ge=0, le=100)
    max_discount_pct: float = Field(ge=0, le=100)
    tiers: list[ApprovalChainTierIn] = Field(min_length=1)


class ApprovalChainTierOut(BaseModel):
    sequence: int
    approver_tier: str

    model_config = {"from_attributes": True}


class ApprovalChainTemplateOut(BaseModel):
    id: int
    name: str
    segment: str | None
    min_discount_pct: float
    max_discount_pct: float
    is_active: bool
    created_at: dt.datetime
    tiers: list[ApprovalChainTierOut]

    model_config = {"from_attributes": True}


class SlaPolicyCreate(BaseModel):
    clock_type: str = Field(min_length=1, max_length=100)
    duration_hours: float = Field(gt=0)
    on_breach_action: str = Field(min_length=1, max_length=50)


class SlaPolicyOut(BaseModel):
    id: int
    clock_type: str
    duration_hours: float
    on_breach_action: str
    is_active: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class SlaClockOut(BaseModel):
    id: int
    clock_type: str
    entity_type: str
    entity_id: int
    on_breach_action: str
    started_at: dt.datetime
    deadline: dt.datetime
    status: str
    resolved_at: dt.datetime | None
    detail: str

    model_config = {"from_attributes": True}


class SlaSweepResultOut(BaseModel):
    execution_id: int
    checked: int
    breached: list[dict]


class ProductSpecificationOut(BaseModel):
    id: int
    name: str
    product_line: str
    is_atomic: bool

    model_config = {"from_attributes": True}


class ProductAttributeDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    data_type: str = Field(min_length=1, max_length=20)
    unit: str | None = None
    allowed_values: str | None = None


class ProductAttributeDefinitionOut(BaseModel):
    id: int
    name: str
    data_type: str
    unit: str | None
    allowed_values: str | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class ProductAttributeValueSet(BaseModel):
    product_specification_id: int
    attribute_definition_id: int
    value: str


class ProductAttributeValueOut(BaseModel):
    id: int
    product_specification_id: int
    attribute_definition_id: int
    attribute_name: str
    value: str

    model_config = {"from_attributes": True}


class AttributeConstraintRuleCreate(BaseModel):
    if_product_spec_id: int
    if_attribute_id: int
    operator: str = Field(min_length=1, max_length=10)
    threshold: str
    rule_type: str = Field(min_length=1, max_length=20)
    then_product_spec_id: int


class AttributeConstraintRuleOut(BaseModel):
    id: int
    if_product_spec_id: int
    if_product_name: str
    if_attribute_id: int
    if_attribute_name: str
    operator: str
    threshold: str
    rule_type: str
    then_product_spec_id: int
    then_product_name: str
    is_active: bool
    created_at: dt.datetime


class IdentityProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class IdentityProviderOut(BaseModel):
    id: int
    name: str
    is_active: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class PonrPolicyCreate(BaseModel):
    entity_type: str = Field(min_length=1, max_length=30)  # "quote" | "order"
    ponr_statuses: str = Field(min_length=1)  # comma-separated status values


class PonrPolicyOut(BaseModel):
    id: int
    entity_type: str
    ponr_statuses: str
    is_active: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class FeasibilityCheckTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    resource_type: str = Field(min_length=1, max_length=50)  # "fiber" | "port" | "switch" | "inventory" | ...


class FeasibilityCheckTypeOut(BaseModel):
    id: int
    name: str
    resource_type: str
    is_active: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class TestRunSummaryOut(BaseModel):
    id: int
    status: str
    total: int
    passed: int
    failed: int
    errors: int
    duration_seconds: float
    started_at: dt.datetime
    completed_at: dt.datetime | None

    model_config = {"from_attributes": True}


class TestRunOut(TestRunSummaryOut):
    output: str
