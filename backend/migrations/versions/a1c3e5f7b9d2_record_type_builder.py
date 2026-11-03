"""Record type builder: custom records get a state (their type's state model), types get a name label and
list columns, and administrators relate record types to each other (record_relationships / record_links)."""
from alembic import op
import sqlalchemy as sa

revision = "a1c3e5f7b9d2"
down_revision = "f9b1d3e5a7c9"
branch_labels = depends_on = None


def _create(name, *columns):
    """Every table also carries the BRT_ID and the system audit columns (app/brt_id.py)."""
    op.create_table(
        name, *columns,
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name=f"uq_{name}_brt_id"),
    )


def upgrade():
    with op.batch_alter_table("custom_record_types") as batch:
        batch.add_column(sa.Column("name_label", sa.String(100), nullable=False, server_default=""))
        batch.add_column(sa.Column("list_fields", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("custom_records") as batch:
        batch.add_column(sa.Column("state", sa.String(50), nullable=True))
    # records made before types had states start in the default model's first state
    op.execute("UPDATE custom_records SET state = 'open' WHERE state IS NULL")

    _create(
        "record_relationships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("from_entity_type", sa.String(30), nullable=False),
        sa.Column("to_entity_type", sa.String(30), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("reverse_label", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_record_relationships_key"),
    )
    _create(
        "record_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("relationship_id", sa.Integer(), sa.ForeignKey("record_relationships.id"), nullable=False, index=True),
        sa.Column("from_id", sa.Integer(), nullable=False, index=True),
        sa.Column("to_id", sa.Integer(), nullable=False, index=True),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("relationship_id", "from_id", "to_id", name="uq_record_links"),
    )


def downgrade():
    op.drop_table("record_links")
    op.drop_table("record_relationships")
    with op.batch_alter_table("custom_records") as batch:
        batch.drop_column("state")
    with op.batch_alter_table("custom_record_types") as batch:
        batch.drop_column("list_fields")
        batch.drop_column("name_label")
