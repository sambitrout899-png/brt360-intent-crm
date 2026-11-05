"""tenant accent: the colour palette of buttons, links and highlights, alongside the theme

Revision ID: a7c9e2f4b6d8
Revises: e2a4c6b8d0f3
Create Date: 2026-11-06 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c9e2f4b6d8'
down_revision: Union[str, Sequence[str], None] = 'e2a4c6b8d0f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(sa.Column("accent", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_column("accent")
