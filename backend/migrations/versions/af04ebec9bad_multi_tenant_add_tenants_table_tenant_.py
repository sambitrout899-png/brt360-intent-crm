"""multi-tenant: add tenants table + tenant_id on root entities

Revision ID: af04ebec9bad
Revises: 295bf27cc602
Create Date: 2026-10-05 10:45:25.046335

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af04ebec9bad'
down_revision: Union[str, Sequence[str], None] = '295bf27cc602'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Root tables that get their own tenant_id column. Hand-written (not the raw
# autogenerate output) because the table already has rows in both the local
# SQLite dev DB and production Postgres from all prior live intent-chain
# testing: adding a NOT NULL column with no default fails on a non-empty
# table on Postgres, and SQLite can't ALTER a constraint onto a table outside
# batch mode at all. So each table goes through add-nullable -> backfill to
# the seeded "default" tenant -> alter-to-NOT-NULL -> add FK, all via
# batch_alter_table so the same code works on SQLite (table-recreate under
# the hood) and Postgres (direct ALTER TABLE) alike.
TENANT_SCOPED_TABLES = [
    'billing_accounts',
    'bundle_compatibility_rules',
    'intent_execution_log',
    'leads',
    'opportunities',
    'orders',
    'parties',
    'product_offerings',
    'product_specifications',
    'quotes',
    'technicians',
    'territory_rules',
    'trouble_tickets',
    'work_orders',
]


def upgrade() -> None:
    bind = op.get_bind()

    tenants_table = op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('vertical', sa.Enum('b2c', 'b2b', 'both', name='tenantvertical'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )

    # Seed the "default" tenant inline so pre-existing rows on every
    # tenant-scoped table (real data from Sprint 1-4 live testing) have
    # somewhere to land. app.seed.get_or_create_tenant() is a no-op against
    # this row on next run (same slug).
    bind.execute(
        tenants_table.insert().values(
            name='Default Tenant',
            slug='default',
            vertical='both',
            created_at=sa.func.now(),
        )
    )
    default_tenant_id = bind.execute(
        sa.text("SELECT id FROM tenants WHERE slug = 'default'")
    ).scalar_one()

    for table in TENANT_SCOPED_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))

        bind.execute(
            sa.text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"),
            {"tid": default_tenant_id},
        )

        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column('tenant_id', existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(f'fk_{table}_tenant_id', 'tenants', ['tenant_id'], ['id'])

    # intent_execution_log.channel: backfill existing rows via server_default
    # (it has no DB-level default in the model - ORM applies "agent" on
    # insert - so the server_default here exists only to satisfy NOT NULL
    # against pre-existing rows, then is dropped to match the model).
    with op.batch_alter_table('intent_execution_log') as batch_op:
        batch_op.add_column(sa.Column('channel', sa.String(length=30), nullable=False, server_default='agent'))
    with op.batch_alter_table('intent_execution_log') as batch_op:
        batch_op.alter_column('channel', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('intent_execution_log') as batch_op:
        batch_op.drop_column('channel')

    for table in reversed(TENANT_SCOPED_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f'fk_{table}_tenant_id', type_='foreignkey')
            batch_op.drop_column('tenant_id')

    op.drop_table('tenants')
