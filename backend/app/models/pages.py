"""Page Designer: how a record's page/form looks for each segment, the
validation rules that guard it, and the products a lead is interested in.

- PageLayout: the sections and fields of a page for an entity (party/
  prospect, lead, opportunity, quote, order) and segment ("B2B", "B2C" or
  None = both). Fields are core fields or field definitions
  (custom_field_definitions - the built-in "standard" fields are system
  definitions there too, so tenants rename, hide and extend them alike).
  Mandatory/optional/hidden/read-only is set per page, so the same field
  can be mandatory for B2B and optional for B2C. A tenant may add pages of
  its own (is_default False) - e.g. "Enterprise onboarding".
- ValidationRule: a simple rule, compiled from plain English by the AI
  rule compiler into a small closed JSON form (never code), or a complex
  one delegated to a Flow Studio flow (trigger_type "validation") whose
  Reject node blocks the save with its message.
- LeadProduct: products the Account Manager records on a lead, from the
  products sold to the prospect's segment.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.timeutils import utcnow
from app.db import Base


class PageLayout(Base):
    __tablename__ = "page_layouts"
    __table_args__ = (UniqueConstraint("tenant_id", "page_key", name="uq_page_layouts_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    page_key: Mapped[str] = mapped_column(String(80))  # e.g. "party.B2B", or a tenant page "party.enterprise_onboarding"
    entity_type: Mapped[str] = mapped_column(String(30))
    segment: Mapped[str | None] = mapped_column(String(10), nullable=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    # {"sections": [{"title": str, "columns": 1|2, "fields": [{"key": str, "mandatory": bool, "hidden": bool,
    #   "readonly": bool, "label": str|None, "help": str|None}]}]}
    layout_json: Mapped[str] = mapped_column(Text, default='{"sections": []}')
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ValidationRule(Base):
    __tablename__ = "validation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    segment: Mapped[str | None] = mapped_column(String(10), nullable=True)  # None = both
    name: Mapped[str] = mapped_column(String(150))
    # What the admin wrote - the rule compiler's input, kept so it can be re-read and re-compiled.
    source_text: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(10), default="simple")  # simple | flow
    # simple: {"when": [cond...], "require": [field...], "check": [cond...], "message": str}
    #   cond = {"field": str, "op": eq|ne|gt|gte|lt|lte|in|not_in|empty|not_empty|matches|length|before_today|after_today, "value": any}
    compiled_json: Mapped[str] = mapped_column(Text, default="{}")
    flow_id: Mapped[int | None] = mapped_column(ForeignKey("flow_definitions.id"), nullable=True)
    message: Mapped[str] = mapped_column(String(300), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class LeadProduct(Base):
    __tablename__ = "lead_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    offering_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
