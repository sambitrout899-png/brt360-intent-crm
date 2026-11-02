"""Commercial licensing (app/models/subscriptions.py): the platform's product catalog,
editions and settings; each organization's subscription, its lines, user licences,
invoices, requests and licence history."""
from alembic import op
import sqlalchemy as sa

revision = "c5e7a9b1d3f4"
down_revision = "b4d6f8a0c2e3"
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
    _create(
        "license_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("unit", sa.String(10), nullable=False),
        sa.Column("unit_label", sa.String(40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("modules", sa.JSON(), nullable=True),
        sa.Column("prices", sa.JSON(), nullable=True),
        sa.Column("annual_discount_pct", sa.Float(), nullable=False),
        sa.Column("min_quantity", sa.Integer(), nullable=False),
        sa.Column("max_quantity", sa.Integer(), nullable=True),
        sa.Column("requires", sa.JSON(), nullable=True),
        sa.Column("limits", sa.JSON(), nullable=True),
        sa.Column("trial_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _create(
        "license_editions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=True),
        sa.Column("discount_pct", sa.Float(), nullable=False),
        sa.Column("trial_days", sa.Integer(), nullable=False),
        sa.Column("highlight", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _create(
        "licensing_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("currencies", sa.JSON(), nullable=True),
        sa.Column("default_currency", sa.String(3), nullable=False),
        sa.Column("tax_name", sa.String(30), nullable=False),
        sa.Column("tax_pct", sa.Float(), nullable=False),
        sa.Column("grace_days", sa.Integer(), nullable=False),
        sa.Column("invoice_prefix", sa.String(20), nullable=False),
        sa.Column("next_invoice_no", sa.Integer(), nullable=False),
        sa.Column("seller_name", sa.String(200), nullable=False),
        sa.Column("seller_address", sa.Text(), nullable=False),
        sa.Column("seller_tax_id", sa.String(60), nullable=False),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _create(
        "tenant_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("edition_code", sa.String(60), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("billing_cycle", sa.String(10), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("term_start", sa.Date(), nullable=True),
        sa.Column("term_end", sa.Date(), nullable=True),
        sa.Column("trial_ends", sa.Date(), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False),
        sa.Column("discount_pct", sa.Float(), nullable=False),
        sa.Column("tax_pct", sa.Float(), nullable=True),
        sa.Column("grace_days", sa.Integer(), nullable=True),
        sa.Column("auto_assign_seats", sa.Boolean(), nullable=False),
        sa.Column("contract_ref", sa.String(80), nullable=False),
        sa.Column("po_number", sa.String(80), nullable=False),
        sa.Column("billing_contact", sa.String(200), nullable=False),
        sa.Column("billing_email", sa.String(200), nullable=False),
        sa.Column("billing_address", sa.Text(), nullable=False),
        sa.Column("tax_id", sa.String(60), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("status_reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _create(
        "subscription_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("tenant_subscriptions.id"), nullable=False, index=True),
        sa.Column("product_code", sa.String(60), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=True),
        sa.Column("discount_pct", sa.Float(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("subscription_id", "product_code", name="uq_subscription_line_product"),
    )
    _create(
        "user_licenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("product_code", sa.String(60), nullable=False),
        sa.Column("assigned_by", sa.String(200), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "product_code", name="uq_user_license"),
    )
    _create(
        "license_invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("number", sa.String(40), nullable=False, unique=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=True),
        sa.Column("subtotal", sa.Float(), nullable=False),
        sa.Column("discount", sa.Float(), nullable=False),
        sa.Column("tax", sa.Float(), nullable=False),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("payment_ref", sa.String(120), nullable=False),
        sa.Column("bill_to", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _create(
        "license_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("product_code", sa.String(60), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    _create(
        "license_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True, index=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade():
    for table in ("license_events", "license_requests", "license_invoices", "user_licenses", "subscription_lines",
                  "tenant_subscriptions", "licensing_settings", "license_editions", "license_products"):
        op.drop_table(table)
