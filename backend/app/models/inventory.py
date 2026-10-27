"""Inventory - BRT 360's own record of the network and everything it is built from.

One model holds four kinds of record, all as ITEMS of an ITEM TYPE:

  location   where things are: region, exchange, cabinet, store, customer premises
  physical   what the field touches: devices, cards, ports, splitters, fibre, ONTs, CPEs, SIMs
  logical    what the network hands out: VLANs, IP blocks, numbers, circuit and service ids
  service    a customer's service as delivered - what it runs on is its relationships

  item types      the tenant's schema: attributes (typed, required, unique, picklists),
                  which types an item may sit inside, how items are named, which SOM
                  resource type an item answers to (port, ont, vlan ...). Types inherit
                  attributes from a parent type.
  items           containment (parent: device -> card -> port; location -> device),
                  lifecycle (planned / available / reserved / in use / maintenance / retired,
                  moved only along allowed transitions), operational status, the order /
                  service / customer it is reserved or used for, reservation expiry, where
                  each attribute's value came from (manual / discovery / import) and when the
                  item was last verified against the network.
  relationships   typed links between items (connects to, depends on, uses, serves,
                  member of, protects) - what impact analysis walks.
  pools           ranges logical resources are allocated from (VLAN / number ranges, IP
                  blocks split into fixed-size subnets, id sequences), per location or
                  shared, with a utilisation threshold.
  history         every change, with who / what / why (intent, fulfillment task, import).
  reconciliation  runs of comparing what a discovery source reports with what is recorded.

The SOM technical actions of Fulfillment (design / assign / reserve / activate /
release) allocate from here - crm_core/inventory_service.py.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

ITEM_KINDS = ("location", "physical", "logical", "service")
LIFECYCLE = ("planned", "available", "reserved", "in_use", "maintenance", "retired")
# the moves an item's lifecycle may make - anything else is refused
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "planned": ("available", "in_use", "retired"),
    "available": ("reserved", "in_use", "maintenance", "retired"),
    "reserved": ("available", "in_use", "retired"),
    "in_use": ("available", "maintenance", "retired"),
    "maintenance": ("available", "in_use", "retired"),
    "retired": (),
}
OPER_STATUSES = ("up", "down", "degraded", "unknown")
# relationship type -> (forward wording, reverse wording)
RELATIONSHIPS: dict[str, tuple[str, str]] = {
    "connects_to": ("connects to", "is connected from"),
    "depends_on": ("depends on", "is needed by"),
    "uses": ("uses", "is used by"),
    "serves": ("serves", "is served by"),
    "member_of": ("is a member of", "has member"),
    "protects": ("protects", "is protected by"),
}
POOL_TYPES = ("range", "ip", "sequence")  # numeric range (VLAN, numbers) · IP subnets · formatted id sequence
# where an attribute's value came from - a higher one is never overwritten by a lower one
SOURCE_PRIORITY = {"import": 1, "discovery": 2, "fulfillment": 3, "intent": 3, "manual": 4}


class InvItemType(Base):
    __tablename__ = "inv_item_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(60))  # olt, ont, vlan - unique per tenant
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # ITEM_KINDS
    description: Mapped[str] = mapped_column(Text, default="")
    parent_code: Mapped[str] = mapped_column(String(60), default="")  # inherits its attributes
    # [{"key", "label", "type": text|number|boolean|picklist|date|ip, "required", "unique", "values": [...]}]
    attributes_json: Mapped[str] = mapped_column(Text, default="[]")
    contained_in: Mapped[str] = mapped_column(Text, default="")  # type codes it may sit inside, comma-separated (blank = anywhere)
    naming: Mapped[str] = mapped_column(String(150), default="")  # e.g. "{location}-OLT-{n:02d}" for new items
    resource_type: Mapped[str] = mapped_column(String(60), default="")  # the SOM resource it answers to: port, ont, vlan ...
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class InvItem(Base):
    __tablename__ = "inv_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("inv_item_types.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # from its type, for fast filtering
    name: Mapped[str] = mapped_column(String(200), default="")
    identifier: Mapped[str] = mapped_column(String(200), index=True)  # unique per type among items not retired
    lifecycle: Mapped[str] = mapped_column(String(20), default="available", index=True)
    oper_status: Mapped[str] = mapped_column(String(20), default="unknown")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("inv_items.id"), nullable=True, index=True)
    location_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # the nearest location above it
    pool_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # the pool a logical value came from
    serial: Mapped[str] = mapped_column(String(120), default="", index=True)
    vendor: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    sources_json: Mapped[str] = mapped_column(Text, default="{}")  # attribute -> where its value came from
    # what it is reserved / used for: held for an opportunity at Presales' feasibility check, then for the
    # order created from it (the hold moves to the order), then in use by the service
    opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    service_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    reference: Mapped[str] = mapped_column(String(120), default="")  # fulfillment order / task, intent run ...
    reserved_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class InvRelationship(Base):
    __tablename__ = "inv_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    rel_type: Mapped[str] = mapped_column(String(30))  # RELATIONSHIPS
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class InvPool(Base):
    __tablename__ = "inv_pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    type_id: Mapped[int] = mapped_column(ForeignKey("inv_item_types.id"))  # what it hands out (a logical item type)
    pool_type: Mapped[str] = mapped_column(String(20), default="range")  # POOL_TYPES
    location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # blank = shared by every location
    range_start: Mapped[str] = mapped_column(String(80), default="")  # 100 · 10.20.0.0/16 · 1
    range_end: Mapped[str] = mapped_column(String(80), default="")  # 999 · (ip: unused) · 999999
    block_size: Mapped[int] = mapped_column(Integer, default=0)  # ip: prefix length handed out (29 = /29)
    format: Mapped[str] = mapped_column(String(80), default="{n}")  # how a value reads: "{n}", "+91 80 {n:08d}", "SVC-{n:06d}"
    threshold_pct: Mapped[int] = mapped_column(Integer, default=80)  # warn when more is used
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class InvHistory(Base):
    __tablename__ = "inv_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(30))  # created, updated, lifecycle, reserved, released, related, reconciled ...
    changes_json: Mapped[str] = mapped_column(Text, default="{}")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    reference: Mapped[str] = mapped_column(String(120), default="")
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class InvReconcileRun(Base):
    __tablename__ = "inv_reconcile_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    source_name: Mapped[str] = mapped_column(String(120), default="")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    details_json: Mapped[str] = mapped_column(Text, default="[]")
    at: Mapped[dt.datetime] = mapped_column(default=utcnow)
