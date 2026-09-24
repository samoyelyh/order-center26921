# ============================================================================
# 对象存储抽象层
#
# 生产：MinIO（S3 兼容，boto3）
# 开发回退：本地文件系统（接口与 MinIO 一致，便于无 Docker 环境跑通链路）
#
# storage_key 规则：assets/{yyyy}/{mm}/{uuid}.{ext}
#   —— 不使用业务名称建目录；业务名称可变，Asset id / storage_key 不变。
# ============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """存储层失败（MinIO 写入失败 / 本地磁盘失败）。"""


class ObjectStorage(Protocol):
    def put(self, storage_key: str, data: bytes, content_type: str) -> None: ...
    def delete(self, storage_key: str) -> None: ...
    def exists(self, storage_key: str) -> bool: ...
    def public_url(self, storage_key: str) -> str: ...
    def get(self, storage_key: str) -> tuple[bytes, str]: ...


_MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "psd": "image/vnd.adobe.photoshop",
    "psb": "image/vnd.adobe.photoshop",
}


def _guess_mime(storage_key: str) -> str:
    ext = storage_key.rsplit(".", 1)[-1].lower() if "." in storage_key else ""
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


def build_storage_key(original_filename: str) -> str:
    """assets/{yyyy}/{mm}/{uuid}.{ext}"""
    now = datetime.now(timezone.utc)
    ext = ""
    if "." in original_filename:
        ext = original_filename.rsplit(".", 1)[1].lower()
        ext = "".join(ch for ch in ext if ch.isalnum())[:12]
    suffix = f".{ext}" if ext else ""
    return f"assets/{now.year:04d}/{now.month:02d}/{uuid.uuid4().hex}{suffix}"


# ---------------------------------------------------------------- MinIO / S3


class S3ObjectStorage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:  # noqa: BLE001 - 统一转成 StorageError
            try:
                self._client.create_bucket(Bucket=self._bucket)
                logger.info("已创建存储桶 %s", self._bucket)
            except Exception as exc:  # noqa: BLE001
                raise StorageError(f"MinIO 存储桶不可用：{exc}") from exc

    def put(self, storage_key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=storage_key,
                Body=data,
                ContentType=content_type or "application/octet-stream",
            )
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"MinIO 写入失败：{exc}") from exc

    def delete(self, storage_key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=storage_key)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"MinIO 删除失败：{exc}") from exc

    def exists(self, storage_key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=storage_key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def public_url(self, storage_key: str) -> str:
        if settings.s3_public_base_url:
            return f"{settings.s3_public_base_url.rstrip('/')}/{storage_key}"
        return f"{settings.s3_endpoint.rstrip('/')}/{self._bucket}/{storage_key}"

    def get(self, storage_key: str) -> tuple[bytes, str]:
        """
        读取对象内容。用于后端代理出图 GET /api/assets/{id}/content：
        浏览器永远不直接访问 storage_key / 桶地址，避免桶策略、跨域、内网地址外泄。
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=storage_key)
            body = response["Body"].read()
            content_type = response.get("ContentType") or "application/octet-stream"
            return body, content_type
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"MinIO 读取失败：{exc}") from exc


# ---------------------------------------------------------------- 本地文件系统


class LocalObjectStorage:
    """无 MinIO 时的开发回退：等价语义，落本地目录并通过后端静态路由暴露。"""

    def __init__(self) -> None:
        self._root = Path(settings.local_storage_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        target = (self._root / storage_key).resolve()
        if not str(target).startswith(str(self._root)):
            raise StorageError("非法的 storage_key（越权路径）")
        return target

    def put(self, storage_key: str, data: bytes, content_type: str) -> None:
        path = self._path(storage_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise StorageError(f"本地存储写入失败：{exc}") from exc

    def delete(self, storage_key: str) -> None:
        path = self._path(storage_key)
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            raise StorageError(f"本地存储删除失败：{exc}") from exc

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).exists()

    def public_url(self, storage_key: str) -> str:
        return f"/storage/{storage_key}"

    def get(self, storage_key: str) -> tuple[bytes, str]:
        path = self._path(storage_key)
        try:
            return path.read_bytes(), _guess_mime(storage_key)
        except OSError as exc:
            raise StorageError(f"本地存储读取失败：{exc}") from exc


# ---------------------------------------------------------------- 单例


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        if settings.storage_backend == "local":
            logger.warning("STORAGE_BACKEND=local，使用本地文件系统代替 MinIO")
            _storage = LocalObjectStorage()
        else:
            _storage = S3ObjectStorage()
    return _storage


def reset_storage() -> None:
    """测试用：重置存储单例。"""
    global _storage
    _storage = None


def storage_root() -> Path:
    return Path(settings.local_storage_root).resolve()
