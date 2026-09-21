"""order imports and category isolation

Phase A-D: retain raw order ZIP/JSON as Assets, normalize order items once,
and keep each import's source snapshot for audit and reprocessing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0009_order_imports"
down_revision: str | None = "0008_asset_image_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW6 = sa.func.now(6)
TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if "category_code" not in _columns(bind, "design_packages"):
        op.add_column(
            "design_packages",
            sa.Column("category_code", mysql.VARCHAR(64), nullable=False, server_default="UNKNOWN"),
        )
        op.add_column(
            "design_packages",
            sa.Column("category_name", mysql.VARCHAR(128), nullable=False, server_default="未知品类"),
        )
        op.create_index("ix_design_packages_category", "design_packages", ["category_code"])
        op.alter_column("design_packages", "category_code", server_default=None)
        op.alter_column("design_packages", "category_name", server_default=None)

    # BLAKE3 is useful for every file (ZIP/JSON/PSD included); pHash remains optional.
    constraints = {c.get("name") for c in sa.inspect(bind).get_check_constraints("assets")}
    if "ck_assets_fingerprint_pair" in constraints:
        op.drop_constraint("ck_assets_fingerprint_pair", "assets", type_="check")

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
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(["raw_zip_asset_id"], ["assets.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["duplicate_of_batch_id"], ["order_import_batches.id"], ondelete="SET NULL"),
            sa.CheckConstraint("status IN ('PARSED','PARTIAL','FAILED','DUPLICATE')", name="ck_order_import_batches_status"),
            **TABLE_ARGS,
        )
        op.create_index("ix_order_import_batches_zip_hash", "order_import_batches", ["zip_blake3"])
        op.create_index("ix_order_import_batches_created", "order_import_batches", ["created_at"])

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
            sa.Column("parsed_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("raw_json_hash", mysql.VARCHAR(64), nullable=False),
            sa.Column("normalized_payload", mysql.JSON(), nullable=True),
            sa.Column("matched_variant_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("matched_material_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("match_method", mysql.VARCHAR(32), nullable=True),
            sa.Column("match_score", sa.Float(), nullable=True),
            sa.Column("match_status", mysql.VARCHAR(24), nullable=False, server_default="UNMATCHED"),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(["first_import_batch_id"], ["order_import_batches.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("dedupe_key", name="uq_order_items_dedupe_key"),
            sa.CheckConstraint("quantity >= 0", name="ck_order_items_quantity_nonnegative"),
            sa.CheckConstraint("match_status IN ('UNMATCHED','REVIEW_REQUIRED','CONFIRMED','FAILED')", name="ck_order_items_match_status"),
            **TABLE_ARGS,
        )
        op.create_index("ix_order_items_order", "order_items", ["order_id"])
        op.create_index("ix_order_items_asin", "order_items", ["child_asin"])
        op.create_index("ix_order_items_category", "order_items", ["category_code"])

    if not sa.inspect(bind).has_table("order_import_batch_items"):
        op.create_table(
            "order_import_batch_items",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("import_batch_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("order_item_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("raw_json_asset_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("raw_json_path", mysql.VARCHAR(1024), nullable=False),
            sa.Column("raw_json_hash", mysql.VARCHAR(64), nullable=False),
            sa.Column("parser_version", mysql.VARCHAR(64), nullable=False),
            sa.Column("parse_status", mysql.VARCHAR(24), nullable=False),
            sa.Column("normalized_payload", mysql.JSON(), nullable=True),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(["import_batch_id"], ["order_import_batches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["raw_json_asset_id"], ["assets.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("import_batch_id", "order_item_id", name="uq_order_import_batch_item"),
            sa.CheckConstraint("parse_status IN ('PARSED','REVIEW_REQUIRED','FAILED')", name="ck_order_import_batch_items_status"),
            **TABLE_ARGS,
        )
        op.create_index("ix_order_import_batch_items_batch", "order_import_batch_items", ["import_batch_id"])

    if not sa.inspect(bind).has_table("order_buyer_assets"):
        op.create_table(
            "order_buyer_assets",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("order_item_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("asset_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("role", mysql.VARCHAR(24), nullable=False),
            sa.Column("source_url", mysql.VARCHAR(2048), nullable=True),
            sa.Column("source_path", mysql.VARCHAR(1024), nullable=True),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
            sa.CheckConstraint("role IN ('BUYER_LOGO_ORIGINAL','BUYER_LOGO_SVG')", name="ck_order_buyer_assets_role"),
            **TABLE_ARGS,
        )
        op.create_index("ix_order_buyer_assets_item", "order_buyer_assets", ["order_item_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("order_buyer_assets", "order_import_batch_items", "order_items", "order_import_batches"):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
    if "category_code" in _columns(bind, "design_packages"):
        op.drop_index("ix_design_packages_category", table_name="design_packages")
        op.drop_column("design_packages", "category_name")
        op.drop_column("design_packages", "category_code")
    constraints = {c.get("name") for c in sa.inspect(bind).get_check_constraints("assets")}
    if "ck_assets_fingerprint_pair" not in constraints:
        op.create_check_constraint("ck_assets_fingerprint_pair", "assets", "(blake3 IS NULL) = (phash IS NULL)")
