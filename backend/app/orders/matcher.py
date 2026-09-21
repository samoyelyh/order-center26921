# ============================================================================
# MaterialMatcher：订单 → Variant / MAT 的素材归因
#
# 只读取 NormalizedOrder / OrderItem 的标准化结果，不解析原始 Amazon JSON。
# **不直接引用素材库 ORM / model** —— 所有素材域信息通过 MaterialPlatformClient
# （MaterialPlatformGateway 契约）获取。
#
# MATERIAL_SOURCE（素材源图）：
#   material_url → MaterialUrlBinding（人工确认绑定则直接 URL → Variant → MAT，不跑图匹配）
#   未绑定 → Child ASIN → 平台候选 Variant → 平台素材源图匹配 → 命中建绑定
#
# FINAL_EFFECT（最终效果图）：
#   final_effect_url → Child ASIN → 平台候选 Variant → 该 Variant 的 FINAL_EFFECT 图 → 匹配
#
# 规则：
#   * 品类隔离：Order.category_code = Candidate.category_code（经平台契约）
#   * 只在该 ASIN 的候选内匹配，禁止与整个素材库全量比较
#   * FINAL_EFFECT 不与其他 MATERIAL_SOURCE 原图混比
#   * 匹配失败 → REVIEW_REQUIRED，交给人工审核
# ============================================================================

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AsinVariantBinding, MaterialUrlBinding
from app.services import image_embedder as _ie
from app.services import material_platform as _mp

logger = logging.getLogger(__name__)

# ResNet-50 余弦相似度阈值：同素材/同效果图 > 0.85 视为命中
MATCH_THRESHOLD = 0.85


def _gateway():
    return _mp.get_material_platform()


def _candidate_variants_by_asin(db: Session, child_asin: str) -> list[str]:
    """Child ASIN → 绑定的候选 Variant id（等效替代 Distribution/Batch 定位）。"""
    return list(
        db.execute(
            select(AsinVariantBinding.variant_id).where(AsinVariantBinding.child_asin == child_asin)
        ).scalars().all()
    )


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
        embedder = _ie.get_embedder()
        va = embedder.embed(a)
        vb = embedder.embed(b)
        import numpy as np

        return float(np.dot(va, vb))
    except Exception:  # noqa: BLE001
        return None


def _best_variant_material_source(gw, query_bytes: bytes, candidate_ids: list[str]) -> tuple[str | None, float | None]:
    """在候选 Variant 内用素材源图匹配，返回最佳 (variant_id, score)。"""
    best_id, best_score = None, None
    for variant_id in candidate_ids:
        data = gw.get_variant_material_source_bytes(variant_id)
        if data is None:
            continue
        score = _similarity(query_bytes, data)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_id, best_score = variant_id, score
    return best_id, best_score


def _best_variant_final_effect(gw, query_bytes: bytes, candidate_ids: list[str], sole_color: str | None) -> tuple[str | None, float | None]:
    """在候选 Variant 内用 FINAL_EFFECT 效果图匹配（不与其他素材源图混比）。"""
    best_id, best_score = None, None
    for variant_id in candidate_ids:
        data = gw.get_variant_final_effect_bytes(variant_id, sole_color)
        if data is None:
            continue
        score = _similarity(query_bytes, data)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_id, best_score = variant_id, score
    return best_id, best_score


def _variant_category_code(gw, variant_id: str) -> str | None:
    """候选 Variant 的品类码（经平台契约，品类隔离依据）。"""
    candidate = gw.get_variant(variant_id)
    return candidate.category_code if candidate else None


def _material_id_of(gw, variant_id: str) -> str | None:
    candidate = gw.get_variant(variant_id)
    return candidate.material_id if candidate else None


def match_material_source(db: Session, item, material_url: str) -> None:
    """MATERIAL_SOURCE：URL 绑定优先，未绑定则 ASIN 候选内图片匹配，命中建绑定。"""
    gw = _gateway()
    # 1) URL 已绑定 → 直接命中（不重新跑图片匹配）
    binding = db.execute(
        select(MaterialUrlBinding).where(MaterialUrlBinding.material_url == material_url)
    ).scalar_one_or_none()
    if binding is not None:
        item.matched_variant_id = binding.variant_id
        item.matched_material_id = binding.material_id or _material_id_of(gw, binding.variant_id)
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
    best_id, best_score = _best_variant_material_source(gw, query_bytes, candidates)
    if best_id is not None and best_score is not None and best_score >= MATCH_THRESHOLD:
        item.matched_variant_id = best_id
        item.matched_material_id = _material_id_of(gw, best_id)
        item.match_method = "IMAGE_MATCH"
        item.match_score = round(best_score, 4)
        item.match_status = "CONFIRMED"
        _ensure_url_binding(db, material_url, best_id, item.matched_material_id, "AUTO_IMAGE")
    else:
        item.match_method = "IMAGE_MATCH" if best_id else None
        item.match_score = round(best_score, 4) if best_score is not None else None
        item.match_status = "REVIEW_REQUIRED"


def match_final_effect(db: Session, item, final_effect_url: str) -> None:
    """FINAL_EFFECT：ASIN → 候选 Variant → 平台 FINAL_EFFECT 效果图匹配。"""
    gw = _gateway()
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

    sole_color = (item.normalized_payload or {}).get("soleColor")
    best_id, best_score = _best_variant_final_effect(gw, query_bytes, candidates, sole_color)
    if best_id is not None and best_score is not None and best_score >= MATCH_THRESHOLD:
        item.matched_variant_id = best_id
        item.matched_material_id = _material_id_of(gw, best_id)
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
    gw = _gateway()
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
        variant_cat = _variant_category_code(gw, item.matched_variant_id)
        if variant_cat and variant_cat != item.category_code:
            item.match_status = "REVIEW_REQUIRED"
            item.match_method = (item.match_method or "") + "|CATEGORY_MISMATCH"
            return False
    return item.match_status == "CONFIRMED"
