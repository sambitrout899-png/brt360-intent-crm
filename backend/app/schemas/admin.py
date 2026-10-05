from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


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
