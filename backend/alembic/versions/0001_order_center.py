"""order-center 订单域初始迁移：建全部订单表

独立项目：不含素材域表（material_platform 的表归属另一个库）。
表：
  order_assets / order_import_batches / order_items / order_import_batch_items /
  order_buyer_assets / material_url_bindings / asin_variant_bindings / order_match_actions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_order_center"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    bind = op.get_bind()

    if not sa.inspect(bind).has_table("order_assets"):
        op.create_table(
            "order_assets",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("storage_key", mysql.VARCHAR(512), nullable=False),
            sa.Column("original_filename", mysql.VARCHAR(512), nullable=False),
            sa.Column("mime_type", mysql.VARCHAR(128), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("blake3", mysql.VARCHAR(64), nullable=False),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.UniqueConstraint("blake3", name="uq_order_assets_blake3"),
            sa.Index("ix_order_assets_created", "created_at"),
            **TABLE_ARGS,
        )

    if not sa.inspect(bind).has_table("order_import_batches"):
        op.create_table(
            "order_import_batches",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("original_filename", mysql.VARCHAR(512), nullable=False),
            sa.Column("raw_zip_asset_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("zip_blake3", mysql.VARCHAR(64), nullable=False),
            sa.Column("category_code", mysql.VARCHAR(64), nullable=False, server_default="UNKNOWN"),
            sa.Column("category_name", mysql.VARCHAR(128), nullable=False, server_default="未知品类"),
            sa.Column("category_source", mysql.VARCHAR(32), nullable=False, server_default="UNKNOWN"),
            sa.Column("category_confidence", mysql.VARCHAR(16), nullable=False, server_default="0"),
            sa.Column("status", mysql.VARCHAR(24), nullable=False, server_default="PARSED"),
            sa.Column("duplicate_of_batch_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("total_json_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.ForeignKeyConstraint(["raw_zip_asset_id"], ["order_assets.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["duplicate_of_batch_id"], ["order_import_batches.id"], ondelete="SET NULL"),
            sa.CheckConstraint("status IN ('PARSED','PARTIAL','FAILED','DUPLICATE')", name="ck_order_import_batches_status"),
            sa.Index("ix_order_import_batches_zip_hash", "zip_blake3"),
            sa.Index("ix_order_import_batches_created", "created_at"),
            **TABLE_ARGS,
        )

    if not sa.inspect(bind).has_table("order_items"):
        op.create_table(
            "order_items",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("dedupe_key", mysql.VARCHAR(128), nullable=False),
            sa.Column("first_import_batch_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("order_id", mysql.VARCHAR(128), nullable=False),
            sa.Column("order_item_id", mysql.VARCHAR(128), nullable=True),
            sa.Column("child_asin", mysql.VARCHAR(32), nullable=True),
            sa.Column("sku", mysql.VARCHAR(255), nullable=True),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("category_code", mysql.VARCHAR(64), nullable=False, server_default="UNKNOWN"),
            sa.Column("category_name", mysql.VARCHAR(128), nullable=False, server_default="未知品类"),
            sa.Column("category_source", mysql.VARCHAR(32), nullable=False, server_default="UNKNOWN"),
            sa.Column("category_confidence", mysql.VARCHAR(16), nullable=False, server_default="0"),
            sa.Column("parser_version", mysql.VARCHAR(64), nullable=False),
            sa.Column("parsed_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.Column("raw_json_hash", mysql.VARCHAR(64), nullable=False),
            sa.Column("normalized_payload", mysql.JSON(), nullable=True),
            sa.Column("matched_variant_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("matched_material_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("match_method", mysql.VARCHAR(64), nullable=True),
            sa.Column("match_score", sa.Float(), nullable=True),
            sa.Column("match_status", mysql.VARCHAR(24), nullable=False, server_default="UNMATCHED"),
            sa.ForeignKeyConstraint(["first_import_batch_id"], ["order_import_batches.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("dedupe_key", name="uq_order_items_dedupe_key"),
            sa.CheckConstraint("quantity >= 0", name="ck_order_items_quantity_nonnegative"),
            sa.CheckConstraint(
                "match_status IN ('UNMATCHED','REVIEW_REQUIRED','CONFIRMED','FAILED')",
                name="ck_order_items_match_status",
            ),
            sa.Index("ix_order_items_order", "order_id"),
            sa.Index("ix_order_items_asin", "child_asin"),
            sa.Index("ix_order_items_category", "category_code"),
            **TABLE_ARGS,
        )

    if not sa.inspect(bind).has_table("order_import_batch_items"):
        op.create_table(
            "order_import_batch_items",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("import_batch_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("order_item_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("raw_json_asset_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("raw_json_path", mysql.VARCHAR(1024), nullable=True),
            sa.Column("raw_json_hash", mysql.VARCHAR(64), nullable=False),
            sa.Column("parser_version", mysql.VARCHAR(64), nullable=False),
            sa.Column("parse_status", mysql.VARCHAR(24), nullable=False, server_default="PARSED"),
            sa.Column("normalized_payload", mysql.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["import_batch_id"], ["order_import_batches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["raw_json_asset_id"], ["order_assets.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("import_batch_id", "order_item_id", name="uq_order_import_batch_item"),
            sa.CheckConstraint("parse_status IN ('PARSED','PARTIAL','FAILED','DUPLICATE')", name="ck_order_import_batch_items_status"),
            sa.Index("ix_order_import_batch_items_batch", "import_batch_id"),
            **TABLE_ARGS,
        )

    if not sa.inspect(bind).has_table("order_buyer_assets"):
        op.create_table(
            "order_buyer_assets",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("order_item_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("role", mysql.VARCHAR(32), nullable=False),
            sa.Column("asset_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("source_name", mysql.VARCHAR(512), nullable=True),
            sa.Column("source_path", mysql.VARCHAR(1024), nullable=True),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["asset_id"], ["order_assets.id"], ondelete="RESTRICT"),
            sa.Index("ix_order_buyer_assets_item", "order_item_id"),
            sa.CheckConstraint("role IN ('BUYER_LOGO_ORIGINAL','BUYER_LOGO_SVG')", name="ck_order_buyer_assets_role"),
            **TABLE_ARGS,
        )

    if not sa.inspect(bind).has_table("material_url_bindings"):
        op.create_table(
            "material_url_bindings",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("material_url", mysql.VARCHAR(512), nullable=False),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("material_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("match_method", mysql.VARCHAR(32), nullable=False, server_default="MANUAL"),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.UniqueConstraint("material_url", name="uq_material_url_bindings_url"),
            sa.Index("ix_material_url_bindings_variant", "variant_id"),
            **TABLE_ARGS,
        )

    if not sa.inspect(bind).has_table("asin_variant_bindings"):
        op.create_table(
            "asin_variant_bindings",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("child_asin", mysql.VARCHAR(32), nullable=False),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("material_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.UniqueConstraint("child_asin", "variant_id", name="uq_asin_variant_bindings_pair"),
            sa.Index("ix_asin_variant_bindings_asin", "child_asin"),
            **TABLE_ARGS,
        )

    if not sa.inspect(bind).has_table("order_match_actions"):
        op.create_table(
            "order_match_actions",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("order_item_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("action", mysql.VARCHAR(32), nullable=False),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("material_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("note", mysql.TEXT(), nullable=True),
            sa.Column("actor", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
            sa.CheckConstraint(
                "action IN ('MATCH_CONFIRMED','MATCH_CHANGED','MATCH_FAILED','MATCH_AUTO')",
                name="ck_order_match_actions_action",
            ),
            sa.Index("ix_order_match_actions_item", "order_item_id"),
            **TABLE_ARGS,
        )


def downgrade() -> None:
    for table in (
        "order_match_actions",
        "asin_variant_bindings",
        "material_url_bindings",
        "order_buyer_assets",
        "order_import_batch_items",
        "order_items",
        "order_import_batches",
        "order_assets",
    ):
        op.drop_table(table)
