"""The rest of the suite builds its database from the models; production
builds it by running every Alembic migration. This test runs the real
migration chain and checks what only that path can break - e.g. the stale
global UNIQUE(name) that made creating a second tenant fail with a 500."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tenant import Tenant, TenantVertical
from app.seed import seed_reference_data

BACKEND = Path(__file__).resolve().parents[1]


def test_every_model_column_exists_after_migrating(tmp_path):
    """A model column no migration creates passes every other test (they build tables from the models)
    and fails in production with "no such column" - e.g. a new table without the system columns."""
    from sqlalchemy import inspect

    import app.models  # noqa: F401 - every model registered
    from app.db import Base

    url = f"sqlite:///{(tmp_path / 'columns.db').as_posix()}"
    done = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env={**os.environ, "DATABASE_URL": url},
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    engine = create_engine(url)
    try:
        db = inspect(engine)
        tables = set(db.get_table_names())
        missing = {}
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                missing[table.name] = "the whole table"
                continue
            have = {c["name"] for c in db.get_columns(table.name)}
            gone = [c.name for c in table.columns if c.name not in have]
            if gone:
                missing[table.name] = gone
    finally:
        engine.dispose()
    assert not missing, missing


def test_second_tenant_can_be_seeded_on_a_migrated_database(tmp_path):
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    env = {**os.environ, "DATABASE_URL": url}
    done = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]

    engine = create_engine(url)
    session = sessionmaker(bind=engine)()
    try:
        for slug in ("first", "second"):
            tenant = Tenant(name=slug.title(), slug=slug, vertical=TenantVertical("both"))
            session.add(tenant)
            session.flush()
            seed_reference_data(session, tenant.id)  # identical names per tenant
        session.commit()
    finally:
        session.close()
        engine.dispose()
