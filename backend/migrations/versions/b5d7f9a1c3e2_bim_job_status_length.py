"""bim_jobs.status: String(20) -> String(30).

A load with some failed rows ends "completed_with_errors" - 21 characters.
SQLite stored it anyway; Postgres refuses, so on Postgres every partly
failed BIM load ended in a server error and lost the rows that were fine."""
from alembic import op
import sqlalchemy as sa

revision = "b5d7f9a1c3e2"
down_revision = "f4a6c8e0b2d1"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("bim_jobs") as batch_op:
        batch_op.alter_column("status", existing_type=sa.String(length=20), type_=sa.String(length=30), existing_nullable=False)


def downgrade():
    with op.batch_alter_table("bim_jobs") as batch_op:
        batch_op.alter_column("status", existing_type=sa.String(length=30), type_=sa.String(length=20), existing_nullable=False)
