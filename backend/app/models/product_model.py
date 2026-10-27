"""Product modelling - hierarchies and Siebel-style product rules.

A product (its root, keyed by offering name like the rest of product
configuration) is a tree:

    Business Broadband Internet            root product
      ├─ attributes (Contention Ratio, Access Medium, Bandwidth, POP Location ...)
      ├─ CPE                               child product (optional)
      │    └─ attributes (CPE Type)
      ├─ BBI-MRC                           child product (mandatory) - the monthly charge
      └─ BBI-NRC                           child product (optional) - the one-time charge

- ProductComponent: a child product at any depth, with its cardinality
  (min/max/default quantity - mandatory = min 1) and, when it carries a
  charge, its price and whether that is monthly (recurring) or one-time.
- Attributes stay OfferingAttribute rows of the root; component_id says
  which child they belong to (None = the root).
- ProductRule:
    eligibility   - who may buy the product (segment, region, customer
                    type, account status, industry, products they own ...)
    compatibility - the product requires / excludes another product, on
                    the same quote/order or in what the customer already has
    constraint    - inside the product's configuration: when attributes /
                    child products have certain values, others are required,
                    excluded or limited ("if Access Medium is DSL then
                    Bandwidth must be at most 2048").
  Each rule keeps the plain-English text it was compiled from (AI rule
  compiler, closed JSON form - never code).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

CHARGE_TYPES = ("recurring", "one_time")
# constraint also covers recommends (soft), messages and resources (provide / consume);
# price is a price formula (crm_core/price_formula.py)
PRODUCT_RULE_TYPES = ("eligibility", "compatibility", "constraint", "price")


class ProductComponent(Base):
    __tablename__ = "product_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    offering_name: Mapped[str] = mapped_column(String(200), index=True)  # the root product
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("product_components.id"), nullable=True)  # None = under the root
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    min_qty: Mapped[int] = mapped_column(Integer, default=0)
    max_qty: Mapped[int] = mapped_column(Integer, default=1)
    default_qty: Mapped[int] = mapped_column(Integer, default=0)
    charge_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    affects_provisioning: Mapped[bool] = mapped_column(Boolean, default=False)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ProductSlotDomain(Base):
    """A relationship slot: a child product whose place is filled by a
    catalog product - one of a list, or any product of a class (so the
    catalog grows without re-modelling the parent). The chosen product's own
    price applies."""

    __tablename__ = "product_slot_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    component_id: Mapped[int] = mapped_column(ForeignKey("product_components.id"), unique=True)
    domain_type: Mapped[str] = mapped_column(String(10), default="products")  # products | class
    products: Mapped[str] = mapped_column(Text, default="")  # comma-separated offering names
    class_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_product: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ProductClass(Base):
    """A product class: products that belong to it inherit its attributes
    (designed as the pseudo-product "class:<name>"), narrowing or overriding
    them; class domains of slots admit any of its products."""

    __tablename__ = "product_classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))
    parent_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    members: Mapped[str] = mapped_column(Text, default="")  # comma-separated offering names
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ProductModelVersion(Base):
    """A released, immutable snapshot of a product's model (attributes, child
    products, slots, rules), effective from valid_from. Selling uses the
    version valid today; quotes record the version they were priced with.
    A product that was never released sells from its workspace, as before."""

    __tablename__ = "product_model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    offering_name: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="released")  # released | retired
    valid_from: Mapped[dt.datetime] = mapped_column(default=utcnow)
    snapshot_json: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    report_json: Mapped[str] = mapped_column(Text, default="{}")  # tests, regression, QA at release
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ProductModelTest(Base):
    """A model test: given these picks, expect valid / invalid, and
    optionally a price or which values an attribute may still take."""

    __tablename__ = "product_model_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    offering_name: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200))
    picks_json: Mapped[str] = mapped_column(Text, default="{}")
    expect: Mapped[str] = mapped_column(String(10), default="valid")  # valid | invalid
    expect_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    expect_domains_json: Mapped[str] = mapped_column(Text, default="{}")  # {"Bandwidth": ["..."]}
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ProductInstance(Base):
    """A product as picked on an opportunity, quote or order - an instance of
    the catalog definition, the way an object is an instance of its class.

    When the product is picked, the definition live in the catalog then (the
    released model valid that day, else the workspace - with everything it
    inherits from its class) is frozen into definition_json. Every later
    configuration step for that opportunity / quote / order works on this
    copy; the catalog definition itself is never touched, and later catalog
    changes never reach this instance unless it is explicitly refreshed.
    An instance is copied along the journey: opportunity -> quote ->
    revision -> order (source_instance_id points back)."""

    __tablename__ = "product_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    context_type: Mapped[str] = mapped_column(String(20))  # opportunity | quote | order
    context_id: Mapped[int] = mapped_column(Integer, index=True)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # quote_items / order_items row
    offering_name: Mapped[str] = mapped_column(String(200))
    offering_id: Mapped[int | None] = mapped_column(ForeignKey("product_offerings.id"), nullable=True)
    model_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # released model version (None = workspace)
    definition_json: Mapped[str] = mapped_column(Text)
    configuration_json: Mapped[str] = mapped_column(Text, default="{}")  # {"pricing": {}, "technical": {}, "quantity": n}
    price_json: Mapped[str] = mapped_column(Text, default="{}")
    source_instance_id: Mapped[int | None] = mapped_column(ForeignKey("product_instances.id"), nullable=True)
    taken_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ProductRule(Base):
    __tablename__ = "product_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    offering_name: Mapped[str] = mapped_column(String(200), index=True)
    rule_type: Mapped[str] = mapped_column(String(20))
    source_text: Mapped[str] = mapped_column(Text, default="")
    # eligibility:   {"mode": "only"|"not", "when": [cond]}  cond = {"field", "op", "value"} on the customer
    # compatibility: {"effect": "requires"|"excludes", "other": str, "scope": "quote"|"account"|"either"}
    # constraint:    {"when": [cond], "then": [{"require": name} | {"exclude": name} | cond]} on attributes / child products
    # price:         {"expression": str, "label": str, "charge": "recurring"|"one_time", "described_as"?: str}
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    message: Mapped[str] = mapped_column(String(300), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
