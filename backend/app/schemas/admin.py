from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, field_validator


class TenantCreate(BaseModel):
    licensed_modules: list[str] = Field(default_factory=lambda: ["telecom_crm"])
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    vertical: str = Field(default="both")
    # The tenant's first admin user - created in the same call, so the
    # tenant is actually loggable-into the moment it's created, not an
    # account-less shell someone has to provision separately.
    admin_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    admin_password: str = Field(min_length=8)
    admin_name: str = Field(default="Admin", max_length=200)

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_slug(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class TenantOut(BaseModel):
    licensed_modules: list[str] | None = None
    id: int
    name: str
    slug: str
    vertical: str
    display_name: str | None = None
    logo_data_url: str | None = None
    brand_color: str | None = None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    """Every field optional; logo_data_url "" (or brand_color "") removes it."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    vertical: str | None = None
    display_name: str | None = Field(default=None, max_length=200)
    logo_data_url: str | None = None
    brand_color: str | None = None
    licensed_modules: list[str] | None = None


class BrandingOut(BaseModel):
    name: str
    display_name: str
    logo_data_url: str | None = None
    brand_color: str | None = None


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
    external_id: str | None = None
    id: int
    name: str
    resource_type: str
    is_active: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class OrchestrationPolicyCreate(BaseModel):
    intent_type: str = Field(min_length=1, max_length=20)  # e.g. "INT-25", or "ALL"
    mode: str = Field(min_length=1, max_length=20)  # "deterministic" | "llm_planned"


class OrchestrationPolicyOut(BaseModel):
    id: int
    intent_type: str
    mode: str
    is_active: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class AiSettingsUpdate(BaseModel):
    # Omitted or blank api_key leaves the currently-stored key unchanged -
    # this endpoint never echoes a real key back, so "leave blank to keep"
    # is the only way to update just the model without re-pasting the key.
    api_key: str | None = None
    # blank = the platform's default model (OPENAI_MODEL)
    model: str = Field(default="", max_length=100)
    is_active: bool = True
    # external / local / rules - None leaves the current choice
    provider: str | None = None
    local_model: str | None = Field(default=None, max_length=100)
    local_live_assist: bool | None = None


class AiSettingsOut(BaseModel):
    configured: bool  # whether this tenant has its own key on file
    masked_api_key: str | None
    model: str
    is_active: bool
    using_global_fallback: bool  # true if this tenant has no key of its own
    provider: str  # what the tenant chose (or the platform default)
    effective: str  # what actually answers now: external / local / rules
    effective_label: str
    platform_key_available: bool
    local_model: str
    local_live_assist: bool


class AiTestIn(BaseModel):
    provider: str | None = None  # test a choice before saving it; None = the saved one
    local_model: str | None = None
    text: str = Field(default="New lead Acme Telecom from a referral in the North, interested in Fiber 800", max_length=500)


class AiTestOut(BaseModel):
    ok: bool
    provider: str
    model: str | None
    latency_ms: int
    intent_type: str | None
    entities: dict
    rationale: str
    error: str | None = None


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
