import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.seed import get_or_create_tenant, seed_reference_data
from app.tenancy import DEFAULT_TENANT_SLUG


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
    # Seeded under the default tenant slug, so requests that send no
    # X-Tenant-Slug header (i.e. every existing test) resolve to it via
    # app.tenancy's fallback - exercising the exact same path production
    # takes for a not-yet-multi-tenant-aware caller.
    tenant = get_or_create_tenant(db, DEFAULT_TENANT_SLUG, "Default Tenant")
    seed_reference_data(db, tenant.id)
    db.commit()
    db.close()

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    # Exposed so tests that need to drive a service function directly
    # (not through an HTTP endpoint) can get a session on this same
    # in-memory DB - StaticPool means every session here shares the one
    # underlying SQLite connection, so it sees exactly what the HTTP calls
    # above committed, and vice versa.
    test_client.TestSessionLocal = TestSession
    yield test_client
    app.dependency_overrides.clear()
