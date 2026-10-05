"""phase 4: prospect-to-order business process overhaul

Generalizes approval chains to polymorphic entity_type/entity_id (so the
same engine also gates Lead -> Opportunity Sales Director qualification,
not just Quotes), adds Lead.status/interested_offering_name, a
'cancelled' QuoteStatus value (INT-20 - withdrawn by the requester,
distinct from a rejected decision), and four new tables: ponr_policies
(configurable point-of-no-return for revise/cancel gating on quotes and
orders), and feasibility_check_types/feasibility_assessments/
feasibility_check_results (configurable multi-resource feasibility
assessment at the Opportunity level, B2B).

Critical backfills:
- approval_chain_instances.entity_id <- quote_id, entity_type <- 'quote'
  for every existing row - they were all quote-chain instances before
  this generalization.
- leads.status <- 'qualified' for every EXISTING lead - they were all
  created under the old always-auto-qualify behavior and already have an
  Opportunity, so the new column's application-level default
  ('pending_qualification') would wrongly re-gate them.

Revision ID: 539d4e4f563a
Revises: 137090a72994
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '539d4e4f563a'
down_revision: Union[str, Sequence[str], None] = '137090a72994'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_QUOTE_STATUS_VALUES = ("draft", "pending_approval", "approved", "rejected")
NEW_QUOTE_STATUS_VALUES = ("draft", "pending_approval", "approved", "rejected", "cancelled")

leadstatus_enum = sa.Enum("pending_qualification", "qualified", "rejected", name="leadstatus")
feasibilityoutcome_enum = sa.Enum("pending", "passed", "failed", name="feasibilityoutcome")


def _find_constraint(bind, table: str, column: str, contype: str) -> str | None:
    """Look up a Postgres constraint by table+column+type, never by a
    guessed auto-generated name - learned the hard way on the
    product_specifications/territory_rules stale-constraint bug."""
    return bind.execute(
        sa.text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY(con.conkey) "
            "WHERE rel.relname = :table AND con.contype = :contype AND att.attname = :column"
        ),
        {"table": table, "column": column, "contype": contype},
    ).scalar_one_or_none()


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # --- ApprovalChainTemplate.entity_type -----------------------------
    op.add_column(
        "approval_chain_templates",
        sa.Column("entity_type", sa.String(length=30), nullable=False, server_default="quote"),
    )

    # --- ApprovalChainInstance: quote_id -> entity_type/entity_id ------
    op.add_column(
        "approval_chain_instances",
        sa.Column("entity_type", sa.String(length=30), nullable=False, server_default="quote"),
    )
    op.add_column("approval_chain_instances", sa.Column("entity_id", sa.Integer(), nullable=True))
    op.execute("UPDATE approval_chain_instances SET entity_id = quote_id")

    if is_pg:
        with op.batch_alter_table("approval_chain_instances") as batch_op:
            batch_op.alter_column("entity_id", nullable=False)
        unique_name = _find_constraint(bind, "approval_chain_instances", "quote_id", "u")
        fk_name = _find_constraint(bind, "approval_chain_instances", "quote_id", "f")
        if unique_name:
            op.drop_constraint(unique_name, "approval_chain_instances", type_="unique")
        if fk_name:
            op.drop_constraint(fk_name, "approval_chain_instances", type_="foreignkey")
        op.drop_column("approval_chain_instances", "quote_id")
        op.create_unique_constraint(
            "uq_approval_chain_instances_entity", "approval_chain_instances", ["entity_type", "entity_id"]
        )
    else:
        # SQLite's batch mode rebuilds the whole table from the reflected
        # structure plus these operations - dropping quote_id here also
        # drops the (unnamed, auto-indexed) UNIQUE/FK that referenced it,
        # so there is nothing to explicitly drop_constraint() by name.
        with op.batch_alter_table("approval_chain_instances") as batch_op:
            batch_op.alter_column("entity_id", nullable=False)
            batch_op.drop_column("quote_id")
            batch_op.create_unique_constraint(
                "uq_approval_chain_instances_entity", ["entity_type", "entity_id"]
            )

    # --- Lead.status / Lead.interested_offering_name -------------------
    leadstatus_enum.create(bind, checkfirst=True)
    op.add_column(
        "leads",
        sa.Column(
            "status", sa.Enum("pending_qualification", "qualified", "rejected", name="leadstatus", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("leads", sa.Column("interested_offering_name", sa.String(length=200), nullable=True))
    # Every lead that already exists predates the Sales Director gate and
    # already has an Opportunity (the old code always auto-qualified) -
    # the new column's ORM-level default of pending_qualification would
    # be wrong for these, so backfill explicitly before making it required.
    op.execute("UPDATE leads SET status = 'qualified' WHERE status IS NULL")
    with op.batch_alter_table("leads") as batch_op:
        batch_op.alter_column("status", nullable=False)

    # --- QuoteStatus gains 'cancelled' ----------------------------------
    if is_pg:
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE quotestatus ADD VALUE IF NOT EXISTS 'cancelled'")
    else:
        with op.batch_alter_table("quotes") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(*OLD_QUOTE_STATUS_VALUES, name="quotestatus"),
                type_=sa.Enum(*NEW_QUOTE_STATUS_VALUES, name="quotestatus"),
            )

    # --- PonrPolicy ------------------------------------------------------
    op.create_table(
        "ponr_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("ponr_statuses", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- Feasibility ------------------------------------------------------
    feasibilityoutcome_enum.create(bind, checkfirst=True)
    outcome_col = lambda: sa.Enum(  # noqa: E731
        "pending", "passed", "failed", name="feasibilityoutcome", create_type=False
    )
    op.create_table(
        "feasibility_check_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "feasibility_assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("outcome", outcome_col(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "feasibility_check_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("check_type_name", sa.String(length=150), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("outcome", outcome_col(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["feasibility_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.drop_table("feasibility_check_results")
    op.drop_table("feasibility_assessments")
    op.drop_table("feasibility_check_types")
    op.drop_table("ponr_policies")
    if is_pg:
        sa.Enum(name="feasibilityoutcome").drop(bind, checkfirst=True)

    if not is_pg:
        with op.batch_alter_table("quotes") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(*NEW_QUOTE_STATUS_VALUES, name="quotestatus"),
                type_=sa.Enum(*OLD_QUOTE_STATUS_VALUES, name="quotestatus"),
            )
    # Postgres enums can't drop a value natively - same accepted tradeoff
    # as the pending_cancellation OrderStatus addition; downgrade leaves
    # 'cancelled' defined but unused there.

    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_column("interested_offering_name")
        batch_op.drop_column("status")
    if is_pg:
        sa.Enum(name="leadstatus").drop(bind, checkfirst=True)

    if is_pg:
        # Drop the composite constraint before the columns it references -
        # Postgres refuses to drop a column a constraint still depends on.
        op.drop_constraint("uq_approval_chain_instances_entity", "approval_chain_instances", type_="unique")
        op.add_column("approval_chain_instances", sa.Column("quote_id", sa.Integer(), nullable=True))
        op.execute("UPDATE approval_chain_instances SET quote_id = entity_id")
        op.drop_column("approval_chain_instances", "entity_id")
        op.drop_column("approval_chain_instances", "entity_type")
        with op.batch_alter_table("approval_chain_instances") as batch_op:
            batch_op.alter_column("quote_id", nullable=False)
        op.create_foreign_key(
            "approval_chain_instances_quote_id_fkey", "approval_chain_instances", "quotes", ["quote_id"], ["id"]
        )
        op.create_unique_constraint("approval_chain_instances_quote_id_key", "approval_chain_instances", ["quote_id"])
    else:
        with op.batch_alter_table("approval_chain_instances") as batch_op:
            batch_op.add_column(sa.Column("quote_id", sa.Integer(), nullable=True))
        op.execute("UPDATE approval_chain_instances SET quote_id = entity_id")
        with op.batch_alter_table("approval_chain_instances") as batch_op:
            batch_op.alter_column("quote_id", nullable=False)
            batch_op.drop_constraint("uq_approval_chain_instances_entity", type_="unique")
            batch_op.drop_column("entity_id")
            batch_op.drop_column("entity_type")
            batch_op.create_foreign_key("approval_chain_instances_quote_id_fkey", "quotes", ["quote_id"], ["id"])
            batch_op.create_unique_constraint("approval_chain_instances_quote_id_key", ["quote_id"])

    op.drop_column("approval_chain_templates", "entity_type")
