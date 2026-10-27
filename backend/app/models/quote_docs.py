"""The documents of a quote - every PDF of the deal, kept on the quote itself.

- QuoteDocument: one PDF on a quote - the proposal BRT generates once the
  discount is approved (INT-109), the customer's purchase order uploaded with
  INT-39, the agreement generated when Sales Ops matches the PO (INT-40), or
  any other PDF someone attaches. Generated ones keep a version number;
  nothing is ever overwritten.
- QuoteDocumentTemplate: a tenant's wording for a generated document (the
  proposal, the agreement). Without a row the vanilla wording applies.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class QuoteDocument(Base):
    __tablename__ = "quote_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # proposal / purchase_order / agreement / other
    title: Mapped[str] = mapped_column(String(200))
    file_name: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100), default="application/pdf")
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="generated")  # generated / uploaded
    version: Mapped[int] = mapped_column(Integer, default=1)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class QuoteDocumentTemplate(Base):
    __tablename__ = "quote_document_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", name="uq_quote_document_templates_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(String(30))  # proposal / agreement
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    intro: Mapped[str] = mapped_column(Text, default="")
    terms: Mapped[str] = mapped_column(Text, default="")
    closing: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)
