"""asset_image_embeddings

图片搜索（以图搜图）的向量索引表。

设计要点（本轮定稿）：
  1. 每个可搜索图片 Asset 对应一条向量，存 asset_id（UNIQUE）：
       asset_id / embedding_model / embedding_version / dim / vector / indexed_at
     向量**只是检索用的指纹**，不是 Material 身份 —— 同一 Asset 被多个业务对象引用时只算一次。
  2. vector 用 BLOB 存 float32 小端字节（numpy 原生），比 JSON 数组紧凑。
  3. 索引失败不阻断素材入库：失败只记录 IMAGE_INDEX_FAILED（ActivityLog），
     之后可对单个 Asset / 全库重建索引（重新生成向量覆盖）。
  4. embedding_model 记录模型标识（当前 'lightweight-hsv-v1'），以后换模型时
     embedding_version 递增，新旧向量按 model+version 区分，互不混算。

Revision ID: 0008_asset_image_embeddings
Revises: 0007_tags_responsible
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_asset_image_embeddings"
down_revision: str | None = "0007_tags_responsible"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_image_embeddings",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=64),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding_model", sa.String(length=64), nullable=False),
        sa.Column("embedding_version", sa.Integer(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("asset_id", name="uq_asset_image_embeddings_asset"),
        sa.Index("ix_asset_image_embeddings_model", "embedding_model", "embedding_version"),
        sa.Index("ix_asset_image_embeddings_indexed_at", "indexed_at"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        comment="图片搜索向量索引（asset_id → embedding，检索用，不代表业务身份）",
    )


def downgrade() -> None:
    op.drop_table("asset_image_embeddings")
