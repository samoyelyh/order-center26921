# ============================================================================
# Milvus Lite 向量索引单测（独立于 ResNet-50，用小维度向量）
#
# 生产用 Milvus（对齐 Milvus 官方 image_search_with_milvus quickstart）；
# 这里验证 MilvusVectorIndex 的写入/搜索/删除协议，不依赖图片与模型。
# ============================================================================

from __future__ import annotations

import tempfile

import numpy as np

from app.services.image_search.index import MilvusVectorIndex


def test_milvus_index_basic_roundtrip():
    tmp = tempfile.mkdtemp(prefix="mc-milvus-test-")
    idx = MilvusVectorIndex(dim=8, path=f"{tmp}/mc.db")
    try:
        vecs = np.random.rand(6, 8).astype(np.float32)
        ids = [f"asset-{i}" for i in range(6)]
        idx.set_all(ids, vecs)

        # 搜索：自己应排第一（余弦≈1）
        hits = idx.search(vecs[2], top_k=3)
        assert hits[0][0] == ids[2]
        assert hits[0][1] > 0.99

        # 排除自己
        hits2 = idx.search(vecs[2], top_k=3, exclude_ids={ids[2]})
        assert all(h != ids[2] for h, _ in hits2)

        # upsert 覆盖
        idx.upsert_one(ids[0], vecs[5])
        v = idx.get_vector(ids[0])
        assert v is not None
        assert abs(float(np.linalg.norm(v - vecs[5]))) < 1e-4

        # 删除
        idx.delete([ids[1]])
        assert idx.get_vector(ids[1]) is None
        assert idx.count() == 5

        # 清空重建
        idx.set_all([], np.array([]))
        assert idx.count() == 0
    finally:
        idx.close()
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def test_milvus_index_search_excludes_missing():
    tmp = tempfile.mkdtemp(prefix="mc-milvus-test-")
    idx = MilvusVectorIndex(dim=4, path=f"{tmp}/mc.db")
    try:
        idx.set_all(["a"], np.array([[1.0, 0, 0, 0]], dtype=np.float32))
        hits = idx.search(np.array([1.0, 0, 0, 0], dtype=np.float32), top_k=1, exclude_ids={"a"})
        assert hits == [], "排除全部候选后应返回空"
    finally:
        idx.close()
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
