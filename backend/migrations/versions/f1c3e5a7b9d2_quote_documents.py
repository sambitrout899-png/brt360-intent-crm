"""The PDFs of a quote (proposal, PO, agreement, others) and the tenant's document templates."""
from alembic import op
import sqlalchemy as sa

revision = "f1c3e5a7b9d2"
down_revision = "e8b0d2f4a6c9"
branch_labels = depends_on = None


def upgrade():
    op.create_table("quote_documents", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quotes.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False), sa.Column("title", sa.String(200), nullable=False),
        sa.Column("file_name", sa.String(200), nullable=False), sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False), sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True), sa.Column("note", sa.String(300), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_quote_documents_quote_id", "quote_documents", ["quote_id"])
    op.create_table("quote_document_templates", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False), sa.Column("intro", sa.Text(), nullable=False),
        sa.Column("terms", sa.Text(), nullable=False), sa.Column("closing", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "kind", name="uq_quote_document_templates_kind"))
    for table in ("quote_documents", "quote_document_templates"):
        op.add_column(table, sa.Column("brt_id", sa.String(20), nullable=False))
        for name in ("sys_created_at", "sys_updated_at"):
            op.add_column(table, sa.Column(name, sa.DateTime(), nullable=True))
        for name in ("sys_created_by", "sys_created_position_id", "sys_department_id", "sys_updated_by"):
            op.add_column(table, sa.Column(name, sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_brt_id", table, ["brt_id"], unique=True)


def downgrade():
    op.drop_table("quote_document_templates")
    op.drop_table("quote_documents")
