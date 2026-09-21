"""order matching bindings (material_url / asin / variant effect images)

阶段 B：素材归因的绑定表（不改动已有 Material / Variant / Batch 体系）：
  1. material_url_bindings  —— 人工确认后的「买家素材 URL → Variant」绑定，URL 命中直接复用，不再跑图匹配
  2. asin_variant_bindings   —— 审核确认时建立「Child ASIN → Variant」绑定。
     现有系统后端没有 Distribution（派发）表，ASIN→Batch→Variant 链路用该绑定替代定位候选。
  3. variant_effect_images   —— Variant 的「最终效果图」（FINAL_EFFECT / Black / White）。
     现有 VariantRevision 只有素材源图（MATERIAL_SOURCE），效果图走这张表，复用已有 Asset。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0010_order_match_bindings"
down_revision: str | None = "0009_order_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    if not op.get_bind().dialect.has_table(op.get_bind(), "material_url_bindings"):
        op.create_table(
            "material_url_bindings",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            # 唯一索引受 utf8mb4 3072 字节限制：URL 截到 512 字符足够去重
            sa.Column("material_url", mysql.VARCHAR(512), nullable=False),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("material_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("match_method", mysql.VARCHAR(32), nullable=False, server_default="MANUAL"),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.ForeignKeyConstraint(["variant_id"], ["material_variants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("material_url", name="uq_material_url_bindings_url"),
            sa.Index("ix_material_url_bindings_variant", "variant_id"),
            **TABLE_ARGS,
        )

    if not op.get_bind().dialect.has_table(op.get_bind(), "asin_variant_bindings"):
        op.create_table(
            "asin_variant_bindings",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("child_asin", mysql.VARCHAR(32), nullable=False),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("material_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.ForeignKeyConstraint(["variant_id"], ["material_variants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("child_asin", "variant_id", name="uq_asin_variant_bindings_pair"),
            sa.Index("ix_asin_variant_bindings_asin", "child_asin"),
            **TABLE_ARGS,
        )

    if not op.get_bind().dialect.has_table(op.get_bind(), "variant_effect_images"):
        op.create_table(
            "variant_effect_images",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("image_role", mysql.VARCHAR(32), nullable=False),
            sa.Column("sole_color", mysql.VARCHAR(16), nullable=True),
            sa.Column("asset_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("source_url", mysql.VARCHAR(2048), nullable=True),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.ForeignKeyConstraint(["variant_id"], ["material_variants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
            sa.CheckConstraint(
                "image_role IN ('MATERIAL_SOURCE','FINAL_EFFECT','PREVIEW_ONLY')",
                name="ck_variant_effect_images_role",
            ),
            sa.UniqueConstraint("variant_id", "image_role", "sole_color", name="uq_variant_effect_role"),
            **TABLE_ARGS,
        )


def downgrade() -> None:
    for table in ("variant_effect_images", "asin_variant_bindings", "material_url_bindings"):
        op.drop_table(table)
