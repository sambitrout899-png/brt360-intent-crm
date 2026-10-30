"""use_case_sets: an administrator's functional use cases - read from the use
case template, mapped on the Functional Map, committed and tested
(app/models/use_cases.py)."""
from alembic import op
import sqlalchemy as sa

revision = "a2c4e6f8b0d1"
down_revision = "f1a3c5e7b9d4"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "use_case_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("read_by", sa.String(40), nullable=False),
        sa.Column("use_cases_json", sa.Text(), nullable=False),
        sa.Column("analysis_json", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("tests_json", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_use_case_sets_brt_id"),
    )
    op.create_index("ix_use_case_sets_tenant", "use_case_sets", ["tenant_id", "id"])


def downgrade():
    op.drop_index("ix_use_case_sets_tenant", table_name="use_case_sets")
    op.drop_table("use_case_sets")
