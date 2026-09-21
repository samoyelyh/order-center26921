"""tags_and_responsible

本轮新增三块：

一、标签体系（创建时继承，之后各自独立）
    design_packages.tags      JSON 数组（上传设计包时填写）
    materials.tags            已有列，沿用
    material_variants.tags    JSON 数组（建版时从所属主素材复制）

    继承只在「创建那一刻」发生（复制），不是动态绑定：
    之后设计包/主素材/副素材的标签各自独立修改，互不覆盖。

二、负责人（设计包级业务字段）
    design_packages.responsible_id      可空（接入用户中心后才有值）
    design_packages.responsible_name    NOT NULL（目前允许只填姓名）

    负责人属于设计包；包下的主素材/副素材「默认展示所属设计包的负责人」，
    不复制到素材上。实际上传人（package_uploads.uploader_*）保持不变：
    负责人 = 业务负责这套设计的人；实际上传人 = 点了上传的那个人。

三、存量回填
    老设计包没有负责人：回填为它的设计美工（designer_name）。
    老设计包/副素材没有标签：空数组。

Revision ID: 0007_tags_responsible
Revises: 0006_design_code
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_tags_responsible"
down_revision: str | None = "0006_design_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESPONSIBLE_INDEX = "ix_design_packages_responsible_name"


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _index_names(bind, table: str) -> set[str]:
    return {idx["name"] for idx in sa.inspect(bind).get_indexes(table) if idx.get("name")}


def upgrade() -> None:
    bind = op.get_bind()

    # ---- design_packages.tags ----
    if "tags" not in _columns(bind, "design_packages"):
        op.add_column(
            "design_packages",
            sa.Column("tags", sa.JSON(), nullable=True, comment="设计包标签（创建时继承给包内素材）"),
        )
        op.execute("UPDATE design_packages SET tags = JSON_ARRAY() WHERE tags IS NULL")

    # ---- design_packages.responsible_*（老数据回填成设计美工） ----
    if "responsible_name" not in _columns(bind, "design_packages"):
        op.add_column(
            "design_packages",
            sa.Column("responsible_name", sa.String(length=128), nullable=True, comment="负责人（业务上负责这套设计的人）"),
        )
        op.execute(
            "UPDATE design_packages SET responsible_name = designer_name "
            "WHERE responsible_name IS NULL OR responsible_name = ''"
        )
        op.alter_column(
            "design_packages", "responsible_name", existing_type=sa.String(length=128), nullable=False
        )
    if "responsible_id" not in _columns(bind, "design_packages"):
        op.add_column(
            "design_packages",
            sa.Column("responsible_id", sa.String(length=64), nullable=True, comment="负责人 userId（接入用户中心后才有值）"),
        )

    if RESPONSIBLE_INDEX not in _index_names(bind, "design_packages"):
        op.create_index(RESPONSIBLE_INDEX, "design_packages", ["responsible_name"])

    # ---- material_variants.tags ----
    if "tags" not in _columns(bind, "material_variants"):
        op.add_column(
            "material_variants",
            sa.Column("tags", sa.JSON(), nullable=True, comment="副素材标签（建版时从主素材复制，之后独立修改）"),
        )
        # 存量副素材：继承所属主素材的标签（与建版时的行为一致）
        op.execute(
            "UPDATE material_variants v JOIN materials m ON m.id = v.material_id "
            "SET v.tags = COALESCE(m.tags, JSON_ARRAY()) WHERE v.tags IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "tags" in _columns(bind, "material_variants"):
        op.drop_column("material_variants", "tags")
    if RESPONSIBLE_INDEX in _index_names(bind, "design_packages"):
        op.drop_index(RESPONSIBLE_INDEX, table_name="design_packages")
    if "responsible_id" in _columns(bind, "design_packages"):
        op.drop_column("design_packages", "responsible_id")
    if "responsible_name" in _columns(bind, "design_packages"):
        op.drop_column("design_packages", "responsible_name")
    if "tags" in _columns(bind, "design_packages"):
        op.drop_column("design_packages", "tags")
