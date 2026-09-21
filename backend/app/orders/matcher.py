# ============================================================================
# MaterialMatcher：订单 → Variant / MAT 的素材归因
#
# 只读取 NormalizedOrder / OrderItem 的标准化结果，不解析原始 Amazon JSON。
#
# MATERIAL_SOURCE（素材源图）：
#   material_url → MaterialUrlBinding（人工确认绑定则直接 URL → Variant → MAT，不跑图匹配）
#   未绑定 → Child ASIN → AsinVariantBinding 定位候选 Variant → 图片匹配 → 命中建绑定
#
# FINAL_EFFECT（最终效果图）：
#   final_effect_url → Child ASIN → 候选 Variant → 该 Variant 的 FINAL_EFFECT 图 → 图片匹配
#
# 规则：
#   * 品类隔离：Order.category_code 必须等于候选 Variant 所属设计包的 category_code
#   * 只在该 ASIN 绑定的候选内匹配，**禁止与整个素材库全量比较**
#   * FINAL_EFFECT 不与其他 MATERIAL_SOURCE 原图混比
#   * 匹配失败 → REVIEW_REQUIRED，交给人工审核
# ============================================================================

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AsinVariantBinding,
    Asset,
    DesignPackage,
    DesignPackageMaterial,
    MaterialUrlBinding,
    MaterialVariant,
    VariantEffectImage,
    VariantRevision,
)
from app.services.image_search.embedder import get_embedder

logger = logging.getLogger(__name__)

# ResNet-50 余弦相似度阈值：同素材/同效果图 > 0.85 视为命中（与素材库主副图区分度实测一致）
MATCH_THRESHOLD = 0.85


def _variant_category_code(db: Session, variant_id: str) -> str | None:
    """候选 Variant 所属设计包的品类码（品类隔离依据）。"""
    row = db.execute(
        select(DesignPackage.category_code)
        .select_from(MaterialVariant)
        .join(DesignPackageMaterial, DesignPackageMaterial.id == MaterialVariant.design_package_material_id)
        .join(DesignPackage, DesignPackage.id == DesignPackageMaterial.design_package_id)
        .where(MaterialVariant.id == variant_id)
    ).scalar_one_or_none()
    if row is not None:
        return row
    return None


def _candidate_variants_by_asin(db: Session, child_asin: str) -> list[str]:
    """Child ASIN → 绑定的候选 Variant id（等效替代 Distribution/Batch 定位）。"""
    return list(
        db.execute(
            select(AsinVariantBinding.variant_id).where(AsinVariantBinding.child_asin == child_asin)
        ).scalars().all()
    )


def _variant_asset_bytes(db: Session, variant_id: str) -> bytes | None:
    """Variant 当前素材源图（VariantRevision.asset_id）的字节。"""
    rev = db.execute(
        select(VariantRevision).where(
            VariantRevision.variant_id == variant_id, VariantRevision.deleted.is_(False)
        ).order_by(VariantRevision.revision_no.desc())
    ).scalars().first()
    if rev is None:
        return None
    return _asset_bytes(db, rev.asset_id)


def _asset_bytes(db: Session, asset_id: str) -> bytes | None:
    asset = db.get(Asset, asset_id)
    if asset is None:
        return None
    from app.services.image_search.service import asset_bytes as read_asset

    try:
        return read_asset(asset)
    except Exception:  # noqa: BLE001
        logger.warning("读取 Asset 失败: %s", asset_id)
        return None


def _download(url: str, timeout: float = 15.0) -> bytes | None:
    try:
        import httpx

        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:  # noqa: BLE001
        logger.warning("下载订单图片失败: %s", url[:80])
    return None


def _similarity(a: bytes, b: bytes) -> float | None:
    try:
        embedder = get_embedder()
        va = embedder.embed(a)
        vb = embedder.embed(b)
        import numpy as np

        return float(np.dot(va, vb))
    except Exception:  # noqa: BLE001
        return None


def _best_variant(db: Session, query_bytes: bytes, candidate_ids: list[str]) -> tuple[str | None, float | None]:
    best_id, best_score = None, None
    for variant_id in candidate_ids:
        data = _variant_asset_bytes(db, variant_id)
        if data is None:
            continue
        score = _similarity(query_bytes, data)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_id, best_score = variant_id, score
    return best_id, best_score


def _material_id_of(db: Session, variant_id: str) -> str | None:
    v = db.get(MaterialVariant, variant_id)
    return v.material_id if v else None


def match_material_source(db: Session, item, material_url: str) -> None:
    """MATERIAL_SOURCE：URL 绑定优先，未绑定则 ASIN 候选内图片匹配，命中建绑定。"""
    # 1) URL 已绑定 → 直接命中（不重新跑图片匹配）
    binding = db.execute(
        select(MaterialUrlBinding).where(MaterialUrlBinding.material_url == material_url)
    ).scalar_one_or_none()
    if binding is not None:
        item.matched_variant_id = binding.variant_id
        item.matched_material_id = binding.material_id or _material_id_of(db, binding.variant_id)
        item.match_method = "URL_BINDING"
        item.match_score = 1.0
        item.match_status = "CONFIRMED"
        return

    # 2) ASIN → 候选 Variant（只在该 ASIN 的绑定内匹配，不全库比较）
    if not item.child_asin:
        item.match_status = "REVIEW_REQUIRED"
        return
    candidates = _candidate_variants_by_asin(db, item.child_asin)
    if not candidates:
        item.match_status = "REVIEW_REQUIRED"
        return

    query_bytes = _download(material_url)
    if query_bytes is None:
        item.match_status = "REVIEW_REQUIRED"
        return
    best_id, best_score = _best_variant(db, query_bytes, candidates)
    if best_id is not None and best_score is not None and best_score >= MATCH_THRESHOLD:
        item.matched_variant_id = best_id
        item.matched_material_id = _material_id_of(db, best_id)
        item.match_method = "IMAGE_MATCH"
        item.match_score = round(best_score, 4)
        item.match_status = "CONFIRMED"
        _ensure_url_binding(db, material_url, best_id, item.matched_material_id, "AUTO_IMAGE")
    else:
        item.match_method = "IMAGE_MATCH" if best_id else None
        item.match_score = round(best_score, 4) if best_score is not None else None
        item.match_status = "REVIEW_REQUIRED"


def match_final_effect(db: Session, item, final_effect_url: str) -> None:
    """FINAL_EFFECT：ASIN → 候选 Variant → 该 Variant 的 FINAL_EFFECT 图匹配（不与素材源图混比）。"""
    if not item.child_asin:
        item.match_status = "REVIEW_REQUIRED"
        return
    candidates = _candidate_variants_by_asin(db, item.child_asin)
    if not candidates:
        item.match_status = "REVIEW_REQUIRED"
        return
    query_bytes = _download(final_effect_url)
    if query_bytes is None:
        item.match_status = "REVIEW_REQUIRED"
        return

    best_id, best_score = None, None
    for variant_id in candidates:
        effects = db.execute(
            select(VariantEffectImage).where(
                VariantEffectImage.variant_id == variant_id,
                VariantEffectImage.image_role == "FINAL_EFFECT",
            )
        ).scalars().all()
        for effect in effects:
            data = None
            if effect.asset_id:
                data = _asset_bytes(db, effect.asset_id)
            elif effect.source_url:
                data = _download(effect.source_url)
            if data is None:
                continue
            score = _similarity(query_bytes, data)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_id, best_score = variant_id, score

    if best_id is not None and best_score is not None and best_score >= MATCH_THRESHOLD:
        item.matched_variant_id = best_id
        item.matched_material_id = _material_id_of(db, best_id)
        item.match_method = "FINAL_EFFECT_IMAGE"
        item.match_score = round(best_score, 4)
        item.match_status = "CONFIRMED"
    else:
        item.match_method = "FINAL_EFFECT_IMAGE" if best_id else None
        item.match_score = round(best_score, 4) if best_score is not None else None
        item.match_status = "REVIEW_REQUIRED"


def _ensure_url_binding(db: Session, material_url: str, variant_id: str, material_id: str | None, method: str) -> None:
    existing = db.execute(
        select(MaterialUrlBinding).where(MaterialUrlBinding.material_url == material_url)
    ).scalar_one_or_none()
    if existing is not None:
        return
    from app.services.repository import new_id

    db.add(MaterialUrlBinding(
        id=new_id("url-binding"), material_url=material_url,
        variant_id=variant_id, material_id=material_id,
        match_method=method, created_by="matcher",
    ))
    db.flush()


def run_matcher(db: Session, item) -> bool:
    """对单个 OrderItem 执行素材归因。返回是否自动命中（True=CONFIRMED）。"""
    payload = item.normalized_payload or {}
    image_type = payload.get("imageType")
    material_url = payload.get("materialUrl")
    final_effect_url = payload.get("finalEffectUrl")
    if image_type == "MATERIAL_SOURCE" and material_url:
        match_material_source(db, item, material_url)
    elif image_type == "FINAL_EFFECT" and final_effect_url:
        match_final_effect(db, item, final_effect_url)
    else:
        # PREVIEW_ONLY / UNKNOWN：不进自动匹配
        item.match_status = "REVIEW_REQUIRED" if image_type == "UNKNOWN" else item.match_status
        return False
    # 品类隔离：自动命中但品类不符 → 降级 REVIEW_REQUIRED
    if item.match_status == "CONFIRMED" and item.matched_variant_id:
        variant_cat = _variant_category_code(db, item.matched_variant_id)
        if variant_cat and variant_cat != item.category_code:
            item.match_status = "REVIEW_REQUIRED"
            item.match_method = (item.match_method or "") + "|CATEGORY_MISMATCH"
            return False
    return item.match_status == "CONFIRMED"
