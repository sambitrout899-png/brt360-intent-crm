"""add pending_cancellation order status

Phase 2: the retention-intercept pattern on INT-09 cancellation needs a
holding status between "active" and "cancelled" - the order is still
live and billable while its retention window is open. Postgres enums
can't have a value removed (no native DROP VALUE), so downgrade is a
no-op there, same tradeoff most teams accept for this kind of change;
SQLite's enum is just a CHECK constraint, so its downgrade can and does
genuinely revert.

Revision ID: 12200dd5f70a
Revises: d85234bcb001
Create Date: 2026-10-05 12:16:37.046764

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '12200dd5f70a'
down_revision: Union[str, Sequence[str], None] = 'd85234bcb001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_VALUES = ("created", "activating", "active", "changed", "suspended", "cancelled", "terminated")
NEW_VALUES = ("created", "activating", "active", "changed", "suspended", "pending_cancellation", "cancelled", "terminated")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE cannot run inside the migration's
        # ambient transaction on older Postgres - autocommit_block()
        # commits it first, same pattern Alembic's own docs recommend
        # for this exact case.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'pending_cancellation'")
    else:
        with op.batch_alter_table("orders") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(*OLD_VALUES, name="orderstatus"),
                type_=sa.Enum(*NEW_VALUES, name="orderstatus"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        pass
    else:
        with op.batch_alter_table("orders") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(*NEW_VALUES, name="orderstatus"),
                type_=sa.Enum(*OLD_VALUES, name="orderstatus"),
            )
