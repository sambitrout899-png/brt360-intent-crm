"""Product configuration - designed per product, used when selling it.

Design time (per product, keyed by offering name so every version of a
product shares it - INT-02 creates new versions under the same name):
- OfferingAttribute: an attribute a product is configured with. kind
  "pricing" (chosen while selling; changes the price) or "technical" (what
  network provisioning needs; flows to the order, its work orders, the
  provisioning call and inventory). The attribute itself - name, data type,
  unit, allowed values - is a ProductAttributeDefinition, shared across
  products.
- AttributePriceImpact: what choosing a value does to the price - a flat
  monthly add-on, a percentage of the base price, or an amount per unit of
  a numeric value.
- OfferingAttributeRule: how attributes constrain each other ("if Bandwidth
  is 10 Gbps, SLA is required" / "... requires SLA 99.99%" / "... excludes
  Contract term 12 months").

Runtime - what was chosen, kept beside the records it belongs to:
QuoteItemConfig (and the price breakdown it produced), QuoteTerms (the
quote's own pricelist), OrderItemConfig. Products without a design, and
records without these rows, behave exactly as they always did.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.attributes import ProductAttributeDefinition
from app.timeutils import utcnow

ATTRIBUTE_KINDS = ("pricing", "technical")
IMPACT_TYPES = ("flat", "percent", "per_unit")
RULE_EFFECTS = ("required", "requires", "excludes")
RULE_OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "any")


class OfferingAttribute(Base):
    __tablename__ = "offering_attributes"
    __table_args__ = (UniqueConstraint("tenant_id", "offering_name", "attribute_definition_id", name="uq_offering_attributes"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    offering_name: Mapped[str] = mapped_column(String(200))
    attribute_definition_id: Mapped[int] = mapped_column(ForeignKey("product_attribute_definitions.id"))
    kind: Mapped[str] = mapped_column(String(20), default="pricing")
    # This product's choices (comma-separated); None = the attribute's own list
    allowed_values: Mapped[str | None] = mapped_column(Text, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    default_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    # Shown/asked only when another attribute of this product has a value:
    # {"attribute": "Service type", "equals": "Managed"}.
    visible_when_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    help: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    # Product modelling (models/product_model.py): the child product it
    # belongs to (None = the root); both flags may be set (an attribute can
    # change the price AND be needed by provisioning - None = from kind); a
    # numeric range (min/max in the attribute's unit); a reference to an
    # inventory object provisioning fills in (IP Address, Place, Logical
    # Device, Device Interface ...).
    component_id: Mapped[int | None] = mapped_column(ForeignKey("product_components.id"), nullable=True)
    affects_price: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    affects_provisioning: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)  # this product's unit (None = the attribute's)

    definition: Mapped[ProductAttributeDefinition] = relationship()
    price_impacts: Mapped[list["AttributePriceImpact"]] = relationship(
        back_populates="offering_attribute", cascade="all, delete-orphan", order_by="AttributePriceImpact.id"
    )


class AttributePriceImpact(Base):
    __tablename__ = "attribute_price_impacts"
    __table_args__ = (UniqueConstraint("offering_attribute_id", "value", name="uq_attribute_price_impacts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offering_attribute_id: Mapped[int] = mapped_column(ForeignKey("offering_attributes.id"))
    # The value that triggers it ("*" = any value - for per_unit on a number)
    value: Mapped[str] = mapped_column(String(200))
    impact_type: Mapped[str] = mapped_column(String(20), default="flat")
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    offering_attribute: Mapped[OfferingAttribute] = relationship(back_populates="price_impacts")


class OfferingAttributeRule(Base):
    """IF <if_attribute> <operator> <if_value> THEN <then_attribute> is
    required / must be <then_value> (requires) / must not be <then_value>
    (excludes)."""

    __tablename__ = "offering_attribute_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    offering_name: Mapped[str] = mapped_column(String(200))
    if_attribute: Mapped[str] = mapped_column(String(100))
    operator: Mapped[str] = mapped_column(String(10), default="eq")
    if_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    then_attribute: Mapped[str] = mapped_column(String(100))
    effect: Mapped[str] = mapped_column(String(20), default="required")
    then_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class QuoteTerms(Base):
    """A quote's own pricelist - it beats the opportunity's and the customer's."""

    __tablename__ = "quote_terms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    price_list_id: Mapped[int | None] = mapped_column(ForeignKey("price_lists.id"), nullable=True)


class QuoteItemConfig(Base):
    """What a quoted product was configured with and how its price was built."""

    __tablename__ = "quote_item_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_item_id: Mapped[int] = mapped_column(ForeignKey("quote_items.id"), unique=True)
    pricing_attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    technical_attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    # {"base": .., "price_list": .., "lines": [{label, amount}], "configured": ..}
    price_breakdown_json: Mapped[str] = mapped_column(Text, default="{}")


class OrderItemConfig(Base):
    """What an ordered product carries downstream: its pricing attributes
    (for billing) and technical attributes (work orders, provisioning,
    inventory)."""

    __tablename__ = "order_item_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"), unique=True)
    pricing_attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    technical_attributes_json: Mapped[str] = mapped_column(Text, default="{}")
