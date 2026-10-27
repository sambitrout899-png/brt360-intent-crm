"""Field Service module (after the Siebel Field Service process model).

Everything here sits BESIDE the existing assurance/fulfillment tables - a
trouble ticket is the service request, a work order is the field activity,
a technician is the field service engineer and an asset is the installed
base. The side tables below add what a real field service operation needs
without changing what those rows already mean:

  service request (trouble_tickets)  + fs_request_details   entitlement, warranty, committed date
  activity        (work_orders)      + fs_activities        schedule, steps, items, skills
  engineer        (technicians)      + fs_engineers         region, skills+level, hours, trunk
  asset           (assets)           + fs_asset_details     product, serial, install date, parent
                                       fs_measurements / fs_readings
  service regions, absences, activity templates, entitlements, warranties,
  inventory locations/levels/transactions, preventive maintenance plans and
  actions, repairs (RMA) and charges.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class FsServiceRegion(Base):
    """A service region and its service calendar (schedule hours + holidays)."""

    __tablename__ = "fs_service_regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    day_start: Mapped[str] = mapped_column(String(5), default="08:00")
    day_end: Mapped[str] = mapped_column(String(5), default="17:00")
    work_days: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4")  # Monday = 0
    holidays_json: Mapped[str] = mapped_column(Text, default="[]")  # ["2026-12-25", ...]
    labor_rate: Mapped[float] = mapped_column(Float, default=0.0)  # billed per hour
    travel_minutes: Mapped[int] = mapped_column(Integer, default=30)  # buffer between two jobs
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsEngineer(Base):
    """Field service engineer profile for a technician."""

    __tablename__ = "fs_engineers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    technician_id: Mapped[int] = mapped_column(ForeignKey("technicians.id"), unique=True)
    region_id: Mapped[int | None] = mapped_column(ForeignKey("fs_service_regions.id"), nullable=True)
    skills_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"skill": "fiber_install", "level": 1..3}]
    day_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # overrides the region
    day_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    work_days: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cost_per_hour: Mapped[float] = mapped_column(Float, default=0.0)
    max_jobs_per_day: Mapped[int] = mapped_column(Integer, default=6)
    phone: Mapped[str] = mapped_column(String(50), default="")
    trunk_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsAbsence(Base):
    """Exception hours: when an engineer is not available."""

    __tablename__ = "fs_engineer_absences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    technician_id: Mapped[int] = mapped_column(ForeignKey("technicians.id"))
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime)
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(200), default="")


class FsActivityTemplate(Base):
    """Activity template: the steps, items and skills one kind of job needs."""

    __tablename__ = "fs_activity_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="repair")  # installation/repair/preventive_maintenance/inspection/other
    description: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 ASAP, 2 high, 3 medium, 4 low
    skills_json: Mapped[str] = mapped_column(Text, default="[]")  # ["fiber_install", ...]
    steps_json: Mapped[str] = mapped_column(Text, default="[]")  # ["Check the ONT", ...]
    items_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"product", "quantity", "tool"}]
    instructions: Mapped[str] = mapped_column(Text, default="")
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsEntitlement(Base):
    """What a customer's agreement entitles them to: response time, coverage hours, discounts."""

    __tablename__ = "fs_entitlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    name: Mapped[str] = mapped_column(String(200))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)  # None = all the customer's assets
    product: Mapped[str] = mapped_column(String(200), default="")  # "" = any product
    coverage: Mapped[str] = mapped_column(String(20), default="business")  # business / 24x7
    response_hours: Mapped[float] = mapped_column(Float, default=8.0)
    resolution_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    labor_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    parts_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    expense_discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    visits_allowed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visits_used: Mapped[int] = mapped_column(Integer, default=0)
    starts_on: Mapped[dt.date] = mapped_column(Date)
    ends_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsWarranty(Base):
    """A product/manufacturer warranty definition."""

    __tablename__ = "fs_warranties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    products: Mapped[str] = mapped_column(Text, default="")  # comma-separated product names, "" = any
    duration_days: Mapped[int] = mapped_column(Integer, default=365)
    covers_parts: Mapped[bool] = mapped_column(Boolean, default=True)
    covers_labor: Mapped[bool] = mapped_column(Boolean, default=True)
    covers_expenses: Mapped[bool] = mapped_column(Boolean, default=False)
    measurement: Mapped[str] = mapped_column(String(100), default="")  # concurrent usage limit, optional
    usage_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    transferable: Mapped[bool] = mapped_column(Boolean, default=True)  # follows an asset swap
    provider: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsAssetWarranty(Base):
    __tablename__ = "fs_asset_warranties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    warranty_id: Mapped[int] = mapped_column(ForeignKey("fs_warranties.id"))
    starts_on: Mapped[dt.date] = mapped_column(Date)
    ends_on: Mapped[dt.date] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsAssetDetail(Base):
    """Service details of an installed asset."""

    __tablename__ = "fs_asset_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), unique=True)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(200), default="")
    serial_number: Mapped[str] = mapped_column(String(200), default="")
    installed_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    parent_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    address: Mapped[str] = mapped_column(String(300), default="")
    region_id: Mapped[int | None] = mapped_column(ForeignKey("fs_service_regions.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class FsMeasurement(Base):
    """Something read off an asset: a counter (usage) or a gauge (threshold)."""

    __tablename__ = "fs_measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    name: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(30), default="")
    kind: Mapped[str] = mapped_column(String(20), default="counter")  # counter / gauge
    min_value: Mapped[float] = mapped_column(Float, default=0.0)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # a counter wraps after this
    lower_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_limit: Mapped[float | None] = mapped_column(Float, nullable=True)


class FsReading(Base):
    __tablename__ = "fs_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    measurement_id: Mapped[int] = mapped_column(ForeignKey("fs_measurements.id"), index=True)
    value: Mapped[float] = mapped_column(Float)
    read_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    activity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="manual")


class FsRequestDetail(Base):
    """Service details of a service request (a trouble ticket)."""

    __tablename__ = "fs_request_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    ticket_id: Mapped[int] = mapped_column(ForeignKey("trouble_tickets.id"), unique=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    entitlement_id: Mapped[int | None] = mapped_column(ForeignKey("fs_entitlements.id"), nullable=True)
    asset_warranty_id: Mapped[int | None] = mapped_column(ForeignKey("fs_asset_warranties.id"), nullable=True)
    opened_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    committed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    verify_note: Mapped[str] = mapped_column(Text, default="")
    resolution_code: Mapped[str] = mapped_column(String(100), default="")
    resolution_notes: Mapped[str] = mapped_column(Text, default="")
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="call")  # call / pm / repair


class FsActivity(Base):
    """Field service details of a work order (the activity)."""

    __tablename__ = "fs_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), unique=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("trouble_tickets.id"), nullable=True)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("fs_activity_templates.id"), nullable=True)
    pm_action_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(40), default="repair")
    priority: Mapped[int] = mapped_column(Integer, default=3)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    earliest_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)  # latest start = SR committed date
    planned_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    planned_end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    region_id: Mapped[int | None] = mapped_column(ForeignKey("fs_service_regions.id"), nullable=True)
    address: Mapped[str] = mapped_column(String(300), default="")
    skills_json: Mapped[str] = mapped_column(Text, default="[]")
    steps_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"text", "done"}]
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    instructions: Mapped[str] = mapped_column(Text, default="")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)  # lock assignment
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    resolution_code: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    actual_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    actual_end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class FsTimeEntry(Base):
    """Time tracker: travel and work time of an activity."""

    __tablename__ = "fs_time_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    activity_id: Mapped[int] = mapped_column(ForeignKey("fs_activities.id"))
    technician_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="work")  # travel / work
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    minutes: Mapped[float] = mapped_column(Float, default=0.0)
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(300), default="")


class FsExpense(Base):
    __tablename__ = "fs_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    activity_id: Mapped[int] = mapped_column(ForeignKey("fs_activities.id"))
    kind: Mapped[str] = mapped_column(String(50), default="travel")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsPartMovement(Base):
    """Part tracker: a part installed at (or removed from) the customer site."""

    __tablename__ = "fs_part_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    activity_id: Mapped[int] = mapped_column(ForeignKey("fs_activities.id"))
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    direction: Mapped[str] = mapped_column(String(10), default="install")  # install / remove
    location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # trunk or stock it came from / went to
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    serial_number: Mapped[str] = mapped_column(String(200), default="")
    swap_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the removal this install replaces
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsInventoryLocation(Base):
    __tablename__ = "fs_inventory_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="warehouse")  # warehouse/field_office/trunk/repair_center/vendor
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("fs_inventory_locations.id"), nullable=True)
    technician_id: Mapped[int | None] = mapped_column(ForeignKey("technicians.id"), nullable=True)
    region_id: Mapped[int | None] = mapped_column(ForeignKey("fs_service_regions.id"), nullable=True)
    address: Mapped[str] = mapped_column(String(300), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FsInventoryLevel(Base):
    """How much of a product a location holds, by status (good / defective)."""

    __tablename__ = "fs_inventory_levels"
    __table_args__ = (UniqueConstraint("location_id", "product_name", "status", name="uq_fs_inventory_levels_bucket"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("fs_inventory_locations.id"))
    product_name: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(20), default="good")
    on_hand: Mapped[float] = mapped_column(Float, default=0.0)
    reserved: Mapped[float] = mapped_column(Float, default=0.0)
    min_qty: Mapped[float] = mapped_column(Float, default=0.0)
    max_qty: Mapped[float] = mapped_column(Float, default=0.0)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    locator: Mapped[str] = mapped_column(String(50), default="")


class FsInventoryTransaction(Base):
    __tablename__ = "fs_inventory_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(String(30))
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Float)
    from_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = outside (supplier/customer)
    from_status: Mapped[str] = mapped_column(String(20), default="good")
    to_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), default="good")
    reference_type: Mapped[str] = mapped_column(String(30), default="")
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsPmPlan(Base):
    """Preventive maintenance plan: which assets, which triggers, which job."""

    __tablename__ = "fs_pm_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(Integer, default=3)
    optional: Mapped[bool] = mapped_column(Boolean, default=False)
    products: Mapped[str] = mapped_column(Text, default="")  # all assets of these products
    asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")  # plus these assets
    triggers_json: Mapped[str] = mapped_column(Text, default="[]")
    template_id: Mapped[int | None] = mapped_column(ForeignKey("fs_activity_templates.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsPmAction(Base):
    __tablename__ = "fs_pm_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("fs_pm_plans.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    trigger_kind: Mapped[str] = mapped_column(String(20))
    due_on: Mapped[dt.date] = mapped_column(Date)
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reading_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled / done / cancelled
    completed_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class FsRepair(Base):
    """A defective product in the repair loop (RMA)."""

    __tablename__ = "fs_repairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    repair_number: Mapped[str] = mapped_column(String(30), default="")
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("trouble_tickets.id"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(200))
    serial_number: Mapped[str] = mapped_column(String(200), default="")
    symptoms: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="internal")  # internal / external (third-party vendor)
    vendor: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="received")
    location_id: Mapped[int | None] = mapped_column(ForeignKey("fs_inventory_locations.id"), nullable=True)
    under_warranty: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    shipped_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    returned_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class FsCharge(Base):
    """A priced line of field work, after warranty/entitlement discounts."""

    __tablename__ = "fs_charges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("trouble_tickets.id"), nullable=True)
    activity_id: Mapped[int | None] = mapped_column(ForeignKey("fs_activities.id"), nullable=True)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # labor / travel / part / expense
    description: Mapped[str] = mapped_column(String(300), default="")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    gross: Mapped[float] = mapped_column(Float, default=0.0)
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    discount_reason: Mapped[str] = mapped_column(String(200), default="")
    net: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / invoiced / waived
    billing_adjustment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
