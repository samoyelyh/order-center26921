# ============================================================================
# 图片搜索（以图搜图）测试
#
# 核心规则（本文件是可执行文档）：
#   1. 图片搜索是「找相似」，与主副素材配对**完全独立**，绝不自动建立
#      主素材 ↔ 副素材 关联（配对仍只按同名 pairKey + 人工修正）
#   2. 查询图是临时文件：不创建 Material / PackageUploadAsset / DesignPackageMaterial
#   3. Asset 入库后自动生成搜索向量；索引失败不阻断上传（记录 IMAGE_INDEX_FAILED）
#   4. 支持 scope（all/main/variant）、标签、负责人筛选；按相似度降序
#   5. 搜索结果只针对「被业务引用的图」：主素材 preview 或副素材当前 Revision
# ============================================================================

from __future__ import annotations

import io

from sqlalchemy import func, select

from app.db.models import (
    Asset,
    AssetImageEmbedding,
    DesignPackageMaterial,
    Material,
    PackageUploadAsset,
)
from app.services.image_search import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_VERSION,
    embed_image_bytes,
)
from tests.conftest import _png_bytes, _patterned_png, _striped_png


def _search(client, data: bytes, filename: str = "q.png", **form):
    files = {"file": (filename, data, "image/png")}
    return client.post("/api/image-search/search", files=files, data=form)


def test_embedder_vector_shape_and_norm():
    import numpy as np

    from app.services.image_search import get_embedder

    dim = get_embedder().dim
    vec = embed_image_bytes(_png_bytes((200, 30, 30), tag="a"))
    assert vec.shape == (dim,)
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-3, "向量应归一化"


def test_upload_image_auto_indexed(client, db_session, create_upload, create_package, upload_file):
    """图片 Asset 入库后自动生成搜索向量（同 Asset 只算一次）。"""
    from app.services.image_search import get_embedder

    dim = get_embedder().dim
    pkg = create_package("图搜-自动索引")
    upload = create_upload(pkg["id"])["packageUpload"]["id"]
    img = upload_file(upload, _png_bytes((90, 90, 90)), "img.png", "image/png")

    rows = db_session.execute(
        select(AssetImageEmbedding).where(AssetImageEmbedding.asset_id == img["assetId"])
    ).scalars().all()
    assert len(rows) == 1, "图片 Asset 应自动入索引且只算一次"
    row = rows[0]
    assert row.dim == dim


def test_similar_colors_search_high_similarity(client, create_upload, create_package, upload_file):
    """同色（视觉接近）的图：搜索相似度高；不同色：不相似。结果需是业务素材。"""
    pkg = create_package("图搜-同色")
    upload = create_upload(pkg["id"])["packageUpload"]["id"]
    red_a = upload_file(upload, _png_bytes((220, 40, 40), tag="a"), "a.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW")
    red_b = upload_file(upload, _png_bytes((230, 50, 50), tag="b"), "b.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW")
    blue = upload_file(upload, _png_bytes((40, 40, 230), tag="c"), "c.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW")
    # 提交位置，让这些图成为「主素材 preview」→ 可搜索
    submit = client.post(
        f"/api/uploads/{upload}/materials",
        json={
            "materials": [
                {"position": i + 1, "previewAssetId": asset["assetId"], "sourceFileName": asset["originalFilename"]}
                for i, asset in enumerate([red_a, red_b, blue])
            ],
            "linkAssetIds": [a["assetId"] for a in [red_a, red_b, blue]],
        },
    )
    assert submit.status_code == 200, submit.text

    res = client.post(
        "/api/image-search/search",
        data={"assetId": red_a["assetId"], "topK": "10"},
    ).json()["results"]
    sims = {r["assetId"]: r["similarity"] for r in res}
    assert red_b["assetId"] in sims, "视觉接近的图应出现在结果里"
    assert sims[red_b["assetId"]] > sims.get(blue["assetId"], -1), "同色相似度应高于不同色"
    assert all(r["assetId"] != red_a["assetId"] for r in res), "找相似不应返回自己"


def test_search_by_image_and_scope(client, phase2_package):
    """上传查询图搜索；主素材与副素材都能被搜到；scope 过滤生效。"""
    ctx = phase2_package(2)
    # 建 V1：副素材成为「当前 Revision 引用」后才可被搜到
    client.post(f"/api/uploads/{ctx['upload_id']}/pairings/confirm", params={"actor": "小柯"})
    v1 = client.post(
        f"/api/design-packages/{ctx['pkg']['id']}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    )
    assert v1.status_code == 200, v1.text

    # 主素材1 的图（_patterned_png(1)）与副素材1 的图（_striped_png(1)）
    r_all = _search(client, _patterned_png(1))
    assert r_all.status_code == 200
    kinds = {item["kind"] for item in r_all.json()["results"]}
    assert kinds, "应有搜索结果"
    assert "MAIN" in kinds or "VARIANT" in kinds

    r_main = _search(client, _patterned_png(1), scope="main")
    assert r_main.json()["results"], "主素材应能被搜到"
    assert all(item["kind"] == "MAIN" for item in r_main.json()["results"])

    r_var = _search(client, _striped_png(1), scope="variant")
    assert r_var.json()["results"], "副素材应能被搜到"
    assert all(item["kind"] == "VARIANT" for item in r_var.json()["results"])


def test_query_image_is_temporary_not_creating_material(client, db_session, phase2_package):
    """搜索查询图是临时文件：不创建任何正式素材。"""
    phase2_package(1)
    before_m = db_session.execute(select(func.count(Material.id))).scalar_one()
    before_pa = db_session.execute(select(func.count(PackageUploadAsset.id))).scalar_one()
    before_dpm = db_session.execute(select(func.count(DesignPackageMaterial.id))).scalar_one()

    r = _search(client, _striped_png(7), "query.png")
    assert r.status_code == 200
    assert db_session.execute(select(func.count(Material.id))).scalar_one() == before_m
    assert db_session.execute(select(func.count(PackageUploadAsset.id))).scalar_one() == before_pa
    assert db_session.execute(select(func.count(DesignPackageMaterial.id))).scalar_one() == before_dpm


def test_search_tags_and_responsible_filter(client, phase2_package):
    """按标签 / 负责人筛选搜索结果（不报错即可，命中任一 tag）。"""
    ctx = phase2_package(1, tags=["万圣节"])
    r = _search(client, _patterned_png(1), responsibleName="肖芸")
    assert r.status_code == 200
    r_tag = _search(client, _patterned_png(1), tags="万圣节")
    assert r_tag.status_code == 200


def test_search_requires_image_or_asset(client):
    r = client.post("/api/image-search/search", data={})
    assert r.status_code == 200
    assert "error" in r.json()


def test_reindex_endpoint(client, db_session, phase2_package):
    ctx = phase2_package(1)
    # 指定 asset 重建
    asset_id = ctx["mains"][0]["assetId"]
    r = client.post("/api/image-search/reindex", data={"assetId": asset_id})
    assert r.status_code == 200
    assert r.json()["indexed"] == 1
    # 全库重建（此时有被业务引用的主素材图）
    r2 = client.post("/api/image-search/reindex")
    assert r2.status_code == 200
    assert r2.json()["indexed"] >= 1


def test_index_failure_does_not_break_upload(client, db_session, create_upload, create_package, upload_file, monkeypatch):
    """索引失败不阻断上传：上传仍成功，且记录 IMAGE_INDEX_FAILED。"""
    from app.services import image_search

    def boom(*args, **kwargs):
        raise RuntimeError("embed 模拟失败")

    monkeypatch.setattr(image_search.service, "embed_image_bytes", boom)
    pkg = create_package("图搜-索引失败")
    upload = create_upload(pkg["id"])["packageUpload"]["id"]
    resp = upload_file(upload, _png_bytes((120, 40, 200)), "ok.png", "image/png")
    assert resp["assetId"]  # 上传本身成功
    from app.db.models import ActivityLog

    logs = db_session.execute(
        select(ActivityLog).where(
            ActivityLog.target_type == "ASSET",
            ActivityLog.target_id == resp["assetId"],
            ActivityLog.action == "IMAGE_INDEX_FAILED",
        )
    ).scalars().all()
    assert logs, "索引失败应记录 IMAGE_INDEX_FAILED"


def test_pairing_does_not_read_image_search():
    """主副素材配对（pairing/batch）绝不 import 图片搜索 —— 两条链路完全独立。"""
    for path in (
        "app/services/pairing_service.py",
        "app/services/batch_service.py",
        "app/api/pairings.py",
        "app/api/batches.py",
    ):
        text = open(path, encoding="utf-8").read()
        assert "image_search" not in text, f"{path} 不应依赖图片搜索"
