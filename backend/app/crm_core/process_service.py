"""Shared helpers of the standard sales processes: which segments a tenant
sells to, who plays each B2B Sales-to-Order role, what an account pays (its
pricelist), and the Sales-to-Order details kept beside each record (a
party's profile, an opportunity's configuration, a quote's PO, an order's
profile - see app/models/crm.py and orders.py). Records without those
details behave exactly as before.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import approval_service, lifecycle_service
from app.models.catalog import ProductOffering
from app.models.crm import Opportunity, OpportunityConfig, Party, PartyProfile, Quote, QuotePurchaseOrder
from app.models.lifecycle import Position
from app.models.orders import Order, OrderProfile
from app.models.process import PROCESS_ROLES, PriceList, PriceListItem, ProcessRole
from app.models.tenant import Tenant, TenantVertical

DEFAULT_PRICE_LISTS = [("Standard", 0.0, True), ("Enterprise", -5.0, False), ("Strategic", -10.0, False)]
# The pricing attributes an Account Manager picks from when configuring a
# product (INT-37) - (name, data type, unit, allowed values).
DEFAULT_PRICING_ATTRIBUTES = [
    ("Bandwidth", "enum", "Mbps", "10 Mbps,50 Mbps,100 Mbps,200 Mbps,500 Mbps,1 Gbps,10 Gbps"),
    ("Contract term", "enum", "months", "12 months,24 months,36 months,60 months"),
    ("SLA", "enum", None, "99.5%,99.9%,99.95%,99.99%"),
    ("Number of sites", "enum", None, "1,2,3,5,10,25,50"),
    ("Static IPs", "enum", None, "None,1,8,16,32"),
]


# ---------------------------------------------------------------- prospect -> account


def _prospects_become_accounts(session: Session, flush_context, instances) -> None:
    """A prospect becomes an account when an order of theirs is activated
    end to end - whichever step activated it. Accounts stay accounts."""
    from sqlalchemy import inspect as sa_inspect

    from app.models.orders import OrderStatus

    for obj in [*session.new, *session.dirty]:
        if not isinstance(obj, Order) or obj.status not in (OrderStatus.active, OrderStatus.completed) or not obj.party_id:
            continue
        if obj not in session.new and not sa_inspect(obj).attrs.status.history.has_changes():
            continue
        party = session.get(Party, obj.party_id)
        if party is None or account_status(party) == "account":
            continue
        profile = party_profile(party, create=True)
        profile.account_status = "account"
        if party.party_type == "prospect":
            party.party_type = "individual" if profile.segment == "B2C" else "organization"


def install_account_conversion() -> None:
    from sqlalchemy import event

    if not event.contains(Session, "before_flush", _prospects_become_accounts):
        event.listen(Session, "before_flush", _prospects_become_accounts)


# ---------------------------------------------------------------- record details


def party_profile(party: Party | None, create: bool = False) -> PartyProfile | None:
    if party is None:
        return None
    if party.profile is None and create:
        party.profile = PartyProfile()
    return party.profile


def account_status(party: Party | None) -> str | None:
    profile = party_profile(party)
    return profile.account_status if profile else None


def party_segment(party: Party | None) -> str | None:
    profile = party_profile(party)
    return profile.segment if profile else None


def opportunity_config(opportunity: Opportunity | None, create: bool = False) -> OpportunityConfig | None:
    if opportunity is None:
        return None
    if opportunity.config is None and create:
        opportunity.config = OpportunityConfig()
    return opportunity.config


def account_manager_of(opportunity: Opportunity | None) -> int | None:
    config = opportunity_config(opportunity)
    return config.account_manager_user_id if config else None


def set_account_manager(opportunity: Opportunity, user_id: int | None) -> None:
    """The Account Manager is whoever first worked the opportunity."""
    if user_id and account_manager_of(opportunity) is None:
        opportunity_config(opportunity, create=True).account_manager_user_id = user_id


def quote_po(quote: Quote | None) -> QuotePurchaseOrder | None:
    return quote.purchase_order if quote is not None else None


def is_matched(quote: Quote | None) -> bool:
    po = quote_po(quote)
    return po is not None and po.matched_at is not None


def order_profile(order: Order | None, create: bool = False) -> OrderProfile | None:
    if order is None:
        return None
    if order.profile is None and create:
        order.profile = OrderProfile()
    return order.profile


# ---------------------------------------------------------------- segments


def segments(db: Session, tenant_id: int) -> set[str]:
    tenant = db.get(Tenant, tenant_id)
    vertical = tenant.vertical if tenant else TenantVertical.both
    return {"B2B"} if vertical == TenantVertical.b2b else {"B2C"} if vertical == TenantVertical.b2c else {"B2B", "B2C"}


# ---------------------------------------------------------------- roles


def role_position(db: Session, tenant_id: int, role_key: str) -> Position | None:
    row = db.query(ProcessRole).filter(ProcessRole.tenant_id == tenant_id, ProcessRole.role_key == role_key).first()
    if row is not None:
        return db.get(Position, row.position_id) if row.position_id else None
    return approval_service.position_by_name(db, tenant_id, PROCESS_ROLES[role_key], create=False)


def role_map(db: Session, tenant_id: int) -> list[dict]:
    out = []
    for key, label in PROCESS_ROLES.items():
        position = role_position(db, tenant_id, key)
        out.append({"role_key": key, "label": label, "position_id": position.id if position else None,
                    "position_name": position.name if position else None})
    return out


def set_role(db: Session, tenant_id: int, role_key: str, position_id: int | None) -> None:
    if role_key not in PROCESS_ROLES:
        raise ValueError(f"Unknown process role '{role_key}'")
    row = db.query(ProcessRole).filter(ProcessRole.tenant_id == tenant_id, ProcessRole.role_key == role_key).first()
    if row is None:
        row = ProcessRole(tenant_id=tenant_id, role_key=role_key)
        db.add(row)
    row.position_id = position_id
    db.flush()


def is_presales_only(db: Session, tenant_id: int, user_id: int | None) -> bool:
    """Someone in the Presales position (and not also an Account Manager or
    an administrator): they own the technical attributes of an opportunity;
    its product and pricing attributes they see but don't change."""
    from app.models.auth import User, UserRole

    user = db.get(User, user_id) if user_id else None
    if user is None or user.role == UserRole.admin:
        return False
    presales = role_position(db, tenant_id, "presales")
    if presales is None or user.id not in approval_service.position_holder_ids(db, presales.id):
        return False
    manager = role_position(db, tenant_id, "account_manager")
    return manager is None or user.id not in approval_service.position_holder_ids(db, manager.id)


def _tenant_rule_first(db: Session, tenant_id: int, entity_type: str, entity_id: int) -> dict | None:
    """The tenant's own assignment rule for a new record beats the standard
    hand-off (rules on later states run anyway, right after the step)."""
    if lifecycle_service.get_meta(db, tenant_id, entity_type, entity_id, create=False) is not None:
        return None
    return lifecycle_service.apply_assignment_rules(db, tenant_id, entity_type, entity_id, None)


def hand_to_role(db: Session, tenant_id: int, entity_type: str, entity_id: int, role_key: str, reason: str, source: str) -> dict | None:
    """Give a record to the position that plays this role - nothing happens
    when the tenant has no such position (the step itself still happens)."""
    ruled = _tenant_rule_first(db, tenant_id, entity_type, entity_id)
    if ruled is not None:
        return ruled
    position = role_position(db, tenant_id, role_key)
    if position is None:
        return None
    return lifecycle_service.assign(db, tenant_id, entity_type, entity_id, None, position.id, reason, source)


def hand_to_user(db: Session, tenant_id: int, entity_type: str, entity_id: int, user_id: int | None, reason: str, source: str) -> dict | None:
    ruled = _tenant_rule_first(db, tenant_id, entity_type, entity_id)
    if ruled is not None or not user_id:
        return ruled
    return lifecycle_service.assign(db, tenant_id, entity_type, entity_id, user_id, None, reason, source)


# ---------------------------------------------------------------- pricing


def price_list_of(db: Session, tenant_id: int, party: Party | None) -> PriceList | None:
    """The customer's pricelist while it is in effect, else the default one in effect."""
    profile = party_profile(party)
    if profile is not None and profile.price_list_id:
        found = db.get(PriceList, profile.price_list_id)
        if found is not None and found.tenant_id == tenant_id and found.in_effect():
            return found
    defaults = db.query(PriceList).filter(PriceList.tenant_id == tenant_id, PriceList.is_default.is_(True),
                                          PriceList.is_active.is_(True)).all()
    return next((p for p in defaults if p.in_effect()), None)


def find_price_list(db: Session, tenant_id: int, ref) -> PriceList | None:
    if ref in (None, ""):
        return None
    query = db.query(PriceList).filter(PriceList.tenant_id == tenant_id, PriceList.is_active.is_(True))
    if isinstance(ref, int) or str(ref).isdigit():
        found = query.filter(PriceList.id == int(ref)).first()
    else:
        found = query.filter(PriceList.name.ilike(str(ref).strip())).first()
    return found if found is not None and found.in_effect() else None


def price_item(db: Session, price_list: PriceList, offering_name: str, quantity: Any = 1) -> PriceListItem | None:
    """The pricelist's price for the product at this quantity - the highest
    volume tier the quantity reaches (Siebel's volume discount)."""
    try:
        qty = max(int(float(quantity or 1)), 1)
    except (TypeError, ValueError):
        qty = 1
    tiers = db.query(PriceListItem).filter(PriceListItem.price_list_id == price_list.id,
                                           PriceListItem.offering_name.ilike(offering_name)).all()
    fitting = [t for t in tiers if (t.min_quantity or 1) <= qty]
    return max(fitting, key=lambda t: t.min_quantity or 1) if fitting else None


def unit_price(db: Session, tenant_id: int, party: Party | None, offering: ProductOffering, quantity: Any = 1) -> tuple[float, str]:
    """(price, where it came from) for one unit of the offering. A list
    with no item for the product and a 0% adjustment = the catalog price."""
    price_list = price_list_of(db, tenant_id, party)
    if price_list is None:
        return offering.price, "list price"
    item = price_item(db, price_list, offering.name, quantity)
    if item is not None:
        return item.price, tier_source(price_list, item)
    if price_list.adjustment_pct:
        return round(offering.price * (1 + price_list.adjustment_pct / 100), 2), f"pricelist '{price_list.name}' ({price_list.adjustment_pct:+g}%)"
    return offering.price, "list price"


def tier_source(price_list: PriceList, item: PriceListItem) -> str:
    return f"pricelist '{price_list.name}'" + (f" ({item.min_quantity}+ units)" if (item.min_quantity or 1) > 1 else "")


# ---------------------------------------------------------------- seeding


def seed_defaults(db: Session, tenant_id: int) -> None:
    """Process-role positions, pricelists and pricing attributes - each only
    where the tenant has none yet."""
    for role_key, name in PROCESS_ROLES.items():
        if db.query(ProcessRole).filter(ProcessRole.tenant_id == tenant_id, ProcessRole.role_key == role_key).first() is None:
            position = approval_service.position_by_name(db, tenant_id, name)
            db.add(ProcessRole(tenant_id=tenant_id, role_key=role_key, position_id=position.id))
    from app.models.attributes import ProductAttributeDefinition

    if db.query(ProductAttributeDefinition).filter(ProductAttributeDefinition.tenant_id == tenant_id).count() == 0:
        for name, data_type, unit, allowed in DEFAULT_PRICING_ATTRIBUTES:
            db.add(ProductAttributeDefinition(tenant_id=tenant_id, name=name, data_type=data_type, unit=unit, allowed_values=allowed))
    if db.query(PriceList).filter(PriceList.tenant_id == tenant_id).count() == 0:
        for name, adjustment, is_default in DEFAULT_PRICE_LISTS:
            db.add(PriceList(tenant_id=tenant_id, name=name, adjustment_pct=adjustment, is_default=is_default))
    db.flush()
