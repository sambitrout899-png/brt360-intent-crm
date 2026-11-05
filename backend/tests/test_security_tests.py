"""Security testing (VAPT): the assessment runs every automated control against the deployed code in its sandbox,
the platform passes every application control, findings carry CVSS and standards, the report is a PDF."""

import io
import zipfile

from app import password_policy, secret_box
from app.models.auth import User, UserRole
from app.orchestration import security_catalog as cat

# depend on how this test environment is configured (development keys, fast hashing, no sign-in rate limit) - not on the code
ENVIRONMENT = {"SEC-CONF-01", "SEC-CONF-02", "SEC-CONF-03", "SEC-CONF-04", "SEC-CONF-05", "SEC-RATE-01"}


def test_cvss_and_catalogue():
    assert cat.cvss("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8
    assert cat.cvss("AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:N/A:N") == 7.7
    assert cat.cvss("AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N") == 5.3
    assert cat.cvss("AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:L/A:N") == 7.1
    assert cat.cvss("AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N") == 0.0
    codes = [c["code"] for c in cat.CHECKS]
    assert len(codes) == len(set(codes)) and len(cat.PROCEDURES) == 15
    from app.orchestration.security_tests import RUNNERS
    assert set(RUNNERS) == set(codes)  # every control has its test
    for c in cat.CHECKS:
        assert c["refs"].get("cwe", "").startswith("CWE-") and c["refs"].get("top10") and c["fix"] and "?" not in str(c["refs"])
    assert {c["cat"] for c in cat.CHECKS} | {"VAPT"} == {k for k, *_ in cat.CATEGORIES}


def test_password_policy_and_secret_box():
    assert password_policy.problem("Password1234") and password_policy.problem("short-pw") and password_policy.problem("aaaaaaaaaaaaaaaa")
    assert password_policy.problem("priyasharma", email="priyasharma@x.com")
    assert password_policy.problem("correct horse battery staple") is None
    sealed = secret_box.seal("sk-live-123")
    assert sealed.startswith("enc:v1:") and "sk-live" not in sealed and secret_box.open_(sealed) == "sk-live-123"
    assert secret_box.open_("legacy-plain") == "legacy-plain"


def test_ai_key_is_encrypted_at_rest(client):
    from sqlalchemy import text

    assert client.put("/api/admin/ai-settings", json={"api_key": "sk-test-abcdef123456", "model": "gpt-4o-mini", "is_active": True, "provider": "rules"}).status_code == 200
    with client.TestSessionLocal() as db:
        raw = db.execute(text("SELECT api_key FROM ai_settings")).scalar()
        from app.models.ai_settings import AiSettings
        assert raw.startswith("enc:v1:") and "sk-test" not in raw
        assert db.query(AiSettings).first().api_key == "sk-test-abcdef123456"  # the code reads it as before
    assert "sk-test-abcdef123456" not in client.get("/api/admin/ai-settings").text


def test_security_headers_and_weak_passwords(client):
    r = client.get("/api/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY" and "no-store" in r.headers["cache-control"]
    weak = client.post("/api/admin/users", json={"email": "weak@default.local", "password": "Password1234", "name": "Weak", "role": "agent"})
    assert weak.status_code == 422 and "common" in weak.text


def _agent(client):
    with client.TestSessionLocal() as db:
        from app.auth import hash_password
        from app.models.tenant import Tenant
        user = User(tenant_id=db.query(Tenant).first().id, email="agent-sec@default.local", password_hash=hash_password("x-x-x-x-x-x-x"), name="Agent", role=UserRole.agent)
        db.add(user)
        db.commit()
        return {"X-Test-User-Id": str(user.id)}


def test_only_administrators_run_assessments(client):
    agent = _agent(client)
    assert client.get("/api/security-tests/catalog", headers=agent).status_code == 403
    assert client.post("/api/security-tests/runs", json={}, headers=agent).status_code == 403
    catalog = client.get("/api/security-tests/catalog").json()
    assert len(catalog["checks"]) == len(cat.CHECKS) and len(catalog["methodology"]) >= 8


def test_full_assessment_passes_every_application_control(client):
    run = client.post("/api/security-tests/runs?wait=true", json={"name": "CI assessment"})
    assert run.status_code == 201, run.text
    body = client.get(f"/api/security-tests/runs/{run.json()['id']}").json()
    assert body["status"] == "completed", body["message"]
    results = {r["code"]: r for r in body["results"]}
    assert set(results) == {c["code"] for c in cat.CHECKS} | {p["code"] for p in cat.PROCEDURES}
    broken = {code: (r["status"], r["message"]) for code, r in results.items()
              if not code.startswith("VAPT") and code not in ENVIRONMENT and r["status"] not in ("passed", "not_applicable", "skipped")}
    assert not broken, broken
    # the attack really happened: thousands of requests, every endpoint
    sandbox = body["summary"]["sandbox"]
    assert sandbox["requests"] > 1000 and sandbox["endpoints"] > 500 and sandbox["server_errors"] == 0
    assert results["SEC-TEN-01"]["evidence"][2]["ok"]  # the positive control saw organization A's own data
    # a failed control is a finding with CVSS, standards and evidence
    finding = results["SEC-CONF-05"]
    assert finding["status"] == "failed" and finding["severity"] == "medium" and finding["cvss"] == 4.4 and finding["refs"]["cwe"] == "CWE-916"
    # manual VAPT procedures are recorded by the tester and counted
    recorded = client.put(f"/api/security-tests/runs/{body['id']}/procedures/VAPT-09", json={"status": "failed", "severity": "high", "note": "pip-audit: 1 high"}).json()
    assert recorded["summary"]["manual"]["recorded"] == 1 and recorded["summary"]["open"]["high"] >= 1
    assert client.put(f"/api/security-tests/runs/{body['id']}/procedures/SEC-AUTH-01", json={"status": "passed"}).status_code == 400
    # the report for customers, and the workbook
    pdf = client.get(f"/api/security-tests/runs/{body['id']}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    for words in (b"Security Assessment Report", b"Executive summary", b"OWASP Top 10:2021 coverage", b"Manual VAPT procedures", b"Attestation", b"CVSS"):
        assert words in pdf.content
    book = client.get(f"/api/security-tests/runs/{body['id']}/results.xlsx")
    assert zipfile.ZipFile(io.BytesIO(book.content)).namelist()
    # nothing of the sandbox is left behind in this organization's data
    with client.TestSessionLocal() as db:
        assert not db.query(User).filter(User.email.like("%sandbox.invalid")).count()
