"""design_code

上传设计包时手填「设计编码」（美工自己的设计编号），并用它把设计与素材关联起来：

    design_packages.design_code  ← 用户输入，例如 HB-2026-0917-A
    code（系统生成 DP-...）      ← 保持不变，仍是设计包的技术编码

为什么不是唯一键：
    一个「设计」可以有多个设计包（同一设计的新一版、补充上传），
    因此多个设计包可以共用同一个 design_code；素材的「关联设计」是
    「引用它的设计包的 design_code 去重集合」。

存量数据：回填成该设计包自己的系统编码 code（等价于「还没有人填过设计编码」），
保证列可以收紧为 NOT NULL。

Revision ID: 0006_design_code
Revises: 0005_pairkey_pairings
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_design_code"
down_revision: str | None = "0005_pairkey_pairings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_design_packages_design_code"


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _index_names(bind, table: str) -> set[str]:
    return {idx["name"] for idx in sa.inspect(bind).get_indexes(table) if idx.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "design_packages")

    if "design_code" not in columns:
        # 先加可空列 → 回填 → 再收紧，存量行不会因为 NOT NULL 失败
        op.add_column(
            "design_packages",
            sa.Column("design_code", sa.String(length=64), nullable=True, comment="设计编码（用户填写）"),
        )
        op.execute("UPDATE design_packages SET design_code = code WHERE design_code IS NULL OR design_code = ''")
        op.alter_column("design_packages", "design_code", existing_type=sa.String(length=64), nullable=False)

    if INDEX_NAME not in _index_names(bind, "design_packages"):
        op.create_index(INDEX_NAME, "design_packages", ["design_code"])


def downgrade() -> None:
    bind = op.get_bind()
    if INDEX_NAME in _index_names(bind, "design_packages"):
        op.drop_index(INDEX_NAME, table_name="design_packages")
    if "design_code" in _columns(bind, "design_packages"):
        op.drop_column("design_packages", "design_code")
