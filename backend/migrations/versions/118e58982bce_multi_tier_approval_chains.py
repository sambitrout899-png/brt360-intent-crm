"""multi-tier approval chains

Replaces the old single-tier approval_requests/approval_decisions tables
with admin-configurable chain templates (approval_chain_templates +
approval_chain_tiers) and their per-quote resolved instances
(approval_chain_instances + approval_step_instances). Seeds every existing
tenant with the same default bands the old hardcoded DISCOUNT_MATRIX used,
so no tenant is left without a chain to resolve against.

Revision ID: 118e58982bce
Revises: af04ebec9bad
Create Date: 2026-10-05 11:12:22.611151

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '118e58982bce'
down_revision: Union[str, Sequence[str], None] = 'af04ebec9bad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (name, segment, min_discount_pct, max_discount_pct, [approver tiers in order])
# Mirrors app.crm_core.approval_service.DEFAULT_SEED_TEMPLATES exactly.
DEFAULT_SEED_TEMPLATES = [
    ("Auto-Approved", None, 0.0, 10.0, ["Auto-Approved"]),
    ("Regional Sign-off", None, 10.0, 20.0, ["Regional Sales Head"]),
    ("VP Sign-off", None, 20.0, 30.0, ["VP Sales"]),
    ("Executive Escalation", None, 30.0, 100.0, ["VP Sales", "CFO"]),
]

# approval_status enum already exists in Postgres (created by
# 295bf27cc602 for the old approval_requests.status column) - reuse it
# rather than trying to create it again.
approval_status_enum = sa.Enum(
    "pending", "approved", "rejected", "conditional", name="approvalstatus", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()

    op.drop_table("approval_decisions")
    op.drop_table("approval_requests")

    op.create_table(
        "approval_chain_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("segment", sa.String(length=100), nullable=True),
        sa.Column("min_discount_pct", sa.Float(), nullable=False),
        sa.Column("max_discount_pct", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "approval_chain_tiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("approver_tier", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["approval_chain_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "approval_chain_instances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("status", approval_status_enum, nullable=False),
        sa.Column("current_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["approval_chain_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_id"),
    )
    op.create_table(
        "approval_step_instances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chain_instance_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("approver_tier", sa.String(length=100), nullable=False),
        sa.Column("status", approval_status_enum, nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["chain_instance_id"], ["approval_chain_instances.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    templates_table = sa.table(
        "approval_chain_templates",
        sa.column("id", sa.Integer()),
        sa.column("tenant_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("segment", sa.String()),
        sa.column("min_discount_pct", sa.Float()),
        sa.column("max_discount_pct", sa.Float()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
    )
    tiers_table = sa.table(
        "approval_chain_tiers",
        sa.column("template_id", sa.Integer()),
        sa.column("sequence", sa.Integer()),
        sa.column("approver_tier", sa.String()),
    )

    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants")).fetchall()]
    for tenant_id in tenant_ids:
        for name, segment, min_pct, max_pct, tiers in DEFAULT_SEED_TEMPLATES:
            bind.execute(
                templates_table.insert().values(
                    tenant_id=tenant_id,
                    name=name,
                    segment=segment,
                    min_discount_pct=min_pct,
                    max_discount_pct=max_pct,
                    is_active=True,
                    created_at=sa.func.now(),
                )
            )
            # sa.table()'s lightweight construct has no declared primary key,
            # so inserted_primary_key isn't available - (tenant_id, name) is
            # unique among just-inserted rows, so look it back up instead.
            template_id = bind.execute(
                sa.text("SELECT id FROM approval_chain_templates WHERE tenant_id = :tid AND name = :name"),
                {"tid": tenant_id, "name": name},
            ).scalar_one()
            for sequence, approver_tier in enumerate(tiers, start=1):
                bind.execute(
                    tiers_table.insert().values(template_id=template_id, sequence=sequence, approver_tier=approver_tier)
                )


def downgrade() -> None:
    op.drop_table("approval_step_instances")
    op.drop_table("approval_chain_instances")
    op.drop_table("approval_chain_tiers")
    op.drop_table("approval_chain_templates")

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("approver_tier", sa.String(length=100), nullable=False),
        sa.Column("deal_size", sa.Float(), nullable=False),
        sa.Column("status", approval_status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_id"),
    )
    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("approval_request_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_request_id"),
    )
