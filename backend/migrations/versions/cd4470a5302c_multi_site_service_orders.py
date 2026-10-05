"""multi site service orders

Phase 3 (B2B process pack): site_label distinguishes multiple
ServiceOrders under one multi-site Order (IPLC's A-End/Z-End pattern).
Unlike the tenant_id/service_account_id backfills earlier in this
project, every existing row gets the exact same value ("Primary"), so a
server_default on the ADD COLUMN itself is sufficient - no separate
backfill UPDATE needed.

Revision ID: cd4470a5302c
Revises: 93a570a0f595
Create Date: 2026-10-05 12:55:26.879603

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cd4470a5302c'
down_revision: Union[str, Sequence[str], None] = '93a570a0f595'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("service_orders") as batch_op:
        batch_op.add_column(
            sa.Column("site_label", sa.String(length=100), nullable=False, server_default="Primary")
        )


def downgrade() -> None:
    with op.batch_alter_table("service_orders") as batch_op:
        batch_op.drop_column("site_label")
