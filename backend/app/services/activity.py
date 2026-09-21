# ============================================================================
# 维护记录写入（统一入口）
#
# Phase 1 不实现完整 ActivityLog UI，但后端从第一天开始写：
#   创建设计包 / 上传文件包 / 创建Material / 复用Material /
#   创建PSD Revision / 上传失败 / 恢复UploadSession
# ============================================================================

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ActivityLog
from app.services.repository import new_id


def write_log(
    db: Session,
    *,
    design_package_id: str | None,
    target_type: str,
    target_id: str,
    actor: str,
    action: str,
    summary: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> ActivityLog:
    log = ActivityLog(
        id=new_id("log"),
        design_package_id=design_package_id,
        target_type=target_type,
        target_id=target_id,
        actor=actor,
        action=action,
        summary=summary,
        before_value=before_value,
        after_value=after_value,
    )
    db.add(log)
    return log
