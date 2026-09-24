"""Keep parse outcome independent from optional future material matching."""

from alembic import op

revision = "0005_parse_match_state"
down_revision = "0004_match_score_distribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_order_items_match_status", "order_items", type_="check")
    op.create_check_constraint(
        "ck_order_items_match_status", "order_items",
        "match_status IN ('NOT_STARTED','UNMATCHED','REVIEW_REQUIRED','CONFIRMED','FAILED')",
    )
    op.drop_constraint("ck_order_items_parse_status", "order_items", type_="check")
    op.create_check_constraint(
        "ck_order_items_parse_status", "order_items",
        "parse_status IN ('PARSED','PARTIAL','FAILED','REVIEW_REQUIRED','NON_CUSTOM')",
    )
    op.drop_constraint("ck_order_import_batch_items_status", "order_import_batch_items", type_="check")
    op.create_check_constraint(
        "ck_order_import_batch_items_status", "order_import_batch_items",
        "parse_status IN ('PARSED','PARTIAL','FAILED','DUPLICATE','REVIEW_REQUIRED','NON_CUSTOM')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_order_items_match_status", "order_items", type_="check")
    op.create_check_constraint(
        "ck_order_items_match_status", "order_items",
        "match_status IN ('UNMATCHED','REVIEW_REQUIRED','CONFIRMED','FAILED')",
    )
    op.drop_constraint("ck_order_items_parse_status", "order_items", type_="check")
    op.create_check_constraint(
        "ck_order_items_parse_status", "order_items",
        "parse_status IN ('PARSED','PARTIAL','FAILED','REVIEW_REQUIRED')",
    )
    op.drop_constraint("ck_order_import_batch_items_status", "order_import_batch_items", type_="check")
    op.create_check_constraint(
        "ck_order_import_batch_items_status", "order_import_batch_items",
        "parse_status IN ('PARSED','PARTIAL','FAILED','DUPLICATE','REVIEW_REQUIRED')",
    )
