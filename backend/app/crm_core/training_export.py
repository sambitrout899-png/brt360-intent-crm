"""Report export to CSV, XLSX and PDF (ANL-008) in the house style (app/documents.py) - no extra dependencies.
Callers pass rows they have already scoped and anonymised."""
from __future__ import annotations

import csv
import io

from app.documents import MEDIA as _MEDIA
from app.documents import Pdf, xlsx


def to_csv(headers: list[str], rows: list[list]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    for row in rows:
        # neutralise spreadsheet formulas in user-entered text (CSV injection)
        writer.writerow(["'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else ("" if v is None else v) for v in row])
    return ("﻿" + out.getvalue()).encode("utf-8")


def to_xlsx(headers: list[str], rows: list[list], sheet: str = "Report") -> bytes:
    """A styled sheet: coloured, frozen header row with filters, banded rows, fitted columns."""
    return xlsx([(sheet, [headers, *rows])])


def to_pdf(title: str, lines: list[str], headers: list[str] | None = None, rows: list[list] | None = None) -> bytes:
    """An A4 report: the summary lines in a call-out, then the rows as a styled table."""
    doc = Pdf(title, "Training report")
    if lines:
        doc.note("\n".join(lines))
    if headers:
        doc.table(headers, [["" if v is None else v for v in r] for r in rows or []], size=8)
    return doc.bytes()


MEDIA = {"csv": _MEDIA["csv"], "xlsx": _MEDIA["xlsx"], "pdf": _MEDIA["pdf"]}


def export(fmt: str, title: str, headers: list[str], rows: list[list], summary: list[str]) -> tuple[bytes, str]:
    if fmt == "csv":
        return to_csv(headers, rows), MEDIA["csv"]
    if fmt == "xlsx":
        return to_xlsx(headers, rows), MEDIA["xlsx"]
    if fmt == "pdf":
        return to_pdf(title, summary, headers, rows), MEDIA["pdf"]
    raise ValueError("format must be csv, xlsx or pdf")
