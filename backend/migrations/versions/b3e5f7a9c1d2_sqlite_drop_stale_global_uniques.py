"""sqlite: actually drop the stale global UNIQUE(name)/UNIQUE(region)

137090a72994 replaced the pre-multi-tenant global uniqueness on
product_specifications.name and territory_rules.region with per-tenant
UNIQUE(tenant_id, ...) - but only dropped the old constraint on Postgres.
On SQLite the old one is an unnamed table constraint that batch mode
reflects and faithfully re-creates, so it survived alongside the new one,
and creating any second tenant failed with a 500 (its seed data collides
with the default tenant's "Fiber100", "North", ...). Test databases never
showed it because they are built from the models, which never had it.

Rebuilds both tables on SQLite from an explicit definition (copy_from), so
nothing is reflected and only the per-tenant constraint remains. Postgres
was already fixed by 137090a72994 - no-op there.

Revision ID: b3e5f7a9c1d2
Revises: 7a1c9e3d5b20
Create Date: 2026-10-07 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e5f7a9c1d2'
down_revision: Union[str, Sequence[str], None] = '7a1c9e3d5b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _product_specifications(meta: sa.MetaData) -> sa.Table:
    return sa.Table(
        "product_specifications",
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_line", sa.String(100), nullable=False),
        sa.Column("is_atomic", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("tenant_id", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_product_specifications_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_product_specifications_tenant_name"),
    )


def _territory_rules(meta: sa.MetaData) -> sa.Table:
    return sa.Table(
        "territory_rules",
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("region", sa.String(100), nullable=False),
        sa.Column("owner_name", sa.String(200), nullable=False),
        sa.Column("tenant_id", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_territory_rules_tenant_id"),
        sa.UniqueConstraint("tenant_id", "region", name="uq_territory_rules_tenant_region"),
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    meta = sa.MetaData()
    # Other tables reference product_specifications by id; SQLite's table
    # rebuild must not trip (or cascade) those FKs mid-copy.
    op.execute("PRAGMA foreign_keys=OFF")
    for table in (_product_specifications(meta), _territory_rules(meta)):
        with op.batch_alter_table(table.name, recreate="always", copy_from=table):
            pass
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    # Re-adding a constraint that only ever caused the multi-tenant bug
    # would make a database with a second tenant impossible to downgrade.
    pass
