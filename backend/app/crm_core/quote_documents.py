"""Every PDF of a deal, kept on its quote (models/quote_docs.py).

B2B Sales-to-Order, as seeded in the vanilla process:
  discount approved (INT-06) -> Generate proposal (INT-109): a proposal PDF
  on the quote -> Send it (INT-22) -> the customer's PO (INT-39, its PDF
  uploaded on the quote) -> Sales Ops Head: Match all (INT-40) -> the order
  is created and the agreement PDF lands on the quote.

The proposal and the agreement are written from the tenant's template
(Admin -> Quote Documents) - or the vanilla wording below when the tenant has
none. A template may be switched off: no proposal step (the PO comes straight
after approval), or no agreement on Match all. Generated documents get a new
version each time; nothing is overwritten or deleted.

The PDF writer (app/documents.py) needs no extra package: plain PDF 1.4 with the
standard Helvetica fonts, in the same house style as every other document.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.quote_docs import QuoteDocument, QuoteDocumentTemplate
from app.timeutils import utcnow

KINDS = {"proposal": "Proposal", "purchase_order": "Purchase order", "agreement": "Agreement", "other": "Document"}
UPLOAD_KINDS = ("purchase_order", "proposal", "agreement", "other")  # a signed proposal / agreement comes back as an upload
MAX_UPLOAD = 10 * 1024 * 1024
PLACEHOLDERS = ("company", "customer", "quote_number", "quote_id", "total", "discount", "date", "valid_until", "po_number",
                "order_id", "agreement_number", "account_manager")

DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "proposal": {
        "enabled": True,
        "title": "Commercial proposal",
        "intro": "Dear {customer},\n\nThank you for the opportunity to support your business. Following our discussions and the "
                 "feasibility study of your site, {company} is pleased to propose the services below. The prices include the "
                 "discount approved for you and are valid until {valid_until}.",
        "terms": "1. Prices are monthly recurring charges unless stated otherwise.\n"
                 "2. This proposal is valid until {valid_until}.\n"
                 "3. Service delivery starts once your purchase order is received and matched to this quote.\n"
                 "4. Installation dates are confirmed after the order is approved.",
        "closing": "To accept, please send us your purchase order quoting reference {quote_number}.\n\nKind regards,\n{account_manager}\n{company}",
    },
    "agreement": {
        "enabled": True,
        "title": "Service agreement",
        "intro": "This agreement is made on {date} between {company} (the provider) and {customer} (the customer), for the "
                 "services in quote {quote_number}, accepted by the customer with purchase order {po_number} and matched by Sales "
                 "Operations. It is fulfilled through order #{order_id}.",
        "terms": "1. The provider delivers and supports the services listed above for the agreed term.\n"
                 "2. The customer pays the agreed charges as invoiced, under the payment terms on record.\n"
                 "3. Changes to the services are made through a change order and recorded against this agreement.\n"
                 "4. Either party may end the agreement under the termination terms of the provider's standard conditions.",
        "closing": "Signed for {company}: ____________________        Signed for {customer}: ____________________",
    },
}


# ---------------------------------------------------------------- the PDF writer

# The house style shared by every document the platform generates (app/documents.py).
from app.documents import Pdf as _Pdf  # noqa: E402


# ---------------------------------------------------------------- templates


def template(db: Session, tenant_id: int, kind: str) -> dict[str, Any]:
    """The tenant's wording for a generated document, or the vanilla one."""
    base = dict(DEFAULT_TEMPLATES[kind])
    row = (db.query(QuoteDocumentTemplate)
           .filter(QuoteDocumentTemplate.tenant_id == tenant_id, QuoteDocumentTemplate.kind == kind).first())
    if row is not None:
        base.update(enabled=bool(row.enabled), title=row.title or base["title"], intro=row.intro, terms=row.terms, closing=row.closing)
    return {"kind": kind, "label": KINDS[kind], **base, "customized": row is not None,
            "updated_at": row.updated_at.isoformat(timespec="seconds") if row is not None and row.updated_at else None}


def templates(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    return [template(db, tenant_id, k) for k in DEFAULT_TEMPLATES]


def save_template(db: Session, tenant_id: int, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind not in DEFAULT_TEMPLATES:
        raise ValueError(f"Unknown document '{kind}' - one of {', '.join(DEFAULT_TEMPLATES)}")
    row = (db.query(QuoteDocumentTemplate)
           .filter(QuoteDocumentTemplate.tenant_id == tenant_id, QuoteDocumentTemplate.kind == kind).first())
    if row is None:
        row = QuoteDocumentTemplate(tenant_id=tenant_id, kind=kind, **{k: DEFAULT_TEMPLATES[kind][k]
                                                                      for k in ("enabled", "title", "intro", "terms", "closing")})
        db.add(row)
    if data.get("reset"):
        for key in ("enabled", "title", "intro", "terms", "closing"):
            setattr(row, key, DEFAULT_TEMPLATES[kind][key])
    else:
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        for key, limit in (("title", 200), ("intro", 5000), ("terms", 10000), ("closing", 3000)):
            if key in data and data[key] is not None:
                row.__setattr__(key, str(data[key])[:limit])
        if not (row.title or "").strip():
            row.title = DEFAULT_TEMPLATES[kind]["title"]
    db.flush()
    return template(db, tenant_id, kind)


def enabled(db: Session, tenant_id: int, kind: str) -> bool:
    return bool(template(db, tenant_id, kind)["enabled"])


def _fill(text: str, values: dict[str, Any]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text or "")


# ---------------------------------------------------------------- what goes in


def _money(value: float | None, currency: str) -> str:
    return f"{currency} {value or 0:,.2f}"


def _deal(db: Session, tenant_id: int, quote) -> dict[str, Any]:
    from app.crm_core import process_service
    from app.crm_core import product_config_service as product_config
    from app.models.auth import User
    from app.models.catalog import ProductOffering
    from app.models.crm import Opportunity, Party
    from app.models.product_config import QuoteItemConfig
    from app.models.tenant import Tenant

    tenant = db.get(Tenant, tenant_id)
    opportunity = db.get(Opportunity, quote.opportunity_id)
    lead = opportunity.lead if opportunity is not None else None
    party = db.get(Party, lead.party_id) if lead is not None and lead.party_id else None
    price_list = process_service.price_list_of(db, tenant_id, party)
    currency = price_list.currency if price_list is not None and price_list.currency else "INR"
    manager_id = process_service.account_manager_of(opportunity)
    manager = db.get(User, manager_id) if manager_id else None
    lines, subtotal = [], 0.0
    for item in quote.items:
        offering = db.get(ProductOffering, item.product_offering_id)
        config = db.query(QuoteItemConfig).filter(QuoteItemConfig.quote_item_id == item.id).first()
        attrs = product_config.loads(config.pricing_attributes_json) if config is not None else {}
        amount = (item.unit_price or 0) * (item.quantity or 1)
        subtotal += amount
        lines.append({"product": offering.name if offering else f"Product #{item.product_offering_id}",
                      "options": ", ".join(f"{k}: {v}" for k, v in (attrs or {}).items()),
                      "quantity": item.quantity, "unit_price": item.unit_price, "amount": amount})
    total = quote.total_price or 0.0
    # A negotiated price (INT-116) is its own line - the discount is what the system priced, before it.
    from app.crm_core import quote_override_service

    override = quote_override_service.active(db, quote.id)
    priced = override.computed_price if override is not None else total
    discount_amount = round(subtotal - priced, 2) if subtotal - priced > 0.009 else 0.0
    adjustment = round(total - priced, 2) if override is not None and abs(total - priced) > 0.009 else 0.0
    po = quote.purchase_order
    today = utcnow().date()
    return {
        "tenant": tenant, "party": party, "opportunity": opportunity, "currency": currency, "lines": lines, "subtotal": subtotal,
        "discount_amount": discount_amount, "adjustment": adjustment, "total": total, "po": po,
        "values": {"company": tenant.name if tenant else "BRT 360", "customer": party.name if party else "the customer",
                   "quote_number": f"Q-{quote.id:05d}" + (f" rev {quote.revision_number}" if (quote.revision_number or 1) > 1 else ""),
                   "quote_id": quote.id, "total": _money(total, currency), "discount": f"{quote.requested_discount_pct or 0:g}%",
                   "date": today.strftime("%d %b %Y"), "valid_until": (today + dt.timedelta(days=30)).strftime("%d %b %Y"),
                   "po_number": po.po_number if po is not None else "-", "order_id": "-", "agreement_number": "-",
                   "account_manager": (manager.name or manager.email) if manager is not None else "Your account manager"},
    }


def _common(pdf: _Pdf, deal: dict[str, Any], quote, heading: str) -> None:
    party = deal["party"]
    v = deal["values"]
    pdf.facts([("Customer", v["customer"]), ("Quote", v["quote_number"]), ("Date", v["date"]),
               ("Account manager", v["account_manager"]), ("Email", party.email if party else None),
               ("Phone", party.phone if party else None), ("Opportunity", deal["opportunity"].name if deal["opportunity"] else None)])
    pdf.heading(heading)
    cur = deal["currency"]
    pdf.table(["#", "Product", "Options", "Qty", "Unit price", "Amount"],
              [[i + 1, ln["product"], ln["options"] or "-", ln["quantity"], _money(ln["unit_price"], cur), _money(ln["amount"], cur)]
               for i, ln in enumerate(deal["lines"])] or [["-", "No products on the quote", "", "", "", ""]],
              [4, 26, 30, 6, 16, 18], {3, 4, 5})
    rows = [("Subtotal", _money(deal["subtotal"], cur), False)]
    if deal["discount_amount"]:
        rows.append((f"Discount ({quote.requested_discount_pct or 0:g}%)", "- " + _money(deal["discount_amount"], cur), False))
    elif quote.requested_discount_pct:
        rows.append(("Approved discount", f"{quote.requested_discount_pct:g}%", False))
    if deal["adjustment"]:
        sign = "- " if deal["adjustment"] < 0 else "+ "
        rows.append(("Negotiated price adjustment", sign + _money(abs(deal["adjustment"]), cur), False))
    rows.append(("Total (monthly)", _money(deal["total"], cur), True))
    pdf.totals(rows)


def render(db: Session, tenant_id: int, quote, kind: str, extra: dict[str, Any] | None = None) -> tuple[bytes, str]:
    """(PDF bytes, title) of a proposal or an agreement for the quote."""
    deal = _deal(db, tenant_id, quote)
    deal["values"].update({k: v for k, v in (extra or {}).items() if v not in (None, "")})
    tpl = template(db, tenant_id, kind)
    v = deal["values"]
    title = _fill(tpl["title"], v)
    from app.report_design import brand

    pdf = _Pdf(title, f"{v['company']}  ·  {v['quote_number']}  ·  {v['date']}", brand(db, tenant_id))
    if tpl["intro"].strip():
        pdf.paragraph(_fill(tpl["intro"], v))
    _common(pdf, deal, quote, "Services" if kind == "agreement" else "What we propose")
    if kind == "agreement":
        po = deal["po"]
        pdf.heading("Acceptance")
        pdf.facts([("Purchase order", po.po_number if po else None),
                   ("PO amount", _money(po.amount, deal["currency"]) if po is not None and po.amount is not None else None),
                   ("PO received", po.received_at.strftime("%d %b %Y") if po is not None and po.received_at else None),
                   ("Matched by Sales Ops", po.matched_at.strftime("%d %b %Y") if po is not None and po.matched_at else None),
                   ("Order", f"#{v['order_id']}" if v.get("order_id") not in (None, "-") else None),
                   ("Agreement number", v["agreement_number"] if v.get("agreement_number") not in (None, "-") else None)])
    if tpl["terms"].strip():
        pdf.heading("Terms")
        pdf.paragraph(_fill(tpl["terms"], v), 9)
    if tpl["closing"].strip():
        pdf.y -= 6
        pdf.paragraph(_fill(tpl["closing"], v))
    return pdf.bytes(f"{v['company']} - {title} - {v['quote_number']}"), title


# ---------------------------------------------------------------- the documents


def ref(doc: QuoteDocument) -> str:
    return f"QDOC-{doc.id}"


def from_ref(db: Session, tenant_id: int, value: Any) -> QuoteDocument | None:
    m = re.fullmatch(r"QDOC-(\d+)", str(value or "").strip())
    doc = db.get(QuoteDocument, int(m.group(1))) if m else None
    return doc if doc is not None and doc.tenant_id == tenant_id else None


def documents(db: Session, quote_id: int, kind: str | None = None) -> list[QuoteDocument]:
    q = db.query(QuoteDocument).filter(QuoteDocument.quote_id == quote_id)
    if kind:
        q = q.filter(QuoteDocument.kind == kind)
    return q.order_by(QuoteDocument.id.desc()).all()


def latest(db: Session, quote_id: int, kind: str, source: str | None = None) -> QuoteDocument | None:
    return next((d for d in documents(db, quote_id, kind) if source is None or d.source == source), None)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:60] or "document"


def generate(db: Session, tenant_id: int, quote, kind: str, user_id: int | None = None, order_id: int | None = None,
             extra: dict[str, Any] | None = None) -> QuoteDocument:
    content, title = render(db, tenant_id, quote, kind, extra)
    version = len([d for d in documents(db, quote.id, kind) if d.source == "generated"]) + 1
    doc = QuoteDocument(tenant_id=tenant_id, quote_id=quote.id, kind=kind, title=f"{title} v{version}",
                        file_name=f"{_slug(KINDS[kind])}-Q{quote.id:05d}-v{version}.pdf", content_type="application/pdf",
                        content=content, size=len(content), source="generated", version=version, order_id=order_id,
                        note=str((extra or {}).get("note") or "")[:300], created_by_user_id=user_id)
    db.add(doc)
    db.flush()
    return doc


def upload(db: Session, tenant_id: int, quote, kind: str, file_name: str, content_base64: str, user_id: int | None = None,
           title: str = "", note: str = "") -> QuoteDocument:
    if kind not in UPLOAD_KINDS:
        raise ValueError(f"Attach it as one of: {', '.join(UPLOAD_KINDS)}")
    try:
        content = base64.b64decode(str(content_base64 or "").split(",")[-1], validate=False)
    except (ValueError, TypeError):
        raise ValueError("The file could not be read") from None
    if not content:
        raise ValueError("The file is empty")
    if len(content) > MAX_UPLOAD:
        raise ValueError("The file is larger than 10 MB")
    if not content[:1024].lstrip().startswith(b"%PDF"):
        raise ValueError("Only PDF files can be attached to a quote")
    name = re.sub(r"[^\w.() -]+", "_", str(file_name or "document.pdf")).strip()[:180] or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    version = len(documents(db, quote.id, kind)) + 1
    doc = QuoteDocument(tenant_id=tenant_id, quote_id=quote.id, kind=kind, title=(title or f"{KINDS[kind]} - {name}")[:200],
                        file_name=name, content_type="application/pdf", content=content, size=len(content), source="uploaded",
                        version=version, note=str(note or "")[:300], created_by_user_id=user_id)
    db.add(doc)
    db.flush()
    if kind == "purchase_order" and quote.purchase_order is not None and not from_ref(db, tenant_id, quote.purchase_order.document_ref):
        quote.purchase_order.document_ref = ref(doc)  # the PO already recorded with INT-39 now has its PDF
        db.flush()
    return doc


def out(db: Session, doc: QuoteDocument) -> dict[str, Any]:
    from app.models.auth import User

    user = db.get(User, doc.created_by_user_id) if doc.created_by_user_id else None
    return {"id": doc.id, "ref": ref(doc), "quote_id": doc.quote_id, "kind": doc.kind, "kind_label": KINDS.get(doc.kind, doc.kind),
            "title": doc.title, "file_name": doc.file_name, "size": doc.size, "source": doc.source, "version": doc.version,
            "order_id": doc.order_id, "note": doc.note or "", "created_at": doc.created_at.isoformat(timespec="seconds"),
            "created_by": (user.name or user.email) if user is not None else None}


def listing(db: Session, quote_id: int) -> list[dict[str, Any]]:
    return [out(db, d) for d in documents(db, quote_id)]
