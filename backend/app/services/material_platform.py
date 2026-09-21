# ============================================================================
# MaterialPlatformClient：order-center 对 material-platform 的唯一访问契约
#
# order-center 不直接引用素材库 ORM / model，所有素材域信息（Variant/MAT/Asset/
# category_code）都通过这个稳定契约获取：
#
#   Child ASIN → Batch（版本）
#   Batch → Variant 候选
#   Variant → MAT
#   Variant → MATERIAL_SOURCE / FINAL_EFFECT Asset（字节或 URL）
#   category_code（Variant 所属设计包品类，品类隔离用）
#
# 提供接口抽象（MaterialPlatformGateway）+ 两个实现：
#   HttpMaterialPlatformClient   —— 真实环境，走素材库 REST API
#   FakeMaterialPlatformGateway  —— 测试/开发环境适配，不依赖真实素材库
# ============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VariantCandidate:
    """素材库 Variant 的候选信息（契约 DTO，不暴露素材库内部模型）。"""

    variant_id: str
    material_id: str | None = None
    design_package_id: str | None = None
    category_code: str | None = None  # 品类隔离依据
    display_code: str | None = None
    material_source_asset_id: str | None = None  # MATERIAL_SOURCE 素材源图 asset
    material_source_url: str | None = None
    final_effect_url: str | None = None  # FINAL_EFFECT 效果图 url（或 asset 内容 url）
    sole_color: str | None = None


class MaterialPlatformGateway(Protocol):
    """素材平台契约接口。实现者必须提供下列能力。"""

    def base_url(self) -> str: ...

    def list_variant_candidates_by_asin(self, child_asin: str) -> list[VariantCandidate]: ...

    def get_variant(self, variant_id: str) -> VariantCandidate | None: ...

    def get_variant_material_source_bytes(self, variant_id: str) -> bytes | None: ...

    def get_variant_final_effect_bytes(self, variant_id: str, sole_color: str | None = None) -> bytes | None: ...

    def list_all_variant_options(self, limit: int = 100) -> list[dict]: ...


# ================================================================ HTTP 实现


class HttpMaterialPlatformClient:
    """通过素材库 REST API 访问 Variant/MAT/Asset。

    约定素材库提供以下端点（material-platform 的稳定契约）：
      GET  /api/material-platform/variants/by-asin/{asin}
      GET  /api/material-platform/variants/{id}
      GET  /api/assets/{id}/content        —— 素材库已有，取 Asset 字节
    这些端点在 material-platform 上作为「外部契约」暴露（见 material-platform 的
    material_platform_contract API）。
    """

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def base_url(self) -> str:
        return self._base

    # ---- 契约端点 ----

    def list_variant_candidates_by_asin(self, child_asin: str) -> list[VariantCandidate]:
        resp = self._client.get(f"{self._base}/api/material-platform/variants/by-asin/{child_asin}")
        resp.raise_for_status()
        return [self._to_candidate(c) for c in resp.json()]

    def get_variant(self, variant_id: str) -> VariantCandidate | None:
        try:
            resp = self._client.get(f"{self._base}/api/material-platform/variants/{variant_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return self._to_candidate(resp.json())
        except Exception:  # noqa: BLE001
            logger.warning("MaterialPlatform get_variant 失败: %s", variant_id)
            return None

    def get_variant_material_source_bytes(self, variant_id: str) -> bytes | None:
        return self._asset_bytes(variant_id, "MATERIAL_SOURCE")

    def get_variant_final_effect_bytes(self, variant_id: str, sole_color: str | None = None) -> bytes | None:
        return self._asset_bytes(variant_id, "FINAL_EFFECT", sole_color)

    def list_all_variant_options(self, limit: int = 100) -> list[dict]:
        """列出候选 Variant 选项（前端审核用）。走素材库契约端点。"""
        try:
            resp = self._client.get(f"{self._base}/api/material-platform/variants", params={"limit": limit})
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "variantId": c.get("variantId") or c.get("id") or "",
                    "materialId": c.get("materialId"),
                    "categoryCode": c.get("categoryCode"),
                    "displayCode": c.get("displayCode"),
                    "designPackageName": c.get("designPackageName"),
                }
                for c in data
            ]
        except Exception:  # noqa: BLE001
            logger.warning("MaterialPlatform list_all_variant_options 失败")
            return []

    def _asset_bytes(self, variant_id: str, role: str, sole_color: str | None = None) -> bytes | None:
        try:
            params = {"variantId": variant_id, "role": role}
            if sole_color:
                params["soleColor"] = sole_color
            resp = self._client.get(f"{self._base}/api/material-platform/variants/{variant_id}/image", params=params)
            if resp.status_code != 200:
                return None
            return resp.content
        except Exception:  # noqa: BLE001
            logger.warning("MaterialPlatform 取 %s 图失败: %s", role, variant_id)
            return None

    @staticmethod
    def _to_candidate(data: dict) -> VariantCandidate:
        return VariantCandidate(
            variant_id=data.get("variantId") or data.get("id") or "",
            material_id=data.get("materialId"),
            design_package_id=data.get("designPackageId"),
            category_code=data.get("categoryCode"),
            display_code=data.get("displayCode"),
            material_source_asset_id=data.get("materialSourceAssetId"),
            material_source_url=data.get("materialSourceUrl"),
            final_effect_url=data.get("finalEffectUrl"),
            sole_color=data.get("soleColor"),
        )


# ================================================================ 测试/开发实现


class FakeMaterialPlatformGateway:
    """开发/测试环境的内存实现：不依赖真实素材库，返回固定的候选 Variant。

    供 order-center 单测与本地无素材库时适配。生产用 HttpMaterialPlatformClient。
    """

    def __init__(self, candidates: list[VariantCandidate] | None = None) -> None:
        self._candidates = candidates or []
        self._by_id = {c.variant_id: c for c in self._candidates}
        self._material_source_bytes: dict[str, bytes] = {}
        self._final_effect_bytes: dict[tuple[str, str | None], bytes] = {}

    def base_url(self) -> str:
        return "fake://material-platform"

    def list_variant_candidates_by_asin(self, child_asin: str) -> list[VariantCandidate]:
        return self._candidates

    def get_variant(self, variant_id: str) -> VariantCandidate | None:
        return self._by_id.get(variant_id)

    def get_variant_material_source_bytes(self, variant_id: str) -> bytes | None:
        return self._material_source_bytes.get(variant_id)

    def get_variant_final_effect_bytes(self, variant_id: str, sole_color: str | None = None) -> bytes | None:
        return self._final_effect_bytes.get((variant_id, sole_color))

    def list_all_variant_options(self, limit: int = 100) -> list[dict]:
        return [
            {
                "variantId": c.variant_id,
                "materialId": c.material_id,
                "categoryCode": c.category_code,
                "displayCode": c.display_code,
                "designPackageName": None,
            }
            for c in self._candidates[:limit]
        ]

    def seed_material_source(self, variant_id: str, data: bytes) -> None:
        self._material_source_bytes[variant_id] = data

    def seed_final_effect(self, variant_id: str, data: bytes, sole_color: str | None = None) -> None:
        self._final_effect_bytes[(variant_id, sole_color)] = data


# ================================================================ 获取当前实现


def get_material_platform() -> MaterialPlatformGateway:
    """按配置返回素材平台客户端。

    MATERIAL_PLATFORM_BASE_URL 设置后走 HTTP；否则回退 Fake（仅用于测试/开发）。
    """
    from app.core.config import settings

    if settings.material_platform_base_url:
        return HttpMaterialPlatformClient(settings.material_platform_base_url)
    logger.warning("未配置 MATERIAL_PLATFORM_BASE_URL，使用 FakeMaterialPlatformGateway（仅测试/开发）")
    return FakeMaterialPlatformGateway()
