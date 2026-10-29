"""Notification deliveries (app/crm_core/notification_delivery.py): what became
of each notification the platform recorded (a "notification.sent" event in the
outbox - a Flow Studio Notify step, a bundle launch ...)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

# sent | in_app (shown on screen by the app itself) | skipped (email delivery is off) |
# no_recipients | no_provider (a channel nothing delivers yet: sms, whatsapp ...) | failed
DELIVERY_STATUSES = ("sent", "in_app", "skipped", "no_recipients", "no_provider", "failed")


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (Index("ix_notification_deliveries_tenant_at", "tenant_id", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    event_id: Mapped[int] = mapped_column(Integer, unique=True)  # the outbox event delivered - each once
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    channel: Mapped[str] = mapped_column(String(30))
    audience: Mapped[str] = mapped_column(String(300), default="")
    recipients: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str] = mapped_column(Text, default="")  # to whom, or why not
