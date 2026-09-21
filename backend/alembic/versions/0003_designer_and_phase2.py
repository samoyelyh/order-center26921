"""designer_and_phase2

两件事合在一个迁移里：

一、人员字段拆分（第五~七条）
    design_packages.designer_id / designer_name
        「设计美工」是设计包的**长期业务属性**（这个包是谁设计的）。
    package_uploads.uploader_id 改为可空
        「实际上传人」是**这一次**谁执行的上传。当前没有登录系统，页面手工填写，
        因此 uploader_id 允许为空；接入用户中心后再自动回填。
    开发库里的存量数据无法推断设计美工，按第五条允许用 uploader_name 兜底，
    仅用于迁移存量数据（新数据由上传页单独填写）。

二、Phase 2 表（第八~十一、二十条）
    derivative_batches      上架版本 V1/V2/V3（版本号唯一事实来源）
    material_variants       设计包位置 × 上架版本
    variant_revisions       副素材 JPG 修订历史
    material_match_results  匹配结果 + 人工裁决（含撤回所需前一状态）

建表顺序刻意安排，用于解决 material_variants.current_revision_id 的循环外键：
    derivative_batches → material_variants → variant_revisions
    → ALTER material_variants 补 fk_variants_current_revision
    → material_match_results

Revision ID: 0003_designer_and_phase2
Revises: 0002_package_upload_assets
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0003_designer_and_phase2"
down_revision: str | None = "0002_package_upload_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

NOW6 = sa.func.now(6)


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------ 1. 设计美工字段
    pkg_columns = _columns(bind, "design_packages")

    if "designer_name" not in pkg_columns:
        op.add_column(
            "design_packages",
            sa.Column(
                "designer_name",
                mysql.VARCHAR(128),
                nullable=False,
                server_default="",
                comment="设计美工姓名（设计包长期属性，可与实际上传人不同）",
            ),
        )
    if "designer_id" not in pkg_columns:
        op.add_column(
            "design_packages",
            sa.Column(
                "designer_id",
                mysql.VARCHAR(64),
                nullable=True,
                comment="设计美工用户 id（接入用户中心前为空）",
            ),
        )

    # 存量开发数据：设计美工未知 → 用该包最早一次上传的 uploader_name 兜底（第七条）
    bind.execute(
        sa.text(
            "UPDATE design_packages p "
            "SET p.designer_name = COALESCE(("
            "  SELECT u.uploader_name FROM package_uploads u "
            "  WHERE u.design_package_id = p.id "
            "  ORDER BY u.created_at ASC LIMIT 1"
            "), p.created_by) "
            "WHERE p.designer_name = '' OR p.designer_name IS NULL"
        )
    )
    # 去掉 server_default：新数据必须显式填写（否则上传页漏填时会被空字符串悄悄存下来）
    op.alter_column(
        "design_packages",
        "designer_name",
        existing_type=mysql.VARCHAR(128),
        nullable=False,
        server_default=None,
    )

    # ------------------------------------------------ 2. 实际上传人 id 允许为空
    upload_columns = _columns(bind, "package_uploads")
    if "uploader_id" in upload_columns:
        op.alter_column(
            "package_uploads",
            "uploader_id",
            existing_type=mysql.VARCHAR(64),
            nullable=True,
            comment="实际上传人用户 id（未接登录系统时为空，仅靠 uploader_name）",
        )

    # ------------------------------------------------ 3. derivative_batches
    if not sa.inspect(bind).has_table("derivative_batches"):
        op.create_table(
            "derivative_batches",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("design_package_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("code", mysql.VARCHAR(16), nullable=False),
            sa.Column("created_from_upload_id", mysql.VARCHAR(64), nullable=True),
            sa.Column(
                "main_material_count_at_creation",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("created_by", mysql.VARCHAR(64), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(
                ["design_package_id"],
                ["design_packages.id"],
                name="fk_batches_pkg",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["created_from_upload_id"],
                ["package_uploads.id"],
                name="fk_batches_upload",
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint("design_package_id", "version_no", name="uq_batches_pkg_version"),
            sa.CheckConstraint("version_no >= 1", name="ck_batches_version_positive"),
            comment="上架版本 V1/V2/V3（版本号唯一事实来源）",
            **TABLE_ARGS,
        )
        op.create_index("ix_batches_pkg_created", "derivative_batches", ["design_package_id", "created_at"])

    # ------------------------------------------------ 4. material_variants（先不建循环 FK）
    if not sa.inspect(bind).has_table("material_variants"):
        op.create_table(
            "material_variants",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("material_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("design_package_material_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("batch_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("display_code", mysql.VARCHAR(32), nullable=False),
            sa.Column("current_revision_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("duplicate_of_variant_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(
                ["material_id"], ["materials.id"], name="fk_variants_material", ondelete="RESTRICT"
            ),
            sa.ForeignKeyConstraint(
                ["design_package_material_id"],
                ["design_package_materials.id"],
                name="fk_variants_dpm",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["batch_id"], ["derivative_batches.id"], name="fk_variants_batch", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["duplicate_of_variant_id"],
                ["material_variants.id"],
                name="fk_variants_duplicate_of",
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "batch_id", "design_package_material_id", name="uq_variants_batch_position"
            ),
            comment="副素材：设计包位置 × 上架版本（display_code 只展示，不做全局唯一）",
            **TABLE_ARGS,
        )
        op.create_index("ix_variants_material", "material_variants", ["material_id"])
        op.create_index("ix_variants_dpm", "material_variants", ["design_package_material_id"])

    # ------------------------------------------------ 5. variant_revisions
    if not sa.inspect(bind).has_table("variant_revisions"):
        op.create_table(
            "variant_revisions",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("asset_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("created_by", mysql.VARCHAR(64), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("note", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(
                ["variant_id"],
                ["material_variants.id"],
                name="fk_variant_revisions_variant",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["asset_id"], ["assets.id"], name="fk_variant_revisions_asset", ondelete="RESTRICT"
            ),
            sa.UniqueConstraint("variant_id", "revision_no", name="uq_variant_revisions_no"),
            sa.CheckConstraint("revision_no >= 1", name="ck_variant_revisions_no_positive"),
            comment="副素材 JPG 修订历史（旧 Revision 永不覆盖）",
            **TABLE_ARGS,
        )
        op.create_index("ix_variant_revisions_asset", "variant_revisions", ["asset_id"])

    # ------------------------------------------------ 6. 补 material_variants 循环 FK
    variant_fks = {fk["name"] for fk in sa.inspect(bind).get_foreign_keys("material_variants")}
    if "fk_variants_current_revision" not in variant_fks:
        op.create_foreign_key(
            "fk_variants_current_revision",
            "material_variants",
            "variant_revisions",
            ["current_revision_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ------------------------------------------------ 7. material_match_results
    if not sa.inspect(bind).has_table("material_match_results"):
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
            sa.ForeignKeyConstraint(
                ["design_package_id"],
                ["design_packages.id"],
                name="fk_match_results_pkg",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["design_package_material_id"],
                ["design_package_materials.id"],
                name="fk_match_results_dpm",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["candidate_asset_id"],
                ["assets.id"],
                name="fk_match_results_candidate",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["variant_id"],
                ["material_variants.id"],
                name="fk_match_results_variant",
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "design_package_id",
                "design_package_material_id",
                name="uq_match_results_pkg_position",
            ),
            sa.CheckConstraint(
                "status IN ('AUTO_HIGH','AUTO_REVIEW','AUTO_LOW','CONFIRMED','REJECTED')",
                name="ck_match_results_status",
            ),
            comment="副素材匹配结果 + 人工裁决（含撤回所需前一状态）",
            **TABLE_ARGS,
        )
        op.create_index(
            "ix_match_results_pkg_status", "material_match_results", ["design_package_id", "status"]
        )
        op.create_index("ix_match_results_candidate", "material_match_results", ["candidate_asset_id"])


def downgrade() -> None:
    op.drop_table("material_match_results")

    variant_fks = {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys("material_variants")}
    if "fk_variants_current_revision" in variant_fks:
        op.drop_constraint("fk_variants_current_revision", "material_variants", type_="foreignkey")

    op.drop_table("variant_revisions")
    op.drop_table("material_variants")
    op.drop_table("derivative_batches")

    op.alter_column(
        "package_uploads",
        "uploader_id",
        existing_type=mysql.VARCHAR(64),
        nullable=False,
        server_default="",
    )
    op.drop_column("design_packages", "designer_id")
    op.drop_column("design_packages", "designer_name")
