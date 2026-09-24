"""Order image matching against the real Child ASIN contract candidate set."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MaterialUrlBinding
from app.orders.candidate_builder import build_pool
from app.services import fingerprint, image_embedder as _ie
from app.services import material_platform as _mp

logger = logging.getLogger(__name__)
MATCH_THRESHOLD = 0.85


def _gateway():
    return _mp.get_material_platform()


def _download(url: str, timeout: float = 15.0) -> bytes | None:
    try:
        import httpx
        response = httpx.get(
            url,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0), read=timeout),
            follow_redirects=True,
        )
        content_type = (response.headers.get("content-type") or "").lower()
        if response.status_code == 200 and response.content and (not content_type or content_type.startswith("image/") or content_type == "application/octet-stream"):
            return response.content
    except Exception:  # noqa: BLE001
        logger.warning("下载订单图片失败：%s", url[:120])
    return None


def _similarity(a: bytes, b: bytes) -> float | None:
    try:
        embedder = _ie.get_embedder()
        import numpy as np
        return float(np.dot(embedder.embed(a), embedder.embed(b)))
    except Exception:  # noqa: BLE001
        return None


def _candidate_score(query: bytes, candidate: bytes) -> tuple[str, float] | None:
    if fingerprint.compute_blake3(query) == fingerprint.compute_blake3(candidate):
        return "BLAKE3_EXACT", 1.0
    query_hash = fingerprint.compute_phash(query)
    candidate_hash = fingerprint.compute_phash(candidate)
    if query_hash and candidate_hash:
        distance = sum((a ^ b).bit_count() for a, b in zip(query_hash, candidate_hash))
        score = 1.0 - distance / 64.0
        if score >= MATCH_THRESHOLD:
            return "PHASH", score
    score = _similarity(query, candidate)
    return ("IMAGE_EMBEDDING", score) if score is not None else None


def _load_contract(gw, child_asin: str):
    return gw.get_candidates_by_child_asin(child_asin)


def _filtered_candidates(contract, category_code: str | None):
    if not category_code or contract.category_code != category_code:
        return [], "CATEGORY_MISMATCH"
    candidates = [c for c in contract.variants if c.category_code == category_code]
    if not candidates:
        return [], "CATEGORY_MISMATCH" if contract.variants else "NO_VARIANT_CANDIDATES"
    return candidates, None


def _rank_images(gw, query: bytes, pool) -> list[tuple[object, str, float]]:
    ranked = []
    for candidate_image in pool.candidates:
            try:
                data = gw.get_image_bytes(candidate_image.image)
            except _mp.MaterialPlatformUnavailableError:
                raise
            if not data:
                continue
            scored = _candidate_score(query, data)
            if scored is None:
                continue
            method, score = scored
            ranked.append((candidate_image, method, score))
    ranked.sort(key=lambda row: row[2], reverse=True)
    return ranked


def _set_failure(item, status: str, reason: str, method: str | None = None):
    item.match_status = status
    item.match_reason = reason
    item.match_method = method
    item.match_score = None
    item.second_match_score = None
    item.score_gap = None
    item.matched_variant_id = None
    item.matched_material_id = None


def _apply_success(item, candidate, method: str, score: float):
    item.matched_variant_id = candidate.variant_id
    item.matched_material_id = candidate.material_id
    item.match_method = method
    item.match_score = round(score, 4)
    item.match_status = "CONFIRMED"
    item.match_reason = None


def _contract_failure(item, exc: Exception):
    if isinstance(exc, _mp.MaterialPlatformContractError):
        _set_failure(item, "UNMATCHED" if exc.code == "CHILD_ASIN_NOT_FOUND" else "REVIEW_REQUIRED", exc.code)
    else:
        _set_failure(item, "REVIEW_REQUIRED", "MATERIAL_PLATFORM_UNAVAILABLE")


def match_material_source(db: Session, item, material_url: str) -> None:
    gw = _gateway()
    binding = db.execute(select(MaterialUrlBinding).where(MaterialUrlBinding.material_url == material_url)).scalar_one_or_none()
    if binding is not None:
        candidate = gw.get_variant(binding.variant_id)
        if candidate is None and item.child_asin:
            try:
                contract = _load_contract(gw, item.child_asin)
                candidate = next((c for c in contract.variants if c.variant_id == binding.variant_id), None)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, _mp.MaterialPlatformUnavailableError):
                    raise
                _contract_failure(item, exc)
                return
        if candidate is not None and candidate.category_code == item.category_code:
            _apply_success(item, candidate, "URL_BINDING", 1.0)
        else:
            _set_failure(item, "REVIEW_REQUIRED", "STALE_BINDING_OR_CATEGORY_MISMATCH", "URL_BINDING")
        return
    if not item.child_asin:
        _set_failure(item, "UNMATCHED", "CHILD_ASIN_MISSING")
        return
    try:
        contract = _load_contract(gw, item.child_asin)
        pool = build_pool(contract, order_category=item.category_code, image_role="MATERIAL_SOURCE")
        if pool.reason:
            _set_failure(item, "REVIEW_REQUIRED" if pool.reason == "CATEGORY_MISMATCH" else "UNMATCHED", pool.reason)
            return
        query = _download(material_url)
        if not query:
            _set_failure(item, "REVIEW_REQUIRED", "IMAGE_DOWNLOAD_FAILED")
            return
        ranked = _rank_images(gw, query, pool)
        best = ranked[0] if ranked else None
        second = ranked[1] if len(ranked) > 1 else None
        if best and best[2] >= MATCH_THRESHOLD and (second is None or best[2] - second[2] >= 0.05):
            _apply_success(item, next(c for c in contract.variants if c.variant_id == best[0].variant_id), best[1], best[2])
        else:
            _set_failure(item, "REVIEW_REQUIRED", "AMBIGUOUS_MATCH" if second else "NO_CONFIDENT_MATCH", best[1] if best else None)
            if best:
                item.match_score = round(best[2], 4)
                item.second_match_score = round(second[2], 4) if second else None
                item.score_gap = round(best[2] - second[2], 4) if second else None
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, _mp.MaterialPlatformUnavailableError):
            raise
        _contract_failure(item, exc)


def match_final_effect(db: Session, item, final_effect_url: str) -> None:
    gw = _gateway()
    if not item.child_asin:
        _set_failure(item, "UNMATCHED", "CHILD_ASIN_MISSING")
        return
    sole_color = (item.normalized_payload or {}).get("soleColor")
    try:
        contract = _load_contract(gw, item.child_asin)
        if not sole_color:
            _set_failure(item, "REVIEW_REQUIRED", "SOLE_COLOR_MISSING")
            return
        pool = build_pool(contract, order_category=item.category_code, image_role="FINAL_EFFECT", sole_color=sole_color)
        if pool.reason:
            _set_failure(item, "REVIEW_REQUIRED", pool.reason)
            return
        query = _download(final_effect_url)
        if not query:
            _set_failure(item, "REVIEW_REQUIRED", "IMAGE_DOWNLOAD_FAILED")
            return
        ranked = _rank_images(gw, query, pool)
        best = ranked[0] if ranked else None
        second = ranked[1] if len(ranked) > 1 else None
        if best and best[2] >= MATCH_THRESHOLD and (second is None or best[2] - second[2] >= 0.05):
            _apply_success(item, next(c for c in contract.variants if c.variant_id == best[0].variant_id), best[1], best[2])
        else:
            _set_failure(item, "REVIEW_REQUIRED", "AMBIGUOUS_MATCH" if second else "NO_CONFIDENT_MATCH", best[1] if best else None)
            if best:
                item.match_score = round(best[2], 4)
                item.second_match_score = round(second[2], 4) if second else None
                item.score_gap = round(best[2] - second[2], 4) if second else None
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, _mp.MaterialPlatformUnavailableError):
            raise
        _contract_failure(item, exc)


def _ensure_url_binding(db: Session, material_url: str, variant_id: str, material_id: str | None, method: str) -> None:
    existing = db.execute(select(MaterialUrlBinding).where(MaterialUrlBinding.material_url == material_url)).scalar_one_or_none()
    if existing is None:
        from app.services.repository import new_id
        db.add(MaterialUrlBinding(id=new_id("url-binding"), material_url=material_url, variant_id=variant_id, material_id=material_id, match_method=method, created_by="matcher"))
        db.flush()


def run_matcher(db: Session, item) -> bool:
    payload = item.normalized_payload or {}
    item.matched_variant_id = None
    item.matched_material_id = None
    item.match_method = None
    item.match_score = None
    item.second_match_score = None
    item.score_gap = None
    item.match_reason = None
    item.match_status = "UNMATCHED"
    if payload.get("parseStatus") == "REVIEW_REQUIRED":
        _set_failure(item, "REVIEW_REQUIRED", "ORDER_PARSE_REVIEW_REQUIRED")
        return False
    image_type = payload.get("imageType")
    material_url = payload.get("materialUrl")
    final_effect_url = payload.get("finalEffectUrl")
    if image_type == "MATERIAL_SOURCE" and material_url:
        match_material_source(db, item, material_url)
    elif image_type == "FINAL_EFFECT" and final_effect_url:
        match_final_effect(db, item, final_effect_url)
    elif image_type == "UNKNOWN":
        _set_failure(item, "REVIEW_REQUIRED", "IMAGE_TYPE_UNKNOWN")
    else:
        _set_failure(item, "UNMATCHED", "NO_MATCHABLE_IMAGE")
        return False
    if item.match_status == "CONFIRMED" and item.match_method != "URL_BINDING" and material_url:
        _ensure_url_binding(db, material_url, item.matched_variant_id, item.matched_material_id, "AUTO_IMAGE")
    return item.match_status == "CONFIRMED"
