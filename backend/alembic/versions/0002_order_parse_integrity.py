"""补齐订单解析状态、审核排序时间和买家附件来源。

Revision ID: 0002_order_parse_integrity
Revises: 0001_order_center
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0002_order_parse_integrity"
down_revision = "0001_order_center"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_items",
        sa.Column("parse_status", mysql.VARCHAR(24), nullable=False, server_default="PARSED"),
    )
    op.add_column(
        "order_items",
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.func.now(6)),
    )
    op.create_check_constraint(
        "ck_order_items_parse_status",
        "order_items",
        "parse_status IN ('PARSED','PARTIAL','FAILED','REVIEW_REQUIRED')",
    )

    op.alter_column(
        "order_buyer_assets",
        "asset_id",
        existing_type=mysql.VARCHAR(64),
        nullable=True,
    )
    op.add_column(
        "order_buyer_assets",
        sa.Column("source_url", mysql.VARCHAR(1024), nullable=True),
    )

    op.drop_constraint("ck_order_import_batch_items_status", "order_import_batch_items", type_="check")
    op.create_check_constraint(
        "ck_order_import_batch_items_status",
        "order_import_batch_items",
        "parse_status IN ('PARSED','PARTIAL','FAILED','DUPLICATE','REVIEW_REQUIRED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_order_import_batch_items_status", "order_import_batch_items", type_="check")
    op.create_check_constraint(
        "ck_order_import_batch_items_status",
        "order_import_batch_items",
        "parse_status IN ('PARSED','PARTIAL','FAILED','DUPLICATE')",
    )
    op.drop_column("order_buyer_assets", "source_url")
    op.alter_column(
        "order_buyer_assets",
        "asset_id",
        existing_type=mysql.VARCHAR(64),
        nullable=False,
    )
    op.drop_constraint("ck_order_items_parse_status", "order_items", type_="check")
    op.drop_column("order_items", "created_at")
    op.drop_column("order_items", "parse_status")
