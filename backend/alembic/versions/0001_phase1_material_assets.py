"""phase1_material_assets

Phase 1：设计包 → 每次上传记录 → Asset → 主素材 MAT → 设计包位置 → 上传会话

Revision ID: 0001_phase1_material_assets
Revises:
Create Date: 2026-09-17

建表顺序刻意安排，用于解决 materials.current_psd_revision_id 的循环外键：
    design_packages → package_uploads → assets → materials
    → material_psd_revisions → ALTER materials 补 FK
    → design_package_materials → upload_sessions → code_sequences → activity_logs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_phase1_material_assets"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    # ---------------------------------------------------------------- 1. design_packages
    op.create_table(
        "design_packages",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),
        sa.Column("code", mysql.VARCHAR(64), nullable=False),
        sa.Column("name", mysql.VARCHAR(255), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_by", mysql.VARCHAR(64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.func.now(6),
            nullable=False,
        ),
        sa.Column("archived_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.UniqueConstraint("code", name="uq_design_packages_code"),
        comment="设计包（长期逻辑实体，无缓存业务字段）",
        **TABLE_ARGS,
    )
    op.create_index("ix_design_packages_created_at", "design_packages", ["created_at"])
    op.create_index("ix_design_packages_archived_at", "design_packages", ["archived_at"])

    # ---------------------------------------------------------------- 2. package_uploads
    op.create_table(
        "package_uploads",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),

        sa.Column("design_package_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("original_package_name", mysql.VARCHAR(512), nullable=False),
        sa.Column("file_size", mysql.BIGINT(), nullable=True),
        sa.Column("upload_session_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("uploader_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("uploader_name", mysql.VARCHAR(128), nullable=False),
        sa.Column("upload_type", mysql.VARCHAR(16), nullable=False),
        sa.Column("target_batch_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("status", mysql.VARCHAR(16), nullable=False, server_default="PARSING"),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.ForeignKeyConstraint(
            ["design_package_id"],
            ["design_packages.id"],
            name="fk_package_uploads_pkg",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("upload_session_id", name="uq_package_uploads_session"),
        sa.CheckConstraint(
            "upload_type IN ('INITIAL','NEW_BATCH','SUPPLEMENT','REVISION')",
            name="ck_package_uploads_type",
        ),
        sa.CheckConstraint(
            "status IN ('PARSING','PARSED','MATCHED','CONFIRMED','SUBMITTED','INTERRUPTED','FAILED')",
            name="ck_package_uploads_status",
        ),
        comment="每次实际的文件包上传行为",
        **TABLE_ARGS,
    )
    op.create_index(
        "ix_package_uploads_pkg_created",
        "package_uploads",
        ["design_package_id", "created_at"],
    )

    # ---------------------------------------------------------------- 3. assets
    op.create_table(
        "assets",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),
        sa.Column("storage_key", mysql.VARCHAR(512), nullable=False),
        sa.Column("original_filename", mysql.VARCHAR(512), nullable=False),
        sa.Column("mime_type", mysql.VARCHAR(128), nullable=False),
        sa.Column("size_bytes", mysql.BIGINT(), nullable=False, server_default="0"),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("blake3", mysql.VARCHAR(64), nullable=True),
        sa.Column("phash", mysql.BINARY(8), nullable=True, comment="64bit pHash"),
        sa.Column("phash_version", sa.Integer(), nullable=True),
        sa.Column("upload_session_id", mysql.VARCHAR(64), nullable=True, comment="资产首次落库时所属的上传行为"),
        sa.Column("created_by", mysql.VARCHAR(64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["upload_session_id"],
            ["package_uploads.id"],
            name="fk_assets_upload",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("storage_key", name="uq_assets_storage_key"),
        # PSD 等不可解码文件：blake3 与 phash 同时为 NULL（NULL 比较不会让 CHECK 失败）
        sa.CheckConstraint("(blake3 IS NULL) = (phash IS NULL)", name="ck_assets_fingerprint_pair"),
        sa.CheckConstraint(
            "phash_version IS NULL OR phash IS NOT NULL", name="ck_assets_phash_version"
        ),
        comment="文件资产（唯一文件实体；BLAKE3 + pHash 在此）",
        **TABLE_ARGS,
    )
    # BLAKE3 建普通索引：用于「完全相同文件」查询，但不做 UNIQUE，重复时由业务层复用 Asset
    op.create_index("ix_assets_blake3", "assets", ["blake3"])
    op.create_index("ix_assets_phash", "assets", ["phash", "phash_version"])
    op.create_index("ix_assets_deleted_at", "assets", ["deleted_at"])
    op.create_index("ix_assets_upload", "assets", ["upload_session_id"])

    # ---------------------------------------------------------------- 4. materials（先不建循环 FK）
    op.create_table(
        "materials",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),
        sa.Column("material_code", mysql.VARCHAR(32), nullable=False),
        sa.Column("name", mysql.VARCHAR(255), nullable=False),
        sa.Column("preview_asset_id", mysql.VARCHAR(64), nullable=False),
        # 循环 FK：本表建完后由 op.create_foreign_key 补上
        sa.Column("current_psd_revision_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("tags", mysql.JSON(), nullable=True),
        sa.Column("source_package_upload_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("created_by", mysql.VARCHAR(64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.Column("archived_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["preview_asset_id"],
            ["assets.id"],
            name="fk_materials_preview_asset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_package_upload_id"],
            ["package_uploads.id"],
            name="fk_materials_source_upload",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("material_code", name="uq_materials_code"),
        comment="主素材永久实体 MAT-xxxxxx（只代表主素材）",
        **TABLE_ARGS,
    )
    op.create_index("ix_materials_created_at", "materials", ["created_at"])
    op.create_index("ix_materials_preview_asset", "materials", ["preview_asset_id"])
    op.create_index("ix_materials_archived_at", "materials", ["archived_at"])

    # ---------------------------------------------------------------- 5. material_psd_revisions
    op.create_table(
        "material_psd_revisions",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),
        sa.Column("material_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("asset_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("created_by", mysql.VARCHAR(64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["materials.id"],
            name="fk_psd_revisions_material",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_psd_revisions_asset",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("material_id", "revision_no", name="uq_psd_revisions_no"),
        sa.CheckConstraint("revision_no >= 1", name="ck_psd_revisions_no_positive"),
        comment="主素材 PSD 修订历史（旧版本永不覆盖删除）",
        **TABLE_ARGS,
    )
    op.create_index("ix_psd_revisions_asset", "material_psd_revisions", ["asset_id"])

    # ---------------------------------------------------------------- 6. 补循环 FK
    op.create_foreign_key(
        "fk_materials_current_psd",
        "materials",
        "material_psd_revisions",
        ["current_psd_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ---------------------------------------------------------------- 7. design_package_materials
    op.create_table(
        "design_package_materials",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),
        sa.Column("design_package_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("material_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("display_name", mysql.VARCHAR(255), nullable=True),
        sa.Column("source_file_name", mysql.VARCHAR(512), nullable=False),
        sa.Column("source_asset_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("created_from_upload_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.ForeignKeyConstraint(
            ["design_package_id"],
            ["design_packages.id"],
            name="fk_dpm_pkg",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["materials.id"],
            name="fk_dpm_material",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_asset_id"],
            ["assets.id"],
            name="fk_dpm_source_asset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_from_upload_id"],
            ["package_uploads.id"],
            name="fk_dpm_upload",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("design_package_id", "position", name="uq_dpm_pkg_position"),
        sa.CheckConstraint("position >= 1", name="ck_dpm_position_positive"),
        comment="设计包位置 → MAT 关联（文件信息通过 source_asset_id 获取）",
        **TABLE_ARGS,
    )
    op.create_index("ix_dpm_material", "design_package_materials", ["material_id"])
    op.create_index(
        "ix_dpm_pkg_position", "design_package_materials", ["design_package_id", "position"]
    )

    # ---------------------------------------------------------------- 8. upload_sessions
    op.create_table(
        "upload_sessions",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),
        sa.Column("design_package_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("package_upload_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("original_package_name", mysql.VARCHAR(512), nullable=False),
        sa.Column("file_size", mysql.BIGINT(), nullable=False, server_default="0"),
        sa.Column("stage", mysql.VARCHAR(16), nullable=False, server_default="PARSING"),
        sa.Column("upload_type", mysql.VARCHAR(16), nullable=False, server_default="INITIAL"),
        sa.Column("operator_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("operator_name", mysql.VARCHAR(128), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("received_files", mysql.JSON(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.ForeignKeyConstraint(
            ["design_package_id"],
            ["design_packages.id"],
            name="fk_upload_sessions_pkg",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["package_upload_id"],
            ["package_uploads.id"],
            name="fk_upload_sessions_upload",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("package_upload_id", name="uq_upload_sessions_upload"),
        sa.CheckConstraint(
            "stage IN ('PARSING','PARSED','MATCHED','CONFIRMED','SUBMITTED','INTERRUPTED','FAILED')",
            name="ck_upload_sessions_stage",
        ),
        comment="上传会话（幂等 / 刷新恢复 / 断点续传锚点）",
        **TABLE_ARGS,
    )
    op.create_index(
        "ix_upload_sessions_pkg_stage", "upload_sessions", ["design_package_id", "stage"]
    )

    # ---------------------------------------------------------------- 9. code_sequences
    op.create_table(
        "code_sequences",
        sa.Column("name", mysql.VARCHAR(32), primary_key=True),
        sa.Column("current_value", mysql.BIGINT(), nullable=False, server_default="0"),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        comment="业务编码发号器（MAT-xxxxxx 并发安全发放）",
        **TABLE_ARGS,
    )
    # 初始化 MAT 序列
    op.execute("INSERT INTO code_sequences (name, current_value) VALUES ('material', 0)")

    # ---------------------------------------------------------------- 10. activity_logs
    op.create_table(
        "activity_logs",
        sa.Column("id", mysql.VARCHAR(64), primary_key=True),
        sa.Column("design_package_id", mysql.VARCHAR(64), nullable=True),
        sa.Column("target_type", mysql.VARCHAR(32), nullable=False),
        sa.Column("target_id", mysql.VARCHAR(64), nullable=False),
        sa.Column("actor", mysql.VARCHAR(128), nullable=False),
        sa.Column("action", mysql.VARCHAR(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
        sa.ForeignKeyConstraint(
            ["design_package_id"],
            ["design_packages.id"],
            name="fk_activity_logs_pkg",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "target_type IN ('DESIGN_PACKAGE','MATERIAL','MATERIAL_VARIANT','DERIVATIVE_BATCH',"
            "'DISTRIBUTION','PARENT_ASIN','CHILD_ASIN','UPLOAD','ASSET')",
            name="ck_activity_logs_target_type",
        ),
        comment="统一维护记录（归属依据 = target_type + target_id）",
        **TABLE_ARGS,
    )
    op.create_index(
        "ix_activity_logs_pkg_created",
        "activity_logs",
        ["design_package_id", "created_at"],
    )
    op.create_index(
        "ix_activity_logs_target_created",
        "activity_logs",
        ["target_type", "target_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("activity_logs")
    op.drop_table("code_sequences")
    op.drop_table("upload_sessions")
    op.drop_table("design_package_materials")
    op.drop_constraint("fk_materials_current_psd", "materials", type_="foreignkey")
    op.drop_table("material_psd_revisions")
    op.drop_table("materials")
    op.drop_table("assets")
    op.drop_table("package_uploads")
    op.drop_table("design_packages")
