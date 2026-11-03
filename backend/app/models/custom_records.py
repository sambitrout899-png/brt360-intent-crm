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
    # what the record's name is called on its page ("Survey title"); "" = Name
    name_label: Mapped[str] = mapped_column(String(100), default="", server_default="")
    # the fields the list of records shows, comma-separated keys ("" = the first few)
    list_fields: Mapped[str] = mapped_column(Text, default="", server_default="")

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
    # where the record is in its type's state model (lifecycle_service) - set on creation to the first state
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    record_type: Mapped[CustomRecordType] = relationship()


class RecordRelationship(Base):
    """A many-to-many relationship an administrator defines between two record types - custom ones or
    built-in ("Site" <-> "Contact": a site has many contacts, a contact looks after many sites).
    One-to-many links are the parent of a type, or a lookup field (data type "lookup")."""

    __tablename__ = "record_relationships"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_record_relationships_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))  # snake_case - what intents and flows call it
    from_entity_type: Mapped[str] = mapped_column(String(30))
    to_entity_type: Mapped[str] = mapped_column(String(30))
    label: Mapped[str] = mapped_column(String(100))  # what "from" calls its linked records: "Contacts"
    reverse_label: Mapped[str] = mapped_column(String(100))  # what "to" calls them: "Sites"
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class RecordLink(Base):
    __tablename__ = "record_links"
    __table_args__ = (UniqueConstraint("relationship_id", "from_id", "to_id", name="uq_record_links"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    relationship_id: Mapped[int] = mapped_column(ForeignKey("record_relationships.id"), index=True)
    from_id: Mapped[int] = mapped_column(Integer, index=True)
    to_id: Mapped[int] = mapped_column(Integer, index=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
