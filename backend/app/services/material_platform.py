"""Stable adapter for the material-platform Child ASIN contract."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)


class MaterialPlatformUnavailableError(RuntimeError):
    """The configured material-platform dependency cannot serve its contract."""


class MaterialPlatformContractError(RuntimeError):
    """A valid platform response says a business object is unavailable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ContractImage:
    image_role: str
    sole_color: str | None = None
    asset_id: str | None = None
    uri: str | None = None


@dataclass(frozen=True)
class BatchInfo:
    batch_id: str
    batch_code: str | None = None
    version_no: int | None = None
    category_code: str | None = None
    design_package_id: str | None = None


@dataclass(frozen=True)
class VariantCandidate:
    variant_id: str
    material_id: str | None = None
    material_code: str | None = None
    design_package_id: str | None = None
    category_code: str | None = None
    display_code: str | None = None
    batch_id: str | None = None
    images: tuple[ContractImage, ...] = ()
    # Deprecated compatibility fields. New code must use ``images`` from the
    # Child ASIN contract; these fields are never populated from the old API.
    material_source_asset_id: str | None = None
    material_source_url: str | None = None
    final_effect_url: str | None = None
    sole_color: str | None = None

    @property
    def material_source_images(self) -> tuple[ContractImage, ...]:
        return tuple(i for i in self.images if i.image_role == "MATERIAL_SOURCE")

    def final_effect_images(self, sole_color: str | None = None) -> tuple[ContractImage, ...]:
        return tuple(
            i for i in self.images
            if i.image_role == "FINAL_EFFECT"
            and (sole_color is None or (i.sole_color or "").upper() == sole_color.upper())
        )


@dataclass(frozen=True)
class ChildAsinCandidates:
    child_asin: str
    category_code: str | None
    batch: BatchInfo | None
    variants: tuple[VariantCandidate, ...]


class MaterialPlatformGateway(Protocol):
    def base_url(self) -> str: ...
    def check_available(self) -> bool: ...
    def get_candidates_by_child_asin(self, child_asin: str) -> ChildAsinCandidates: ...
    def get_variant(self, variant_id: str) -> VariantCandidate | None: ...
    def get_image_bytes(self, image: ContractImage) -> bytes | None: ...
    def list_variant_options(self, child_asin: str, limit: int = 100) -> list[dict]: ...


class HttpMaterialPlatformClient:
    """HTTP client for GET /api/contract/materials-by-child-asin/{child_asin}."""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0), read=timeout),
            follow_redirects=True,
        )
        self._cache: dict[str, ChildAsinCandidates] = {}

    def base_url(self) -> str:
        return self._base

    def check_available(self) -> bool:
        try:
            response = self._client.get(f"{self._base}/api/health")
            response.raise_for_status()
            if response.json().get("status") != "ok":
                raise MaterialPlatformUnavailableError("material-platform 健康检查未通过")
            return True
        except MaterialPlatformUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MaterialPlatformUnavailableError(f"material-platform 不可访问：{self._base}") from exc

    def get_candidates_by_child_asin(self, child_asin: str) -> ChildAsinCandidates:
        try:
            response = self._client.get(f"{self._base}/api/contract/materials-by-child-asin/{child_asin}")
            if response.status_code == 404:
                raise MaterialPlatformContractError("CHILD_ASIN_NOT_FOUND", f"Child ASIN 不存在：{child_asin}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("childAsin") or not isinstance(payload.get("variants"), list):
                raise ValueError("material-platform contract response malformed")
            result = self._to_contract(payload)
            self._cache[child_asin] = result
            return result
        except MaterialPlatformContractError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MaterialPlatformUnavailableError(f"material-platform Child ASIN 契约不可用：{child_asin}") from exc

    def get_variant(self, variant_id: str) -> VariantCandidate | None:
        for result in self._cache.values():
            for candidate in result.variants:
                if candidate.variant_id == variant_id:
                    return candidate
        return None

    def get_image_bytes(self, image: ContractImage) -> bytes | None:
        if not image.uri:
            return None
        try:
            url = image.uri if image.uri.startswith(("http://", "https://")) else urljoin(self._base + "/", image.uri.lstrip("/"))
            response = self._client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            if content_type and not (content_type.startswith("image/") or content_type == "application/octet-stream"):
                raise MaterialPlatformUnavailableError(f"素材图片 Content-Type 无效：{content_type}")
            return response.content or None
        except Exception as exc:  # noqa: BLE001
            raise MaterialPlatformUnavailableError(f"material-platform 图片不可用：{image.uri}") from exc

    def list_variant_options(self, child_asin: str, limit: int = 100) -> list[dict]:
        result = self.get_candidates_by_child_asin(child_asin)
        return [_option(c, result.batch) for c in result.variants[:limit]]

    @staticmethod
    def _to_contract(data: dict) -> ChildAsinCandidates:
        raw_batch = data.get("batch") or {}
        batch = BatchInfo(
            batch_id=str(raw_batch.get("batchId")),
            batch_code=raw_batch.get("batchCode"),
            version_no=raw_batch.get("versionNo"),
            category_code=raw_batch.get("categoryCode"),
            design_package_id=raw_batch.get("designPackageId"),
        ) if raw_batch.get("batchId") else None
        variants = []
        for raw in data.get("variants") or []:
            images = tuple(ContractImage(
                image_role=str(img.get("imageRole") or ""),
                sole_color=img.get("soleColor"), asset_id=img.get("assetId"), uri=img.get("uri"),
            ) for img in (raw.get("images") or []))
            variants.append(VariantCandidate(
                variant_id=str(raw.get("variantId") or ""), display_code=raw.get("displayCode"),
                material_id=raw.get("materialId"), material_code=raw.get("materialCode"),
                category_code=raw.get("categoryCode"), batch_id=raw.get("batchId") or (batch.batch_id if batch else None),
                design_package_id=batch.design_package_id if batch else None, images=images,
            ))
        return ChildAsinCandidates(
            child_asin=str(data.get("childAsin") or ""), category_code=data.get("categoryCode"),
            batch=batch, variants=tuple(v for v in variants if v.variant_id),
        )


class FakeMaterialPlatformGateway:
    """In-memory implementation of the same contract for development/tests."""

    def __init__(self, candidates: list[VariantCandidate] | None = None) -> None:
        self._candidates = candidates or []
        self._by_id = {c.variant_id: c for c in self._candidates}
        self._by_asin: dict[str, list[str]] = {}
        self._material_source_bytes: dict[str, bytes] = {}
        self._final_effect_bytes: dict[tuple[str, str | None], bytes] = {}
        self._batches: dict[str, BatchInfo] = {}

    def base_url(self) -> str:
        return "fake://material-platform"

    def check_available(self) -> bool:
        return True

    def get_candidates_by_child_asin(self, child_asin: str) -> ChildAsinCandidates:
        ids = self._by_asin.get(child_asin, [c.variant_id for c in self._candidates])
        variants = tuple(self._by_id[v] for v in ids if v in self._by_id)
        if child_asin in self._by_asin and not variants and child_asin not in self._batches:
            raise MaterialPlatformContractError("CHILD_ASIN_NOT_FOUND", f"Child ASIN 不存在：{child_asin}")
        batch = self._batches.get(child_asin) or BatchInfo(
            batch_id=f"fake-batch-{child_asin}", batch_code="FAKE", version_no=1,
            category_code=variants[0].category_code if variants else None, design_package_id="fake-package",
        )
        return ChildAsinCandidates(child_asin, batch.category_code, batch, variants)

    def get_variant(self, variant_id: str) -> VariantCandidate | None:
        return self._by_id.get(variant_id)

    def get_image_bytes(self, image: ContractImage) -> bytes | None:
        for variant_id, candidate in self._by_id.items():
            if any(i.asset_id == image.asset_id for i in candidate.images):
                if image.image_role == "MATERIAL_SOURCE":
                    return self._material_source_bytes.get(variant_id)
                return self._final_effect_bytes.get((variant_id, image.sole_color))
        return None

    def list_variant_options(self, child_asin: str, limit: int = 100) -> list[dict]:
        result = self.get_candidates_by_child_asin(child_asin)
        return [_option(c, result.batch) for c in result.variants[:limit]]

    def seed_material_source(self, variant_id: str, data: bytes) -> None:
        self._material_source_bytes[variant_id] = data
        candidate = self._by_id.get(variant_id)
        if candidate and not candidate.material_source_images:
            self._by_id[variant_id] = _with_image(candidate, ContractImage("MATERIAL_SOURCE", asset_id=f"fake-ms-{variant_id}"))

    def seed_asin_candidates(self, child_asin: str, variant_ids: list[str]) -> None:
        self._by_asin[child_asin] = list(variant_ids)

    def seed_final_effect(self, variant_id: str, data: bytes, sole_color: str | None = None) -> None:
        self._final_effect_bytes[(variant_id, sole_color)] = data
        candidate = self._by_id.get(variant_id)
        if candidate:
            self._by_id[variant_id] = _with_image(candidate, ContractImage("FINAL_EFFECT", sole_color=sole_color, asset_id=f"fake-fe-{variant_id}-{sole_color}"))

    # Compatibility helpers for older development fixtures.  They are aliases
    # over the contract-shaped implementation and are not used by matcher.py.
    def list_variant_candidates_by_asin(self, child_asin: str) -> list[VariantCandidate]:
        return list(self.get_candidates_by_child_asin(child_asin).variants)

    def get_variant_material_source_bytes(self, variant_id: str) -> bytes | None:
        return self._material_source_bytes.get(variant_id)

    def get_variant_final_effect_bytes(self, variant_id: str, sole_color: str | None = None) -> bytes | None:
        return self._final_effect_bytes.get((variant_id, sole_color))

    def list_all_variant_options(self, limit: int = 100) -> list[dict]:
        return [_option(c, None) for c in self._candidates[:limit]]


def _with_image(candidate: VariantCandidate, image: ContractImage) -> VariantCandidate:
    return VariantCandidate(**{**candidate.__dict__, "images": tuple(candidate.images) + (image,)})


def _option(candidate: VariantCandidate, batch: BatchInfo | None) -> dict:
    return {
        "variantId": candidate.variant_id, "materialId": candidate.material_id,
        "materialCode": candidate.material_code, "categoryCode": candidate.category_code,
        "displayCode": candidate.display_code, "batchId": candidate.batch_id or (batch.batch_id if batch else None),
        "batchCode": batch.batch_code if batch else None, "versionNo": batch.version_no if batch else None,
        "designPackageId": candidate.design_package_id or (batch.design_package_id if batch else None),
        "images": [i.__dict__ for i in candidate.images],
    }


def get_material_platform() -> MaterialPlatformGateway:
    from app.core.config import settings
    if settings.material_platform_base_url:
        return HttpMaterialPlatformClient(settings.material_platform_base_url)
    if settings.is_production_like or not settings.material_platform_allow_fake:
        raise MaterialPlatformUnavailableError("未配置 MATERIAL_PLATFORM_BASE_URL；当前环境禁止使用 FakeMaterialPlatformGateway")
    logger.warning("未配置 MATERIAL_PLATFORM_BASE_URL，使用 FakeMaterialPlatformGateway（仅测试/开发）")
    return FakeMaterialPlatformGateway()
