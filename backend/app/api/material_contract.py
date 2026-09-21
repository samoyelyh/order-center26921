# ============================================================================
# material-platform 契约代理 API
#
# order-center 通过 MaterialPlatformClient（MaterialPlatformGateway）访问素材库，
# 前端审核页需要「候选 Variant 列表」供人工确认/更换。
# 此端点把 gateway 的候选列表包装成前端 DTO，不暴露素材库内部模型。
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.services.material_platform import get_material_platform

router = APIRouter(prefix="/material-platform", tags=["material-platform"])


@router.get("/variants", summary="素材平台候选 Variant 列表（前端审核用）")
def list_variant_options(limit: int = 100) -> list[dict]:
    gw = get_material_platform()
    # 契约实现需提供候选枚举；Fake 返回空（测试），Http 走素材库契约端点。
    candidates = gw.list_all_variant_options(limit)
    return candidates
