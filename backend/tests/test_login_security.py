"""Sign-in security: lockout and two-step sign-in (app/crm_core/login_security.py,
app/experience/routes_auth.py)."""

import base64
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app import auth, ratelimit
from app.auth import create_access_token, hash_password
from app.config import get_settings
from app.crm_core import login_security
from app.models.auth import User, UserRole
from app.models.tenant import Tenant

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def real_tokens(client):
    """The suite signs every request in as a test admin; here who is signed in
    must come from the token, as in production."""
    from app.main import app

    app.dependency_overrides.pop(auth.get_current_user, None)
    yield


def _admin(client):
    db = client.TestSessionLocal()
    admin = db.query(User).filter(User.role == UserRole.admin).order_by(User.id).first()
    headers = _bearer(create_access_token(admin))
    db.close()
    return headers


def _user(client, email="sec@example.com", role=UserRole.agent):
    db = client.TestSessionLocal()
    tenant_id = db.query(Tenant.id).first()[0]
    user = User(tenant_id=tenant_id, email=email, password_hash=hash_password(PASSWORD), name="Sec", role=role)
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    return user_id


def _login(client, email="sec@example.com", password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _session(client, user_id):
    db = client.TestSessionLocal()
    user = db.get(User, user_id)
    token = create_access_token(user)
    db.close()
    return _bearer(token)


def _code(secret, offset=0):
    return login_security.code_at(secret, int(time.time() // 30) + offset)


def test_totp_matches_rfc_6238():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert login_security.code_at(secret, 59 // 30) == "287082"  # RFC 6238 test vector, last 6 of 94287082
    assert login_security.code_at(secret, 1111111109 // 30) == "081804"


def test_secret_is_stored_encrypted():
    secret = login_security.new_secret()
    stored = login_security.encrypt(secret)
    assert secret not in stored and login_security.decrypt(stored) == secret
    assert login_security.decrypt("not-a-token") is None


def test_wrong_passwords_lock_the_account_until_an_admin_unlocks_it(client):
    user_id = _user(client)
    assert _login(client, "nobody@example.com").json()["detail"] == "Incorrect email or password"
    for _ in range(get_settings().login_max_failures):
        assert _login(client, password="wrong").status_code == 401
    resp = _login(client)  # the right password, but locked now
    assert resp.status_code == 429 and "locked for 15 more minutes" in resp.json()["detail"]

    assert client.post(f"/api/admin/users/{user_id}/unlock", headers=_admin(client)).json()["locked_until"] is None
    assert _login(client).json()["access_token"]


def test_too_many_sign_ins_from_one_address(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_ip_per_minute", 3)
    ratelimit._local.clear()
    _user(client)
    assert [_login(client).status_code for _ in range(4)] == [200, 200, 200, 429]


def test_two_step_sign_in_from_setup_to_recovery_codes(client):
    user_id = _user(client)
    me = _session(client, user_id)
    assert client.get("/api/auth/mfa", headers=me).json() == {"enabled": False, "required": False, "recovery_codes_left": 0}

    setup = client.post("/api/auth/mfa/setup", headers=me).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/BRT%20360%3Asec%40example.com?secret=")
    assert setup["qr_data_url"].startswith("data:image/png;base64,")
    assert client.post("/api/auth/mfa/enable", headers=me, json={"code": "000000"}).status_code == 400
    enabled = client.post("/api/auth/mfa/enable", headers=me, json={"code": _code(setup["secret"])}).json()
    recovery = enabled["recovery_codes"]
    assert len(recovery) == 10 and enabled["access_token"] is None

    first = _login(client).json()  # the password alone is no longer a session
    assert first["access_token"] is None and first["mfa_required"] and first["step_token"]
    step = first["step_token"]
    assert client.post("/api/auth/login/mfa", json={"step_token": step, "code": "123456"}).status_code == 401

    code = _code(setup["secret"], 1)  # the next step's code: newer than the one used to enable
    done = client.post("/api/auth/login/mfa", json={"step_token": step, "code": code}).json()
    assert done["access_token"]
    assert client.post("/api/auth/login/mfa", json={"step_token": step, "code": code}).status_code == 401  # each code once

    by_recovery = client.post("/api/auth/login/mfa", json={"step_token": step, "code": recovery[0].upper()})
    assert by_recovery.status_code == 200  # a recovery code in place of a code...
    assert client.post("/api/auth/login/mfa", json={"step_token": step, "code": recovery[0]}).status_code == 401  # ...once
    assert client.get("/api/auth/mfa", headers=me).json()["recovery_codes_left"] == 9


def test_a_step_token_is_never_a_session(client):
    user_id = _user(client)
    db = client.TestSessionLocal()
    try:
        user = db.get(User, user_id)
        for purpose in ("mfa", "mfa_setup"):
            token = create_access_token(user, purpose=purpose, minutes=5)
            request = SimpleNamespace(state=SimpleNamespace())
            with pytest.raises(HTTPException) as caught:
                auth.get_current_user(request, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
            assert caught.value.status_code == 401
        with pytest.raises(HTTPException):  # nor is a session a step token
            auth.user_for_step(create_access_token(user), "mfa", db)
    finally:
        db.close()


def test_a_company_can_require_two_step_sign_in(client):
    boss = _session(client, _user(client, "boss@example.com", UserRole.admin))
    agent_id = _user(client, "agent@example.com")

    # the admin who switches it on must have it on, or they would lock themselves out
    assert client.put("/api/admin/security", headers=boss, json={"mfa_required": True}).status_code == 400
    secret = client.post("/api/auth/mfa/setup", headers=boss).json()["secret"]
    client.post("/api/auth/mfa/enable", headers=boss, json={"code": _code(secret)})
    assert client.put("/api/admin/security", headers=boss, json={"mfa_required": True}).json()["mfa_required"] is True
    assert client.get("/api/admin/security", headers=boss).json()["mfa_required"] is True

    first = _login(client, "agent@example.com").json()
    assert first["access_token"] is None and first["mfa_setup_required"]
    setup_step = _bearer(first["step_token"])
    assert client.get("/api/auth/mfa", headers=setup_step).status_code == 401  # good for setting up, nothing else
    setup = client.post("/api/auth/mfa/setup", headers=setup_step).json()
    done = client.post("/api/auth/mfa/enable", headers=setup_step, json={"code": _code(setup["secret"])}).json()
    assert done["access_token"] and len(done["recovery_codes"]) == 10  # set up = signed in

    me = _session(client, agent_id)
    resp = client.post("/api/auth/mfa/disable", headers=me, json={"password": PASSWORD, "code": _code(setup["secret"], 1)})
    assert resp.status_code == 400 and "requires" in resp.json()["detail"]  # can't be switched off while required


def test_turning_it_off_needs_password_and_code_and_an_admin_can_reset_it(client):
    user_id = _user(client)
    me = _session(client, user_id)
    secret = client.post("/api/auth/mfa/setup", headers=me).json()["secret"]
    client.post("/api/auth/mfa/enable", headers=me, json={"code": _code(secret)})
    assert client.post("/api/auth/mfa/disable", headers=me, json={"password": "wrong", "code": _code(secret, 1)}).status_code == 400
    assert client.post("/api/auth/mfa/disable", headers=me, json={"password": PASSWORD, "code": _code(secret, 1)}).status_code == 204
    assert client.get("/api/auth/mfa", headers=me).json()["enabled"] is False

    secret = client.post("/api/auth/mfa/setup", headers=me).json()["secret"]
    client.post("/api/auth/mfa/enable", headers=me, json={"code": _code(secret)})
    reset = client.post(f"/api/admin/users/{user_id}/reset-mfa", headers=_admin(client)).json()  # lost phone and codes
    assert reset["mfa_enabled"] is False
    assert _login(client).json()["access_token"]


def test_idle_timeout_ends_a_session_left_alone(client):
    import datetime as dt

    from app.timeutils import utcnow

    user_id = _user(client)
    me = _session(client, user_id)
    admin = _admin(client)
    assert client.put("/api/admin/security", headers=admin, json={"mfa_required": False, "idle_timeout_minutes": 3}).status_code == 400
    policy = client.put("/api/admin/security", headers=admin, json={"mfa_required": False, "idle_timeout_minutes": 30}).json()
    assert policy == {"mfa_required": False, "idle_timeout_minutes": 30}
    assert client.get("/api/auth/me", headers=me).json()["idle_timeout_minutes"] == 30  # the screens warn from this

    def seen(minutes_ago):
        db = client.TestSessionLocal()
        db.get(User, user_id).last_seen_at = utcnow() - dt.timedelta(minutes=minutes_ago)
        db.commit()
        db.close()

    def last_seen():
        db = client.TestSessionLocal()
        value = db.get(User, user_id).last_seen_at
        db.close()
        return value

    seen(10)
    background = {**me, "X-BRT-Background": "1"}
    assert client.get("/api/auth/me", headers=background).status_code == 200
    assert utcnow() - last_seen() > dt.timedelta(minutes=9)  # the screens' polling doesn't keep a session alive
    assert client.get("/api/auth/me", headers=me).status_code == 200
    assert utcnow() - last_seen() < dt.timedelta(minutes=1)  # what the user does does

    seen(31)
    assert client.get("/api/auth/me", headers=me).status_code == 200  # within the 2-minute grace
    seen(33)
    resp = client.get("/api/auth/me", headers=me)
    assert resp.status_code == 401 and resp.json()["detail"] == "Signed out after 30 minutes without activity"
    assert _login(client).json()["access_token"]  # signing in again starts the clock afresh
    assert client.get("/api/auth/me", headers=me).status_code == 200

    off = client.put("/api/admin/security", headers=admin, json={"mfa_required": False, "idle_timeout_minutes": 0}).json()
    assert off["idle_timeout_minutes"] is None
    seen(600)
    assert client.get("/api/auth/me", headers=me).status_code == 200  # off: only the token's own expiry counts
