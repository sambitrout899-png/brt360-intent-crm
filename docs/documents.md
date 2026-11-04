# Generated documents - one house style

Every Word, Excel and PDF document the platform generates comes from `app/documents.py` (standard library only - no
extra packages), so a proposal, a test report, a template and an export look like they come from the same product:
the BRT 360 blue (`ACCENT`), coloured headings, styled tables and a page-numbered footer.

| Writer | What it gives |
| --- | --- |
| `docx(blocks)` | A coloured title band, coloured Heading 1/2/3 (real Word heading styles - the templates read back as before), shaded *Guidance* hints, amber *Note* call-outs, bullets, tables with a coloured header row and banded rows, footer "BRT 360 · title · Page X of Y" |
| `xlsx(sheets)` | A bold white-on-blue header row, frozen, with filters; banded rows, borders, fitted column widths; formulas from data neutralised. The first row stays the header, so a template filled in reads back as it is |
| `Pdf(title, subtitle)` | A coloured banner on every page, headings with an accent bar, paragraphs, facts, summary tiles, bullets, tables (coloured header repeated on each page, banded rows, status colours), code blocks, totals, footer with page X of Y |
| `template_xlsx(...)` | An upload template: sheet 1 = the header row to fill in, sheet 2 = a guide to every column (required, what to put in it) |

## Where they are used

**Templates (Word)** - use case template, process BRD template (Command Bar -> Build a process), now with coloured
headings, shaded guidance and a footer; they are read back exactly as before.

**Templates (Excel, new)** - bulk orders (Operations -> Bulk Orders -> *⬇ Excel template*,
`GET /api/bulk-orders/processes/{process}/template.xlsx`) and BIM loads (Admin -> BIM -> *⬇ Excel template*,
`GET /api/bim/template/{entity}?format=xlsx`). Filled in, they are uploaded **as they are**: bulk orders and BIM accept
`.xlsx` (its first sheet) as well as CSV.

**Documents** - test cases (Word: summary table, facts, steps table, expected result, sign-off table; Excel too),
solution design (requirements / steps / hand-offs / gaps tables, sign-off), quote proposals and agreements, Product
Modeller exports, training exports.

**Reports as PDF (new)**

| Report | Where | API |
| --- | --- | --- |
| System test evidence | Admin -> Test Suite -> a run -> *📄 PDF report* (or *PDF (every step)*) | `GET /api/system-tests/runs/{id}/report.pdf[?all_steps=true]` (+ `results.xlsx`, `cases.xlsx`) |
| Bulk order results | Operations -> Bulk Orders -> a request -> *📄 PDF report* | `GET /api/bulk-orders/{id}/results.pdf` (+ `results.xlsx`) |
| Analytics reports and record lists | Analytics -> Explorer / drill-down -> *📄 PDF* / *⬇ Excel* | `POST /api/analytics/export` with `format: pdf / xlsx` |
| Audit trail (with its filters) | Audit Log -> *📄 PDF report* / *⬇ Excel* | `GET /api/audit/feed/export?format=pdf\|xlsx&...` |
| Training reports | Training -> Insights / session results | `GET /api/training/analytics/export?format=pdf` (now a styled table) |

PDFs use the standard fonts (Helvetica / Courier, Windows-1252): characters outside it (₹, →, ✔) are written as
INR, ->, OK.

## Report Design - the organization's look on every PDF

**Admin -> Platform -> Report Design** (any organization admin, whichever modules it holds). One setting per
organization (`tenants.report_design`, migration `b6d8f0a2c4e7`) that every PDF above reads, as well as quote
proposals and agreements:

| Setting | What it does |
| --- | --- |
| Logo | The app's logo (set by the platform administrator), a logo just for reports (PNG or JPEG, ≤ 512 KB - transparency kept), or none; left or right. Over a colour band it sits on a white plate so any logo reads |
| Accent colour | Header, headings, table header rows, call-outs, rules. Blank = the app's brand colour, else the BRT 360 blue. A pale colour gets dark text automatically |
| Header style | Colour band / light tint / clean (white with a coloured rule) |
| Company line | Company name (blank = the organization's display name) and a tagline (address, website ...) |
| Footer | Its text with `{company}` `{title}` `{date}` (blank = "company · title"), a confidentiality line, the "Generated ..." stamp on or off |
| Watermark | Large see-through diagonal text on every page (DRAFT, CONFIDENTIAL ...) |
| Page size | A4 or Letter |

The screen shows a live preview of a sample report as you change it; nothing changes until *Save design*.
*Reset to standard* goes back to the app's logo and colour.

API: `GET / PUT / DELETE /api/admin/report-design`, `POST /api/admin/report-design/preview` (a sample PDF for unsaved
settings). Code: `app/report_design.py` (`brand(db, tenant_id)` -> `documents.Brand`, passed to `Pdf(title, subtitle, brand)`).
A logo a PDF can't show (SVG, GIF, WebP, damaged) is left out with the reason shown on the screen; a report always comes out.
