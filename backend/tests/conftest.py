import os

# Must be set before anything below imports app.config - bcrypt's real
# cost factor (12) is correct for production but needlessly slow for a
# suite that creates several users per test purely as fixtures, not as
# anything a test is actually verifying. Production must never set this.
os.environ["BCRYPT_ROUNDS"] = "4"
# Every test client signs in from the same address; the per-IP sign-in limit
# would trip on the suite, not on anything under test (test_login_security turns it on).
os.environ["LOGIN_IP_PER_MINUTE"] = "0"

import pytest
from fastapi import Depends, Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import rls
from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models.auth import User, UserRole
from app.seed import get_or_create_tenant, get_or_create_user, seed_reference_data
from app.tenancy import DEFAULT_TENANT_SLUG

DEFAULT_TEST_ADMIN_EMAIL = "test-admin@default.local"
DEFAULT_TEST_PLATFORM_ADMIN_EMAIL = "test-platform-admin@brt360.local"

# TEST_DATABASE_URL=postgresql+psycopg://... runs the whole suite on Postgres - an
# empty database to throw away (it is wiped). TEST_RLS=1 also turns the row-level
# security policies on (app/rls.py), and every request works as its user's tenant.
POSTGRES_URL = os.environ.get("TEST_DATABASE_URL", "")
RLS_ROLE = "brt360_app_test"
_postgres = None  # the owner: builds and empties the schema
_postgres_app = None  # what the tests' sessions use - the plain role under RLS, as in production


def _postgres_engine():
    """Built once per run (schema, BRT_ID register, policies); emptied before each test."""
    global _postgres, _postgres_app
    if _postgres is None:
        _postgres = _postgres_app = create_engine(POSTGRES_URL)
        with _postgres.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            Base.metadata.create_all(bind=conn)
            if os.environ.get("TEST_RLS") == "1":
                rls.enable(conn, RLS_ROLE)
        if os.environ.get("TEST_RLS") == "1":
            _postgres_app = create_engine(POSTGRES_URL)
            rls.use_role(_postgres_app, RLS_ROLE)
    else:
        # the tables the last test wrote to, emptied and their ids restarted - with DELETE: TRUNCATE
        # makes new files for every table (~2 s a test). The BRT_ID register stays, so codes are
        # unique across tests.
        with _postgres.begin() as conn:
            probe = " UNION ALL ".join(f"SELECT '{t.name}' WHERE EXISTS (SELECT 1 FROM \"{t.name}\")" for t in Base.metadata.sorted_tables)
            used = list(conn.execute(text(probe)).scalars())
            conn.execute(text("SET LOCAL session_replication_role = replica"))  # foreign keys: any order
            for name in used:
                conn.execute(text(f'DELETE FROM "{name}"'))
                if "id" in Base.metadata.tables[name].c:  # some tables are keyed otherwise
                    conn.execute(text(f"SELECT setval(s, 1, false) FROM pg_get_serial_sequence('\"{name}\"', 'id') AS s WHERE s IS NOT NULL"))
    return _postgres_app


@pytest.fixture()
def client():
    if POSTGRES_URL:
        engine = _postgres_engine()
    else:
        # StaticPool pins every caller (including FastAPI's test-request thread)
        # to the same single SQLite connection, so the in-memory DB and its
        # schema/seed data are actually shared across threads - without it each
        # thread opens its own empty ":memory:" database.
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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

    def override_get_current_user(x_test_user_id: int | None = Header(default=None), db: Session = Depends(get_db)) -> User:
        session = TestSession()
        try:
            user_id = x_test_user_id or default_user_id
            user = session.get(User, user_id)
            if user is None:
                raise ValueError(f"Test user {user_id} not found - did the test create it before using it?")
            session.expunge(user)
        finally:
            session.close()
        rls.scope(db, user.tenant_id)  # as the real get_current_user does: the request's session works as the user's tenant
        return user

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
