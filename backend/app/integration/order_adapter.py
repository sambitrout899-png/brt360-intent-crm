"""Internal adapter shaped like TMF622 (Product Ordering) / TMF637 (Inventory)
/ TMF678 (Billing) - HLD 6.2. Same rationale as tmf620_adapter.py: crm_core
and orchestration never touch these ORM models directly for order/billing
operations, only through this module, so a real OSM/UIM/BRM integration
later is a contained change.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.models.orders import (
    BillingAccount,
    BillingAdjustment,
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    Resource,
    ResourceStatus,
    ServiceOrder,
    ServiceOrderStatus,
)


def create_order(
    db: Session,
    *,
    tenant_id: int,
    party_id: int,
    service_account_id: int,
    quote_id: int | None,
    order_type: OrderType,
    monthly_price: float,
    contract_months: int = 12,
) -> Order:
    order = Order(
        tenant_id=tenant_id,
        party_id=party_id,
        service_account_id=service_account_id,
        quote_id=quote_id,
        order_type=order_type,
        status=OrderStatus.created,
        monthly_price=monthly_price,
        contract_months=contract_months,
    )
    db.add(order)
    db.flush()
    return order


def add_order_item(db: Session, order_id: int, product_offering_id: int, quantity: int = 1) -> OrderItem:
    item = OrderItem(order_id=order_id, product_offering_id=product_offering_id, quantity=quantity)
    db.add(item)
    db.flush()
    return item


def create_service_order(db: Session, order_id: int) -> ServiceOrder:
    so = ServiceOrder(order_id=order_id, status=ServiceOrderStatus.pending)
    db.add(so)
    db.flush()
    return so


def add_resource(db: Session, service_order_id: int, resource_type: str, identifier: str) -> Resource:
    resource = Resource(
        service_order_id=service_order_id,
        resource_type=resource_type,
        identifier=identifier,
        status=ResourceStatus.reserved,
    )
    db.add(resource)
    db.flush()
    return resource


def activate_service_order(db: Session, service_order_id: int) -> ServiceOrder:
    so = db.get(ServiceOrder, service_order_id)
    if so is None:
        raise ValueError(f"ServiceOrder {service_order_id} not found")
    so.status = ServiceOrderStatus.active
    for r in so.resources:
        r.status = ResourceStatus.active
    db.flush()
    return so


def release_service_order(db: Session, service_order_id: int) -> ServiceOrder:
    so = db.get(ServiceOrder, service_order_id)
    if so is None:
        raise ValueError(f"ServiceOrder {service_order_id} not found")
    so.status = ServiceOrderStatus.released
    for r in so.resources:
        r.status = ResourceStatus.released
    db.flush()
    return so


def set_order_status(db: Session, order_id: int, status: OrderStatus) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise ValueError(f"Order {order_id} not found")
    order.status = status
    db.flush()
    return order


def get_or_create_billing_account(db: Session, tenant_id: int, party_id: int) -> BillingAccount:
    account = db.query(BillingAccount).filter(BillingAccount.party_id == party_id).first()
    if account is None:
        account = BillingAccount(tenant_id=tenant_id, party_id=party_id, status="active")
        db.add(account)
        db.flush()
    return account


def apply_billing_adjustment(
    db: Session, billing_account_id: int, order_id: int | None, amount: float, reason: str
) -> BillingAdjustment:
    adjustment = BillingAdjustment(billing_account_id=billing_account_id, order_id=order_id, amount=amount, reason=reason)
    db.add(adjustment)
    account = db.get(BillingAccount, billing_account_id)
    account.balance += amount
    db.flush()
    return adjustment


def set_billing_account_status(db: Session, billing_account_id: int, status: str, rate_plan: str | None = None) -> BillingAccount:
    account = db.get(BillingAccount, billing_account_id)
    if account is None:
        raise ValueError(f"BillingAccount {billing_account_id} not found")
    account.status = status
    if rate_plan is not None:
        account.rate_plan = rate_plan
    db.flush()
    return account
