# ============================================================================
# 上架版本 API（Phase 2）
#
#   POST /api/design-packages/{id}/batches   确认整包配对并生成 V1/V2
#   GET  /api/design-packages/{id}/batches   版本列表（含副素材）
#
# 本轮只做到「生成版本」。派发运营 / ASIN / 订单 URL 属 Phase 3。
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dto import BatchCreateRequest, DerivativeBatchDTO
from app.services.batch_service import (
    create_batch,
    list_batch_variants,
    list_batches,
    to_batch_dto,
)

router = APIRouter(tags=["batches"])

DEFAULT_ACTOR = "肖芸"


@router.post(
    "/design-packages/{design_package_id}/batches",
    response_model=DerivativeBatchDTO,
    summary="确认整包配对并生成下一版（第一次即 V1，单事务）",
)
def create_derivative_batch(
    design_package_id: str,
    payload: BatchCreateRequest,
    db: Session = Depends(get_db),
) -> DerivativeBatchDTO:
    actor = (payload.actor or "").strip() or DEFAULT_ACTOR
    try:
        result = create_batch(db, design_package_id, actor=actor, upload_id=payload.uploadId)
        db.commit()
    except Exception:
        db.rollback()
        raise

    dto = to_batch_dto(result.batch, list_batch_variants(db, result.batch))
    dto.createdVariantCount = result.created_count
    dto.reusedVariantCount = result.reused_count
    dto.reusedNotes = result.reused_notes
    return dto


@router.get(
    "/design-packages/{design_package_id}/batches",
    response_model=list[DerivativeBatchDTO],
    summary="版本列表（含每个版本使用的副素材与 Revision）",
)
def get_batches(design_package_id: str, db: Session = Depends(get_db)) -> list[DerivativeBatchDTO]:
    return list_batches(db, design_package_id)
