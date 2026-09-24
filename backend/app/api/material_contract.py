# ============================================================================
# material-platform 契约代理 API
#
# order-center 通过 MaterialPlatformClient（MaterialPlatformGateway）访问素材库，
# 前端审核页需要「候选 Variant 列表」供人工确认/更换。
# 此端点把 gateway 的候选列表包装成前端 DTO，不暴露素材库内部模型。
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.material_platform import MaterialPlatformContractError, MaterialPlatformUnavailableError, get_material_platform

router = APIRouter(prefix="/material-platform", tags=["material-platform"])


@router.get("/variants", summary="按 Child ASIN 获取 Batch/Variant 候选（前端审核用）")
def list_variant_options(childAsin: str, limit: int = 100) -> dict:
    try:
        gw = get_material_platform()
        contract = gw.get_candidates_by_child_asin(childAsin)
        variants = []
        for candidate in contract.variants[:limit]:
            variants.append({
                "variantId": candidate.variant_id,
                "materialId": candidate.material_id,
                "materialCode": candidate.material_code,
                "categoryCode": candidate.category_code,
                "displayCode": candidate.display_code,
                "batchId": candidate.batch_id or (contract.batch.batch_id if contract.batch else None),
                "batchCode": contract.batch.batch_code if contract.batch else None,
                "versionNo": contract.batch.version_no if contract.batch else None,
                "designPackageId": candidate.design_package_id or (contract.batch.design_package_id if contract.batch else None),
                "images": [image.__dict__ for image in candidate.images],
            })
        return {
            "childAsin": contract.child_asin,
            "categoryCode": contract.category_code,
            "batch": contract.batch.__dict__ if contract.batch else None,
            "variants": variants,
        }
    except MaterialPlatformContractError as exc:
        raise HTTPException(404, detail={"code": exc.code, "message": str(exc)}) from exc
    except MaterialPlatformUnavailableError as exc:
        raise HTTPException(503, detail={"code": "MATERIAL_PLATFORM_UNAVAILABLE", "message": str(exc)}) from exc
