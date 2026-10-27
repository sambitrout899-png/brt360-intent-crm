"""Tenant-defined record types ("custom objects") and their records.

A CustomRecordType is what an administrator creates in Admin -> Record
Types (e.g. "Site Survey"); a CustomRecord is one row of it. The record
itself only holds what every type has - a number, a name and an optional
link to a parent record (a customer, an order, or another custom record);
every other field is a CustomFieldDefinition with entity_type
"x_<key>" and its values live in custom_field_values, exactly like the
extra fields of the built-in records. See crm_core/custom_record_service.py.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow

# custom field / lifecycle tables keep entity_type in a String(30)
ENTITY_PREFIX = "x_"
KEY_MAX_LENGTH = 30 - len(ENTITY_PREFIX)


class CustomRecordType(Base):
    __tablename__ = "custom_record_types"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_custom_record_types_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH))  # snake_case, never changes once created
    label: Mapped[str] = mapped_column(String(100))  # "Site Survey"
    plural_label: Mapped[str] = mapped_column(String(100))  # "Site Surveys"
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(10), default="📄")
    number_prefix: Mapped[str] = mapped_column(String(10))  # "SS" -> SS-0001
    next_number: Mapped[int] = mapped_column(Integer, default=1)
    # what a record hangs off: a built-in type ("party", "order", ...) or
    # another custom type ("x_site"); None = stands alone
    parent_entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    parent_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    @property
    def entity_type(self) -> str:
        return f"{ENTITY_PREFIX}{self.key}"


class CustomRecord(Base):
    __tablename__ = "custom_records"
    __table_args__ = (UniqueConstraint("type_id", "number", name="uq_custom_records_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("custom_record_types.id"), index=True)
    number: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    record_type: Mapped[CustomRecordType] = relationship()
