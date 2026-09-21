# ============================================================================
# 图片搜索 API（以图搜图）
#
#   POST /api/image-search/search       上传查询图 或 用已有 Asset 向量 搜索
#   POST /api/image-search/reindex      重建指定 Asset / 全库的搜索向量
#
# 原则：
#   * 查询图是**临时文件**：只用于本次搜索，不创建 Material /
#     PackageUploadAsset / DesignPackageMaterial，搜索完立即删除。
#   * 搜索结果与主副素材配对**完全独立**：这里绝不自动建立 主素材↔副素材 关联。
# ============================================================================

from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.errors import AssetNotFound
from app.db.models import Asset
from app.db.session import get_db
from app.services.activity import write_log
from app.services.image_search import rebuild_all, search

router = APIRouter(tags=["image-search"])

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _is_image(filename: str) -> bool:
    return os.path.splitext((filename or "").lower())[1] in ALLOWED_EXT


def _temporary_query_path(upload: UploadFile) -> str:
    """把查询图写到临时目录（不进正式 Asset 素材库），返回路径。"""
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(upload.filename or ".png")[1])
    with os.fdopen(fd, "wb") as f:
        while True:
            chunk = upload.file.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
    return path


@router.post("/image-search/search", summary="以图搜图（上传查询图或按已有 Asset 找相似）")
def image_search(
    file: UploadFile | None = File(default=None, description="查询图（临时，搜索完即删）"),
    assetId: str | None = Form(default=None, description="已有 Asset 直接找相似，无需上传图"),
    scope: str = Form(default="all"),
    designPackageId: str | None = Form(default=None),
    tags: str | None = Form(default=None, description="逗号分隔的标签，命中任一即可"),
    responsibleName: str | None = Form(default=None),
    topK: int = Form(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    query_bytes: bytes | None = None
    temp_path: str | None = None
    try:
        if file is not None:
            if not _is_image(file.filename or ""):
                return {"results": [], "error": "请上传图片文件（png/jpg/webp/gif/bmp）"}
            temp_path = _temporary_query_path(file)
            with open(temp_path, "rb") as f:
                query_bytes = f.read()
        elif assetId:
            asset = db.get(Asset, assetId)
            if asset is None:
                raise AssetNotFound(f"Asset 不存在：{assetId}")
        else:
            return {"results": [], "error": "需要查询图或 assetId"}

        tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

        results = search(
            db,
            query_bytes=query_bytes,
            asset_id=assetId,
            scope=scope,
            design_package_id=designPackageId or None,
            tags=tag_list,
            responsible_name=responsibleName or None,
            top_k=topK,
        )
        return {"results": results}
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


@router.post("/image-search/reindex", summary="重建搜索向量（指定 Asset 或全库）")
def reindex(
    assetId: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict:
    if assetId:
        from app.services.image_search import index_asset

        asset = db.get(Asset, assetId)
        if asset is None:
            raise AssetNotFound(f"Asset 不存在：{assetId}")
        try:
            index_asset(db, asset)
            db.commit()
            write_log(
                db,
                design_package_id=None,
                target_type="ASSET",
                target_id=asset.id,
                actor="system",
                action="IMAGE_REINDEXED",
                summary=f"图片向量重建完成：{asset.id}",
            )
            db.commit()
            return {"indexed": 1, "failed": 0, "failedIds": []}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            write_log(
                db,
                design_package_id=None,
                target_type="ASSET",
                target_id=asset.id,
                actor="system",
                action="IMAGE_INDEX_FAILED",
                summary=f"图片向量重建失败：{asset.id}（{exc}）",
            )
            db.commit()
            return {"indexed": 0, "failed": 1, "failedIds": [asset.id]}
    return rebuild_all(db)
