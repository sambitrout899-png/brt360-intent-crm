"""Idle timeout: a tenant's limit, and each user's last signed-in request
(app/auth.py, get_current_user)."""
from alembic import op
import sqlalchemy as sa

revision = "a4d6f8b0c2e5"
down_revision = "f3c5e7a9b1d4"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("idle_timeout_minutes", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("idle_timeout_minutes")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("last_seen_at")
