"""Report design - how a tenant's PDF reports look (Admin -> Report Design).

One setting per tenant (tenants.report_design, JSON) that every PDF the platform generates reads through brand():
the logo (the app's own, one just for reports, or none) and where it sits, the accent colour (blank = the app's
brand colour, else the BRT 360 blue), the header style, the company name and a tagline (address, website ...),
the footer (placeholders {company} {title} {date}), a confidentiality line, a watermark (DRAFT ...), the
"Generated ..." stamp and the page size.

    brand(db, tenant_id)          the documents.Brand for Pdf(..., brand=...) - never fails: a bad logo is left out
    describe(db, tenant_id)       the settings as saved, what they come to, and whether the logo can be used
    clean(payload)                validated settings (ValueError with the reason)
    sample(brand)                 a two-page preview report
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from sqlalchemy.orm import Session

from app import documents as d
from app.models.tenant import Tenant

MAX_LOGO_BYTES = 512 * 1024
LOGO_SOURCES = ("app", "custom", "none")
DEFAULTS: dict[str, Any] = {
    "logo": "app",              # app = the logo in the app header (platform admin sets it) | custom = logo_data_url | none
    "logo_data_url": None,
    "logo_position": "left",    # left | right
    "accent": "",               # #rrggbb; blank = the app's brand colour, else the BRT 360 blue
    "header_style": "band",     # band | tint | clean
    "company_name": "",         # blank = the organization's display name
    "tagline": "",
    "footer_text": "",          # blank = "company · report title"
    "confidentiality": "",
    "watermark": "",
    "show_generated": True,
    "page_size": "A4",          # A4 | Letter
}
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg);base64,([A-Za-z0-9+/=\s]+)$")


def clean(payload: dict[str, Any]) -> dict[str, Any]:
    """The settings to save: known keys only, each checked; a key left out keeps its default."""
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in payload and payload[key] is not None:
            out[key] = payload[key]
    if out["logo"] not in LOGO_SOURCES:
        raise ValueError("logo must be app, custom or none")
    if out["logo_position"] not in ("left", "right"):
        raise ValueError("logo_position must be left or right")
    if out["header_style"] not in d.HEADER_STYLES:
        raise ValueError(f"header_style must be one of {', '.join(d.HEADER_STYLES)}")
    if out["page_size"] not in d.PAGE_SIZES:
        raise ValueError(f"page_size must be one of {', '.join(d.PAGE_SIZES)}")
    out["accent"] = str(out["accent"] or "").strip()
    if out["accent"] and not _COLOUR.match(out["accent"]):
        raise ValueError("accent must look like #0d5c8c")
    out["show_generated"] = bool(out["show_generated"])
    for key, limit in (("company_name", 120), ("tagline", 160), ("footer_text", 200), ("confidentiality", 200), ("watermark", 24)):
        out[key] = str(out[key] or "").strip()
        if len(out[key]) > limit:
            raise ValueError(f"{key.replace('_', ' ')} is longer than {limit} characters")
    if out["logo_data_url"]:
        out["logo_data_url"] = _check_logo(str(out["logo_data_url"]))
    else:
        out["logo_data_url"] = None
    if out["logo"] == "custom" and not out["logo_data_url"]:
        raise ValueError("Upload the report logo (PNG or JPEG), or choose the app's logo or no logo")
    return out


def _check_logo(data_url: str) -> str:
    match = _DATA_URL.match(data_url.strip())
    if match is None:
        raise ValueError("The report logo must be a PNG or JPEG image")
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise ValueError("The report logo could not be read")
    if len(raw) > MAX_LOGO_BYTES:
        raise ValueError(f"The report logo is {len(raw) // 1024} KB - the limit is {MAX_LOGO_BYTES // 1024} KB")
    try:
        d.image(raw)  # with the reason if a PDF can't show it
    except ValueError:
        raise
    except Exception:
        raise ValueError("The report logo could not be read - save it again as a PNG or JPEG")
    return data_url.strip()


def settings(tenant: Tenant | None) -> dict[str, Any]:
    saved = (tenant.report_design if tenant is not None else None) or {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def _logo_url(tenant: Tenant | None, s: dict[str, Any]) -> str | None:
    if s["logo"] == "custom":
        return s["logo_data_url"]
    if s["logo"] == "app" and tenant is not None:
        return tenant.logo_data_url
    return None


def _logo(tenant: Tenant | None, s: dict[str, Any]) -> tuple[d.Image | None, str]:
    """(the logo, a note on it) - a logo a PDF can't show is left out, with the reason."""
    url = _logo_url(tenant, s)
    if s["logo"] == "none":
        return None, "No logo"
    if not url:
        return None, "The organization has no app logo yet - upload one for reports here, or ask the platform administrator"
    try:
        return d.image_from_data_url(url), "Shown"
    except Exception as exc:  # a damaged image: the report comes out without it
        return None, f"Not shown: {exc}"


def _brand_of(tenant: Tenant | None, s: dict[str, Any]) -> d.Brand:
    logo, _ = _logo(tenant, s)
    accent = s["accent"] or (tenant.brand_color if tenant is not None else "") or ""
    company = s["company_name"] or (tenant.display_name or tenant.name if tenant is not None else "") or "BRT 360"
    return d.Brand(company=company, accent=d.rgb_of(accent), logo=logo, header=s["header_style"],
                   logo_position=s["logo_position"], tagline=s["tagline"], footer=s["footer_text"],
                   confidentiality=s["confidentiality"], watermark=s["watermark"], generated=s["show_generated"],
                   page_size=s["page_size"])


def brand(db: Session, tenant_id: int | None, override: dict[str, Any] | None = None) -> d.Brand:
    """The tenant's look for a PDF. override = unsaved settings (the preview). Never raises: reports must still come out."""
    try:
        tenant = db.get(Tenant, tenant_id) if tenant_id is not None else None
        s = clean(override) if override is not None else settings(tenant)
        return _brand_of(tenant, s)
    except ValueError:
        raise
    except Exception:  # pragma: no cover - a broken setting must never stop a report
        return d.Brand()


def describe(db: Session, tenant_id: int) -> dict[str, Any]:
    tenant = db.get(Tenant, tenant_id)
    s = settings(tenant)
    logo, logo_note = _logo(tenant, s)
    b = _brand_of(tenant, s)
    return {
        "settings": s,
        "customized": bool(tenant is not None and tenant.report_design),
        "app_logo_data_url": tenant.logo_data_url if tenant is not None else None,
        "app_brand_color": tenant.brand_color if tenant is not None else None,
        "organization": (tenant.display_name or tenant.name) if tenant is not None else "",
        "effective": {"company": b.company, "accent": "#" + d.hex_of(b.accent).lower(), "logo": logo is not None, "logo_note": logo_note},
        "header_styles": [{"key": "band", "label": "Colour band"}, {"key": "tint", "label": "Light tint"}, {"key": "clean", "label": "Clean (white)"}],
        "page_sizes": list(d.PAGE_SIZES),
        "placeholders": ["{company}", "{title}", "{date}"],
    }


def save(db: Session, tenant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("Organization not found")
    tenant.report_design = clean(payload)
    db.commit()
    return describe(db, tenant_id)


def reset(db: Session, tenant_id: int) -> dict[str, Any]:
    tenant = db.get(Tenant, tenant_id)
    if tenant is not None:
        tenant.report_design = None
        db.commit()
    return describe(db, tenant_id)


def sample(b: d.Brand) -> bytes:
    """A preview: what a report looks like with these settings - first page and a continuation page."""
    doc = d.Pdf("Monthly service report", "Sample report · how your PDF reports will look", b)
    doc.tiles([("Orders", "1,284", None), ("Completed", "1,197", d.STATUS["passed"]), ("Failed", "12", d.STATUS["failed"]),
               ("On time", "96%", None)])
    doc.heading("Summary")
    doc.paragraph("Every PDF the platform produces - test evidence, bulk order results, analytics and audit exports, "
                  "training reports, proposals and agreements - uses this design: your logo, your colours, your header "
                  "and footer.")
    doc.note("Headings, table header rows, call-outs and rules all take the accent colour.")
    doc.facts([("Prepared for", "Operations leadership"), ("Period", "October 2026"), ("Prepared by", b.company),
               ("Classification", b.confidentiality or "-")])
    doc.heading("Orders by region")
    regions = ["North", "South", "East", "West", "Central"]
    rows = [[f"ORD-{4100 + i}", regions[i % 5], ["Fibre 500", "Mobile 40GB", "SD-WAN", "Voice bundle"][i % 4],
             f"{(i * 37) % 900 + 100:,}", ["passed", "passed", "failed", "skipped", "passed"][i % 5]] for i in range(46)]
    doc.table(["Order", "Region", "Product", "Value", "Status"], rows, widths=[1.2, 1, 1.6, 0.8, 0.8], right={3}, status_col=4)
    doc.subheading("Next steps")
    doc.bullets(["Review the failed orders with the fulfilment team", "Share the report with the regional leads"])
    return doc.bytes()
