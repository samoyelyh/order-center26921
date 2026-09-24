"""Persist top-two candidate scores for conservative review decisions."""

from alembic import op
import sqlalchemy as sa

revision = "0004_match_score_distribution"
down_revision = "0003_match_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_items", sa.Column("second_match_score", sa.Float(), nullable=True))
    op.add_column("order_items", sa.Column("score_gap", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("order_items", "score_gap")
    op.drop_column("order_items", "second_match_score")
