"""What a Customer 360 page can be made of, and the rules a layout must follow.

A layout is plain JSON the tenant admin owns:

    {"theme": {...}, "header": {"kpis": [recipe ids]}, "panes": [
        {"id", "title", "domain", "icon", "color", "width", "widgets": [
            {"id", "recipe", "type", "title", "dataset", "source", "metric": {...}}]}]}

Every widget reads one canonical *dataset* (opportunities, invoices,
network_health, ...). Where the rows come from is the widget's *source*:
"brt" (this CRM's own records) or the key of a connector - an external
billing / service / network / inventory system whose own schema is mapped
onto the dataset's fields (see connectors.py). normalize_layout() is the
single gate every layout passes through - from the studio, the agent or
the INT-33 intent - so nothing malformed is ever stored or rendered.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- domains

DOMAINS: dict[str, dict[str, str]] = {
    "sales": {"label": "Sales", "icon": "💼", "color": "#38bdf8"},
    "service": {"label": "Service", "icon": "🛠️", "color": "#f59e0b"},
    "billing": {"label": "Billing", "icon": "💳", "color": "#a78bfa"},
    "network": {"label": "Network", "icon": "📡", "color": "#34d399"},
    "inventory": {"label": "Inventory", "icon": "🗄️", "color": "#f472b6"},
    "custom": {"label": "Overview", "icon": "✨", "color": "#94a3b8"},
}

# ---------------------------------------------------------------- datasets


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str = "text"  # text | number | money | percent | date | status


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    domain: str
    fields: tuple[Field, ...]
    internal: bool  # BRT 360 holds this data itself
    date_field: str | None = None
    description: str = ""
    # BRT 360's Network Inventory can answer it - for a tenant that records its network there,
    # unless a connected external system serves the dataset (then that system answers)
    inventory: bool = False

    def field_keys(self) -> list[str]:
        return [f.key for f in self.fields]

    def field(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


_F = Field
DATASETS: dict[str, Dataset] = {d.key: d for d in [
    Dataset("leads", "Leads", "sales", (
        _F("id", "Lead", "number"), _F("status", "Status", "status"), _F("source", "Source"), _F("region", "Region"),
        _F("propensity_score", "Propensity", "percent"), _F("offering", "Product"), _F("created_at", "Created", "date"),
    ), True, "created_at", "Leads raised for this customer"),
    Dataset("opportunities", "Opportunities", "sales", (
        _F("id", "Opportunity", "number"), _F("name", "Name"), _F("stage", "Stage", "status"), _F("offering", "Product"),
        _F("quantity", "Qty", "number"), _F("value", "Value", "money"), _F("created_at", "Created", "date"),
    ), True, "created_at", "Deals in the pipeline"),
    Dataset("quotes", "Quotes", "sales", (
        _F("id", "Quote", "number"), _F("status", "Status", "status"), _F("amount", "Amount", "money"),
        _F("discount_pct", "Discount", "percent"), _F("created_at", "Created", "date"),
    ), True, "created_at", "Quotes and their approval state"),
    Dataset("orders", "Orders", "sales", (
        _F("id", "Order", "number"), _F("status", "Status", "status"), _F("order_type", "Type"),
        _F("monthly_price", "Monthly", "money"), _F("contract_months", "Term (months)", "number"),
        _F("contract_value", "Contract value", "money"), _F("created_at", "Created", "date"),
    ), True, "created_at", "Orders and recurring revenue"),
    Dataset("tickets", "Tickets", "service", (
        _F("id", "Ticket", "number"), _F("category", "Category"), _F("severity", "Severity", "status"),
        _F("priority", "Priority", "status"), _F("status", "Status", "status"), _F("sla_class", "SLA"),
        _F("created_at", "Opened", "date"),
    ), True, "created_at", "Complaints and incidents"),
    Dataset("work_orders", "Field visits", "service", (
        _F("id", "Work order", "number"), _F("kind", "Kind"), _F("status", "Status", "status"),
        _F("scheduled_window", "Window"), _F("technician", "Technician"), _F("created_at", "Created", "date"),
    ), True, "created_at", "Technician visits and installation work"),
    Dataset("account", "Billing account", "billing", (
        _F("balance", "Balance", "money"), _F("status", "Status", "status"), _F("rate_plan", "Rate plan"),
    ), True, None, "Balance and rate plan"),
    Dataset("adjustments", "Credits & charges", "billing", (
        _F("id", "Adjustment", "number"), _F("amount", "Amount", "money"), _F("reason", "Reason"),
        _F("created_at", "Date", "date"),
    ), True, "created_at", "Billing adjustments made by the CRM"),
    Dataset("invoices", "Invoices", "billing", (
        _F("invoice_id", "Invoice"), _F("period", "Period", "date"), _F("amount", "Amount", "money"),
        _F("status", "Status", "status"), _F("due_date", "Due", "date"),
    ), False, "period", "Invoices from the billing system"),
    Dataset("payments", "Payments", "billing", (
        _F("payment_id", "Payment"), _F("date", "Date", "date"), _F("amount", "Amount", "money"), _F("method", "Method"),
    ), False, "date", "Payments received"),
    Dataset("usage", "Usage", "billing", (
        _F("period", "Period", "date"), _F("data_gb", "Data (GB)", "number"), _F("voice_min", "Voice (min)", "number"),
        _F("sms", "SMS", "number"),
    ), False, "period", "Rated usage per month"),
    Dataset("network_health", "Network health", "network", (
        _F("element", "Element"), _F("type_name", "Type"), _F("site", "Site"), _F("status", "Status", "status"),
        _F("utilization_pct", "Utilization", "percent"), _F("latency_ms", "Latency (ms)", "number"),
        _F("availability_pct", "Availability", "percent"),
    ), False, None, "Live state of the network serving this customer (from Network Inventory: the elements on the "
       "customer's service paths, their state and how full they are)", inventory=True),
    Dataset("resources", "Network resources", "network", (
        _F("id", "Resource", "number"), _F("resource_type", "Type"), _F("identifier", "Identifier"),
        _F("status", "Status", "status"), _F("oper_status", "Operational", "status"), _F("site", "Site"), _F("path", "Network path"),
    ), True, None, "Ports, VLANs, IP blocks, numbers and equipment the customer's orders hold - live from Network Inventory"),
    Dataset("network_services", "Network services", "network", (
        _F("service", "Service"), _F("product", "Product"), _F("status", "Status", "status"), _F("oper_status", "Operational", "status"),
        _F("location", "Served from"), _F("path", "Network path"), _F("resources", "Resources", "number"),
        _F("order_id", "Order", "number"), _F("at_risk", "At risk", "status"),
    ), True, None, "The customer's services as delivered: what each runs on, where, and whether any of it is in trouble"),
    Dataset("assets", "Assets", "inventory", (
        _F("id", "Asset", "number"), _F("asset_type", "Type"), _F("identifier", "Identifier"),
        _F("status", "Status", "status"), _F("inventory_ref", "Inventory ref"), _F("created_at", "Installed", "date"),
    ), True, "created_at", "Installed products and equipment"),
    Dataset("devices", "Devices", "inventory", (
        _F("device_id", "Device"), _F("type_name", "Type"), _F("model", "Model"), _F("site", "Site"), _F("status", "Status", "status"),
        _F("serial", "Serial"), _F("firmware", "Firmware"),
    ), False, None, "CPE and devices from network inventory", inventory=True),
]}

# ---------------------------------------------------------------- widgets

WIDGET_TYPES = ("kpi", "line", "area", "bar", "donut", "gauge", "table", "records", "insights")
SCALAR_TYPES = ("kpi", "gauge")
CATEGORY_TYPES = ("bar", "donut")
TREND_TYPES = ("line", "area", "bar")
AGGS = ("count", "sum", "avg", "max", "min", "latest")
RECORD_SECTIONS = ("identity", "leads", "opportunities", "quotes", "service_accounts", "tickets", "billing")
WIDTHS = ("third", "half", "two_thirds", "full")


@dataclass(frozen=True)
class Recipe:
    id: str
    title: str
    domain: str
    type: str
    dataset: str | None
    metric: dict[str, Any] = field(default_factory=dict)
    keywords: tuple[str, ...] = ()
    description: str = ""

    def widget(self) -> dict[str, Any]:
        return {
            "id": _new_id("w"),
            "recipe": self.id,
            "type": self.type,
            "title": self.title,
            "dataset": self.dataset,
            "source": None,
            "metric": copy.deepcopy(self.metric),
        }


_OPEN = {"status": ["open", "diagnosing", "in_progress", "new"]}
_R = Recipe
RECIPES: dict[str, Recipe] = {r.id: r for r in [
    # sales
    _R("sales.pipeline_by_stage", "Pipeline by stage", "sales", "bar", "opportunities", {"agg": "count", "group_by": "stage"},
       ("pipeline", "stage", "funnel", "opportunities by stage", "deals by stage"), "How many deals sit at each stage"),
    _R("sales.pipeline_value", "Pipeline value", "sales", "kpi", "opportunities",
       {"agg": "sum", "field": "value", "format": "money", "filter": {"stage": ["draft", "qualified", "quoted"]}},
       ("pipeline value", "deal value", "opportunity value", "potential"), "Total value of open deals"),
    _R("sales.mrr", "Monthly recurring revenue", "sales", "kpi", "orders",
       {"agg": "sum", "field": "monthly_price", "format": "money", "filter": {"status": ["active", "completed", "changed", "pending_cancellation"]}},
       ("mrr", "recurring revenue", "monthly revenue", "arpu"), "Recurring revenue from live orders"),
    _R("sales.orders_by_status", "Orders by status", "sales", "donut", "orders", {"agg": "count", "group_by": "status"},
       ("orders by status", "order status", "orders"), "Where each order is in delivery"),
    _R("sales.bookings_trend", "Bookings trend", "sales", "area", "orders",
       {"agg": "sum", "field": "contract_value", "x": "created_at", "format": "money"},
       ("bookings", "contract value", "order trend", "sales trend", "orders over time"), "Contract value booked per month"),
    _R("sales.quotes_table", "Quotes", "sales", "table", "quotes",
       {"columns": ["id", "status", "amount", "discount_pct", "created_at"], "limit": 6}, ("quotes", "quote list", "proposals"),
       "Latest quotes with status"),
    _R("sales.lead_sources", "Leads by source", "sales", "donut", "leads", {"agg": "count", "group_by": "source"},
       ("lead source", "leads by source", "where leads come from"), "Which channels bring leads"),
    _R("sales.records", "Deals workbench", "sales", "records", None, {"sections": ["leads", "opportunities", "quotes"]},
       ("workbench", "deal actions", "manage deals", "lead actions", "sales records"), "Leads, opportunities and quotes with one-click actions"),
    # service
    _R("service.open_tickets", "Open tickets", "service", "kpi", "tickets", {"agg": "count", "filter": _OPEN},
       ("open tickets", "open complaints", "open incidents", "ticket count"), "Tickets not yet resolved"),
    _R("service.tickets_by_priority", "Tickets by priority", "service", "donut", "tickets", {"agg": "count", "group_by": "priority"},
       ("tickets by priority", "priority", "ticket priority", "severity"), "Open work by urgency"),
    _R("service.tickets_trend", "Tickets over time", "service", "bar", "tickets", {"agg": "count", "x": "created_at"},
       ("ticket trend", "tickets over time", "complaints over time", "incident trend"), "Complaints raised per month"),
    _R("service.tickets_by_category", "Tickets by category", "service", "bar", "tickets", {"agg": "count", "group_by": "category"},
       ("ticket category", "tickets by category", "problem types", "issue types"), "What customers complain about"),
    _R("service.tickets_table", "Recent tickets", "service", "table", "tickets",
       {"columns": ["id", "category", "priority", "status", "created_at"], "limit": 6}, ("ticket list", "recent tickets", "tickets"),
       "Latest tickets with status"),
    _R("service.field_visits", "Field visits", "service", "table", "work_orders",
       {"columns": ["id", "kind", "status", "technician", "scheduled_window"], "limit": 6},
       ("field visits", "technician", "work orders", "engineer", "dispatch"), "Technician visits and installation work"),
    _R("service.records", "Service workbench", "service", "records", None, {"sections": ["tickets", "identity"]},
       ("service actions", "ticket actions", "service workbench", "identity"), "Tickets and identity checks with actions"),
    # billing
    _R("billing.revenue_trend", "Billed revenue", "billing", "area", "invoices",
       {"agg": "sum", "field": "amount", "x": "period", "format": "money"},
       ("revenue", "billed revenue", "invoice trend", "billing trend", "spend", "revenue trend"), "Invoiced amount per month"),
    _R("billing.balance", "Account balance", "billing", "kpi", "account", {"agg": "latest", "field": "balance", "format": "money"},
       ("balance", "outstanding", "amount due", "account balance"), "What the customer owes right now"),
    _R("billing.invoice_status", "Invoices by status", "billing", "donut", "invoices", {"agg": "count", "group_by": "status"},
       ("invoice status", "paid", "overdue", "unpaid", "collections"), "Paid, due and overdue invoices"),
    _R("billing.invoices_table", "Invoices", "billing", "table", "invoices",
       {"columns": ["invoice_id", "period", "amount", "status", "due_date"], "limit": 6}, ("invoices", "invoice list", "bills"),
       "Latest invoices"),
    _R("billing.usage_trend", "Data usage", "billing", "line", "usage", {"agg": "sum", "field": "data_gb", "x": "period"},
       ("usage", "data usage", "consumption", "data trend", "gb"), "Data consumed per month"),
    _R("billing.payments_table", "Payments", "billing", "table", "payments",
       {"columns": ["payment_id", "date", "amount", "method"], "limit": 6}, ("payments", "payment history", "receipts"),
       "Payments received"),
    _R("billing.adjustments", "Credits & charges", "billing", "table", "adjustments",
       {"columns": ["id", "amount", "reason", "created_at"], "limit": 6}, ("credits", "adjustments", "charges", "refunds"),
       "Adjustments raised by the CRM"),
    # network
    _R("network.availability", "Network availability", "network", "gauge", "network_health",
       {"agg": "avg", "field": "availability_pct", "format": "percent", "max": 100},
       ("availability", "uptime", "network health", "health", "sla"), "Average availability across the customer's elements"),
    _R("network.utilization", "Utilization by element", "network", "bar", "network_health",
       {"agg": "avg", "field": "utilization_pct", "group_by": "element", "format": "percent"},
       ("utilization", "capacity", "load", "congestion", "bandwidth"), "How loaded each link and device is"),
    _R("network.latency", "Average latency", "network", "kpi", "network_health", {"agg": "avg", "field": "latency_ms", "format": "number", "unit": "ms"},
       ("latency", "delay", "ping", "response time"), "Average round-trip latency"),
    _R("network.elements_table", "Network elements", "network", "table", "network_health",
       {"columns": ["element", "site", "status", "utilization_pct", "latency_ms"], "limit": 8},
       ("network elements", "links", "sites", "alarms", "network status"), "Every element and its live state"),
    _R("network.resources", "Network resources", "network", "table", "resources",
       {"columns": ["resource_type", "identifier", "status", "oper_status", "path"], "limit": 8},
       ("circuits", "resources", "ip block", "sim", "network resources", "vlan", "port"), "Resources this customer's orders hold"),
    _R("network.services", "Network services", "network", "table", "network_services",
       {"columns": ["service", "product", "oper_status", "location", "path"], "limit": 8},
       ("network services", "service path", "what network", "which port", "olt", "exchange", "served from", "network inventory", "network assets"),
       "Each service, where it is served from and the path it runs on"),
    _R("network.services_by_state", "Services by network state", "network", "donut", "network_services", {"agg": "count", "group_by": "oper_status"},
       ("service state", "services up", "services down", "network state"), "How many of the customer's services are up, degraded or down"),
    _R("network.services_at_risk", "Services at risk", "network", "kpi", "network_services", {"agg": "count", "filter": {"at_risk": ["yes"]}},
       ("at risk", "services at risk", "outage", "maintenance impact"), "Services on equipment that is down, degraded or in maintenance"),
    # inventory
    _R("inventory.assets_by_type", "Assets by type", "inventory", "donut", "assets", {"agg": "count", "group_by": "asset_type"},
       ("assets by type", "asset mix", "installed base"), "Installed base by type"),
    _R("inventory.assets_table", "Installed assets", "inventory", "table", "assets",
       {"columns": ["asset_type", "identifier", "status", "inventory_ref"], "limit": 8}, ("assets", "installed assets", "asset list"),
       "Everything installed for this customer"),
    _R("inventory.devices", "Devices", "inventory", "table", "devices",
       {"columns": ["device_id", "model", "site", "status", "firmware"], "limit": 8}, ("devices", "cpe", "router", "equipment", "hardware"),
       "Devices from the inventory system"),
    _R("inventory.devices_by_status", "Devices by status", "inventory", "donut", "devices", {"agg": "count", "group_by": "status"},
       ("device status", "devices by status", "faulty devices"), "Healthy vs. faulty devices"),
    _R("inventory.records", "Service accounts", "inventory", "records", None, {"sections": ["service_accounts", "billing"]},
       ("service accounts", "subscriptions", "order actions", "live services"), "Service accounts and live services with actions"),
    # overview
    _R("custom.insights", "AI insights", "custom", "insights", None, {},
       ("insights", "recommendations", "next best action", "ai", "risks", "alerts"), "What needs attention, with one-click actions"),
]}

# Recipes a fresh pane of a domain starts with.
DOMAIN_STARTERS: dict[str, list[str]] = {
    "sales": ["sales.pipeline_by_stage", "sales.bookings_trend", "sales.records"],
    "service": ["service.tickets_by_priority", "service.tickets_trend", "service.tickets_table"],
    "billing": ["billing.revenue_trend", "billing.invoice_status", "billing.invoices_table"],
    "network": ["network.services", "network.services_at_risk", "network.elements_table"],
    "inventory": ["inventory.devices", "inventory.assets_by_type"],
    "custom": ["custom.insights"],
}

HEADER_KPI_CHOICES = [r.id for r in RECIPES.values() if r.type in SCALAR_TYPES]
DEFAULT_HEADER_KPIS = ["sales.mrr", "sales.pipeline_value", "service.open_tickets", "billing.balance"]

# ---------------------------------------------------------------- themes

THEMES: dict[str, dict[str, str]] = {
    "midnight": {"label": "Midnight", "bg": "#020617", "surface": "rgba(15,23,42,0.72)", "border": "#1e293b",
                 "text": "#e2e8f0", "muted": "#94a3b8", "accent": "#38bdf8", "hero": "linear-gradient(135deg,#0f172a,#082f49)"},
    "ocean": {"label": "Ocean", "bg": "#03131c", "surface": "rgba(8,47,73,0.55)", "border": "#0e4a5c",
              "text": "#e0f2fe", "muted": "#7dd3fc", "accent": "#22d3ee", "hero": "linear-gradient(135deg,#083344,#0c4a6e)"},
    "aurora": {"label": "Aurora", "bg": "#0b0718", "surface": "rgba(30,20,60,0.6)", "border": "#3b2a6b",
               "text": "#ede9fe", "muted": "#c4b5fd", "accent": "#a78bfa", "hero": "linear-gradient(135deg,#2e1065,#134e4a)"},
    "graphite": {"label": "Graphite", "bg": "#0c0c0e", "surface": "rgba(30,30,34,0.85)", "border": "#2e2e33",
                 "text": "#f4f4f5", "muted": "#a1a1aa", "accent": "#f97316", "hero": "linear-gradient(135deg,#18181b,#27272a)"},
    "daylight": {"label": "Daylight", "bg": "#f1f5f9", "surface": "#ffffff", "border": "#e2e8f0",
                 "text": "#0f172a", "muted": "#64748b", "accent": "#0284c7", "hero": "linear-gradient(135deg,#e0f2fe,#f8fafc)"},
}
CARD_STYLES = ("glass", "solid", "outline")
DENSITIES = ("comfortable", "compact")
HEADER_STYLES = ("hero", "compact")

# ---------------------------------------------------------------- templates

TEMPLATES: dict[str, dict[str, Any]] = {
    "telecom_360": {
        "label": "Complete telecom 360",
        "description": "Sales, service, billing, network and inventory side by side, with AI insights on top.",
        "panes": [
            ("custom", "Needs attention", "full", ["custom.insights"]),
            ("sales", None, "half", ["sales.pipeline_by_stage", "sales.bookings_trend", "sales.records"]),
            ("service", None, "half", ["service.tickets_by_priority", "service.tickets_trend", "service.records"]),
            ("billing", None, "half", ["billing.revenue_trend", "billing.invoice_status", "billing.invoices_table"]),
            ("network", None, "half", ["network.services", "network.services_at_risk", "network.elements_table"]),
            ("inventory", None, "full", ["inventory.devices", "inventory.records"]),
        ],
    },
    "sales_focus": {
        "label": "Account manager",
        "description": "Pipeline and revenue first, service health as context.",
        "panes": [
            ("sales", "Pipeline & revenue", "two_thirds", ["sales.pipeline_by_stage", "sales.bookings_trend", "sales.records"]),
            ("custom", "Next best actions", "third", ["custom.insights"]),
            ("billing", None, "half", ["billing.revenue_trend", "billing.balance"]),
            ("service", "Service health", "half", ["service.open_tickets", "service.tickets_by_priority"]),
        ],
    },
    "service_focus": {
        "label": "Customer care",
        "description": "Tickets, network state and field visits for fast resolution.",
        "panes": [
            ("service", "Open issues", "two_thirds", ["service.tickets_by_priority", "service.tickets_table", "service.records"]),
            ("custom", "Needs attention", "third", ["custom.insights"]),
            ("network", None, "half", ["network.services", "network.services_at_risk", "network.elements_table"]),
            ("service", "Field work", "half", ["service.field_visits"]),
            ("inventory", None, "full", ["inventory.records"]),
        ],
    },
    "classic": {
        "label": "Classic records",
        "description": "The original record-by-record view with every action button.",
        "panes": [
            ("sales", "Deals", "full", ["sales.records"]),
            ("service", "Service", "half", ["service.records"]),
            ("inventory", "Accounts & services", "half", ["inventory.records"]),
        ],
    },
}
DEFAULT_TEMPLATE = "telecom_360"

MAX_PANES = 12
MAX_WIDGETS = 12
# The Customer 360 page's tabs. A pane may name one explicitly; without it the
# pane lands on the tab of its domain (older layouts keep working unchanged).
PANE_TABS = ("overview", "sales", "services", "orders", "billing", "support", "details", "account", "activity")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,59}$")


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def make_pane(domain: str, title: str | None = None, width: str = "half", recipes: list[str] | None = None) -> dict[str, Any]:
    meta = DOMAINS.get(domain, DOMAINS["custom"])
    return {
        "id": _new_id("p"),
        "title": title or meta["label"],
        "domain": domain if domain in DOMAINS else "custom",
        "icon": meta["icon"],
        "color": meta["color"],
        "width": width if width in WIDTHS else "half",
        "widgets": [RECIPES[r].widget() for r in (recipes if recipes is not None else DOMAIN_STARTERS.get(domain, [])) if r in RECIPES],
    }


def template_layout(key: str = DEFAULT_TEMPLATE, theme: dict[str, Any] | None = None) -> dict[str, Any]:
    tpl = TEMPLATES.get(key) or TEMPLATES[DEFAULT_TEMPLATE]
    return normalize_layout({
        "theme": theme or {"preset": "midnight", "accent": None, "card_style": "glass", "density": "comfortable", "header_style": "hero"},
        "header": {"kpis": list(DEFAULT_HEADER_KPIS), "show_health": True},
        "panes": [make_pane(domain, title, width, recipes) for domain, title, width, recipes in tpl["panes"]],
        "template": key if key in TEMPLATES else DEFAULT_TEMPLATE,
    })


def default_layout() -> dict[str, Any]:
    return template_layout(DEFAULT_TEMPLATE)


# ---------------------------------------------------------------- validation


def _text(value: Any, limit: int, fallback: str = "") -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and str(value).strip() else fallback


def _clean_metric(raw: Any, dataset: Dataset | None, wtype: str) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    keys = set(dataset.field_keys()) if dataset else set()
    if wtype == "records":
        sections = [s for s in raw.get("sections") or [] if s in RECORD_SECTIONS]
        return {"sections": list(dict.fromkeys(sections)) or ["leads", "opportunities", "quotes"]}
    if dataset is None:
        return out
    agg = raw.get("agg") if raw.get("agg") in AGGS else "count"
    out["agg"] = agg
    if raw.get("field") in keys:
        out["field"] = raw["field"]
    elif agg != "count":
        out["agg"] = "count"
    if raw.get("group_by") in keys:
        out["group_by"] = raw["group_by"]
    if raw.get("x") in keys:
        out["x"] = raw["x"]
    if isinstance(raw.get("filter"), dict):
        flt = {k: [str(v) for v in (vals if isinstance(vals, list) else [vals])][:20] for k, vals in raw["filter"].items() if k in keys}
        if flt:
            out["filter"] = flt
    if isinstance(raw.get("columns"), list):
        cols = [c for c in raw["columns"] if c in keys]
        if cols:
            out["columns"] = list(dict.fromkeys(cols))[:8]
    if raw.get("format") in ("money", "number", "percent"):
        out["format"] = raw["format"]
    if isinstance(raw.get("unit"), str):
        out["unit"] = raw["unit"][:12]
    for num_key, lo, hi in (("limit", 1, 50), ("max", 1, 1_000_000)):
        try:
            if raw.get(num_key) is not None:
                out[num_key] = max(lo, min(hi, int(raw[num_key])))
        except (TypeError, ValueError):
            pass
    # Make the metric fit the chart it feeds.
    if wtype in TREND_TYPES and "x" not in out and "group_by" not in out:
        if dataset.date_field:
            out["x"] = dataset.date_field
        else:
            out["group_by"] = next((f.key for f in dataset.fields if f.type == "status"), dataset.fields[0].key)
    if wtype in CATEGORY_TYPES and "group_by" not in out and "x" not in out:
        out["group_by"] = next((f.key for f in dataset.fields if f.type == "status"), dataset.fields[0].key)
    if wtype == "table" and "columns" not in out:
        out["columns"] = dataset.field_keys()[:5]
    return out


COMPONENTS = {
    "account_information": "Account information", "service_accounts": "Service accounts and installed assets",
    "orders": "Orders", "sales": "Sales pipeline", "billing": "Billing accounts and bill profiles",
    "tickets": "Service requests", "insights": "Account alerts", "health": "Customer health score",
    "profile": "Customer profile", "hierarchy": "Hierarchy and preferences", "activity": "Account activity",
}


def normalize_audience(raw: Any) -> dict[str, Any]:
    """Missing dimensions mean everyone; an explicit empty selection means nobody."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {"enabled": raw.get("enabled", True) is not False}
    for key, choices in (("customer_types", ("account", "prospect")), ("segments", ("B2B", "B2C"))):
        values = raw.get(key)
        out[key] = list(choices) if values is None else [v for v in choices if isinstance(values, list) and v in values]
    return out


def audience_matches(raw: Any, context: dict[str, Any]) -> bool:
    rule = normalize_audience(raw)
    # Selecting both segments also includes customers whose segment is not recorded.
    return rule["enabled"] and context["customer_type"] in rule["customer_types"] and (
        len(rule["segments"]) == 2 or context["segment"] in rule["segments"])


def normalize_widget(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    recipe = RECIPES.get(raw.get("recipe") or "")
    wtype = raw.get("type") if raw.get("type") in WIDGET_TYPES else (recipe.type if recipe else None)
    if wtype is None:
        return None
    dataset_key = raw.get("dataset") if raw.get("dataset") in DATASETS else (recipe.dataset if recipe else None)
    if wtype in ("records", "insights"):
        dataset_key = None
    elif dataset_key is None:
        return None
    dataset = DATASETS.get(dataset_key or "")
    metric = _clean_metric(raw.get("metric", recipe.metric if recipe else {}), dataset, wtype)
    source = raw.get("source")
    if not (isinstance(source, str) and (source == "brt" or _SOURCE.match(source))):
        source = None
    return {
        "id": raw["id"] if isinstance(raw.get("id"), str) and _ID.match(raw["id"]) else _new_id("w"),
        "recipe": recipe.id if recipe else None,
        "type": wtype,
        "title": _text(raw.get("title"), 80, recipe.title if recipe else (dataset.label if dataset else "Widget")),
        "dataset": dataset_key,
        "source": source,
        "metric": metric,
        "span": 2 if raw.get("span") == 2 else 1,
        "audience": normalize_audience(raw.get("audience")),
    }


def normalize_layout(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    theme_raw = raw.get("theme") if isinstance(raw.get("theme"), dict) else {}
    accent = theme_raw.get("accent")
    theme = {
        "preset": theme_raw.get("preset") if theme_raw.get("preset") in THEMES else "midnight",
        "accent": accent if isinstance(accent, str) and _HEX.match(accent) else None,
        "card_style": theme_raw.get("card_style") if theme_raw.get("card_style") in CARD_STYLES else "glass",
        "density": theme_raw.get("density") if theme_raw.get("density") in DENSITIES else "comfortable",
        "header_style": theme_raw.get("header_style") if theme_raw.get("header_style") in HEADER_STYLES else "hero",
    }
    header_raw = raw.get("header") if isinstance(raw.get("header"), dict) else {}
    kpis = [k for k in header_raw.get("kpis", DEFAULT_HEADER_KPIS) or [] if k in HEADER_KPI_CHOICES]
    header = {"kpis": list(dict.fromkeys(kpis))[:6], "show_health": header_raw.get("show_health", True) is not False}

    panes = []
    seen: set[str] = set()
    for p in (raw.get("panes") or [])[:MAX_PANES]:
        if not isinstance(p, dict):
            continue
        domain = p.get("domain") if p.get("domain") in DOMAINS else "custom"
        meta = DOMAINS[domain]
        pid = p["id"] if isinstance(p.get("id"), str) and _ID.match(p["id"]) and p["id"] not in seen else _new_id("p")
        seen.add(pid)
        widgets = [w for w in (normalize_widget(w) for w in (p.get("widgets") or [])[:MAX_WIDGETS]) if w]
        color = p.get("color")
        panes.append({
            "id": pid,
            "title": _text(p.get("title"), 60, meta["label"]),
            "domain": domain,
            "icon": _text(p.get("icon"), 8, meta["icon"]),
            "color": color if isinstance(color, str) and _HEX.match(color) else meta["color"],
            "width": p.get("width") if p.get("width") in WIDTHS else "half",
            "collapsed": bool(p.get("collapsed", False)),
            "tab": p.get("tab") if p.get("tab") in PANE_TABS else None,
            "audience": normalize_audience(p.get("audience")),
            "widgets": widgets,
        })
    return {
        "version": 1,
        "template": raw.get("template") if raw.get("template") in TEMPLATES else None,
        "theme": theme,
        "header": header,
        "component_audiences": {key: normalize_audience(value) for key, value in
                                (raw.get("component_audiences") or {}).items()
                                if key in COMPONENTS or key in {"kpi:" + k for k in HEADER_KPI_CHOICES}}
                              if isinstance(raw.get("component_audiences", {}), dict) else {},
        "panes": panes,
    }


def describe(layout: dict[str, Any]) -> str:
    """One line per pane - what the agent and the audit trail see."""
    lines = [f"theme={layout['theme']['preset']} accent={layout['theme']['accent'] or 'default'} "
             f"cards={layout['theme']['card_style']} density={layout['theme']['density']}"]
    for p in layout["panes"]:
        widgets = ", ".join(f"{w['title']} ({w['type']}{', src=' + w['source'] if w['source'] else ''}, audience={w.get('audience')})" for w in p["widgets"])
        lines.append(f"[{p['id']}] {p['title']} ({p['domain']}{', tab=' + p['tab'] if p.get('tab') else ''}, {p['width']}, audience={p.get('audience')}): {widgets or 'empty'}")
    lines.append(f"Standard component audiences={layout.get('component_audiences', {})}")
    return "\n".join(lines)


def public_catalog() -> dict[str, Any]:
    """Everything the studio needs to offer choices."""
    return {
        "domains": [{"key": k, **v} for k, v in DOMAINS.items()],
        "datasets": [
            {"key": d.key, "label": d.label, "domain": d.domain, "internal": d.internal, "inventory": d.inventory, "description": d.description,
             "date_field": d.date_field, "fields": [{"key": f.key, "label": f.label, "type": f.type} for f in d.fields]}
            for d in DATASETS.values()
        ],
        "recipes": [
            {"id": r.id, "title": r.title, "domain": r.domain, "type": r.type, "dataset": r.dataset, "description": r.description}
            for r in RECIPES.values()
        ],
        "widget_types": list(WIDGET_TYPES),
        "widths": list(WIDTHS),
        "themes": [{"key": k, **v} for k, v in THEMES.items()],
        "card_styles": list(CARD_STYLES),
        "densities": list(DENSITIES),
        "templates": [{"key": k, "label": v["label"], "description": v["description"]} for k, v in TEMPLATES.items()],
        "header_kpis": HEADER_KPI_CHOICES,
        "record_sections": list(RECORD_SECTIONS),
        "pane_tabs": list(PANE_TABS),
    }
