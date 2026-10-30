"""Change control: workspaces, object locks, revisions and releases
(app/models/change_control.py); the tenant's root administrator; revisions
of a Functional Map version (v3, v3.1 ...)."""
from alembic import op
import sqlalchemy as sa

revision = "e9b1d3f5a7c2"
down_revision = "d7a9c1e3f5b8"
branch_labels = depends_on = None


def _sys():
    return [sa.Column("brt_id", sa.String(20), nullable=False),
            sa.Column("sys_created_at", sa.DateTime(), nullable=True),
            sa.Column("sys_created_by", sa.Integer(), nullable=True),
            sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
            sa.Column("sys_department_id", sa.Integer(), nullable=True),
            sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
            sa.Column("sys_updated_by", sa.Integer(), nullable=True)]


def upgrade():
    op.create_table(
        "change_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("release_id", sa.Integer(), nullable=True),
        *_sys(),
        sa.UniqueConstraint("brt_id", name="uq_change_workspaces_brt_id"),
    )
    op.create_index("ix_change_workspaces_tenant", "change_workspaces", ["tenant_id", "status"])
    op.create_table(
        "change_locks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("object_type", sa.String(40), nullable=False),
        sa.Column("object_key", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("change_workspaces.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=False),
        *_sys(),
        sa.UniqueConstraint("brt_id", name="uq_change_locks_brt_id"),
        sa.UniqueConstraint("tenant_id", "object_type", "object_key", name="uq_change_locks_object"),
    )
    op.create_table(
        "change_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("object_type", sa.String(40), nullable=False),
        sa.Column("object_key", sa.String(80), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("change_workspaces.id"), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("base_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_sys(),
        sa.UniqueConstraint("brt_id", name="uq_change_revisions_brt_id"),
    )
    op.create_index("ix_change_revisions_object", "change_revisions", ["tenant_id", "object_type", "object_key"])
    op.create_table(
        "change_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("workspace_ids", sa.JSON(), nullable=False),
        sa.Column("map_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deployed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("deployed_at", sa.DateTime(), nullable=True),
        sa.Column("undo_json", sa.Text(), nullable=False),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        *_sys(),
        sa.UniqueConstraint("brt_id", name="uq_change_releases_brt_id"),
    )
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("root_admin_user_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("function_map_versions") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("function_map_versions") as batch:
        batch.drop_column("parent_id")
        batch.drop_column("revision")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("root_admin_user_id")
    op.drop_table("change_releases")
    op.drop_index("ix_change_revisions_object", table_name="change_revisions")
    op.drop_table("change_revisions")
    op.drop_table("change_locks")
    op.drop_index("ix_change_workspaces_tenant", table_name="change_workspaces")
    op.drop_table("change_workspaces")
