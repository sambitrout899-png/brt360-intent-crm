"""Configurable Point of No Return (PONR).

Per the B2B BRD docs (IPLC's APPLICATION_PONR/BUSINESS_PONR flags), what's
legal to change - and what it costs - depends on exactly how far a quote
or order has progressed, not a flat "is it still a draft" check. Rather
than hardcoding which statuses are "too late to revise," that set is
admin-configurable reference data per tenant + entity_type, same
philosophy as every other Phase 1+ primitive. No policy configured for a
tenant/entity_type falls back to a sensible default (see
ponr_service.DEFAULT_PONR_STATUSES) rather than blocking everything or
nothing.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text

from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class PonrPolicy(Base):
    __tablename__ = "ponr_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    entity_type: Mapped[str] = mapped_column(String(30))  # "quote" | "order"
    # Comma-separated status values that mean PONR has been reached - once
    # the entity is in one of these statuses, revise/cancel (this kind,
    # not a terminal order-lifecycle action like INT-09) is blocked.
    ponr_statuses: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
