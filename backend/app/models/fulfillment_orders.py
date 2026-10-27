"""Fulfillment - layered order orchestration on eTOM fundamentals.

A tenant admin designs a FULFILLMENT PATTERN (the orchestration plan
template) on a three-lane canvas - one lane per order layer:

  COM  Customer Order Management   eTOM 1.1.1 CRM - Order Handling, Billing &
                                   Collections Management: receives the CRM
                                   order, registers the customer / billing
                                   account / billing profile with billing,
                                   routes to SOM, then starts billing, creates
                                   the installed assets and completes the order
  SOM  Service Order Management    eTOM 1.1.2 Service Management - Service
                                   Configuration & Activation: technical actions
                                   to the inventory system, whose logical and
                                   physical resource responses are collected on
                                   the SOM order; then routes to TOM
  TOM  Technical Order Management  eTOM 1.1.3 Resource Management - Resource
                                   Provisioning / Workforce Management: Field
                                   Service work orders (installation,
                                   connectivity test, acceptance signing ...),
                                   each done when the engineer closes it

Each lane holds fulfillment FUNCTIONS (orchestration/fulfillment_functions.py) and the
dependencies between them. At run time a CRM order becomes a COM FulfillmentOrder;
its "route to SOM" task opens the SOM FulfillmentOrder, whose "route to TOM" opens the
TOM FulfillmentOrder. Each order's tasks run as their dependencies complete; a task
that waits (a work order in the field, an asynchronous inventory response)
holds its order until the response arrives. A child order completing
completes the parent's routing task, so the order moves back up the stack:
TOM -> SOM -> COM -> billing, assets, order complete.

A failed task puts its order in FALLOUT - classified like any
flow error - for someone to retry, skip or complete by hand.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

LAYERS = ("COM", "SOM", "TOM")
PLAN_MODES = ("deterministic", "llm")
ORDER_STATUSES = ("in_progress", "waiting", "fallout", "completed", "cancelled")
TASK_STATUSES = ("pending", "running", "waiting", "completed", "skipped", "failed")


class FulfillmentPattern(Base):
    """A fulfillment pattern - what the tenant admin designs."""

    __tablename__ = "fulfillment_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    # Which CRM orders it fulfils - blank = any. Product names are comma-separated.
    products: Mapped[str] = mapped_column(Text, default="")
    segment: Mapped[str] = mapped_column(String(20), default="")
    order_type: Mapped[str] = mapped_column(String(20), default="new")
    # The highest-priority active pattern that matches an order is the one used.
    priority: Mapped[int] = mapped_column(Integer, default=100)
    # The intents whose completion starts it on their order - vanilla (INT-25, INT-107 ...) or the
    # tenant's custom ones (FLOW-12), comma-separated. Blank = Order Control approval (INT-25).
    # Sending an order by hand (INT-117, the monitor) uses any pattern that fits.
    trigger_intents: Mapped[str] = mapped_column(Text, default="")
    # deterministic: the designed plan as drawn; llm: an LLM picks which of the
    # pattern's functions an order needs and in what order - validated, and the
    # designed plan whenever no model is available or its plan is unusable.
    mode: Mapped[str] = mapped_column(String(20), default="deterministic")
    # {"nodes": [{"id", "layer", "function", "label", "x", "y", "config", "mandatory"}],
    #  "edges": [{"id", "source", "target"}]}  - an edge: target waits for source (same lane)
    graph_json: Mapped[str] = mapped_column(Text, default='{"nodes": [], "edges": []}')
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class FulfillmentOrder(Base):
    """One order of one layer - COM (from the CRM order), SOM (from COM), TOM (from SOM)."""

    __tablename__ = "fulfillment_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    layer: Mapped[str] = mapped_column(String(3))
    number: Mapped[str] = mapped_column(String(30), default="")  # COM-12 / SOM-12 / TOM-12
    crm_order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("fulfillment_orders.id"), nullable=True)
    # the parent's task that opened this order (route to SOM / route to TOM) - completes with it
    parent_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pattern_id: Mapped[int | None] = mapped_column(ForeignKey("fulfillment_patterns.id"), nullable=True)
    pattern_version: Mapped[int] = mapped_column(Integer, default=1)
    # how the plan was made: deterministic | llm | llm_fallback
    planned_by: Mapped[str] = mapped_column(String(20), default="deterministic")
    plan_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    # what the order carries: the CRM order's data, and the responses its tasks collected
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class FulfillmentTask(Base):
    """One fulfillment function of an order's orchestration plan."""

    __tablename__ = "fulfillment_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    fulfillment_order_id: Mapped[int] = mapped_column(ForeignKey("fulfillment_orders.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(50))
    function: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(150), default="")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    depends_on_json: Mapped[str] = mapped_column(Text, default="[]")  # node ids this task waits for
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    request_json: Mapped[str] = mapped_column(Text, default="{}")  # what was sent (billing, inventory, field)
    response_json: Mapped[str] = mapped_column(Text, default="{}")  # what came back
    error_json: Mapped[str] = mapped_column(Text, default="")
    # what the task waits on: a work order in the field, an inventory request id, a child order
    work_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    external_ref: Mapped[str] = mapped_column(String(100), default="", index=True)
    child_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
