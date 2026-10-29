"""flow_runs: flow runs paused at a Wait or Wait for approval step
(app/models/flows.py FlowRun, app/orchestration/flow_runs.py)."""
from alembic import op
import sqlalchemy as sa

revision = "b5e7a9c1d3f6"
down_revision = "a4d6f8b0c2e5"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "flow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("flow_id", sa.Integer(), sa.ForeignKey("flow_definitions.id"), nullable=False),
        sa.Column("execution_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("node_id", sa.String(60), nullable=False),
        sa.Column("wait_kind", sa.String(20), nullable=False),
        sa.Column("resume_at", sa.DateTime(), nullable=True),
        sa.Column("approver_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approver_position_id", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(20), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=False),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("outcome_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_flow_runs_brt_id"),
    )
    op.create_index("ix_flow_runs_status_resume_at", "flow_runs", ["status", "resume_at"])


def downgrade():
    op.drop_index("ix_flow_runs_status_resume_at", table_name="flow_runs")
    op.drop_table("flow_runs")
