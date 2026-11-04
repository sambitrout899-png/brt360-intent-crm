"""Every document the platform generates - Word, Excel and PDF - in one house style, with the standard library only.

    docx(blocks, title)   Word: a coloured title band, coloured headings, shaded guidance and note boxes, bullet lists,
                          styled tables (coloured header row, banded rows) and a footer "BRT 360 · title · page X of Y".
    xlsx(sheets)          Excel: a coloured, bold, frozen header row with filters, banded rows, borders and column widths
                          that fit - the first row stays the header, so a template filled in is read back as it is.
    read_xlsx(content)    the sheets of an .xlsx as rows of text (templates come back as uploads).
    Pdf(title, subtitle)  PDF 1.4 (Helvetica / Courier): a coloured banner on every page, headings, paragraphs, facts,
                          summary tiles, bullet lists, tables with a coloured header row, banded rows and status colours,
                          code blocks, totals, and a footer with page X of Y.

One palette (ACCENT ...) so a proposal, a test report and a template look like they come from the same product.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Iterable
from xml.sax.saxutils import escape

# ---------------------------------------------------------------- the house style

ACCENT = (0.05, 0.36, 0.55)  # the BRT 360 blue of banners, headings and header rows
ACCENT_DARK = (0.04, 0.24, 0.38)
ACCENT_LIGHT = (0.90, 0.94, 0.97)
TEXT = (0.1, 0.12, 0.16)
MUTED = (0.42, 0.45, 0.5)
RULE = (0.82, 0.85, 0.88)
BAND = (0.95, 0.96, 0.98)
STATUS = {"passed": (0.06, 0.54, 0.37), "failed": (0.76, 0.22, 0.17), "error": (0.61, 0.17, 0.55), "skipped": (0.6, 0.45, 0.04),
          "completed": (0.06, 0.54, 0.37), "running": (0.05, 0.36, 0.55), "cancelled": MUTED, "invalid": (0.76, 0.22, 0.17)}


def hex_of(rgb: tuple[float, float, float]) -> str:
    return "".join(f"{round(c * 255):02X}" for c in rgb)


ACCENT_HEX, ACCENT_DARK_HEX, ACCENT_LIGHT_HEX, MUTED_HEX, BAND_HEX = (hex_of(c) for c in (ACCENT, ACCENT_DARK, ACCENT_LIGHT, MUTED, BAND))


# ---------------------------------------------------------------- Word

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _x(text: Any) -> str:
    return escape(str(text if text is not None else ""))


def _run(text: str, props: str = "") -> str:
    parts = str(text).split("\n")
    body = '<w:br/>'.join(f'<w:t xml:space="preserve">{_x(p)}</w:t>' for p in parts)
    return f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}{body}</w:r>"


def _p(text: str, style: str | None = None, props: str = "", run_props: str = "") -> str:
    ppr = (f'<w:pStyle w:val="{style}"/>' if style else "") + props
    return f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{_run(text, run_props)}</w:p>"


def _table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    width = 9000 // max(cols, 1)
    border = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="C9D2DC"/>' for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{border}</w:tblBorders>'
           '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="60" w:type="dxa"/>'
           '<w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>' + f'<w:gridCol w:w="{width}"/>' * cols + "</w:tblGrid>"]
    for n, row in enumerate(rows):
        head = n == 0
        fill = ACCENT_HEX if head else (BAND_HEX if n % 2 == 0 else "FFFFFF")
        cells = []
        tight = '<w:spacing w:before="0" w:after="0"/>'
        for c in range(cols):
            value = row[c] if c < len(row) else ""
            run_props = '<w:b/><w:color w:val="FFFFFF"/><w:sz w:val="20"/>' if head else '<w:sz w:val="20"/>'
            para = _p(str(value), None, tight, run_props)
            cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/><w:shd w:val="clear" w:color="auto" w:fill="{fill}"/></w:tcPr>{para}</w:tc>')
        out.append(f'<w:tr>{"<w:trPr><w:tblHeader/></w:trPr>" if head else ""}{"".join(cells)}</w:tr>')
    out.append("</w:tbl>")
    return "".join(out) + _p("", props='<w:spacing w:before="0" w:after="80"/>')


_DOCX_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {_W}>
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="22"/>
<w:color w:val="1A1F29"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:shd w:val="clear" w:color="auto" w:fill="{ACCENT_HEX}"/>
<w:pBdr><w:top w:val="single" w:sz="48" w:space="8" w:color="{ACCENT_HEX}"/><w:left w:val="single" w:sz="48" w:space="8" w:color="{ACCENT_HEX}"/>
<w:bottom w:val="single" w:sz="48" w:space="8" w:color="{ACCENT_HEX}"/><w:right w:val="single" w:sz="48" w:space="8" w:color="{ACCENT_HEX}"/></w:pBdr>
<w:spacing w:before="0" w:after="320"/></w:pPr><w:rPr><w:b/><w:color w:val="FFFFFF"/><w:sz w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:pPr><w:shd w:val="clear" w:color="auto" w:fill="{ACCENT_HEX}"/>
<w:spacing w:before="0" w:after="240"/></w:pPr><w:rPr><w:color w:val="DCEBF5"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="320" w:after="120"/><w:pBdr><w:bottom w:val="single" w:sz="12" w:space="2" w:color="{ACCENT_HEX}"/></w:pBdr>
<w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="{ACCENT_DARK_HEX}"/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="220" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="{ACCENT_HEX}"/><w:sz w:val="25"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="160" w:after="60"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:color w:val="{MUTED_HEX}"/><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Guidance"><w:name w:val="Guidance"/><w:basedOn w:val="Normal"/><w:pPr><w:shd w:val="clear" w:color="auto" w:fill="{ACCENT_LIGHT_HEX}"/>
<w:pBdr><w:left w:val="single" w:sz="24" w:space="6" w:color="{ACCENT_HEX}"/></w:pBdr><w:ind w:left="120"/><w:spacing w:before="60" w:after="120"/></w:pPr>
<w:rPr><w:i/><w:color w:val="44505F"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/><w:basedOn w:val="Normal"/><w:pPr><w:shd w:val="clear" w:color="auto" w:fill="FFF7E6"/>
<w:pBdr><w:left w:val="single" w:sz="24" w:space="6" w:color="D99A06"/></w:pBdr><w:ind w:left="120"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>
<w:spacing w:after="40"/><w:ind w:left="360" w:hanging="240"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="{MUTED_HEX}"/><w:sz w:val="16"/></w:rPr></w:style>
</w:styles>"""

_NUMBERING = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering {_W}><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
              '<w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
              f'<w:rPr><w:color w:val="{ACCENT_HEX}"/></w:rPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')


def docx(blocks: Iterable[tuple[str, Any]], title: str = "") -> bytes:
    """A Word document from (style, value) blocks: "Title", "Subtitle", "Heading1"/"Heading2"/"Heading3", "Guidance" (a shaded
    hint), "Note" (an amber call-out), "Bullet", "Table" (value = rows, the first the header), "PageBreak", or "" (body)."""
    blocks = [(s, v) for s, v in blocks if v is not None]
    title = title or next((str(v) for s, v in blocks if s == "Title"), "BRT 360")
    body: list[str] = []
    for style, value in blocks:
        if style == "Table":
            body.append(_table([list(r) for r in value]))
        elif style == "PageBreak":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        elif style == "Bullet":
            body.append(_p(str(value), "ListBullet"))
        elif style in ("Title", "Subtitle", "Heading1", "Heading2", "Heading3", "Guidance", "Note"):
            body.append(_p(str(value), style))
        else:
            body.append(_p(str(value)))
    footer = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {_W}><w:p><w:pPr><w:pStyle w:val="Footer"/>'
              f'<w:pBdr><w:top w:val="single" w:sz="4" w:space="4" w:color="C9D2DC"/></w:pBdr><w:tabs><w:tab w:val="right" w:pos="9000"/></w:tabs></w:pPr>'
              f'{_run(f"BRT 360 · {title}")}<w:r><w:tab/></w:r>{_run("Page ")}'
              '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
              f'{_run(" of ")}<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> NUMPAGES </w:instrText></w:r>'
              '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_W}><w:body>' + "".join(body)
                + '<w:sectPr><w:footerReference w:type="default" r:id="rId3"/><w:pgSz w:w="11906" w:h="16838"/>'
                  '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                   '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                   '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                   '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                   f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{_x(title)}</dc:title><dc:creator>BRT 360</dc:creator></cp:coreProperties>')
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                   '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>')
        z.writestr("word/styles.xml", _DOCX_STYLES)
        z.writestr("word/numbering.xml", _NUMBERING)
        z.writestr("word/footer1.xml", footer)
    return out.getvalue()


# ---------------------------------------------------------------- Excel


def _col(i: int) -> str:
    name, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


_XLSX_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="4"><font><sz val="10"/><name val="Calibri"/></font><font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    f'<font><b/><sz val="14"/><color rgb="FF{ACCENT_DARK_HEX}"/><name val="Calibri"/></font><font><i/><sz val="10"/><color rgb="FF{MUTED_HEX}"/><name val="Calibri"/></font></fonts>'
    f'<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    f'<fill><patternFill patternType="solid"><fgColor rgb="FF{ACCENT_HEX}"/><bgColor indexed="64"/></patternFill></fill>'
    f'<fill><patternFill patternType="solid"><fgColor rgb="FF{BAND_HEX}"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"><color rgb="FFC9D2DC"/></left><right style="thin"><color rgb="FFC9D2DC"/></right>'
    '<top style="thin"><color rgb="FFC9D2DC"/></top><bottom style="thin"><color rgb="FFC9D2DC"/></bottom><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment wrapText="1"/></xf></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def _sheet_xml(rows: list[list[Any]], header: bool = True) -> str:
    cols = max((len(r) for r in rows), default=1)
    widths = [10] * cols
    for row in rows[:500]:
        for c, value in enumerate(row):
            widths[c] = max(widths[c], min(60, len(str(value if value is not None else "")) + 2))
    lines = []
    for r, row in enumerate(rows, start=1):
        style = 1 if header and r == 1 else (3 if r % 2 == 1 else 2)
        cells = []
        for c, value in enumerate(row):
            ref = f"{_col(c)}{r}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            else:
                text = "" if value is None else str(value)
                if r > 1 and text[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"-?\d+(\.\d+)?", text):
                    text = "'" + text  # never a formula from data (spreadsheet injection)
                cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
        height = ' ht="22" customHeight="1"' if header and r == 1 else ""
        lines.append(f'<row r="{r}"{height}>{"".join(cells)}</row>')
    last = f"{_col(cols - 1)}{max(len(rows), 1)}"
    view = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            if header else '<sheetViews><sheetView workbookViewId="0"/></sheetViews>')
    columns = "<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)) + "</cols>"
    autofilter = f'<autoFilter ref="A1:{last}"/>' if header and rows else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{view}{columns}<sheetData>{"".join(lines)}</sheetData>{autofilter}</worksheet>')


def xlsx(sheets: list[tuple[str, list[list[Any]]]] | list[dict[str, Any]]) -> bytes:
    """Sheets as (name, rows) - the first row is the header - or {"name", "rows", "header": False} for a sheet with no header row."""
    norm = [s if isinstance(s, dict) else {"name": s[0], "rows": s[1]} for s in sheets] or [{"name": "Sheet1", "rows": []}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(norm))) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        used: set[str] = set()
        names = []
        for s in norm:
            name = re.sub(r"[\[\]:*?/\\]", " ", str(s["name"]))[:31] or "Sheet"
            while name in used:
                name = f"{name[:28]} {len(used)}"
            used.add(name)
            names.append(name)
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(norm)))
                   + f'<Relationship Id="rId{len(norm) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", _XLSX_STYLES)
        for i, s in enumerate(norm):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml([list(r) for r in s["rows"]], s.get("header", True)))
    return buf.getvalue()


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(content: bytes) -> list[tuple[str, list[list[str]]]]:
    """The sheets of an .xlsx file as (name, rows of text)."""
    z = zipfile.ZipFile(io.BytesIO(content))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    workbook = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    out = []
    for sheet in workbook.find("m:sheets", _NS):
        target = targets.get(sheet.get(f"{{{_NS['r']}}}id"), "")
        path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        if path not in z.namelist():
            continue
        rows: list[list[str]] = []
        for row in ET.fromstring(z.read(path)).iter(f"{{{_NS['m']}}}row"):
            cells: dict[int, str] = {}
            for c in row.findall("m:c", _NS):
                kind, v = c.get("t"), c.find("m:v", _NS)
                if kind == "s" and v is not None:
                    value = shared[int(v.text)]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                else:
                    value = v.text if v is not None and v.text is not None else ""
                    if re.fullmatch(r"-?\d+\.0", value or ""):
                        value = value[:-2]
                cells[_col_index(c.get("r", "A1"))] = value
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        out.append((sheet.get("name") or "", rows))
    return out


def xlsx_to_csv(content: bytes) -> str:
    """The first sheet of an .xlsx as CSV text - a filled-in template read back like a CSV upload."""
    import csv

    sheets = read_xlsx(content)
    rows = sheets[0][1] if sheets else []
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def is_xlsx(content: bytes) -> bool:
    if content[:2] != b"PK":
        return False
    try:
        return "xl/workbook.xml" in zipfile.ZipFile(io.BytesIO(content)).namelist()
    except zipfile.BadZipFile:
        return False


# ---------------------------------------------------------------- PDF

_PAGE_W, _PAGE_H, _MARGIN = 595, 842, 50
# Helvetica / Helvetica-Bold advance widths (1/1000 em) for ASCII 32-126
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
         278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611,
         722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
         556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_HELV_B = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
           333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611,
           722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
           611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
_SUBST = {"₹": "INR ", "→": "->", "←": "<-", "≤": "<=", "≥": ">=", "✔": "OK", "✘": "X", "✓": "OK", "✗": "X", "…": "...", "⏭": ">>",
          "•": "\x95", "▶": ">", "📄": "", "⬇": "", "✨": "", "🔗": "", "👥": ""}


def plain(value: Any) -> str:
    """Text the standard PDF fonts can show (Windows-1252)."""
    text = str(value if value is not None else "")
    for a, b in _SUBST.items():
        text = text.replace(a, b)
    return text.encode("cp1252", "replace").decode("cp1252").replace("\x00", "")


def _enc(value: Any) -> str:
    return plain(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\r", "")


def width(text: str, size: float, bold: bool = False, mono: bool = False) -> float:
    if mono:
        return len(text) * 0.6 * size
    table = _HELV_B if bold else _HELV
    return sum(table[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in plain(text)) * size / 1000


_width = width


def wrap(text: str, avail: float, size: float, bold: bool = False, mono: bool = False) -> list[str]:
    """The text in lines that fit the width - a word longer than a line is cut."""
    lines, line = [], ""
    for word in plain(text).split(" "):
        while width(word, size, bold, mono) > avail and len(word) > 1:
            cut = max(1, int(len(word) * avail / width(word, size, bold, mono)) - 1)
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:cut])
            word = word[cut:]
        trial = f"{line} {word}".strip() if line else word
        if width(trial, size, bold, mono) > avail and line:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line or not lines else lines


_wrap = wrap


class Pdf:
    """A paginated A4 document in the house style. Blocks keep their own space (need) and break pages cleanly."""

    def __init__(self, title: str, subtitle: str = ""):
        self.title, self.subtitle = plain(title), plain(subtitle)
        self.pages: list[list[str]] = []
        self.y = 0.0
        self._page()

    # -- drawing primitives
    def _page(self) -> None:
        self.ops: list[str] = []
        self.pages.append(self.ops)
        first = len(self.pages) == 1
        height = 70 if first else 34
        self.rect(0, _PAGE_H - height, _PAGE_W, height, ACCENT)
        self.rect(0, _PAGE_H - height - 3, _PAGE_W, 3, ACCENT_DARK)
        if first:
            self.text(_MARGIN, _PAGE_H - 38, self.title, 18, True, (1, 1, 1))
            self.text(_MARGIN, _PAGE_H - 56, self.subtitle, 9, False, (0.85, 0.92, 0.97))
            self.y = _PAGE_H - 100
        else:
            self.text(_MARGIN, _PAGE_H - 22, self.title, 10, True, (1, 1, 1))
            self.y = _PAGE_H - 62

    def rect(self, x: float, y: float, w: float, h: float, rgb: tuple[float, float, float]) -> None:
        self.ops.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")

    def box(self, x: float, y: float, w: float, h: float, rgb: tuple[float, float, float] = RULE) -> None:
        self.ops.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} RG 0.6 w {x:.1f} {y:.1f} {w:.1f} {h:.1f} re S")

    def line(self, x1: float, y1: float, x2: float, y2: float, rgb=RULE) -> None:
        self.ops.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} RG 0.6 w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    def text(self, x: float, y: float, value: Any, size: float = 10, bold: bool = False, rgb=TEXT, mono: bool = False) -> None:
        font = "F3" if mono else ("F2" if bold else "F1")
        self.ops.append(f"BT {rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg /{font} {size} Tf {x:.1f} {y:.1f} Td ({_enc(value)}) Tj ET")

    def need(self, height: float) -> None:
        if self.y - height < 60:
            self._page()

    def page_break(self) -> None:
        self._page()

    # -- blocks
    def heading(self, value: str) -> None:
        self.need(40)
        self.y -= 10
        self.rect(_MARGIN, self.y - 4, 4, 16, ACCENT)
        self.text(_MARGIN + 10, self.y, value, 13, True, ACCENT_DARK)
        self.y -= 8
        self.line(_MARGIN, self.y, _PAGE_W - _MARGIN, self.y, ACCENT)
        self.y -= 16

    def subheading(self, value: str, rgb=ACCENT) -> None:
        self.need(30)
        self.y -= 4
        for line in wrap(value, _PAGE_W - 2 * _MARGIN, 11, True):
            self.text(_MARGIN, self.y, line, 11, True, rgb)
            self.y -= 14
        self.y -= 2

    def paragraph(self, value: str, size: float = 10, rgb=TEXT, indent: float = 0) -> None:
        for raw in str(value or "").split("\n"):
            if not raw.strip():
                self.y -= size * 0.7
                continue
            for line in wrap(raw, _PAGE_W - 2 * _MARGIN - indent, size):
                self.need(size + 4)
                self.text(_MARGIN + indent, self.y, line, size, False, rgb)
                self.y -= size + 4
        self.y -= 4

    def note(self, value: str, rgb=ACCENT, fill=ACCENT_LIGHT) -> None:
        """A shaded call-out with a coloured edge."""
        lines = wrap(value, _PAGE_W - 2 * _MARGIN - 20, 9.5)
        height = 10 + 13 * len(lines)
        self.need(height + 6)
        self.rect(_MARGIN, self.y - height + 12, _PAGE_W - 2 * _MARGIN, height, fill)
        self.rect(_MARGIN, self.y - height + 12, 3, height, rgb)
        for n, line in enumerate(lines):
            self.text(_MARGIN + 12, self.y - n * 13, line, 9.5, False, TEXT)
        self.y -= height + 8

    def bullets(self, items: Iterable[str], size: float = 10) -> None:
        for item in items:
            lines = wrap(str(item), _PAGE_W - 2 * _MARGIN - 14, size)
            self.need(len(lines) * (size + 4))
            self.rect(_MARGIN + 2, self.y + 2.5, 3.5, 3.5, ACCENT)
            for line in lines:
                self.text(_MARGIN + 14, self.y, line, size)
                self.y -= size + 4
        self.y -= 4

    def facts(self, rows: list[tuple[str, Any]]) -> None:
        """Two columns of label / value pairs."""
        rows = [(k, v) for k, v in rows if v not in (None, "")]
        col_w = (_PAGE_W - 2 * _MARGIN) / 2
        for i in range(0, len(rows), 2):
            pair = rows[i:i + 2]
            wrapped = [wrap(str(value), col_w - 12, 10) for _, value in pair]
            height = 16 + 13 * max(len(w) for w in wrapped)
            self.need(height)
            for j, ((label, _), lines) in enumerate(zip(pair, wrapped)):
                x = _MARGIN + j * col_w
                self.text(x, self.y, str(label).upper(), 7, True, MUTED)
                for n, line in enumerate(lines):
                    self.text(x, self.y - 12 - n * 13, line, 10)
            self.y -= height
        self.y -= 2

    def tiles(self, items: list[tuple[str, Any, tuple[float, float, float] | None]]) -> None:
        """Summary figures in coloured boxes: (label, value, colour)."""
        if not items:
            return
        gap = 8
        w = (_PAGE_W - 2 * _MARGIN - gap * (len(items) - 1)) / len(items)
        self.need(56)
        for i, (label, value, rgb) in enumerate(items):
            x = _MARGIN + i * (w + gap)
            self.rect(x, self.y - 40, w, 50, BAND)
            self.rect(x, self.y + 7, w, 3, rgb or ACCENT)
            self.text(x + 8, self.y - 4, str(label).upper(), 7, True, MUTED)
            self.text(x + 8, self.y - 28, value, 18, True, rgb or TEXT)
        self.y -= 62

    def table(self, headers: list[str], rows: list[list[Any]], widths: list[float] | None = None, right: set[int] | None = None,
              status_col: int | None = None, size: float = 9) -> None:
        """A table with a coloured header row (repeated on a new page), banded rows; status_col colours that column."""
        right = right or set()
        total = _PAGE_W - 2 * _MARGIN
        widths = widths or [1.0] * len(headers)
        scale = total / sum(widths)
        widths = [w * scale for w in widths]

        def row(cells: list[Any], bold: bool, shade=None) -> None:
            wrapped = [wrap(str(cell if cell is not None else ""), widths[i] - 8, size, bold) for i, cell in enumerate(cells)]
            height = 7 + (size + 2) * max(len(w) for w in wrapped)
            if self.y - height - 2 < 60:
                self._page()
                if not bold:
                    row(headers, True, ACCENT)
            if shade:
                self.rect(_MARGIN, self.y - height + size + 3, total, height, shade)
            x = _MARGIN
            for i, lines in enumerate(wrapped):
                colour = (1, 1, 1) if shade == ACCENT else TEXT
                cell_bold = bold
                if status_col == i and not bold:
                    colour = STATUS.get(str(cells[i]).lower(), TEXT)
                    cell_bold = True
                for n, value in enumerate(lines):
                    tx = x + widths[i] - 4 - width(value, size, cell_bold) if i in right else x + 4
                    self.text(tx, self.y - n * (size + 2), value, size, cell_bold, colour)
                x += widths[i]
            self.y -= height

        row(headers, True, ACCENT)
        for n, cells in enumerate(rows):
            row(list(cells), False, BAND if n % 2 else None)
        self.line(_MARGIN, self.y + size, _PAGE_W - _MARGIN, self.y + size)
        self.y -= 6

    def code(self, value: str, size: float = 7.5, label: str = "") -> None:
        """Monospaced text in a shaded box (request / response evidence)."""
        if label:
            self.need(14)
            self.text(_MARGIN, self.y, label, 7.5, True, MUTED)
            self.y -= 11
        lines: list[str] = []
        avail = _PAGE_W - 2 * _MARGIN - 16
        for raw in str(value or "").replace("\t", "  ").split("\n"):
            indent = " " * (len(raw) - len(raw.lstrip(" ")))  # keep the indentation of JSON and code
            body = wrap(raw.strip(" "), avail - width(indent, size, mono=True), size, mono=True) or [""]
            lines += [indent + part for part in body]
        for start in range(0, len(lines), 80):
            chunk = lines[start:start + 80]
            while chunk:
                fit = max(1, int((self.y - 70) // (size + 2.5)))
                part, chunk = chunk[:fit], chunk[fit:]
                height = 8 + (size + 2.5) * len(part)
                self.rect(_MARGIN, self.y - height + size + 3, _PAGE_W - 2 * _MARGIN, height, BAND)
                for n, line in enumerate(part):
                    self.text(_MARGIN + 8, self.y - n * (size + 2.5), line, size, False, (0.2, 0.24, 0.3), mono=True)
                self.y -= height + 2
                if chunk:
                    self._page()
        self.y -= 4

    def totals(self, rows: list[tuple[str, str, bool]]) -> None:
        for label, value, strong in rows:
            self.need(18)
            size = 11 if strong else 9.5
            right_edge = _PAGE_W - _MARGIN - 4
            self.text(right_edge - 170, self.y, label, size, strong, TEXT if strong else MUTED)
            self.text(right_edge - width(value, size, strong), self.y, value, size, strong)
            self.y -= 16
        self.y -= 6

    # -- output
    def bytes(self, footer: str = "") -> bytes:
        footer = plain(footer or f"BRT 360 · {self.title}")
        count = len(self.pages)
        for n, ops in enumerate(self.pages, start=1):
            self.ops = ops
            self.line(_MARGIN, 44, _PAGE_W - _MARGIN, 44)
            self.text(_MARGIN, 32, footer, 7.5, False, MUTED)
            label = f"Page {n} of {count}"
            self.text(_PAGE_W - _MARGIN - width(label, 7.5), 32, label, 7.5, False, MUTED)
        objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
                                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
                                b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
                                f"<< /Producer (BRT 360) /Title ({_enc(self.title)}) >>".encode("cp1252", "replace")]
        kids = []
        for ops in self.pages:
            stream = "\n".join(ops).encode("cp1252", "replace")
            page_id = len(objects) + 1
            objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] /Resources << /Font << /F1 3 0 R "
                           f"/F2 4 0 R /F3 5 0 R >> >> /Contents {page_id + 1} 0 R >>".encode())
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
        out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
        return out.getvalue()


def table_pdf(title: str, subtitle: str, headers: list[str], rows: list[list[Any]], summary: list[str] | None = None,
              status_col: int | None = None, widths: list[float] | None = None) -> bytes:
    """A report as a PDF: a summary, then the rows as a styled table."""
    doc = Pdf(title, subtitle)
    if summary:
        doc.note("\n".join(summary))
    doc.table(headers, [["" if v is None else v for v in r] for r in rows], widths=widths, status_col=status_col, size=8)
    return doc.bytes()


def template_xlsx(title: str, columns: list[dict[str, Any]], example: list[Any] | None = None) -> bytes:
    """An upload template: sheet 1 = just the coloured header row to fill in (read back as it is); sheet 2 = the guide -
    every column with whether it's required, what to put in it, and an example."""
    guide = [["Column", "Required", "What to put in it", "Example"]]
    for i, c in enumerate(columns):
        guide.append([c["name"], "Yes" if c.get("required") else "", c.get("help", ""), example[i] if example and i < len(example) else ""])
    return xlsx([(title[:31] or "Data", [[c["name"] for c in columns]]), ("Guide", guide)])


MEDIA = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "csv": "text/csv; charset=utf-8"}
