"""Exercises the REAL authentication path - every other test uses
conftest.py's dependency_overrides to bypass JWT verification entirely
for convenience, which is correct for testing business logic but would
never catch a bug in login/token creation/verification itself. These
tests temporarily remove that override to hit the genuine code path.
"""

from app.auth import get_current_user
from app.main import app
from tests.conftest import DEFAULT_TEST_ADMIN_EMAIL


def _without_auth_override():
    saved = app.dependency_overrides.pop(get_current_user, None)
    return saved


def _restore_auth_override(saved):
    if saved is not None:
        app.dependency_overrides[get_current_user] = saved


def test_login_with_correct_password_returns_working_token(client):
    saved = _without_auth_override()
    try:
        db = client.TestSessionLocal()
        try:
            from app.auth import hash_password
            from app.models.auth import User

            user = db.query(User).filter(User.email == DEFAULT_TEST_ADMIN_EMAIL).one()
            user.password_hash = hash_password("real-password-123")
            db.commit()
        finally:
            db.close()

        login_resp = client.post("/api/auth/login", json={"email": DEFAULT_TEST_ADMIN_EMAIL, "password": "real-password-123"})
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        assert token

        me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == DEFAULT_TEST_ADMIN_EMAIL

        no_auth_resp = client.get("/api/auth/me")
        assert no_auth_resp.status_code == 401

        bad_token_resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
        assert bad_token_resp.status_code == 401
    finally:
        _restore_auth_override(saved)


def test_login_with_wrong_password_rejected(client):
    saved = _without_auth_override()
    try:
        db = client.TestSessionLocal()
        try:
            from app.auth import hash_password
            from app.models.auth import User

            user = db.query(User).filter(User.email == DEFAULT_TEST_ADMIN_EMAIL).one()
            user.password_hash = hash_password("the-real-password")
            db.commit()
        finally:
            db.close()

        resp = client.post("/api/auth/login", json={"email": DEFAULT_TEST_ADMIN_EMAIL, "password": "wrong-password"})
        assert resp.status_code == 401
    finally:
        _restore_auth_override(saved)


def test_login_unknown_email_rejected(client):
    saved = _without_auth_override()
    try:
        resp = client.post("/api/auth/login", json={"email": "nobody@nowhere.test", "password": "whatever123"})
        assert resp.status_code == 401
    finally:
        _restore_auth_override(saved)


def test_deactivated_user_cannot_authenticate(client):
    saved = _without_auth_override()
    try:
        db = client.TestSessionLocal()
        try:
            from app.auth import hash_password
            from app.models.auth import User

            user = db.query(User).filter(User.email == DEFAULT_TEST_ADMIN_EMAIL).one()
            user.password_hash = hash_password("real-password-123")
            user.is_active = False
            db.commit()
        finally:
            db.close()

        resp = client.post("/api/auth/login", json={"email": DEFAULT_TEST_ADMIN_EMAIL, "password": "real-password-123"})
        assert resp.status_code == 401
    finally:
        _restore_auth_override(saved)


def test_viewer_role_blocked_from_submitting_intents(client):
    db = client.TestSessionLocal()
    try:
        from app.auth import hash_password
        from app.models.auth import User, UserRole
        from app.models.tenant import Tenant

        tenant = db.query(Tenant).filter(Tenant.slug == "default").one()
        viewer = User(
            tenant_id=tenant.id,
            email="viewer@default.local",
            password_hash=hash_password("x"),
            name="Viewer",
            role=UserRole.viewer,
        )
        db.add(viewer)
        db.commit()
        viewer_id = viewer.id
    finally:
        db.close()

    resp = client.post(
        "/api/intents",
        json={"text": "Qualify this lead. company_name: ViewerBlockedCo, source: web, region: ALL"},
        headers={"X-Test-User-Id": str(viewer_id)},
    )
    assert resp.status_code == 403


def test_agent_role_blocked_from_admin_routes(client):
    db = client.TestSessionLocal()
    try:
        from app.auth import hash_password
        from app.models.auth import User, UserRole
        from app.models.tenant import Tenant

        tenant = db.query(Tenant).filter(Tenant.slug == "default").one()
        agent = User(
            tenant_id=tenant.id,
            email="agent@default.local",
            password_hash=hash_password("x"),
            name="Agent",
            role=UserRole.agent,
        )
        db.add(agent)
        db.commit()
        agent_id = agent.id
    finally:
        db.close()

    resp = client.get("/api/admin/approval-chains", headers={"X-Test-User-Id": str(agent_id)})
    assert resp.status_code == 403


def test_change_password_requires_correct_current_password(client):
    saved = _without_auth_override()
    try:
        db = client.TestSessionLocal()
        try:
            from app.auth import hash_password
            from app.models.auth import User

            user = db.query(User).filter(User.email == DEFAULT_TEST_ADMIN_EMAIL).one()
            user.password_hash = hash_password("original-password")
            db.commit()
        finally:
            db.close()

        token = client.post(
            "/api/auth/login", json={"email": DEFAULT_TEST_ADMIN_EMAIL, "password": "original-password"}
        ).json()["access_token"]
        auth_header = {"Authorization": f"Bearer {token}"}

        wrong_resp = client.post(
            "/api/auth/change-password",
            json={"current_password": "not-the-right-one", "new_password": "new-password-123"},
            headers=auth_header,
        )
        assert wrong_resp.status_code == 401

        ok_resp = client.post(
            "/api/auth/change-password",
            json={"current_password": "original-password", "new_password": "new-password-123"},
            headers=auth_header,
        )
        assert ok_resp.status_code == 204

        relogin_resp = client.post(
            "/api/auth/login", json={"email": DEFAULT_TEST_ADMIN_EMAIL, "password": "new-password-123"}
        )
        assert relogin_resp.status_code == 200
    finally:
        _restore_auth_override(saved)
