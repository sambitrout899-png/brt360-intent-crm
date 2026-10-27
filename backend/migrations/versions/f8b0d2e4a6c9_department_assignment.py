"""Records can be assigned to a department (as well as a user or a position),
assignment history records it, and assignment rules can target one
(additive: new nullable columns only, nothing existing changes).

Revision ID: f8b0d2e4a6c9
Revises: d6f8b0c2e4a7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8b0d2e4a6c9"
down_revision: Union[str, Sequence[str], None] = "d6f8b0c2e4a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("entity_record_meta") as batch_op:
        batch_op.add_column(sa.Column("owner_department_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("entity_assignment_history") as batch_op:
        batch_op.add_column(sa.Column("from_department_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("to_department_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("assignment_rules") as batch_op:
        batch_op.add_column(sa.Column("department_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assignment_rules") as batch_op:
        batch_op.drop_column("department_id")
    with op.batch_alter_table("entity_assignment_history") as batch_op:
        batch_op.drop_column("to_department_id")
        batch_op.drop_column("from_department_id")
    with op.batch_alter_table("entity_record_meta") as batch_op:
        batch_op.drop_column("owner_department_id")
