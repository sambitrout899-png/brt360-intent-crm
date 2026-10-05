"""users and roles

Adds real authentication: users table + a role enum. Also seeds the two
bootstrap accounts (a platform_admin and the "default" tenant's first
admin) inline, reading credentials from app.config.Settings - the same
BOOTSTRAP_* env vars app.seed.py uses - so setting real values in
production's .env BEFORE running this migration actually takes effect;
hardcoding the insecure dev default here would silently ignore that.
Change both passwords via the UI immediately after first login either way.

Revision ID: 2f289a462815
Revises: c7703b4c7c04
Create Date: 2026-10-05 14:50:10.178185

"""
from typing import Sequence, Union

import bcrypt
from alembic import op
import sqlalchemy as sa

from app.config import get_settings


# revision identifiers, used by Alembic.
revision: str = '2f289a462815'
down_revision: Union[str, Sequence[str], None] = 'c7703b4c7c04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def upgrade() -> None:
    bind = op.get_bind()
    settings = get_settings()

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.Enum("platform_admin", "admin", "agent", "viewer", name="userrole"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    # role MUST be declared with the real Enum type here, not sa.String() -
    # on SQLite this makes no difference (enums are just a CHECK
    # constraint over a VARCHAR, strings bind fine either way), but
    # Postgres has a genuine "userrole" enum type and refuses to
    # implicitly cast a VARCHAR-typed bind parameter onto it, raising
    # DatatypeMismatch on the INSERT below.
    users_table = sa.table(
        "users",
        sa.column("tenant_id", sa.Integer()),
        sa.column("email", sa.String()),
        sa.column("password_hash", sa.String()),
        sa.column("name", sa.String()),
        sa.column("role", sa.Enum("platform_admin", "admin", "agent", "viewer", name="userrole")),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
    )

    bind.execute(
        users_table.insert().values(
            tenant_id=None,
            email=settings.bootstrap_platform_admin_email,
            password_hash=_hash(settings.bootstrap_platform_admin_password),
            name="Platform Admin",
            role="platform_admin",
            is_active=True,
            created_at=sa.func.now(),
        )
    )

    default_tenant_id = bind.execute(sa.text("SELECT id FROM tenants WHERE slug = 'default'")).scalar_one_or_none()
    if default_tenant_id is not None:
        bind.execute(
            users_table.insert().values(
                tenant_id=default_tenant_id,
                email=settings.bootstrap_tenant_admin_email,
                password_hash=_hash(settings.bootstrap_tenant_admin_password),
                name="Default Admin",
                role="admin",
                is_active=True,
                created_at=sa.func.now(),
            )
        )


def downgrade() -> None:
    op.drop_table("users")
