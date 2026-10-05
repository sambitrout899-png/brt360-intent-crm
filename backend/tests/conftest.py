import os

# Must be set before anything below imports app.config - bcrypt's real
# cost factor (12) is correct for production but needlessly slow for a
# suite that creates several users per test purely as fixtures, not as
# anything a test is actually verifying. Production must never set this.
os.environ["BCRYPT_ROUNDS"] = "4"

import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models.auth import User, UserRole
from app.seed import get_or_create_tenant, get_or_create_user, seed_reference_data
from app.tenancy import DEFAULT_TENANT_SLUG

DEFAULT_TEST_ADMIN_EMAIL = "test-admin@default.local"
DEFAULT_TEST_PLATFORM_ADMIN_EMAIL = "test-platform-admin@brt360.local"


@pytest.fixture()
def client():
    # StaticPool pins every caller (including FastAPI's test-request thread)
    # to the same single SQLite connection, so the in-memory DB and its
    # schema/seed data are actually shared across threads - without it each
    # thread opens its own empty ":memory:" database.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestSession()
    tenant = get_or_create_tenant(db, DEFAULT_TENANT_SLUG, "Default Tenant")
    seed_reference_data(db, tenant.id)
    # Every test authenticates as this admin by default (see
    # override_get_current_user below) - role=admin so both regular
    # intent calls and /api/admin/* routes work without every existing
    # test needing to know or care that auth now exists. A test that
    # needs to act as a DIFFERENT user (a different tenant, a different
    # role) creates one directly via client.TestSessionLocal() and passes
    # X-Test-User-Id to pick it instead.
    default_user = get_or_create_user(
        db,
        tenant_id=tenant.id,
        email=DEFAULT_TEST_ADMIN_EMAIL,
        password="test-password-not-used",
        name="Test Admin",
        role=UserRole.admin,
    )
    # Platform-level tests (tenant creation) authenticate as this one via
    # X-Test-User-Id instead - most tests never touch /api/platform/*, so
    # the default user above stays an ordinary tenant admin.
    get_or_create_user(
        db,
        tenant_id=None,
        email=DEFAULT_TEST_PLATFORM_ADMIN_EMAIL,
        password="test-password-not-used",
        name="Test Platform Admin",
        role=UserRole.platform_admin,
    )
    db.commit()
    default_user_id = default_user.id
    db.close()

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    def override_get_current_user(x_test_user_id: int | None = Header(default=None)) -> User:
        session = TestSession()
        try:
            user_id = x_test_user_id or default_user_id
            user = session.get(User, user_id)
            if user is None:
                raise ValueError(f"Test user {user_id} not found - did the test create it before using it?")
            session.expunge(user)
            return user
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    test_client = TestClient(app)
    # Exposed so tests that need to drive a service function directly
    # (not through an HTTP endpoint), or create fixtures like a second
    # tenant/user, can get a session on this same in-memory DB -
    # StaticPool means every session here shares the one underlying
    # SQLite connection, so it sees exactly what the HTTP calls above
    # committed, and vice versa.
    test_client.TestSessionLocal = TestSession
    yield test_client
    app.dependency_overrides.clear()
