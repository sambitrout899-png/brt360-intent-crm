# Security testing (VAPT)

**Admin → Test Suite → 🛡️ Security testing (VAPT)** - an assessment of the platform, its multi-tenancy and its AI,
with a PDF report written for customers and auditors (in the organization's report design).

## Standards

| Standard | Use |
| --- | --- |
| OWASP Web Security Testing Guide v4.2 | The test cases each control follows (WSTG IDs on every control) |
| OWASP ASVS 4.0.3, Level 2 | The requirements verified |
| OWASP Top 10:2021 / API Security Top 10:2023 / Top 10 for LLM Applications 2025 | Risk coverage - the report has a coverage matrix |
| PTES, NIST SP 800-115 | The VAPT procedure (pre-engagement → discovery → exploitation → reporting → retest) |
| CVSS v3.1 | Severity of every finding (vector + base score, computed by `security_catalog.cvss`) |
| CWE, ISO/IEC 27001:2022 Annex A | Weakness class and the control each check evidences |

## What runs, and where

67 automated controls in 11 areas + 15 manual VAPT procedures (`app/orchestration/security_catalog.py`):

| Area | Examples |
| --- | --- |
| Platform hardening | signing keys and secrets not defaults, RS256, bootstrap passwords, bcrypt cost, CORS, security headers, HTTP methods, error leakage, least-privileged DB role + RLS, session lifetime, email TLS, API inventory |
| Authentication | every endpoint (all ~740 operations) refuses an anonymous request except the reviewed public set; lockout; no account enumeration (status, message, timing); password policy; credential hashes; two-step sign-in enforced, wrong/replayed codes; step tokens; deactivation; password change |
| Session & tokens | forged signatures (HS256, RS256), `alg: none`, expiry, tampered claims, token only in the Authorization header (no URL, no cookie → no CSRF), server-side idle timeout |
| Access control | every `/api/admin` operation refused to an agent, every `/api/platform` operation refused to a tenant admin, viewers can't act, no privilege escalation, no mass assignment |
| Tenant isolation | organization A reads **every** list endpoint and every record-by-id endpoint with B's ids, tries to change / delete B's records, spoofs tenant headers, exports - B's data carries a marker the checks look for; a positive control proves the sweep sees A's own data; PostgreSQL RLS checked on the live database |
| Injection | SQL injection in every text query parameter, stored script never served as HTML, spreadsheet formulas neutralised, path traversal in every text path parameter, malformed bodies on every endpoint, oversized input, upload types |
| Data protection | no secret in any answer, secrets encrypted at rest, no-store caching, no secrets in URLs |
| Logging & integrity | the tamper-evident audit chain verifies, failed sign-ins counted, actions attributed |
| AI safety | prompt injection changes nothing, AI acts only with the caller's permissions, personal data masked before an AI provider, confidence threshold and per-tenant AI limits |
| Resource consumption | sign-in rate limit per address, capped page sizes |
| Public edge | HTTPS redirect, TLS 1.2+/certificate, HSTS, edge headers, server banner, `/docs` `/openapi.json` `/metrics` not public (needs an https origin in `CORS_ALLOWED_ORIGINS`) |

Where each control runs:

- **Sandbox** - the deployed code and configuration (every router, the same middleware and settings) served in-process against a
  throwaway in-memory database with two synthetic organizations. Their tenants and users have ids no real record has; their tokens
  live for the run only and are never stored; the database is discarded. **No live data is read or changed.**
- **Live** - read-only queries of this installation's configuration and database.
- **Edge** - requests to the configured public https address only.
- **Manual** - VAPT-01 … VAPT-15 (scoping/authorization, port scan, TLS, authenticated DAST with ZAP/Burp, business logic, client side,
  uploads, SSRF, dependency scan, container/host hardening, secrets scan, DoS (authorized only), backup/restore, monitoring drill, retest):
  the tester records the outcome, a severity for a finding, and the evidence reference.

A full assessment sends about 2,300 requests and takes 2-4 minutes, in the background (progress on screen).

## Score, grade, report

- Score = verified controls weighted by the severity of the finding they would raise (Critical 10, High 7, Medium 4, Low 2).
- Grade: **A+** every control verified · **A** ≥ 95 % and no critical/high · **B** ≥ 85 % and no critical · **C** ≥ 70 % · **D** ≥ 50 % · **F**.
- PDF report: executive summary, scope and rules of engagement, methodology, results by area, OWASP Top 10 / API Top 10 coverage,
  findings (CVSS vector, standards, evidence, remediation), every control and its result, manual procedures, attestation and sign-off.
  Excel workbook: results and evidence.

On a correctly configured production installation every application control passes; the configuration controls pass once
`JWT_PRIVATE_KEY_FILE` (and `JWT_ACCEPT_HS256=false`), `BRT_ID_SECRET`, changed bootstrap passwords, `BCRYPT_ROUNDS ≥ 12`,
`LOGIN_IP_PER_MINUTE`, `DATABASE_APP_ROLE` and the web tier's TLS/HSTS/headers are in place. On a development machine those
show as findings - correctly.

## Hardening that came with it

- Passwords: 12+ characters, common/breached passwords and the account's own name refused (`app/password_policy.py`, NIST SP 800-63B).
- Security headers on every API answer (`app/service.py`): nosniff, `X-Frame-Options: DENY`, CSP, Referrer-Policy, Permissions-Policy, `Cache-Control: no-store`.
- AI provider keys encrypted at rest (`app/secret_box.py`; migration `c9e1a3b5d7f2` encrypts existing keys).
- Sign-in fields bounded (`max_length`); page sizes capped on five list endpoints; bulk-order result downloads answer 404 (not 500) for another organization's id.

## API

`GET /api/security-tests/catalog` · `POST /api/security-tests/runs` (`{name, categories}`) · `GET /api/security-tests/runs[/{id}]` ·
`POST …/runs/{id}/cancel` · `PUT …/runs/{id}/procedures/{VAPT-nn}` (`{status, note, severity}`) · `GET …/runs/{id}/report.pdf` · `GET …/runs/{id}/results.xlsx`.
Administrators only.
