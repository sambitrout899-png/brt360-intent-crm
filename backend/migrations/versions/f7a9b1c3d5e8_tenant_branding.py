"""tenant branding: display name, logo, accent colour

Revision ID: f7a9b1c3d5e8
Revises: e6f8a0b2c4d7
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a9b1c3d5e8'
down_revision: Union[str, Sequence[str], None] = 'e6f8a0b2c4d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("logo_data_url", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("brand_color", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_column("brand_color")
        batch_op.drop_column("logo_data_url")
        batch_op.drop_column("display_name")
