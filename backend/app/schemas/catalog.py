from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class OfferingOut(BaseModel):
    id: int
    name: str
    segment: str
    status: str
    version: int
    price: float
    price_floor: float
    composition: str
    effective_date: dt.datetime
    end_date: dt.datetime | None
    parent_offering_id: int | None
    replacement_offering_id: int | None

    model_config = {"from_attributes": True}


class AuditLogOut(BaseModel):
    id: int
    intent_type: str
    raw_text: str
    confidence: float
    status: str
    rationale: str
    created_at: dt.datetime
    completed_at: dt.datetime | None

    model_config = {"from_attributes": True}


class AuditLogDetailOut(AuditLogOut):
    entities_json: str
    context_json: str
    plan_json: str
    human_override_json: str | None
