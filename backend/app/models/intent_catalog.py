"""Tenant intent catalog: how each intent appears to - and who may run it.

Built-in intents (INT-01..32) carry their behaviour in code; an
IntentSetting row only overrides how the tenant presents them (label,
description, category, icon), whether they are switched on, which roles
may run them, extra phrases the classifier should recognise, and default
values for their fields. No row = the built-in defaults, available to
everyone.

Custom intents (FLOW-<id>) are added from Admin -> Intents: the row holds
their field schema (fields_json) and presentation; their behaviour is the
custom_intent flow they point to, editable in the Flow Studio.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class IntentSetting(Base):
    __tablename__ = "intent_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_intent_settings_tenant_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    code: Mapped[str] = mapped_column(String(20))  # INT-05 / FLOW-12
    label: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # comma-separated AppRole ids; empty = every role
    role_ids: Mapped[str] = mapped_column(Text, default="")
    # comma-separated extra phrases the classifier recognises
    phrases: Mapped[str] = mapped_column(Text, default="")
    # JSON {field_key: default value}
    defaults_json: Mapped[str] = mapped_column(Text, default="{}")
    # custom intents only - JSON list of field specs (see intent_registry.FieldSpec)
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)
