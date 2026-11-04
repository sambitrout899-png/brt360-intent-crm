"""Admin -> Report Design: the organization's logo, colours, header, footer and watermark on every PDF report."""

import base64
import struct
import zlib

from app import documents as d
from app.models.tenant import Tenant


def _png(width=40, height=12, alpha=True) -> bytes:
    """A real PNG - RGBA rows filtered with None / Sub / Up alternately (the decoder undoes each)."""
    channels = 4 if alpha else 3
    raw = b""
    prev = bytes(width * channels)
    for y in range(height):
        row = bytes((x * 7 + y * 13 + c * 50) % 256 if c < 3 else (255 if x > 5 else 0) for x in range(width) for c in range(channels))
        kind = y % 3
        if kind == 1:
            row_f = bytes((row[i] - (row[i - channels] if i >= channels else 0)) % 256 for i in range(len(row)))
        elif kind == 2:
            row_f = bytes((row[i] - prev[i]) % 256 for i in range(len(row)))
        else:
            row_f = row
        raw += bytes([kind]) + row_f
        prev = row

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _url(data: bytes, kind="png") -> str:
    return f"data:image/{kind};base64," + base64.b64encode(data).decode()


def test_logo_decoding():
    rgba = d.image(_png())
    assert (rgba.width, rgba.height, rgba.smask is not None) == (40, 12, True)
    colour, alpha = zlib.decompress(rgba.data), zlib.decompress(rgba.smask)
    assert len(colour) == 40 * 12 * 3 and alpha[:6] == bytes(6) and alpha[6] == 255
    assert colour[3 * 41:3 * 41 + 3] == bytes(((1 * 7 + 1 * 13 + c * 50) % 256) for c in range(3))  # row 1 (Sub) pixel 1
    assert colour[3 * 40 * 2 + 3:3 * 40 * 2 + 6] == bytes(((7 + 26 + c * 50) % 256) for c in range(3))  # row 2 (Up) pixel 1
    rgb = d.image(_png(alpha=False))
    assert rgb.smask is None and "/Predictor 15" in rgb.parms  # no transparency: passed through as it is
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xc0\x00\x11\x08\x00\x20\x00\x50\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9"
    assert (d.image(jpeg).width, d.image(jpeg).height, d.image(jpeg).filters) == (80, 32, "/DCTDecode")
    for bad in (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", b"GIF89a....", b"hello"):
        try:
            d.image(bad)
            raise AssertionError("accepted")
        except ValueError:
            pass


def test_admin_designs_the_reports_and_every_pdf_follows(client):
    design = client.get("/api/admin/report-design").json()
    assert design["settings"]["header_style"] == "band" and not design["customized"]
    assert design["effective"]["accent"] == "#" + d.ACCENT_HEX.lower()

    # checked: colour, logo type, a custom logo that isn't there
    assert client.put("/api/admin/report-design", json={"accent": "red"}).status_code == 400
    assert client.put("/api/admin/report-design", json={"logo": "custom"}).status_code == 400
    svg = client.put("/api/admin/report-design", json={"logo": "custom", "logo_data_url": "data:image/svg+xml;base64,PHN2Zy8+"})
    assert svg.status_code == 400 and "PNG" in svg.text

    # the preview: unsaved settings, a sample report
    preview = client.post("/api/admin/report-design/preview", json={"logo": "custom", "logo_data_url": _url(_png()), "watermark": "DRAFT",
                                                                  "company_name": "Acme Telecom"})
    assert preview.status_code == 200 and preview.content[:5] == b"%PDF-"
    assert b"/Logo" in preview.content and b"/SMask" in preview.content and b"(DRAFT)" in preview.content and b"Acme Telecom" in preview.content
    assert not client.get("/api/admin/report-design").json()["customized"]  # nothing saved by a preview

    saved = client.put("/api/admin/report-design", json={
        "logo": "custom", "logo_data_url": _url(_png()), "logo_position": "right", "accent": "#c8102e", "header_style": "tint",
        "company_name": "Acme Telecom", "tagline": "acme.example", "footer_text": "{company} | {title}",
        "confidentiality": "Confidential - internal", "watermark": "INTERNAL", "show_generated": False, "page_size": "Letter"})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["customized"] and body["effective"] == {"company": "Acme Telecom", "accent": "#c8102e", "logo": True, "logo_note": "Shown"}

    # a real report - the audit trail export - carries it
    pdf = client.get("/api/audit/feed/export?format=pdf").content
    assert pdf[:5] == b"%PDF-" and b"/Logo" in pdf and b"Acme Telecom | Audit trail" in pdf and b"(INTERNAL)" in pdf
    assert b"Confidential - internal" in pdf and b"/MediaBox [0 0 612 792]" in pdf and b"Generated" not in pdf
    assert b"0.784 0.063 0.180 rg" in pdf  # the accent

    # back to the standard look
    reset = client.delete("/api/admin/report-design").json()
    assert not reset["customized"] and reset["settings"]["logo"] == "app"
    pdf = client.get("/api/audit/feed/export?format=pdf").content
    assert b"/Logo" not in pdf and b"/MediaBox [0 0 595 842]" in pdf


def test_app_logo_is_used_and_a_bad_one_never_stops_a_report(client):
    with client.TestSessionLocal() as db:
        tenant = db.query(Tenant).first()
        tenant.logo_data_url = _url(_png())
        tenant.brand_color = "#0f766e"
        db.commit()
    design = client.get("/api/admin/report-design").json()
    assert design["effective"]["logo"] and design["effective"]["accent"] == "#0f766e"
    assert b"/Logo" in client.post("/api/admin/report-design/preview").content
    with client.TestSessionLocal() as db:
        db.query(Tenant).first().logo_data_url = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
        db.commit()
    design = client.get("/api/admin/report-design").json()
    assert not design["effective"]["logo"] and design["effective"]["logo_note"].startswith("Not shown")
    pdf = client.get("/api/audit/feed/export?format=pdf")
    assert pdf.status_code == 200 and b"/Logo" not in pdf.content


def test_every_organization_has_report_design_whatever_its_modules(client):
    with client.TestSessionLocal() as db:
        db.query(Tenant).first().licensed_modules = ["training_management"]
        db.commit()
    assert client.get("/api/admin/report-design").status_code == 200
    assert client.put("/api/admin/report-design", json={"watermark": "DRAFT"}).status_code == 200
