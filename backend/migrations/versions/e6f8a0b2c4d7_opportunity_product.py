"""opportunity product: offering_name + quantity on opportunities

Existing opportunities take their lead's product interest, if any.

Revision ID: e6f8a0b2c4d7
Revises: d5e7f9a1b3c6
Create Date: 2026-10-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f8a0b2c4d7'
down_revision: Union[str, Sequence[str], None] = 'd5e7f9a1b3c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.add_column(sa.Column("offering_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
    op.execute(
        "UPDATE opportunities SET offering_name = "
        "(SELECT leads.interested_offering_name FROM leads WHERE leads.id = opportunities.lead_id) "
        "WHERE offering_name IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.drop_column("quantity")
        batch_op.drop_column("offering_name")
