"""ai settings: provider (external / local model / rules)

Revision ID: b9c1d3e5f7a0
Revises: a8b0c2d4e6f9
Create Date: 2026-10-08 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9c1d3e5f7a0'
down_revision: Union[str, Sequence[str], None] = 'a8b0c2d4e6f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_settings") as batch_op:
        batch_op.add_column(sa.Column("provider", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("local_model", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("local_live_assist", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("ai_settings") as batch_op:
        batch_op.drop_column("local_live_assist")
        batch_op.drop_column("local_model")
        batch_op.drop_column("provider")
