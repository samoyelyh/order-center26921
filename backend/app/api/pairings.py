# ============================================================================
# 主副素材配对 API（按同名 pairKey，无相似度）
#
#   POST   /api/uploads/{uploadId}/pair                     本次上传跑一遍同名配对
#   GET    /api/uploads/{uploadId}/pairings                 查询本次上传的配对
#   PATCH  /api/uploads/{uploadId}/pairings/{id}            人工修改配对
#   POST   /api/uploads/{uploadId}/pairings/confirm         确认整包配对
#
# 已删除的旧匹配路由（当前 API 不再提供）：
#   POST /design-packages/{id}/match、GET/PATCH .../matches、
#   .../matches/confirm-high、.../matches/{id}/revert
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dto import (
    DataAnomalyDTO,
    MaterialPairingDTO,
    PairingConfirmResponse,
    PairingPatchRequest,
    PairingRunResponse,
)
from app.schemas.mappers import build_count_check
from app.services.activity import write_log
from app.services.pairing_service import (
    build_pairing_dtos,
    confirm_pairings,
    load_pairings,
    pairing_summary,
    run_pairing,
    runtime_anomalies,
    update_pairing,
)

router = APIRouter(tags=["pairings"])

DEFAULT_ACTOR = "肖芸"


def _actor(value: str | None) -> str:
    return (value or "").strip() or DEFAULT_ACTOR


@router.post(
    "/uploads/{upload_id}/pair",
    response_model=PairingRunResponse,
    summary="本次上传按同名 pairKey 自动配对（主图 / PSD / 副图）",
)
def run_upload_pairing(
    upload_id: str,
    actor: str | None = None,
    db: Session = Depends(get_db),
) -> PairingRunResponse:
    result = run_pairing(db, upload_id)
    dtos = build_pairing_dtos(db, result.inputs, result.rows)
    summary = pairing_summary(dtos)

    write_log(
        db,
        design_package_id=result.inputs.package.id,
        target_type="DESIGN_PACKAGE",
        target_id=result.inputs.package.id,
        actor=_actor(actor),
        action="RUN_PAIRING",
        summary=(
            f"同名配对：主素材 {len(result.inputs.positions)} 个、副图 "
            f"{result.variant_count} 张 → 已配对 {result.paired_count} 个、"
            f"未配对 {result.unpaired_count} 个"
        ),
    )
    db.commit()

    count_check = build_count_check(
        len(result.inputs.positions),
        result.variant_count,
        has_pairings=bool(dtos),
    )
    pair_keys = [
        key for key in (result.inputs.pair_key_by_position.get(p.id, "") for p in result.inputs.positions) if key
    ]

    return PairingRunResponse(
        designPackageId=result.inputs.package.id,
        packageUploadId=upload_id,
        mainCount=len(result.inputs.positions),
        variantUploadCount=result.variant_count,
        pairedCount=result.paired_count,
        unpairedCount=result.unpaired_count,
        confirmedCount=summary.get("CONFIRMED", 0),
        manualCount=summary.get("manual", 0),
        pairKeys=pair_keys,
        pairings=dtos,
        anomalies=result.anomalies,
        countCheck=count_check,
    )


@router.get(
    "/uploads/{upload_id}/pairings",
    response_model=list[MaterialPairingDTO],
    summary="查询本次上传的配对结果（没跑过配对则返回空表）",
)
def list_pairings(upload_id: str, db: Session = Depends(get_db)) -> list[MaterialPairingDTO]:
    _inputs, _rows, dtos, _summary = load_pairings(db, upload_id)
    return dtos


@router.patch(
    "/uploads/{upload_id}/pairings/{pairing_id}",
    response_model=MaterialPairingDTO,
    summary="人工修改配对（换一张本次上传的副图，或取消配对）",
)
def patch_pairing(
    upload_id: str,
    pairing_id: str,
    payload: PairingPatchRequest,
    db: Session = Depends(get_db),
) -> MaterialPairingDTO:
    dto, _summary = update_pairing(
        db,
        upload_id,
        pairing_id,
        variant_asset_id=payload.variantAssetId,
        pair_key=payload.pairKey,
        actor=_actor(payload.actor),
        confirm_reassign=payload.confirmReassign,
    )
    db.commit()
    return dto


@router.post(
    "/uploads/{upload_id}/pairings/confirm",
    response_model=PairingConfirmResponse,
    summary="确认整包配对（有缺副图等阻断异常时拒绝）",
)
def confirm_upload_pairings(
    upload_id: str,
    actor: str | None = None,
    db: Session = Depends(get_db),
) -> PairingConfirmResponse:
    confirmed, dtos, summary, anomalies = confirm_pairings(db, upload_id, actor=_actor(actor))
    db.commit()
    return PairingConfirmResponse(
        confirmedCount=confirmed,
        pairings=dtos,
        anomalies=anomalies,
        summary=summary,
    )


__all__ = ["DataAnomalyDTO", "router", "runtime_anomalies"]
