"""tenant theme: the colour theme every user of the organization sees, and whether users may choose their own

Revision ID: e2a4c6b8d0f3
Revises: c9e1a3b5d7f2
Create Date: 2026-11-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2a4c6b8d0f3'
down_revision: Union[str, Sequence[str], None] = 'c9e1a3b5d7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(sa.Column("theme", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("theme_user_choice", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_column("theme_user_choice")
        batch_op.drop_column("theme")
