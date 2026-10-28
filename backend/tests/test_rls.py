"""Row-level security (app/rls.py): Postgres itself keeps a tenant's rows to that
tenant. Runs only in the suite's Postgres mode with the policies on:
TEST_DATABASE_URL=postgresql+psycopg://... TEST_RLS=1 pytest tests/test_rls.py"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import rls
from app.models.crm import Party
from app.models.tenant import Tenant

pytestmark = pytest.mark.skipif(not (os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_RLS") == "1"),
                                reason="needs the Postgres test mode with row-level security on")


def _two_tenants(client):
    with client.TestSessionLocal() as db:  # unscoped: set up both tenants' data
        a, b = Tenant(slug="rls-a", name="RLS A"), Tenant(slug="rls-b", name="RLS B")
        db.add_all([a, b])
        db.flush()
        db.add_all([Party(tenant_id=a.id, name="Alpha Ltd", party_type="organization"),
                    Party(tenant_id=b.id, name="Beta Ltd", party_type="organization")])
        db.commit()
        return a.id, b.id


def _names(db):
    # deliberately NO tenant filter - the bug the policy is the second wall against
    return sorted(db.execute(text("SELECT name FROM parties WHERE name IN ('Alpha Ltd', 'Beta Ltd')")).scalars())


def test_a_query_without_a_tenant_filter_sees_only_its_own_tenant(client):
    a, b = _two_tenants(client)
    with client.TestSessionLocal() as db:
        rls.scope(db, a)
        assert _names(db) == ["Alpha Ltd"]
    with client.TestSessionLocal() as db:
        rls.scope(db, b)
        assert _names(db) == ["Beta Ltd"]
    with client.TestSessionLocal() as db:  # no tenant (jobs, login, platform admin): everything
        assert _names(db) == ["Alpha Ltd", "Beta Ltd"]


def test_writing_a_row_for_another_tenant_is_refused(client):
    a, b = _two_tenants(client)
    with client.TestSessionLocal() as db:
        rls.scope(db, a)
        db.add(Party(tenant_id=b, name="Smuggled Ltd", party_type="organization"))
        with pytest.raises(DBAPIError, match="row-level security"):
            db.flush()
        db.rollback()
    with client.TestSessionLocal() as db:
        rls.scope(db, a)
        beta = db.execute(text("UPDATE parties SET name = 'Hijacked' WHERE name = 'Beta Ltd'"))
        assert beta.rowcount == 0  # another tenant's row isn't even there to update
        db.commit()
    with client.TestSessionLocal() as db:
        assert "Beta Ltd" in _names(db)


def test_the_scope_survives_a_commit_and_never_leaks_through_the_pool(client):
    a, _ = _two_tenants(client)
    with client.TestSessionLocal() as db:
        rls.scope(db, a)
        assert _names(db) == ["Alpha Ltd"]
        db.commit()  # a new transaction begins after this
        assert _names(db) == ["Alpha Ltd"]
    for _ in range(5):  # the next sessions reuse pooled connections: no tenant may stick to them
        with client.TestSessionLocal() as db:
            assert _names(db) == ["Alpha Ltd", "Beta Ltd"]


def test_a_tenant_request_cannot_reach_another_tenants_record(client):
    _, b = _two_tenants(client)
    with client.TestSessionLocal() as db:
        beta_id = db.execute(text("SELECT id FROM parties WHERE name = 'Beta Ltd'")).scalar()
    # the default test user is the default tenant's admin
    assert client.get(f"/api/records/party/{beta_id}/about").status_code == 404


def test_every_tenant_table_carries_the_policy(client):
    with client.TestSessionLocal() as db:
        tables = set(rls.tenant_tables(db.connection()))
        covered = set(db.execute(text("SELECT tablename FROM pg_policies WHERE policyname = :p"), {"p": rls.POLICY}).scalars())
        forced = set(db.execute(text("SELECT relname FROM pg_class WHERE relforcerowsecurity")).scalars())
    assert len(tables) > 50 and tables <= covered and tables <= forced
