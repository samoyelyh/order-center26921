"""pua_role_unique

把 package_upload_assets 的唯一键从 (package_upload_id, asset_id) 改成
(package_upload_id, asset_id, file_role)。

为什么必须改：
    同一份字节（BLAKE3 相同 → 复用同一个 Asset）完全可以既当某位置的主图、
    又当另一位置的副图 —— 用户把同一张图同时放进主素材槽和副图槽时就是这种情况。
    旧的唯一键下，第二次上传只能**改写**已有那一行的 file_role，
    于是主图的 MAIN_PREVIEW 关联被静默抹掉，主素材直接失去文件归属。
    加上 file_role 后，这张表才真正是「只追加、不修改」的关联表。

Revision ID: 0004_pua_role_unique
Revises: 0003_designer_and_phase2
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_pua_role_unique"
down_revision: str | None = "0003_designer_and_phase2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _unique_names(bind, table: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(bind).get_unique_constraints(table)
        if constraint.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    names = _unique_names(bind, "package_upload_assets")
    if "uq_pua_upload_asset_role" not in names:
        if "uq_pua_upload_asset" in names:
            op.drop_constraint("uq_pua_upload_asset", "package_upload_assets", type_="unique")
        op.create_unique_constraint(
            "uq_pua_upload_asset_role",
            "package_upload_assets",
            ["package_upload_id", "asset_id", "file_role"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    names = _unique_names(bind, "package_upload_assets")
    # 回退前先去重：旧的唯一键不允许同一 (upload, asset) 出现两行
    op.execute(
        sa.text(
            "DELETE p FROM package_upload_assets p "
            "JOIN package_upload_assets q "
            "  ON p.package_upload_id = q.package_upload_id AND p.asset_id = q.asset_id "
            " AND p.id > q.id"
        )
    )
    if "uq_pua_upload_asset_role" in names:
        op.drop_constraint("uq_pua_upload_asset_role", "package_upload_assets", type_="unique")
    if "uq_pua_upload_asset" not in names:
        op.create_unique_constraint(
            "uq_pua_upload_asset",
            "package_upload_assets",
            ["package_upload_id", "asset_id"],
        )
