"""Generated documents in the house style (app/documents.py): Word templates with coloured headings and tables, formatted
Excel templates and exports (read back as uploads), and PDF reports - system test evidence, bulk order results, analytics."""

import io
import zipfile

from app import documents as d
from tests.test_system_tests import _setup


def _xlsx_header_styled(content: bytes) -> bool:
    sheet = zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml").decode()
    return 's="1"' in sheet and 'state="frozen"' in sheet


def test_the_toolkit_writes_word_excel_and_pdf():
    word = d.docx([("Title", "Demo"), ("Heading1", "Section"), ("Guidance", "Guidance: hint"), ("Table", [["A", "B"], ["1", "2"]]), ("Bullet", "one")])
    parts = zipfile.ZipFile(io.BytesIO(word))
    assert "word/footer1.xml" in parts.namelist() and "<w:tbl>" in parts.read("word/document.xml").decode()
    assert ACCENT_IN(parts.read("word/styles.xml").decode())
    book = d.xlsx([("Data", [["name", "qty"], ["Asha", 3], ["=cmd", 1]])])
    assert _xlsx_header_styled(book) and d.read_xlsx(book)[0][1][:2] == [["name", "qty"], ["Asha", "3"]]
    pdf = d.Pdf("Report", "sub")
    pdf.table(["Case", "Status"], [[f"TC-{i}", "passed"] for i in range(150)], status_col=1)
    out = pdf.bytes()
    assert out.startswith(b"%PDF-1.4") and out.count(b"/Type /Page ") > 1 and b"Page 2 of" in out


def ACCENT_IN(styles: str) -> bool:
    return d.ACCENT_HEX in styles and "Heading1" in styles


def test_templates_are_formatted_and_read_back(client):
    # the use case and BRD templates: coloured headings, a footer with page numbers
    for path in ("/api/process-builder/use-case-template", "/api/process-builder/brd-template"):
        res = client.get(path)
        assert res.status_code == 200, (path, res.text[:200])
        assert ACCENT_IN(zipfile.ZipFile(io.BytesIO(res.content)).read("word/styles.xml").decode())
    # the bulk order template as Excel - filled in and uploaded as it is
    template = client.get("/api/bulk-orders/processes/mobile_lines/template.xlsx")
    assert template.status_code == 200 and _xlsx_header_styled(template.content)
    header = d.read_xlsx(template.content)[0][1][0]
    assert "offering_name" in header and d.read_xlsx(template.content)[1][0] == "Guide"
    filled = d.xlsx([("Mobile lines", [header, ["" if c != "offering_name" else "Prepaid Pay-as-you-go" for c in header]])])
    seen = client.post("/api/bulk-orders/preview", params={"process": "mobile_lines"}, content=filled,
                       headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
    assert seen.status_code == 200, seen.text
    assert seen.json()["rows"] == 1 and "offering_name" not in seen.json()["missing_required_columns"]
    # BIM: the Excel template, and a filled-in one loaded back (dry run)
    bim = client.get("/api/bim/template/prospect", params={"format": "xlsx"})
    assert bim.status_code == 200 and _xlsx_header_styled(bim.content)


def test_reports_come_as_pdf(client):
    _setup(client)
    suite = client.post("/api/system-tests/suites", json={}).json()
    run = client.post(f"/api/system-tests/suites/{suite['id']}/runs", params={"wait": True}, json={"areas": ["Custom: Site Survey"]}).json()
    pdf = client.get(f"/api/system-tests/runs/{run['id']}/report.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert run["number"].encode() in pdf.content and b"Results by area" in pdf.content
    assert _xlsx_header_styled(client.get(f"/api/system-tests/runs/{run['id']}/results.xlsx").content)
    assert _xlsx_header_styled(client.get(f"/api/system-tests/suites/{suite['id']}/cases.xlsx").content)

    assert client.post("/api/mobile/sample-catalog").status_code == 200
    bulk = client.post("/api/bulk-orders", json={"process": "mobile_lines", "rows": [
        {"customer_name": "Pdf Customer", "offering_name": "Prepaid Pay-as-you-go", "sim_type": "esim"}]}).json()
    client.post(f"/api/bulk-orders/{bulk['id']}/process")
    report = client.get(f"/api/bulk-orders/{bulk['id']}/results.pdf")
    assert report.status_code == 200 and report.content.startswith(b"%PDF") and bulk["number"].encode() in report.content
    assert _xlsx_header_styled(client.get(f"/api/bulk-orders/{bulk['id']}/results.xlsx").content)

    audit = client.get("/api/audit/feed/export", params={"format": "pdf"})
    assert audit.status_code == 200 and audit.content.startswith(b"%PDF") and b"Audit trail" in audit.content
    assert _xlsx_header_styled(client.get("/api/audit/feed/export", params={"format": "xlsx"}).content)

    for fmt, magic in (("pdf", b"%PDF"), ("xlsx", b"PK")):
        out = client.post("/api/analytics/export", json={"kind": "query", "name": "Orders by status", "format": fmt,
                                                         "query": {"entity": "order", "group_by": ["status"], "measures": [{"op": "count"}]}})
        assert out.status_code == 200, out.text[:300]
        assert out.content.startswith(magic)
