"""Mobile subscribers - what an operator, an MVNO or an MVNE needs on top of the
communications layer (models/comms.py) to run prepaid and postpaid mobile lines.

  MbLine          one subscriber line: its number (MSISDN), SIM (ICCID / IMSI, physical
                  or eSIM), plan, prepaid or postpaid, status, barrings, roaming, the
                  prepaid wallet and its validity, the postpaid credit limit and spend
                  this cycle, the security deposit, the shared-plan group it is in and
                  the wholesale partner (MVNO / reseller brand) it belongs to
  MbPack          a bundle on a line - a data / voice / SMS / roaming pack, a value-added
                  service or the plan's own monthly allowance - with what is left of it
  MbWalletTxn     every movement of a prepaid wallet (recharge, voucher, transfer, pack,
                  plan fee, emergency credit and its recovery, refund, migration)
  MbVoucher       a prepaid recharge voucher - only a hash of its PIN is kept
  MbPartner       a wholesale partner: an MVNO, an MVNE, a reseller or a dealer, with its
                  wholesale rates, revenue share and number range
  MbSettlement    a partner's statement for a period: wholesale usage owed, commission due
  MbLineEvent     a line's history - who changed what, when

The ledger stays the communications one: postpaid charges post to the billing account
(comm_billing), so the bill run bills them; prepaid money lives in the line's wallet.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

PAYMENT_TYPES = ("prepaid", "postpaid")
LINE_STATUSES = ("active", "suspended", "terminated")
SIM_TYPES = ("physical", "esim")
# What a barring stops. Raised by an agent (lost_stolen, fraud, outgoing ...), or by the
# system (no_balance, validity, credit_limit, non_payment).
BARRINGS = ("lost_stolen", "fraud", "non_payment", "outgoing", "incoming", "international", "premium", "data", "roaming",
            "no_balance", "validity", "credit_limit")
SYSTEM_BARRINGS = ("no_balance", "validity", "credit_limit")
PACK_KINDS = ("data", "voice", "sms", "combo", "roaming", "vas", "plan")
PARTNER_KINDS = ("mvno", "mvne", "reseller", "dealer")


class MbPartner(Base):
    __tablename__ = "mb_partners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="mvno")
    brand: Mapped[str] = mapped_column(String(100), default="")
    # per unit (minute / message / MB) the partner pays the host network for its lines' usage
    wholesale_rates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revenue_share_pct: Mapped[float] = mapped_column(Float, default=0.0)  # dealers / resellers: commission on recharges they sell
    number_range: Mapped[str] = mapped_column(String(60), default="")  # "9180000000-9180999999" - its lines' numbers
    contact_email: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / suspended
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class MbLine(Base):
    __tablename__ = "mb_lines"
    __table_args__ = (UniqueConstraint("tenant_id", "msisdn", name="uq_mb_line_msisdn"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), index=True)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("mb_partners.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    msisdn: Mapped[str] = mapped_column(String(20))
    iccid: Mapped[str] = mapped_column(String(25), default="")
    imsi: Mapped[str] = mapped_column(String(20), default="")
    sim_type: Mapped[str] = mapped_column(String(10), default="physical")
    user_name: Mapped[str] = mapped_column(String(200), default="")  # who uses it - an employee on a business account
    plan: Mapped[str] = mapped_column(String(60), default="")  # a plan code from the mobile settings
    payment_type: Mapped[str] = mapped_column(String(10), default="prepaid")
    status: Mapped[str] = mapped_column(String(15), default="active")
    status_reason: Mapped[str] = mapped_column(String(300), default="")
    barrings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    roaming: Mapped[bool] = mapped_column(Boolean, default=False)
    # prepaid
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    valid_until: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    advance_owed: Mapped[float] = mapped_column(Float, default=0.0)  # emergency credit to recover from the next recharge
    # postpaid
    credit_limit: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = none
    spend_alert_pct: Mapped[int] = mapped_column(Integer, default=80)
    cycle_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    cycle_spend: Mapped[float] = mapped_column(Float, default=0.0)
    alert_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    deposit: Mapped[float] = mapped_column(Float, default=0.0)
    # shared plan: the line whose packs this one shares (None = it is not in a group, or it is the head)
    group_head_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    swap_cooldown_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    previous_msisdns: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"msisdn", "until"}] - quarantined
    activated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    terminated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    quarantine_until: Mapped[dt.date | None] = mapped_column(Date, nullable=True)  # a terminated line's number comes back after it
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class MbPack(Base):
    __tablename__ = "mb_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("mb_lines.id"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(15), default="data")
    # {"data": MB, "voice": minutes, "sms": messages} granted, and what is left
    granted: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    remaining: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    roaming: Mapped[bool] = mapped_column(Boolean, default=False)  # its allowance is for usage abroad
    price: Mapped[float] = mapped_column(Float, default=0.0)
    paid_from: Mapped[str] = mapped_column(String(10), default="balance")  # balance (prepaid) / bill (postpaid) / free
    valid_from: Mapped[dt.datetime] = mapped_column(default=utcnow)
    valid_to: Mapped[dt.datetime] = mapped_column(DateTime)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(15), default="active")  # active / expired / cancelled
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class MbWalletTxn(Base):
    __tablename__ = "mb_wallet_txns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("mb_lines.id"), index=True)
    partner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the dealer that sold the recharge
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Float)  # + money in, - money out
    balance_after: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str] = mapped_column(String(20), default="")
    reference: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class MbVoucher(Base):
    __tablename__ = "mb_vouchers"
    __table_args__ = (UniqueConstraint("tenant_id", "serial", name="uq_mb_voucher_serial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    batch: Mapped[str] = mapped_column(String(30), index=True)
    serial: Mapped[str] = mapped_column(String(40))
    pin_hash: Mapped[str] = mapped_column(String(64), index=True)
    face_value: Mapped[float] = mapped_column(Float)
    validity_days: Mapped[int] = mapped_column(Integer, default=30)  # how long a recharge with it keeps the line valid
    partner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the dealer it was issued to
    status: Mapped[str] = mapped_column(String(15), default="available")  # available / redeemed / blocked / expired
    expires_on: Mapped[dt.date] = mapped_column(Date)
    redeemed_line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redeemed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # The PINs of a new batch, kept until they are downloaded once (then cleared) - on the batch's first voucher.
    pending_pins: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class MbSettlement(Base):
    __tablename__ = "mb_settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("mb_partners.id"), index=True)
    number: Mapped[str] = mapped_column(String(30), default="")
    period_start: Mapped[dt.date] = mapped_column(Date)
    period_end: Mapped[dt.date] = mapped_column(Date)
    lines: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"label", "quantity", "rate", "amount"}]
    wholesale_due: Mapped[float] = mapped_column(Float, default=0.0)  # the partner owes the host
    commission_due: Mapped[float] = mapped_column(Float, default=0.0)  # the host owes the partner
    net: Mapped[float] = mapped_column(Float, default=0.0)  # + partner pays, - host pays
    status: Mapped[str] = mapped_column(String(15), default="issued")  # issued / settled
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class MbLineEvent(Base):
    __tablename__ = "mb_line_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("mb_lines.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(String(400), default="")
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
