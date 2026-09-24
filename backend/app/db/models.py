# ============================================================================
# order-center 订单中心 ORM 模型
#
# 独立项目：不复制 material-platform 的素材域模型（Material/Variant/Batch 等）。
# 对素材库的依赖全部走 MaterialPlatformClient（HTTP 契约），不直接引用素材库 ORM。
#
# 自有表：
#   order_assets              —— 订单原始文件（ZIP / JSON / 买家 Logo）物理存储
#   order_import_batches      —— 一次领星 ZIP 导入批次
#   order_items               —— 每个订单行（含解析/匹配结果）
#   order_import_batch_items  —— 导入批次 × 订单 快照
#   order_buyer_assets        —— 买家 Logo（原始 + SVG），订单生产附件
#   material_url_bindings     —— 买家素材 URL → Variant（variant_id 存字符串，经平台契约校验）
#   asin_variant_bindings     —— Child ASIN → Variant 候选（定位候选）
#   order_match_actions       —— 人工审核记录（替代素材库 activity_logs）
# ============================================================================

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _ts() -> Mapped[datetime]:
    return mapped_column(DATETIME(fsp=6), default=datetime.utcnow, nullable=False)


class Base(DeclarativeBase):
    pass


# ================================================================ 订单文件


class OrderAsset(Base):
    """订单域的物理文件（ZIP / JSON / 买家 Logo）。独立于素材库 assets 表。

    存储层复用 storage 抽象（本地/MinIO），但业务表完全属于 order-center。
    """

    __tablename__ = "order_assets"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    storage_key: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    blake3: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    created_by: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("blake3", name="uq_order_assets_blake3"),
        Index("ix_order_assets_created", "created_at"),
        {"comment": "订单原始文件（ZIP/JSON/Logo），不属于素材库 assets"},
    )


# ================================================================ 订单导入


class OrderImportBatch(Base):
    """一次领星订单 ZIP 导入。原始 ZIP 永久保留在 OrderAsset 中，重复 ZIP 只标记为重复。"""

    __tablename__ = "order_import_batches"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    original_filename: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    raw_zip_asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_assets.id", ondelete="RESTRICT"), nullable=False
    )
    zip_blake3: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    category_code: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, default="UNKNOWN")
    category_name: Mapped[str] = mapped_column(VARCHAR(128), nullable=False, default="未知品类")
    category_source: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="UNKNOWN")
    category_confidence: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="0")
    status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False, default="PARSED")
    duplicate_of_batch_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("order_import_batches.id", ondelete="SET NULL"), nullable=True
    )
    total_json_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('PARSED','PARTIAL','FAILED','DUPLICATE')", name="ck_order_import_batches_status"),
        Index("ix_order_import_batches_zip_hash", "zip_blake3"),
        Index("ix_order_import_batches_created", "created_at"),
        {"comment": "订单 ZIP 导入批次"},
    )


class OrderItem(Base):
    """单个订单行。含标准化解析结果与素材匹配结果。

    匹配目标（variant_id / material_id）只存字符串 id，经 MaterialPlatformClient 契约校验，
    不建立到素材库表的 FK。
    """

    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    first_import_batch_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_import_batches.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    order_item_id: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    child_asin: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    sku: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    category_code: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, default="UNKNOWN")
    category_name: Mapped[str] = mapped_column(VARCHAR(128), nullable=False, default="未知品类")
    category_source: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="UNKNOWN")
    category_confidence: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="0")
    parser_version: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=datetime.utcnow, nullable=False)
    parse_status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False, default="PARSED")
    raw_json_hash: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    normalized_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    matched_variant_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    matched_material_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    match_method: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    match_score: Mapped[float | None] = mapped_column(nullable=True)
    second_match_score: Mapped[float | None] = mapped_column(nullable=True)
    score_gap: Mapped[float | None] = mapped_column(nullable=True)
    match_reason: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    match_status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False, default="UNMATCHED")
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_order_items_dedupe_key"),
        CheckConstraint("quantity >= 0", name="ck_order_items_quantity_nonnegative"),
        CheckConstraint(
            "match_status IN ('NOT_STARTED','UNMATCHED','REVIEW_REQUIRED','CONFIRMED','FAILED')",
            name="ck_order_items_match_status",
        ),
        CheckConstraint(
            "parse_status IN ('PARSED','PARTIAL','FAILED','REVIEW_REQUIRED','NON_CUSTOM')",
            name="ck_order_items_parse_status",
        ),
        Index("ix_order_items_order", "order_id"),
        Index("ix_order_items_asin", "child_asin"),
        Index("ix_order_items_category", "category_code"),
        {"comment": "订单行（含解析与匹配结果；variant/material 只存字符串 id 经平台契约校验）"},
    )


class OrderImportBatchItem(Base):
    """导入批次 × 订单 快照（原始 JSON Asset、parse_status、normalized payload）。"""

    __tablename__ = "order_import_batch_items"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    import_batch_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False
    )
    raw_json_asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_assets.id", ondelete="RESTRICT"), nullable=False
    )
    raw_json_path: Mapped[str] = mapped_column(VARCHAR(1024), nullable=True)
    raw_json_hash: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    parse_status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False, default="PARSED")
    normalized_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("import_batch_id", "order_item_id", name="uq_order_import_batch_item"),
        CheckConstraint(
            "parse_status IN ('PARSED','PARTIAL','FAILED','DUPLICATE','REVIEW_REQUIRED','NON_CUSTOM')",
            name="ck_order_import_batch_items_status",
        ),
        Index("ix_order_import_batch_items_batch", "import_batch_id"),
        {"comment": "订单导入批次快照"},
    )


class OrderBuyerAsset(Base):
    """买家上传的 Logo / SVG 等订单生产附件。不属于素材库业务实体。"""

    __tablename__ = "order_buyer_assets"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    order_item_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("order_assets.id", ondelete="RESTRICT"), nullable=True
    )
    source_name: Mapped[str | None] = mapped_column(VARCHAR(512), nullable=True)
    source_url: Mapped[str | None] = mapped_column(VARCHAR(1024), nullable=True)
    source_path: Mapped[str | None] = mapped_column(VARCHAR(1024), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_order_buyer_assets_item", "order_item_id"),
        CheckConstraint("role IN ('BUYER_LOGO_ORIGINAL','BUYER_LOGO_SVG')", name="ck_order_buyer_assets_role"),
        {"comment": "订单买家附件，不属于素材库业务实体"},
    )


# ================================================================ 素材归因绑定


class MaterialUrlBinding(Base):
    """买家订单里的素材 URL → Variant 的绑定（人工确认后建立）。

    variant_id 只存字符串，经 MaterialPlatformClient 契约校验；不建 FK。
    相同 URL 以后直接复用（URL → Variant → MAT），不重新跑图片匹配。
    """

    __tablename__ = "material_url_bindings"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    material_url: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    variant_id: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    material_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    match_method: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="MANUAL")
    created_by: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("material_url", name="uq_material_url_bindings_url"),
        Index("ix_material_url_bindings_variant", "variant_id"),
        {"comment": "买家素材 URL → Variant 绑定（variant_id 经平台契约校验，不建 FK）"},
    )


class AsinVariantBinding(Base):
    """Deprecated compatibility table; never written by new matching flows.

    The authoritative source is now material-platform's Child ASIN contract
    (Distribution/Batch/Variant candidates). Existing rows are retained for
    historical/debugging compatibility and are not used for automatic matching.
    """

    __tablename__ = "asin_variant_bindings"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    child_asin: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    variant_id: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    material_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    created_by: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("child_asin", "variant_id", name="uq_asin_variant_bindings_pair"),
        Index("ix_asin_variant_bindings_asin", "child_asin"),
        {"comment": "Child ASIN → Variant 绑定（variant_id 经平台契约校验，不建 FK）"},
    )


# ================================================================ 人工审核记录


class OrderMatchAction(Base):
    """人工审核动作记录（确认/更换/无法识别）。替代素材库 activity_logs。"""

    __tablename__ = "order_match_actions"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    order_item_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    variant_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    material_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        CheckConstraint(
            "action IN ('MATCH_CONFIRMED','MATCH_CHANGED','MATCH_FAILED','MATCH_AUTO')",
            name="ck_order_match_actions_action",
        ),
        Index("ix_order_match_actions_item", "order_item_id"),
        {"comment": "订单素材匹配的人工审核记录（order-center 自有，不写素材库 activity_logs）"},
    )
