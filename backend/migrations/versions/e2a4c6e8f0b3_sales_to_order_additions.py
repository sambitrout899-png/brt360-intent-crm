"""Sales-to-Order (B2B) and B2C additions - purely additive.

Nothing existing is removed or rewritten:
- new stages/statuses: opportunity configured / in_feasibility /
  feasibility_failed / feasible; order pending_approval / rejected
- new tables beside the existing records (their own columns - and any
  custom field keys tenants chose - stay as they are): party_profiles,
  opportunity_configs, quote_purchase_orders, order_profiles,
  order_milestones, price_lists, price_list_items, process_roles
- approval rules gain an optional kind (monetary / record), the state a
  record rule gates, an optional deal-value band and a position per tier;
  existing rules are labelled with the kind they always were (quote ->
  monetary, lead/order -> record gate) and keep working unchanged - their
  tiers stay unbound, so they are decided exactly as before
- an approval is now unique per record *and gate* ("discount", or the state
  it unlocks) instead of per record; existing approvals keep their meaning
- every tenant gets the process-role positions (Account Manager, Presales,
  Sales Ops Head, CPMO, Installation team), the Standard / Enterprise /
  Strategic pricelists (Standard is 0%, so prices don't change) and the
  pricing attributes - each only where the tenant has none yet. No new
  approval rule is switched on for an existing tenant.

Revision ID: e2a4c6e8f0b3
Revises: a1b2c3d4e5f6
"""

import datetime as dt
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2a4c6e8f0b3"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENUMS = {
    # (table, column, enum name): (old values, added values)
    ("opportunities", "stage", "opportunitystage"): (
        ("draft", "qualified", "quoted", "won", "lost"),
        ("configured", "in_feasibility", "feasibility_failed", "feasible"),
    ),
    ("orders", "status", "orderstatus"): (
        ("created", "activating", "active", "changed", "suspended", "pending_cancellation", "cancelled", "terminated"),
        ("pending_approval", "rejected"),
    ),
}

PROCESS_ROLES = {
    "account_manager": "Account Manager",
    "presales": "Presales",
    "sales_ops": "Sales Ops Head",
    "cpmo": "CPMO",
    "bsd": "Installation team (BSD)",
}
PRICE_LISTS = [("Standard", 0.0, True), ("Enterprise", -5.0, False), ("Strategic", -10.0, False)]
PRICING_ATTRIBUTES = [
    ("Bandwidth", "enum", "Mbps", "10 Mbps,50 Mbps,100 Mbps,200 Mbps,500 Mbps,1 Gbps,10 Gbps"),
    ("Contract term", "enum", "months", "12 months,24 months,36 months,60 months"),
    ("SLA", "enum", None, "99.5%,99.9%,99.95%,99.99%"),
    ("Number of sites", "enum", None, "1,2,3,5,10,25,50"),
    ("Static IPs", "enum", None, "None,1,8,16,32"),
]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _grow_enums() -> None:
    bind = op.get_bind()
    for (table, column, name), (old, added) in ENUMS.items():
        if bind.dialect.name == "postgresql":
            with op.get_context().autocommit_block():
                for value in added:
                    op.execute(f"ALTER TYPE {name} ADD VALUE IF NOT EXISTS '{value}'")
        else:
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(
                    column, existing_type=sa.Enum(*old, name=name), type_=sa.Enum(*old, *added, name=name)
                )


def _shrink_enums() -> None:
    if op.get_bind().dialect.name == "postgresql":
        return  # Postgres can't drop enum values; unused ones are harmless
    for (table, column, name), (old, added) in ENUMS.items():
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column, existing_type=sa.Enum(*old, *added, name=name), type_=sa.Enum(*old, name=name)
            )


def _rebuild(bind) -> str:
    """batch_alter_table's recreate mode: SQLite rebuilds the table (it can't
    drop a constraint in place); Postgres alters it in place."""
    return "always" if bind.dialect.name == "sqlite" else "auto"


def _entity_unique_name(bind) -> str | None:
    for constraint in sa.inspect(bind).get_unique_constraints("approval_chain_instances"):
        if sorted(constraint["column_names"]) == ["entity_id", "entity_type"]:
            return constraint["name"]
    return None


def _create_tables() -> None:
    op.create_table(
        "price_lists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        sa.Column("adjustment_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_price_lists_tenant_name"),
    )
    op.create_table(
        "price_list_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("price_list_id", sa.Integer(), sa.ForeignKey("price_lists.id"), nullable=False),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.UniqueConstraint("price_list_id", "offering_name", name="uq_price_list_items_offering"),
    )
    op.create_table(
        "process_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("role_key", sa.String(30), nullable=False),
        sa.Column("position_id", sa.Integer(), sa.ForeignKey("positions.id"), nullable=True),
        sa.UniqueConstraint("tenant_id", "role_key", name="uq_process_roles_tenant_role"),
    )
    op.create_table(
        "party_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id"), nullable=False, unique=True),
        sa.Column("segment", sa.String(10), nullable=True),
        sa.Column("account_status", sa.String(20), nullable=True),
        sa.Column("price_list_id", sa.Integer(), sa.ForeignKey("price_lists.id"), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
    )
    op.create_table(
        "opportunity_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id"), nullable=False, unique=True),
        sa.Column("pricing_attributes_json", sa.Text(), nullable=True),
        sa.Column("site_address", sa.String(300), nullable=True),
        sa.Column("technical_parameters", sa.Text(), nullable=True),
        sa.Column("feasibility_notes", sa.Text(), nullable=True),
        sa.Column("account_manager_user_id", sa.Integer(), nullable=True),
    )
    op.create_table(
        "quote_purchase_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quotes.id"), nullable=False, unique=True),
        sa.Column("po_number", sa.String(100), nullable=False),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("document_ref", sa.String(300), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("matched_at", sa.DateTime(), nullable=True),
        sa.Column("match_notes", sa.Text(), nullable=True),
    )
    op.create_table(
        "order_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False, unique=True),
        sa.Column("segment", sa.String(10), nullable=True),
        sa.Column("requested_discount_pct", sa.Float(), nullable=True),
    )
    op.create_table(
        "order_milestones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("reached_at", sa.DateTime(), nullable=False),
    )


def _extend_approvals() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("approval_chain_templates") as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("trigger_state", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("min_amount", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("max_amount", sa.Float(), nullable=True))
    with op.batch_alter_table("approval_chain_tiers") as batch_op:
        batch_op.add_column(sa.Column("position_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_approval_chain_tiers_position_id", "positions", ["position_id"], ["id"])

    old_unique = _entity_unique_name(bind)
    # SQLite needs the table rebuilt to drop its (possibly unnamed) unique constraint;
    # Postgres alters in place - a rebuild there would drop the primary key other tables reference
    with op.batch_alter_table("approval_chain_instances", recreate=_rebuild(bind)) as batch_op:
        if old_unique:
            batch_op.drop_constraint(old_unique, type_="unique")
        batch_op.add_column(sa.Column("gate", sa.String(50), nullable=False, server_default="discount"))
        batch_op.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("summary", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("requested_by_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("decided_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key("fk_approval_chain_instances_tenant_id", "tenants", ["tenant_id"], ["id"])
    with op.batch_alter_table("approval_step_instances") as batch_op:
        batch_op.add_column(sa.Column("position_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("decided_by_user_id", sa.Integer(), nullable=True))

    # Existing rules and approvals, labelled with what they always were.
    op.execute("UPDATE approval_chain_templates SET kind = 'monetary' WHERE entity_type = 'quote'")
    op.execute("UPDATE approval_chain_templates SET kind = 'record', trigger_state = 'qualified' WHERE entity_type = 'lead'")
    op.execute("UPDATE approval_chain_templates SET kind = 'record', trigger_state = 'activating' WHERE entity_type = 'order'")
    op.execute("UPDATE approval_chain_instances SET gate = 'qualified' WHERE entity_type = 'lead'")
    op.execute("UPDATE approval_chain_instances SET gate = 'activating' WHERE entity_type = 'order'")
    op.execute(
        "UPDATE approval_chain_instances SET tenant_id = "
        "(SELECT t.tenant_id FROM approval_chain_templates t WHERE t.id = approval_chain_instances.template_id)"
    )
    with op.batch_alter_table("approval_chain_instances") as batch_op:
        batch_op.create_unique_constraint("uq_approval_chain_instances_entity_gate", ["entity_type", "entity_id", "gate"])


def _seed_tenants() -> None:
    bind = op.get_bind()
    now = _now()
    for (tenant_id,) in bind.execute(sa.text("SELECT id FROM tenants")).fetchall():
        for role_key, name in PROCESS_ROLES.items():
            position_id = bind.execute(
                sa.text("SELECT id FROM positions WHERE tenant_id = :t AND name = :n"), {"t": tenant_id, "n": name}
            ).scalar()
            if position_id is None:
                bind.execute(
                    sa.text("INSERT INTO positions (tenant_id, name, data_scope, is_active, created_at) "
                            "VALUES (:t, :n, 'org', :active, :now)"),
                    {"t": tenant_id, "n": name, "active": True, "now": now},
                )
                position_id = bind.execute(
                    sa.text("SELECT id FROM positions WHERE tenant_id = :t AND name = :n"), {"t": tenant_id, "n": name}
                ).scalar()
            bind.execute(
                sa.text("INSERT INTO process_roles (tenant_id, role_key, position_id) VALUES (:t, :k, :p)"),
                {"t": tenant_id, "k": role_key, "p": position_id},
            )
        for name, adjustment, is_default in PRICE_LISTS:
            bind.execute(
                sa.text("INSERT INTO price_lists (tenant_id, name, currency, adjustment_pct, is_default, is_active, created_at) "
                        "VALUES (:t, :n, 'INR', :a, :d, :active, :now)"),
                {"t": tenant_id, "n": name, "a": adjustment, "d": is_default, "active": True, "now": now},
            )
        has_attributes = bind.execute(
            sa.text("SELECT COUNT(*) FROM product_attribute_definitions WHERE tenant_id = :t"), {"t": tenant_id}
        ).scalar()
        if not has_attributes:
            for name, data_type, unit, allowed in PRICING_ATTRIBUTES:
                bind.execute(
                    sa.text("INSERT INTO product_attribute_definitions (tenant_id, name, data_type, unit, allowed_values, created_at) "
                            "VALUES (:t, :n, :d, :u, :v, :now)"),
                    {"t": tenant_id, "n": name, "d": data_type, "u": unit, "v": allowed, "now": now},
                )


def upgrade() -> None:
    _grow_enums()
    _create_tables()
    _extend_approvals()
    _seed_tenants()


def downgrade() -> None:
    """Back to the previous schema. Removes only what upgrade added (the
    Sales-to-Order tables and columns); records created through the new
    steps lose those details. Records in a new stage/status keep that value
    in the database - move them to an older stage first."""
    with op.batch_alter_table("approval_chain_instances") as batch_op:
        batch_op.drop_constraint("uq_approval_chain_instances_entity_gate", type_="unique")
    # Only one approval per record survives the older unique rule: keep the
    # record's classic one.
    op.execute(
        "DELETE FROM approval_step_instances WHERE chain_instance_id IN (SELECT id FROM approval_chain_instances WHERE NOT ("
        "(entity_type = 'quote' AND gate = 'discount') OR (entity_type = 'lead' AND gate = 'qualified') OR "
        "(entity_type = 'order' AND gate = 'activating')))"
    )
    op.execute(
        "DELETE FROM approval_chain_instances WHERE NOT ((entity_type = 'quote' AND gate = 'discount') OR "
        "(entity_type = 'lead' AND gate = 'qualified') OR (entity_type = 'order' AND gate = 'activating'))"
    )
    with op.batch_alter_table("approval_chain_instances", recreate=_rebuild(op.get_bind())) as batch_op:
        batch_op.drop_constraint("fk_approval_chain_instances_tenant_id", type_="foreignkey")
        for column in ("decided_at", "requested_by_user_id", "summary", "tenant_id", "gate"):
            batch_op.drop_column(column)
        batch_op.create_unique_constraint("uq_approval_chain_instances_entity", ["entity_type", "entity_id"])
    with op.batch_alter_table("approval_step_instances") as batch_op:
        batch_op.drop_column("decided_by_user_id")
        batch_op.drop_column("position_id")
    with op.batch_alter_table("approval_chain_tiers") as batch_op:
        batch_op.drop_constraint("fk_approval_chain_tiers_position_id", type_="foreignkey")
        batch_op.drop_column("position_id")
    with op.batch_alter_table("approval_chain_templates") as batch_op:
        for column in ("max_amount", "min_amount", "trigger_state", "kind"):
            batch_op.drop_column(column)
    for table in ("order_milestones", "order_profiles", "quote_purchase_orders", "opportunity_configs",
                  "party_profiles", "process_roles", "price_list_items", "price_lists"):
        op.drop_table(table)
    _shrink_enums()
