"""package_upload_assets

第五~十一条：修正 Asset 与上传行为的归属结构。

改造前：assets.upload_session_id（单值 FK）
    - 一个物理文件只能属于「一次」上传，无法表达多对多
    - BLAKE3 复用时会改写这个字段，直接把旧上传的归属**改坏**，
      导致旧上传的副图在整包视图里消失（「副素材 0」的直接原因之一）

改造后：package_upload_assets（上传行为 ↔ Asset 多对多 + 文件角色）
    - Asset = 物理文件身份（BLAKE3 去重，可被多次上传复用）
    - PackageUploadAsset = 「这次上传以什么角色交付了这个文件」（只追加，不修改）
    - 旧上传的归属关系再也不会被新上传破坏

本次迁移：
    1. 建 package_upload_assets 表（含 file_role / position_hint 约束与索引）
    2. 把存量 assets.upload_session_id 关系搬迁为关联行，
       file_role 按文件名推断（与 app/services/files.py 规则一致）
    3. 删除 assets 上的 ix_assets_upload 索引、fk_assets_upload 外键与 upload_session_id 字段

Revision ID: 0002_package_upload_assets
Revises: 0001_phase1_material_assets
Create Date: 2026-09-17
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002_package_upload_assets"
down_revision: str | None = "0001_phase1_material_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

# 与 app/services/files.py 保持一致的分类规则（迁移脚本不依赖应用代码）
_MAIN_PREVIEW_RE = re.compile(r"^\d{1,4}$")
_VARIANT_RE = re.compile(r"^\d{1,4}[-_]\d{1,3}$")
_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"}
_DESIGN_EXTENSIONS = {"psd", "psb"}


def _classify(filename: str) -> str:
    base = (filename or "").replace("\\", "/").split("/")[-1]
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    stem = (base.rsplit(".", 1)[0] if "." in base else base).strip()

    if ext in _DESIGN_EXTENSIONS:
        return "PSD"
    if ext not in _IMAGE_EXTENSIONS:
        return "OTHER"
    if _VARIANT_RE.match(stem):
        return "VARIANT"
    if _MAIN_PREVIEW_RE.match(stem):
        return "MAIN_PREVIEW"
    return "OTHER"


def _position_hint(filename: str) -> int | None:
    base = (filename or "").replace("\\", "/").split("/")[-1]
    stem = (base.rsplit(".", 1)[0] if "." in base else base).strip()
    match = re.match(r"^(\d{1,4})", stem)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 9999 else None


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------ 1. package_upload_assets
    # 注意：MySQL 的 DDL 不是事务性的，中途失败会留下「表已建好」的状态。
    # 因此这里逐步存在性判断，保证同一个迁移可以安全地重跑。
    if not sa.inspect(bind).has_table("package_upload_assets"):
        op.create_table(
            "package_upload_assets",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("package_upload_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("asset_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("file_role", mysql.VARCHAR(16), nullable=False, comment="MAIN_PREVIEW/PSD/VARIANT/OTHER"),
            sa.Column("original_filename", mysql.VARCHAR(512), nullable=False),
            sa.Column("position_hint", sa.Integer(), nullable=True, comment="文件名位置提示，仅用于展示排序"),
            sa.Column(
                "created_at",
                mysql.DATETIME(fsp=6),
                server_default=sa.func.now(6),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["package_upload_id"],
                ["package_uploads.id"],
                name="fk_pua_upload",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["asset_id"],
                ["assets.id"],
                name="fk_pua_asset",
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("package_upload_id", "asset_id", name="uq_pua_upload_asset"),
            sa.CheckConstraint(
                "file_role IN ('MAIN_PREVIEW','PSD','VARIANT','OTHER')",
                name="ck_pua_file_role",
            ),
            sa.CheckConstraint(
                "position_hint IS NULL OR (position_hint >= 1 AND position_hint <= 9999)",
                name="ck_pua_position_hint",
            ),
            comment="上传行为 ↔ Asset 多对多关联（含文件角色）",
            **TABLE_ARGS,
        )
        op.create_index(
            "ix_pua_upload_role", "package_upload_assets", ["package_upload_id", "file_role"]
        )
        op.create_index("ix_pua_asset", "package_upload_assets", ["asset_id"])

    # ------------------------------------------------ 2. 搬迁存量归属关系
    asset_columns = {col["name"] for col in sa.inspect(bind).get_columns("assets")}

    if "upload_session_id" in asset_columns:
        rows = bind.execute(
            sa.text(
                "SELECT id, upload_session_id, original_filename "
                "FROM assets WHERE upload_session_id IS NOT NULL"
            )
        ).fetchall()

        for asset_id, upload_id, filename in rows:
            already = bind.execute(
                sa.text(
                    "SELECT 1 FROM package_upload_assets "
                    "WHERE package_upload_id = :upload_id AND asset_id = :asset_id LIMIT 1"
                ),
                {"upload_id": upload_id, "asset_id": asset_id},
            ).first()
            if already:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO package_upload_assets "
                    "(id, package_upload_id, asset_id, file_role, original_filename, "
                    " position_hint, created_at) "
                    "VALUES (:id, :upload_id, :asset_id, :file_role, :filename, "
                    " :position_hint, NOW(6))"
                ),
                {
                    "id": f"pua_{uuid.uuid4().hex[:24]}",
                    "upload_id": upload_id,
                    "asset_id": asset_id,
                    "file_role": _classify(filename or ""),
                    "filename": filename or "",
                    "position_hint": _position_hint(filename or ""),
                },
            )

    # ------------------------------------------------ 3. 移除 assets 上的单归属字段
    # 顺序要求（MySQL）：必须先删外键，才能删它依赖的索引，最后才能删字段。
    asset_fks = {fk["name"] for fk in sa.inspect(bind).get_foreign_keys("assets")}
    if "fk_assets_upload" in asset_fks:
        op.drop_constraint("fk_assets_upload", "assets", type_="foreignkey")

    asset_indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("assets")}
    if "ix_assets_upload" in asset_indexes:
        op.drop_index("ix_assets_upload", table_name="assets")

    if "upload_session_id" in {col["name"] for col in sa.inspect(bind).get_columns("assets")}:
        op.drop_column("assets", "upload_session_id")


def downgrade() -> None:
    # 恢复 assets.upload_session_id（取该 Asset 最早的一次上传归属）
    op.add_column(
        "assets",
        sa.Column("upload_session_id", mysql.VARCHAR(64), nullable=True, comment="资产首次落库时所属的上传行为"),
    )
    op.create_foreign_key(
        "fk_assets_upload",
        "assets",
        "package_uploads",
        ["upload_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_assets_upload", "assets", ["upload_session_id"])

    op.execute(
        sa.text(
            "UPDATE assets a "
            "JOIN ("
            "  SELECT asset_id, MIN(created_at) AS first_at "
            "  FROM package_upload_assets GROUP BY asset_id"
            ") f ON f.asset_id = a.id "
            "JOIN package_upload_assets p "
            "  ON p.asset_id = a.id AND p.created_at = f.first_at "
            "SET a.upload_session_id = p.package_upload_id"
        )
    )

    # drop_table 会连带删除表上的索引与外键；
    # 不能先单独 DROP INDEX ix_pua_asset —— 它被 fk_pua_asset 依赖，MySQL 会拒绝（errno 1553）。
    op.drop_table("package_upload_assets")
