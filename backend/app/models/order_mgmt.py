"""Order Management (after the Siebel Order Management process model).

Beside the existing quote/order rows - nothing on quotes, quote_items,
orders or order_items changes meaning:

  quote  + om_quote_details     number, effective dates (expiry), verification
  order  + om_order_details     number, revision, order class, sales-order
                                status, ship-to, shipping, tax, payment terms,
                                hold, cancellation, follow-on / return links
  quote/order lines             om_lines - every line of a quote or order:
                                the main product (mirrors quote_items /
                                order_items) plus equipment, add-ons, write-ins
                                and one-time charges; price type, discount,
                                action code (Add/Update/Delete/-/Suspend/Resume),
                                availability (ATP), shipping and the asset it
                                became; om_line_changes keeps the change history
  revisions                     om_order_revisions - a supplemental order's
                                before/after with action codes
  shipments                     om_shipments / om_shipment_lines
  payments & credit             om_payments, om_credit_profiles, om_credit_checks
  setup                         om_tax_rules, om_shipping_methods, om_favorites,
                                om_recommendations, om_promotions
  agreements                    om_agreements (quote -> agreement, AutoContract)
  forecast                      om_opportunity_revenue (update opportunity from quote)
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class OmQuoteDetail(Base):
    __tablename__ = "om_quote_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    quote_number: Mapped[str] = mapped_column(String(30), default="")
    effective_from: Mapped[dt.date] = mapped_column(Date)
    effective_through: Mapped[dt.date] = mapped_column(Date)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    opportunity_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    promotion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OmOrderDetail(Base):
    __tablename__ = "om_order_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    order_number: Mapped[str] = mapped_column(String(30), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    order_class: Mapped[str] = mapped_column(String(20), default="sales")  # sales / return / internal
    # the sales-order journey: pending -> awaiting_approval -> booked -> awaiting_shipping
    # -> partially_shipped -> shipped -> fulfilled; cancel_pending -> cancelled
    om_status: Mapped[str] = mapped_column(String(30), default="pending")
    requested_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    ship_to_name: Mapped[str] = mapped_column(String(200), default="")
    ship_to_address: Mapped[str] = mapped_column(String(400), default="")
    ship_region: Mapped[str] = mapped_column(String(100), default="")
    shipping_method_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    carrier: Mapped[str] = mapped_column(String(100), default="")
    shipping_terms: Mapped[str] = mapped_column(String(50), default="")
    freight: Mapped[float] = mapped_column(Float, default=0.0)
    tax_jurisdiction: Mapped[str] = mapped_column(String(100), default="")
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)
    tax_recurring: Mapped[float] = mapped_column(Float, default=0.0)
    payment_terms: Mapped[str] = mapped_column(String(50), default="")
    hold: Mapped[bool] = mapped_column(Boolean, default=False)
    hold_reason: Mapped[str] = mapped_column(String(200), default="")
    held_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_reason: Mapped[str] = mapped_column(String(200), default="")
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    source_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # follow-on of
    return_of_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    promotion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    # B2C omnichannel: where the order came in, the reference there, and the
    # installation address the Order Module checks feasibility against.
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    channel_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    installation_address: Mapped[str | None] = mapped_column(String(400), nullable=True)


class OmLine(Base):
    """One line of a quote or an order."""

    __tablename__ = "om_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    context_type: Mapped[str] = mapped_column(String(10))  # quote / order
    context_id: Mapped[int] = mapped_column(Integer, index=True)
    line_number: Mapped[int] = mapped_column(Integer, default=1)
    core_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # quote_items / order_items row it mirrors
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name: Mapped[str] = mapped_column(String(200))
    product_offering_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="service")  # service / equipment / one_time / write_in
    price_type: Mapped[str] = mapped_column(String(20), default="recurring")  # recurring / one_time / usage
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    list_price: Mapped[float] = mapped_column(Float, default=0.0)
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)
    discount_reason: Mapped[str] = mapped_column(String(300), default="")
    net_price: Mapped[float] = mapped_column(Float, default=0.0)  # per unit, after discount
    action_code: Mapped[str] = mapped_column(String(10), default="Add")  # Add / Update / Delete / - / Suspend / Resume
    status: Mapped[str] = mapped_column(String(30), default="pending")
    requested_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    atp_status: Mapped[str] = mapped_column(String(30), default="")  # available / partial / backorder / not_tracked
    atp_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    atp_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shipped_qty: Mapped[float] = mapped_column(Float, default=0.0)
    forecastable: Mapped[bool] = mapped_column(Boolean, default=True)
    trackable: Mapped[bool] = mapped_column(Boolean, default=False)  # becomes an asset when fulfilled
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    integration_id: Mapped[str] = mapped_column(String(40), default="")
    revised_line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commitment_months: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class OmLineChange(Base):
    __tablename__ = "om_line_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    line_id: Mapped[int] = mapped_column(Integer, index=True)
    context_type: Mapped[str] = mapped_column(String(10))
    context_id: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    field: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[str] = mapped_column(String(300), default="")
    new_value: Mapped[str] = mapped_column(String(300), default="")
    changed_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class OmOrderRevision(Base):
    __tablename__ = "om_order_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default="supplemental")  # supplemental / cancel / revise
    reason: Mapped[str] = mapped_column(String(300), default="")
    snapshot_json: Mapped[str] = mapped_column(Text, default="[]")  # the lines before
    changes_json: Mapped[str] = mapped_column(Text, default="[]")  # [{line, action, field, old, new}]
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class OmShipment(Base):
    __tablename__ = "om_shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    shipment_number: Mapped[str] = mapped_column(String(30), default="")
    carrier: Mapped[str] = mapped_column(String(100), default="")
    method: Mapped[str] = mapped_column(String(100), default="")
    tracking_number: Mapped[str] = mapped_column(String(100), default="")
    from_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="shipped")  # shipped / delivered
    shipped_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class OmShipmentLine(Base):
    __tablename__ = "om_shipment_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    shipment_id: Mapped[int] = mapped_column(ForeignKey("om_shipments.id"))
    line_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)


class OmPayment(Base):
    """A payment line. Card payments keep only the brand, the last four digits
    and the authorization reference - never a card number."""

    __tablename__ = "om_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    context_type: Mapped[str] = mapped_column(String(10), default="order")
    context_id: Mapped[int] = mapped_column(Integer)
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    method: Mapped[str] = mapped_column(String(20))  # credit_card / purchase_order / bank_transfer / cash / cheque / on_account
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / authorized / declined / captured / voided / refunded
    card_brand: Mapped[str] = mapped_column(String(20), default="")
    card_last4: Mapped[str] = mapped_column(String(4), default="")
    auth_code: Mapped[str] = mapped_column(String(40), default="")
    po_number: Mapped[str] = mapped_column(String(60), default="")
    reference: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class OmCreditProfile(Base):
    __tablename__ = "om_credit_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), unique=True)
    credit_limit: Mapped[float] = mapped_column(Float, default=0.0)
    payment_terms: Mapped[str] = mapped_column(String(50), default="Net 30")
    on_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[str] = mapped_column(String(20), default="")


class OmCreditCheck(Base):
    __tablename__ = "om_credit_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(Integer)
    context_type: Mapped[str] = mapped_column(String(10), default="order")
    context_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    exposure: Mapped[float] = mapped_column(Float, default=0.0)
    credit_limit: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[str] = mapped_column(String(20))  # approved / declined / review
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class OmTaxRule(Base):
    __tablename__ = "om_tax_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    jurisdiction: Mapped[str] = mapped_column(String(100), default="ALL")
    name: Mapped[str] = mapped_column(String(100))
    rate_pct: Mapped[float] = mapped_column(Float, default=0.0)
    applies_to: Mapped[str] = mapped_column(String(20), default="all")  # all / service / equipment / one_time / recurring
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OmShippingMethod(Base):
    __tablename__ = "om_shipping_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))
    carrier: Mapped[str] = mapped_column(String(100), default="")
    base_charge: Mapped[float] = mapped_column(Float, default=0.0)
    per_unit: Mapped[float] = mapped_column(Float, default=0.0)
    free_over: Mapped[float | None] = mapped_column(Float, nullable=True)
    days: Mapped[int] = mapped_column(Integer, default=3)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OmFavorite(Base):
    """Product selection favorite: a set of lines added in one go."""

    __tablename__ = "om_favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    items_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"product", "quantity"}]
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OmRecommendation(Base):
    __tablename__ = "om_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    product: Mapped[str] = mapped_column(String(200))
    recommended_product: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="cross_sell")  # cross_sell / up_sell
    score: Mapped[float] = mapped_column(Float, default=50.0)
    message: Mapped[str] = mapped_column(String(300), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OmPromotion(Base):
    __tablename__ = "om_promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(40), default="")
    products: Mapped[str] = mapped_column(Text, default="")  # products that must be on the quote/order
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)  # off each matching line
    commitment_months: Mapped[int] = mapped_column(Integer, default=0)
    early_termination_fee: Mapped[float] = mapped_column(Float, default=0.0)
    valid_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OmAgreement(Base):
    __tablename__ = "om_agreements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    agreement_number: Mapped[str] = mapped_column(String(30), default="")
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    quote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # draft / active / expired / terminated
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    term_months: Mapped[int] = mapped_column(Integer, default=12)
    payment_terms: Mapped[str] = mapped_column(String(50), default="Net 30")
    monthly_value: Mapped[float] = mapped_column(Float, default=0.0)
    one_time_value: Mapped[float] = mapped_column(Float, default=0.0)
    response_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[str] = mapped_column(String(20), default="business")
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    entitlement_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lines_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class OmOpportunityRevenue(Base):
    __tablename__ = "om_opportunity_revenue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    quote_id: Mapped[int] = mapped_column(Integer)
    line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name: Mapped[str] = mapped_column(String(200))
    revenue_type: Mapped[str] = mapped_column(String(20), default="recurring")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    forecastable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class OmChannelInteraction(Base):
    """One touch of a B2C order journey on one channel (IVR, retail point,
    store, WhatsApp, SMS, USSD, web, app ...) - the order can start on one
    and continue on another; this is its omnichannel timeline."""

    __tablename__ = "om_channel_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(30), default="note")  # order_started / order_placed / enquiry / status_check / update ...
    reference: Mapped[str] = mapped_column(String(100), default="")  # call id, conversation id, POS terminal, session ...
    note: Mapped[str] = mapped_column(String(1000), default="")
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[dt.datetime] = mapped_column(default=utcnow)
