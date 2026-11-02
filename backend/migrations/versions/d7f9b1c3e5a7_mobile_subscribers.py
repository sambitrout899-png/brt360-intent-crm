"""Mobile subscribers (app/models/mobile.py): prepaid and postpaid lines, packs, wallet
movements, recharge vouchers, wholesale partners (MVNO / MVNE / reseller / dealer), their
settlements and each line's history."""
from alembic import op
import sqlalchemy as sa

revision = "d7f9b1c3e5a7"
down_revision = "c5e7a9b1d3f4"
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


def _tenant():
    return sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True)


def upgrade():
    _create(
        "mb_partners",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("wholesale_rates", sa.JSON(), nullable=True),
        sa.Column("revenue_share_pct", sa.Float(), nullable=False),
        sa.Column("number_range", sa.String(60), nullable=False),
        sa.Column("contact_email", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _create(
        "mb_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id"), nullable=False, index=True),
        sa.Column("partner_id", sa.Integer(), sa.ForeignKey("mb_partners.id"), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("order_item_id", sa.Integer(), nullable=True, index=True),
        sa.Column("asset_id", sa.Integer(), nullable=True, index=True),
        sa.Column("plan_charge", sa.String(10), nullable=False),
        sa.Column("msisdn", sa.String(20), nullable=False),
        sa.Column("iccid", sa.String(25), nullable=False),
        sa.Column("imsi", sa.String(20), nullable=False),
        sa.Column("sim_type", sa.String(10), nullable=False),
        sa.Column("user_name", sa.String(200), nullable=False),
        sa.Column("plan", sa.String(200), nullable=False),
        sa.Column("plan_profile", sa.JSON(), nullable=True),
        sa.Column("payment_type", sa.String(10), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("status_reason", sa.String(300), nullable=False),
        sa.Column("barrings", sa.JSON(), nullable=True),
        sa.Column("roaming", sa.Boolean(), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("advance_owed", sa.Float(), nullable=False),
        sa.Column("credit_limit", sa.Float(), nullable=False),
        sa.Column("spend_alert_pct", sa.Integer(), nullable=False),
        sa.Column("cycle_start", sa.Date(), nullable=True),
        sa.Column("cycle_spend", sa.Float(), nullable=False),
        sa.Column("alert_sent", sa.Boolean(), nullable=False),
        sa.Column("deposit", sa.Float(), nullable=False),
        sa.Column("group_head_id", sa.Integer(), nullable=True),
        sa.Column("swap_cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("previous_msisdns", sa.JSON(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("terminated_at", sa.DateTime(), nullable=True),
        sa.Column("quarantine_until", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "msisdn", name="uq_mb_line_msisdn"),
    )
    _create(
        "mb_packs",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("mb_lines.id"), nullable=False, index=True),
        sa.Column("code", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(15), nullable=False),
        sa.Column("granted", sa.JSON(), nullable=True),
        sa.Column("remaining", sa.JSON(), nullable=True),
        sa.Column("roaming", sa.Boolean(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("paid_from", sa.String(10), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_to", sa.DateTime(), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _create(
        "mb_wallet_txns",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("mb_lines.id"), nullable=False, index=True),
        sa.Column("partner_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("balance_after", sa.Float(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(120), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False, index=True),
    )
    _create(
        "mb_vouchers",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("batch", sa.String(30), nullable=False, index=True),
        sa.Column("serial", sa.String(40), nullable=False),
        sa.Column("pin_hash", sa.String(64), nullable=False, index=True),
        sa.Column("face_value", sa.Float(), nullable=False),
        sa.Column("validity_days", sa.Integer(), nullable=False),
        sa.Column("partner_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("redeemed_line_id", sa.Integer(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("pending_pins", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "serial", name="uq_mb_voucher_serial"),
    )
    _create(
        "mb_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("partner_id", sa.Integer(), sa.ForeignKey("mb_partners.id"), nullable=False, index=True),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=True),
        sa.Column("wholesale_due", sa.Float(), nullable=False),
        sa.Column("commission_due", sa.Float(), nullable=False),
        sa.Column("net", sa.Float(), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _create(
        "mb_line_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("mb_lines.id"), nullable=False, index=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("summary", sa.String(400), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade():
    for table in ("mb_line_events", "mb_settlements", "mb_vouchers", "mb_wallet_txns", "mb_packs", "mb_lines", "mb_partners"):
        op.drop_table(table)
