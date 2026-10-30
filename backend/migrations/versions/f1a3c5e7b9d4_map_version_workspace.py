"""A Functional Map version made in an administrator's workspace knows it
(change control) - locked to it, released with it."""
from alembic import op
import sqlalchemy as sa

revision = "f1a3c5e7b9d4"
down_revision = "e9b1d3f5a7c2"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("function_map_versions") as batch:
        batch.add_column(sa.Column("workspace_id", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("function_map_versions") as batch:
        batch.drop_column("workspace_id")
