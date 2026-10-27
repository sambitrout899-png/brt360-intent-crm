"""Custom record types: tenant-defined record types (custom_record_types) and their
records (custom_records). Their fields are custom_field_definitions with
entity_type "x_<key>"."""
from alembic import op
import sqlalchemy as sa

revision = "e2b4d6f8a0c3"
down_revision = "d8a0c2e4f6b1"
branch_labels = depends_on = None


def _system_columns(table: str) -> list:
    # the system columns every table carries (app/brt_id.py)
    return [
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name=f"uq_{table}_brt_id"),
    ]


def upgrade():
    op.create_table(
        "custom_record_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.String(28), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("plural_label", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("icon", sa.String(10), nullable=False, server_default="📄"),
        sa.Column("number_prefix", sa.String(10), nullable=False),
        sa.Column("next_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_entity_type", sa.String(30), nullable=True),
        sa.Column("parent_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system_columns("custom_record_types"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_custom_record_types_key"),
    )
    op.create_index("ix_custom_record_types_tenant_id", "custom_record_types", ["tenant_id"])

    op.create_table(
        "custom_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("type_id", sa.Integer(), sa.ForeignKey("custom_record_types.id"), nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_system_columns("custom_records"),
        sa.UniqueConstraint("type_id", "number", name="uq_custom_records_number"),
    )
    op.create_index("ix_custom_records_tenant_id", "custom_records", ["tenant_id"])
    op.create_index("ix_custom_records_type_id", "custom_records", ["type_id"])
    op.create_index("ix_custom_records_parent_id", "custom_records", ["parent_id"])


def downgrade():
    op.drop_index("ix_custom_records_parent_id", table_name="custom_records")
    op.drop_index("ix_custom_records_type_id", table_name="custom_records")
    op.drop_index("ix_custom_records_tenant_id", table_name="custom_records")
    op.drop_table("custom_records")
    op.drop_index("ix_custom_record_types_tenant_id", table_name="custom_record_types")
    op.drop_table("custom_record_types")
