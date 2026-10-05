"""service account hierarchy

Adds the missing middle tier of the 3-tier account hierarchy (Phase 1
primitive #5): Party -> BillingAccount -> ServiceAccount -> Order. Every
existing Order already has a party, and every party with an order already
has a BillingAccount (INT-07 always creates one), so backfill is: one
"Primary Service Account" per billing account that has orders, then point
every one of that party's existing orders at it - same add-nullable ->
backfill -> alter-not-null -> add-FK pattern as af04ebec9bad, so this is
safe against the production Postgres database's existing order rows.

Revision ID: d85234bcb001
Revises: 8c466c9157bc
Create Date: 2026-10-05 12:00:42.631871

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd85234bcb001'
down_revision: Union[str, Sequence[str], None] = '8c466c9157bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "service_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("billing_account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["billing_account_id"], ["billing_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # One default service account per billing account that already has
    # orders - existing orders need somewhere to land before the column
    # can be made NOT NULL.
    billing_accounts_with_orders = bind.execute(
        sa.text(
            "SELECT DISTINCT b.id, b.tenant_id FROM billing_accounts b "
            "JOIN orders o ON o.party_id = b.party_id"
        )
    ).fetchall()

    service_accounts_table = sa.table(
        "service_accounts",
        sa.column("id", sa.Integer()),
        sa.column("tenant_id", sa.Integer()),
        sa.column("billing_account_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime()),
    )

    for billing_account_id, tenant_id in billing_accounts_with_orders:
        bind.execute(
            service_accounts_table.insert().values(
                tenant_id=tenant_id,
                billing_account_id=billing_account_id,
                name="Primary Service Account",
                status="active",
                created_at=sa.func.now(),
            )
        )

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("service_account_id", sa.Integer(), nullable=True))

    bind.execute(
        sa.text(
            "UPDATE orders SET service_account_id = ("
            "  SELECT sa.id FROM service_accounts sa "
            "  JOIN billing_accounts b ON sa.billing_account_id = b.id "
            "  WHERE b.party_id = orders.party_id"
            ") WHERE service_account_id IS NULL"
        )
    )

    with op.batch_alter_table("orders") as batch_op:
        batch_op.alter_column("service_account_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key("fk_orders_service_account_id", "service_accounts", ["service_account_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("fk_orders_service_account_id", type_="foreignkey")
        batch_op.drop_column("service_account_id")
    op.drop_table("service_accounts")
