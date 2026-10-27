"""Phase 7 - tenant-defined custom fields on CRM entities (party, lead,
opportunity, quote, order). Values are stored polymorphically as text and
validated/coerced by data_type in crm_core/custom_field_service.py, same
approach as ProductAttributeDefinition/Value for product specs.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

CUSTOM_FIELD_DATA_TYPES = ("text", "number", "boolean", "date", "picklist", "email", "phone", "textarea")


class CustomFieldDefinition(Base):
    __tablename__ = "custom_field_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "entity_type", "key", name="uq_custom_field_definitions_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    key: Mapped[str] = mapped_column(String(60))  # snake_case, what intents/flows refer to
    label: Mapped[str] = mapped_column(String(150))
    data_type: Mapped[str] = mapped_column(String(20), default="text")
    allowed_values: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated, picklist only
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    default_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    # Page Designer: the standard fields BRT 360 ships (is_system) live here
    # too, so tenants rename, hide and extend them like their own. segment =
    # "B2B"/"B2C" when the field belongs to one segment only (None = both);
    # section/position = where new pages show it by default.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    segment: Mapped[str | None] = mapped_column(String(10), nullable=True)
    section: Mapped[str | None] = mapped_column(String(60), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    help_text: Mapped[str | None] = mapped_column(String(300), nullable=True)


class CustomFieldValue(Base):
    __tablename__ = "custom_field_values"
    __table_args__ = (UniqueConstraint("field_id", "entity_id", name="uq_custom_field_values"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    field_id: Mapped[int] = mapped_column(ForeignKey("custom_field_definitions.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)
