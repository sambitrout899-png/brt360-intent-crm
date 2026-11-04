"""Mobile subscribers - prepaid and postpaid lines for operators, MVNOs and MVNEs
(models/mobile.py), built into the platform's product, sales and order model:

  products     plans and packs are catalog products - the product line says what they are
               (mobile = plan; mobile_addon / mobile_vas / roaming = bought on a plan), their
               product model's attributes, as configured when sold, say what they give
               (profile()). The operating policy is the communications settings, key "mobile".
  lines        sold on orders (INT-136, B2C direct orders, B2B quotes) and installed as assets:
               abo_service.assetize brings the line to life on its asset (on_asset_installed);
               change orders on the asset - product change, suspend, resume, terminate, port-in,
               account transfer - move it (on_asset_changed). Number from the order's Inventory
               reservation, the partner's range, the phone-number pool or the tenant's range; SIM
               likewise, or given, or an eSIM. Terminated numbers are quarantined, then reused.
  prepaid      recharge (card / cash / UPI / voucher PIN), validity and grace, emergency credit
               recovered from the next recharge, balance transfer, low balance
  postpaid     a plan sold on an order is billed on its asset by the bill run; packs, fees and
               usage post to the billing account so the bill run bills them; credit limit and
               spend alert (bill shock); security deposit; prepaid <-> postpaid migration
  packs        data / voice / SMS / combo / roaming packs and value-added services, auto-renewal;
               the plan's own allowance is a pack too; a shared plan's members draw on its packs
  network      SIM swap (identity checked, a cool-down on international and premium use), number
               change, barring (lost / stolen, fraud, outgoing, international, premium, data ...),
               roaming
  charging     a usage event is charged online: packs first, then the wallet (prepaid) or the
               bill within the credit limit (postpaid); refused when barred or out of money
  wholesale    partners (MVNO / MVNE / reseller / dealer) with wholesale rates, revenue share and a
               number range; recharge vouchers (only PIN hashes kept); settlements per period

Every function an intent runs takes the intent's entities and refuses (ValueError) whatever the
line's state or the policy doesn't allow, so a failed run leaves nothing half done.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import re
import secrets
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import comm_billing as cb, comm_service as cs, inventory_service as inv
from app.models.comms import CmAccountDetail, CmUsage
from app.models.crm import Party
from app.models.mobile import (
    BARRINGS,
    PARTNER_KINDS,
    SIM_TYPES,
    SYSTEM_BARRINGS,
    MbLine,
    MbLineEvent,
    MbPack,
    MbPartner,
    MbSettlement,
    MbVoucher,
    MbWalletTxn,
)
from app.timeutils import utcnow

_r = cs._r
_bool = cs._bool
EVENT_TYPES = ("voice", "sms", "data")
UNITS = {"voice": "min", "sms": "SMS", "data": "MB"}
# barrings that stop everything the line does
_STOP_ALL = ("lost_stolen", "fraud", "non_payment", "outgoing", "credit_limit", "validity")
_PAY_METHODS = {"card": "card", "credit card": "card", "debit card": "card", "credit_card": "card", "cash": "cash", "upi": "upi",
                "wallet": "wallet_app", "wallet app": "wallet_app", "wallet_app": "wallet_app", "bank": "bank_transfer",
                "bank transfer": "bank_transfer", "bank_transfer": "bank_transfer", "voucher": "voucher", "net banking": "bank_transfer"}


def _today() -> dt.date:
    return utcnow().date()


def settings(db: Session, tenant_id: int) -> dict[str, Any]:
    return cs.settings(db, tenant_id)["mobile"]


# ------------------------------------------------------------------ catalog


# What the Product Modeller says a mobile product gives. A plan or pack is an ordinary catalog product:
# its product line (catalog specifications) says it is mobile, and its attributes - as configured when it
# was sold, else their defaults - say what it gives. These names are read (any spelling / case):
_ATTR = {
    "payment_type": ("paymenttype", "billingtype", "prepaidpostpaid", "payment"),
    "validity": ("validity", "validitydays", "validityperiod", "duration"),
    "data": ("dataallowance", "data", "datalimit", "internet", "databundle"),
    "voice": ("voiceminutes", "voice", "minutes", "voiceallowance", "calls"),
    "sms": ("sms", "smsallowance", "messages", "texts"),
    "roaming": ("roaming", "roamingpack", "useabroad"),
    "shared": ("sharedallowance", "shared", "datapool", "pool", "sharedpool"),
    "sim_type": ("simtype", "sim"),
}
UNLIMITED = 10_000_000.0


def _picks(definition: dict[str, Any] | None, configuration: dict[str, Any] | None) -> dict[str, Any]:
    """{normalised attribute name: value} - what was configured, else the attribute's default."""
    out: dict[str, Any] = {}
    for attr in (definition or {}).get("attributes", []):
        if attr.get("default") not in (None, ""):
            out[cs._key(attr["name"])] = attr["default"]
    for part in ("technical", "pricing"):
        for k, v in ((configuration or {}).get(part) or {}).items():
            if v not in (None, ""):
                out[cs._key(k)] = v
    return out


def _pick(picks: dict[str, Any], what: str) -> Any:
    return next((picks[k] for k in _ATTR[what] if k in picks), None)


def _amount(value: Any, unit: str) -> float:
    """'10 GB' -> 10240 (MB), 'Unlimited' -> UNLIMITED, '300' -> 300."""
    text = str(value or "").strip().lower()
    if not text or text in ("0", "none", "no", "false"):
        return 0.0
    if "unlimited" in text:
        return UNLIMITED
    m = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb|kb|hours?|hrs?|min\w*)?", text)
    if not m:
        return 0.0
    n, u = float(m.group(1)), (m.group(2) or "")
    if unit == "data":
        return n * 1024 * 1024 if u == "tb" else n * 1024 if u == "gb" else n / 1024 if u == "kb" else n
    if unit == "voice" and u.startswith("h"):
        return n * 60
    return n


def _days(value: Any, default: int) -> int:
    text = str(value or "").strip().lower()
    m = re.search(r"(\d+)\s*(day|week|month|year)?", text)
    if not m:
        return default
    n, u = int(m.group(1)), m.group(2) or "day"
    return max(1, n * {"day": 1, "week": 7, "month": 30, "year": 365}[u])


def offering_of(db: Session, tenant_id: int, name: Any):
    from app.crm_core import product_model_service as pm
    from app.models.catalog import ProductOffering

    text = str(name or "").strip()
    if not text:
        return None
    found = pm.offering_of(db, tenant_id, text)
    if found is None:  # a retired product a line was sold on still describes the line
        found = (db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.name.ilike(text))
                 .order_by(ProductOffering.version.desc()).first())
    return found


def product_kind(db: Session, tenant_id: int, offering) -> str | None:
    """'plan' / 'pack' / 'vas' / 'roaming' for a mobile product (its product lines), else None."""
    from app.models.catalog import ProductSpecification
    from app.models.mobile import MOBILE_PRODUCT_LINES

    if offering is None:
        return None
    names = [n.strip() for n in (offering.composition or "").split(",") if n.strip()]
    lines = {s.product_line for s in db.query(ProductSpecification).filter(ProductSpecification.tenant_id == tenant_id,
                                                                            ProductSpecification.name.in_(names or [""]))}
    if "mobile" in lines:
        return "plan"
    return next((MOBILE_PRODUCT_LINES[l] for l in ("roaming", "mobile_vas", "mobile_addon") if l in lines), None)


def profile(db: Session, tenant_id: int, offering_name: Any, configuration: dict[str, Any] | None = None,
            definition: dict[str, Any] | None = None, party: Party | None = None) -> dict[str, Any] | None:
    """What a mobile plan or pack gives - from the product's model (its frozen definition when given) and the
    configuration it was sold with. None when it isn't a mobile product. With the customer, its price is the
    customer's pricelist price."""
    from app.crm_core import product_config_service as pc

    offering = offering_of(db, tenant_id, offering_name)
    kind = product_kind(db, tenant_id, offering)
    if kind is None:
        return None
    definition = definition or pc.schema(db, tenant_id, offering.name)
    picks = _picks(definition, configuration)
    allowance = {k: _amount(_pick(picks, k), k) for k in EVENT_TYPES}
    allowance = {k: v for k, v in allowance.items() if v > 0}
    paid = str(_pick(picks, "payment_type") or "").lower()
    price = offering.price
    if party is not None:
        price = pc.base_price(db, tenant_id, pc.price_list_for(db, tenant_id, party), offering)[0]
    if configuration and (configuration.get("price") or {}).get("configured") is not None:
        price = float(configuration["price"]["configured"])
    roaming = kind == "roaming" or cs._bool(_pick(picks, "roaming"))
    return {
        "code": offering.name, "name": offering.name, "offering_id": offering.id, "kind": kind if kind != "roaming" else "roaming",
        "payment_type": ("prepaid" if "pre" in paid else "postpaid") if kind == "plan" else None,
        "validity_days": _days(_pick(picks, "validity"), 30 if kind == "plan" else 28),
        "allowance": allowance, "unlimited": [k for k, v in allowance.items() if v >= UNLIMITED],
        "roaming": roaming, "shared": kind == "plan" and cs._bool(_pick(picks, "shared")),
        "sim_type": "esim" if "esim" in cs._key(_pick(picks, "sim_type")) else "physical",
        "price": round(float(price or 0), 2), "fee": round(float(price or 0), 2), "model_version": definition.get("version"),
        "status": getattr(offering.status, "value", offering.status), "segment": offering.segment,
    }


def plan_of(db: Session, tenant_id: int, ref: Any, party: Party | None = None, configuration: dict[str, Any] | None = None) -> dict[str, Any]:
    p = profile(db, tenant_id, ref, configuration, party=party)
    if p is None or p["kind"] != "plan":
        raise ValueError(f"'{ref}' is not a mobile plan in the catalog - a plan is a product on the 'mobile' product line "
                         f"(Product Modeller); plans on sale: {', '.join(r['name'] for r in catalog(db, tenant_id)['plans'][:8]) or 'none yet'}")
    return p


def pack_of(db: Session, tenant_id: int, ref: Any, party: Party | None = None, plan: str | None = None) -> dict[str, Any]:
    """A pack / bolt-on / value-added service from the catalog - active, eligible for the customer and
    allowed on the line's plan by the catalog's compatibility rules."""
    from app.crm_core import product_model_service as pm
    from app.models.catalog import BundleCompatibilityRule

    p = profile(db, tenant_id, ref, party=party)
    if p is None or p["kind"] == "plan":
        raise ValueError(f"'{ref}' is not a mobile pack in the catalog - packs are products on the mobile_addon, mobile_vas or roaming "
                         f"product line; on sale: {', '.join(r['name'] for r in catalog(db, tenant_id)['packs'][:8]) or 'none yet'}")
    if p["status"] != "active":
        raise ValueError(f"{p['name']} is not on sale ({p['status']})")
    if party is not None:
        errors = pm.customer_rule_errors(db, tenant_id, p["name"], party, [plan] if plan else [])
        if errors:
            raise ValueError("; ".join(errors))
    rules = db.query(BundleCompatibilityRule).filter(BundleCompatibilityRule.tenant_id == tenant_id, BundleCompatibilityRule.primary_product_line == "mobile").all()
    line_of_kind = {"pack": "mobile_addon", "vas": "mobile_vas", "roaming": "roaming"}[p["kind"]]
    if rules and line_of_kind not in {r.addon_product_line for r in rules}:
        raise ValueError(f"The catalog's compatibility rules don't allow {line_of_kind} products on a mobile plan")
    return p


def _t(name: str, default: Any, values: list[str] | None = None, data_type: str | None = None, unit: str | None = None) -> dict[str, Any]:
    """A characteristic of the product: one allowed value (so a product change never carries another plan's value over)."""
    if values is None and data_type is None:
        values = [str(default)]
    return {"op": "upsert_attribute", "name": name, "kind": "technical", "data_type": data_type or ("enum" if values else "string"),
            "values": values or [], "default": str(default), "unit": unit}


# name, segment, specification (its product line), monthly list price, attributes (product model)
SAMPLE_CATALOG: list[tuple[str, str, str, float, list[dict[str, Any]]]] = [
    ("Prepaid Pay-as-you-go", "B2C", "MobileLine", 0.0,
     [_t("Payment type", "Prepaid"), _t("Validity", 30, data_type="number", unit="days"),
      _t("SIM type", "Physical SIM", ["Physical SIM", "eSIM"])]),
    ("Prepaid Smart 28", "B2C", "MobileLine", 299.0,
     [_t("Payment type", "Prepaid"), _t("Validity", 28, data_type="number", unit="days"),
      _t("Data allowance", "30 GB"), _t("Voice minutes", "1000"), _t("SMS", "300"), _t("SIM type", "Physical SIM", ["Physical SIM", "eSIM"])]),
    ("Postpaid 399", "B2C", "MobileLine", 399.0,
     [_t("Payment type", "Postpaid"), _t("Validity", 30, data_type="number", unit="days"),
      {"op": "upsert_attribute", "name": "Data allowance", "kind": "pricing", "data_type": "enum", "values": ["40 GB", "75 GB", "Unlimited"],
       "default": "40 GB", "price_impacts": [{"value": "75 GB", "type": "flat", "amount": 150}, {"value": "Unlimited", "type": "flat", "amount": 300}]},
      _t("Voice minutes", "3000"), _t("SMS", "1000"), _t("SIM type", "Physical SIM", ["Physical SIM", "eSIM"])]),
    ("Business Postpaid 599", "B2B", "MobileLine", 599.0,
     [_t("Payment type", "Postpaid"), _t("Validity", 30, data_type="number", unit="days"),
      _t("Data allowance", "75 GB"), _t("Voice minutes", "5000"), _t("SMS", "3000"), _t("SIM type", "eSIM", ["Physical SIM", "eSIM"])]),
    ("Business Shared Pool 1499", "B2B", "MobileLine", 1499.0,
     [_t("Payment type", "Postpaid"), _t("Validity", 30, data_type="number", unit="days"),
      _t("Data allowance", "200 GB"), _t("Voice minutes", "10000"), _t("SMS", "5000"), _t("Shared allowance", "true", data_type="boolean"),
      _t("SIM type", "eSIM", ["Physical SIM", "eSIM"])]),
    ("Data 1 GB Day Pass", "", "MobileData", 19.0, [_t("Validity", 1, data_type="number", unit="days"), _t("Data allowance", "1 GB")]),
    ("Data 10 GB Booster", "", "MobileData", 199.0, [_t("Validity", 28, data_type="number", unit="days"), _t("Data allowance", "10 GB")]),
    ("Voice 100 Minutes", "", "MobileData", 49.0, [_t("Validity", 28, data_type="number", unit="days"), _t("Voice minutes", "100")]),
    ("Roaming 7 Days", "", "MobileRoaming", 599.0,
     [_t("Validity", 7, data_type="number", unit="days"), _t("Data allowance", "1 GB"), _t("Voice minutes", "100"), _t("Roaming", "true", data_type="boolean")]),
    ("Caller Tune", "", "MobileVAS", 30.0, [_t("Validity", 30, data_type="number", unit="days")]),
]


def sample_catalog(db: Session, tenant_id: int) -> dict[str, Any]:
    """Typical mobile plans and packs as catalog products with their product models (Product Modeller) -
    a starting point to edit; products that exist already are left alone. Prices are placeholders."""
    from app.crm_core import product_config_service as pc
    from app.integration import tmf620_adapter
    from app.models.catalog import BundleCompatibilityRule, BundleEligibilityRule, ProductSpecification

    # the mobile product lines and what can be bought on a plan (seed.py gives them to new tenants too)
    have = {s.name for s in db.query(ProductSpecification).filter(ProductSpecification.tenant_id == tenant_id)}
    for spec_name, line in (("MobileLine", "mobile"), ("MobileData", "mobile_addon"), ("MobileVAS", "mobile_vas"), ("MobileRoaming", "roaming")):
        if spec_name not in have:
            db.add(ProductSpecification(tenant_id=tenant_id, name=spec_name, product_line=line, is_atomic=True))
    rules = {(r.primary_product_line, r.addon_product_line) for r in db.query(BundleCompatibilityRule).filter(BundleCompatibilityRule.tenant_id == tenant_id)}
    for addon, most in (("mobile_addon", 10), ("mobile_vas", 10), ("roaming", 3)):
        if ("mobile", addon) not in rules:
            db.add(BundleCompatibilityRule(tenant_id=tenant_id, primary_product_line="mobile", addon_product_line=addon, max_addons=most))
    db.flush()
    made, kept = [], []
    for name, segment, spec, price, attrs in SAMPLE_CATALOG:
        if offering_of(db, tenant_id, name) is not None:
            kept.append(name)
            continue
        o = tmf620_adapter.create_offering_version(db, tenant_id=tenant_id, name=name, segment=segment, composition=spec, list_price=price,
                                           price_floor=0.0, discount_ceiling_pct=20.0)
        for seg, channel in ((segment, "B2B" if segment == "B2B" else "B2C"),) if segment else (("B2C", "B2C"), ("B2B", "B2B")):
            db.add(BundleEligibilityRule(product_offering_id=o.id, segment=seg, channel=channel, region="ALL"))
        tmf620_adapter.publish_offering(db, o.id)
        pc.apply_design(db, tenant_id, name, attrs)
        made.append(name)
    db.flush()
    return {"created": made, "kept": kept, **catalog(db, tenant_id)}


def catalog(db: Session, tenant_id: int) -> dict[str, Any]:
    """The mobile plans and packs on sale - catalog products, as the Product Modeller designs them."""
    from app.models.catalog import OfferingStatus, ProductOffering

    plans, packs = [], []
    seen: set[str] = set()
    for o in (db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.status == OfferingStatus.active)
              .order_by(ProductOffering.name, ProductOffering.version.desc())):
        if o.name.lower() in seen:
            continue
        seen.add(o.name.lower())
        p = profile(db, tenant_id, o.name)
        if p is not None:
            (plans if p["kind"] == "plan" else packs).append(p)
    return {"plans": plans, "packs": packs}

# ------------------------------------------------------------------ lines


def norm_msisdn(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits


def line_of(db: Session, tenant_id: int, ref: Any, *, live: bool = True, what: str = "Line") -> MbLine:
    """A line by its number (with or without the country code) or its id."""
    if isinstance(ref, MbLine):
        return ref
    text = str(ref or "").strip()
    if not text:
        raise ValueError("Which mobile number?")
    digits = norm_msisdn(text)
    q = db.query(MbLine).filter(MbLine.tenant_id == tenant_id)
    line = None
    if digits and len(digits) >= 6:
        rows = q.filter(MbLine.msisdn == digits).all() or [l for l in q.filter(MbLine.msisdn.like(f"%{digits[-10:]}")).all()]
        rows = sorted(rows, key=lambda l: (l.status == "terminated", -l.id))
        line = rows[0] if rows else None
    elif digits:
        line = db.get(MbLine, int(digits))
        line = line if line is not None and line.tenant_id == tenant_id else None
    if line is None:
        raise ValueError(f"{what} {text} not found")
    if live and line.status == "terminated":
        raise ValueError(f"{line.msisdn} was terminated on {line.terminated_at:%Y-%m-%d}")
    return line


def _barrings(line: MbLine) -> list[str]:
    return list(line.barrings or [])


def _bar(line: MbLine, name: str, on: bool = True) -> bool:
    current = _barrings(line)
    if on and name not in current:
        line.barrings = current + [name]
        return True
    if not on and name in current:
        line.barrings = [b for b in current if b != name]
        return True
    return False


def _event(db: Session, line: MbLine, action: str, summary: str, **detail: Any) -> None:
    db.add(MbLineEvent(tenant_id=line.tenant_id, line_id=line.id, action=action, summary=summary[:400],
                       detail={k: v for k, v in detail.items() if isinstance(v, (str, int, float, bool, list, dict)) or v is None}))


def _wallet(db: Session, line: MbLine, kind: str, amount: float, method: str = "", reference: str = "", partner_id: int | None = None) -> MbWalletTxn:
    line.balance = _r(line.balance + amount)
    t = MbWalletTxn(tenant_id=line.tenant_id, line_id=line.id, kind=kind, amount=_r(amount), balance_after=line.balance,
                    method=method, reference=reference[:120], partner_id=partner_id)
    db.add(t)
    return t


def _post(db: Session, tenant_id: int, line: MbLine, amount: float, reason: str) -> None:
    """A postpaid charge (or credit, negative) on the account's bill."""
    cb._post(db, tenant_id, line.party_id, _r(amount), f"{line.msisdn}: {reason}")


def _charge(db: Session, tenant_id: int, line: MbLine, amount: float, what: str, kind: str = "pack") -> str:
    """A one-off price: from the wallet (prepaid) or on the bill (postpaid). Returns where it was paid from."""
    amount = _r(amount)
    if amount <= 0:
        return "free"
    if line.payment_type == "prepaid":
        if line.balance < amount:
            raise ValueError(f"{line.msisdn} has {line.balance:,.2f} - {what} costs {amount:,.2f}. Recharge first.")
        _wallet(db, line, kind, -amount, reference=what)
        return "balance"
    _post(db, tenant_id, line, amount, what)
    line.cycle_spend = _r(line.cycle_spend + amount)
    return "bill"


def _add_pack(db: Session, line: MbLine, spec: dict[str, Any], paid_from: str, kind: str | None = None, auto_renew: bool = False,
              start: dt.datetime | None = None) -> MbPack:
    start = start or utcnow()
    allowance = {k: float(v) for k, v in (spec.get("allowance") or {}).items() if float(v or 0) > 0}
    p = MbPack(tenant_id=line.tenant_id, line_id=line.id, code=str(spec["code"]), name=str(spec["name"]), kind=kind or spec.get("kind") or "data",
               granted=allowance, remaining=dict(allowance), roaming=bool(spec.get("roaming")), price=_r(spec.get("price", spec.get("fee", 0))),
               paid_from=paid_from, valid_from=start, valid_to=start + dt.timedelta(days=int(spec.get("validity_days") or 30)),
               auto_renew=auto_renew, status="active")
    db.add(p)
    return p


def _active_packs(db: Session, line: MbLine) -> list[MbPack]:
    now = utcnow()
    return (db.query(MbPack).filter(MbPack.line_id == line.id, MbPack.status == "active", MbPack.valid_to > now)
            .order_by(MbPack.valid_to, MbPack.id).all())


def _plan_pack(db: Session, line: MbLine) -> MbPack | None:
    return db.query(MbPack).filter(MbPack.line_id == line.id, MbPack.kind == "plan", MbPack.status == "active").order_by(MbPack.id.desc()).first()


def pack_out(p: MbPack) -> dict[str, Any]:
    return {"id": p.id, "code": p.code, "name": p.name, "kind": p.kind, "granted": p.granted or {}, "remaining": p.remaining or {},
            "roaming": p.roaming, "price": p.price, "paid_from": p.paid_from, "valid_from": p.valid_from, "valid_to": p.valid_to,
            "auto_renew": p.auto_renew, "status": p.status}


def line_out(db: Session, line: MbLine, full: bool = False) -> dict[str, Any]:
    party = db.get(Party, line.party_id)
    partner = db.get(MbPartner, line.partner_id) if line.partner_id else None
    out: dict[str, Any] = {
        "id": line.id, "msisdn": line.msisdn, "party_id": line.party_id, "customer": party.name if party else "", "user_name": line.user_name,
        "iccid": line.iccid, "imsi": line.imsi, "sim_type": line.sim_type, "plan": line.plan, "payment_type": line.payment_type,
        "status": line.status, "status_reason": line.status_reason, "barrings": _barrings(line), "roaming": line.roaming,
        "balance": _r(line.balance), "valid_until": line.valid_until, "advance_owed": _r(line.advance_owed),
        "credit_limit": _r(line.credit_limit), "spend_alert_pct": line.spend_alert_pct, "cycle_start": line.cycle_start,
        "cycle_spend": _r(line.cycle_spend), "deposit": _r(line.deposit), "group_head_id": line.group_head_id,
        "partner": partner.name if partner else None, "partner_id": line.partner_id, "activated_at": line.activated_at,
        "terminated_at": line.terminated_at, "swap_cooldown_until": line.swap_cooldown_until,
        "order_id": line.order_id, "order_item_id": line.order_item_id, "asset_id": line.asset_id, "plan_charge": line.plan_charge,
        "plan_profile": line.plan_profile,
    }
    if full:
        packs = _active_packs(db, line)
        head = db.get(MbLine, line.group_head_id) if line.group_head_id else None
        members = db.query(MbLine).filter(MbLine.group_head_id == line.id, MbLine.status != "terminated").all()
        out.update({
            "packs": [pack_out(p) for p in packs],
            "shared_from": {"msisdn": head.msisdn, "packs": [pack_out(p) for p in _active_packs(db, head) if not p.roaming]} if head else None,
            "members": [m.msisdn for m in members],
            "remaining": _remaining(packs + ([p for p in _active_packs(db, head) if not p.roaming] if head else [])),
            "wallet": [{"kind": t.kind, "amount": t.amount, "balance_after": t.balance_after, "method": t.method, "reference": t.reference, "at": t.at}
                       for t in db.query(MbWalletTxn).filter(MbWalletTxn.line_id == line.id).order_by(MbWalletTxn.id.desc()).limit(15)],
            "usage": [cb.usage_out(u) for u in db.query(CmUsage).filter(CmUsage.tenant_id == line.tenant_id, CmUsage.service == line.msisdn)
                      .order_by(CmUsage.id.desc()).limit(15)],
            "history": [{"action": e.action, "summary": e.summary, "at": e.at}
                        for e in db.query(MbLineEvent).filter(MbLineEvent.line_id == line.id).order_by(MbLineEvent.id.desc()).limit(25)],
        })
    return out


def _remaining(packs: list[MbPack]) -> dict[str, float]:
    total: dict[str, float] = {}
    for p in packs:
        if p.roaming:
            continue
        for k, v in (p.remaining or {}).items():
            total[k] = round(total.get(k, 0.0) + float(v), 2)
    return total


def lines(db: Session, tenant_id: int, query: str = "", status: str = "", payment_type: str = "", party_id: int | None = None,
          limit: int = 300) -> list[dict[str, Any]]:
    q = db.query(MbLine).filter(MbLine.tenant_id == tenant_id)
    if status:
        q = q.filter(MbLine.status == status)
    if payment_type:
        q = q.filter(MbLine.payment_type == payment_type)
    if party_id:
        q = q.filter(MbLine.party_id == party_id)
    if query:
        digits = norm_msisdn(query)
        if digits:
            q = q.filter(MbLine.msisdn.like(f"%{digits}%") | MbLine.iccid.like(f"%{digits}%"))
        else:
            ids = [p.id for p in db.query(Party.id).filter(Party.tenant_id == tenant_id, Party.name.ilike(f"%{query}%"))]
            q = q.filter(MbLine.party_id.in_(ids or [0]) | MbLine.user_name.ilike(f"%{query}%"))
    return [line_out(db, l) for l in q.order_by(MbLine.id.desc()).limit(limit)]


# ------------------------------------------------------------------ numbers and SIMs


def _parse_range(text: str) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d{6,15})\s*(?:-|–|to)\s*(\d{6,15})\s*$", str(text or ""))
    if not m or len(m.group(1)) != len(m.group(2)) or int(m.group(1)) > int(m.group(2)):
        return None
    return int(m.group(1)), int(m.group(2))


def number_taken(db: Session, tenant_id: int, msisdn: str) -> str | None:
    """Why a number can't be given out now - in use, or quarantined after it was given up."""
    today = _today()
    for l in db.query(MbLine).filter(MbLine.tenant_id == tenant_id, MbLine.msisdn == msisdn).all():
        if l.status != "terminated":
            return f"{msisdn} is in use"
        if l.quarantine_until and l.quarantine_until >= today:
            return f"{msisdn} is quarantined until {l.quarantine_until}"
    for l in db.query(MbLine).filter(MbLine.tenant_id == tenant_id, MbLine.previous_msisdns.isnot(None)).all():
        for old in l.previous_msisdns or []:
            if old.get("msisdn") == msisdn and str(old.get("until") or "") >= today.isoformat():
                return f"{msisdn} is quarantined until {old['until']}"
    return None


def _blocked_numbers(db: Session, tenant_id: int) -> set[str]:
    """Every number that can't be given out now: in use, or still quarantined."""
    today = _today()
    out: set[str] = set()
    for line in db.query(MbLine).filter(MbLine.tenant_id == tenant_id).all():
        if line.status != "terminated" or (line.quarantine_until and line.quarantine_until >= today):
            out.add(line.msisdn)
        out.update(o.get("msisdn") or "" for o in line.previous_msisdns or [] if str(o.get("until") or "") >= today.isoformat())
    return out


def _free_in_range(db: Session, tenant_id: int, rng: tuple[int, int], skip: set[str]) -> str | None:
    blocked = _blocked_numbers(db, tenant_id) | skip
    width = len(str(rng[0]))
    for n in range(rng[0], min(rng[1], rng[0] + 200_000) + 1):
        value = str(n).zfill(width)
        if value not in blocked:
            return value
    return None


def _number(db: Session, tenant_id: int, party_id: int, partner: MbPartner | None, given: str, reference: str, skip: set[str]) -> str:
    if given:
        msisdn = norm_msisdn(given)
        if len(msisdn) < 6:
            raise ValueError(f"'{given}' is not a mobile number")
        if why := number_taken(db, tenant_id, msisdn):
            raise ValueError(why)
        if partner and (rng := _parse_range(partner.number_range)) and not rng[0] <= int(msisdn) <= rng[1]:
            raise ValueError(f"{msisdn} is outside {partner.name}'s number range {partner.number_range}")
        inv.assign_to_subscriber(db, tenant_id, "phone_number", party_id=party_id, reference=reference, identifier=msisdn)
        return msisdn
    if partner and (rng := _parse_range(partner.number_range)):
        value = _free_in_range(db, tenant_id, rng, skip)
        if value is None:
            raise ValueError(f"{partner.name}'s number range {partner.number_range} is used up")
        return value
    item = inv.assign_to_subscriber(db, tenant_id, "phone_number", party_id=party_id, reference=reference)
    if item is not None:
        return norm_msisdn(item.identifier) or item.identifier
    rng = _parse_range(settings(db, tenant_id)["number_range"])
    if rng is None:
        raise ValueError("Set a number range under Mobile -> Catalog (number_range, e.g. 9100000000-9199999999) or a phone-number pool in Inventory")
    value = _free_in_range(db, tenant_id, rng, skip)
    if value is None:
        raise ValueError("Every number in the tenant's range is in use - widen number_range or add a phone-number pool in Inventory")
    return value


def _sim(db: Session, tenant_id: int, party_id: int, sim_type: str, iccid: str, reference: str, pending_ok: bool = False) -> tuple[str, str, str]:
    """(iccid, imsi, esim activation code) for a new SIM. pending_ok: no ICCID and no SIM stock gives ("", "", "") -
    the line waits for a SIM (an order being fulfilled must not fail for it)."""
    if not iccid and sim_type != "esim" and pending_ok and not inv.managed(db, tenant_id, "sim"):
        return "", "", ""
    if sim_type == "esim":
        iccid = iccid or "8991" + "".join(str(secrets.randbelow(10)) for _ in range(15))
        code = f"LPA:1${settings(db, tenant_id)['smdp_address']}${secrets.token_hex(8).upper()}"
        return iccid, "", code
    if iccid:
        iccid = re.sub(r"\s", "", iccid)
        if not re.fullmatch(r"\d{18,22}F?", iccid):
            raise ValueError(f"'{iccid}' is not an ICCID (18 to 22 digits)")
        if db.query(MbLine.id).filter(MbLine.tenant_id == tenant_id, MbLine.iccid == iccid, MbLine.status != "terminated").first():
            raise ValueError(f"SIM {iccid} is already on another line")
        item = inv.assign_to_subscriber(db, tenant_id, "sim", party_id=party_id, reference=reference, identifier=iccid)
    else:
        item = inv.assign_to_subscriber(db, tenant_id, "sim", party_id=party_id, reference=reference)
        if item is None:
            raise ValueError("Give the SIM's ICCID (Inventory keeps no SIM stock to pick one from) - or choose an eSIM")
        iccid = item.identifier
    imsi = ""
    if item is not None:
        imsi = str(inv._j(item.attributes_json, {}).get("imsi") or "")
    return iccid, imsi, ""


def _partner(db: Session, tenant_id: int, ref: Any, required: bool = False) -> MbPartner | None:
    if ref in (None, ""):
        if required:
            raise ValueError("Which partner?")
        return None
    q = db.query(MbPartner).filter(MbPartner.tenant_id == tenant_id)
    if str(ref).isdigit():
        p = q.filter(MbPartner.id == int(ref)).first()
    else:
        p = q.filter(MbPartner.name.ilike(str(ref).strip())).first() or q.filter(MbPartner.brand.ilike(str(ref).strip())).first()
        if p is None:
            like = q.filter(MbPartner.name.ilike(f"%{str(ref).strip()}%")).all()
            p = like[0] if len(like) == 1 else None
    if p is None:
        raise ValueError(f"No partner '{ref}' - onboard it first")
    return p


def _standing(db: Session, tenant_id: int, party_id: int, postpaid: bool) -> None:
    detail = db.query(CmAccountDetail).filter(CmAccountDetail.party_id == party_id).first()
    if detail is not None and not detail.good_standing:
        raise ValueError(f"The account is blocked ({detail.block_reason or 'not in good standing'}) - no new lines")
    if postpaid:
        overdue = cb.overdue_amount(db, tenant_id, party_id)
        if overdue > 0:
            raise ValueError(f"The account has {overdue:,.2f} overdue - settle it before a postpaid line")


def _list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in re.split(r"[,;\n]", str(value)) if v.strip()]


def _pay_method(value: Any, default: str = "cash") -> str:
    raw = str(value or "").strip().lower().replace("_", " ")
    if not raw:
        return default
    method = _PAY_METHODS.get(raw) or _PAY_METHODS.get(raw.replace(" ", "_"))
    if method is None:
        raise ValueError("Payment method: card, cash, upi, wallet_app, bank_transfer or voucher")
    return method


# ------------------------------------------------------------------ activation: lines are sold on orders and live as assets


def _plan_charge(prof: dict[str, Any], from_order: bool) -> str:
    if prof["payment_type"] == "prepaid":
        return "wallet"
    return "asset" if from_order else "ledger"


def _start_plan(db: Session, tenant_id: int, line: MbLine, prof: dict[str, Any], what: str = "") -> MbPack | None:
    """The plan's allowance for a period, paid as the line's plan is charged: from the wallet (prepaid - not
    started while the balance can't pay it; the next recharge starts it), on the bill (postpaid sold without an
    order) or nothing here (postpaid on an order - the bill run bills its asset)."""
    price = prof["price"]
    if line.plan_charge == "wallet":
        if price > line.balance:
            _event(db, line, "plan_due", f"{prof['name']} ({price:,.2f}) starts with the next recharge - {line.balance:,.2f} on the wallet")
            return None
        paid = _charge(db, tenant_id, line, price, what or f"{prof['name']} plan", kind="plan_fee") if price else "free"
        line.valid_until = max(line.valid_until or _today(), _today() + dt.timedelta(days=int(prof["validity_days"])))
        _bar(line, "validity", False)
    elif line.plan_charge == "ledger":
        paid = _charge(db, tenant_id, line, price, what or f"{prof['name']} plan", kind="plan_fee") if price else "free"
    else:
        paid = "asset"
    return _add_pack(db, line, prof, paid, kind="plan", auto_renew=True)


def _activate_line(db: Session, tenant_id: int, party: Party, prof: dict[str, Any], req: dict[str, Any], *, order_id: int | None = None,
                   order_item_id: int | None = None, asset_id: int | None = None) -> tuple[MbLine, str]:
    """One line on a plan: number, SIM, plan, first recharge or deposit. Returns (line, eSIM activation code)."""
    s = settings(db, tenant_id)
    partner = _partner(db, tenant_id, req.get("partner_id") or req.get("wholesale_partner"))
    if partner and partner.status != "active":
        raise ValueError(f"{partner.name} is {partner.status} - no new lines")
    reference = f"mobile line for {party.name}" + (f" (order {order_id})" if order_id else "")
    sim_type = str(req.get("sim_type") or prof["sim_type"]).lower()
    held_number = held_sim = None
    if order_id:  # fulfilment reserved them for the order (a mobile pattern's SOM steps): the line takes exactly those
        used = {l.msisdn for l in db.query(MbLine).filter(MbLine.order_id == order_id)} | {l.iccid for l in db.query(MbLine).filter(MbLine.order_id == order_id)}
        held_number = next((i for i in inv.held_by_order(db, tenant_id, order_id, "phone_number") if norm_msisdn(i.identifier) not in used), None)
        held_sim = next((i for i in inv.held_by_order(db, tenant_id, order_id, "sim") if i.identifier not in used), None) if sim_type != "esim" else None
    if held_number is not None and not req.get("msisdn"):
        msisdn = norm_msisdn(held_number.identifier) or held_number.identifier
        if why := number_taken(db, tenant_id, msisdn):
            raise ValueError(why)
        inv.set_lifecycle(db, tenant_id, held_number, "in_use", source="mobile", reference=reference)
    else:
        msisdn = _number(db, tenant_id, party.id, partner, str(req.get("msisdn") or ""), reference, set(req.get("_taken") or []))
    if held_sim is not None and not req.get("iccid"):
        inv.set_lifecycle(db, tenant_id, held_sim, "in_use", source="mobile", reference=reference)
        iccid, imsi, code = held_sim.identifier, str(inv._j(held_sim.attributes_json, {}).get("imsi") or ""), ""
    else:
        iccid, imsi, code = _sim(db, tenant_id, party.id, sim_type, str(req.get("iccid") or ""), reference, pending_ok=order_item_id is not None)
    postpaid = prof["payment_type"] == "postpaid"
    limit = _r(req["credit_limit"] if req.get("credit_limit") not in (None, "") else s["credit_limit_default"]) if postpaid else 0.0
    line = MbLine(tenant_id=tenant_id, party_id=party.id, partner_id=partner.id if partner else None, order_id=order_id, order_item_id=order_item_id,
                  asset_id=asset_id, plan_charge=_plan_charge(prof, order_item_id is not None), msisdn=msisdn, iccid=iccid, imsi=imsi,
                  sim_type=sim_type, user_name=str(req.get("user_name") or "")[:200], plan=prof["name"], plan_profile=prof,
                  payment_type=prof["payment_type"], status="active" if iccid else "pending",
                  status_reason="" if iccid else "waiting for a SIM - swap its ICCID in (Swap a SIM)", barrings=[], roaming=False,
                  balance=0.0, advance_owed=0.0, credit_limit=limit, spend_alert_pct=80, cycle_spend=0.0, alert_sent=False,
                  deposit=_r(req.get("deposit") if req.get("deposit") not in (None, "") else (s["postpaid_deposit"] if postpaid else 0)) if postpaid else 0.0,
                  previous_msisdns=[], cycle_start=_today() if postpaid else None)
    db.add(line)
    db.flush()
    line.imsi = line.imsi or f"{s['imsi_prefix']}{line.id:010d}"[:15]
    if not postpaid:
        line.valid_until = _today() + dt.timedelta(days=int(s["prepaid_validity_days"]))
        recharge = _r(req.get("initial_recharge") or 0)
        if recharge > 0:
            _wallet(db, line, "recharge", recharge, method=_pay_method(req.get("payment_method")), reference="first recharge")
    _start_plan(db, tenant_id, line, prof)
    _event(db, line, "activated", f"Activated on {prof['name']} ({prof['payment_type']})" + (f", SIM {iccid}" if iccid else ", waiting for a SIM")
           + (f", order {order_id}" if order_id else "") + (f", deposit {line.deposit:,.2f}" if line.deposit else ""),
           plan=prof["name"], partner=partner.name if partner else None, order_id=order_id, asset_id=asset_id)
    return line, code


def activate(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """INT-136 - sell mobile lines on orders, through the platform's order flow: the plan is configured from its
    product model and priced from the customer's pricelist; each line is an order line of its own and becomes
    an installed asset (so it can later be changed, suspended or ported on its own). A fulfilment pattern made
    for the plan fulfils it; otherwise the mobile activation completes it at once (no field work). The lines
    appear as the assets are installed (on_asset_installed)."""
    from app.crm_core import account_service, instance_service, om_service as om, product_config_service as pc
    from app.integration import order_adapter
    from app.models.orders import OrderType
    from app.models.product_config import OrderItemConfig
    from app.orchestration.actions_prospect import check_products

    s = settings(db, tenant_id)
    party = cs.party_of(db, tenant_id, e)
    prof = plan_of(db, tenant_id, e.get("offering_name") or e.get("plan"), party=party)
    if prof["status"] != "active":
        raise ValueError(f"{prof['name']} is not on sale ({prof['status']})")
    if e.get("payment_type") and str(e["payment_type"]).lower() != prof["payment_type"]:
        raise ValueError(f"{prof['name']} is a {prof['payment_type']} plan - pick a {str(e['payment_type']).lower()} one")
    quantity = int(e.get("quantity") or 1)
    if not 1 <= quantity <= int(s["max_bulk_lines"]):
        raise ValueError(f"Activate 1 to {s['max_bulk_lines']} lines at a time")
    numbers, iccids, users = _list(e.get("msisdn")), _list(e.get("iccid")), _list(e.get("user_name"))
    if len(numbers) > quantity or len(iccids) > quantity:
        raise ValueError(f"More numbers / SIMs than lines ({quantity})")
    sim_type = str(e.get("sim_type") or "").lower()
    if sim_type and sim_type not in SIM_TYPES:
        raise ValueError("SIM type: physical or esim")
    partner = _partner(db, tenant_id, e.get("wholesale_partner") or e.get("partner"))
    if partner and partner.status != "active":
        raise ValueError(f"{partner.name} is {partner.status} - no new lines")
    _standing(db, tenant_id, party.id, prof["payment_type"] == "postpaid")
    check_products(db, tenant_id, party, [(prof["name"], quantity)])  # sold to the customer's segment, and eligible
    if prof["payment_type"] == "postpaid" and e.get("credit_limit") not in (None, "") and float(e["credit_limit"]) > float(s["credit_limit_max"]):
        raise ValueError(f"The credit limit can be at most {s['credit_limit_max']:,.2f}")
    if prof["payment_type"] == "prepaid" and prof["price"] > 0 and _r(e.get("initial_recharge") or 0) < prof["price"]:
        raise ValueError(f"{prof['name']} costs {prof['price']:,.2f} - give a first recharge of at least that much")
    cs.no_card_numbers(e.get("reference"))
    offering = offering_of(db, tenant_id, prof["name"])
    price_list = pc.price_list_for(db, tenant_id, party)
    base, _ = pc.base_price(db, tenant_id, price_list, offering, 1)
    configured = pc.configure(db, tenant_id, offering.name, e.get("pricing_attributes") or {}, e.get("technical_attributes") or {}, base,
                              party=party, price_list=price_list)
    billing = order_adapter.get_or_create_billing_account(db, tenant_id, party.id)
    service_account = account_service.get_or_create_service_account(db, tenant_id, billing.id, f"{offering.name} Service")
    orders, codes, made = [], {}, []
    taken: list[str] = []
    for i in range(quantity):
        req = {"msisdn": numbers[i] if i < len(numbers) else "", "iccid": iccids[i] if i < len(iccids) else "", "sim_type": sim_type,
               "user_name": users[i] if i < len(users) else "", "partner_id": partner.id if partner else None,
               "initial_recharge": e.get("initial_recharge"), "payment_method": e.get("payment_method"), "deposit": e.get("deposit"),
               "credit_limit": e.get("credit_limit")}
        order = order_adapter.create_order(db, tenant_id=tenant_id, party_id=party.id, service_account_id=service_account.id, quote_id=None,
                                           order_type=OrderType.new, monthly_price=configured["configured"])
        item = order_adapter.add_order_item(db, order.id, offering.id, 1)
        item.unit_price = item.net_unit_price = configured["configured"]
        item.discount_pct = 0.0
        db.add(OrderItemConfig(order_item_id=item.id, pricing_attributes_json=_dumps(configured["pricing"]),
                               technical_attributes_json=_dumps(configured["technical"])))
        inst = instance_service.take(db, tenant_id, "order", order.id, offering.name, item_id=item.id)
        # what the line is to be activated with travels with the order line (and so onto its asset)
        inst.configuration_json = _dumps({"pricing": configured["pricing"], "technical": configured["technical"], "quantity": 1,
                                          "mobile": {**req, "_taken": taken[:]}})
        inst.price_json = _dumps(configured)
        detail = om.order_detail(db, tenant_id, order)
        detail.channel = str(e.get("order_channel") or "store")[:30]
        db.flush()
        _fulfil(db, tenant_id, order, prof["name"])
        db.refresh(order)
        for line in db.query(MbLine).filter(MbLine.order_item_id == item.id).all():
            made.append(line)
            taken.append(line.msisdn)
        orders.append({"order_id": order.id, "status": order.status.value})
    db.flush()
    for line in made:
        ev = db.query(MbLineEvent).filter(MbLineEvent.line_id == line.id, MbLineEvent.action == "esim_code").first()
        if ev is not None:
            codes[line.msisdn] = (ev.detail or {}).get("code")
    out = [line_out(db, l) for l in made]
    return {"party_id": party.id, "count": len(made), "ordered": quantity, "orders": orders, "lines": out,
            "msisdn": made[0].msisdn if made else None, "line_id": made[0].id if made else None, "payment_type": prof["payment_type"],
            "plan": prof["name"], "monthly_price": configured["configured"], **({"esim_activation_codes": codes} if codes else {}),
            "summary": (f"{len(made)} {prof['payment_type']} line(s) on {prof['name']}: {', '.join(l.msisdn for l in made[:10])}"
                        + (" ..." if len(made) > 10 else "")) if made else
                       f"{quantity} order(s) for {prof['name']} sent to fulfilment - the lines appear as they are installed"}


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def _fulfil(db: Session, tenant_id: int, order, plan: str) -> None:
    """A fulfilment pattern designed for this plan (it lists the product) runs the order; otherwise the
    mobile activation fulfils it at once - a mobile line needs no field work."""
    from app.crm_core import abo_service
    from app.orchestration import fulfillment_engine

    pattern = fulfillment_engine.matching_pattern(db, tenant_id, order)
    named = pattern is not None and cs._key(plan) in {cs._key(p) for p in (pattern.products or "").split(",")}
    if named:
        f_order = fulfillment_engine.receive(db, tenant_id, order, pattern=pattern)
        fulfillment_engine.advance(db, f_order)
        return
    from app.integration import network_activation_adapter, orchestration_adapter

    orchestration_adapter.submit_order_for_orchestration(order.id)
    network_activation_adapter.activate_connection(order.items[0].id)
    abo_service.complete(db, order)


def on_asset_installed(db: Session, order, item, asset, data: dict[str, Any]) -> None:
    """abo_service.assetize: an installed asset of a mobile plan gets its line(s) - one per unit ordered, as the
    order line's mobile request says (number, SIM, first recharge ...) or by default; a pack on the order goes
    on the order's new lines (or the customer's only line)."""
    if db.query(MbLine.id).filter(MbLine.order_item_id == item.id).first():
        db.query(MbLine).filter(MbLine.order_item_id == item.id, MbLine.asset_id.is_(None)).update({MbLine.asset_id: asset.id}, synchronize_session=False)
        return
    configuration = data.get("configuration") or {}
    party = db.get(Party, order.party_id)
    prof = profile(db, order.tenant_id, data.get("offering_name"), configuration, data.get("definition"), party=party)
    if prof is None:
        return
    if prof["kind"] == "plan":
        req = dict(configuration.get("mobile") or {})
        taken = list(req.pop("_taken", []))
        for n in range(max(1, int(data.get("quantity") or 1))):
            one = req if n == 0 else {k: v for k, v in req.items() if k not in ("msisdn", "iccid", "user_name")}
            line, code = _activate_line(db, order.tenant_id, party, prof, {**one, "_taken": taken}, order_id=order.id, order_item_id=item.id,
                                        asset_id=asset.id)
            taken.append(line.msisdn)
            if code:
                _event(db, line, "esim_code", "eSIM activation code issued", code=code)
        return
    targets = [l for l in db.query(MbLine).filter(MbLine.order_id == order.id, MbLine.status != "terminated").order_by(MbLine.id)]
    if not targets:
        live = db.query(MbLine).filter(MbLine.tenant_id == order.tenant_id, MbLine.party_id == order.party_id, MbLine.status == "active").all()
        targets = live if len(live) == 1 else []
    for line in targets[:max(1, int(data.get("quantity") or 1))]:
        paid = "asset" if line.payment_type == "postpaid" else _charge(db, order.tenant_id, line, prof["price"], prof["name"])
        _add_pack(db, line, prof, paid)
        _event(db, line, "pack_added", f"Added {prof['name']} (order {order.id})")


def wallet_paid(db: Session, row: Any) -> bool:
    """A live service (comm_service.live_orders row) whose price prepaid mobile lines pay from their wallets -
    the bill run must not bill it as well."""
    q = db.query(MbLine.id).filter(MbLine.plan_charge == "wallet", MbLine.status != "terminated")
    asset_id = getattr(row, "asset_id", None)
    return bool((asset_id and q.filter(MbLine.asset_id == asset_id).first()) or q.filter(MbLine.order_id == row.id).first())


def _single_asset(db: Session, line: MbLine):
    """The installed asset that is this line alone - its changes go through change orders. None when the line
    has none, or shares it with other lines (an SME's lines sold as one order line): those change line by line."""
    from app.models.fulfillment import Asset

    if not line.asset_id:
        return None
    others = db.query(MbLine.id).filter(MbLine.asset_id == line.asset_id, MbLine.id != line.id, MbLine.status != "terminated").first()
    asset = db.get(Asset, line.asset_id)
    return asset if others is None and asset is not None and not asset.superseded_by_asset_id else None


def _change_order(db: Session, tenant_id: int, line: MbLine, operation: str, values: dict[str, Any], request: dict[str, Any] | None = None) -> dict[str, Any]:
    """A change order on the line's asset, fulfilled now (asset-based ordering); the line follows it in
    on_asset_changed - what only the line knows (refund the balance, a new limit ...) rides along in request."""
    from app.crm_core import abo_service

    db.info.setdefault("mobile_change", {})[line.asset_id] = request or {}
    out = abo_service.submit(db, tenant_id, line.asset_id, operation, {**values, "fulfil_now": True})
    db.info.get("mobile_change", {}).pop(line.asset_id, None)
    return {"change_order_id": out["order_id"], "operation": out["operation"], "new_monthly": out.get("new_monthly"),
            "asset_id": out.get("asset_id")}


def on_asset_changed(db: Session, order, change, asset, data: dict[str, Any]) -> None:
    """abo_service.complete: a fulfilled change order on an asset moves its mobile line(s) with it - a product
    change is a plan change (or a prepaid <-> postpaid switch), suspend / resume / terminate / port-out change
    the line's status, a port-in brings the customer's number, an account transfer moves the line."""
    from app.crm_core import abo_service

    rows = db.query(MbLine).filter(MbLine.asset_id == asset.id, MbLine.status != "terminated").all()
    if not rows:
        return
    req = db.info.get("mobile_change", {}).get(asset.id) or {}
    op, tenant_id = change.operation, order.tenant_id
    for line in rows:
        why = f"change order {order.id}"
        if op in abo_service.REPLACING:
            line.asset_id = data.get("asset_id") or line.asset_id
            if op == "product_change":
                # a product change carries over settings the new product also has - fine for options the customer chose
                # (pricing), never for what the new plan IS (payment type, allowances): those come from its own model
                chosen = {"pricing": (data.get("configuration") or {}).get("pricing") or {}, "technical": {},
                          "price": (data.get("configuration") or {}).get("price")}
                prof = profile(db, tenant_id, data.get("offering_name"), chosen, data.get("definition"), party=db.get(Party, line.party_id))
                if prof is not None and prof["kind"] == "plan":
                    if prof["payment_type"] != line.payment_type:
                        _switch_payment(db, tenant_id, line, prof["payment_type"], req, from_order=True)
                    _apply_plan(db, tenant_id, line, prof, why)
        elif op == "modify":
            prof = profile(db, tenant_id, data.get("offering_name"), data.get("configuration"), data.get("definition"), party=db.get(Party, line.party_id))
            if prof is not None and prof["kind"] == "plan":
                _apply_plan(db, tenant_id, line, prof, why)
        elif op == "suspend":
            line.status, line.status_reason = "suspended", req.get("reason") or why
            _event(db, line, "suspended", f"Suspended - {line.status_reason}")
        elif op == "resume":
            line.status, line.status_reason = "active", ""
            _event(db, line, "resumed", f"Resumed ({why})")
        elif op in abo_service.ENDING:
            _terminate(db, tenant_id, line, req.get("reason") or ("ported out" if op == "port_out" else why), bool(req.get("refund_balance")),
                       req.get("payment_method"), quarantine=op != "port_out")
        elif op == "number_portability":
            ported = norm_msisdn((data.get("portability") or {}).get("port_number"))
            if ported and ported != line.msisdn:
                if db.query(MbLine.id).filter(MbLine.tenant_id == tenant_id, MbLine.msisdn == ported, MbLine.status != "terminated").first():
                    raise ValueError(f"{ported} is already a line here")
                old, line.msisdn = line.msisdn, ported
                inv.release_from_subscriber(db, tenant_id, "phone_number", old, note="replaced by a ported-in number")
                db.query(CmUsage).filter(CmUsage.tenant_id == tenant_id, CmUsage.service == old).update({CmUsage.service: ported}, synchronize_session=False)
                _event(db, line, "ported_in", f"Number {ported} ported in from {(data.get('portability') or {}).get('donor_operator')} - {old} released")
        elif op == "customer_transfer":
            line.party_id = data.get("party_id") or line.party_id
            _event(db, line, "transferred", f"Moved to {cs.party_name(db, line.party_id)} ({why})")

# ------------------------------------------------------------------ prepaid


def _hash_pin(tenant_id: int, pin: str) -> str:
    return hashlib.sha256(f"{tenant_id}:{re.sub(r'[^0-9A-Za-z]', '', pin)}".encode()).hexdigest()


def _extend_validity(line: MbLine, days: int) -> None:
    base = max(line.valid_until or _today(), _today())
    line.valid_until = base + dt.timedelta(days=days)
    _bar(line, "validity", False)
    if line.status == "suspended" and line.status_reason == "validity expired":
        line.status, line.status_reason = "active", ""


def recharge(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    line = line_of(db, tenant_id, e.get("msisdn"))
    if line.payment_type != "prepaid":
        raise ValueError(f"{line.msisdn} is postpaid - take a bill payment instead")
    if "lost_stolen" in _barrings(line) or "fraud" in _barrings(line):
        raise ValueError(f"{line.msisdn} is barred ({', '.join(_barrings(line))}) - no recharges until it is cleared")
    cs.no_card_numbers(e.get("reference"), e.get("card_last4"))
    dealer = _partner(db, tenant_id, e.get("dealer"))
    pin = str(e.get("voucher_pin") or "").strip()
    validity = int(s["prepaid_validity_days"])
    if pin:
        since = utcnow() - dt.timedelta(hours=24)
        failures = db.query(MbLineEvent).filter(MbLineEvent.line_id == line.id, MbLineEvent.action == "voucher_failed", MbLineEvent.at >= since).count()
        if failures >= 5:
            raise ValueError(f"Too many wrong voucher PINs on {line.msisdn} today - try again tomorrow or pay another way")
        v = db.query(MbVoucher).filter(MbVoucher.tenant_id == tenant_id, MbVoucher.pin_hash == _hash_pin(tenant_id, pin)).first()
        problem = ("That PIN is not a voucher" if v is None else f"Voucher {v.serial} was already used" if v.status == "redeemed"
                   else f"Voucher {v.serial} is {v.status}" if v.status != "available" else f"Voucher {v.serial} expired on {v.expires_on}"
                   if v.expires_on < _today() else None)
        if problem:
            _event(db, line, "voucher_failed", problem)
            db.flush()
            return {"line_id": line.id, "msisdn": line.msisdn, "recharged": False, "reason": problem, "balance": _r(line.balance),
                    "attempts_left": max(0, 4 - failures)}
        amount, method, reference, validity = _r(v.face_value), "voucher", v.serial, int(v.validity_days or validity)
        v.status, v.redeemed_line_id, v.redeemed_at = "redeemed", line.id, utcnow()
        dealer = dealer or (db.get(MbPartner, v.partner_id) if v.partner_id else None)
    else:
        if e.get("amount") in (None, ""):
            raise ValueError("How much? (or a voucher PIN)")
        amount = _r(e["amount"])
        if not float(s["min_recharge"]) <= amount <= float(s["max_recharge"]):
            raise ValueError(f"A recharge is {s['min_recharge']:,.2f} to {s['max_recharge']:,.2f}")
        method = _pay_method(e.get("payment_method"))
        if method == "voucher":
            raise ValueError("Give the voucher's PIN")
        card = cs.last4(e.get("card_last4"), "Card") if e.get("card_last4") not in (None, "") else ""
        reference = str(e.get("reference") or (f"card ending {card}" if card else ""))
    _wallet(db, line, "voucher" if method == "voucher" else "recharge", amount, method=method, reference=reference,
            partner_id=dealer.id if dealer else None)
    recovered = 0.0
    if line.advance_owed > 0:
        recovered = _r(min(line.advance_owed, line.balance))
        if recovered > 0:
            _wallet(db, line, "advance_recovery", -recovered, reference="emergency credit recovered")
            line.advance_owed = _r(line.advance_owed - recovered)
    _extend_validity(line, validity)
    if line.plan_charge == "wallet" and line.plan_profile and _plan_pack(db, line) is None and line.status in ("active", "suspended"):
        _start_plan(db, tenant_id, line, line.plan_profile)  # a plan waiting for the money starts now
    if line.balance > 0:
        _bar(line, "no_balance", False)
    _event(db, line, "recharged", f"Recharged {amount:,.2f} by {method}" + (f" ({recovered:,.2f} emergency credit recovered)" if recovered else ""),
           amount=amount, method=method)
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "recharged": True, "amount": amount, "method": method, "recovered": recovered,
            "balance": _r(line.balance), "valid_until": line.valid_until.isoformat(), "dealer": dealer.name if dealer else None}


def transfer(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    source = line_of(db, tenant_id, e.get("msisdn"))
    target = line_of(db, tenant_id, e.get("to_msisdn"), what="Receiving line")
    if source.id == target.id:
        raise ValueError("Pick two different lines")
    for l in (source, target):
        if l.payment_type != "prepaid":
            raise ValueError(f"{l.msisdn} is postpaid - balance moves only between prepaid lines")
        if {"lost_stolen", "fraud"} & set(_barrings(l)):
            raise ValueError(f"{l.msisdn} is barred ({', '.join(_barrings(l))})")
    if source.status != "active":
        raise ValueError(f"{source.msisdn} is {source.status}")
    if source.advance_owed > 0:
        raise ValueError(f"{source.msisdn} still owes {source.advance_owed:,.2f} of emergency credit")
    amount = _r(e.get("amount") or 0)
    if amount <= 0:
        raise ValueError("How much to transfer?")
    if amount > float(s["transfer_max"]):
        raise ValueError(f"At most {s['transfer_max']:,.2f} per transfer")
    fee = _r(s["transfer_fee"])
    if source.balance < amount + fee:
        raise ValueError(f"{source.msisdn} has {source.balance:,.2f} - {amount + fee:,.2f} is needed with the {fee:,.2f} fee")
    _wallet(db, source, "transfer_out", -amount, reference=f"to {target.msisdn}")
    if fee:
        _wallet(db, source, "fee", -fee, reference="balance transfer fee")
    _wallet(db, target, "transfer_in", amount, reference=f"from {source.msisdn}")
    _bar(target, "no_balance", False)
    if source.balance <= 0:
        _bar(source, "no_balance", True)
    _event(db, source, "transfer_out", f"Sent {amount:,.2f} to {target.msisdn}")
    _event(db, target, "transfer_in", f"Received {amount:,.2f} from {source.msisdn}")
    db.flush()
    return {"line_id": source.id, "msisdn": source.msisdn, "to_msisdn": target.msisdn, "amount": amount, "fee": fee,
            "balance": _r(source.balance), "to_balance": _r(target.balance)}


def emergency_credit(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    line = line_of(db, tenant_id, e.get("msisdn"))
    if line.payment_type != "prepaid":
        raise ValueError(f"{line.msisdn} is postpaid - emergency credit is for prepaid lines")
    if {"lost_stolen", "fraud"} & set(_barrings(line)) or line.status != "active":
        raise ValueError(f"{line.msisdn} is {line.status}{' and barred' if _barrings(line) else ''} - no emergency credit")
    if line.advance_owed > 0:
        raise ValueError(f"{line.msisdn} still owes {line.advance_owed:,.2f} from the last emergency credit")
    amount, fee = _r(s["advance_amount"]), _r(s["advance_fee"])
    if line.balance >= amount:
        raise ValueError(f"{line.msisdn} has {line.balance:,.2f} - emergency credit is for a balance under {amount:,.2f}")
    days = (utcnow() - line.activated_at).days
    if days < int(s["advance_min_days"]):
        raise ValueError(f"{line.msisdn} has been on the network {days} day(s) - emergency credit needs {s['advance_min_days']}")
    _wallet(db, line, "advance", amount, reference="emergency credit")
    line.advance_owed = _r(amount + fee)
    _bar(line, "no_balance", False)
    if line.valid_until is None or line.valid_until < _today():
        _extend_validity(line, int(s["advance_validity_days"]))
    _event(db, line, "emergency_credit", f"Emergency credit {amount:,.2f} - {line.advance_owed:,.2f} recovered from the next recharge")
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "credited": amount, "fee": fee, "owed": line.advance_owed, "balance": _r(line.balance)}


# ------------------------------------------------------------------ packs and plans


def manage_pack(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    line = line_of(db, tenant_id, e.get("msisdn"))
    action = str(e.get("action") or "add").lower()
    if action in ("remove", "cancel", "unsubscribe", "stop"):
        spec_key = cs._key(e.get("pack"))
        found = [p for p in _active_packs(db, line) if p.kind != "plan" and spec_key in (cs._key(p.code), cs._key(p.name))]
        if not found:
            raise ValueError(f"{line.msisdn} has no active pack '{e.get('pack')}'")
        for p in found:
            p.status, p.auto_renew = "cancelled", False
        _event(db, line, "pack_removed", f"Removed {found[0].name}")
        db.flush()
        return {"line_id": line.id, "msisdn": line.msisdn, "action": "remove", "pack": found[0].code, "removed": len(found)}
    if action not in ("add", "buy", "subscribe"):
        raise ValueError("Action: add or remove")
    if line.status != "active":
        raise ValueError(f"{line.msisdn} is {line.status}")
    if {"lost_stolen", "fraud"} & set(_barrings(line)):
        raise ValueError(f"{line.msisdn} is barred ({', '.join(_barrings(line))})")
    spec = pack_of(db, tenant_id, e.get("pack") or e.get("offering_name"), party=db.get(Party, line.party_id), plan=line.plan)
    if spec.get("roaming") and not line.roaming:
        raise ValueError(f"Turn roaming on for {line.msisdn} before a roaming pack")
    paid = _charge(db, tenant_id, line, spec["price"], spec["name"])
    p = _add_pack(db, line, spec, paid, auto_renew=_bool(e.get("auto_renew")))
    if line.payment_type == "prepaid" and line.balance <= 0:
        _bar(line, "no_balance", True)
    _event(db, line, "pack_added", f"Added {spec['name']} ({spec['price']:,.2f}, from the {'wallet' if paid == 'balance' else paid})")
    _check_limit(db, line)
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "action": "add", "pack": spec["code"], "pack_id": p.id, "price": p.price, "paid_from": paid,
            "valid_to": p.valid_to.isoformat(), "balance": _r(line.balance), "cycle_spend": _r(line.cycle_spend)}


def _apply_plan(db: Session, tenant_id: int, line: MbLine, prof: dict[str, Any], why: str = "") -> MbPack | None:
    """The line moves to a plan (a product change, a reconfiguration, a switch): the old plan's allowance ends -
    a postpaid plan the mobile engine billed gets the unused part of its month back - and the new one starts,
    paid as the line's plan is charged."""
    old = _plan_pack(db, line)
    if old is not None:
        if line.plan_charge == "ledger" and old.paid_from == "bill" and old.price > 0:
            total = (old.valid_to - old.valid_from).total_seconds() or 1
            unused = max(0.0, (old.valid_to - utcnow()).total_seconds() / total)
            if unused > 0.01:
                _post(db, tenant_id, line, -_r(old.price * unused), f"{old.name} - unused part of the month")
                line.cycle_spend = _r(max(0.0, line.cycle_spend - old.price * unused))
        old.status, old.auto_renew = "cancelled", False
    previous, line.plan, line.plan_profile = line.plan, prof["name"], prof
    pack = _start_plan(db, tenant_id, line, prof)
    _event(db, line, "plan_changed", f"Plan {previous} -> {prof['name']}" + (f" ({why})" if why else ""))
    return pack


def _switch_payment(db: Session, tenant_id: int, line: MbLine, to: str, e: dict[str, Any], from_order: bool = False) -> float:
    """Prepaid <-> postpaid money moves: the prepaid balance goes to the bill (emergency credit recovered first),
    the deposit comes back as balance. Returns what was carried over."""
    s = settings(db, tenant_id)
    carried = 0.0
    if to == "postpaid":
        if line.advance_owed > 0:
            if line.balance < line.advance_owed:
                raise ValueError(f"{line.msisdn} owes {line.advance_owed:,.2f} of emergency credit - recharge it first")
            _wallet(db, line, "advance_recovery", -line.advance_owed, reference="emergency credit recovered")
            line.advance_owed = 0.0
        carried = _r(line.balance)
        if carried > 0:
            _wallet(db, line, "migration", -carried, reference="carried to the postpaid bill")
            _post(db, tenant_id, line, -carried, "prepaid balance carried over")
        line.credit_limit = _r(e.get("credit_limit") if e.get("credit_limit") not in (None, "") else s["credit_limit_default"])
        line.deposit = _r(line.deposit + _r(e.get("deposit") or 0))
        line.cycle_start, line.cycle_spend, line.alert_sent, line.valid_until = _today(), 0.0, False, None
        for b in ("no_balance", "validity"):
            _bar(line, b, False)
        line.plan_charge = "asset" if from_order or line.asset_id else "ledger"
    else:
        if line.deposit > 0:
            _wallet(db, line, "migration", line.deposit, reference="security deposit returned as balance")
            carried, line.deposit = _r(line.deposit), 0.0
        line.credit_limit, line.cycle_start, line.cycle_spend = 0.0, None, 0.0
        _bar(line, "credit_limit", False)
        line.valid_until = _today() + dt.timedelta(days=int(s["prepaid_validity_days"]))
        line.plan_charge = "wallet"
    previous, line.payment_type = line.payment_type, to
    _event(db, line, "migrated", f"{previous} -> {to}" + (f", {carried:,.2f} carried over" if carried else ""))
    return carried


def change_plan(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """INT-142. A line that is an asset of its own changes plan by a product-change order on the asset (billed and
    tracked like any product change); other lines change directly."""
    line = line_of(db, tenant_id, e.get("msisdn"))
    party = db.get(Party, line.party_id)
    plan = plan_of(db, tenant_id, e.get("plan") or e.get("offering_name"), party=party)
    if cs._key(plan["name"]) == cs._key(line.plan):
        raise ValueError(f"{line.msisdn} is already on {plan['name']}")
    if plan["payment_type"] != line.payment_type:
        raise ValueError(f"{plan['name']} is {plan['payment_type']} and {line.msisdn} is {line.payment_type} - switch it to {plan['payment_type']} instead")
    if line.status != "active":
        raise ValueError(f"{line.msisdn} is {line.status}")
    if plan["status"] != "active":
        raise ValueError(f"{plan['name']} is not on sale ({plan['status']})")
    previous, out = line.plan, {}
    if line.payment_type == "prepaid" and plan["price"] > line.balance:
        raise ValueError(f"{line.msisdn} has {line.balance:,.2f} - {plan['name']} costs {plan['price']:,.2f}. Recharge first.")
    if _single_asset(db, line) is not None:
        out = _change_order(db, tenant_id, line, "product_change", {"offering_name": plan["name"],
                                                                     "pricing_attributes": e.get("pricing_attributes") or {}})
    else:
        _apply_plan(db, tenant_id, line, plan)
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "plan": line.plan, "previous_plan": previous, "price": plan["price"],
            "balance": _r(line.balance), **out}


def migrate(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """INT-134 - prepaid <-> postpaid, keeping the number and the SIM: a product change to a plan of the other
    payment type (a change order on the line's asset when it has one of its own)."""
    line = line_of(db, tenant_id, e.get("msisdn"))
    party = db.get(Party, line.party_id)
    to = str(e.get("to_payment_type") or ("postpaid" if line.payment_type == "prepaid" else "prepaid")).lower()
    if to == line.payment_type:
        raise ValueError(f"{line.msisdn} is already {to}")
    if line.status != "active":
        raise ValueError(f"{line.msisdn} is {line.status}")
    if e.get("plan") or e.get("offering_name"):
        plan = plan_of(db, tenant_id, e.get("plan") or e.get("offering_name"), party=party)
    else:
        plan = next((p for p in catalog(db, tenant_id)["plans"] if p["payment_type"] == to), None)
    if plan is None or plan["payment_type"] != to:
        raise ValueError(f"Pick a {to} plan")
    if to == "postpaid":
        _standing(db, tenant_id, line.party_id, True)
    else:
        owed = cb.balance(db, tenant_id, line.party_id)
        if owed > 0:
            raise ValueError(f"The account owes {owed:,.2f} - settle the bill before moving {line.msisdn} to prepaid")
    previous, before = line.payment_type, _r(line.balance) if line.payment_type == "prepaid" else _r(line.deposit)
    out: dict[str, Any] = {}
    if _single_asset(db, line) is not None:
        out = _change_order(db, tenant_id, line, "product_change", {"offering_name": plan["name"]},
                            {"credit_limit": e.get("credit_limit"), "deposit": e.get("deposit")})
    else:
        _switch_payment(db, tenant_id, line, to, e)
        _apply_plan(db, tenant_id, line, plan)
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "payment_type": line.payment_type, "previous": previous, "plan": line.plan,
            "carried_over": before if line.payment_type != previous else 0.0, "credit_limit": _r(line.credit_limit), "balance": _r(line.balance), **out}

# ------------------------------------------------------------------ postpaid controls


def _check_limit(db: Session, line: MbLine) -> str | None:
    """Spend alert and credit-limit barring for a postpaid line. Returns 'alert' / 'barred' / None."""
    if line.payment_type != "postpaid" or line.credit_limit <= 0:
        return None
    if line.cycle_spend >= line.credit_limit:
        if _bar(line, "credit_limit", True):
            _event(db, line, "credit_limit", f"Spend {line.cycle_spend:,.2f} reached the {line.credit_limit:,.2f} limit - outgoing barred")
        return "barred"
    _bar(line, "credit_limit", False)
    if not line.alert_sent and line.cycle_spend >= line.credit_limit * line.spend_alert_pct / 100:
        line.alert_sent = True
        _event(db, line, "spend_alert", f"Spend {line.cycle_spend:,.2f} is {line.spend_alert_pct}% of the {line.credit_limit:,.2f} limit")
        return "alert"
    return None


def spend_limit(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    line = line_of(db, tenant_id, e.get("msisdn"))
    if line.payment_type != "postpaid":
        raise ValueError(f"{line.msisdn} is prepaid - its wallet is its limit")
    if e.get("credit_limit") in (None, "") and e.get("spend_alert_pct") in (None, ""):
        raise ValueError("Give a credit limit or an alert percentage")
    if e.get("credit_limit") not in (None, ""):
        limit = _r(e["credit_limit"])
        if limit < 0 or limit > float(s["credit_limit_max"]):
            raise ValueError(f"The credit limit is 0 (none) to {s['credit_limit_max']:,.2f}")
        line.credit_limit = limit
    if e.get("spend_alert_pct") not in (None, ""):
        pct = int(float(e["spend_alert_pct"]))
        if not 10 <= pct <= 100:
            raise ValueError("The alert is at 10% to 100% of the limit")
        line.spend_alert_pct = pct
    line.alert_sent = line.credit_limit > 0 and line.cycle_spend >= line.credit_limit * line.spend_alert_pct / 100
    if line.credit_limit <= 0:
        _bar(line, "credit_limit", False)
    state = _check_limit(db, line)
    _event(db, line, "spend_limit", f"Credit limit {line.credit_limit:,.2f}, alert at {line.spend_alert_pct}%")
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "credit_limit": _r(line.credit_limit), "spend_alert_pct": line.spend_alert_pct,
            "cycle_spend": _r(line.cycle_spend), "barred": "credit_limit" in _barrings(line), "state": state}


def deposit(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    line = line_of(db, tenant_id, e.get("msisdn"))
    action = str(e.get("action") or "take").lower()
    amount = _r(e.get("amount") or 0)
    if line.payment_type != "postpaid":
        raise ValueError(f"{line.msisdn} is prepaid - a deposit is for postpaid lines")
    if action in ("refund", "return", "release"):
        owed = cb.balance(db, tenant_id, line.party_id)
        if owed > 0:
            raise ValueError(f"The account owes {owed:,.2f} - settle it before the deposit is returned")
        amount = amount or line.deposit
        if amount <= 0 or amount > line.deposit:
            raise ValueError(f"{line.msisdn} holds a {line.deposit:,.2f} deposit")
        line.deposit = _r(line.deposit - amount)
        _event(db, line, "deposit_refunded", f"Deposit {amount:,.2f} returned by {_pay_method(e.get('payment_method'), 'bank_transfer')}")
    elif action in ("take", "collect", "add"):
        if amount <= 0:
            raise ValueError("How much deposit?")
        cs.no_card_numbers(e.get("reference"))
        line.deposit = _r(line.deposit + amount)
        _event(db, line, "deposit_taken", f"Deposit {amount:,.2f} taken by {_pay_method(e.get('payment_method'))}")
    else:
        raise ValueError("Action: take or refund")
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "action": "refund" if action in ("refund", "return", "release") else "take",
            "amount": amount, "deposit": _r(line.deposit)}


# ------------------------------------------------------------------ SIM, number, barring, roaming, sharing


def swap_sim(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    line = line_of(db, tenant_id, e.get("msisdn"))
    first = line.status == "pending" and not line.iccid  # the first SIM of a line activated without one: no checks, no fee
    if not first and not _bool(e.get("identity_verified")):
        raise ValueError("Verify the customer's identity before a SIM swap (identity_verified)")
    reason = "first SIM" if first else str(e.get("reason") or "damaged").lower()
    sim_type = str(e.get("sim_type") or line.sim_type).lower()
    if sim_type not in SIM_TYPES:
        raise ValueError("SIM type: physical or esim")
    old = line.iccid
    iccid, imsi, code = _sim(db, tenant_id, line.party_id, sim_type, str(e.get("new_iccid") or ""), f"SIM swap {line.msisdn}")
    if iccid == old:
        raise ValueError("That is the SIM the line already has")
    inv.release_from_subscriber(db, tenant_id, "sim", old, note=f"swapped out ({reason})")
    fee = 0.0 if first or _bool(e.get("waive_fee")) else _r(s["sim_swap_fee"])
    paid = _charge(db, tenant_id, line, fee, "SIM swap", kind="fee") if fee else "free"
    line.iccid, line.sim_type = iccid, sim_type
    line.imsi = imsi or f"{s['imsi_prefix']}{line.id:05d}{secrets.randbelow(10 ** 5):05d}"[:15]
    if first:
        line.status, line.status_reason = "active", ""
    else:
        line.swap_cooldown_until = utcnow() + dt.timedelta(hours=int(s["swap_cooldown_hours"]))
    _bar(line, "lost_stolen", False)
    _event(db, line, "sim_swapped", f"First SIM {iccid} ({sim_type}) - the line is active" if first else
           f"SIM {old} -> {iccid} ({reason}, {sim_type}); international and premium blocked until {line.swap_cooldown_until:%Y-%m-%d %H:%M}")
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "old_iccid": old, "iccid": iccid, "sim_type": sim_type, "fee": fee, "paid_from": paid,
            "status": line.status, "cooldown_until": line.swap_cooldown_until.isoformat() if line.swap_cooldown_until else None,
            **({"esim_activation_code": code} if code else {})}


def change_number(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    line = line_of(db, tenant_id, e.get("msisdn"))
    partner = db.get(MbPartner, line.partner_id) if line.partner_id else None
    new = _number(db, tenant_id, line.party_id, partner, str(e.get("new_msisdn") or ""), f"number change {line.msisdn}", {line.msisdn})
    fee = 0.0 if _bool(e.get("waive_fee")) else _r(s["number_change_fee"])
    paid = _charge(db, tenant_id, line, fee, "number change", kind="fee") if fee else "free"
    old = line.msisdn
    until = (_today() + dt.timedelta(days=int(s["quarantine_days"]))).isoformat()
    line.previous_msisdns = list(line.previous_msisdns or []) + [{"msisdn": old, "until": until}]
    # usage history follows the line
    db.query(CmUsage).filter(CmUsage.tenant_id == tenant_id, CmUsage.service == old).update({CmUsage.service: new}, synchronize_session=False)
    line.msisdn = new
    _event(db, line, "number_changed", f"Number {old} -> {new}; {old} is quarantined until {until}" + (f" ({e['reason']})" if e.get("reason") else ""))
    db.flush()
    return {"line_id": line.id, "msisdn": new, "old_msisdn": old, "quarantined_until": until, "fee": fee, "paid_from": paid}


def barring(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    line = line_of(db, tenant_id, e.get("msisdn"))
    action = str(e.get("action") or "bar").lower()
    name = str(e.get("barring") or "").lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    name = {"lost": "lost_stolen", "stolen": "lost_stolen", "lost_or_stolen": "lost_stolen", "intl": "international", "isd": "international",
            "outgoing_calls": "outgoing", "incoming_calls": "incoming", "internet": "data", "premium_rate": "premium"}.get(name, name)
    if name not in BARRINGS:
        raise ValueError(f"Barring: {', '.join(b for b in BARRINGS if b not in SYSTEM_BARRINGS)}")
    if action in ("unbar", "lift", "remove", "clear"):
        if name in SYSTEM_BARRINGS:
            hint = {"no_balance": "recharge it", "validity": "recharge it", "credit_limit": "raise the credit limit or wait for the next cycle"}[name]
            raise ValueError(f"The {name.replace('_', ' ')} barring lifts by itself - {hint}")
        if name == "lost_stolen" and not _bool(e.get("identity_verified")):
            raise ValueError("Verify the customer's identity before lifting a lost / stolen barring (or swap the SIM)")
        if name == "non_payment":
            overdue = cb.overdue_amount(db, tenant_id, line.party_id)
            if overdue > 0:
                raise ValueError(f"The account still has {overdue:,.2f} overdue")
        if not _bar(line, name, False):
            raise ValueError(f"{line.msisdn} is not barred for {name.replace('_', ' ')}")
        _event(db, line, "unbarred", f"Barring lifted: {name}" + (f" - {e['reason']}" if e.get("reason") else ""))
    elif action in ("bar", "block", "add"):
        if name in SYSTEM_BARRINGS:
            raise ValueError(f"{name.replace('_', ' ')} is set by the system")
        if name in ("lost_stolen", "fraud") and not e.get("reason"):
            raise ValueError("Say why (a reason)")
        if not _bar(line, name, True):
            raise ValueError(f"{line.msisdn} is already barred for {name.replace('_', ' ')}")
        if name == "roaming":
            line.roaming = False
        _event(db, line, "barred", f"Barred: {name}" + (f" - {e['reason']}" if e.get("reason") else ""))
    else:
        raise ValueError("Action: bar or unbar")
    db.flush()
    return {"line_id": line.id, "msisdn": line.msisdn, "action": "unbar" if action in ("unbar", "lift", "remove", "clear") else "bar",
            "barring": name, "barrings": _barrings(line)}


def roaming(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    line = line_of(db, tenant_id, e.get("msisdn"))
    on = str(e.get("action") or "on").lower() in ("on", "enable", "activate", "start", "true", "yes")
    if not on:
        if not line.roaming:
            raise ValueError(f"Roaming is already off on {line.msisdn}")
        line.roaming = False
        for p in _active_packs(db, line):
            if p.roaming:
                p.auto_renew = False
        _event(db, line, "roaming_off", "Roaming turned off")
        db.flush()
        return {"line_id": line.id, "msisdn": line.msisdn, "roaming": False}
    if line.roaming and not e.get("pack"):
        raise ValueError(f"Roaming is already on on {line.msisdn}")
    if line.status != "active":
        raise ValueError(f"{line.msisdn} is {line.status}")
    blocked = set(_barrings(line)) & {"roaming", "international", "lost_stolen", "fraud", "outgoing", "non_payment"}
    if blocked:
        raise ValueError(f"{line.msisdn} is barred for {', '.join(sorted(blocked))} - lift that first")
    if line.swap_cooldown_until and line.swap_cooldown_until > utcnow():
        raise ValueError(f"The SIM was just swapped - roaming opens after {line.swap_cooldown_until:%Y-%m-%d %H:%M}")
    if line.payment_type == "prepaid" and line.balance < float(s["roaming_min_balance"]) and not e.get("pack"):
        raise ValueError(f"Roaming needs at least {s['roaming_min_balance']:,.2f} on a prepaid line (or a roaming pack)")
    if line.payment_type == "postpaid":
        _standing(db, tenant_id, line.party_id, True)
        if line.credit_limit <= 0 and line.deposit <= 0:
            raise ValueError("A postpaid line needs a credit limit or a deposit before roaming")
    line.roaming = True
    out: dict[str, Any] = {"line_id": line.id, "msisdn": line.msisdn, "roaming": True}
    if e.get("pack"):
        out["pack"] = manage_pack(db, tenant_id, {"msisdn": line.msisdn, "pack": e["pack"], "action": "add"})
    _event(db, line, "roaming_on", "Roaming turned on" + (f" with {out['pack']['pack']}" if out.get("pack") else ""))
    db.flush()
    out["balance"] = _r(line.balance)
    return out


def shared_plan(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    head = line_of(db, tenant_id, e.get("msisdn"), what="Plan owner line")
    member = line_of(db, tenant_id, e.get("member_msisdn"), what="Member line")
    action = str(e.get("action") or "add").lower()
    if action in ("remove", "leave", "drop"):
        if member.group_head_id != head.id:
            raise ValueError(f"{member.msisdn} doesn't share {head.msisdn}'s plan")
        member.group_head_id = None
        _event(db, member, "left_group", f"Left {head.msisdn}'s shared plan")
        _event(db, head, "member_removed", f"{member.msisdn} left the shared plan")
        db.flush()
        return {"line_id": head.id, "msisdn": head.msisdn, "member": member.msisdn, "action": "remove", "members": _members(db, head)}
    if action not in ("add", "join"):
        raise ValueError("Action: add or remove")
    plan = head.plan_profile or {}
    if not plan.get("shared"):
        raise ValueError(f"{head.msisdn} is on {head.plan}, which is not a shared plan")
    if head.id == member.id:
        raise ValueError("Pick another line to share with")
    if head.group_head_id:
        raise ValueError(f"{head.msisdn} itself shares another line's plan")
    if db.query(MbLine.id).filter(MbLine.group_head_id == member.id, MbLine.status != "terminated").first():
        raise ValueError(f"{member.msisdn} already heads a shared plan")
    if member.group_head_id:
        raise ValueError(f"{member.msisdn} already shares {db.get(MbLine, member.group_head_id).msisdn}'s plan")
    if cs.root_of(db, member.party_id) != cs.root_of(db, head.party_id):
        raise ValueError("Only lines of the same customer (or account hierarchy) can share a plan")
    if len(_members(db, head)) >= int(s["shared_max_members"]):
        raise ValueError(f"A shared plan takes at most {s['shared_max_members']} members")
    member.group_head_id = head.id
    _event(db, member, "joined_group", f"Shares {head.msisdn}'s {plan['name']}")
    _event(db, head, "member_added", f"{member.msisdn} shares the plan")
    db.flush()
    return {"line_id": head.id, "msisdn": head.msisdn, "member": member.msisdn, "action": "add", "members": _members(db, head)}


def _members(db: Session, head: MbLine) -> list[str]:
    return [m.msisdn for m in db.query(MbLine).filter(MbLine.group_head_id == head.id, MbLine.status != "terminated").order_by(MbLine.id)]


def _terminate(db: Session, tenant_id: int, line: MbLine, reason: str, refund: bool = False, method: Any = None, quarantine: bool = True) -> dict[str, Any]:
    s = settings(db, tenant_id)
    out: dict[str, Any] = {}
    for p in _active_packs(db, line):
        p.status, p.auto_renew = "cancelled", False
    members = db.query(MbLine).filter(MbLine.group_head_id == line.id).all()
    for m in members:
        m.group_head_id = None
        _event(db, m, "left_group", f"{line.msisdn}'s shared plan ended - it was terminated")
    line.group_head_id = None
    if line.payment_type == "prepaid":
        left = _r(line.balance)
        if left > 0:
            kind = "refund" if refund else "forfeit"
            _wallet(db, line, kind, -left, method=_pay_method(method, "bank_transfer") if refund else "",
                    reference="balance refunded on termination" if refund else "balance forfeited on termination")
            out[kind] = left
    elif line.deposit > 0:
        _post(db, tenant_id, line, -line.deposit, "security deposit applied to the final bill")
        out["deposit_applied"], line.deposit = _r(line.deposit), 0.0
    inv.release_from_subscriber(db, tenant_id, "sim", line.iccid, note="line terminated")
    line.status, line.status_reason, line.terminated_at, line.roaming = "terminated", reason[:300], utcnow(), False
    if quarantine:
        line.quarantine_until = _today() + dt.timedelta(days=int(s["quarantine_days"]))
        out["quarantined_until"] = line.quarantine_until.isoformat()
    else:  # ported out: the number left with the customer
        inv.release_from_subscriber(db, tenant_id, "phone_number", line.msisdn, note="ported out")
    out["members_released"] = [m.msisdn for m in members]
    _event(db, line, "terminated", f"Terminated - {reason}" + (f"; number quarantined until {line.quarantine_until}" if quarantine else "; number ported out"))
    return out


def lifecycle(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """INT-143. A line that is an asset of its own is suspended, resumed or terminated by a change order on the
    asset - so billing, the order history and the asset follow; other lines change directly."""
    line = line_of(db, tenant_id, e.get("msisdn"))
    action = {"reactivate": "resume", "restore": "resume", "disconnect": "terminate", "cancel": "terminate"}.get(
        str(e.get("action") or "").lower(), str(e.get("action") or "").lower())
    reason = str(e.get("reason") or "")[:300]
    out: dict[str, Any] = {"line_id": line.id, "msisdn": line.msisdn, "action": action}
    if action == "suspend":
        if line.status == "suspended":
            raise ValueError(f"{line.msisdn} is already suspended")
    elif action == "resume":
        if line.status != "suspended":
            raise ValueError(f"{line.msisdn} is {line.status}, not suspended")
        if line.status_reason == "validity expired":
            raise ValueError(f"{line.msisdn} was suspended when its validity ran out - recharge it")
        blocked = set(_barrings(line)) & {"lost_stolen", "fraud", "non_payment"}
        if blocked:
            raise ValueError(f"{line.msisdn} is barred for {', '.join(sorted(blocked))} - lift that first")
    elif action == "terminate":
        if not reason:
            raise ValueError("Say why the line is terminated (a reason)")
    else:
        raise ValueError("Action: suspend, resume or terminate")
    if _single_asset(db, line) is not None:
        before = (_r(line.balance), _r(line.deposit))
        out.update(_change_order(db, tenant_id, line, action, {}, {"reason": reason or None, "refund_balance": _bool(e.get("refund_balance")),
                                                                    "payment_method": e.get("payment_method")}))
        if action == "terminate":
            out.update({"refund" if _bool(e.get("refund_balance")) else "forfeit": before[0]} if line.payment_type == "prepaid" and before[0] else {})
            out.update({"deposit_applied": before[1]} if line.payment_type == "postpaid" and before[1] else {})
            out["quarantined_until"] = line.quarantine_until.isoformat() if line.quarantine_until else None
    elif action == "suspend":
        line.status, line.status_reason = "suspended", reason or "at the customer's request"
        _event(db, line, "suspended", f"Suspended - {line.status_reason}")
    elif action == "resume":
        line.status, line.status_reason = "active", ""
        _event(db, line, "resumed", "Resumed" + (f" - {reason}" if reason else ""))
    else:
        out.update(_terminate(db, tenant_id, line, reason, _bool(e.get("refund_balance")), e.get("payment_method")))
    db.flush()
    out["status"] = line.status
    return out

# ------------------------------------------------------------------ online charging


def charge_usage(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """Charge one usage event online, like the network's charging system asks: packs first
    (the line's, then its shared plan's), then the wallet or the bill. Refused - with the reason,
    nothing taken - when the line can't make it."""
    s = settings(db, tenant_id)
    line = line_of(db, tenant_id, e.get("msisdn"))
    etype = str(e.get("event_type") or "voice").lower()
    if etype not in EVENT_TYPES:
        raise ValueError("Usage is voice, sms or data")
    qty = float(e.get("quantity") or 0)
    if qty <= 0:
        raise ValueError("How much? (minutes, messages or MB)")
    intl, prem, abroad = _bool(e.get("international")), _bool(e.get("premium")), _bool(e.get("roaming"))
    base = {"line_id": line.id, "msisdn": line.msisdn, "event_type": etype, "quantity": qty}

    def refuse(reason: str) -> dict[str, Any]:
        _event(db, line, "usage_refused", f"{etype} {qty:g} {UNITS[etype]} refused - {reason}")
        db.flush()
        return {**base, "granted": False, "reason": reason, "charged": 0.0, "balance": _r(line.balance), "cycle_spend": _r(line.cycle_spend)}

    bars = set(_barrings(line))
    if line.status != "active":
        return refuse(f"the line is {line.status}")
    stop = bars & set(_STOP_ALL)
    if stop:
        return refuse(f"barred ({', '.join(sorted(stop))})")
    if etype == "data" and "data" in bars:
        return refuse("data is barred")
    if intl and "international" in bars or prem and "premium" in bars:
        return refuse("international calls are barred" if intl else "premium-rate numbers are barred")
    if abroad and (not line.roaming or "roaming" in bars):
        return refuse("roaming is off")
    if (intl or prem or abroad) and line.swap_cooldown_until and line.swap_cooldown_until > utcnow():
        return refuse(f"the SIM was just swapped - international, premium and roaming open after {line.swap_cooldown_until:%Y-%m-%d %H:%M}")
    # packs: international and premium usage never comes out of a pack
    plan: list[tuple[MbPack, float]] = []
    left = qty
    if not (intl or prem):
        head = db.get(MbLine, line.group_head_id) if line.group_head_id else None
        pool = _active_packs(db, line) + ([p for p in _active_packs(db, head) if not p.roaming] if head and head.status == "active" else [])
        for p in pool:
            if left <= 0:
                break
            have = float((p.remaining or {}).get(etype, 0))
            if p.roaming != abroad or have <= 0:
                continue
            take = min(have, left)
            plan.append((p, take))
            left -= take
    amount = 0.0
    if left > 0:
        amount = cb.rate(db, tenant_id, etype, left, intl, prem) * (float(s["roaming_multiplier"]) if abroad else 1.0)
        amount = _r(amount)
    if line.payment_type == "prepaid" and amount > line.balance:
        if line.balance <= 0:
            _bar(line, "no_balance", True)
        return refuse(f"insufficient balance ({line.balance:,.2f}, {amount:,.2f} needed)")
    if line.payment_type == "postpaid" and line.credit_limit > 0 and line.cycle_spend + amount > line.credit_limit:
        _bar(line, "credit_limit", True)
        _event(db, line, "credit_limit", f"Usage would pass the {line.credit_limit:,.2f} limit - outgoing barred")
        return refuse(f"the {line.credit_limit:,.2f} credit limit would be passed")
    used: dict[str, float] = {}
    for p, take in plan:
        remaining = dict(p.remaining or {})
        remaining[etype] = round(float(remaining.get(etype, 0)) - take, 4)
        p.remaining = remaining
        used[p.code] = round(used.get(p.code, 0.0) + take, 4)
    if amount > 0 and line.payment_type == "prepaid":
        _wallet(db, line, "usage", -amount, reference=f"{etype} {left:g} {UNITS[etype]}" + (" abroad" if abroad else ""))
    elif amount > 0:
        line.cycle_spend = _r(line.cycle_spend + amount)
    cb.record_usage(db, tenant_id, line.party_id, [{
        "event_type": etype, "quantity": qty, "service": line.msisdn, "destination": str(e.get("destination") or "")[:100],
        "international": intl, "premium": prem, "rate_plan": "roaming" if abroad else "standard",
        # prepaid usage is paid already - the bill run must not bill it again
        "amount": amount if line.payment_type == "postpaid" else 0.0,
    }])
    alert = None
    if line.payment_type == "prepaid":
        if line.balance <= 0:
            _bar(line, "no_balance", True)
        if line.balance < float(s["low_balance_alert"]):
            alert = "low_balance"
    else:
        alert = _check_limit(db, line)
    db.flush()
    return {**base, "granted": True, "from_packs": used, "charged_quantity": round(left, 4), "charged": amount,
            "paid_from": "balance" if line.payment_type == "prepaid" else "bill", "balance": _r(line.balance),
            "cycle_spend": _r(line.cycle_spend), "alert": alert}


def balance_view(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    line = line_of(db, tenant_id, e.get("msisdn"), live=False)
    view = line_out(db, line, full=True)
    left = ", ".join(f"{v:g} {UNITS[k]}" for k, v in view["remaining"].items() if k in UNITS) or "no allowance left"
    money = (f"balance {view['balance']:,.2f}, valid until {view['valid_until']}" if line.payment_type == "prepaid"
             else f"spent {view['cycle_spend']:,.2f} this cycle" + (f" of a {view['credit_limit']:,.2f} limit" if line.credit_limit else ""))
    return {**view, "line_id": line.id, "summary": f"{line.msisdn} ({line.payment_type}, {line.status}): {money}; {left}"}


# ------------------------------------------------------------------ wholesale: partners, vouchers, settlements


def partner_out(p: MbPartner) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "kind": p.kind, "brand": p.brand, "wholesale_rates": p.wholesale_rates or {},
            "revenue_share_pct": p.revenue_share_pct, "number_range": p.number_range, "contact_email": p.contact_email, "status": p.status,
            "created_at": p.created_at}


def save_partner(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    name = str(e.get("wholesale_partner") or e.get("partner") or e.get("name") or "").strip()
    if not name:
        raise ValueError("The partner's name?")
    p = db.query(MbPartner).filter(MbPartner.tenant_id == tenant_id, MbPartner.name.ilike(name)).first()
    created = p is None
    if p is None:
        p = MbPartner(tenant_id=tenant_id, name=name[:200], kind="mvno", brand="", wholesale_rates={}, revenue_share_pct=0.0, number_range="",
                      contact_email="", status="active", notes="")
        db.add(p)
    if e.get("partner_kind") not in (None, ""):
        kind = str(e["partner_kind"]).lower()
        if kind not in PARTNER_KINDS:
            raise ValueError(f"Partner kind: {', '.join(PARTNER_KINDS)}")
        p.kind = kind
    if e.get("brand") not in (None, ""):
        p.brand = str(e["brand"])[:100]
    if e.get("wholesale_rates") not in (None, ""):
        raw = e["wholesale_rates"] if isinstance(e["wholesale_rates"], dict) else dict(
            re.findall(r"([A-Za-z]+)\s*(?:[:=]|\bis\b|\bat\b)?\s*(\d+(?:\.\d+)?)", str(e["wholesale_rates"])))
        if not raw:
            raise ValueError("Wholesale rates are per voice minute, SMS and MB of data, e.g. voice 0.2, sms 0.05, data 0.01")
        rates = {}
        for k, v in raw.items():
            key = {"minute": "voice", "minutes": "voice", "call": "voice", "calls": "voice", "message": "sms", "messages": "sms",
                   "mb": "data", "internet": "data"}.get(str(k).lower(), str(k).lower())
            if key not in EVENT_TYPES:
                raise ValueError("Wholesale rates are per voice minute, SMS and MB of data, e.g. voice 0.2, sms 0.05, data 0.01")
            try:
                rates[key] = float(str(v).split()[0])
            except ValueError as exc:
                raise ValueError(f"'{v}' is not a rate") from exc
        p.wholesale_rates = rates
    if e.get("revenue_share_pct") not in (None, ""):
        pct = float(e["revenue_share_pct"])
        if not 0 <= pct <= 100:
            raise ValueError("The revenue share is 0% to 100%")
        p.revenue_share_pct = pct
    if e.get("number_range") not in (None, ""):
        rng = _parse_range(str(e["number_range"]))
        if rng is None:
            raise ValueError("A number range is start-end with the same number of digits, e.g. 9180000000-9180999999")
        for other in db.query(MbPartner).filter(MbPartner.tenant_id == tenant_id, MbPartner.id != (p.id or 0)).all():
            o = _parse_range(other.number_range)
            if o and not (rng[1] < o[0] or rng[0] > o[1]):
                raise ValueError(f"That range overlaps {other.name}'s ({other.number_range})")
        p.number_range = f"{rng[0]}-{rng[1]}"
    if e.get("contact_email") not in (None, ""):
        p.contact_email = str(e["contact_email"])[:200]
    if (e.get("partner_status") or e.get("status")) not in (None, ""):
        status = str(e.get("partner_status") or e.get("status")).lower()
        if status not in ("active", "suspended"):
            raise ValueError("Status: active or suspended")
        p.status = status
    if p.kind in ("mvno", "mvne") and not p.wholesale_rates and created:
        raise ValueError(f"An {p.kind.upper()} needs its wholesale rates (voice, sms, data)")
    db.flush()
    return {"partner_id": p.id, "partner": p.name, "created": created, **partner_out(p)}


def generate_vouchers(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    s = settings(db, tenant_id)
    count = int(e.get("count") or e.get("quantity") or 0)
    if not 1 <= count <= int(s["voucher_max_batch"]):
        raise ValueError(f"A batch has 1 to {s['voucher_max_batch']} vouchers")
    value = _r(e.get("face_value") or e.get("amount") or 0)
    if not float(s["min_recharge"]) <= value <= float(s["max_recharge"]):
        raise ValueError(f"A voucher is worth {s['min_recharge']:,.2f} to {s['max_recharge']:,.2f}")
    validity = int(e.get("validity_days") or s["prepaid_validity_days"])
    expires = _today() + dt.timedelta(days=int(e.get("expires_in_days") or 365))
    dealer = _partner(db, tenant_id, e.get("dealer"))
    seq = db.query(MbVoucher.batch).filter(MbVoucher.tenant_id == tenant_id).distinct().count() + 1
    batch = f"B{_today():%Y%m%d}-{seq:03d}"
    pins, rows, seen = [], [], set()
    for n in range(1, count + 1):
        while True:
            pin = "".join(str(secrets.randbelow(10)) for _ in range(14))
            h = _hash_pin(tenant_id, pin)
            if h not in seen and not db.query(MbVoucher.id).filter(MbVoucher.tenant_id == tenant_id, MbVoucher.pin_hash == h).first():
                break
        seen.add(h)
        serial = f"{batch}-{n:06d}"
        rows.append(MbVoucher(tenant_id=tenant_id, batch=batch, serial=serial, pin_hash=h, face_value=value, validity_days=validity,
                              partner_id=dealer.id if dealer else None, status="available", expires_on=expires))
        pins.append(f"{serial},{pin}")
    rows[0].pending_pins = "serial,pin\n" + "\n".join(pins) + "\n"
    db.add_all(rows)
    db.flush()
    return {"batch": batch, "count": count, "face_value": value, "validity_days": validity, "expires_on": expires.isoformat(),
            "dealer": dealer.name if dealer else None, "first_serial": rows[0].serial, "last_serial": rows[-1].serial,
            "download": f"/api/mobile/vouchers/batches/{batch}/pins",
            "summary": f"{count} vouchers of {value:,.2f} - download the PINs once from Mobile -> Vouchers"}


def take_pins(db: Session, tenant_id: int, batch: str) -> str:
    """The batch's PINs as CSV - once; they are cleared as they are handed out."""
    first = db.query(MbVoucher).filter(MbVoucher.tenant_id == tenant_id, MbVoucher.batch == batch).order_by(MbVoucher.id).first()
    if first is None:
        raise ValueError(f"No voucher batch {batch}")
    if not first.pending_pins:
        raise ValueError(f"The PINs of {batch} were downloaded already - they are not kept")
    text, first.pending_pins = first.pending_pins, None
    db.flush()
    return text


def voucher_batches(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for v in db.query(MbVoucher).filter(MbVoucher.tenant_id == tenant_id).order_by(MbVoucher.id):
        b = out.setdefault(v.batch, {"batch": v.batch, "face_value": v.face_value, "validity_days": v.validity_days, "expires_on": v.expires_on,
                                     "partner_id": v.partner_id, "created_at": v.created_at, "count": 0, "redeemed": 0, "available": 0,
                                     "blocked": 0, "expired": 0, "pins_pending": False})
        b["count"] += 1
        b[v.status] = b.get(v.status, 0) + 1
        b["pins_pending"] = b["pins_pending"] or bool(v.pending_pins)
    return list(reversed(out.values()))


def block_batch(db: Session, tenant_id: int, batch: str, blocked: bool = True) -> int:
    rows = db.query(MbVoucher).filter(MbVoucher.tenant_id == tenant_id, MbVoucher.batch == batch,
                                      MbVoucher.status == ("available" if blocked else "blocked")).all()
    for v in rows:
        v.status = "blocked" if blocked else "available"
    db.flush()
    return len(rows)


def _period(e: dict[str, Any]) -> tuple[dt.date, dt.date]:
    start, end = cs._date(e.get("period_start")), cs._date(e.get("period_end"))
    if start is None and end is None:
        first = _today().replace(day=1)
        end = first - dt.timedelta(days=1)
        start = end.replace(day=1)
    elif start is None:
        start = end.replace(day=1)
    elif end is None:
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    if start > end:
        raise ValueError("The period starts after it ends")
    return start, end


def settlement_out(db: Session, st: MbSettlement) -> dict[str, Any]:
    p = db.get(MbPartner, st.partner_id)
    return {"id": st.id, "number": st.number, "partner": p.name if p else "", "partner_id": st.partner_id, "period_start": st.period_start,
            "period_end": st.period_end, "lines": st.lines or [], "wholesale_due": st.wholesale_due, "commission_due": st.commission_due,
            "net": st.net, "status": st.status, "created_at": st.created_at}


def settle(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    partner = _partner(db, tenant_id, e.get("wholesale_partner") or e.get("partner"), required=True)
    start, end = _period(e)
    if db.query(MbSettlement.id).filter(MbSettlement.partner_id == partner.id, MbSettlement.period_start == start,
                                        MbSettlement.period_end == end).first():
        raise ValueError(f"{partner.name} is settled for {start} - {end} already")
    since, until = dt.datetime.combine(start, dt.time.min), dt.datetime.combine(end, dt.time.max)
    numbers = {l.msisdn for l in db.query(MbLine).filter(MbLine.tenant_id == tenant_id, MbLine.partner_id == partner.id)}
    rows: list[dict[str, Any]] = []
    wholesale = 0.0
    if numbers:
        totals = {k: 0.0 for k in EVENT_TYPES}
        for u in db.query(CmUsage).filter(CmUsage.tenant_id == tenant_id, CmUsage.service.in_(numbers), CmUsage.at >= since, CmUsage.at <= until):
            totals[u.event_type] = totals.get(u.event_type, 0.0) + u.quantity
        for k, qty in totals.items():
            rate = float((partner.wholesale_rates or {}).get(k, 0))
            if qty and rate:
                amount = _r(qty * rate)
                wholesale += amount
                rows.append({"label": f"Wholesale {k} ({UNITS[k]})", "quantity": round(qty, 2), "rate": rate, "amount": amount})
    sold = sum(t.amount for t in db.query(MbWalletTxn).filter(MbWalletTxn.tenant_id == tenant_id, MbWalletTxn.partner_id == partner.id,
                                                             MbWalletTxn.kind.in_(("recharge", "voucher")), MbWalletTxn.at >= since,
                                                             MbWalletTxn.at <= until))
    commission = _r(sold * partner.revenue_share_pct / 100)
    if commission:
        rows.append({"label": f"Commission on recharges sold ({partner.revenue_share_pct:g}%)", "quantity": _r(sold),
                     "rate": partner.revenue_share_pct / 100, "amount": -commission})
    st = MbSettlement(tenant_id=tenant_id, partner_id=partner.id, period_start=start, period_end=end, lines=rows, wholesale_due=_r(wholesale),
                      commission_due=commission, net=_r(wholesale - commission), status="issued")
    db.add(st)
    db.flush()
    st.number = f"STL-{st.id:06d}"
    db.flush()
    return {"settlement_id": st.id, "number": st.number, "partner": partner.name, **settlement_out(db, st),
            "summary": f"{partner.name} {start} - {end}: wholesale {wholesale:,.2f}, commission {commission:,.2f}, net {st.net:,.2f}"}


def settlements(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    return [settlement_out(db, s) for s in db.query(MbSettlement).filter(MbSettlement.tenant_id == tenant_id).order_by(MbSettlement.id.desc()).limit(200)]


def partners(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    out = []
    for p in db.query(MbPartner).filter(MbPartner.tenant_id == tenant_id).order_by(MbPartner.name):
        n = db.query(MbLine.id).filter(MbLine.partner_id == p.id, MbLine.status != "terminated").count()
        out.append({**partner_out(p), "lines": n})
    return out


def overview(db: Session, tenant_id: int) -> dict[str, Any]:
    rows = db.query(MbLine).filter(MbLine.tenant_id == tenant_id).all()
    live = [l for l in rows if l.status != "terminated"]
    return {
        "lines": len(live), "prepaid": sum(1 for l in live if l.payment_type == "prepaid"), "postpaid": sum(1 for l in live if l.payment_type == "postpaid"),
        "active": sum(1 for l in live if l.status == "active"), "suspended": sum(1 for l in live if l.status == "suspended"),
        "barred": sum(1 for l in live if l.barrings), "roaming": sum(1 for l in live if l.roaming), "terminated": len(rows) - len(live),
        "prepaid_balance": _r(sum(l.balance for l in live if l.payment_type == "prepaid")),
        "postpaid_spend": _r(sum(l.cycle_spend for l in live if l.payment_type == "postpaid")),
        "vouchers_available": db.query(MbVoucher.id).filter(MbVoucher.tenant_id == tenant_id, MbVoucher.status == "available").count(),
        "partners": db.query(MbPartner.id).filter(MbPartner.tenant_id == tenant_id).count(),
        "recent": [{"line_id": ev.line_id, "action": ev.action, "summary": ev.summary, "at": ev.at}
                   for ev in db.query(MbLineEvent).filter(MbLineEvent.tenant_id == tenant_id).order_by(MbLineEvent.id.desc()).limit(20)],
    }


# ------------------------------------------------------------------ housekeeping (scheduler)


def _months_later(d: dt.date, n: int = 1) -> dt.date:
    month = d.month - 1 + n
    year, month = d.year + month // 12, month % 12 + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def housekeeping(db: Session, tenant_id: int | None = None, now: dt.datetime | None = None) -> dict[str, int]:
    """Packs expire or renew, prepaid validity runs out (incoming only, then suspended after the
    grace period), postpaid cycles start again, quarantined numbers go back, vouchers expire."""
    now = now or utcnow()
    today = now.date()
    counts = {"renewed": 0, "expired": 0, "validity": 0, "suspended": 0, "cycles": 0, "numbers_released": 0, "vouchers_expired": 0}
    s_cache: dict[int, dict[str, Any]] = {}
    lq = db.query(MbLine).filter(MbLine.status != "terminated")
    if tenant_id:
        lq = lq.filter(MbLine.tenant_id == tenant_id)
    for line in lq.all():
        s = s_cache.setdefault(line.tenant_id, settings(db, line.tenant_id))
        if line.payment_type == "prepaid" and line.valid_until and line.valid_until < today:
            if _bar(line, "validity", True):
                _event(db, line, "validity", f"Validity ended {line.valid_until} - incoming only for {s['grace_days']} days")
                counts["validity"] += 1
            if line.status == "active" and line.valid_until + dt.timedelta(days=int(s["grace_days"])) < today:
                line.status, line.status_reason = "suspended", "validity expired"
                _event(db, line, "suspended", "Suspended - validity and grace period over")
                counts["suspended"] += 1
        if line.payment_type == "postpaid" and line.cycle_start and _months_later(line.cycle_start) <= today:
            while _months_later(line.cycle_start) <= today:
                line.cycle_start = _months_later(line.cycle_start)
            line.cycle_spend, line.alert_sent = 0.0, False
            if _bar(line, "credit_limit", False):
                _event(db, line, "unbarred", "New billing cycle - credit-limit barring lifted")
            counts["cycles"] += 1
        kept = []
        for old in line.previous_msisdns or []:
            if str(old.get("until") or "") < today.isoformat():
                inv.release_from_subscriber(db, line.tenant_id, "phone_number", old.get("msisdn") or "", note="quarantine over")
                counts["numbers_released"] += 1
            else:
                kept.append(old)
        if len(kept) != len(line.previous_msisdns or []):
            line.previous_msisdns = kept
    # after the cycles started again, so a renewed plan is the new cycle's first spend
    pq = db.query(MbPack).filter(MbPack.status == "active", MbPack.valid_to <= now)
    if tenant_id:
        pq = pq.filter(MbPack.tenant_id == tenant_id)
    for p in pq.limit(2000).all():
        line = db.get(MbLine, p.line_id)
        p.status = "expired"
        if not p.auto_renew or line is None or line.status != "active":
            counts["expired"] += 1
            continue
        try:
            paid = "asset" if p.paid_from == "asset" else _charge(db, line.tenant_id, line, p.price, f"{p.name} renewal",
                                                                  kind="plan_fee" if p.kind == "plan" else "pack")
        except ValueError:
            _event(db, line, "renewal_failed", f"{p.name} not renewed - {line.balance:,.2f} on the wallet, {p.price:,.2f} needed")
            counts["expired"] += 1
            continue
        days = max(1, round((p.valid_to - p.valid_from).total_seconds() / 86400))
        _add_pack(db, line, {"code": p.code, "name": p.name, "kind": p.kind, "allowance": p.granted or {}, "roaming": p.roaming, "price": p.price,
                             "validity_days": days}, paid, kind=p.kind, auto_renew=True, start=now)
        if p.kind == "plan" and line.payment_type == "prepaid":
            _extend_validity(line, days)
        _event(db, line, "renewed", f"{p.name} renewed ({p.price:,.2f})")
        _check_limit(db, line)
        counts["renewed"] += 1
    tq = db.query(MbLine).filter(MbLine.status == "terminated", MbLine.quarantine_until.isnot(None), MbLine.quarantine_until < today)
    if tenant_id:
        tq = tq.filter(MbLine.tenant_id == tenant_id)
    for line in tq.all():
        inv.release_from_subscriber(db, line.tenant_id, "phone_number", line.msisdn, note="quarantine over")
        line.quarantine_until = None
        counts["numbers_released"] += 1
    vq = db.query(MbVoucher).filter(MbVoucher.status == "available", MbVoucher.expires_on < today)
    if tenant_id:
        vq = vq.filter(MbVoucher.tenant_id == tenant_id)
    for v in vq.limit(5000).all():
        v.status = "expired"
        counts["vouchers_expired"] += 1
    db.flush()
    return counts


_last_run: dt.datetime | None = None


def run_due(session_factory) -> None:
    """Every 15 minutes at most (the scheduler ticks every minute)."""
    global _last_run
    now = utcnow()
    if _last_run and now - _last_run < dt.timedelta(minutes=15):
        return
    _last_run = now
    db = session_factory()
    try:
        housekeeping(db, now=now)
        db.commit()
    finally:
        db.close()
