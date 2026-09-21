# ============================================================================
# ImageSearchService：图片搜索编排
#
# 查询图/Asset → embedding → 向量索引 Top-K → MySQL 补充（标签/负责人/设计包）
#
# **与主副素材配对完全独立**：这里的结果只用于「找相似」，绝不用于建立
# 主素材 ↔ 副素材 的关联（配对仍只按同名 pairKey + 人工修正）。
# ============================================================================

from __future__ import annotations

import struct

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    AssetImageEmbedding,
    DerivativeBatch,
    DesignPackage,
    DesignPackageMaterial,
    Material,
    MaterialVariant,
    VariantRevision,
)
from app.schemas.mappers import asset_content_url
from app.services.fingerprint import compute_blake3, compute_phash
from app.services.image_search.embedder import get_embedder
from app.services.image_search.index import MilvusVectorIndex, get_index

CANDIDATE_CAP = 10000  # 全量候选：pHash/BLAKE3 强相似的素材不能被视觉 Top-K 截断漏掉


def _hamming(a: bytes, b: bytes) -> int:
    return sum(bin(x ^ y).count("1") for x, y in zip(a, b))


def _pack_vector(vec: np.ndarray) -> bytes:
    return vec.astype("<f4").tobytes()


def _unpack_vector(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4").astype(np.float32).reshape(dim)


def embed_image_bytes(data: bytes) -> np.ndarray:
    return get_embedder().embed(data)


def save_embedding(db: Session, asset_id: str, vec: np.ndarray) -> None:
    """写入（覆盖）某 Asset 的向量。

    向量写入向量索引（Milvus Lite / numpy 兜底），DB 表保留元数据 + 向量副本。
    调用方负责提交事务。
    """
    embedder = get_embedder()
    row = db.execute(
        select(AssetImageEmbedding).where(AssetImageEmbedding.asset_id == asset_id)
    ).scalar_one_or_none()
    if row is None:
        row = AssetImageEmbedding(id=f"embed-{asset_id}", asset_id=asset_id)
        db.add(row)
    row.embedding_model = embedder.model
    row.embedding_version = embedder.version
    row.dim = int(embedder.dim)
    row.vector = _pack_vector(vec)
    row.indexed_at = _now()
    # 写向量索引（Milvus 优先；numpy 兜底由 get_index 决定）
    index = get_index()
    if isinstance(index, MilvusVectorIndex):
        index.upsert_one(asset_id, vec)
    db.flush()
    return None


def get_asset_vector(db: Session, asset_id: str) -> np.ndarray | None:
    """优先从向量索引（Milvus）取，回退 DB 副本。"""
    index = get_index()
    if isinstance(index, MilvusVectorIndex):
        vec = index.get_vector(asset_id)
        if vec is not None:
            return vec
    row = db.execute(
        select(AssetImageEmbedding).where(AssetImageEmbedding.asset_id == asset_id)
    ).scalar_one_or_none()
    if row is None:
        return None
    return _unpack_vector(row.vector, row.dim)


def _load_index_vectors(db: Session) -> tuple[list[str], np.ndarray | None]:
    """把当前模型版本的全部向量组装成可 search 的索引。

    Milvus：返回空矩阵（search 直接走 Milvus）；numpy：从 DB 读出全部向量。
    """
    index = get_index()
    if isinstance(index, MilvusVectorIndex):
        return [], None
    embedder = get_embedder()
    rows = db.execute(
        select(AssetImageEmbedding).where(
            AssetImageEmbedding.embedding_model == embedder.model,
            AssetImageEmbedding.embedding_version == embedder.version,
        )
    ).scalars().all()
    ids = [row.asset_id for row in rows]
    if not ids:
        return [], None
    vectors = np.stack([_unpack_vector(row.vector, row.dim) for row in rows])
    return ids, vectors


def _now():
    from app.services.repository import utcnow

    return utcnow()


# ---------------------------------------------------------------- 结果反查


def _enrich(db: Session, asset_ids: list[str]) -> dict[str, list[dict]]:
    """把一个 asset_id 反查成「它作为主素材 / 副素材」的结果条目（可能两条）。"""
    out: dict[str, list[dict]] = {}

    # 主素材：preview_asset_id == asset
    materials = db.execute(
        select(Material).where(
            Material.preview_asset_id.in_(asset_ids), Material.archived_at.is_(None)
        )
    ).scalars().all()

    # 副素材：当前 Revision 的 asset_id == asset
    variants = db.execute(
        select(MaterialVariant)
        .join(VariantRevision, VariantRevision.id == MaterialVariant.current_revision_id)
        .where(
            VariantRevision.asset_id.in_(asset_ids),
            MaterialVariant.deleted.is_(False),
        )
    ).scalars().all()

    # 位置 / 包 / 批次
    material_ids = [m.id for m in materials]
    variant_ids = [v.id for v in variants]
    position_rows = db.execute(
        select(DesignPackageMaterial).where(
            DesignPackageMaterial.material_id.in_(material_ids)
        )
    ).scalars().all() if material_ids else []
    pkg_ids = {p.design_package_id for p in position_rows}
    variant_position_ids = [v.design_package_material_id for v in variants]
    variant_positions = db.execute(
        select(DesignPackageMaterial).where(
            DesignPackageMaterial.id.in_(variant_position_ids)
        )
    ).scalars().all() if variant_position_ids else []
    for p in variant_positions:
        pkg_ids.add(p.design_package_id)
    batch_ids = {v.batch_id for v in variants}
    batches = db.execute(
        select(DerivativeBatch).where(DerivativeBatch.id.in_(batch_ids))
    ).scalars().all() if batch_ids else []
    pkg_ids = {x for x in pkg_ids if x}
    packages = db.execute(
        select(DesignPackage).where(DesignPackage.id.in_(pkg_ids))
    ).scalars().all() if pkg_ids else []

    material_by_id = {m.id: m for m in materials}
    variant_by_id = {v.id: v for v in variants}
    position_by_material: dict[str, list[DesignPackageMaterial]] = {}
    for p in position_rows:
        position_by_material.setdefault(p.material_id, []).append(p)
    position_by_id = {p.id: p for p in variant_positions}
    package_by_id = {p.id: p for p in packages}
    batch_by_id = {b.id: b for b in batches}

    for m in materials:
        for p in position_by_material.get(m.id, [])[:1]:  # 主素材取第一个位置的设计包
            pkg = package_by_id.get(p.design_package_id)
            out.setdefault(m.preview_asset_id, []).append(
                {
                    "assetId": m.preview_asset_id,
                    "kind": "MAIN",
                    "materialCode": m.material_code,
                    "displayCode": None,
                    "name": m.name,
                    "position": p.position,
                    "designPackageId": pkg.id if pkg else None,
                    "designPackageName": pkg.name if pkg else None,
                    "responsibleName": pkg.responsible_name if pkg else None,
                    "tags": list(m.tags or []),
                    "batchCode": None,
                }
            )

    for v in variants:
        p = position_by_id.get(v.design_package_material_id)
        pkg = package_by_id.get(p.design_package_id) if p else None
        batch = batch_by_id.get(v.batch_id)
        rev = db.execute(
            select(VariantRevision).where(VariantRevision.id == v.current_revision_id)
        ).scalar_one_or_none()
        asset_id = rev.asset_id if rev else None
        if not asset_id:
            continue
        mat = material_by_id.get(v.material_id)
        out.setdefault(asset_id, []).append(
            {
                "assetId": asset_id,
                "kind": "VARIANT",
                "materialCode": mat.material_code if mat else None,
                "displayCode": v.display_code,
                "name": f"副素材 {v.display_code}",
                "position": p.position if p else None,
                "designPackageId": pkg.id if pkg else None,
                "designPackageName": pkg.name if pkg else None,
                "responsibleName": pkg.responsible_name if pkg else None,
                "tags": list(v.tags or []),
                "batchCode": batch.code if batch else None,
                "mainMaterialCode": mat.material_code if mat else None,
            }
        )
    return out


# ---------------------------------------------------------------- 搜索


def _query_tags_for_asset(db: Session, asset_id: str) -> list[str]:
    """查询素材（主素材 preview / 副素材 Revision）自己带的标签，用于相似度加权。

    用户人工录入的字段（标签）参与匹配：结果命中这些标签时相似度加分。
    """
    tags: set[str] = set()
    m = db.execute(
        select(Material).where(
            Material.preview_asset_id == asset_id, Material.archived_at.is_(None)
        )
    ).scalar_one_or_none()
    if m is not None:
        tags.update(m.tags or [])
    rev_ids = db.execute(
        select(VariantRevision.id).where(VariantRevision.asset_id == asset_id)
    ).scalars().all()
    if rev_ids:
        vs = db.execute(
            select(MaterialVariant).where(
                MaterialVariant.current_revision_id.in_(rev_ids),
                MaterialVariant.deleted.is_(False),
            )
        ).scalars().all()
        for v in vs:
            tags.update(v.tags or [])
    return sorted(tags)


def search(
    db: Session,
    *,
    query_bytes: bytes | None = None,
    asset_id: str | None = None,
    scope: str = "all",
    design_package_id: str | None = None,
    tags: list[str] | None = None,
    responsible_name: str | None = None,
    top_k: int = 20,
) -> list[dict]:
    """按查询图或已有 Asset 向量搜索，返回按相似度降序的结果条目。

    相似度 = 三个已录/可算的指纹融合：
      * BLAKE3 完全相同 → 100%（内容完全相同的文件）
      * 否则 视觉向量 60% + pHash 感知相似度 40%（pHash 为 1 - 汉明距离/64）
    另：找相似（asset_id）时查询素材的人工标签命中会再 +2%/个（封顶 +20%）。
    """
    # 查询指纹：BLAKE3 + pHash（上传的查询图现场算；找相似用 Asset 里已录的）
    q_blake3: str | None = None
    q_phash: bytes | None = None
    if query_bytes is not None:
        query_vec = embed_image_bytes(query_bytes)
        boost_tags: list[str] = []
        try:
            q_blake3 = compute_blake3(query_bytes)
            q_phash = compute_phash(query_bytes)
        except Exception:  # noqa: BLE001
            q_blake3 = q_phash = None
    elif asset_id is not None:
        query_vec = get_asset_vector(db, asset_id)
        if query_vec is None:
            return []
        boost_tags = _query_tags_for_asset(db, asset_id)
        q_asset = db.get(Asset, asset_id)
        if q_asset is not None:
            q_blake3 = q_asset.blake3
            q_phash = q_asset.phash
    else:
        return []

    # 组装向量索引（Milvus：直接搜索；numpy 兜底：从 DB 载入全部向量）
    ids, vectors = _load_index_vectors(db)
    index = get_index()
    if vectors is not None:
        index.set_all(ids, vectors)
    candidates = index.search(
        query_vec,
        min(CANDIDATE_CAP, max(len(ids), 1)) if vectors is not None else CANDIDATE_CAP,
        exclude_ids={asset_id} if asset_id else None,
    )

    # 候选 Asset 的 BLAKE3 / pHash（已录字段，参与相似度）
    candidate_ids = [cid for cid, _ in candidates]
    asset_rows = db.execute(
        select(Asset).where(Asset.id.in_(candidate_ids))
    ).scalars().all()
    fingerprint_by_asset = {a.id: (a.blake3, a.phash) for a in asset_rows}

    # 反查业务信息
    enriched = _enrich(db, candidate_ids)
    score_by_asset = dict(candidates)

    # 展开成结果条目，应用筛选 + 融合打分
    results: list[dict] = []
    boost_set = set(boost_tags)
    for asset_id_c, visual_score in candidates:
        for entry in enriched.get(asset_id_c, []):
            if scope == "main" and entry["kind"] != "MAIN":
                continue
            if scope == "variant" and entry["kind"] != "VARIANT":
                continue
            if design_package_id and entry["designPackageId"] != design_package_id:
                continue
            if responsible_name and entry["responsibleName"] != responsible_name:
                continue
            if tags:
                entry_tags = set(entry["tags"] or [])
                if not any(t in entry_tags for t in tags):
                    continue

            # ---- 融合打分：视觉 60% + pHash 40%；BLAKE3 相同 → 100% ----
            cand_blake3, cand_phash = fingerprint_by_asset.get(asset_id_c, (None, None))
            matched_blake3 = bool(q_blake3 and cand_blake3 and q_blake3 == cand_blake3)
            phash_sim: float | None = None
            if q_phash and cand_phash:
                phash_sim = round(1.0 - _hamming(q_phash, cand_phash) / 64.0, 4)
            if matched_blake3:
                score = 1.0
            elif phash_sim is not None:
                score = 0.6 * visual_score + 0.4 * phash_sim
            else:
                score = visual_score

            # 标签加权：命中查询素材的人工标签 → 相似度加分
            matched = sorted(set(entry["tags"] or []) & boost_set)
            if matched:
                score = min(1.0, score + 0.02 * len(matched))

            entry["similarity"] = round(max(0.0, min(1.0, score)) * 100, 1)
            entry["matchedTags"] = matched
            entry["matchedBlake3"] = matched_blake3
            entry["phashSimilarity"] = phash_sim
            entry["previewUrl"] = asset_content_url(asset_id_c)
            results.append(entry)

    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------- 索引维护


def index_asset(db: Session, asset: Asset) -> bool:
    """给单个 Asset 生成向量并写入索引（同 Asset 只算一次 / 覆盖更新）。

    返回 True=成功；失败抛异常由调用方记录 IMAGE_INDEX_FAILED，不阻断入库事务。
    """
    data = asset_bytes(asset)
    vec = embed_image_bytes(data)
    save_embedding(db, asset.id, vec)
    return True


def asset_bytes(asset: Asset) -> bytes:
    from app.services.storage import get_storage

    storage = get_storage()
    data, _content_type = storage.get(asset.storage_key)
    return data


def indexable_asset_ids(db: Session) -> list[str]:
    """需要入索引的图片 Asset：主素材 preview + 副素材当前 Revision。"""
    ids: set[str] = set()
    rows = db.execute(
        select(Material.preview_asset_id).where(Material.archived_at.is_(None))
    ).scalars().all()
    ids.update(rows)
    rev_ids = db.execute(
        select(MaterialVariant.current_revision_id).where(
            MaterialVariant.deleted.is_(False), MaterialVariant.current_revision_id.is_not(None)
        )
    ).scalars().all()
    if rev_ids:
        asset_rows = db.execute(
            select(VariantRevision.asset_id).where(VariantRevision.id.in_(rev_ids))
        ).scalars().all()
        ids.update(asset_rows)
    return sorted(ids)


def rebuild_all(db: Session) -> dict:
    """全库重建索引：对每个可索引 Asset 重新生成向量。失败逐个记录并继续。"""
    from app.services.activity import write_log

    asset_ids = indexable_asset_ids(db)
    assets = db.execute(
        select(Asset).where(Asset.id.in_(asset_ids))
    ).scalars().all()
    ok = 0
    failed: list[str] = []
    for asset in assets:
        try:
            # 每个 Asset 用独立嵌套事务：失败只回滚这一个，不影响其它已写入的
            with db.begin_nested():
                index_asset(db, asset)
                db.flush()
            ok += 1
        except Exception:  # noqa: BLE001
            failed.append(asset.id)
            write_log(
                db,
                design_package_id=None,
                target_type="ASSET",
                target_id=asset.id,
                actor="system",
                action="IMAGE_INDEX_FAILED",
                summary=f"图片向量重建失败：{asset.id}",
            )
    db.commit()
    return {"indexed": ok, "failed": len(failed), "failedIds": failed[:20]}
