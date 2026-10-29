"""The messaging-app order channel is stored as "chat" (was a product name) -
orders and their channel interactions already recorded on it follow
(app/crm_core/b2c_order_service.py, CHANNELS)."""
from alembic import op

revision = "b8d2f4a6c1e3"
down_revision = "c6f8b0d2e4a7"
branch_labels = depends_on = None


def upgrade():
    for table in ("om_order_details", "om_channel_interactions"):
        op.execute(f"UPDATE {table} SET channel = 'chat' WHERE channel = 'whatsapp'")


def downgrade():
    for table in ("om_order_details", "om_channel_interactions"):
        op.execute(f"UPDATE {table} SET channel = 'whatsapp' WHERE channel = 'chat'")
