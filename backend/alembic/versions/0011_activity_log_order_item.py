"""activity_logs target_type 增加 ORDER_ITEM（订单素材归因审核记录）"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_activity_log_order_item"
down_revision: str | None = "0010_order_match_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_activity_logs_target_type", "activity_logs", type_="check")
    op.create_check_constraint(
        "ck_activity_logs_target_type",
        "activity_logs",
        "target_type IN ('DESIGN_PACKAGE','MATERIAL','MATERIAL_VARIANT','DERIVATIVE_BATCH',"
        "'DISTRIBUTION','PARENT_ASIN','CHILD_ASIN','UPLOAD','ASSET','ORDER_ITEM')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_activity_logs_target_type", "activity_logs", type_="check")
    op.create_check_constraint(
        "ck_activity_logs_target_type",
        "activity_logs",
        "target_type IN ('DESIGN_PACKAGE','MATERIAL','MATERIAL_VARIANT','DERIVATIVE_BATCH',"
        "'DISTRIBUTION','PARENT_ASIN','CHILD_ASIN','UPLOAD','ASSET')",
    )
