import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models.catalog import BundleCompatibilityRule, ProductSpecification
from app.seed import ATOMIC_PRODUCTS, COMPATIBILITY_RULES


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
    for name, line in ATOMIC_PRODUCTS:
        db.add(ProductSpecification(name=name, product_line=line, is_atomic=True))
    for primary, addon, max_addons in COMPATIBILITY_RULES:
        db.add(BundleCompatibilityRule(primary_product_line=primary, addon_product_line=addon, max_addons=max_addons))
    db.commit()
    db.close()

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
