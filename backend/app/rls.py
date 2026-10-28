"""Row-level security: Postgres itself keeps each tenant's rows to that tenant.

Every table with a tenant_id column carries one policy, `tenant_isolation`:
- inside a tenant's request (app.tenant_id set) a row is visible - and may
  be written - only if it is that tenant's, or shared (tenant_id NULL);
- with no tenant set (login, public links, platform admins, background jobs,
  migrations, scripts) the policy lets everything through, as before.

So a query that forgets its tenant filter returns nothing of another tenant's
instead of leaking it: the application's filters stay the first wall, this is
the second. SQLite has no RLS; there all of this does nothing.

The tenant travels on the SQLAlchemy session (session.info["tenant_id"], set
by `scope` once the user is known) and is applied to every transaction the
session begins - as a transaction-local setting, so a pooled connection never
carries it into someone else's request.

Postgres never applies RLS to a superuser - and the Docker image's database
user is one. So the app's connections switch to a plain role (`use_role`,
DATABASE_APP_ROLE): no superuser rights, no BYPASSRLS, only reading and
writing data. Migrations, scripts and this module's setup keep the owner.
"""

from __future__ import annotations

import re

from sqlalchemy import event, text
from sqlalchemy.orm import Session

SETTING = "app.tenant_id"
POLICY = "tenant_isolation"
# NULLIF: an unset custom setting reads as NULL or '' depending on the session's history
_CURRENT = f"NULLIF(current_setting('{SETTING}', true), '')"
CONDITION = f"(tenant_id IS NULL OR {_CURRENT} IS NULL OR tenant_id = {_CURRENT}::integer)"


def tenant_tables(connection) -> list[str]:
    return list(connection.execute(text(
        "SELECT c.table_name FROM information_schema.columns c "
        "JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE c.table_schema = current_schema() AND c.column_name = 'tenant_id' AND t.table_type = 'BASE TABLE' "
        "ORDER BY c.table_name")).scalars())


def _role_name(role: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role or ""):
        raise ValueError(f"Not a usable role name: {role!r}")
    return role


def enable(connection, app_role: str | None = None) -> list[str]:
    """The policy on every tenant table, and the app's plain role with the data
    rights it needs (both idempotent - run after every migration). FORCE: also
    binds a non-superuser owner."""
    tables = tenant_tables(connection)
    for table in tables:
        connection.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        connection.execute(text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        connection.execute(text(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"'))
        connection.execute(text(f'CREATE POLICY {POLICY} ON "{table}" USING {CONDITION} WITH CHECK {CONDITION}'))
    if app_role:
        role = _role_name(app_role)
        connection.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS; END IF; END $$"))
        connection.execute(text(f"GRANT {role} TO CURRENT_USER"))  # so the app's connections may SET ROLE to it
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {role}"))
        for kind, rights in (("TABLES", "SELECT, INSERT, UPDATE, DELETE"), ("SEQUENCES", "USAGE, SELECT, UPDATE")):
            connection.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {rights} ON {kind} TO {role}"))
    return tables


def use_role(engine, role: str) -> None:
    """Every connection of `engine` works as the plain `role` - the one RLS applies to."""
    statement = f"SET ROLE {_role_name(role)}"

    @event.listens_for(engine, "connect")
    def _set_role(dbapi_connection, connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(statement)
        dbapi_connection.commit()


def disable(connection) -> None:
    for table in tenant_tables(connection):
        connection.execute(text(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"'))
        connection.execute(text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
        connection.execute(text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))


def scope(session: Session, tenant_id: int | None) -> None:
    """From now on this session works as `tenant_id` (None: unrestricted)."""
    session.info["tenant_id"] = tenant_id
    if tenant_id is not None and session.in_transaction() and session.get_bind().dialect.name == "postgresql":
        _apply(session.connection(), tenant_id)  # the transaction already running (the user was just loaded in it)


def _apply(connection, tenant_id: int) -> None:
    connection.exec_driver_sql(f"SELECT set_config('{SETTING}', %s, true)", (str(int(tenant_id)),))


@event.listens_for(Session, "after_begin")
def _scope_new_transaction(session, transaction, connection) -> None:
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None and connection.dialect.name == "postgresql":
        _apply(connection, tenant_id)


if __name__ == "__main__":
    # Run after every `alembic upgrade head` (the Docker migrate service does): the policy on
    # every tenant table, new ones included. `--off` takes them all away again (the rollback).
    import sys

    from sqlalchemy import create_engine

    from app.config import get_settings

    url = get_settings().database_url
    if not url.startswith("postgresql"):
        print("Row-level security: not Postgres - nothing to do")
        raise SystemExit(0)
    role = get_settings().database_app_role or "brt360_app"
    with create_engine(url).begin() as conn:
        if "--off" in sys.argv:
            disable(conn)
            print("Row-level security: OFF on every table")
        else:
            print(f"Row-level security: ON for {len(enable(conn, role))} tenant tables; app role '{role}' has data rights")
