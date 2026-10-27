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

The PDF writer needs no extra package: plain PDF 1.4 with the standard
Helvetica fonts.
"""

from __future__ import annotations

import base64
import datetime as dt
import io
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

_PAGE_W, _PAGE_H, _MARGIN = 595, 842, 50
_ACCENT = (0.05, 0.36, 0.55)
_MUTED = (0.42, 0.45, 0.5)
_TEXT = (0.1, 0.12, 0.16)


def _enc(value: Any) -> str:
    text = str(value if value is not None else "")
    text = text.replace("₹", "INR ").replace("→", "->").replace("≤", "<=").replace("≥", ">=")
    text = text.encode("cp1252", "replace").decode("cp1252")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _width(text: str, size: float, bold: bool = False) -> float:
    """Close enough to Helvetica's metrics to wrap and right-align."""
    narrow, wide = set("iljtfI.,;:'!|() "), set("mwMW@%")
    units = sum(0.28 if c in narrow else 0.83 if c in wide else 0.62 if c.isupper() or c.isdigit() else 0.52 for c in text)
    return units * size * (1.06 if bold else 1.0)


def _wrap(text: str, width: float, size: float, bold: bool = False) -> list[str]:
    """The text in lines that fit the width - a word longer than a line is cut."""
    lines, line = [], ""
    for word in text.split(" "):
        while _width(word, size, bold) > width and len(word) > 1:  # e.g. a long reference with no spaces
            cut = max(1, int(len(word) * width / _width(word, size, bold)) - 1)
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:cut])
            word = word[cut:]
        trial = f"{line} {word}".strip()
        if _width(trial, size, bold) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line or not lines else lines


class _Pdf:
    def __init__(self, title: str, subtitle: str):
        self.title, self.subtitle = title, subtitle
        self.pages: list[list[str]] = []
        self.y = 0.0
        self._page()

    # -- drawing
    def _page(self) -> None:
        self.ops: list[str] = []
        self.pages.append(self.ops)
        self.rect(0, _PAGE_H - 70, _PAGE_W, 70, _ACCENT)
        self.text(_MARGIN, _PAGE_H - 38, self.title, 18, True, (1, 1, 1))
        self.text(_MARGIN, _PAGE_H - 56, self.subtitle, 9, False, (0.85, 0.92, 0.97))
        self.y = _PAGE_H - 100

    def rect(self, x: float, y: float, w: float, h: float, rgb: tuple[float, float, float]) -> None:
        self.ops.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")

    def line(self, x1: float, y1: float, x2: float, y2: float, rgb=(0.82, 0.85, 0.88)) -> None:
        self.ops.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} RG 0.6 w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    def text(self, x: float, y: float, value: Any, size: float = 10, bold: bool = False, rgb=_TEXT) -> None:
        self.ops.append(f"BT {rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg /{'F2' if bold else 'F1'} {size} Tf "
                        f"{x:.1f} {y:.1f} Td ({_enc(value)}) Tj ET")

    def need(self, height: float) -> None:
        if self.y - height < 60:
            self._page()

    # -- blocks
    def heading(self, value: str) -> None:
        self.need(34)
        self.y -= 10
        self.text(_MARGIN, self.y, value, 12, True, _ACCENT)
        self.y -= 6
        self.line(_MARGIN, self.y, _PAGE_W - _MARGIN, self.y)
        self.y -= 14

    def paragraph(self, value: str, size: float = 10, rgb=_TEXT) -> None:
        for raw in str(value or "").split("\n"):
            if not raw.strip():
                self.y -= size * 0.7
                continue
            for line in _wrap(raw, _PAGE_W - 2 * _MARGIN, size):
                self.need(size + 4)
                self.text(_MARGIN, self.y, line, size, False, rgb)
                self.y -= size + 4
        self.y -= 4

    def facts(self, rows: list[tuple[str, Any]]) -> None:
        """Two columns of label / value pairs."""
        rows = [(k, v) for k, v in rows if v not in (None, "")]
        half = (len(rows) + 1) // 2
        col_w = (_PAGE_W - 2 * _MARGIN) / 2
        for i in range(half):
            self.need(28)
            for j, (label, value) in enumerate(rows[i::half][:2] if half else []):
                x = _MARGIN + j * col_w
                self.text(x, self.y, label.upper(), 7, True, _MUTED)
                self.text(x, self.y - 12, value, 10)
            self.y -= 28
        self.y -= 2

    def table(self, headers: list[str], rows: list[list[Any]], widths: list[float], right: set[int]) -> None:
        total = _PAGE_W - 2 * _MARGIN
        scale = total / sum(widths)
        widths = [w * scale for w in widths]

        def row(cells: list[Any], bold: bool, shade=None, size: float = 9) -> None:
            wrapped = [_wrap(str(cell if cell is not None else ""), widths[i] - 8, size, bold) for i, cell in enumerate(cells)]
            height = 7 + 11 * max(len(w) for w in wrapped)
            self.need(height + 2)
            if shade:
                self.rect(_MARGIN, self.y - height + 12, total, height, shade)
            x = _MARGIN
            for i, lines in enumerate(wrapped):
                for n, value in enumerate(lines):
                    tx = x + widths[i] - 4 - _width(value, size, bold) if i in right else x + 4
                    self.text(tx, self.y - n * 11, value, size, bold, (1, 1, 1) if shade == _ACCENT else _TEXT)
                x += widths[i]
            self.y -= height

        row(headers, True, _ACCENT)
        for n, cells in enumerate(rows):
            row(cells, False, (0.95, 0.96, 0.98) if n % 2 else None)
        self.y -= 4

    def totals(self, rows: list[tuple[str, str, bool]]) -> None:
        for label, value, strong in rows:
            self.need(18)
            size = 11 if strong else 9.5
            right = _PAGE_W - _MARGIN - 4
            self.text(right - 170, self.y, label, size, strong, _TEXT if strong else _MUTED)
            self.text(right - _width(value, size, strong), self.y, value, size, strong)
            self.y -= 16
        self.y -= 6

    # -- output
    def bytes(self, footer: str) -> bytes:
        count = len(self.pages)
        for n, ops in enumerate(self.pages, start=1):
            self.ops = ops
            self.line(_MARGIN, 44, _PAGE_W - _MARGIN, 44)
            self.text(_MARGIN, 32, footer, 7.5, False, _MUTED)
            label = f"Page {n} of {count}"
            self.text(_PAGE_W - _MARGIN - _width(label, 7.5), 32, label, 7.5, False, _MUTED)
        objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
                                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
                                f"<< /Producer (BRT 360) /Title ({_enc(self.title)}) >>".encode("cp1252", "replace")]
        kids = []
        for ops in self.pages:
            stream = "\n".join(ops).encode("cp1252", "replace")
            page_id = len(objects) + 1
            objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] /Resources << /Font << /F1 3 0 R "
                           f"/F2 4 0 R >> >> /Contents {page_id + 1} 0 R >>".encode())
            objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
            kids.append(f"{page_id} 0 R")
        objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {count} >>".encode()
        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, obj in enumerate(objects, start=1):
            offsets.append(out.tell())
            out.write(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = out.tell()
        out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
        for offset in offsets:
            out.write(f"{offset:010d} 00000 n \n".encode())
        out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
        return out.getvalue()


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
    pdf = _Pdf(title, f"{v['company']}  ·  {v['quote_number']}  ·  {v['date']}")
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
