"""A business requirements document (BRD) turned into a Process Builder request.

Word (.docx) and plain text (.txt, .md) are read with the standard library
only - a .docx is a zip of XML. A BRD written on the BRT 360 template is read
section by section into the sentences the builder understands (its name,
where it goes on the Functional Map, its stages, approvals, automation,
fields and assignment); any other document is handed over as text, for the
AI model to split into those pieces (process_builder._ai_steps).
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 6000  # what the Process Builder takes in one request
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# The template's sections: (heading, what goes in it, an example line).
SECTIONS: list[tuple[str, str, str]] = [
    ("Process name", "A short name for the process.", "Enterprise deal desk"),
    ("Business function", "Where it goes on the Functional Map: 'New process' or the name of the process it extends (e.g. B2B Sales to Order, Lead to Quote).",
     "B2B Sales to Order"),
    ("Goal", "Why the business needs it - one or two sentences (for the record; nothing is built from it).",
     "Large enterprise deals get a commercial review before the quote goes out."),
    ("Stages", "The stages a record moves through, in order, one per line (leave empty when the process has no stages of its own).",
     "Qualified\nCommercial review\nQuote approved\nProposal sent"),
    ("Approvals", "Who must approve what, one rule per line.",
     "Quotes with discount above 15% need approval from the Regional Sales Head, then the Finance Controller"),
    ("Automation", "What should happen by itself, one per line, starting with when.",
     "When a quote is approved, notify the account manager that the quote is cleared"),
    ("Fields and screens", "Fields to capture and page changes, one per line.",
     "On the quote page add a mandatory field Discount reason"),
    ("Assignment", "Who gets the work, one rule per line.", "Assign all new leads to Inside Sales round robin"),
    ("Integrations and other needs", "Anything outside BRT 360 (other systems, schedules, reports) - assessed, not built.",
     "Push approved orders to the ERP"),
]
_HEADINGS = {h.lower(): h for h, _, _ in SECTIONS}
_HEADING = re.compile(r"^\s*(?:\d+[.)]\s*|#+\s*)?(?P<h>[A-Za-z][A-Za-z &/]+?)\s*:?\s*$")
_GUIDANCE = re.compile(r"^\s*(?:guidance|e\.g\.|example)\s*[:.]", re.I)


class BrdError(ValueError):
    pass


def read(filename: str, data: bytes) -> str:
    """The document's text, one paragraph (or table row) per line."""
    if len(data) > MAX_BYTES:
        raise BrdError("The document is over 2 MB - trim it or paste the process in")
    name = filename.lower()
    if name.endswith((".txt", ".md")):
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
    if name.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if z.getinfo("word/document.xml").file_size > 10 * MAX_BYTES:  # a zip that unpacks into far more than it says
                    raise BrdError("The document's text is too large to read - paste the process in instead")
                root = ElementTree.fromstring(z.read("word/document.xml"))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise BrdError("That isn't a Word document this can read - save it as .docx again, or as text") from exc
        lines = []
        body = root.find(f"{_W}body")
        for block in list(body) if body is not None else []:
            if block.tag == f"{_W}p":
                lines.append(_text(block))
            elif block.tag == f"{_W}tbl":
                for row in block.iter(f"{_W}tr"):
                    cells = [" ".join(_text(p) for p in cell.iter(f"{_W}p")).strip() for cell in row.iter(f"{_W}tc")]
                    lines.append(" | ".join(c for c in cells if c))
        return "\n".join(lines)
    raise BrdError("Upload a Word document (.docx) or a text file (.txt, .md)")


def _text(paragraph) -> str:
    return "".join(t.text or "" for t in paragraph.iter(f"{_W}t")).strip()


def _sections(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip(" \t-•*")
        if not line:
            continue
        m = _HEADING.match(line)
        if m and m.group("h").strip().lower() in _HEADINGS:
            current = _HEADINGS[m.group("h").strip().lower()]
            found.setdefault(current, [])
            continue
        if current and not _GUIDANCE.match(line):
            found[current].append(line)
    return found


def to_request(text: str, processes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """{request, name, placement, template, sections}: the builder's input from
    a BRD. On the template (3 sections or more) the sections become the
    builder's sentences; otherwise the text goes over as it is."""
    sections = _sections(text)
    if len(sections) < 3:
        body = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(body) < 5:
            raise BrdError("The document has no text in it")
        return {"request": body[:MAX_TEXT], "name": None, "placement": None, "template": False, "sections": {},
                "truncated": len(body) > MAX_TEXT}
    name = (sections.get("Process name") or [""])[0][:150] or None
    parts: list[str] = []
    stages = [s for s in sections.get("Stages", []) if s]
    if len(stages) >= 2:
        parts.append(f"{name or 'New process'}: " + " -> ".join(stages))
    for heading in ("Approvals", "Automation", "Fields and screens", "Assignment", "Integrations and other needs"):
        parts += [s.rstrip(".") + "." for s in sections.get(heading, [])]
    if not parts:
        raise BrdError("The template's Stages, Approvals, Automation, Fields, Assignment sections are all empty")
    placement = None
    where = " ".join(sections.get("Business function", [])).strip()
    if where:
        known = {p["label"].lower(): p["key"] for p in processes or []}
        key = known.get(where.lower()) or next((k for label, k in known.items() if label in where.lower() or where.lower() in label), None)
        placement = {"mode": "append", "process_key": key} if key and not re.match(r"^\s*new\b", where, re.I) else {"mode": "new", "label": name or where}
    request = "\n".join(parts)
    return {"request": request[:MAX_TEXT], "name": name, "placement": placement, "template": True,
            "sections": {k: v for k, v in sections.items()}, "truncated": len(request) > MAX_TEXT}


# ---------------------------------------------------------------- the template


def _para(text: str, style: str | None = None, italic: bool = False) -> str:
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    run_props = "<w:rPr><w:i/><w:color w:val=\"6B7280\"/></w:rPr>" if italic else ""
    return f'<w:p>{props}<w:r>{run_props}<w:t xml:space="preserve">{esc}</w:t></w:r></w:p>'


def template_docx() -> bytes:
    """The BRD template as a Word document: one heading per section, a guidance
    line (dropped when it is read back) and an example to overwrite."""
    body = [_para("Business Requirements - New Process", "Title"),
            _para("Guidance: fill each section, one item per line. Delete the examples you don't need. Upload it in BRT 360: "
                  "Intent Command Bar -> Build a process -> Upload a BRD.", italic=True)]
    for heading, guidance, example in SECTIONS:
        body.append(_para(heading, "Heading1"))
        body.append(_para(f"Guidance: {guidance}", italic=True))
        body += [_para(line) for line in example.split("\n")]
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(body) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
              '<w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
              '<w:pPr><w:keepNext/><w:spacing w:before="320" w:after="80"/><w:outlineLvl w:val="0"/></w:pPr>'
              '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style></w:styles>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles)
    return out.getvalue()
