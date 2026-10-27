"""Tenant inventory feasibility synchronization."""
from alembic import op
import sqlalchemy as sa
revision = "a1b2c3d4e5f6"
down_revision = ("f7a9b1c3d5e8", "b9c1d3e5f7a0")
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("feasibility_check_types", sa.Column("external_id", sa.String(200), nullable=True))
    with op.batch_alter_table("feasibility_check_types") as batch:
        batch.create_unique_constraint("uq_feasibility_external", ["tenant_id", "external_id"])
    op.create_table("inventory_sync_configs",
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("last_error", sa.String(300)))

def downgrade():
    op.drop_table("inventory_sync_configs")
    with op.batch_alter_table("feasibility_check_types") as batch:
        batch.drop_constraint("uq_feasibility_external", type_="unique")
    op.drop_column("feasibility_check_types", "external_id")
