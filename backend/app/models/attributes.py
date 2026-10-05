"""Typed product attributes + constraint rules (Phase 1 primitive #4).

Per the B2C/B2B BRD research, a product like "FTTH PS" carries typed
attributes (Download/Upload Speed, Contention Ratio, ...), not just a
name - and admin-configured requires/excludes rules can key off those
values (e.g. a 1:1 contention ratio requiring a specific support add-on).
This extends ProductSpecification; ProductOffering.composition (see
app/models/catalog.py) stays the simple comma-separated list of product
NAMES it always was. These rules run inside
catalog_service.validate_composition as a second constraint layer,
alongside the existing line-level BundleCompatibilityRule check - that one
keys off product-line categories, this one off specific attribute values.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class ProductAttributeDefinition(Base):
    __tablename__ = "product_attribute_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))  # e.g. "Download Speed"
    data_type: Mapped[str] = mapped_column(String(20))  # "number" | "string" | "boolean" | "enum"
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)  # e.g. "Mbps"
    allowed_values: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated, for "enum"
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ProductAttributeValue(Base):
    """One (product spec, attribute) -> raw value. Coerced to its typed
    form (number/bool/string) by attribute_service, per the definition's
    data_type - kept as a plain string column here, same MVP-simple
    convention ProductOffering.composition already uses for its list.
    """

    __tablename__ = "product_attribute_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_specification_id: Mapped[int] = mapped_column(ForeignKey("product_specifications.id"))
    attribute_definition_id: Mapped[int] = mapped_column(ForeignKey("product_attribute_definitions.id"))
    value: Mapped[str] = mapped_column(String(200))

    attribute_definition: Mapped[ProductAttributeDefinition] = relationship()


class AttributeConstraintRule(Base):
    """IF if_product_spec's if_attribute value {operator} threshold, THEN
    then_product_spec is required/excluded in the same bundle composition.
    """

    __tablename__ = "attribute_constraint_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    if_product_spec_id: Mapped[int] = mapped_column(ForeignKey("product_specifications.id"))
    if_attribute_id: Mapped[int] = mapped_column(ForeignKey("product_attribute_definitions.id"))
    operator: Mapped[str] = mapped_column(String(10))  # "eq" | "ne" | "gt" | "gte" | "lt" | "lte"
    threshold: Mapped[str] = mapped_column(String(200))
    rule_type: Mapped[str] = mapped_column(String(20))  # "requires" | "excludes"
    then_product_spec_id: Mapped[int] = mapped_column(ForeignKey("product_specifications.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
