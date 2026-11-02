"""Commercial licensing (app/models/subscriptions.py) - the catalog, each organization's
subscription and its price, seats, entitlements, lifecycle and invoices.

Enforcement stays where it always was: an organization's modules are the list on its
tenant row (app/licensing.py checks it on every request). sync() writes that list from
the subscription's active lines whenever the subscription changes, so a contract that
expires, is suspended or loses a service closes those modules on the next request -
the data stays. Per-user services (sold per user) additionally open only for users
holding a seat (user_modules()).

Lifecycle (run_due, every few minutes from the scheduler):
  trial     -> expired      the trial ended without being converted
  active    -> (renewed)    the term ended and auto-renew is on: a new term starts
  active    -> past_due     the term ended without renewal, or an invoice is overdue
  past_due  -> expired      the term ended more than the grace period ago
  past_due  -> suspended    an invoice is overdue by more than the grace period
  past_due / suspended -> active   every overdue invoice is paid (and the term runs)
Statuses that grant modules: trial, active, past_due (grace). The rest grant nothing.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.auth import User, UserRole
from app.models.subscriptions import (
    BILLING_CYCLES,
    CYCLE_MONTHS,
    GRANTING,
    PRODUCT_KINDS,
    PRODUCT_UNITS,
    SUBSCRIPTION_STATUSES,
    LicenseEdition,
    LicenseEvent,
    LicenseInvoice,
    LicenseProduct,
    LicenseRequest,
    LicensingSettings,
    SubscriptionLine,
    TenantSubscription,
    UserLicense,
)
from app.models.tenant import Tenant
from app.timeutils import utcnow

_CODE = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
LIMIT_LABELS = {"users": "Users", "ai_tokens_month": "AI tokens / month", "storage_gb": "Storage (GB)", "api_calls_day": "API calls / day",
                "sandboxes": "Sandboxes"}

# The catalog a fresh platform starts with - every price is the platform admin's to change.
DEFAULT_PRODUCTS: list[dict[str, Any]] = [
    {"code": "core_platform", "name": "BRT 360 Core Platform", "kind": "core", "unit": "org", "unit_label": "organization",
     "description": "The platform every organization runs on.", "modules": [], "prices": {"INR": 25000, "USD": 300},
     "annual_discount_pct": 10, "max_quantity": 1, "limits": {"storage_gb": 50, "api_calls_day": 50000},
     "features": ["Identity, sign-in security & MFA", "Organization structure and access", "Audit trail", "Integration hub (BIM)", "AI assistant"],
     "trial_days": 14},
    {"code": "core_user", "name": "Core User Licence", "kind": "core", "unit": "user", "unit_label": "user",
     "description": "One named user - every active user needs one.", "modules": [], "prices": {"INR": 1200, "USD": 15},
     "annual_discount_pct": 10, "requires": ["core_platform"], "limits": {"users": 1},
     "features": ["Sign-in", "Dashboard, My Work and notifications"]},
    {"code": "telecom_crm", "name": "Telecom CRM", "kind": "service", "unit": "user", "unit_label": "user",
     "description": "Customers, sales, products, orders, billing, fulfilment, inventory and field service.", "modules": ["telecom_crm"],
     "prices": {"INR": 2500, "USD": 30}, "annual_discount_pct": 10, "requires": ["core_user"],
     "features": ["Lead to cash", "Product modeller & CPQ", "Order fulfilment & inventory", "Field service", "Customer 360 & analytics"]},
    {"code": "training_management", "name": "Training & Learning", "kind": "service", "unit": "user", "unit_label": "user",
     "description": "Programs, sessions, assessments, feedback and learning outcomes.", "modules": ["training_management"],
     "prices": {"INR": 800, "USD": 10}, "annual_discount_pct": 10, "requires": ["core_user"],
     "features": ["Programs & curriculum", "Sessions & attendance", "Assessments & feedback"]},
    {"code": "flow_studio", "name": "Flow Studio", "kind": "service", "unit": "org", "unit_label": "organization",
     "description": "Design and automate reusable workflows.", "modules": ["flow_studio"], "prices": {"INR": 15000, "USD": 180},
     "annual_discount_pct": 10, "max_quantity": 1, "requires": ["core_platform"], "features": ["Visual flows", "Durable waits & approvals"]},
    {"code": "page_designer", "name": "Page Designer", "kind": "service", "unit": "org", "unit_label": "organization",
     "description": "Configure forms, layouts and validation.", "modules": ["page_designer"], "prices": {"INR": 10000, "USD": 120},
     "annual_discount_pct": 10, "max_quantity": 1, "requires": ["core_platform"], "features": ["Page layouts", "Validation rules"]},
    {"code": "custom_development", "name": "Custom Development", "kind": "service", "unit": "org", "unit_label": "organization",
     "description": "Custom fields, record types, intents and process building.", "modules": ["custom_development"],
     "prices": {"INR": 20000, "USD": 240}, "annual_discount_pct": 10, "max_quantity": 1, "requires": ["core_platform"],
     "features": ["Custom fields & record types", "Custom intents", "Process builder & functional map"]},
    {"code": "ai_credits", "name": "AI Credits", "kind": "add_on", "unit": "pack", "unit_label": "1M AI tokens / month",
     "description": "Monthly AI model allowance.", "modules": [], "prices": {"INR": 2000, "USD": 25}, "requires": ["core_platform"],
     "limits": {"ai_tokens_month": 1000000}},
    {"code": "extra_storage", "name": "Additional Storage", "kind": "add_on", "unit": "pack", "unit_label": "10 GB",
     "description": "More file and data storage.", "modules": [], "prices": {"INR": 500, "USD": 6}, "requires": ["core_platform"],
     "limits": {"storage_gb": 10}},
    {"code": "sandbox", "name": "Sandbox", "kind": "add_on", "unit": "pack", "unit_label": "sandbox",
     "description": "A full copy of the organization to try changes in.", "modules": [], "prices": {"INR": 8000, "USD": 100},
     "requires": ["core_platform"], "limits": {"sandboxes": 1}},
    {"code": "premier_support", "name": "Premier Support", "kind": "add_on", "unit": "org", "unit_label": "organization",
     "description": "24x7 support, 1-hour response on critical issues, a named success manager.", "modules": [],
     "prices": {"INR": 12000, "USD": 150}, "max_quantity": 1, "requires": ["core_platform"]},
]
DEFAULT_EDITIONS: list[dict[str, Any]] = [
    {"code": "starter", "name": "Starter", "description": "For a small team getting started with Telecom CRM.", "discount_pct": 0, "trial_days": 14,
     "items": [{"product": "core_platform", "quantity": 1}, {"product": "core_user", "quantity": 10}, {"product": "telecom_crm", "quantity": 10},
               {"product": "ai_credits", "quantity": 1}]},
    {"code": "professional", "name": "Professional", "description": "Telecom CRM with workflow automation and page design.", "discount_pct": 5,
     "highlight": True, "trial_days": 14,
     "items": [{"product": "core_platform", "quantity": 1}, {"product": "core_user", "quantity": 50}, {"product": "telecom_crm", "quantity": 50},
               {"product": "flow_studio", "quantity": 1}, {"product": "page_designer", "quantity": 1}, {"product": "ai_credits", "quantity": 5}]},
    {"code": "enterprise", "name": "Enterprise", "description": "Everything, at scale - with a sandbox and premier support.", "discount_pct": 12,
     "items": [{"product": "core_platform", "quantity": 1}, {"product": "core_user", "quantity": 200}, {"product": "telecom_crm", "quantity": 200},
               {"product": "training_management", "quantity": 200}, {"product": "flow_studio", "quantity": 1},
               {"product": "page_designer", "quantity": 1}, {"product": "custom_development", "quantity": 1},
               {"product": "ai_credits", "quantity": 20}, {"product": "sandbox", "quantity": 1}, {"product": "premier_support", "quantity": 1}]},
    {"code": "learning", "name": "Learning", "description": "Training & Learning on its own.", "discount_pct": 0, "trial_days": 14,
     "items": [{"product": "core_platform", "quantity": 1}, {"product": "core_user", "quantity": 25}, {"product": "training_management", "quantity": 25}]},
]


# ================================================================== small helpers


def today() -> dt.date:
    return utcnow().date()


def add_months(day: dt.date, months: int) -> dt.date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _date(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"'{value}' is not a date (YYYY-MM-DD)") from exc


def _pct(value: Any, what: str) -> float:
    try:
        pct = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a number") from exc
    if not 0 <= pct <= 100:
        raise ValueError(f"{what} must be between 0 and 100")
    return pct


def _money(value: float) -> float:
    return round(float(value) + 1e-9, 2)


def log(db: Session, tenant_id: int | None, action: str, summary: str, actor: str = "", detail: dict | None = None) -> None:
    db.add(LicenseEvent(tenant_id=tenant_id, action=action, summary=summary[:500], actor=(actor or "system")[:200], detail=detail))


# ================================================================== settings


def settings(db: Session) -> LicensingSettings:
    row = db.query(LicensingSettings).order_by(LicensingSettings.id).first()
    if row is None:
        row = LicensingSettings(currencies=["INR", "USD"], default_currency="INR")
        db.add(row)
        db.flush()
    return row


def settings_out(row: LicensingSettings) -> dict[str, Any]:
    return {"currencies": row.currencies or [row.default_currency], "default_currency": row.default_currency, "tax_name": row.tax_name,
            "tax_pct": row.tax_pct, "grace_days": row.grace_days, "invoice_prefix": row.invoice_prefix, "next_invoice_no": row.next_invoice_no,
            "seller_name": row.seller_name, "seller_address": row.seller_address, "seller_tax_id": row.seller_tax_id,
            "payment_terms_days": row.payment_terms_days}


def save_settings(db: Session, data: dict[str, Any], actor: str) -> LicensingSettings:
    row = settings(db)
    if "currencies" in data:
        currencies = [str(c).strip().upper() for c in data["currencies"] or [] if str(c).strip()]
        bad = [c for c in currencies if not _CURRENCY.match(c)]
        if bad or not currencies:
            raise ValueError("Currencies are 3-letter codes like INR, USD, EUR - at least one")
        row.currencies = list(dict.fromkeys(currencies))
    if "default_currency" in data:
        row.default_currency = str(data["default_currency"]).upper()
    if row.default_currency not in (row.currencies or []):
        raise ValueError(f"The default currency {row.default_currency} must be one of the currencies")
    for field in ("tax_name", "invoice_prefix", "seller_name", "seller_address", "seller_tax_id"):
        if field in data:
            setattr(row, field, str(data[field] or "").strip())
    if "tax_pct" in data:
        row.tax_pct = _pct(data["tax_pct"], "Tax")
    for field in ("grace_days", "payment_terms_days", "next_invoice_no"):
        if field in data:
            value = int(data[field])
            if value < (1 if field == "next_invoice_no" else 0):
                raise ValueError(f"{field.replace('_', ' ')} can't be negative")
            setattr(row, field, value)
    log(db, None, "settings", "Licensing settings changed", actor, {k: v for k, v in data.items()})
    db.flush()
    return row


# ================================================================== catalog


def ensure_catalog(db: Session) -> None:
    """A fresh platform gets the default catalog once (nothing is added back after the admin edits it)."""
    if db.query(LicenseProduct.id).first() is not None:
        return
    for i, p in enumerate(DEFAULT_PRODUCTS):
        db.add(LicenseProduct(sort_order=i, **{"annual_discount_pct": 0, "min_quantity": 1, "trial_days": 0, **p}))
    for i, e in enumerate(DEFAULT_EDITIONS):
        db.add(LicenseEdition(sort_order=i, **e))
    settings(db)
    db.flush()


def products(db: Session, active_only: bool = False) -> list[LicenseProduct]:
    q = db.query(LicenseProduct)
    if active_only:
        q = q.filter(LicenseProduct.is_active.is_(True))
    return q.order_by(LicenseProduct.sort_order, LicenseProduct.id).all()


def product_map(db: Session) -> dict[str, LicenseProduct]:
    return {p.code: p for p in db.query(LicenseProduct).all()}


def product_out(p: LicenseProduct) -> dict[str, Any]:
    return {"id": p.id, "code": p.code, "name": p.name, "kind": p.kind, "unit": p.unit, "unit_label": p.unit_label,
            "description": p.description, "features": p.features or [], "modules": p.modules or [], "prices": p.prices or {},
            "annual_discount_pct": p.annual_discount_pct, "min_quantity": p.min_quantity, "max_quantity": p.max_quantity,
            "requires": p.requires or [], "limits": p.limits or {}, "trial_days": p.trial_days, "is_active": p.is_active,
            "sort_order": p.sort_order}


def save_product(db: Session, data: dict[str, Any], actor: str, code: str | None = None) -> LicenseProduct:
    from app.licensing import MODULES

    row = db.query(LicenseProduct).filter(LicenseProduct.code == code).first() if code else None
    if code and row is None:
        raise LookupError(f"No product '{code}'")
    if row is None:
        new_code = str(data.get("code") or "").strip().lower()
        if not _CODE.match(new_code):
            raise ValueError("A product code is lowercase letters, digits and _ (e.g. telecom_crm)")
        if db.query(LicenseProduct.id).filter(LicenseProduct.code == new_code).first():
            raise ValueError(f"There is already a product '{new_code}'")
        row = LicenseProduct(code=new_code, sort_order=db.query(LicenseProduct).count())
        db.add(row)
    if "name" in data or row.name is None:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("A product needs a name")
        row.name = name
    for field, allowed in (("kind", PRODUCT_KINDS), ("unit", PRODUCT_UNITS)):
        if field in data:
            if data[field] not in allowed:
                raise ValueError(f"{field} is one of {', '.join(allowed)}")
            setattr(row, field, data[field])
        elif getattr(row, field) is None:
            setattr(row, field, allowed[1] if field == "kind" else "user")
    for field in ("unit_label", "description"):
        if field in data:
            setattr(row, field, str(data[field] or "").strip())
    if "features" in data:
        row.features = [str(f).strip() for f in data["features"] or [] if str(f).strip()]
    if "modules" in data:
        modules = sorted({str(m) for m in data["modules"] or []})
        unknown = [m for m in modules if m not in MODULES]
        if unknown:
            raise ValueError(f"Unknown modules: {', '.join(unknown)} (modules: {', '.join(MODULES)})")
        row.modules = modules
    if "prices" in data:
        currencies = settings(db).currencies or []
        prices: dict[str, float] = {}
        for currency, amount in (data["prices"] or {}).items():
            currency = str(currency).upper()
            if amount in (None, ""):
                continue
            if currency not in currencies:
                raise ValueError(f"{currency} isn't one of the platform's currencies ({', '.join(currencies)}) - add it in Settings first")
            value = float(amount)
            if value < 0:
                raise ValueError("A price can't be negative")
            prices[currency] = _money(value)
        row.prices = prices
    if "annual_discount_pct" in data:
        row.annual_discount_pct = _pct(data["annual_discount_pct"], "The annual discount")
    if "min_quantity" in data:
        row.min_quantity = max(0, int(data["min_quantity"] or 0))
    if "max_quantity" in data:
        row.max_quantity = int(data["max_quantity"]) if data["max_quantity"] not in (None, "") else None
    if row.max_quantity is not None and row.max_quantity < (row.min_quantity or 0):
        raise ValueError("The maximum quantity is below the minimum")
    if "requires" in data:
        known = set(product_map(db))
        requires = [str(r) for r in data["requires"] or [] if str(r)]
        missing = [r for r in requires if r not in known or r == row.code]
        if missing:
            raise ValueError(f"Can't require {', '.join(missing)}")
        row.requires = requires
    if "limits" in data:
        limits: dict[str, float] = {}
        for k, v in (data["limits"] or {}).items():
            if v in (None, ""):
                continue
            if not re.match(r"^[a-z][a-z0-9_]{0,40}$", str(k)):
                raise ValueError(f"Limit '{k}' - use names like ai_tokens_month, storage_gb")
            limits[str(k)] = float(v)
        row.limits = limits
    for field in ("trial_days", "sort_order"):
        if field in data:
            setattr(row, field, max(0, int(data[field] or 0)))
    if "is_active" in data:
        row.is_active = bool(data["is_active"])
    for field, default in (("annual_discount_pct", 0.0), ("min_quantity", 1), ("trial_days", 0), ("unit_label", "user"), ("description", ""), ("is_active", True)):
        if getattr(row, field) is None:
            setattr(row, field, default)
    db.flush()
    log(db, None, "product_saved", f"Product {row.code} ({row.name}) saved", actor, {k: v for k, v in data.items() if k != "description"})
    return row


def edition_out(db: Session, e: LicenseEdition, currency: str | None = None) -> dict[str, Any]:
    currency = currency or settings(db).default_currency
    catalog = product_map(db)
    priced = _price_lines(catalog, [(i["product"], int(i.get("quantity") or 0), None, 0.0) for i in e.items or [] if i.get("product") in catalog],
                          currency, "monthly")
    net = sum(line["monthly"] for line in priced) * (1 - (e.discount_pct or 0) / 100)
    return {"id": e.id, "code": e.code, "name": e.name, "description": e.description, "items": e.items or [], "discount_pct": e.discount_pct,
            "trial_days": e.trial_days, "highlight": e.highlight, "is_active": e.is_active, "sort_order": e.sort_order,
            "monthly_estimate": _money(net), "currency": currency,
            "modules": sorted({m for i in e.items or [] for m in (catalog[i["product"]].modules or []) if i.get("product") in catalog})}


def save_edition(db: Session, data: dict[str, Any], actor: str, code: str | None = None) -> LicenseEdition:
    row = db.query(LicenseEdition).filter(LicenseEdition.code == code).first() if code else None
    if code and row is None:
        raise LookupError(f"No edition '{code}'")
    if row is None:
        new_code = str(data.get("code") or "").strip().lower()
        if not _CODE.match(new_code):
            raise ValueError("An edition code is lowercase letters, digits and _ (e.g. professional)")
        if db.query(LicenseEdition.id).filter(LicenseEdition.code == new_code).first():
            raise ValueError(f"There is already an edition '{new_code}'")
        row = LicenseEdition(code=new_code, sort_order=db.query(LicenseEdition).count(), discount_pct=0.0, trial_days=0, highlight=False,
                             is_active=True, description="")
        db.add(row)
    if "name" in data or row.name is None:
        if not str(data.get("name") or "").strip():
            raise ValueError("An edition needs a name")
        row.name = str(data["name"]).strip()
    if "description" in data:
        row.description = str(data["description"] or "")
    if "items" in data:
        catalog = product_map(db)
        items = []
        for item in data["items"] or []:
            code_ = str(item.get("product") or "")
            if code_ not in catalog:
                raise ValueError(f"No product '{code_}'")
            items.append({"product": code_, "quantity": max(1, int(item.get("quantity") or 1))})
        problems = check_lines(catalog, [(i["product"], i["quantity"]) for i in items])
        if problems:
            raise ValueError("; ".join(problems))
        row.items = items
    if "discount_pct" in data:
        row.discount_pct = _pct(data["discount_pct"], "The edition discount")
    for field in ("trial_days", "sort_order"):
        if field in data:
            setattr(row, field, max(0, int(data[field] or 0)))
    for field in ("highlight", "is_active"):
        if field in data:
            setattr(row, field, bool(data[field]))
    db.flush()
    log(db, None, "edition_saved", f"Edition {row.code} ({row.name}) saved", actor)
    return row


def check_lines(catalog: dict[str, LicenseProduct], lines: list[tuple[str, int]]) -> list[str]:
    """What's wrong with a set of (product, quantity): a core product, requirements, quantity bounds, seats."""
    problems: list[str] = []
    have = {code: qty for code, qty in lines if qty > 0}
    if not any(catalog[c].kind == "core" for c in have if c in catalog):
        problems.append("A subscription needs the Core platform")
    for code, qty in have.items():
        p = catalog.get(code)
        if p is None:
            problems.append(f"No product '{code}'")
            continue
        if qty < (p.min_quantity or 0):
            problems.append(f"{p.name}: at least {p.min_quantity}")
        if p.max_quantity is not None and qty > p.max_quantity:
            problems.append(f"{p.name}: at most {p.max_quantity}")
        for need in p.requires or []:
            if need not in have:
                problems.append(f"{p.name} needs {catalog[need].name if need in catalog else need}")
    core_seats = sum(q for c, q in have.items() if c in catalog and catalog[c].kind == "core" and catalog[c].unit == "user")
    for code, qty in have.items():
        p = catalog.get(code)
        if p is not None and p.unit == "user" and p.kind != "core" and core_seats and qty > core_seats:
            problems.append(f"{p.name}: {qty} seats but only {core_seats} Core user licences")
    return problems


# ================================================================== pricing


def _price_lines(catalog: dict[str, LicenseProduct], lines: list[tuple[str, int, float | None, float]], currency: str,
                 cycle: str) -> list[dict[str, Any]]:
    out = []
    for code, qty, override, discount in lines:
        p = catalog[code]
        list_price = (p.prices or {}).get(currency)
        unit = override if override is not None else list_price
        annual_off = p.annual_discount_pct if cycle == "annual" and override is None else 0.0
        effective = None if unit is None else unit * (1 - annual_off / 100) * (1 - (discount or 0) / 100)
        out.append({"product": code, "name": p.name, "kind": p.kind, "unit": p.unit, "unit_label": p.unit_label, "quantity": qty,
                    "list_price": list_price, "unit_price": unit, "overridden": override is not None, "annual_discount_pct": annual_off,
                    "discount_pct": discount or 0.0, "effective_unit_price": None if effective is None else _money(effective),
                    "monthly": 0.0 if effective is None else _money(effective * qty), "priced": effective is not None})
    return out


def active_lines(sub: TenantSubscription, lines: list[SubscriptionLine], on: dt.date | None = None) -> list[SubscriptionLine]:
    on = on or today()
    # a line's own dates only - the contract's term is governed by its status (an early renewal keeps granting)
    return [ln for ln in lines if ln.is_active and ln.quantity > 0 and (ln.start_date is None or ln.start_date <= on)
            and (ln.end_date is None or ln.end_date >= on)]


def lines_of(db: Session, sub: TenantSubscription) -> list[SubscriptionLine]:
    return db.query(SubscriptionLine).filter(SubscriptionLine.subscription_id == sub.id).order_by(SubscriptionLine.id).all()


def pricing(db: Session, sub: TenantSubscription, lines: list[SubscriptionLine] | None = None) -> dict[str, Any]:
    """The contract's price: per line, then the contract discount, tax, MRR / ARR / per billing period / term value."""
    catalog = product_map(db)
    lines = [ln for ln in (lines if lines is not None else lines_of(db, sub)) if ln.is_active and ln.product_code in catalog]
    priced = _price_lines(catalog, [(ln.product_code, ln.quantity, ln.unit_price, ln.discount_pct) for ln in lines], sub.currency, sub.billing_cycle)
    for row, ln in zip(priced, lines):
        row.update(line_id=ln.id, start_date=ln.start_date, end_date=ln.end_date, notes=ln.notes)
    subtotal = sum(r["monthly"] for r in priced)
    discount = subtotal * (sub.discount_pct or 0) / 100
    net = subtotal - discount
    tax_pct = sub.tax_pct if sub.tax_pct is not None else settings(db).tax_pct
    tax = net * tax_pct / 100
    months = CYCLE_MONTHS.get(sub.billing_cycle, 1)
    return {"currency": sub.currency, "billing_cycle": sub.billing_cycle, "lines": priced,
            "unpriced": [r["name"] for r in priced if not r["priced"]],
            "subtotal_monthly": _money(subtotal), "discount_pct": sub.discount_pct, "discount_monthly": _money(discount),
            "net_monthly": _money(net), "tax_name": settings(db).tax_name, "tax_pct": tax_pct, "tax_monthly": _money(tax),
            "total_monthly": _money(net + tax), "mrr": _money(net), "arr": _money(net * 12),
            "per_period": _money((net + tax) * months), "period_months": months, "term_value": _money(net * (sub.term_months or 12))}


# ================================================================== entitlements


def subscription(db: Session, tenant_id: int) -> TenantSubscription | None:
    return db.query(TenantSubscription).filter(TenantSubscription.tenant_id == tenant_id).first()


def grace_days(db: Session, sub: TenantSubscription) -> int:
    return sub.grace_days if sub.grace_days is not None else settings(db).grace_days


def granting(db: Session, sub: TenantSubscription, on: dt.date | None = None) -> bool:
    on = on or today()
    if sub.status not in GRANTING:
        return False
    if sub.status == "trial" and sub.trial_ends and on > sub.trial_ends:
        return False
    if sub.status == "past_due" and sub.term_end and on > sub.term_end + dt.timedelta(days=grace_days(db, sub)):
        return False
    return True


def entitlements(db: Session, sub: TenantSubscription, on: dt.date | None = None) -> dict[str, Any]:
    """What the contract grants today: modules (org-wide and per-user), limits, seats per per-user product."""
    catalog = product_map(db)
    lines = active_lines(sub, lines_of(db, sub), on) if granting(db, sub, on) else []
    org_modules: set[str] = set()
    seat_modules: dict[str, list[str]] = {}
    seats: dict[str, int] = {}
    limits: dict[str, float] = {}
    for ln in lines:
        p = catalog.get(ln.product_code)
        if p is None:  # a retired catalog product keeps granting what it was sold with - only a missing one doesn't
            continue
        if p.unit == "user":
            seats[p.code] = seats.get(p.code, 0) + ln.quantity
            if p.modules:
                seat_modules[p.code] = list(p.modules)
        else:
            org_modules |= set(p.modules or [])
        for k, v in (p.limits or {}).items():
            limits[k] = limits.get(k, 0) + v * ln.quantity
    modules = org_modules | {m for ms in seat_modules.values() for m in ms}
    return {"granting": bool(lines), "modules": sorted(modules), "org_modules": sorted(org_modules), "seat_modules": seat_modules,
            "seats": seats, "limits": limits}


def sync(db: Session, tenant_id: int, actor: str = "system") -> dict[str, Any] | None:
    """The tenant's module list from its subscription - what every request is checked against.
    Returns {"before", "after"} (None when the tenant has no subscription: its list is left alone)."""
    from app.db import forget

    sub = subscription(db, tenant_id)
    tenant = db.get(Tenant, tenant_id)
    if sub is None or tenant is None:
        return None
    before = sorted(tenant.licensed_modules or []) if tenant.licensed_modules is not None else None
    after = entitlements(db, sub)["modules"]
    if before != after:
        tenant.licensed_modules = after
        log(db, tenant_id, "modules", f"Modules now: {', '.join(after) or 'none'}", actor, {"before": before, "after": after})
    forget(db, ("licensed_modules", tenant_id))
    forget(db, ("user_modules", tenant_id))
    db.flush()
    return {"before": before or [], "after": after}


def user_modules(db: Session, user: User) -> set[str] | None:
    """The modules THIS user may use, or None = the organization's (no subscription). A per-user service's
    module opens only for users holding its seat; org-wide products open theirs for everyone."""
    from app.db import cached

    if user.tenant_id is None:
        return None

    def load():
        sub = subscription(db, user.tenant_id)
        if sub is None:
            return None
        ent = entitlements(db, sub)
        return ent

    ent = cached(db, ("user_modules", user.tenant_id), (TenantSubscription, SubscriptionLine, LicenseProduct), load)
    if ent is None:
        return None
    held = held_products(db, user.tenant_id, user.id)
    allowed = set(ent["org_modules"])
    for code, modules in ent["seat_modules"].items():
        if code in held:
            allowed |= set(modules)
    return allowed


def held_products(db: Session, tenant_id: int, user_id: int) -> set[str]:
    from app.db import cached

    rows = cached(db, ("user_licenses", tenant_id), UserLicense,
                  lambda: [(r.user_id, r.product_code) for r in db.query(UserLicense).filter(UserLicense.tenant_id == tenant_id)])
    return {code for uid, code in rows if uid == user_id}


# ================================================================== subscriptions


def save_subscription(db: Session, tenant_id: int, data: dict[str, Any], actor: str) -> TenantSubscription:
    if db.get(Tenant, tenant_id) is None:
        raise LookupError("Tenant not found")
    ensure_catalog(db)
    conf = settings(db)
    sub = subscription(db, tenant_id)
    created = sub is None
    if sub is None:
        sub = TenantSubscription(tenant_id=tenant_id, status="trial", currency=conf.default_currency, billing_cycle="annual", term_months=12,
                                 auto_renew=True, discount_pct=0.0, auto_assign_seats=True, contract_ref="", po_number="", billing_contact="",
                                 billing_email="", billing_address="", tax_id="", notes="", status_reason="")
        db.add(sub)
        db.flush()
    if "currency" in data:
        currency = str(data["currency"]).upper()
        if currency not in (conf.currencies or []):
            raise ValueError(f"{currency} isn't one of the platform's currencies ({', '.join(conf.currencies or [])})")
        sub.currency = currency
    if "billing_cycle" in data:
        if data["billing_cycle"] not in BILLING_CYCLES:
            raise ValueError(f"Billing cycle is one of {', '.join(BILLING_CYCLES)}")
        sub.billing_cycle = data["billing_cycle"]
    if "term_months" in data:
        months = int(data["term_months"] or 0)
        if months < 1 or months > 120:
            raise ValueError("The term is 1 to 120 months")
        sub.term_months = months
    for field in ("term_start", "term_end", "trial_ends"):
        if field in data:
            setattr(sub, field, _date(data[field]))
    if "term_start" in data and sub.term_start and "term_end" not in data:
        sub.term_end = add_months(sub.term_start, sub.term_months) - dt.timedelta(days=1)
    if sub.term_start and sub.term_end and sub.term_end < sub.term_start:
        raise ValueError("The term ends before it starts")
    if "discount_pct" in data:
        sub.discount_pct = _pct(data["discount_pct"], "The contract discount")
    if "tax_pct" in data:
        sub.tax_pct = None if data["tax_pct"] in (None, "") else _pct(data["tax_pct"], "Tax")
    if "grace_days" in data:
        sub.grace_days = None if data["grace_days"] in (None, "") else max(0, int(data["grace_days"]))
    for field in ("auto_renew", "auto_assign_seats"):
        if field in data:
            setattr(sub, field, bool(data[field]))
    for field in ("contract_ref", "po_number", "billing_contact", "billing_email", "billing_address", "tax_id", "notes"):
        if field in data:
            setattr(sub, field, str(data[field] or "").strip())
    if created:
        log(db, tenant_id, "created", f"Subscription created ({sub.currency}, {sub.billing_cycle})", actor)
    if data.get("edition_code"):
        apply_edition(db, sub, str(data["edition_code"]), actor, start_trial=created)
    elif created and not data.get("edition_code"):
        sub.trial_ends = sub.trial_ends or today() + dt.timedelta(days=14)
    else:
        log(db, tenant_id, "updated", "Subscription terms changed", actor, {k: v for k, v in data.items() if k not in ("notes", "billing_address")})
    db.flush()
    sync(db, tenant_id, actor)
    return sub


def apply_edition(db: Session, sub: TenantSubscription, code: str, actor: str, start_trial: bool = False) -> None:
    """The edition's products become the contract's lines (other lines are kept; listed ones take the edition's quantity)."""
    edition = db.query(LicenseEdition).filter(LicenseEdition.code == code).first()
    if edition is None:
        raise ValueError(f"No edition '{code}'")
    existing = {ln.product_code: ln for ln in lines_of(db, sub)}
    for item in edition.items or []:
        ln = existing.get(item["product"])
        if ln is None:
            db.add(SubscriptionLine(tenant_id=sub.tenant_id, subscription_id=sub.id, product_code=item["product"], quantity=int(item["quantity"]),
                                    discount_pct=0.0, is_active=True, notes=""))
        else:
            ln.quantity, ln.is_active = int(item["quantity"]), True
    sub.edition_code = edition.code
    sub.discount_pct = edition.discount_pct or sub.discount_pct
    if start_trial and edition.trial_days:
        sub.status, sub.trial_ends = "trial", today() + dt.timedelta(days=edition.trial_days)
    log(db, sub.tenant_id, "edition", f"Edition {edition.name} applied", actor)
    db.flush()


def save_line(db: Session, sub: TenantSubscription, data: dict[str, Any], actor: str) -> SubscriptionLine:
    catalog = product_map(db)
    code = str(data.get("product_code") or "")
    if code not in catalog:
        raise ValueError(f"No product '{code}'")
    ln = db.query(SubscriptionLine).filter(SubscriptionLine.subscription_id == sub.id, SubscriptionLine.product_code == code).first()
    before = None if ln is None else {"quantity": ln.quantity, "unit_price": ln.unit_price, "discount_pct": ln.discount_pct}
    if ln is None:
        ln = SubscriptionLine(tenant_id=sub.tenant_id, subscription_id=sub.id, product_code=code, quantity=1, discount_pct=0.0, is_active=True, notes="")
        db.add(ln)
    if "quantity" in data:
        qty = int(data["quantity"] or 0)
        if qty < 0:
            raise ValueError("The quantity can't be negative")
        ln.quantity = qty
    if "unit_price" in data:
        ln.unit_price = None if data["unit_price"] in (None, "") else _money(float(data["unit_price"]))
        if ln.unit_price is not None and ln.unit_price < 0:
            raise ValueError("A price can't be negative")
    if "discount_pct" in data:
        ln.discount_pct = _pct(data["discount_pct"], "The line discount")
    for field in ("start_date", "end_date"):
        if field in data:
            setattr(ln, field, _date(data[field]))
    if "is_active" in data:
        ln.is_active = bool(data["is_active"])
    if "notes" in data:
        ln.notes = str(data["notes"] or "")[:300]
    p = catalog[code]
    if p.unit == "user":
        assigned = db.query(UserLicense).filter(UserLicense.tenant_id == sub.tenant_id, UserLicense.product_code == code).count()
        if ln.is_active and ln.quantity < assigned:
            raise ValueError(f"{p.name}: {assigned} seats are assigned - unassign {assigned - ln.quantity} before lowering to {ln.quantity}")
    db.flush()
    log(db, sub.tenant_id, "line", f"{p.name}: {ln.quantity} x {p.unit_label}" + (f" at {ln.unit_price:g}" if ln.unit_price is not None else "")
        + ("" if ln.is_active else " (removed)"), actor, {"before": before, "after": {"quantity": ln.quantity, "unit_price": ln.unit_price,
                                                                                       "discount_pct": ln.discount_pct}})
    sync(db, sub.tenant_id, actor)
    return ln


def remove_line(db: Session, sub: TenantSubscription, code: str, actor: str) -> None:
    ln = db.query(SubscriptionLine).filter(SubscriptionLine.subscription_id == sub.id, SubscriptionLine.product_code == code).first()
    if ln is None:
        raise LookupError(f"The contract has no '{code}'")
    removed = db.query(UserLicense).filter(UserLicense.tenant_id == sub.tenant_id, UserLicense.product_code == code)
    count = removed.count()
    removed.delete(synchronize_session=False)
    db.delete(ln)
    db.flush()
    log(db, sub.tenant_id, "line_removed", f"{code} removed" + (f" ({count} seat assignments released)" if count else ""), actor)
    sync(db, sub.tenant_id, actor)


TRANSITIONS: dict[str, tuple[str, ...]] = {
    "trial": ("active", "cancelled", "expired", "suspended"),
    "active": ("past_due", "suspended", "cancelled"),
    "past_due": ("active", "suspended", "cancelled", "expired"),
    "suspended": ("active", "cancelled"),
    "cancelled": ("active", "trial"),
    "expired": ("active", "trial"),
}


def set_status(db: Session, sub: TenantSubscription, status: str, reason: str, actor: str) -> TenantSubscription:
    if status not in SUBSCRIPTION_STATUSES:
        raise ValueError(f"Status is one of {', '.join(SUBSCRIPTION_STATUSES)}")
    if status == sub.status:
        return sub
    if status not in TRANSITIONS[sub.status]:
        raise ValueError(f"A {sub.status} subscription can't become {status} (it can become {', '.join(TRANSITIONS[sub.status])})")
    if status in ("active", "trial"):
        problems = check_lines(product_map(db), [(ln.product_code, ln.quantity) for ln in lines_of(db, sub) if ln.is_active])
        priced = pricing(db, sub)
        if priced["unpriced"]:
            problems.append(f"No {sub.currency} price for {', '.join(priced['unpriced'])} - set one in the catalog or override it on the line")
        if problems:
            raise ValueError("Fix the contract first - " + "; ".join(problems))
    if status == "active":
        if sub.term_start is None or (sub.term_end and sub.term_end < today()):
            sub.term_start = today()
            sub.term_end = add_months(sub.term_start, sub.term_months) - dt.timedelta(days=1)
        sub.trial_ends = None
    if status == "trial":
        sub.trial_ends = today() + dt.timedelta(days=14)
    previous = sub.status
    sub.status, sub.status_reason = status, (reason or "")[:300]
    db.flush()
    log(db, sub.tenant_id, "status", f"{previous} -> {status}" + (f": {reason}" if reason else ""), actor)
    sync(db, sub.tenant_id, actor)
    if status in ("active", "trial") and sub.auto_assign_seats:
        auto_fill(db, sub.tenant_id, actor)
    return sub


def renew(db: Session, sub: TenantSubscription, actor: str, months: int | None = None) -> TenantSubscription:
    start = (sub.term_end + dt.timedelta(days=1)) if sub.term_end else today()
    months = months or sub.term_months
    sub.term_start, sub.term_end = start, add_months(start, months) - dt.timedelta(days=1)
    if sub.status in ("past_due", "expired"):
        sub.status, sub.status_reason = "active", "renewed"
    db.flush()
    log(db, sub.tenant_id, "renewed", f"Renewed {start} to {sub.term_end}", actor)
    sync(db, sub.tenant_id, actor)
    return sub


# ================================================================== seats


def seat_summary(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    sub = subscription(db, tenant_id)
    if sub is None:
        return []
    ent = entitlements(db, sub)
    catalog = product_map(db)
    counts: dict[str, int] = {}
    for (code,) in db.query(UserLicense.product_code).filter(UserLicense.tenant_id == tenant_id):
        counts[code] = counts.get(code, 0) + 1
    codes = list(dict.fromkeys(list(ent["seats"]) + list(counts)))
    out = []
    for code in sorted(codes, key=lambda c: (catalog[c].kind != "core", catalog[c].sort_order) if c in catalog else (True, 999)):
        purchased, assigned = ent["seats"].get(code, 0), counts.get(code, 0)
        out.append({"product": code, "name": catalog[code].name if code in catalog else code, "kind": catalog[code].kind if code in catalog else "",
                    "modules": (catalog[code].modules or []) if code in catalog else [], "purchased": purchased, "assigned": assigned,
                    "free": max(0, purchased - assigned), "over": max(0, assigned - purchased)})
    return out


def _free(db: Session, tenant_id: int, code: str) -> int:
    return next((s["free"] for s in seat_summary(db, tenant_id) if s["product"] == code), 0)


def assign(db: Session, tenant_id: int, user_id: int, code: str, actor: str) -> UserLicense:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise LookupError("User not found")
    if not user.is_active:
        raise ValueError(f"{user.name} is deactivated")
    existing = db.query(UserLicense).filter(UserLicense.user_id == user_id, UserLicense.product_code == code).first()
    if existing is not None:
        return existing
    catalog = product_map(db)
    p = catalog.get(code)
    if p is None or p.unit != "user":
        raise ValueError(f"'{code}' isn't sold per user")
    if _free(db, tenant_id, code) <= 0:
        raise ValueError(f"No free {p.name} seat - buy more or unassign one first")
    if p.kind != "core":
        core = [c for c, cp in catalog.items() if cp.kind == "core" and cp.unit == "user"]
        if core and not db.query(UserLicense.id).filter(UserLicense.user_id == user_id, UserLicense.product_code.in_(core)).first():
            raise ValueError(f"{user.name} needs a Core user licence first")
    row = UserLicense(tenant_id=tenant_id, user_id=user_id, product_code=code, assigned_by=actor[:200])
    db.add(row)
    db.flush()
    log(db, tenant_id, "seat_assigned", f"{p.name} seat to {user.name}", actor)
    return row


def unassign(db: Session, tenant_id: int, user_id: int, code: str, actor: str) -> None:
    catalog = product_map(db)
    rows = db.query(UserLicense).filter(UserLicense.tenant_id == tenant_id, UserLicense.user_id == user_id)
    target = rows.filter(UserLicense.product_code == code).first()
    if target is None:
        raise LookupError("That user doesn't hold that licence")
    p = catalog.get(code)
    if p is not None and p.kind == "core":
        # a user without a Core licence can't hold service seats either
        others = [r for r in rows.all() if r.product_code != code and r.product_code in catalog and catalog[r.product_code].kind != "core"]
        for r in others:
            db.delete(r)
    db.delete(target)
    db.flush()
    user = db.get(User, user_id)
    log(db, tenant_id, "seat_released", f"{p.name if p else code} seat released from {user.name if user else user_id}", actor)


def auto_fill(db: Session, tenant_id: int, actor: str = "system") -> int:
    """Free seats to active users who lack them - Core first, then each per-user service. Returns how many."""
    users = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True), User.role != UserRole.platform_admin).order_by(User.id).all()
    given = 0
    for seat in sorted(seat_summary(db, tenant_id), key=lambda s: s["kind"] != "core"):
        free = seat["free"]
        holders = {uid for (uid,) in db.query(UserLicense.user_id).filter(UserLicense.tenant_id == tenant_id,
                                                                           UserLicense.product_code == seat["product"])}
        for u in users:
            if free <= 0:
                break
            if u.id in holders:
                continue
            try:
                assign(db, tenant_id, u.id, seat["product"], actor)
            except ValueError:
                continue
            free -= 1
            given += 1
    return given


def on_user_created(db: Session, user: User, actor: str = "system") -> None:
    """A new user takes a Core user licence (refused when none is free) and, when the contract says so,
    a free seat of each per-user service."""
    sub = subscription(db, user.tenant_id) if user.tenant_id else None
    if sub is None:
        return
    catalog = product_map(db)
    core = [s for s in seat_summary(db, user.tenant_id) if s["kind"] == "core" and catalog.get(s["product"]) and catalog[s["product"]].unit == "user"]
    if core:
        seat = next((s for s in core if s["free"] > 0), None)
        if seat is None:
            raise ValueError(f"All {sum(s['purchased'] for s in core)} Core user licences are in use - "
                             "deactivate a user or ask your provider for more (Admin -> Licenses & Billing)")
        assign(db, user.tenant_id, user.id, seat["product"], actor)
    if sub.auto_assign_seats:
        for s in seat_summary(db, user.tenant_id):
            if s["kind"] != "core" and s["free"] > 0:
                try:
                    assign(db, user.tenant_id, user.id, s["product"], actor)
                except ValueError:
                    pass


def on_user_deactivated(db: Session, user: User, actor: str = "system") -> None:
    rows = db.query(UserLicense).filter(UserLicense.user_id == user.id)
    if rows.count():
        rows.delete(synchronize_session=False)
        log(db, user.tenant_id, "seat_released", f"Seats released - {user.name} deactivated", actor)
        db.flush()


# ================================================================== usage


def usage(db: Session, tenant_id: int) -> dict[str, float | None]:
    users = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True)).count()
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    savepoint = db.begin_nested()
    try:  # the AI gateway's call log (no import - it belongs to the AI module)
        tokens = db.execute(text("SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM ai_calls WHERE tenant_id = :t AND at >= :s"),
                            {"t": tenant_id, "s": start}).scalar() or 0
        savepoint.commit()
    except Exception:  # noqa: BLE001 - no call log in this install
        savepoint.rollback()
        tokens = None
    return {"users": users, "ai_tokens_month": tokens, "storage_gb": None, "api_calls_day": None, "sandboxes": None}


# ================================================================== invoices


def invoice_out(inv: LicenseInvoice) -> dict[str, Any]:
    overdue = inv.status == "issued" and inv.due_on < today()
    return {"id": inv.id, "tenant_id": inv.tenant_id, "number": inv.number, "status": "overdue" if overdue else inv.status, "currency": inv.currency,
            "period_start": inv.period_start, "period_end": inv.period_end, "issued_on": inv.issued_on, "due_on": inv.due_on,
            "lines": inv.lines or [], "subtotal": inv.subtotal, "discount": inv.discount, "tax": inv.tax, "total": inv.total,
            "paid_on": inv.paid_on, "payment_ref": inv.payment_ref, "bill_to": inv.bill_to or {}}


def generate_invoice(db: Session, sub: TenantSubscription, actor: str, period_start: dt.date | None = None) -> LicenseInvoice:
    """The next billing period's invoice (from the term start, one billing cycle at a time)."""
    conf = settings(db)
    last = (db.query(LicenseInvoice).filter(LicenseInvoice.tenant_id == sub.tenant_id, LicenseInvoice.status != "void")
            .order_by(LicenseInvoice.period_end.desc()).first())
    start = period_start or (last.period_end + dt.timedelta(days=1) if last else sub.term_start or today())
    months = CYCLE_MONTHS.get(sub.billing_cycle, 1)
    end = add_months(start, months) - dt.timedelta(days=1)
    if sub.term_end and start > sub.term_end:
        raise ValueError(f"Everything up to the term end ({sub.term_end}) is invoiced - renew the term first")
    price = pricing(db, sub)
    if price["unpriced"]:
        raise ValueError(f"No {sub.currency} price for {', '.join(price['unpriced'])}")
    lines = [{"product": r["product"], "description": f"{r['name']} - {r['quantity']} x {r['unit_label']} x {months} month{'s' if months > 1 else ''}",
              "quantity": r["quantity"], "unit_price": r["effective_unit_price"], "months": months, "amount": _money(r["monthly"] * months)}
             for r in price["lines"] if r["monthly"]]
    subtotal = sum(line["amount"] for line in lines)
    discount = subtotal * (sub.discount_pct or 0) / 100
    tax = (subtotal - discount) * price["tax_pct"] / 100
    tenant = db.get(Tenant, sub.tenant_id)
    number = f"{conf.invoice_prefix}{conf.next_invoice_no:06d}"
    conf.next_invoice_no += 1
    issued = today()
    inv = LicenseInvoice(tenant_id=sub.tenant_id, number=number, status="issued", currency=sub.currency, period_start=start, period_end=end,
                         issued_on=issued, due_on=issued + dt.timedelta(days=conf.payment_terms_days), lines=lines, subtotal=_money(subtotal),
                         discount=_money(discount), tax=_money(tax), total=_money(subtotal - discount + tax), payment_ref="",
                         bill_to={"name": (tenant.display_name or tenant.name) if tenant else "", "contact": sub.billing_contact,
                                  "email": sub.billing_email, "address": sub.billing_address, "tax_id": sub.tax_id, "po_number": sub.po_number,
                                  "contract_ref": sub.contract_ref, "tax_name": price["tax_name"], "tax_pct": price["tax_pct"],
                                  "seller": {"name": conf.seller_name, "address": conf.seller_address, "tax_id": conf.seller_tax_id}})
    db.add(inv)
    db.flush()
    log(db, sub.tenant_id, "invoice", f"Invoice {number} issued: {inv.currency} {inv.total:,.2f} for {start} to {end}", actor)
    return inv


def set_invoice_status(db: Session, inv: LicenseInvoice, status: str, actor: str, payment_ref: str = "") -> LicenseInvoice:
    if status not in ("paid", "void", "issued"):
        raise ValueError("An invoice can be marked paid, void or issued")
    if inv.status == "void" and status != "void":
        raise ValueError("A void invoice stays void")
    inv.status = status
    inv.paid_on = today() if status == "paid" else None
    inv.payment_ref = payment_ref[:120] if status == "paid" else ""
    db.flush()
    log(db, inv.tenant_id, "invoice", f"Invoice {inv.number} {status}" + (f" ({payment_ref})" if payment_ref else ""), actor)
    sub = subscription(db, inv.tenant_id)
    if sub is not None:
        advance(db, sub, actor)
    return inv


# ================================================================== lifecycle


def advance(db: Session, sub: TenantSubscription, actor: str = "system", on: dt.date | None = None) -> str | None:
    """One subscription's due status change (see the module docstring). Returns the new status, if any."""
    on = on or today()
    grace = dt.timedelta(days=grace_days(db, sub))
    overdue = (db.query(LicenseInvoice).filter(LicenseInvoice.tenant_id == sub.tenant_id, LicenseInvoice.status == "issued",
                                               LicenseInvoice.due_on < on).order_by(LicenseInvoice.due_on).first())
    new, reason = None, ""
    if sub.status == "trial" and sub.trial_ends and on > sub.trial_ends:
        new, reason = "expired", "the trial ended"
    elif sub.status in ("active", "past_due") and sub.term_end and on > sub.term_end:
        if sub.auto_renew and not overdue:
            renew(db, sub, actor)
            return "renewed"
        if sub.status == "active":
            new, reason = "past_due", "the term ended without renewal"
        elif on > sub.term_end + grace:
            new, reason = "expired", "the term ended and the grace period is over"
    elif sub.status == "active" and overdue:
        new, reason = "past_due", f"invoice {overdue.number} is overdue"
    elif sub.status == "past_due" and overdue and on > overdue.due_on + grace:
        new, reason = "suspended", f"invoice {overdue.number} is overdue beyond the grace period"
    elif sub.status in ("past_due", "suspended") and not overdue and (sub.term_end is None or on <= sub.term_end) \
            and sub.status_reason.startswith("invoice"):
        new, reason = "active", "overdue invoices paid"
    if new is None:
        return None
    previous = sub.status
    sub.status, sub.status_reason = new, reason
    db.flush()
    log(db, sub.tenant_id, "status", f"{previous} -> {new}: {reason}", actor)
    sync(db, sub.tenant_id, actor)
    return new


_last_run = 0.0


def run_due(session_factory, every_seconds: float = 900.0) -> None:
    """The scheduler's job: every subscription's due status change (at most every 15 minutes)."""
    global _last_run
    if time.monotonic() - _last_run < every_seconds:
        return
    _last_run = time.monotonic()
    with session_factory() as db:
        for sub in db.query(TenantSubscription).all():
            try:
                advance(db, sub)
                db.commit()
            except Exception:  # noqa: BLE001 - one bad contract never stops the others
                db.rollback()


# ================================================================== requests


def request_out(r: LicenseRequest, catalog: dict[str, LicenseProduct] | None = None) -> dict[str, Any]:
    p = (catalog or {}).get(r.product_code or "")
    return {"id": r.id, "tenant_id": r.tenant_id, "kind": r.kind, "product_code": r.product_code, "product_name": p.name if p else r.product_code,
            "quantity": r.quantity, "message": r.message, "status": r.status, "requested_by": r.requested_by, "response": r.response,
            "created_at": r.created_at, "resolved_at": r.resolved_at}


def create_request(db: Session, tenant_id: int, data: dict[str, Any], actor: str) -> LicenseRequest:
    kind = str(data.get("kind") or "seats")
    if kind not in ("seats", "product", "renewal", "other"):
        raise ValueError("A request is for seats, a product, a renewal or other")
    code = data.get("product_code") or None
    if kind in ("seats", "product"):
        if code not in product_map(db):
            raise ValueError("Which product?")
        if int(data.get("quantity") or 0) < 1:
            raise ValueError("How many?")
    row = LicenseRequest(tenant_id=tenant_id, kind=kind, product_code=code, quantity=int(data.get("quantity") or 0),
                         message=str(data.get("message") or "")[:2000], status="open", requested_by=actor[:200], response="")
    db.add(row)
    db.flush()
    log(db, tenant_id, "request", f"Requested {kind}" + (f": {row.quantity} x {code}" if code else ""), actor)
    return row


def resolve_request(db: Session, req: LicenseRequest, approve: bool, response: str, actor: str, apply: bool = True) -> LicenseRequest:
    if req.status != "open":
        raise ValueError(f"The request is already {req.status}")
    if approve and apply and req.kind in ("seats", "product") and req.product_code:
        sub = subscription(db, req.tenant_id)
        if sub is None:
            raise ValueError("The organization has no subscription yet - create one first")
        ln = db.query(SubscriptionLine).filter(SubscriptionLine.subscription_id == sub.id, SubscriptionLine.product_code == req.product_code).first()
        current = ln.quantity if ln is not None and ln.is_active else 0
        save_line(db, sub, {"product_code": req.product_code, "quantity": current + req.quantity, "is_active": True}, actor)
    if approve and apply and req.kind == "renewal":
        sub = subscription(db, req.tenant_id)
        if sub is not None:
            renew(db, sub, actor)
    req.status = "approved" if approve else "declined"
    req.response, req.resolved_at = response[:2000], utcnow()
    db.flush()
    log(db, req.tenant_id, "request", f"Request #{req.id} {req.status}" + (f": {response}" if response else ""), actor)
    return req


# ================================================================== views


def subscription_out(db: Session, sub: TenantSubscription, internal: bool = True) -> dict[str, Any]:
    tenant = db.get(Tenant, sub.tenant_id)
    edition = db.query(LicenseEdition).filter(LicenseEdition.code == sub.edition_code).first() if sub.edition_code else None
    ent = entitlements(db, sub)
    on = today()
    days_left = (sub.trial_ends - on).days if sub.status == "trial" and sub.trial_ends else (sub.term_end - on).days if sub.term_end else None
    out = {"id": sub.id, "tenant_id": sub.tenant_id, "tenant_name": (tenant.display_name or tenant.name) if tenant else "",
           "tenant_slug": tenant.slug if tenant else "", "edition_code": sub.edition_code, "edition_name": edition.name if edition else None,
           "status": sub.status, "status_reason": sub.status_reason, "granting": ent["granting"], "currency": sub.currency,
           "billing_cycle": sub.billing_cycle, "term_months": sub.term_months, "term_start": sub.term_start, "term_end": sub.term_end,
           "trial_ends": sub.trial_ends, "days_left": days_left, "auto_renew": sub.auto_renew, "discount_pct": sub.discount_pct,
           "tax_pct": sub.tax_pct, "grace_days": sub.grace_days, "effective_grace_days": grace_days(db, sub),
           "auto_assign_seats": sub.auto_assign_seats, "contract_ref": sub.contract_ref, "po_number": sub.po_number,
           "billing_contact": sub.billing_contact, "billing_email": sub.billing_email, "billing_address": sub.billing_address, "tax_id": sub.tax_id,
           "modules": ent["modules"], "limits": ent["limits"], "pricing": pricing(db, sub), "seats": seat_summary(db, sub.tenant_id),
           "issues": check_lines(product_map(db), [(ln.product_code, ln.quantity) for ln in lines_of(db, sub) if ln.is_active]),
           "next_statuses": list(TRANSITIONS.get(sub.status, ()))}
    if internal:
        out["notes"] = sub.notes
    return out


def events(db: Session, tenant_id: int | None, limit: int = 100) -> list[dict[str, Any]]:
    q = db.query(LicenseEvent)
    q = q.filter(LicenseEvent.tenant_id == tenant_id) if tenant_id is not None else q
    return [{"id": e.id, "tenant_id": e.tenant_id, "action": e.action, "summary": e.summary, "actor": e.actor, "at": e.at}
            for e in q.order_by(LicenseEvent.id.desc()).limit(limit)]


def tenant_view(db: Session, tenant_id: int) -> dict[str, Any]:
    """What an organization's administrator sees: their contract (no internal notes), seats, limits vs usage."""
    sub = subscription(db, tenant_id)
    tenant = db.get(Tenant, tenant_id)
    catalog = product_map(db)
    used = usage(db, tenant_id)
    users = db.query(User).filter(User.tenant_id == tenant_id, User.role != UserRole.platform_admin).order_by(User.name).all()
    held: dict[int, list[str]] = {}
    for r in db.query(UserLicense).filter(UserLicense.tenant_id == tenant_id):
        held.setdefault(r.user_id, []).append(r.product_code)
    if sub is None:
        from app.licensing import MODULES, enabled

        return {"subscription": None, "modules": sorted(enabled(db, tenant_id)), "module_labels": {k: v["label"] for k, v in MODULES.items()},
                "usage": used, "limits": [], "users": [], "invoices": [], "requests": [], "catalog": [], "events": [],
                "tenant_name": (tenant.display_name or tenant.name) if tenant else ""}
    from app.licensing import MODULES

    ent = entitlements(db, sub)
    limits = [{"key": k, "label": LIMIT_LABELS.get(k, k.replace("_", " ")), "limit": v, "used": used.get(k)} for k, v in ent["limits"].items()]
    if "users" not in ent["limits"] and ent["seats"]:
        core = sum(q for c, q in ent["seats"].items() if c in catalog and catalog[c].kind == "core")
        if core:
            limits.insert(0, {"key": "users", "label": "Users", "limit": core, "used": used["users"]})
    invoices = db.query(LicenseInvoice).filter(LicenseInvoice.tenant_id == tenant_id).order_by(LicenseInvoice.id.desc()).limit(50).all()
    requests = db.query(LicenseRequest).filter(LicenseRequest.tenant_id == tenant_id).order_by(LicenseRequest.id.desc()).limit(50).all()
    return {
        "tenant_name": (tenant.display_name or tenant.name) if tenant else "",
        "subscription": subscription_out(db, sub, internal=False), "modules": ent["modules"],
        "module_labels": {k: v["label"] for k, v in MODULES.items()}, "usage": used, "limits": limits,
        "users": [{"id": u.id, "name": u.name, "email": u.email, "role": u.role.value, "is_active": u.is_active, "licenses": sorted(held.get(u.id, []))}
                  for u in users],
        "invoices": [invoice_out(i) for i in invoices], "requests": [request_out(r, catalog) for r in requests],
        "catalog": [product_out(p) for p in products(db, active_only=True)], "events": events(db, tenant_id, 30),
    }


def overview(db: Session) -> dict[str, Any]:
    """The platform admin's dashboard: revenue, statuses, seats, renewals, overdue invoices, open requests."""
    subs = db.query(TenantSubscription).all()
    tenants = {t.id: t for t in db.query(Tenant).all()}
    by_status: dict[str, int] = {}
    mrr: dict[str, float] = {}
    by_product: dict[str, dict[str, float]] = {}
    seats_bought = seats_used = 0
    renewals = []
    on = today()
    for sub in subs:
        by_status[sub.status] = by_status.get(sub.status, 0) + 1
        price = pricing(db, sub)
        if sub.status in ("active", "past_due"):
            mrr[sub.currency] = _money(mrr.get(sub.currency, 0) + price["mrr"])
            for line in price["lines"]:
                bucket = by_product.setdefault(line["name"], {})
                bucket[sub.currency] = _money(bucket.get(sub.currency, 0) + line["monthly"] * (1 - (sub.discount_pct or 0) / 100))
        for s in seat_summary(db, sub.tenant_id):
            seats_bought += s["purchased"]
            seats_used += s["assigned"]
        end = sub.trial_ends if sub.status == "trial" else sub.term_end
        if end and sub.status in ("trial", "active", "past_due") and (end - on).days <= 45:
            t = tenants.get(sub.tenant_id)
            renewals.append({"tenant_id": sub.tenant_id, "tenant_name": (t.display_name or t.name) if t else "", "status": sub.status,
                             "ends": end, "days_left": (end - on).days, "auto_renew": sub.auto_renew, "mrr": price["mrr"], "currency": sub.currency})
    overdue = [invoice_out(i) for i in db.query(LicenseInvoice).filter(LicenseInvoice.status == "issued", LicenseInvoice.due_on < on)]
    for inv in overdue:
        t = tenants.get(inv["tenant_id"])
        inv["tenant_name"] = (t.display_name or t.name) if t else ""
    return {"tenants": len(tenants), "subscribed": len(subs), "unsubscribed": len(tenants) - len(subs), "by_status": by_status,
            "mrr": mrr, "arr": {c: _money(v * 12) for c, v in mrr.items()}, "by_product": by_product,
            "seats": {"purchased": seats_bought, "assigned": seats_used}, "renewals": sorted(renewals, key=lambda r: r["days_left"]),
            "overdue_invoices": overdue, "open_requests": db.query(LicenseRequest).filter(LicenseRequest.status == "open").count()}


def tenants_list(db: Session) -> list[dict[str, Any]]:
    from app.licensing import LEGACY_MODULES

    subs = {s.tenant_id: s for s in db.query(TenantSubscription).all()}
    out = []
    for t in db.query(Tenant).order_by(Tenant.name).all():
        sub = subs.get(t.id)
        row = {"tenant_id": t.id, "name": t.display_name or t.name, "slug": t.slug,
               "modules": t.licensed_modules if t.licensed_modules is not None else LEGACY_MODULES,
               "users": db.query(User).filter(User.tenant_id == t.id, User.is_active.is_(True)).count(), "subscription": None}
        if sub is not None:
            price = pricing(db, sub)
            row["subscription"] = {"status": sub.status, "edition_code": sub.edition_code, "currency": sub.currency, "mrr": price["mrr"],
                                   "term_end": sub.term_end, "trial_ends": sub.trial_ends, "billing_cycle": sub.billing_cycle}
        out.append(row)
    return out
