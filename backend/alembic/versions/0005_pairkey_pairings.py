"""pairkey_pairings

Phase 2 重构：**取消所有基于 pHash 相似度与匈牙利算法的主副素材自动匹配**，
改为「同一次上传内按同名 pairKey 直接配对」。

本迁移做三件事：

一、package_upload_assets 增加 pair_key
    pair_key = 文件名的「同名键」（basename 去掉扩展名，小写）：
        main/1.jpg、psd/1.psd、variant/1.jpg  →  pair_key = "1"
    唯一约束从 (package_upload_id, asset_id, file_role) 换成
    (package_upload_id, file_role, pair_key)：同一上传同一角色下出现重复 pairKey 就是异常。
    存量数据按 original_filename 回填；回填后若仍有重复（理论上不会），
    保留最小 id 的那条，其余 pair_key 置空以避开唯一键（它们会在页面上报「无法解析 pairKey」）。

二、删除 material_match_results
    它是上一版「pHash + 匈牙利匹配 + 人工裁决」的落库表，字段里带
    matched_similarity / best_candidate_similarity / phash_distance，
    整套语义已被 material_pairings 取代。表是本次 Phase 2 新建的派生数据（可由配对重算），
    因此直接删除，不留死代码。

三、新建 material_pairings
    design_package_id / package_upload_id / design_package_material_id /
    variant_asset_id / pair_key / source(NAME|MANUAL) / status(PAIRED|UNPAIRED|CONFIRMED)
    / confirmed_by / confirmed_at（另含 psd_asset_id 与建版后的 variant_id）

Revision ID: 0005_pairkey_pairings
Revises: 0004_pua_role_unique
Create Date: 2026-09-17
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005_pairkey_pairings"
down_revision: str | None = "0004_pua_role_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

NOW6 = sa.func.now(6)


def _pair_key(filename: str) -> str:
    """与 app/services/files.py 的 pair_key_of 保持一致：basename 去扩展名 + 小写。"""
    base = (filename or "").replace("\\", "/").split("/")[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem.strip().lower()


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _unique_names(bind, table: str) -> set[str]:
    return {
        c["name"]
        for c in sa.inspect(bind).get_unique_constraints(table)
        if c.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------ 一、package_upload_assets.pair_key
    if "pair_key" not in _columns(bind, "package_upload_assets"):
        op.add_column(
            "package_upload_assets",
            sa.Column(
                "pair_key",
                mysql.VARCHAR(255),
                nullable=False,
                server_default="",
                comment="同名配对键（文件名去扩展名，小写；仅本设计包内有效）",
            ),
        )

        rows = bind.execute(
            sa.text("SELECT id, original_filename FROM package_upload_assets ORDER BY created_at")
        ).fetchall()
        seen: set[tuple[str, str, str]] = set()
        for link_id, filename in rows:
            key = _pair_key(filename)
            row = bind.execute(
                sa.text(
                    "SELECT package_upload_id, file_role FROM package_upload_assets WHERE id = :id"
                ),
                {"id": link_id},
            ).first()
            upload_id, role = row[0], row[1]
            marker = (upload_id, role, key)
            if key and marker in seen:
                # 存量重复（旧唯一键不含 pair_key）：置空，页面会提示「无法解析 pairKey」
                key = ""
            elif key:
                seen.add(marker)
            bind.execute(
                sa.text("UPDATE package_upload_assets SET pair_key = :k WHERE id = :id"),
                {"k": key, "id": link_id},
            )
        op.alter_column(
            "package_upload_assets",
            "pair_key",
            existing_type=mysql.VARCHAR(255),
            nullable=False,
            server_default=None,
        )

    existing_unique = _unique_names(bind, "package_upload_assets")
    if "uq_pua_upload_role_pairkey" not in existing_unique:
        for legacy in ("uq_pua_upload_asset_role", "uq_pua_upload_asset"):
            if legacy in existing_unique:
                op.drop_constraint(legacy, "package_upload_assets", type_="unique")
        op.create_unique_constraint(
            "uq_pua_upload_role_pairkey",
            "package_upload_assets",
            ["package_upload_id", "file_role", "pair_key"],
        )
    indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("package_upload_assets")}
    if "ix_pua_upload_pairkey" not in indexes:
        op.create_index(
            "ix_pua_upload_pairkey", "package_upload_assets", ["package_upload_id", "pair_key"]
        )

    # ------------------------------------------------ 二、删除旧的匹配结果表
    if sa.inspect(bind).has_table("material_match_results"):
        op.drop_table("material_match_results")

    # ------------------------------------------------ 三、material_pairings
    if not sa.inspect(bind).has_table("material_pairings"):
        op.create_table(
            "material_pairings",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("design_package_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("package_upload_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("design_package_material_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("variant_asset_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("pair_key", mysql.VARCHAR(255), nullable=False, server_default=""),
            sa.Column("source", mysql.VARCHAR(16), nullable=False, server_default="NAME"),
            sa.Column("status", mysql.VARCHAR(16), nullable=False, server_default="UNPAIRED"),
            sa.Column("psd_asset_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("confirmed_by", mysql.VARCHAR(64), nullable=True),
            sa.Column("confirmed_at", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(
                ["design_package_id"],
                ["design_packages.id"],
                name="fk_pairings_pkg",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["package_upload_id"],
                ["package_uploads.id"],
                name="fk_pairings_upload",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["design_package_material_id"],
                ["design_package_materials.id"],
                name="fk_pairings_dpm",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["variant_asset_id"], ["assets.id"], name="fk_pairings_variant_asset", ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["psd_asset_id"], ["assets.id"], name="fk_pairings_psd_asset", ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["variant_id"],
                ["material_variants.id"],
                name="fk_pairings_variant",
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "package_upload_id",
                "design_package_material_id",
                name="uq_pairings_upload_position",
            ),
            sa.UniqueConstraint(
                "package_upload_id", "variant_asset_id", name="uq_pairings_upload_variant"
            ),
            sa.CheckConstraint("source IN ('NAME','MANUAL')", name="ck_pairings_source"),
            sa.CheckConstraint(
                "status IN ('PAIRED','UNPAIRED','CONFIRMED')", name="ck_pairings_status"
            ),
            comment="主素材位置 ↔ 上传副图 的配对（按同名 pairKey，无相似度）",
            **TABLE_ARGS,
        )
        op.create_index("ix_pairings_pkg_status", "material_pairings", ["design_package_id", "status"])
        op.create_index("ix_pairings_upload", "material_pairings", ["package_upload_id"])


def downgrade() -> None:
    op.drop_table("material_pairings")

    op.create_table(
        "material_match_results",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),
        sa.Column("design_package_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("design_package_material_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("candidate_asset_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("variant_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("matched_similarity", sa.Float(), nullable=True),
        sa.Column("best_candidate_similarity", sa.Float(), nullable=True),
        sa.Column("phash_distance", sa.Integer(), nullable=True),
        sa.Column("status", mysql.VARCHAR(16), nullable=False),
        sa.Column("filename_mismatch", mysql.VARCHAR(512), nullable=True),
        sa.Column("previous_status", mysql.VARCHAR(16), nullable=True),
        sa.Column("previous_variant_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("previous_candidate_asset_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("decided_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("decided_by", mysql.VARCHAR(64), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
        sa.UniqueConstraint(
            "design_package_id", "design_package_material_id", name="uq_match_results_pkg_position"
        ),
        **TABLE_ARGS,
    )

    bind = op.get_bind()
    names = _unique_names(bind, "package_upload_assets")
    if "uq_pua_upload_role_pairkey" in names:
        op.drop_constraint("uq_pua_upload_role_pairkey", "package_upload_assets", type_="unique")
    op.create_unique_constraint(
        "uq_pua_upload_asset_role",
        "package_upload_assets",
        ["package_upload_id", "asset_id", "file_role"],
    )
    indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("package_upload_assets")}
    if "ix_pua_upload_pairkey" in indexes:
        op.drop_index("ix_pua_upload_pairkey", table_name="package_upload_assets")
    if "pair_key" in _columns(bind, "package_upload_assets"):
        op.drop_column("package_upload_assets", "pair_key")


# 迁移脚本不依赖应用代码，仅本地保留一个未使用的正则常量以呼应文件名规则
_FILENAME_HINT = re.compile(r"^.+$")
