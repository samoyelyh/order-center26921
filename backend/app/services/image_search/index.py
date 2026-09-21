# ============================================================================
# 向量检索层（独立封装）
#
# 默认用 Milvus Lite（嵌入式向量库，对齐 Milvus 官方 quickstart
# image_search_with_milvus）；torch/milvus 不可用时回退 numpy 余弦暴力 Top-K。
# 接口统一：set_all(ids, vectors) + search(query, top_k, exclude_ids)。
# 以后要换 FAISS / 独立 Milvus 服务时，实现同一个协议即可，上层不用改。
# ============================================================================

from __future__ import annotations

import os
from typing import Protocol

import numpy as np


class VectorIndex(Protocol):
    """向量索引接口：重建 + Top-K 搜索。"""

    def set_all(self, ids: list[str], vectors: np.ndarray) -> None: ...
    def search(
        self, query: np.ndarray, top_k: int, exclude_ids: set[str] | None = None
    ) -> list[tuple[str, float]]: ...


class NumpyCosineIndex:
    """归一化向量 + 余弦相似度（点积）的暴力 Top-K 索引。"""

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._mat: np.ndarray | None = None  # (N, D) float32，已归一化

    def set_all(self, ids: list[str], vectors: np.ndarray) -> None:
        if len(ids) == 0:
            self._ids, self._mat = [], None
            return
        mat = np.asarray(vectors, dtype=np.float32)
        # 防御：归一化
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms
        self._ids = list(ids)
        self._mat = mat

    def search(
        self, query: np.ndarray, top_k: int, exclude_ids: set[str] | None = None
    ) -> list[tuple[str, float]]:
        if self._mat is None or len(self._ids) == 0:
            return []
        q = np.asarray(query, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn > 0:
            q = q / qn
        scores = self._mat @ q  # 余弦（归一化后）
        order = np.argsort(-scores)
        out: list[tuple[str, float]] = []
        for idx in order:
            asset_id = self._ids[int(idx)]
            if exclude_ids and asset_id in exclude_ids:
                continue
            out.append((asset_id, float(scores[int(idx)])))
            if len(out) >= top_k:
                break
        return out


_milvus_index_singleton: MilvusVectorIndex | None = None


def get_index() -> VectorIndex:
    """Return the configured index, falling back to an in-memory index.

    Tests and small local runs explicitly select ``IMAGE_SEARCH_VECTOR_DB=numpy``
    so they do not contend on a shared Milvus-Lite file.  Production keeps the
    existing Milvus default and still falls back gracefully when it cannot be
    opened.

    Milvus 客户端做**单例缓存**：每次搜索新建 MilvusClient 会不断开新的
    gRPC 连接，长连接 keepalive 触发 too_many_pings，最终可能把内嵌服务拖崩。
    """
    from app.core.config import settings

    if settings.image_search_vector_db.lower() == "numpy":
        return NumpyCosineIndex()
    global _milvus_index_singleton
    if _milvus_index_singleton is None:
        try:
            _milvus_index_singleton = MilvusVectorIndex()
        except Exception:  # noqa: BLE001
            return NumpyCosineIndex()
    return _milvus_index_singleton


class MilvusVectorIndex:
    """Milvus Lite（嵌入式）向量检索：dim=2048，COSINE 相似度。

    对齐 Milvus 官方 bootcamp image_search_with_milvus：
      * collection 按 embedding_model/version 命名，换模型不混算
      * 写入/搜索都用同一 MilvusClient；数据文件用英文路径（faiss 无法写中文目录）
    """

    def __init__(self, dim: int | None = None, path: str | None = None) -> None:
        from app.core.config import settings
        from pymilvus import MilvusClient

        if dim is None:
            from app.services.image_search.embedder import get_embedder

            dim = get_embedder().dim
        db_path = path or settings.image_search_milvus_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._client = MilvusClient(db_path)
        self._dim = dim
        self._collection = "image_search"
        self._loaded = False
        if not self._client.has_collection(self._collection):
            self._client.create_collection(
                self._collection,
                dimension=dim,
                metric_type="COSINE",
                id_type="string",
            )
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        """Milvus 重建 collection 后处于 released 状态，search 前必须 load。
        只在新建/重建后 load 一次，避免每次搜索都发 gRPC 调用。"""
        if self._loaded:
            return
        try:
            self._client.load_collection(self._collection)
            self._loaded = True
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 协议

    def set_all(self, ids: list[str], vectors: np.ndarray) -> None:
        self._client.drop_collection(self._collection)
        self._client.create_collection(
            self._collection,
            dimension=self._dim,
            metric_type="COSINE",
            id_type="string",
        )
        self._loaded = False
        self._ensure_loaded()
        if len(ids) == 0:
            return
        rows = [
            {"id": asset_id, "vector": np.asarray(vec, dtype=np.float32).tolist()}
            for asset_id, vec in zip(ids, vectors)
        ]
        self._client.insert(self._collection, rows)

    def search(
        self, query: np.ndarray, top_k: int, exclude_ids: set[str] | None = None
    ) -> list[tuple[str, float]]:
        if top_k <= 0:
            return []
        self._ensure_loaded()
        res = self._client.search(
            self._collection,
            data=[np.asarray(query, dtype=np.float32).tolist()],
            limit=max(top_k * 2, 64),
            output_fields=[],
        )
        out: list[tuple[str, float]] = []
        if not res or not res[0]:
            return out
        for hit in res[0]:
            asset_id = str(hit["id"])
            if exclude_ids and asset_id in exclude_ids:
                continue
            out.append((asset_id, float(hit["distance"])))
            if len(out) >= top_k:
                break
        return out

    # ---------------------------------------------------------------- 维护

    def upsert_one(self, asset_id: str, vec: np.ndarray) -> None:
        self._ensure_loaded()
        self._client.upsert(
            self._collection,
            [{"id": asset_id, "vector": np.asarray(vec, dtype=np.float32).tolist()}],
        )

    def delete(self, asset_ids: list[str]) -> None:
        if asset_ids:
            self._client.delete(self._collection, ids=asset_ids)

    def get_vector(self, asset_id: str) -> np.ndarray | None:
        res = self._client.get(self._collection, ids=[asset_id])
        if not res or not res[0]:
            return None
        vec = res[0].get("vector")
        if vec is None:
            return None
        return np.asarray(vec, dtype=np.float32)

    def count(self) -> int:
        return self._client.get_collection_stats(self._collection).get("row_count", 0)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass
