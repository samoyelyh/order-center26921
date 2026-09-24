"""Persist machine-readable material match failure reasons."""

from alembic import op
import sqlalchemy as sa

revision = "0003_match_reason"
down_revision = "0002_order_parse_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_items", sa.Column("match_reason", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("order_items", "match_reason")
