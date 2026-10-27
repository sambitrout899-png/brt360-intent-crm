"""The reporting model: what can be reported on, and how it hangs together.

Every entity is one business table (orders, invoices, tickets, usage ...)
with the tables it belongs to joined in (an order's customer and the
customer's profile, an invoice's customer ...). Its fields are read from the
SQLAlchemy models themselves - a column added to a model shows up here with
no change - plus:
  - computed fields (an order's contract value, an invoice's outstanding
    amount and ageing bucket, a ticket's resolution hours ...);
  - the tenant's custom fields (cf.<key>), on the entity and on its customer.

A field is a dimension (text, picklist, yes/no), a date (sliced by year /
quarter / month / week / day / weekday / hour) or a number (a measure:
summed, averaged ... - or used as a dimension, e.g. binned).
"""
from __future__ import annotations

import datetime as dt
import enum
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, Integer, Numeric, String, Text
from sqlalchemy.orm import Session, aliased

from app.models.accounts import ServiceAccount
from app.models.assurance import Technician, TechnicianAssignment, TroubleTicket, WorkOrder
from app.models.catalog import ProductOffering
from app.models.comms import CmCreditAlert, CmFraudAlert, CmInvoice, CmPayment, CmSurvey, CmUsage
from app.models.crm import Lead, Opportunity, Party, PartyProfile, Quote
from app.models.fulfillment import Asset, Proposal
from app.models.orders import BillingAccount, Order, OrderItem, OrderProfile

GRAINS = ("year", "quarter", "month", "week", "day", "weekday", "hour")
AGGREGATIONS = ("count", "count_distinct", "sum", "avg", "min", "max", "median", "p90", "stddev")
_SKIP = re.compile(r"(?:_json|password|_hash|token|secret|signature|document_ref|instance_json|history_json)$")
_MONEY = re.compile(r"(?:price|amount|total|balance|revenue|recurring|one_time|usage$|adjustments|taxes|paid|credited|owed|unapplied|value|mrr|tcv|outstanding|charge)", re.I)
_PCT = re.compile(r"(?:_pct|percent|rate$)", re.I)
_LABELS = {"id": "Record ID", "party_type": "Party type", "sla_class": "SLA class", "mrr": "MRR", "tcv": "Contract value (TCV)",
           "propensity_score": "Propensity score", "requested_discount_pct": "Requested discount %", "discount_pct": "Discount %",
           "csat": "CSAT", "nps": "NPS"}


def humanize(name: str) -> str:
    if name in _LABELS:
        return _LABELS[name]
    text = name.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


@dataclass
class Join:
    key: str  # prefix of the joined fields ("customer" -> customer.name)
    label: str  # their group in the field list
    model: Any
    on: Callable[[dict[str, Any]], Any]  # aliases -> join condition ("" = the entity itself)
    columns: tuple[str, ...] | None = None  # only these columns (None = all reportable ones)


@dataclass
class Computed:
    key: str
    label: str
    kind: str  # dimension / number / date / bool
    needs: tuple[str, ...]
    fn: Callable[[dict[str, Any]], Any]
    fmt: str | None = None
    group: str | None = None


@dataclass
class Entity:
    key: str
    label: str
    model: Any
    date: str  # the default date field
    joins: list[Join] = field(default_factory=list)
    computed: list[Computed] = field(default_factory=list)
    scope: tuple[str, str] = ("", "")  # (entity type for visibility, field holding its id)
    record_type: str | None = None  # what the record panel opens (EntityType), when it can
    cf_types: dict[str, tuple[str, ...]] = field(default_factory=dict)  # field prefix -> custom-field entity types
    money: str | None = None  # the default value measure
    description: str = ""


@dataclass
class Field:
    key: str
    label: str
    kind: str  # dimension / number / date / bool
    group: str
    fmt: str | None = None
    values: list[str] | None = None  # a picklist / enum's values
    column: Any = None  # SQL column (aliased), None for computed / custom fields
    needs: tuple[str, ...] = ()
    fn: Callable[[dict[str, Any]], Any] | None = None
    custom: tuple[str, int, str] | None = None  # (id field, custom field id, data type)

    def public(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "kind": self.kind, "group": self.group, "fmt": self.fmt,
                "values": self.values, "measure": self.kind == "number", "custom": self.custom is not None,
                "computed": self.fn is not None}


# ---------------------------------------------------------------- computed fields


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _days(a: Any, b: Any) -> float | None:
    if a is None or b is None:
        return None
    if isinstance(a, dt.date) and not isinstance(a, dt.datetime):
        a = dt.datetime.combine(a, dt.time())
    if isinstance(b, dt.date) and not isinstance(b, dt.datetime):
        b = dt.datetime.combine(b, dt.time())
    return round((b - a).total_seconds() / 86400, 2)


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _status(v: Any) -> str:
    return str(getattr(v, "value", v) or "")


def _aging(row: dict[str, Any]) -> str:
    out = _num(row.get("total")) - _num(row.get("paid")) - _num(row.get("credited"))
    if out <= 0.005:
        return "Settled"
    late = _days(row.get("due_date"), _now().date())
    if late is None or late <= 0:
        return "Current"
    return "1-30 days" if late <= 30 else "31-60 days" if late <= 60 else "61-90 days" if late <= 90 else "90+ days"


def _band(v: Any, cuts: tuple[float, ...], labels: tuple[str, ...]) -> str | None:
    if v is None:
        return None
    for cut, label in zip(cuts, labels):
        if _num(v) < cut:
            return label
    return labels[-1]


LIVE_ORDER = ("active", "changed", "pending_cancellation", "suspended")
CLOSED_TICKET = ("resolved", "closed", "cancelled")
LOST_STAGES = ("lost", "feasibility_failed")  # an opportunity that ended without a sale


def _computed() -> dict[str, list[Computed]]:
    return {
        "party": [
            Computed("tenure_months", "Tenure (months)", "number", ("created_at",), lambda r: round((_days(r["created_at"], _now()) or 0) / 30.44, 1)),
        ],
        "lead": [
            Computed("propensity_band", "Propensity band", "dimension", ("propensity_score",),
                     lambda r: _band(r["propensity_score"], (0.4, 0.7, 2), ("Low (<0.4)", "Medium (0.4-0.7)", "High (0.7+)"))),
            Computed("age_days", "Age (days)", "number", ("created_at",), lambda r: _days(r["created_at"], _now()), "days"),
        ],
        "opportunity": [
            Computed("is_won", "Won", "bool", ("stage",), lambda r: _status(r["stage"]) == "won"),
            Computed("is_closed", "Closed", "bool", ("stage",), lambda r: _status(r["stage"]) in ("won", "lost")),
            Computed("age_days", "Age (days)", "number", ("created_at",), lambda r: _days(r["created_at"], _now()), "days"),
            Computed("cycle_days", "Sales cycle (days)", "number", ("created_at", "updated_at", "stage"),
                     lambda r: _days(r["created_at"], r["updated_at"]) if _status(r["stage"]) in ("won", "lost") else None, "days"),
        ],
        "quote": [
            Computed("net_value", "Net value", "number", ("total_price", "requested_discount_pct"),
                     lambda r: round(_num(r["total_price"]) * (1 - _num(r["requested_discount_pct"]) / 100), 2), "money"),
            Computed("discount_band", "Discount band", "dimension", ("requested_discount_pct",),
                     lambda r: _band(r["requested_discount_pct"], (0.001, 10, 20, 1e9), ("No discount", "Up to 10%", "10-20%", "Over 20%"))),
        ],
        "order": [
            Computed("tcv", "Contract value (TCV)", "number", ("monthly_price", "contract_months"),
                     lambda r: round(_num(r["monthly_price"]) * _num(r["contract_months"]), 2), "money"),
            Computed("mrr", "MRR (live orders)", "number", ("monthly_price", "status"),
                     lambda r: _num(r["monthly_price"]) if _status(r["status"]) in LIVE_ORDER else 0.0, "money"),
            Computed("is_live", "Live", "bool", ("status",), lambda r: _status(r["status"]) in LIVE_ORDER),
            Computed("age_days", "Age (days)", "number", ("created_at",), lambda r: _days(r["created_at"], _now()), "days"),
        ],
        "order_item": [
            Computed("line_value", "Line value (monthly)", "number", ("quantity", "net_unit_price", "unit_price"),
                     lambda r: round(_num(r["quantity"]) * _num(r["net_unit_price"] if r["net_unit_price"] is not None else r["unit_price"]), 2), "money"),
        ],
        "ticket": [
            Computed("is_resolved", "Resolved", "bool", ("status",), lambda r: _status(r["status"]) in CLOSED_TICKET),
            Computed("resolution_hours", "Resolution time (hours)", "number", ("created_at", "updated_at", "status"),
                     lambda r: round((_days(r["created_at"], r["updated_at"]) or 0) * 24, 1) if _status(r["status"]) in CLOSED_TICKET else None),
            Computed("age_days", "Age (days)", "number", ("created_at",), lambda r: _days(r["created_at"], _now()), "days"),
        ],
        "invoice": [
            Computed("outstanding", "Outstanding", "number", ("total", "paid", "credited"),
                     lambda r: round(max(0.0, _num(r["total"]) - _num(r["paid"]) - _num(r["credited"])), 2), "money"),
            Computed("aging_bucket", "Ageing bucket", "dimension", ("total", "paid", "credited", "due_date"), _aging),
            Computed("days_late", "Days past due", "number", ("due_date", "total", "paid", "credited"),
                     lambda r: max(0.0, _days(r["due_date"], _now().date()) or 0) if _aging(r) not in ("Settled", "Current") else 0.0, "days"),
        ],
        "usage": [
            Computed("gb", "Data (GB)", "number", ("quantity", "event_type"),
                     lambda r: round(_num(r["quantity"]) / 1024, 4) if _status(r["event_type"]) == "data" else 0.0),
            Computed("minutes", "Voice (minutes)", "number", ("quantity", "event_type"),
                     lambda r: _num(r["quantity"]) if _status(r["event_type"]) == "voice" else 0.0),
            Computed("messages", "SMS (messages)", "number", ("quantity", "event_type"),
                     lambda r: _num(r["quantity"]) if _status(r["event_type"]) == "sms" else 0.0),
        ],
        "survey": [
            Computed("nps_group", "NPS group", "dimension", ("would_recommend",),
                     lambda r: None if r["would_recommend"] is None else "Promoter" if r["would_recommend"] >= 9
                     else "Passive" if r["would_recommend"] >= 7 else "Detractor"),
            Computed("nps_points", "NPS points", "number", ("would_recommend",),
                     lambda r: None if r["would_recommend"] is None else 100.0 if r["would_recommend"] >= 9
                     else 0.0 if r["would_recommend"] >= 7 else -100.0),
        ],
        "work_order": [
            Computed("is_complete", "Complete", "bool", ("status",), lambda r: _status(r["status"]) == "complete"),
            Computed("turnaround_hours", "Turnaround (hours)", "number", ("created_at", "updated_at", "status"),
                     lambda r: round((_days(r["created_at"], r["updated_at"]) or 0) * 24, 1) if _status(r["status"]) == "complete" else None),
        ],
    }


# ---------------------------------------------------------------- entities


def _customer(on: Callable[[dict[str, Any]], Any]) -> list[Join]:
    """The customer (party) and its profile, joined by `on`."""
    return [
        Join("customer", "Customer", Party, on, ("id", "name", "party_type", "created_at")),
        Join("customer_profile", "Customer", PartyProfile, lambda a: a["customer_profile"].party_id == a["customer"].id,
             ("segment", "account_status", "industry")),
    ]


def entities() -> dict[str, Entity]:
    c = _computed()
    out = [
        Entity("party", "Customers & prospects", Party, "created_at", [
            Join("profile", "Profile", PartyProfile, lambda a: a["profile"].party_id == a[""].id, ("segment", "account_status", "industry")),
            Join("billing", "Billing account", BillingAccount, lambda a: a["billing"].party_id == a[""].id, ("status", "rate_plan", "balance")),
        ], c["party"], ("party", "id"), "party", {"": ("party",)}, "billing.balance", "Every customer and prospect, B2B and B2C"),
        Entity("lead", "Leads", Lead, "created_at", _customer(lambda a: a["customer"].id == a[""].party_id),
               c["lead"], ("lead", "id"), "lead", {"": ("lead",), "customer": ("party",)}, None, "Leads with their source, owner and propensity"),
        Entity("opportunity", "Opportunities", Opportunity, "created_at", [
            Join("lead", "Lead", Lead, lambda a: a["lead"].id == a[""].lead_id, ("source", "region", "owner_name", "propensity_score")),
            *_customer(lambda a: a["customer"].id == a["lead"].party_id),
        ], c["opportunity"], ("opportunity", "id"), "opportunity", {"": ("opportunity",), "customer": ("party",)}, None,
            "The sales pipeline: stages, products, cycle time"),
        Entity("quote", "Quotes", Quote, "created_at", [
            Join("opportunity", "Opportunity", Opportunity, lambda a: a["opportunity"].id == a[""].opportunity_id, ("id", "name", "stage", "offering_name")),
            Join("lead", "Lead", Lead, lambda a: a["lead"].id == a["opportunity"].lead_id, ("source", "region", "owner_name")),
            *_customer(lambda a: a["customer"].id == a["lead"].party_id),
        ], c["quote"], ("quote", "id"), "quote", {"": ("quote",), "customer": ("party",)}, "total_price", "Quotes, discounts and approvals"),
        Entity("order", "Orders", Order, "created_at", [
            Join("profile", "Order", OrderProfile, lambda a: a["profile"].order_id == a[""].id, ("segment", "requested_discount_pct")),
            *_customer(lambda a: a["customer"].id == a[""].party_id),
        ], c["order"], ("order", "id"), "order", {"": ("order",), "customer": ("party",)}, "monthly_price",
            "Orders with their recurring revenue, contract and status"),
        Entity("order_item", "Order lines (products)", OrderItem, "order.created_at", [
            Join("order", "Order", Order, lambda a: a["order"].id == a[""].order_id, ("id", "status", "order_type", "contract_months", "created_at", "party_id")),
            Join("offering", "Product", ProductOffering, lambda a: a["offering"].id == a[""].product_offering_id, ("name", "segment", "status", "price")),
            *_customer(lambda a: a["customer"].id == a["order"].party_id),
        ], c["order_item"], ("order", "order.id"), None, {"customer": ("party",)}, "line_value", "Every product line sold"),
        Entity("ticket", "Trouble tickets", TroubleTicket, "created_at", _customer(lambda a: a["customer"].id == a[""].party_id),
               c["ticket"], ("party", "party_id"), "ticket", {"": ("ticket", "trouble_ticket"), "customer": ("party",)}, None,
               "Faults and complaints: volumes, severity, resolution time"),
        Entity("work_order", "Work orders (field)", WorkOrder, "created_at", [
            Join("assignment", "Technician", TechnicianAssignment, lambda a: a["assignment"].work_order_id == a[""].id, ("assigned_at",)),
            Join("technician", "Technician", Technician, lambda a: a["technician"].id == a["assignment"].technician_id, ("name", "region", "status")),
        ], c["work_order"], ("order", "order_id"), None, {}, None, "Field dispatches and installations"),
        Entity("invoice", "Invoices", CmInvoice, "issue_date", _customer(lambda a: a["customer"].id == a[""].party_id),
               c["invoice"], ("party", "party_id"), None, {"customer": ("party",)}, "total", "Billing: charges, collections, ageing"),
        Entity("payment", "Payments", CmPayment, "received_at", _customer(lambda a: a["customer"].id == a[""].party_id),
               [], ("party", "party_id"), None, {"customer": ("party",)}, "amount", "Payments received, by method and status"),
        Entity("usage", "Usage (CDRs)", CmUsage, "at", _customer(lambda a: a["customer"].id == a[""].party_id),
               c["usage"], ("party", "party_id"), None, {"customer": ("party",)}, "amount", "Voice, SMS and data usage records"),
        Entity("asset", "Services & assets", Asset, "created_at", [
            Join("account", "Service account", ServiceAccount, lambda a: a["account"].id == a[""].service_account_id, ("name", "status")),
            *_customer(lambda a: a["customer"].id == a["account"].party_id),
        ], [], ("party", "customer.id"), None, {"customer": ("party",)}, None, "Installed services, circuits and devices"),
        Entity("proposal", "Proposals", Proposal, "sent_at", [
            Join("quote", "Quote", Quote, lambda a: a["quote"].id == a[""].quote_id, ("id", "total_price", "status")),
        ], [], ("quote", "quote_id"), None, {}, "quote.total_price", "Proposals sent and the customers' decisions"),
        Entity("survey", "Customer surveys", CmSurvey, "created_at", [
            Join("ticket", "Ticket", TroubleTicket, lambda a: a["ticket"].id == a[""].ticket_id, ("category", "severity", "product_line", "party_id")),
        ], c["survey"], ("party", "ticket.party_id"), None, {}, None, "CSAT and NPS from ticket surveys"),
        Entity("fraud_alert", "Fraud alerts", CmFraudAlert, "created_at", _customer(lambda a: a["customer"].id == a[""].party_id),
               [], ("party", "party_id"), None, {"customer": ("party",)}, "observed", "Fraud alerts by type, level and outcome"),
        Entity("credit_alert", "Credit & collections", CmCreditAlert, "created_at", _customer(lambda a: a["customer"].id == a[""].party_id),
               [], ("party", "party_id"), None, {"customer": ("party",)}, "amount_owed", "Collections cases and amounts owed"),
    ]
    return {e.key: e for e in out}


ENTITIES = entities()


# ---------------------------------------------------------------- fields


def _kind(col: Any) -> str | None:
    t = col.type
    if isinstance(t, Boolean):
        return "bool"
    if isinstance(t, (DateTime, Date)):
        return "date"
    if isinstance(t, Enum):
        return "dimension"
    if isinstance(t, (Integer, Float, Numeric)):
        return "number"
    if isinstance(t, Text):
        return None  # long text: not reportable (notes, descriptions)
    if isinstance(t, String):
        return "dimension"
    return None


def _reportable(name: str) -> bool:
    if name in ("tenant_id",) or _SKIP.search(name):
        return False
    return name == "id" or not name.endswith("_id")


def _fmt(name: str, kind: str) -> str | None:
    if kind != "number" or name == "id":
        return None
    if _PCT.search(name):
        return "pct"
    if _MONEY.search(name) and "quantity" not in name:
        return "money"
    return None


def _values(col: Any) -> list[str] | None:
    t = col.type
    if isinstance(t, Enum):
        cls = getattr(t, "enum_class", None)
        if cls is not None and issubclass(cls, enum.Enum):
            return [str(m.value) for m in cls]
        return [str(v) for v in t.enums]
    return None


@dataclass
class Model:
    """An entity resolved for one tenant: its aliases, joins and every field."""

    entity: Entity
    aliases: dict[str, Any]
    fields: dict[str, Field]

    def field(self, key: str) -> Field:
        if key not in self.fields:
            raise ValueError(f"“{key}” is not a field of {self.entity.label}")
        return self.fields[key]


def model(db: Session | None, tenant_id: int | None, entity_key: str) -> Model:
    if entity_key not in ENTITIES:
        raise ValueError(f"Unknown entity “{entity_key}” - pick one of: {', '.join(ENTITIES)}")
    e = ENTITIES[entity_key]
    aliases: dict[str, Any] = {"": aliased(e.model, name=f"a_{e.key}")}
    fields: dict[str, Field] = {}
    for col in e.model.__table__.columns:
        kind = _kind(col)
        if kind and _reportable(col.name):
            if col.name == "id" or col.name.endswith("_by"):  # ids and who-did-it (user ids) are never summed
                kind = "dimension"
            fields[col.name] = Field(col.name, humanize(col.name), kind, e.label, _fmt(col.name, kind), _values(col),
                                     getattr(aliases[""], col.key))
    for j in e.joins:
        aliases[j.key] = aliased(j.model, name=f"a_{e.key}_{j.key}")
        for col in j.model.__table__.columns:
            if j.columns is not None and col.name not in j.columns:
                continue
            kind = _kind(col)
            if not kind or not (_reportable(col.name) or j.columns is not None):
                continue
            if col.name == "id" or col.name.endswith("_id"):
                kind = "dimension"
            key = f"{j.key}.{col.name}"
            label = f"{j.label} › {humanize(col.name)}" if j.label != e.label else humanize(col.name)
            fields[key] = Field(key, label, kind, j.label, _fmt(col.name, kind), _values(col), getattr(aliases[j.key], col.key))
    for cpt in e.computed:
        fields[cpt.key] = Field(cpt.key, cpt.label, cpt.kind, cpt.group or e.label, cpt.fmt or _fmt(cpt.key, cpt.kind), None,
                                None, cpt.needs, cpt.fn)
    if db is not None and tenant_id is not None:
        _custom_fields(db, tenant_id, e, fields)
    return Model(e, aliases, fields)


def _custom_fields(db: Session, tenant_id: int, e: Entity, fields: dict[str, Field]) -> None:
    from app.models.custom_fields import CustomFieldDefinition

    defs = db.query(CustomFieldDefinition).filter(CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.is_active.is_(True)).all()
    for prefix, types in e.cf_types.items():
        id_key = "id" if prefix == "" else f"{prefix}.id"
        if id_key not in fields:
            continue
        group = e.label if prefix == "" else next((j.label for j in e.joins if j.key == prefix), prefix)
        for d in defs:
            if d.entity_type not in types:
                continue
            key = f"cf.{d.key}" if prefix == "" else f"{prefix}.cf.{d.key}"
            kind = "number" if d.data_type in ("number", "money", "percent", "integer", "decimal") else \
                "bool" if d.data_type in ("boolean", "checkbox") else "date" if d.data_type in ("date", "datetime") else "dimension"
            values = [v.strip() for v in (d.allowed_values or "").split(",") if v.strip()] or None
            label = d.label if prefix == "" else f"{group} › {d.label}"
            fields[key] = Field(key, label, kind, group, "money" if d.data_type == "money" else None, values,
                                needs=(id_key,), custom=(id_key, d.id, d.data_type))


def catalog(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Every entity with its fields - the Analytics Studio's field list."""
    out = []
    for key, e in ENTITIES.items():
        m = model(db, tenant_id, key)
        out.append({"key": key, "label": e.label, "description": e.description, "date": e.date, "money": e.money,
                    "record_type": e.record_type, "fields": [f.public() for f in m.fields.values()]})
    return out
