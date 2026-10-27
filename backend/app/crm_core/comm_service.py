"""Communications - the customer, premises, asset, sales and service side of
the Siebel Communications Guide (billing, credit and fraud are in
comm_billing.py).

  accounts       account hierarchy and classes (customer, service and billing
                 accounts and their aggregators; moving a parent moves its whole
                 tree - mergers), good standing, partners, contacts on several
                 accounts, billing profiles (postpaid / prepaid, payment method,
                 bill media, top-up), account profiles (exemption, financial with
                 a credit check, fraud thresholds, loyalty scores, site,
                 statement, customer), validated addresses
  premises       premises, service points (hookups), equipment and
                 infrastructure, service requests and usage per premises
  special rating friends & family lists with a maximum size, locked while an
                 order for the service is in flight
  assets         related assets (backup / option), component transactions
                 (install / replace / remove), change requests
  opportunities  related sites, partners, a qualification profile
  service        parent / child trouble tickets (one network ticket, many
                 customer tickets), resolve / close / reopen with a solution,
                 order fallout tickets, solutions, satisfaction surveys,
                 ticket analysis
  agreements     revisions (name (.n)), sub-agreements, terms, the agreement
                 document, orders and assets it covers
  work orders    an order split into one work order per provider
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import fs_service, om_service as om, ticket_service
from app.integration import ticket_adapter
from app.models.assurance import TroubleTicket
from app.models.comms import (
    ACCOUNT_CLASSES,
    PROFILE_KINDS,
    CmAccountDetail,
    CmAddress,
    CmAgreementDetail,
    CmAgreementRevision,
    CmAssetRelation,
    CmAssetTransaction,
    CmBillingProfile,
    CmChangeRequest,
    CmContact,
    CmContactAccount,
    CmOpportunityLink,
    CmOpportunityProfile,
    CmPremise,
    CmProfile,
    CmProvider,
    CmProviderWorkOrder,
    CmRatingItem,
    CmRatingList,
    CmServicePoint,
    CmSetting,
    CmSiteItem,
    CmSolution,
    CmSurvey,
    CmTicketDetail,
)
from app.models.crm import Opportunity, Party, PartyProfile
from app.models.field_service import FsAssetDetail, FsEntitlement
from app.models.fulfillment import Asset, AssetStatus
from app.models.order_mgmt import OmAgreement, OmLine, OmOrderDetail, OmRecommendation
from app.models.orders import Order, OrderStatus
from app.timeutils import utcnow

CARD_NUMBER = om.CARD_NUMBER
LIVE = (OrderStatus.active, OrderStatus.changed, OrderStatus.suspended)

DEFAULTS: dict[str, Any] = {
    # billing
    "payment_terms_days": 21,
    "usage_rates": {"voice": 0.5, "sms": 0.25, "data": 0.05, "international_multiplier": 4.0, "premium_multiplier": 6.0},
    "adjustment_auto_amount": 500.0,  # credits up to this post straight away...
    "adjustment_auto_pct": 25.0,  # ...if they are at most this % of what they adjust
    "arrangement_max_installments": 6,
    "arrangement_auto_amount": 10000.0,
    "arrangement_interest_pct": 1.0,  # per month of the plan
    "arrangement_max_deferral_days": 60,
    # collections
    "grace_days": 5,
    "late_fee_days": 15,
    "late_fee_pct": 2.0,
    "late_fee_min": 25.0,
    "collections_ladder": [[1, "courtesy_email_sms"], [7, "courtesy_phone_call"], [15, "courtesy_dunning_letter"],
                           [30, "demanding_phone_call"], [45, "harsh_dunning_letter"], [60, "suspend_services"],
                           [90, "refer_to_outside_agency"]],
    # fraud
    "fraud_defaults": {"daily_threshold": 1000.0, "international_threshold": 500.0, "domestic_threshold": 800.0,
                       "max_call_minutes": 240.0, "max_calls": 200, "sensitive_threshold": 3, "credit_threshold": 5000.0,
                       "overflow_pct": 10.0, "simultaneous_minutes": 15},
    "fraud_contact_attempts": 3,
    # sales
    "qualification_questions": [
        {"key": "budget", "question": "Is there an approved budget?", "weight": 25},
        {"key": "authority", "question": "Are we talking to the decision maker?", "weight": 20},
        {"key": "need", "question": "Is there a confirmed need or pain?", "weight": 25},
        {"key": "timeline", "question": "Will they buy within 90 days?", "weight": 20},
        {"key": "switching", "question": "Are they unhappy with their current provider?", "weight": 10},
    ],
    "target_market_score": 60,
    # agreements
    "agreement_sections": [
        "SERVICE AGREEMENT {agreement_number} (revision {revision})",
        "This agreement is made between {provider} and {customer} and is effective from {start} to {end} ({term_months} months).",
        "1. Services. The provider supplies: {lines}.",
        "2. Charges. {monthly_value} per month and {one_time_value} one-time, payable {payment_terms}.",
        "3. Service level. {coverage} coverage with a response within {response_hours} hours.",
        "4. Further terms. {terms}",
        "5. Renewal. {renewal}",
        "Signed for {customer}: ____________________    Signed for {provider}: ____________________",
    ],
}


# ------------------------------------------------------------------ helpers


def _r(x: Any) -> float:
    return round(float(x or 0), 2)


def _j(text: str | None, default: Any) -> Any:
    try:
        v = json.loads(text or "")
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


def _bool(v: Any, default: bool = False) -> bool:
    if v in (None, ""):
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _date(value: Any) -> dt.date | None:
    return om._date(value)


def _own(db: Session, model, row_id: Any, tenant_id: int, label: str):
    try:
        row = db.get(model, int(row_id or 0))
    except (TypeError, ValueError):
        row = None
    if row is None or row.tenant_id != tenant_id:
        raise ValueError(f"{label} {row_id} not found")
    return row


def no_card_numbers(*values: Any) -> None:
    for v in values:
        if v and CARD_NUMBER.search(str(v)):
            raise ValueError("Never enter a full card or account number - only the last 4 digits")


def last4(value: Any, label: str) -> str:
    text = re.sub(r"\D", "", str(value or ""))
    if not text:
        return ""
    if len(text) != 4:
        raise ValueError(f"{label}: give only the last 4 digits")
    return text


def kv(text: Any) -> dict[str, str]:
    """'tax exempt: yes, certificate = TX-77' -> {'tax_exempt': 'yes', 'certificate': 'TX-77'}"""
    if isinstance(text, dict):
        return {re.sub(r"\W+", "_", str(k).strip().lower()).strip("_"): str(v) for k, v in text.items()}
    out: dict[str, str] = {}
    for part in re.split(r"[;,\n]", str(text or "")):
        m = re.match(r"\s*([\w .'-]+?)\s*(?::|=|\bis\b|\bto\b)\s*(.+?)\s*$", part) or \
            re.match(r"\s*([\w .'-]+?)\s+(yes|no|y|n|true|false)\s*$", part, re.I)
        if m:
            out[re.sub(r"\W+", "_", m.group(1).strip().lower()).strip("_")] = m.group(2).strip()
    return out


def settings(db: Session, tenant_id: int) -> dict[str, Any]:
    row = db.query(CmSetting).filter(CmSetting.tenant_id == tenant_id).first()
    data = _j(row.data_json, {}) if row else {}
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def save_settings(db: Session, tenant_id: int, data: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in data if k not in DEFAULTS]
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")
    row = db.query(CmSetting).filter(CmSetting.tenant_id == tenant_id).first()
    if row is None:
        row = CmSetting(tenant_id=tenant_id, data_json="{}")
        db.add(row)
    current = _j(row.data_json, {})
    current.update(data)
    row.data_json = json.dumps(current)
    db.flush()
    return settings(db, tenant_id)


def party_of(db: Session, tenant_id: int, e: dict[str, Any], id_key: str = "party_id", name_key: str = "customer",
             required: bool = True) -> Party | None:
    """The customer an intent is about - by id, or by (unique) name."""
    if e.get(id_key) not in (None, ""):
        return _own(db, Party, e[id_key], tenant_id, "Customer")
    name = str(e.get(name_key) or e.get("company_name") or "").strip()
    if name:
        exact = db.query(Party).filter(Party.tenant_id == tenant_id, Party.name.ilike(name)).all()
        if len(exact) == 1:
            return exact[0]
        like = db.query(Party).filter(Party.tenant_id == tenant_id, Party.name.ilike(f"%{name}%")).all()
        if len(like) == 1:
            return like[0]
        if len(like) > 1:
            raise ValueError(f"More than one customer matches '{name}' - pick one: {', '.join(p.name for p in like[:6])}")
        raise ValueError(f"No customer called '{name}'")
    if required:
        raise ValueError("Which customer?")
    return None


def party_name(db: Session, party_id: int | None) -> str:
    p = db.get(Party, party_id) if party_id else None
    return p.name if p else ""


# ------------------------------------------------------------------ account hierarchy


def account_detail(db: Session, tenant_id: int, party: Party, create: bool = True) -> CmAccountDetail | None:
    d = db.query(CmAccountDetail).filter(CmAccountDetail.party_id == party.id).first()
    if d is None and create:
        d = CmAccountDetail(tenant_id=tenant_id, party_id=party.id, account_class="customer",
                            account_type="residential" if party.party_type == "individual" else "business")
        db.add(d)
        db.flush()
    return d


def _ancestors(db: Session, party_id: int) -> list[int]:
    seen: list[int] = []
    cur = party_id
    while cur and cur not in seen and len(seen) < 50:
        seen.append(cur)
        d = db.query(CmAccountDetail).filter(CmAccountDetail.party_id == cur).first()
        cur = d.parent_party_id if d else None
    return seen


def children_of(db: Session, tenant_id: int, party_id: int) -> list[CmAccountDetail]:
    return (db.query(CmAccountDetail).filter(CmAccountDetail.tenant_id == tenant_id, CmAccountDetail.parent_party_id == party_id)
            .order_by(CmAccountDetail.id).all())


def root_of(db: Session, party_id: int) -> int:
    return _ancestors(db, party_id)[-1]


def set_hierarchy(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """Create a child account or move / classify an existing one."""
    party = party_of(db, tenant_id, e, required=False)
    created = False
    if party is None:
        name = str(e.get("account_name") or "").strip()
        if not name:
            raise ValueError("Which account - pick one, or give the new account's name")
        party = Party(tenant_id=tenant_id, name=name, party_type="organization", email=e.get("email") or None, phone=e.get("phone") or None)
        db.add(party)
        db.flush()
        created = True
    detail = account_detail(db, tenant_id, party)
    parent = party_of(db, tenant_id, e, "parent_id", "parent", required=False)
    if parent is not None:
        if parent.id == party.id or party.id in _ancestors(db, parent.id):
            raise ValueError(f"{parent.name} is inside {party.name}'s own hierarchy - that would make a loop")
        account_detail(db, tenant_id, parent)
        detail.parent_party_id = parent.id
        if created:
            pp = db.query(PartyProfile).filter(PartyProfile.party_id == parent.id).first()
            if pp is not None:
                db.add(PartyProfile(party_id=party.id, segment=pp.segment, account_status=pp.account_status, price_list_id=pp.price_list_id,
                                    industry=pp.industry))
    elif _bool(e.get("detach")):
        detail.parent_party_id = None
    if e.get("account_class"):
        cls = str(e["account_class"]).strip().lower().replace(" ", "_").replace("-", "_")
        cls = {"bill": "billing", "billing_account": "billing", "service_account": "service", "aggregator": "billing_aggregator"}.get(cls, cls)
        if cls not in ACCOUNT_CLASSES:
            raise ValueError(f"Account class: {', '.join(ACCOUNT_CLASSES)}")
        detail.account_class = cls
    elif created:
        detail.account_class = "service"
    for f in ("account_type", "site", "synonyms", "region"):
        if e.get(f) not in (None, ""):
            setattr(detail, f, str(e[f]).strip()[:300])
    if e.get("good_standing") not in (None, ""):
        detail.good_standing = _bool(e["good_standing"])
        detail.block_reason = "" if detail.good_standing else str(e.get("reason") or "Blocked")[:200]
    if e.get("partner") not in (None, ""):
        detail.partner = _bool(e["partner"])
    db.flush()
    return {"party_id": party.id, "created": created, "account": account_out(db, tenant_id, party),
            "hierarchy": tree(db, tenant_id, root_of(db, party.id))}


def account_out(db: Session, tenant_id: int, party: Party) -> dict[str, Any]:
    d = account_detail(db, tenant_id, party, create=False)
    return {"party_id": party.id, "name": party.name, "account_class": d.account_class if d else "customer",
            "account_type": d.account_type if d else "", "parent_party_id": d.parent_party_id if d else None,
            "parent_name": party_name(db, d.parent_party_id) if d else "", "site": d.site if d else "",
            "synonyms": d.synonyms if d else "", "good_standing": d.good_standing if d else True,
            "block_reason": d.block_reason if d else "", "partner": d.partner if d else False}


def tree(db: Session, tenant_id: int, party_id: int, depth: int = 0) -> dict[str, Any]:
    party = db.get(Party, party_id)
    d = db.query(CmAccountDetail).filter(CmAccountDetail.party_id == party_id).first()
    from app.crm_core import comm_billing as cb

    node = {"party_id": party_id, "name": party.name if party else f"#{party_id}", "account_class": d.account_class if d else "customer",
            "site": d.site if d else "", "good_standing": d.good_standing if d else True,
            "balance": cb.balance(db, tenant_id, party_id), "services": len(live_orders(db, tenant_id, party_id)), "children": []}
    if depth < 8:
        node["children"] = [tree(db, tenant_id, c.party_id, depth + 1) for c in children_of(db, tenant_id, party_id)]
    node["rollup_balance"] = _r(node["balance"] + sum(c["rollup_balance"] for c in node["children"]))
    node["rollup_services"] = node["services"] + sum(c["rollup_services"] for c in node["children"])
    return node


def live_orders(db: Session, tenant_id: int, party_id: int) -> list[Any]:
    """Read-only billable service projections, including fulfilled installed assets."""
    from types import SimpleNamespace
    from app.crm_core import abo_service
    from app.models.fulfillment import AssetStatus
    rows = db.query(Order).filter(Order.tenant_id == tenant_id, Order.party_id == party_id, Order.status.in_(LIVE)).all()
    represented = {o.id for o in rows}
    for asset in abo_service.customer_assets(db, tenant_id, party_id):
        if asset.status == AssetStatus.decommissioned:
            continue
        data = json.loads(asset.instance_json or "{}")
        if not data.get("order_id") or data["order_id"] in represented:
            continue
        source = db.get(Order, data["order_id"])
        rows.append(SimpleNamespace(id=source.id, party_id=party_id, service_account_id=asset.service_account_id,
            status=OrderStatus.suspended if asset.status == AssetStatus.suspended else OrderStatus.active,
            monthly_price=abo_service.recurring_price(asset), created_at=asset.created_at,
            items=[SimpleNamespace(product_offering_id=data["product_offering_id"])], asset_id=asset.id,
            contract_months=source.contract_months))
    return rows


def explorer(db: Session, tenant_id: int, account_class: str = "") -> list[dict[str, Any]]:
    """Billing / service account explorer: accounts of a class (and their aggregators)."""
    q = db.query(CmAccountDetail).filter(CmAccountDetail.tenant_id == tenant_id)
    if account_class:
        classes = (account_class, f"{account_class}_aggregator") if account_class in ("billing", "service") else (account_class,)
        q = q.filter(CmAccountDetail.account_class.in_(classes))
    out = []
    for d in q.order_by(CmAccountDetail.id).all():
        p = db.get(Party, d.party_id)
        if p:
            out.append({**account_out(db, tenant_id, p), "root_party_id": root_of(db, p.id)})
    return out


# ------------------------------------------------------------------ contacts


def contact_out(db: Session, c: CmContact) -> dict[str, Any]:
    links = db.query(CmContactAccount).filter(CmContactAccount.contact_id == c.id).all()
    return {"id": c.id, "name": f"{c.first_name} {c.last_name}".strip(), "first_name": c.first_name, "last_name": c.last_name,
            "title": c.title, "email": c.email, "phone": c.phone, "preferred_channel": c.preferred_channel, "active": c.active,
            "accounts": [{"party_id": link.party_id, "name": party_name(db, link.party_id), "role": link.role, "primary": link.is_primary} for link in links]}


def add_contact(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = party_of(db, tenant_id, e)
    first, last = str(e.get("first_name") or "").strip(), str(e.get("last_name") or "").strip()
    if not last and e.get("contact_name"):
        parts = str(e["contact_name"]).strip().split()
        first, last = " ".join(parts[:-1]), parts[-1]
    if not last:
        raise ValueError("The contact's name?")
    email = str(e.get("email") or "").strip().lower()
    contact = None
    if email:
        contact = db.query(CmContact).filter(CmContact.tenant_id == tenant_id, CmContact.email == email).first()
    reused = contact is not None
    if contact is None:
        contact = CmContact(tenant_id=tenant_id, first_name=first, last_name=last, email=email, phone=str(e.get("phone") or ""),
                            title=str(e.get("title") or ""), preferred_channel=str(e.get("preferred_channel") or "email"))
        db.add(contact)
        db.flush()
    link = db.query(CmContactAccount).filter(CmContactAccount.contact_id == contact.id, CmContactAccount.party_id == party.id).first()
    if link is None:
        link = CmContactAccount(tenant_id=tenant_id, contact_id=contact.id, party_id=party.id)
        db.add(link)
    if e.get("role"):
        link.role = str(e["role"])[:50]
    is_primary = _bool(e.get("primary")) or not db.query(CmContactAccount).filter(CmContactAccount.party_id == party.id,
                                                                                      CmContactAccount.is_primary.is_(True)).count()
    if is_primary:
        for other in db.query(CmContactAccount).filter(CmContactAccount.party_id == party.id).all():
            other.is_primary = False
        link.is_primary = True
        account_detail(db, tenant_id, party).primary_contact_id = contact.id
    # each contact has one primary account
    if not db.query(CmContactAccount).filter(CmContactAccount.contact_id == contact.id, CmContactAccount.id != (link.id or 0)).count():
        link.is_primary = True
    db.flush()
    return {"party_id": party.id, "contact_id": contact.id, "existing_contact": reused, "contact": contact_out(db, contact)}


def contacts_of(db: Session, tenant_id: int, party_id: int) -> list[dict[str, Any]]:
    ids = [c.contact_id for c in db.query(CmContactAccount).filter(CmContactAccount.tenant_id == tenant_id, CmContactAccount.party_id == party_id)]
    return [contact_out(db, c) for c in db.query(CmContact).filter(CmContact.id.in_(ids or [0])).order_by(CmContact.id).all()]


def primary_contact(db: Session, party_id: int) -> CmContact | None:
    link = (db.query(CmContactAccount).filter(CmContactAccount.party_id == party_id)
            .order_by(CmContactAccount.is_primary.desc(), CmContactAccount.id).first())
    return db.get(CmContact, link.contact_id) if link else None


# ------------------------------------------------------------------ billing profiles


_METHODS = {"card": "credit_card", "credit card": "credit_card", "credit_card": "credit_card", "debit card": "credit_card",
            "auto debit": "auto_debit", "auto_debit": "auto_debit", "direct debit": "auto_debit", "debit": "auto_debit", "eft": "auto_debit",
            "bill me": "bill_me", "bill_me": "bill_me", "invoice": "bill_me"}


def billing_profiles(db: Session, tenant_id: int, party_id: int) -> list[CmBillingProfile]:
    return (db.query(CmBillingProfile).filter(CmBillingProfile.tenant_id == tenant_id, CmBillingProfile.party_id == party_id)
            .order_by(CmBillingProfile.is_primary.desc(), CmBillingProfile.id).all())


def primary_profile(db: Session, tenant_id: int, party_id: int, create: bool = False) -> CmBillingProfile | None:
    rows = billing_profiles(db, tenant_id, party_id)
    if rows:
        return rows[0]
    if not create:
        return None
    from app.integration import order_adapter
    account = order_adapter.get_or_create_billing_account(db, tenant_id, party_id)
    party = db.get(Party, party_id)
    p = CmBillingProfile(tenant_id=tenant_id, party_id=party_id, billing_account_id=account.id, name="Default billing profile", is_primary=True,
                         email=(party.email or "") if party else "", payment_terms_days=int(settings(db, tenant_id)["payment_terms_days"]))
    db.add(p)
    db.flush()
    return p


def profile_out(p: CmBillingProfile) -> dict[str, Any]:
    return {"id": p.id, "billing_account_id": p.billing_account_id, "name": p.name, "primary": p.is_primary, "frequency": p.frequency, "bill_day": p.bill_day, "payment_type": p.payment_type,
            "payment_method": p.payment_method, "card": f"{p.card_brand or 'card'} ending {p.card_last4}" if p.card_last4 else "",
            "bank_account": f"ending {p.bank_last4}" if p.bank_last4 else "", "card_blocked": p.card_blocked, "bill_media": p.bill_media,
            "email": p.email, "payment_terms_days": p.payment_terms_days, "auto_topup": p.auto_topup, "topup_amount": p.topup_amount,
            "topup_threshold": p.topup_threshold, "prepaid_balance": _r(p.prepaid_balance), "status": p.status, "sub_status": p.sub_status,
            "last_synced_at": p.last_synced_at}


def save_billing_profile(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = party_of(db, tenant_id, e)
    no_card_numbers(e.get("card_last4"), e.get("bank_last4"), e.get("name"), e.get("reference"))
    rows = billing_profiles(db, tenant_id, party.id)
    name = str(e.get("profile_name") or e.get("name") or "").strip()
    p = None
    if e.get("billing_profile_id"):
        p = _own(db, CmBillingProfile, e["billing_profile_id"], tenant_id, "Billing profile")
        if p.party_id != party.id:
            raise ValueError("Bill profile belongs to a different customer")
    elif name:
        p = next((r for r in rows if _key(r.name) == _key(name)), None)
    elif len(rows) == 1 and not _bool(e.get("new")):
        p = rows[0]
    created = p is None
    from app.integration import order_adapter
    billing = order_adapter.get_or_create_billing_account(db, tenant_id, party.id)
    if p is None:
        p = CmBillingProfile(tenant_id=tenant_id, party_id=party.id, name=name or f"{party.name} billing",
                             email=(party.email or ""), payment_terms_days=int(settings(db, tenant_id)["payment_terms_days"]))
        db.add(p)
    p.billing_account_id = billing.id
    if e.get("frequency"):
        freq = str(e["frequency"]).strip().lower()
        freq = {"month": "monthly", "quarter": "quarterly", "year": "annual", "yearly": "annual", "annually": "annual"}.get(freq, freq)
        if freq not in ("monthly", "quarterly", "annual"):
            raise ValueError("Billing frequency: monthly, quarterly or annual")
        p.frequency = freq
    others = [r for r in rows if r is not p]
    if others and any(o.frequency != p.frequency for o in others):
        raise ValueError(f"All of {party.name}'s billing profiles bill {others[0].frequency} - the billing system needs one frequency per account")
    if e.get("payment_type"):
        pt = str(e["payment_type"]).strip().lower().replace("-", "")
        if pt not in ("postpaid", "prepaid"):
            raise ValueError("Payment type: postpaid or prepaid")
        p.payment_type = pt
    if e.get("payment_method"):
        m = _METHODS.get(str(e["payment_method"]).strip().lower().replace("_", " "), _METHODS.get(str(e["payment_method"]).strip().lower()))
        if m is None:
            raise ValueError("Payment method: credit_card, auto_debit or bill_me")
        p.payment_method = m
    if e.get("card_last4") not in (None, ""):
        p.card_last4 = last4(e["card_last4"], "Card")
    if e.get("card_brand"):
        p.card_brand = str(e["card_brand"]).lower()[:20]
    if e.get("bank_last4") not in (None, ""):
        p.bank_last4 = last4(e["bank_last4"], "Bank account")
    if p.payment_method == "credit_card" and not p.card_last4:
        raise ValueError("A card payment method needs the card's last 4 digits")
    if p.payment_method == "auto_debit" and not p.bank_last4:
        raise ValueError("Automatic debit needs the bank account's last 4 digits")
    if e.get("bill_media"):
        media = str(e["bill_media"]).strip().lower()
        media = {"e-mail": "email", "electronic": "email", "post": "paper", "print": "paper", "online": "portal"}.get(media, media)
        if media not in ("paper", "email", "portal", "sms"):
            raise ValueError("Bill media: paper, email, portal or sms")
        p.bill_media = media
    if e.get("email"):
        p.email = str(e["email"]).strip()
    if p.bill_media == "email" and not p.email:
        raise ValueError("Bills by email need an email address")
    for f in ("bill_day", "payment_terms_days"):
        if e.get(f) not in (None, ""):
            setattr(p, f, max(1, min(int(float(e[f])), 28 if f == "bill_day" else 90)))
    for f in ("topup_amount", "topup_threshold"):
        if e.get(f) not in (None, ""):
            setattr(p, f, max(0.0, float(e[f])))
    for f in ("auto_topup", "sms_notify"):
        if e.get(f) not in (None, ""):
            setattr(p, f, _bool(e[f]))
    if p.auto_topup and p.topup_amount <= 0:
        raise ValueError("Auto top-up needs a top-up amount")
    if e.get("address"):
        p.address = str(e["address"])[:400]
    if _bool(e.get("primary")) or not others:
        for o in others:
            o.is_primary = False
        p.is_primary = True
    p.last_synced_at = utcnow()  # passed to the billing system
    db.flush()
    return {"party_id": party.id, "billing_profile_id": p.id, "created": created, "profile": profile_out(p)}


# ------------------------------------------------------------------ account profiles


_PROFILE_FIELDS: dict[str, tuple[str, ...]] = {
    "customer": ("annual_revenue", "employees", "industry", "competitors", "partners", "sales_notes"),
    "exemption": ("lifeline_eligible", "lifeline_received", "lifeline_effective", "lifeline_renewal", "lifeline_status", "tax_exempt",
                  "tax_exemption_number", "exempt_jurisdictions", "federal_id", "state_id", "directory_assistance_exempt", "status"),
    "financial": ("legal_entity", "corporate_number", "tax_profile_code", "bank_name", "bank_branch", "rating", "source", "last_update", "updated_by"),
    "fraud": ("daily_threshold", "international_threshold", "domestic_threshold", "max_call_minutes", "max_calls", "sensitive_threshold",
              "credit_threshold", "overflow_pct", "block_international"),
    "site": ("floors", "equipment_location", "servicing_company", "supply", "open_market", "delivery_instructions", "access_hours"),
    "statement": ("media", "copies", "frequency", "vendor", "contact", "address"),
    "loyalty": (),
}
_ALIASES = {"exempt": "tax_exempt", "tax_exemption": "tax_exempt", "certificate": "tax_exemption_number", "exemption_number": "tax_exemption_number",
            "lifeline": "lifeline_eligible", "credit_rating": "rating", "daily": "daily_threshold", "international": "international_threshold",
            "domestic": "domestic_threshold", "max_duration": "max_call_minutes", "max_number_of_calls": "max_calls", "calls_per_day": "max_calls",
            "sensitive_numbers": "sensitive_threshold", "credit": "credit_threshold", "overflow": "overflow_pct", "jurisdictions": "exempt_jurisdictions"}


def profile_row(db: Session, tenant_id: int, party_id: int, kind: str, create: bool = True) -> CmProfile | None:
    row = db.query(CmProfile).filter(CmProfile.tenant_id == tenant_id, CmProfile.party_id == party_id, CmProfile.kind == kind).first()
    if row is None and create:
        row = CmProfile(tenant_id=tenant_id, party_id=party_id, kind=kind, data_json="{}")
        db.add(row)
        db.flush()
    return row


def profile_data(db: Session, tenant_id: int, party_id: int, kind: str) -> dict[str, Any]:
    row = profile_row(db, tenant_id, party_id, kind, create=False)
    return _j(row.data_json, {}) if row else {}


def update_profile(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = party_of(db, tenant_id, e)
    kind = str(e.get("profile_kind") or "").strip().lower()
    if kind not in PROFILE_KINDS:
        raise ValueError(f"Which profile: {', '.join(PROFILE_KINDS)}")
    if kind == "loyalty":
        return {"party_id": party.id, "profile_kind": kind, "profile": loyalty(db, tenant_id, party.id, refresh=True)}
    values = kv(e.get("values"))
    for k in _PROFILE_FIELDS[kind]:
        if e.get(k) not in (None, ""):
            values[k] = str(e[k])
    values = {_ALIASES.get(k, k): v for k, v in values.items()}
    unknown = [k for k in values if k not in _PROFILE_FIELDS[kind]]
    if unknown:
        raise ValueError(f"A {kind} profile has: {', '.join(_PROFILE_FIELDS[kind])} (not {', '.join(unknown)})")
    no_card_numbers(*values.values())
    row = profile_row(db, tenant_id, party.id, kind)
    data = _j(row.data_json, {})
    for k, v in values.items():
        if k in ("tax_exempt", "lifeline_eligible", "open_market", "block_international", "directory_assistance_exempt"):
            data[k] = _bool(v)
        elif kind == "fraud" and k != "block_international":
            try:
                data[k] = float(re.sub(r"[^\d.]", "", v))
            except ValueError as exc:
                raise ValueError(f"{k} must be a number") from exc
        else:
            data[k] = v
    out: dict[str, Any] = {"party_id": party.id, "profile_kind": kind}
    if kind == "financial" and _bool(e.get("credit_check")):
        check = om.credit_check(db, tenant_id, party.id, float(e.get("amount") or 0), "party", None)
        data.update(rating={"approved": "A", "review": "B", "declined": "C"}[check.result], source="Internal credit policy",
                    last_update=utcnow().date().isoformat(), credit_result=check.result, credit_reason=check.reason)
        out["credit_check"] = om.credit_out(check)
    row.data_json = json.dumps(data)
    row.name = row.name or f"{kind.title()} profile"
    if kind == "fraud":
        from app.crm_core import comm_billing as cb

        out["alerts_cleared"] = cb.recheck_fraud_alerts(db, tenant_id, party.id)
    db.flush()
    out["profile"] = data
    return out


def fraud_thresholds(db: Session, tenant_id: int, party_id: int) -> dict[str, Any]:
    return {**settings(db, tenant_id)["fraud_defaults"], **profile_data(db, tenant_id, party_id, "fraud")}


def tax_exempt(db: Session, tenant_id: int, party_id: int) -> bool:
    return bool(profile_data(db, tenant_id, party_id, "exemption").get("tax_exempt"))


def loyalty(db: Session, tenant_id: int, party_id: int, refresh: bool = False) -> dict[str, Any]:
    """Churn risk, lifetime value, debt risk and up / cross-sell - the scores a
    predictive model would give, worked out from what the account shows."""
    from app.crm_core import comm_billing as cb

    row = profile_row(db, tenant_id, party_id, "loyalty")
    data = _j(row.data_json, {})
    if data and not refresh:
        return data
    orders = live_orders(db, tenant_id, party_id)
    mrr = sum(o.monthly_price for o in orders if o.status != OrderStatus.suspended)
    tickets = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.party_id == party_id).all()
    open_tickets = [t for t in tickets if t.status != "resolved"]
    surveys = (db.query(CmSurvey).join(TroubleTicket, TroubleTicket.id == CmSurvey.ticket_id)
               .filter(TroubleTicket.party_id == party_id).all())
    csat = sum(s.score for s in surveys) / len(surveys) if surveys else None
    overdue = cb.overdue_amount(db, tenant_id, party_id)
    invoices = cb.invoices_of(db, tenant_id, party_id)
    shock = 0.0
    if len(invoices) >= 2 and invoices[1].total > 0:
        shock = (invoices[0].total - invoices[1].total) / invoices[1].total * 100
    reasons: list[str] = []
    churn = 10.0
    if open_tickets:
        churn += min(30, 10 * len(open_tickets))
        reasons.append(f"{len(open_tickets)} open ticket(s)")
    if any(t.severity in ("critical", "high") for t in open_tickets):
        churn += 10
        reasons.append("a high-severity problem is open")
    if csat is not None and csat < 60:
        churn += 20
        reasons.append(f"satisfaction {csat:.0f}/100")
    if shock > 30:
        churn += 15
        reasons.append(f"last bill {shock:.0f}% higher")
    if overdue > 0:
        churn += 10
        reasons.append("overdue balance")
    if any(o.status == OrderStatus.suspended for o in orders):
        churn += 15
        reasons.append("a service is suspended")
    churn = min(95.0, churn)
    expected_months = max(3.0, 36 * (1 - churn / 100))
    debt = min(95.0, (overdue / max(mrr, 1.0)) * 25 + 10 * db.query(cb.CmCreditAlert).filter(
        cb.CmCreditAlert.party_id == party_id, cb.CmCreditAlert.status.in_(("open", "pending"))).count())
    owned = {_key(om._offering_name(db, i.product_offering_id)) for o in orders for i in o.items}
    recs = [{"product": r.recommended_product, "kind": r.kind, "message": r.message}
            for r in db.query(OmRecommendation).filter(OmRecommendation.tenant_id == tenant_id, OmRecommendation.active.is_(True))
            .order_by(OmRecommendation.score.desc()).all()
            if _key(r.product) in owned and _key(r.recommended_product) not in owned][:3]
    data = {"churn_risk": round(churn), "churn_band": "high" if churn >= 50 else "medium" if churn >= 25 else "low",
            "lifetime_value": _r(mrr * expected_months), "monthly_revenue": _r(mrr), "debt_risk": round(debt),
            "satisfaction": round(csat) if csat is not None else None, "upsell": recs, "reasons": reasons or ["nothing at risk"],
            "scored_at": utcnow().isoformat(timespec="seconds")}
    row.data_json = json.dumps(data)
    row.name = "Loyalty profile"
    db.flush()
    return data


# ------------------------------------------------------------------ addresses and premises


_SUFFIX = {"road": "RD", "street": "ST", "avenue": "AVE", "boulevard": "BLVD", "drive": "DR", "lane": "LN", "court": "CT",
           "place": "PL", "square": "SQ", "highway": "HWY", "terrace": "TER", "parkway": "PKWY", "circle": "CIR"}
_UNIT = re.compile(r"\b(apt|apartment|unit|suite|ste|flat|floor|fl)\.?\s*#?\s*([\w-]+)", re.I)


def parse_address(text: str) -> dict[str, str]:
    raw = re.sub(r"\s+", " ", str(text or "")).strip(" ,")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out = {"house_number": "", "street": "", "unit": "", "city": "", "region": "", "postal_code": "", "country": ""}
    if m := _UNIT.search(raw):
        unit = {"apartment": "APT", "apt": "APT", "unit": "UNIT", "suite": "STE", "ste": "STE", "flat": "FLAT", "floor": "FL",
                "fl": "FL"}[m.group(1).lower()]
        out["unit"] = f"{unit} {m.group(2).upper()}"
        parts = [p for p in parts if not _UNIT.fullmatch(p)]
        parts = [_UNIT.sub("", p).strip() for p in parts]
        parts = [p for p in parts if p]
    if parts and (m := re.match(r"^(\d+[A-Za-z]?)\s+(.+)$", parts[0])):
        out["house_number"], out["street"] = m.group(1), m.group(2)
        parts = parts[1:]
    elif parts:
        out["street"] = parts[0]
        parts = parts[1:]
    for p in list(parts):
        if m := re.search(r"\b(\d{4,6}(?:-\d{4})?)\b", p):
            out["postal_code"] = m.group(1)
            rest = p.replace(m.group(1), "").strip()
            if rest and not out["city"]:
                out["city"] = rest
            elif rest:
                out["region"] = rest
            parts.remove(p)
    if parts and not out["city"]:
        out["city"] = parts.pop(0)
    if parts:
        out["region"] = out["region"] or parts.pop(0)
    if parts:
        out["country"] = parts.pop(0)
    return out


def standard_form(a: dict[str, str]) -> str:
    words = a.get("street", "").split()
    if words and words[-1].lower() in _SUFFIX:
        words[-1] = _SUFFIX[words[-1].lower()]
    street = " ".join(words).upper()
    return " ".join(x for x in (a.get("house_number", ""), street, a.get("unit", "").upper(), a.get("city", "").upper(),
                                a.get("region", "").upper(), a.get("postal_code", "")) if x)


def validate_address(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = party_of(db, tenant_id, e, required=False)
    text = str(e.get("address") or "").strip()
    row = None
    if e.get("address_id"):
        row = _own(db, CmAddress, e["address_id"], tenant_id, "Address")
        text = text or row.raw
    if not text:
        raise ValueError("Which address?")
    parts = parse_address(text)
    issues = [label for key, label in (("house_number", "a house / building number"), ("street", "the street"), ("city", "the city"))
              if not parts[key]]
    std = standard_form(parts)
    if row is None:
        row = next((a for a in db.query(CmAddress).filter(CmAddress.tenant_id == tenant_id).all() if a.standard_form == std), None)
    revalidated = row is not None and row.validated
    if row is None:
        row = CmAddress(tenant_id=tenant_id, party_id=party.id if party else None)
        db.add(row)
    row.raw = text
    for k, v in parts.items():
        setattr(row, k, v)
    if party is not None:
        row.party_id = party.id
    if e.get("kind"):
        row.kind = str(e["kind"]).lower()[:20]
    if e.get("access_instruction"):
        row.access_instruction = str(e["access_instruction"])[:300]
    row.standard_form = std
    row.validated = not issues
    row.validated_at = utcnow() if not issues else None
    premise = None
    if not issues and _bool(e.get("make_premise")):
        premise = find_or_create_premise(db, tenant_id, text, parts, party.id if party else None)
        row.premise_id = premise.id
    if party is not None and not db.query(CmAddress).filter(CmAddress.party_id == party.id, CmAddress.is_primary.is_(True)).count():
        row.is_primary = True
    db.flush()
    return {"address_id": row.id, "validated": row.validated, "revalidated": revalidated, "standard_form": std, "parts": parts,
            "issues": [f"Missing {i}" for i in issues], "premise_id": premise.id if premise else row.premise_id,
            "party_id": party.id if party else None}


def find_or_create_premise(db: Session, tenant_id: int, address: str, parts: dict[str, str] | None = None,
                           party_id: int | None = None) -> CmPremise:
    std = standard_form(parts) if parts else standard_form(parse_address(address))
    premise = next((p for p in db.query(CmPremise).filter(CmPremise.tenant_id == tenant_id).all()
                    if standard_form(parse_address(p.address)) == std or _key(p.address) == _key(std)), None)
    if premise is None:
        parts = parts or parse_address(address)
        premise = CmPremise(tenant_id=tenant_id, address=address.strip(), city=parts.get("city", ""), region=parts.get("region", ""),
                            postal_code=parts.get("postal_code", ""), party_id=party_id)
        db.add(premise)
        db.flush()
    elif party_id and premise.party_id != party_id:
        premise.party_id = party_id  # a new occupant; the premises keeps its history
        premise.status = "active"
    return premise


def register_hookup(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = party_of(db, tenant_id, e, required=False)
    if e.get("premise_id"):
        premise = _own(db, CmPremise, e["premise_id"], tenant_id, "Premises")
        if party is not None:
            premise.party_id = party.id
    elif e.get("address"):
        premise = find_or_create_premise(db, tenant_id, str(e["address"]), None, party.id if party else None)
    else:
        raise ValueError("Which premises (or its address)?")
    for f in ("buildings", "floors", "read_sequence"):
        if e.get(f) not in (None, ""):
            setattr(premise, f, int(float(e[f])))
    for f in ("read_route", "bill_group", "premise_type"):
        if e.get(f):
            setattr(premise, f, str(e[f])[:50])
    point = None
    if e.get("service_point") or e.get("product"):
        ident = str(e.get("service_point") or "").strip() or f"SP-{premise.id}-{db.query(CmServicePoint).filter(CmServicePoint.premise_id == premise.id).count() + 1}"
        point = db.query(CmServicePoint).filter(CmServicePoint.premise_id == premise.id, CmServicePoint.identifier == ident,
                                                CmServicePoint.removed_at.is_(None)).first()
        if point is None:
            point = CmServicePoint(tenant_id=tenant_id, premise_id=premise.id, identifier=ident)
            db.add(point)
        point.product = str(e.get("product") or point.product or "")
        point.service_type = str(e.get("service_type") or point.service_type or "")
        point.location = str(e.get("location") or point.location or "")
        if e.get("asset_id"):
            point.asset_id = _own(db, Asset, e["asset_id"], tenant_id, "Asset").id
    if _bool(e.get("remove")) and point is not None:
        point.removed_at = utcnow()
    premise.verified_at = utcnow()
    db.flush()
    return {"premise_id": premise.id, "service_point_id": point.id if point else None, "premise": premise_view(db, tenant_id, premise)}


def premise_view(db: Session, tenant_id: int, premise: CmPremise) -> dict[str, Any]:
    from app.crm_core import comm_billing as cb

    points = db.query(CmServicePoint).filter(CmServicePoint.premise_id == premise.id).order_by(CmServicePoint.id).all()
    asset_ids = [p.asset_id for p in points if p.asset_id]
    ticket_rows = db.query(CmTicketDetail).filter(CmTicketDetail.premise_id == premise.id).all()
    tickets = []
    for d in ticket_rows:
        t = db.get(TroubleTicket, d.ticket_id)
        if t:
            tickets.append({"ticket_id": t.id, "number": d.ticket_number, "party": party_name(db, t.party_id), "status": t.status,
                            "description": t.description[:160], "created_at": t.created_at})
    return {"id": premise.id, "address": premise.address, "city": premise.city, "region": premise.region, "postal_code": premise.postal_code,
            "premise_type": premise.premise_type, "buildings": premise.buildings, "floors": premise.floors, "read_route": premise.read_route,
            "read_sequence": premise.read_sequence, "bill_group": premise.bill_group, "occupant": party_name(db, premise.party_id),
            "party_id": premise.party_id, "status": premise.status, "verified_at": premise.verified_at,
            "service_points": [{"id": p.id, "identifier": p.identifier, "product": p.product, "service_type": p.service_type,
                                "location": p.location, "asset_id": p.asset_id, "installed_at": p.installed_at, "removed_at": p.removed_at}
                               for p in points],
            "service_requests": tickets, "repeat_problem": len(tickets) >= 3,
            "infrastructure": [site_item_out(i) for i in db.query(CmSiteItem).filter(CmSiteItem.premise_id == premise.id).all()],
            "usage": cb.usage_history(db, tenant_id, asset_ids=asset_ids) if asset_ids else []}


def premises(db: Session, tenant_id: int, party_id: int | None = None, query: str = "") -> list[dict[str, Any]]:
    q = db.query(CmPremise).filter(CmPremise.tenant_id == tenant_id)
    if party_id:
        q = q.filter(CmPremise.party_id == party_id)
    rows = q.order_by(CmPremise.id.desc()).limit(200).all()
    if query:
        rows = [r for r in rows if _key(query) in _key(r.address)]
    return [{"id": r.id, "address": r.address, "occupant": party_name(db, r.party_id), "party_id": r.party_id, "status": r.status,
             "service_points": db.query(CmServicePoint).filter(CmServicePoint.premise_id == r.id, CmServicePoint.removed_at.is_(None)).count()}
            for r in rows]


def site_item_out(i: CmSiteItem) -> dict[str, Any]:
    return {"id": i.id, "kind": i.kind, "service_type": i.service_type, "name": i.name, "value": i.value, "quantity": i.quantity,
            "owner": i.owner, "planned": i.planned, "install_date": i.install_date, "party_id": i.party_id, "premise_id": i.premise_id}


def save_site_item(db: Session, tenant_id: int, data: dict[str, Any], item_id: int | None = None) -> CmSiteItem:
    item = _own(db, CmSiteItem, item_id, tenant_id, "Item") if item_id else CmSiteItem(tenant_id=tenant_id)
    if item_id is None:
        db.add(item)
    if data.get("party_id"):
        item.party_id = _own(db, Party, data["party_id"], tenant_id, "Customer").id
    if data.get("premise_id"):
        item.premise_id = _own(db, CmPremise, data["premise_id"], tenant_id, "Premises").id
    if not (item.party_id or item.premise_id):
        raise ValueError("Equipment belongs to an account or a premises")
    kind = str(data.get("kind") or item.kind or "equipment")
    if kind not in ("equipment", "other_equipment", "infrastructure"):
        raise ValueError("Kind: equipment, other_equipment or infrastructure")
    item.kind = kind
    for f in ("service_type", "name", "value", "owner"):
        if f in data and data[f] is not None:
            setattr(item, f, str(data[f])[:200])
    if not item.name:
        raise ValueError("Give the product or attribute")
    if data.get("quantity") not in (None, ""):
        item.quantity = float(data["quantity"])
    if "planned" in data:
        item.planned = _bool(data["planned"])
    if "install_date" in data:
        item.install_date = _date(data["install_date"])
    db.flush()
    return item


# ------------------------------------------------------------------ special rating lists


def rating_list_out(db: Session, lst: CmRatingList) -> dict[str, Any]:
    items = db.query(CmRatingItem).filter(CmRatingItem.list_id == lst.id).order_by(CmRatingItem.id).all()
    return {"id": lst.id, "name": lst.name, "product": lst.product, "max_items": lst.max_items, "primary": lst.is_primary,
            "asset_id": lst.asset_id, "last_sync_at": lst.last_sync_at, "active_items": sum(1 for i in items if not i.inactive),
            "items": [{"id": i.id, "value": i.value, "inactive": i.inactive, "start_date": i.start_date, "end_date": i.end_date,
                       "last_sync_at": i.last_sync_at} for i in items]}


def _in_flight(db: Session, tenant_id: int, party_id: int, product: str) -> str | None:
    if not product:
        return None
    for d in db.query(OmOrderDetail).join(Order, Order.id == OmOrderDetail.order_id).filter(
            Order.tenant_id == tenant_id, Order.party_id == party_id,
            OmOrderDetail.om_status.notin_(("fulfilled", "cancelled", "delivered"))).all():
        if any(_key(line.product_name) == _key(product) for line in db.query(OmLine).filter(
                OmLine.context_type == "order", OmLine.context_id == d.order_id, OmLine.action_code != "Delete").all()):
            return d.order_number
    return None


def manage_rating_list(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = party_of(db, tenant_id, e)
    lists = db.query(CmRatingList).filter(CmRatingList.tenant_id == tenant_id, CmRatingList.party_id == party.id).order_by(CmRatingList.id).all()
    name = str(e.get("list_name") or "").strip()
    lst = next((x for x in lists if _key(x.name) == _key(name)), None) if name else (lists[0] if len(lists) == 1 else None)
    created = False
    if lst is None:
        if not name and lists:
            raise ValueError(f"Which list: {', '.join(x.name for x in lists)}")
        lst = CmRatingList(tenant_id=tenant_id, party_id=party.id, name=name or "Friends & family", is_primary=not lists,
                           product=str(e.get("product") or "Friends & Family"), max_items=int(float(e.get("max_items") or 5)))
        db.add(lst)
        db.flush()
        created = True
    elif e.get("max_items") not in (None, ""):
        lst.max_items = max(1, int(float(e["max_items"])))
    if e.get("product") and not created:
        lst.product = str(e["product"])
    add = [re.sub(r"[^\d+]", "", n) for n in _numbers(e.get("add_numbers"))]
    remove = [re.sub(r"[^\d+]", "", n) for n in _numbers(e.get("remove_numbers"))]
    if (add or remove) and not created and (order := _in_flight(db, tenant_id, party.id, lst.product)):
        raise ValueError(f"Order {order} for {lst.product} is still in flight - the list can change once it completes")
    items = db.query(CmRatingItem).filter(CmRatingItem.list_id == lst.id).all()
    now = utcnow()
    for n in remove:
        for i in items:
            if i.value == n and not i.inactive:
                i.inactive, i.end_date = True, now
    active = [i for i in items if not i.inactive]
    for n in add:
        if len(n.lstrip("+")) < 5:
            raise ValueError(f"'{n}' is not a phone number")
        if any(i.value == n for i in active):
            continue
        if len(active) >= lst.max_items:
            raise ValueError(f"{lst.name} holds at most {lst.max_items} numbers ({lst.product})")
        old = next((i for i in items if i.value == n), None)
        if old is not None:
            old.inactive, old.end_date, old.start_date = False, None, now
            active.append(old)
        else:
            item = CmRatingItem(tenant_id=tenant_id, list_id=lst.id, value=n)
            db.add(item)
            active.append(item)
    if e.get("asset_id"):
        lst.asset_id = _own(db, Asset, e["asset_id"], tenant_id, "Asset").id
    if _bool(e.get("primary")):
        for x in lists:
            x.is_primary = x.id == lst.id
    db.flush()
    lst.last_sync_at = now  # synchronised with the billing system's rating lists
    for i in db.query(CmRatingItem).filter(CmRatingItem.list_id == lst.id, CmRatingItem.inactive.is_(False)).all():
        i.last_sync_at = now
    db.flush()
    return {"party_id": party.id, "list_id": lst.id, "created": created, "list": rating_list_out(db, lst)}


def _numbers(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[,;]|\band\b", str(v or "")) if x.strip()]


# ------------------------------------------------------------------ assets


def asset_transaction(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    asset = _own(db, Asset, e.get("asset_id"), tenant_id, "Asset")
    kind = str(e.get("transaction") or "install").strip().lower()
    kind = {"add": "install", "swap": "replace", "change": "replace", "uninstall": "remove", "link": "relate", "relation": "relate"}.get(kind, kind)
    if kind not in ("install", "replace", "remove", "upgrade", "move", "relate"):
        raise ValueError("Transaction: install, replace, remove, upgrade, move or relate")
    party_id = fs_service.party_of_asset(db, asset)
    component = str(e.get("component") or "").strip()
    note = str(e.get("description") or "").strip()
    out: dict[str, Any] = {"asset_id": asset.id, "transaction": kind}
    children = _components(db, tenant_id, asset.id)
    if kind == "relate":
        other = _own(db, Asset, e.get("related_asset_id"), tenant_id, "Related asset")
        relation = str(e.get("relation") or "backup").lower()
        if relation not in ("backup", "option", "component", "redundant"):
            raise ValueError("Relation: backup, option, component or redundant")
        if other.id == asset.id:
            raise ValueError("An asset can't be related to itself")
        db.add(CmAssetRelation(tenant_id=tenant_id, asset_id=asset.id, related_asset_id=other.id, relation=relation))
        note = note or f"{relation} - {fs_service.asset_label(db, other)}"
        out["related_asset_id"] = other.id
    elif kind == "install":
        if not component:
            raise ValueError("Which component is installed?")
        if party_id is None:
            raise ValueError("The asset has no customer")
        new = fs_service.register_asset(db, tenant_id, party_id=party_id, product_name=component, serial_number=str(e.get("serial_number") or ""),
                                        parent_asset_id=asset.id, service_account_id=asset.service_account_id)
        out["component_asset_id"] = new.id
        note = note or f"Installed {component}"
    elif kind in ("replace", "remove"):
        old = next((c for c in children if _key(fs_service.product_of(db, c)) == _key(component) or str(c.id) == component), None) if component else None
        if old is None:
            raise ValueError(f"No component '{component}' on this asset" if component else
                             f"Which component? ({', '.join(fs_service.product_of(db, c) for c in children) or 'none installed'})")
        old.status = AssetStatus.decommissioned
        out["removed_asset_id"] = old.id
        if kind == "replace":
            new = fs_service.register_asset(db, tenant_id, party_id=party_id, product_name=fs_service.product_of(db, old),
                                            serial_number=str(e.get("serial_number") or ""), parent_asset_id=asset.id,
                                            service_account_id=asset.service_account_id)
            out["component_asset_id"] = new.id
        note = note or f"{kind.title()}d {fs_service.product_of(db, old)}"
    elif kind == "move" and e.get("address"):
        fs_service.update_asset(db, tenant_id, asset.id, {"address": str(e["address"])})
        note = note or f"Moved to {e['address']}"
    db.add(CmAssetTransaction(tenant_id=tenant_id, asset_id=asset.id, component_asset_id=out.get("component_asset_id") or out.get("removed_asset_id"),
                              kind=kind, description=note or kind))
    db.flush()
    out["asset"] = asset_view(db, tenant_id, asset)
    return out


def _components(db: Session, tenant_id: int, asset_id: int) -> list[Asset]:
    ids = [d.asset_id for d in db.query(FsAssetDetail).filter(FsAssetDetail.tenant_id == tenant_id, FsAssetDetail.parent_asset_id == asset_id)]
    return db.query(Asset).filter(Asset.id.in_(ids or [0])).all()


def asset_view(db: Session, tenant_id: int, asset: Asset) -> dict[str, Any]:
    from app.crm_core import comm_billing as cb

    rel = db.query(CmAssetRelation).filter((CmAssetRelation.asset_id == asset.id) | (CmAssetRelation.related_asset_id == asset.id)).all()
    points = db.query(CmServicePoint).filter(CmServicePoint.asset_id == asset.id).all()
    return {
        "asset_id": asset.id, "label": fs_service.asset_label(db, asset), "status": asset.status.value if hasattr(asset.status, "value") else asset.status,
        "components": [{"asset_id": c.id, "product": fs_service.product_of(db, c), "status": c.status.value if hasattr(c.status, "value") else c.status,
                        "identifier": c.identifier} for c in _components(db, tenant_id, asset.id)],
        "relations": [{"asset_id": r.related_asset_id if r.asset_id == asset.id else r.asset_id, "relation": r.relation,
                       "label": fs_service.asset_label(db, db.get(Asset, r.related_asset_id if r.asset_id == asset.id else r.asset_id))} for r in rel],
        "transactions": [{"kind": t.kind, "description": t.description, "at": t.at} for t in db.query(CmAssetTransaction)
                         .filter(CmAssetTransaction.asset_id == asset.id).order_by(CmAssetTransaction.id.desc()).limit(30)],
        "change_requests": [change_request_out(c) for c in db.query(CmChangeRequest).filter(CmChangeRequest.asset_id == asset.id)
                            .order_by(CmChangeRequest.id.desc())],
        "service_points": [{"id": p.id, "identifier": p.identifier, "premise_id": p.premise_id, "service_type": p.service_type} for p in points],
        "usage": cb.usage_history(db, tenant_id, asset_ids=[asset.id]),
    }


def change_request_out(c: CmChangeRequest) -> dict[str, Any]:
    return {"id": c.id, "number": c.number, "asset_id": c.asset_id, "party_id": c.party_id, "type": c.cr_type, "area": c.area,
            "subarea": c.subarea, "priority": c.priority, "status": c.status, "owner": c.owner_name, "description": c.description,
            "created_at": c.created_at}


def raise_change_request(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    asset = _own(db, Asset, e["asset_id"], tenant_id, "Asset") if e.get("asset_id") else None
    party = party_of(db, tenant_id, e, required=False)
    party_id = party.id if party else (fs_service.party_of_asset(db, asset) if asset else None)
    if not (asset or party_id):
        raise ValueError("Raise it on an asset or for a customer")
    text = str(e.get("description") or "").strip()
    if not text:
        raise ValueError("What should change?")
    cr_type = str(e.get("cr_type") or "enhancement").lower()
    if cr_type not in ("enhancement", "defect", "configuration"):
        raise ValueError("Type: enhancement, defect or configuration")
    prio = str(e.get("priority") or "medium").lower()
    cr = CmChangeRequest(tenant_id=tenant_id, asset_id=asset.id if asset else None, party_id=party_id, cr_type=cr_type,
                         area=str(e.get("area") or (fs_service.product_of(db, asset) if asset else ""))[:100],
                         subarea=str(e.get("subarea") or "")[:100], priority=prio if prio in ("low", "medium", "high", "critical") else "medium",
                         owner_name=str(e.get("owner") or "")[:200], status="assigned" if e.get("owner") else "open", description=text)
    db.add(cr)
    db.flush()
    cr.number = f"CR-{cr.id:05d}"
    db.flush()
    return {"change_request_id": cr.id, "asset_id": cr.asset_id, "party_id": party_id, "change_request": change_request_out(cr)}


# ------------------------------------------------------------------ opportunities


def _opportunity(db: Session, tenant_id: int, opportunity_id: Any) -> Opportunity:
    return _own(db, Opportunity, opportunity_id, tenant_id, "Opportunity")


def link_opportunity(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    opp = _opportunity(db, tenant_id, e.get("opportunity_id"))
    party = party_of(db, tenant_id, e, "related_party_id", "related_account")
    kind = str(e.get("link_kind") or "related_site").strip().lower().replace(" ", "_")
    kind = {"site": "related_site", "related": "related_site", "partners": "partner"}.get(kind, kind)
    if kind not in ("related_site", "partner"):
        raise ValueError("Add it as a related_site or a partner")
    main = opp.lead.party_id if opp.lead else None
    if party.id == main:
        raise ValueError(f"{party.name} is already the opportunity's account")
    if kind == "partner":
        d = account_detail(db, tenant_id, party, create=False)
        if not (d and d.partner):
            raise ValueError(f"{party.name} is not a partner account - mark it as a partner first")
    link = db.query(CmOpportunityLink).filter(CmOpportunityLink.opportunity_id == opp.id, CmOpportunityLink.party_id == party.id,
                                              CmOpportunityLink.kind == kind).first()
    if link is None:
        link = CmOpportunityLink(tenant_id=tenant_id, opportunity_id=opp.id, party_id=party.id, kind=kind)
        db.add(link)
    if e.get("note"):
        link.note = str(e["note"])[:300]
    db.flush()
    return {"opportunity_id": opp.id, "party_id": party.id, "kind": kind, "links": opportunity_links(db, tenant_id, opp.id)}


def opportunity_links(db: Session, tenant_id: int, opportunity_id: int) -> list[dict[str, Any]]:
    return [{"id": x.id, "party_id": x.party_id, "name": party_name(db, x.party_id), "kind": x.kind, "note": x.note}
            for x in db.query(CmOpportunityLink).filter(CmOpportunityLink.tenant_id == tenant_id, CmOpportunityLink.opportunity_id == opportunity_id)]


def profile_opportunity(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    opp = _opportunity(db, tenant_id, e.get("opportunity_id"))
    cfg = settings(db, tenant_id)
    questions = cfg["qualification_questions"]
    row = db.query(CmOpportunityProfile).filter(CmOpportunityProfile.opportunity_id == opp.id).first()
    if row is None:
        row = CmOpportunityProfile(tenant_id=tenant_id, opportunity_id=opp.id)
        db.add(row)
    answers = _j(row.answers_json, {})
    given = kv(e.get("answers"))
    for q in questions:
        if e.get(q["key"]) not in (None, ""):
            given[q["key"]] = str(e[q["key"]])
    for k, v in given.items():
        q = next((q for q in questions if q["key"] == k or _key(q["key"]) == _key(k)), None)
        if q is None:
            raise ValueError(f"The qualification script asks about: {', '.join(q['key'] for q in questions)}")
        answers[q["key"]] = "yes" if _bool(v) else "no" if str(v).strip().lower() in ("no", "n", "false", "0") else str(v)
    score = sum(q["weight"] for q in questions if answers.get(q["key"]) == "yes")
    row.answers_json = json.dumps(answers)
    row.score = float(score)
    row.target_market = score >= cfg["target_market_score"]
    db.flush()
    unanswered = [q["question"] for q in questions if q["key"] not in answers]
    return {"opportunity_id": opp.id, "score": score, "target_market": row.target_market, "answers": answers, "unanswered": unanswered}


# ------------------------------------------------------------------ trouble tickets


def ticket_of(db: Session, tenant_id: int, ticket_id: Any) -> TroubleTicket:
    return _own(db, TroubleTicket, ticket_id, tenant_id, "Ticket")


def ticket_detail(db: Session, tenant_id: int, ticket: TroubleTicket, create: bool = True) -> CmTicketDetail | None:
    d = db.query(CmTicketDetail).filter(CmTicketDetail.ticket_id == ticket.id).first()
    if d is None and create:
        d = CmTicketDetail(tenant_id=tenant_id, ticket_id=ticket.id, ticket_number=f"TT-{ticket.id:06d}")
        db.add(d)
        db.flush()
    return d


def _closed(t: TroubleTicket) -> bool:
    return t.status in ("resolved", "closed")


def link_tickets(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """Hang customer tickets off one parent (network) ticket."""
    parent = ticket_of(db, tenant_id, e.get("parent_ticket_id"))
    pd = ticket_detail(db, tenant_id, parent)
    if _closed(parent):
        raise ValueError(f"Ticket {pd.ticket_number} is closed - reopen it first")
    if e.get("service_item"):
        pd.service_item = str(e["service_item"])[:100]
    pd.origin = "network" if pd.origin == "customer" and (e.get("service_item") or _bool(e.get("network"))) else pd.origin
    ids = [int(x) for x in re.findall(r"\d+", str(e.get("ticket_ids") or ""))]
    if not ids and pd.service_item:
        # every open ticket on the same service item (exchange, circuit, number)
        same = db.query(CmTicketDetail).filter(CmTicketDetail.tenant_id == tenant_id, CmTicketDetail.service_item == pd.service_item,
                                               CmTicketDetail.ticket_id != parent.id).all()
        ids = [d.ticket_id for d in same]
        key = _key(pd.service_item)
        for t in db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.status != "resolved",
                                                TroubleTicket.id != parent.id).all():
            if key and key in _key(t.description) and t.id not in ids:
                ids.append(t.id)
    if not ids:
        raise ValueError("Which tickets go under it? (ticket numbers, or the service item they share)")
    linked, skipped = [], []
    ancestors = set(_ticket_ancestors(db, parent.id))
    for tid in ids:
        child = ticket_of(db, tenant_id, tid)
        if child.id == parent.id or child.id in ancestors:
            skipped.append({"ticket_id": tid, "why": "would make a loop"})
            continue
        if _closed(child):
            skipped.append({"ticket_id": tid, "why": "closed"})
            continue
        cd = ticket_detail(db, tenant_id, child)
        cd.parent_ticket_id = parent.id
        if pd.service_item and not cd.service_item:
            cd.service_item = pd.service_item
        linked.append(tid)
    db.flush()
    return {"ticket_id": parent.id, "parent_ticket_id": parent.id, "linked": linked, "skipped": skipped,
            "children": len(db.query(CmTicketDetail).filter(CmTicketDetail.parent_ticket_id == parent.id).all())}


def _ticket_ancestors(db: Session, ticket_id: int) -> list[int]:
    seen: list[int] = []
    cur: int | None = ticket_id
    while cur and cur not in seen and len(seen) < 30:
        seen.append(cur)
        d = db.query(CmTicketDetail).filter(CmTicketDetail.ticket_id == cur).first()
        cur = d.parent_ticket_id if d else None
    return seen


def resolve_ticket(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    ticket = ticket_of(db, tenant_id, e.get("ticket_id"))
    d = ticket_detail(db, tenant_id, ticket)
    action = str(e.get("ticket_action") or "resolve").lower()
    if action == "close":
        action = "resolve"
    if action not in ("resolve", "reopen"):
        raise ValueError("Resolve (close) or reopen the ticket")
    out: dict[str, Any] = {"ticket_id": ticket.id, "number": d.ticket_number}
    if action == "reopen":
        if not _closed(ticket):
            raise ValueError(f"Ticket {d.ticket_number} is still open")
        ticket.status, d.sub_status, d.closed_at = "open", "reopened", None
        d.reopened_count += 1
        out["status"] = "open"
        db.flush()
        return out
    if _closed(ticket):
        raise ValueError(f"Ticket {d.ticket_number} is already closed")
    resolution = str(e.get("resolution") or "").strip()
    solution = None
    if e.get("solution"):
        solution = find_solution(db, tenant_id, e["solution"])
    if solution is None and not resolution:
        raise ValueError("How was it resolved? (a resolution or a solution)")
    if solution is not None:
        solution.uses += 1
        d.solution_id = solution.id
        resolution = resolution or solution.resolution
    elif _bool(e.get("save_solution")):
        solution = CmSolution(tenant_id=tenant_id, title=ticket.description[:120] or ticket.category, problem=ticket.description,
                              resolution=resolution, product_line=ticket.product_line, keywords=ticket.category.replace("_", " "), uses=1)
        db.add(solution)
        db.flush()
        d.solution_id = solution.id
    ticket.status, d.sub_status, d.resolution, d.closed_at = "resolved", "resolved", resolution, utcnow()
    notify = _bool(e.get("notify"), True)
    if notify:
        d.customer_notified_at = utcnow()
    # children: verify each customer's problem is fixed before closing it
    children = db.query(CmTicketDetail).filter(CmTicketDetail.parent_ticket_id == ticket.id).all()
    close_children = _bool(e.get("close_children"))
    done = []
    for cd in children:
        child = db.get(TroubleTicket, cd.ticket_id)
        if child is None or _closed(child):
            continue
        if close_children:
            child.status, cd.sub_status, cd.closed_at = "resolved", "resolved", utcnow()
            cd.resolution = f"Resolved with parent {d.ticket_number}: {resolution}"
        else:
            cd.sub_status = "pending_verification"
        cd.customer_notified_at = utcnow() if notify else cd.customer_notified_at
        done.append(child.id)
    # a fallout ticket: release the order it held
    if d.origin == "fallout" and d.order_id:
        order = db.get(Order, d.order_id)
        if order is not None:
            od = om.order_detail(db, tenant_id, order)
            if od.hold and od.hold_reason.lower().startswith("fallout"):
                om.hold(db, tenant_id, order, "release", "")
                out["order_released"] = od.order_number
    db.flush()
    out.update(status="resolved", resolution=resolution, solution_id=d.solution_id, children=done,
               children_closed=close_children, customer_notified=notify)
    return out


def log_fallout(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """An order failed in fulfilment: a trouble ticket carries the failure until it is fixed."""
    order = om.order_of(db, tenant_id, e.get("order_id"))
    od = om.order_detail(db, tenant_id, order)
    code = str(e.get("error_code") or "").strip()
    system = str(e.get("system_of_failure") or "provisioning").strip()
    text = str(e.get("description") or f"Order {od.order_number} failed in {system}" + (f" ({code})" if code else ""))
    ticket = ticket_adapter.create_ticket(db, tenant_id=tenant_id, party_id=order.party_id, category="order_fallout", severity="high",
                                          product_line="orders", sla_class="standard", priority=ticket_service.assign_priority("standard", "high"),
                                          description=text)
    d = ticket_detail(db, tenant_id, ticket)
    d.origin, d.order_id, d.error_code, d.system_of_failure = "fallout", order.id, code, system
    d.failure_code = str(e.get("failure_code") or "")[:60]
    d.global_error_code = _global_error(code)
    d.group_name = "Order fallout"
    if e.get("line"):
        try:
            d.order_line_id = om.find_line(db, tenant_id, "order", order, e["line"]).id
        except ValueError:
            pass
    held = False
    if not od.hold and od.om_status not in ("cancelled", "fulfilled"):
        try:
            om.hold(db, tenant_id, order, "hold", f"Fallout: {text}"[:200])
            held = True
        except ValueError:
            held = False
    db.flush()
    return {"ticket_id": ticket.id, "order_id": order.id, "number": d.ticket_number, "global_error_code": d.global_error_code,
            "order_held": held, "recovery": _recovery(d.global_error_code)}


_GLOBAL_ERRORS = [("timeout", "SYS-TIMEOUT"), ("address", "DATA-ADDRESS"), ("credit", "FIN-CREDIT"), ("inventory", "RES-INVENTORY"),
                  ("stock", "RES-INVENTORY"), ("port", "RES-PORT"), ("number", "RES-NUMBER"), ("config", "DATA-CONFIG")]


def _global_error(code: str) -> str:
    c = code.lower()
    return next((g for k, g in _GLOBAL_ERRORS if k in c), "GEN-UNKNOWN" if code else "")


def _recovery(global_code: str) -> str:
    return {"SYS-TIMEOUT": "Resubmit the order to the fulfilment system", "DATA-ADDRESS": "Validate the service address, then resubmit",
            "FIN-CREDIT": "Run a credit check and record a payment, then resubmit", "RES-INVENTORY": "Check availability and ship from another location",
            "RES-PORT": "Reserve another network port, then resubmit", "RES-NUMBER": "Assign another number, then resubmit",
            "DATA-CONFIG": "Correct the configuration (a supplemental order), then resubmit"}.get(global_code, "Investigate, fix and resubmit the order")


def find_solution(db: Session, tenant_id: int, ref: Any) -> CmSolution | None:
    text = str(ref or "").strip()
    if text.isdigit():
        row = db.get(CmSolution, int(text))
        return row if row and row.tenant_id == tenant_id else None
    hits = search_solutions(db, tenant_id, text)
    return db.get(CmSolution, hits[0]["id"]) if hits and hits[0]["score"] >= 2 else None


def search_solutions(db: Session, tenant_id: int, query: str, limit: int = 5) -> list[dict[str, Any]]:
    words = {w for w in re.findall(r"[a-z0-9]{3,}", query.lower()) if w not in {"the", "and", "for", "with", "not", "customer", "ticket"}}
    out = []
    for s in db.query(CmSolution).filter(CmSolution.tenant_id == tenant_id, CmSolution.published.is_(True)).all():
        hay = f"{s.title} {s.problem} {s.keywords} {s.product_line}".lower()
        score = sum(2 if w in s.title.lower() else 1 for w in words if w in hay)
        if score:
            out.append({"id": s.id, "title": s.title, "resolution": s.resolution, "uses": s.uses, "score": score, "kind": "solution"})
    out.sort(key=lambda x: (-x["score"], -x["uses"]))
    return out[:limit]


def solutions_for(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    query = str(e.get("query") or "")
    ticket = None
    if e.get("ticket_id"):
        ticket = ticket_of(db, tenant_id, e["ticket_id"])
        query = f"{query} {ticket.description} {ticket.category.replace('_', ' ')}"
    if not query.strip():
        raise ValueError("Describe the problem (or pick the ticket)")
    hits = search_solutions(db, tenant_id, query)
    # how earlier, similar tickets were resolved
    words = {w for w in re.findall(r"[a-z0-9]{4,}", query.lower())}
    similar = []
    for d in db.query(CmTicketDetail).filter(CmTicketDetail.tenant_id == tenant_id, CmTicketDetail.resolution != "").all():
        t = db.get(TroubleTicket, d.ticket_id)
        if t is None or (ticket is not None and t.id == ticket.id):
            continue
        score = sum(1 for w in words if w in t.description.lower())
        if score >= 2:
            similar.append({"ticket_id": t.id, "number": d.ticket_number, "description": t.description[:140], "resolution": d.resolution,
                            "score": score, "kind": "resolved_ticket"})
    similar.sort(key=lambda x: -x["score"])
    return {"ticket_id": ticket.id if ticket else None, "solutions": hits, "similar_tickets": similar[:5],
            "found": bool(hits or similar)}


def record_survey(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    ticket = ticket_of(db, tenant_id, e.get("ticket_id"))
    ratings = {}
    for f in ("overall", "timeliness", "professionalism"):
        v = e.get(f)
        if v in (None, ""):
            continue
        n = int(float(v))
        if not 1 <= n <= 5:
            raise ValueError(f"{f.title()} is rated 1 to 5")
        ratings[f] = n
    if "overall" not in ratings:
        raise ValueError("The overall rating (1-5)?")
    rec = e.get("would_recommend")
    rec_n = int(float(rec)) if rec not in (None, "") else None
    if rec_n is not None and not 0 <= rec_n <= 10:
        raise ValueError("Would recommend is 0 to 10")
    resolved = _bool(e.get("resolved"), True)
    parts = [r * 20 for r in ratings.values()] + ([rec_n * 10] if rec_n is not None else [])
    score = sum(parts) / len(parts) - (0 if resolved else 20)
    s = CmSurvey(tenant_id=tenant_id, ticket_id=ticket.id, channel=str(e.get("channel") or "phone"), resolved=resolved,
                 would_recommend=rec_n, comments=str(e.get("comments") or ""), score=round(max(0.0, score), 1), **ratings)
    d = ticket_detail(db, tenant_id, ticket, create=False)
    s.contact_id = d.contact_id if d else None
    db.add(s)
    db.flush()
    follow_up = s.score < 50 or not resolved
    return {"ticket_id": ticket.id, "survey_id": s.id, "score": s.score, "follow_up": follow_up,
            "next": "Reopen the ticket - the customer says it is not fixed" if not resolved else ""}


def ticket_view(db: Session, tenant_id: int, ticket: TroubleTicket) -> dict[str, Any]:
    d = ticket_detail(db, tenant_id, ticket)
    children = []
    for cd in db.query(CmTicketDetail).filter(CmTicketDetail.parent_ticket_id == ticket.id).all():
        c = db.get(TroubleTicket, cd.ticket_id)
        if c:
            children.append({"ticket_id": c.id, "number": cd.ticket_number, "party": party_name(db, c.party_id), "status": c.status,
                             "sub_status": cd.sub_status})
    contact = db.get(CmContact, d.contact_id) if d.contact_id else None
    sol = db.get(CmSolution, d.solution_id) if d.solution_id else None
    surveys = db.query(CmSurvey).filter(CmSurvey.ticket_id == ticket.id).order_by(CmSurvey.id.desc()).all()
    return {"ticket_id": ticket.id, "number": d.ticket_number, "origin": d.origin, "parent_ticket_id": d.parent_ticket_id,
            "parent_number": ticket_detail(db, tenant_id, db.get(TroubleTicket, d.parent_ticket_id)).ticket_number
            if d.parent_ticket_id and db.get(TroubleTicket, d.parent_ticket_id) else None,
            "group": d.group_name, "owner": d.owner_name, "ref_number": d.ref_number, "service_item": d.service_item,
            "contact": contact_out(db, contact) if contact else None, "premise_id": d.premise_id, "asset_id": d.asset_id,
            "sub_status": d.sub_status, "resolution": d.resolution, "closed_at": d.closed_at, "reopened": d.reopened_count,
            "customer_notified_at": d.customer_notified_at, "children": children,
            "solution": {"id": sol.id, "title": sol.title} if sol else None,
            "fallout": {"order_id": d.order_id, "order_number": om.order_detail(db, tenant_id, db.get(Order, d.order_id)).order_number
                        if d.order_id and db.get(Order, d.order_id) else None, "error_code": d.error_code,
                        "global_error_code": d.global_error_code, "failure_code": d.failure_code, "system_of_failure": d.system_of_failure,
                        "recovery": _recovery(d.global_error_code)} if d.origin == "fallout" else None,
            "surveys": [{"score": s.score, "overall": s.overall, "resolved": s.resolved, "comments": s.comments, "at": s.created_at} for s in surveys]}


def update_ticket(db: Session, tenant_id: int, ticket_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Assignment and associations a CSR sets on a ticket (group, owner, contact, premises, asset)."""
    ticket = ticket_of(db, tenant_id, ticket_id)
    d = ticket_detail(db, tenant_id, ticket)
    if _closed(ticket) and any(k in data for k in ("group", "owner", "contact_id", "premise_id", "asset_id")):
        raise ValueError(f"Ticket {d.ticket_number} is closed - reopen it to change it")
    for f, col in (("group", "group_name"), ("owner", "owner_name"), ("ref_number", "ref_number"), ("service_item", "service_item")):
        if f in data:
            setattr(d, col, str(data[f] or "")[:200])
    if "contact_id" in data:
        d.contact_id = _own(db, CmContact, data["contact_id"], tenant_id, "Contact").id if data["contact_id"] else None
    if "premise_id" in data:
        d.premise_id = _own(db, CmPremise, data["premise_id"], tenant_id, "Premises").id if data["premise_id"] else None
    if "asset_id" in data:
        d.asset_id = _own(db, Asset, data["asset_id"], tenant_id, "Asset").id if data["asset_id"] else None
    db.flush()
    return ticket_view(db, tenant_id, ticket)


def ticket_analysis(db: Session, tenant_id: int, days: int = 30, parties: set[int] | None = None) -> dict[str, Any]:
    """parties: only these customers' tickets (the caller's data scope); None = all."""
    since = utcnow() - dt.timedelta(days=days)
    tickets = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.created_at >= since).all()
    if parties is not None:
        tickets = [t for t in tickets if t.party_id in parties]
    details = {d.ticket_id: d for d in db.query(CmTicketDetail).filter(CmTicketDetail.ticket_id.in_([t.id for t in tickets] or [0]))}
    by: dict[str, dict[str, int]] = {"status": {}, "severity": {}, "category": {}, "origin": {}, "product_line": {}}
    hours = []
    for t in tickets:
        d = details.get(t.id)
        for k, v in (("status", t.status), ("severity", t.severity), ("category", t.category), ("product_line", t.product_line),
                     ("origin", d.origin if d else "customer")):
            by[k][v] = by[k].get(v, 0) + 1
        if d and d.closed_at:
            hours.append((d.closed_at - t.created_at).total_seconds() / 3600)
    today = utcnow().date()
    surveys = db.query(CmSurvey).filter(CmSurvey.tenant_id == tenant_id, CmSurvey.created_at >= since).all()
    if parties is not None:
        seen = {tid for (tid,) in db.query(TroubleTicket.id).filter(TroubleTicket.tenant_id == tenant_id,
                                                                      TroubleTicket.party_id.in_(parties or {-1}))}
        surveys = [s for s in surveys if s.ticket_id in seen]
    return {"days": days, "total": len(tickets), "today": sum(1 for t in tickets if t.created_at.date() == today),
            "open": sum(1 for t in tickets if t.status != "resolved"), "by": by,
            "mean_hours_to_resolve": round(sum(hours) / len(hours), 1) if hours else None,
            "satisfaction": round(sum(s.score for s in surveys) / len(surveys), 1) if surveys else None,
            "network_parents": sum(1 for d in details.values() if d.origin == "network"),
            "fallout_open": sum(1 for t in tickets if details.get(t.id) and details[t.id].origin == "fallout" and t.status != "resolved")}


# ------------------------------------------------------------------ agreements


def agreement_of(db: Session, tenant_id: int, ref: Any) -> OmAgreement:
    text = str(ref or "").strip()
    if re.match(r"(?i)AGR?-\d+$", text):
        row = db.query(OmAgreement).filter(OmAgreement.tenant_id == tenant_id, OmAgreement.agreement_number == text.upper()).first()
        if row is None:
            raise ValueError(f"Agreement {text} not found")
        return row
    return _own(db, OmAgreement, text, tenant_id, "Agreement")


def agreement_detail(db: Session, tenant_id: int, a: OmAgreement) -> CmAgreementDetail:
    d = db.query(CmAgreementDetail).filter(CmAgreementDetail.agreement_id == a.id).first()
    if d is None:
        d = CmAgreementDetail(tenant_id=tenant_id, agreement_id=a.id, base_name=a.name,
                              order_ids_json=json.dumps([a.order_id] if a.order_id else []))
        db.add(d)
        db.flush()
    return d


def _agreement_snapshot(a: OmAgreement, d: CmAgreementDetail) -> dict[str, Any]:
    return {"name": a.name, "status": a.status, "start_date": a.start_date.isoformat(), "end_date": a.end_date.isoformat(),
            "term_months": a.term_months, "payment_terms": a.payment_terms, "monthly_value": a.monthly_value, "one_time_value": a.one_time_value,
            "response_hours": a.response_hours, "coverage": a.coverage, "auto_renew": a.auto_renew, "terms": _j(d.terms_json, []),
            "orders": _j(d.order_ids_json, []), "accounts": _j(d.account_ids_json, []), "revision": d.revision}


def revise_agreement(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    a = agreement_of(db, tenant_id, e.get("agreement_id"))
    d = agreement_detail(db, tenant_id, a)
    if e.get("sub_agreement"):
        return sub_agreement(db, tenant_id, a, str(e["sub_agreement"]), e)
    changes_text = str(e.get("changes") or "").strip()
    if not changes_text:
        raise ValueError("What changes in the agreement?")
    before = _agreement_snapshot(a, d)
    changes: list[dict[str, Any]] = []
    terms = _j(d.terms_json, [])
    for part in [p.strip() for p in re.split(r";|\n|,\s*(?=(?:add|set|change|extend|remove|approve|cover|link|attach)\b)", changes_text, flags=re.I) if p.strip()]:
        low = part.lower()
        if m := re.match(r"(?:add|set)\s+(?:a\s+)?term\s+(.+?)\s*[:=]\s*(.+)$", part, re.I):
            name, value = m.group(1).strip(), m.group(2).strip()
            terms = [t for t in terms if _key(t.get("term")) != _key(name)] + [{"term": name, "value": value}]
            changes.append({"field": f"term {name}", "new": value})
        elif m := re.match(r"remove\s+(?:the\s+)?term\s+(.+)$", part, re.I):
            terms = [t for t in terms if _key(t.get("term")) != _key(m.group(1))]
            changes.append({"field": f"term {m.group(1).strip()}", "new": "(removed)"})
        elif m := re.search(r"\bend(?:\s+date)?\b\D*(\d{4}-\d{2}-\d{2})", part, re.I):
            a.end_date = dt.date.fromisoformat(m.group(1))
            changes.append({"field": "end_date", "new": m.group(1)})
        elif m := re.search(r"\b(?:term|extend\w*)\b[^\d]*(\d+)\s*months?", part, re.I):
            a.term_months = int(m.group(1))
            a.end_date = om._date(a.start_date.isoformat()) + dt.timedelta(days=round(a.term_months * 30.44))
            changes.append({"field": "term_months", "new": a.term_months})
        elif m := re.search(r"\bmonthly(?:\s+value|\s+charge|\s+fee)?\b\D*([\d,]+(?:\.\d+)?)", part, re.I):
            a.monthly_value = float(m.group(1).replace(",", ""))
            changes.append({"field": "monthly_value", "new": a.monthly_value})
        elif m := re.search(r"\bresponse\b\D*(\d+(?:\.\d+)?)\s*(?:h|hours?)", part, re.I):
            a.response_hours = float(m.group(1))
            if a.entitlement_id and (ent := db.get(FsEntitlement, a.entitlement_id)):
                ent.response_hours = a.response_hours
            changes.append({"field": "response_hours", "new": a.response_hours})
        elif m := re.search(r"\bpayment\s+terms?\b\s*(?:to\s+)?(.+)$", part, re.I):
            a.payment_terms = m.group(1).strip().title()[:50]
            changes.append({"field": "payment_terms", "new": a.payment_terms})
        elif m := re.search(r"\b(?:add|link|attach|cover)\s+order\s*#?\s*(\d+)", part, re.I):
            order = om.order_of(db, tenant_id, m.group(1))
            ids = _j(d.order_ids_json, [])
            if order.id not in ids:
                ids.append(order.id)
            d.order_ids_json = json.dumps(ids)
            changes.append({"field": "orders", "new": order.id})
        elif m := re.search(r"\b(?:add|cover)\s+(?:account|customer)\s+(.+)$", part, re.I):
            other = party_of(db, tenant_id, {"customer": m.group(1).strip()})
            ids = _j(d.account_ids_json, [])
            if other.id not in ids and other.id != a.party_id:
                ids.append(other.id)
            d.account_ids_json = json.dumps(ids)
            changes.append({"field": "accounts", "new": other.name})
        elif "24x7" in low.replace(" ", "") or "24/7" in low:
            a.coverage = "24x7"
            changes.append({"field": "coverage", "new": "24x7"})
        elif re.search(r"\bno\s+auto[\s-]?renew|\bdo\s+not\s+renew", low):
            a.auto_renew = False
            changes.append({"field": "auto_renew", "new": False})
        elif re.search(r"\bauto[\s-]?renew", low):
            a.auto_renew = True
            changes.append({"field": "auto_renew", "new": True})
        elif m := re.search(r"\bapprover\b\s*(?:is\s+|to\s+)?(.+)$", part, re.I):
            d.approver = m.group(1).strip()[:200]
            changes.append({"field": "approver", "new": d.approver})
        else:
            raise ValueError(f"Not understood: '{part}'. Try: add term Early exit: 3 months' fees; end date 2027-12-31; "
                             "term 24 months; monthly value 5000; response 4 hours; payment terms Net 45; add order 7; add account Acme York")
    d.terms_json = json.dumps(terms)
    d.revision += 1
    base = d.base_name or re.sub(r"\s*\(\.\d+\)$", "", a.name)
    d.base_name = base
    a.name = f"{base} (.{d.revision})"[:200]
    d.document, d.document_generated_at, d.approved_at, d.signed_at = "", None, None, None  # a new revision needs a new document
    db.add(CmAgreementRevision(tenant_id=tenant_id, agreement_id=a.id, revision=d.revision, reason=str(e.get("reason") or "")[:300],
                               snapshot_json=json.dumps(before), changes_json=json.dumps(changes, default=str)))
    db.flush()
    return {"agreement_id": a.id, "revision": d.revision, "name": a.name, "changes": changes, "agreement": agreement_view(db, tenant_id, a)}


def sub_agreement(db: Session, tenant_id: int, parent: OmAgreement, name: str, e: dict[str, Any]) -> dict[str, Any]:
    child = OmAgreement(tenant_id=tenant_id, party_id=parent.party_id, name=name.strip()[:200], status="draft", start_date=parent.start_date,
                        end_date=parent.end_date, term_months=parent.term_months, payment_terms=parent.payment_terms,
                        response_hours=parent.response_hours, coverage=parent.coverage, auto_renew=parent.auto_renew, lines_json="[]")
    db.add(child)
    db.flush()
    child.agreement_number = f"AG-{child.id:06d}"
    cd = agreement_detail(db, tenant_id, child)
    cd.parent_agreement_id = parent.id
    cd.agreement_type = str(e.get("agreement_type") or "sla")
    db.flush()
    return {"agreement_id": child.id, "parent_agreement_id": parent.id, "name": child.name, "agreement_number": child.agreement_number,
            "agreement": agreement_view(db, tenant_id, child)}


def generate_document(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    from app.models.tenant import Tenant

    a = agreement_of(db, tenant_id, e.get("agreement_id"))
    d = agreement_detail(db, tenant_id, a)
    tenant = db.get(Tenant, tenant_id)
    lines = _j(a.lines_json, [])
    terms = _j(d.terms_json, [])
    values = {
        "agreement_number": a.agreement_number, "revision": d.revision, "provider": getattr(tenant, "name", "the provider"),
        "customer": party_name(db, a.party_id), "start": a.start_date.isoformat(), "end": a.end_date.isoformat(), "term_months": a.term_months,
        "lines": ", ".join(f"{x.get('quantity', 1):g} x {x.get('product')}" if isinstance(x.get("quantity", 1), (int, float)) else str(x.get("product"))
                           for x in lines) or a.name,
        "monthly_value": f"{a.monthly_value:,.2f}", "one_time_value": f"{a.one_time_value:,.2f}", "payment_terms": a.payment_terms,
        "coverage": "Round-the-clock (24x7)" if a.coverage == "24x7" else "Business-hours", "response_hours": f"{a.response_hours:g}" if a.response_hours else "-",
        "terms": "; ".join(f"{t['term']}: {t['value']}" for t in terms) or "None.",
        "renewal": "Renews automatically for the same term unless either party gives notice." if a.auto_renew else "Does not renew automatically.",
    }
    sections = settings(db, tenant_id)["agreement_sections"]
    text = "\n\n".join(str(s).format_map(_Safe(values)) for s in sections)
    d.document, d.document_generated_at = text, utcnow()
    if _bool(e.get("signed")):
        d.signed_at = utcnow()
        a.status = "active"
    db.flush()
    return {"agreement_id": a.id, "document": text, "generated_at": d.document_generated_at, "signed": bool(d.signed_at)}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def agreement_view(db: Session, tenant_id: int, a: OmAgreement) -> dict[str, Any]:
    d = agreement_detail(db, tenant_id, a)
    subs = [x for x in db.query(CmAgreementDetail).filter(CmAgreementDetail.parent_agreement_id == a.id).all()]
    ent = db.get(FsEntitlement, a.entitlement_id) if a.entitlement_id else None
    return {"id": a.id, "agreement_number": a.agreement_number, "name": a.name, "party_id": a.party_id, "customer": party_name(db, a.party_id),
            "status": a.status, "type": d.agreement_type, "revision": d.revision, "valid": d.valid, "start_date": a.start_date, "end_date": a.end_date,
            "term_months": a.term_months, "monthly_value": a.monthly_value, "one_time_value": a.one_time_value, "payment_terms": a.payment_terms,
            "coverage": a.coverage, "response_hours": a.response_hours, "auto_renew": a.auto_renew, "approver": d.approver,
            "po_number": d.po_number, "terms": _j(d.terms_json, []), "lines": _j(a.lines_json, []),
            "parent_agreement_id": d.parent_agreement_id,
            "sub_agreements": [{"id": s.agreement_id, "name": db.get(OmAgreement, s.agreement_id).name} for s in subs if db.get(OmAgreement, s.agreement_id)],
            "orders": _j(d.order_ids_json, []), "accounts": [{"party_id": p, "name": party_name(db, p)} for p in _j(d.account_ids_json, [])],
            "entitlement": {"id": ent.id, "name": ent.name, "response_hours": ent.response_hours, "coverage": ent.coverage} if ent else None,
            "document": d.document, "document_generated_at": d.document_generated_at, "signed_at": d.signed_at,
            "revisions": [{"revision": r.revision, "reason": r.reason, "changes": _j(r.changes_json, []), "at": r.created_at}
                          for r in db.query(CmAgreementRevision).filter(CmAgreementRevision.agreement_id == a.id).order_by(CmAgreementRevision.revision.desc())]}


# ------------------------------------------------------------------ provider work orders


def allocate_providers(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    order = om.order_of(db, tenant_id, e.get("order_id"))
    od = om.order_detail(db, tenant_id, order)
    if od.om_status in ("pending", "awaiting_approval", "cancelled"):
        raise ValueError(f"Order {od.order_number} is {od.om_status} - submit it first, then allocate it to providers")
    existing = db.query(CmProviderWorkOrder).filter(CmProviderWorkOrder.order_id == order.id).all()
    if existing and not _bool(e.get("reallocate")):
        raise ValueError(f"Order {od.order_number} already has provider work orders: {', '.join(w.number for w in existing)}")
    for w in existing:
        if w.status == "submitted":
            db.delete(w)
    providers = db.query(CmProvider).filter(CmProvider.tenant_id == tenant_id, CmProvider.active.is_(True)).all()
    groups: dict[str, list[dict[str, Any]]] = {}
    lead: dict[str, int] = {}
    for line in om.lines(db, tenant_id, "order", order, include_deleted=False):
        name = line.product_name.lower()
        prov = next((p for p in providers if any(k.strip() and k.strip().lower() in name for k in p.keywords.split(","))), None)
        key = prov.name if prov else "In-house network"
        lead[key] = prov.lead_days if prov else 3
        groups.setdefault(key, []).append({"line_id": line.id, "product": line.product_name, "quantity": line.quantity, "action": line.action_code})
    if not groups:
        raise ValueError(f"Order {od.order_number} has no lines to allocate")
    made = []
    for prov_name, rows in groups.items():
        w = CmProviderWorkOrder(tenant_id=tenant_id, order_id=order.id, provider=prov_name, lines_json=json.dumps(rows),
                                due_date=utcnow().date() + dt.timedelta(days=lead[prov_name]), payment_terms=od.payment_terms,
                                freight_terms=od.shipping_terms or "", return_to=str(e.get("return_to") or ""))
        db.add(w)
        db.flush()
        w.number = f"PWO-{w.id:05d}"
        made.append(provider_wo_out(w))
    db.flush()
    return {"order_id": order.id, "order_number": od.order_number, "work_orders": made}


def provider_wo_out(w: CmProviderWorkOrder) -> dict[str, Any]:
    return {"id": w.id, "number": w.number, "order_id": w.order_id, "provider": w.provider, "lines": _j(w.lines_json, []), "status": w.status,
            "due_date": w.due_date, "payment_terms": w.payment_terms, "freight_terms": w.freight_terms, "return_to": w.return_to,
            "notes": w.notes, "notify_on_complete": w.notify_on_complete, "completed_at": w.completed_at}


def update_provider_wo(db: Session, tenant_id: int, wo_id: int, data: dict[str, Any]) -> dict[str, Any]:
    w = _own(db, CmProviderWorkOrder, wo_id, tenant_id, "Provider work order")
    if data.get("status"):
        st = str(data["status"]).lower()
        if st not in ("submitted", "in_progress", "backordered", "complete", "failed"):
            raise ValueError("Status: submitted, in_progress, backordered, complete or failed")
        w.status = st
        w.completed_at = utcnow() if st == "complete" else None
    for f in ("notes", "notify_on_complete", "payment_terms", "freight_terms", "return_to"):
        if f in data and data[f] is not None:
            setattr(w, f, str(data[f]))
    if data.get("due_date"):
        w.due_date = _date(data["due_date"])
    out = provider_wo_out(w)
    if w.status == "failed":
        out["fallout"] = log_fallout(db, tenant_id, {"order_id": w.order_id, "error_code": "PROVIDER-FAILED", "system_of_failure": w.provider,
                                                      "description": f"{w.provider} could not complete {w.number}: {w.notes}"[:300]})
    db.flush()
    return out


def provider_work_orders(db: Session, tenant_id: int, order_id: int | None = None) -> list[dict[str, Any]]:
    q = db.query(CmProviderWorkOrder).filter(CmProviderWorkOrder.tenant_id == tenant_id)
    if order_id:
        q = q.filter(CmProviderWorkOrder.order_id == order_id)
    return [provider_wo_out(w) for w in q.order_by(CmProviderWorkOrder.id.desc()).limit(300)]


# ------------------------------------------------------------------ the account view


def account_view(db: Session, tenant_id: int, party: Party) -> dict[str, Any]:
    """Everything the communications layer knows about one account."""
    from app.crm_core import comm_billing as cb

    root = root_of(db, party.id)
    return {
        "account": account_out(db, tenant_id, party),
        "hierarchy": tree(db, tenant_id, root),
        "contacts": contacts_of(db, tenant_id, party.id),
        "billing_profiles": [profile_out(p) for p in billing_profiles(db, tenant_id, party.id)],
        "profiles": {k: profile_data(db, tenant_id, party.id, k) for k in PROFILE_KINDS if k != "loyalty"},
        "loyalty": loyalty(db, tenant_id, party.id),
        "addresses": [{"id": a.id, "raw": a.raw, "standard_form": a.standard_form, "kind": a.kind, "primary": a.is_primary,
                       "validated": a.validated, "premise_id": a.premise_id}
                      for a in db.query(CmAddress).filter(CmAddress.tenant_id == tenant_id, CmAddress.party_id == party.id).order_by(CmAddress.id)],
        "premises": premises(db, tenant_id, party.id),
        "site_items": [site_item_out(i) for i in db.query(CmSiteItem).filter(CmSiteItem.tenant_id == tenant_id, CmSiteItem.party_id == party.id)],
        "rating_lists": [rating_list_out(db, x) for x in db.query(CmRatingList).filter(CmRatingList.tenant_id == tenant_id,
                                                                                         CmRatingList.party_id == party.id)],
        "change_requests": [change_request_out(c) for c in db.query(CmChangeRequest).filter(CmChangeRequest.tenant_id == tenant_id,
                                                                                              CmChangeRequest.party_id == party.id)],
        "agreements": [{"id": a.id, "agreement_number": a.agreement_number, "name": a.name, "status": a.status, "end_date": a.end_date}
                       for a in db.query(OmAgreement).filter(OmAgreement.tenant_id == tenant_id, OmAgreement.party_id == party.id)],
        "billing": cb.summary(db, tenant_id, party.id),
    }
