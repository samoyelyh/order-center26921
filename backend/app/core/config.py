# ============================================================================
# 后端配置（环境变量驱动，默认值面向本地开发）
# ============================================================================

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    value = os.getenv(key)
    return default if value is None or value == "" else value


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    return _env(key, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # ---------------- 应用 ----------------
    app_name: str = "order-center"
    app_env: str = field(default_factory=lambda: _env("APP_ENV", "development").strip().lower())
    api_prefix: str = "/api"
    debug: bool = field(default_factory=lambda: _env_bool("APP_DEBUG", True))

    # ---------------- MySQL ----------------
    mysql_host: str = field(default_factory=lambda: _env("MYSQL_HOST", "127.0.0.1"))
    mysql_port: int = field(default_factory=lambda: _env_int("MYSQL_PORT", 3306))
    mysql_user: str = field(default_factory=lambda: _env("MYSQL_USER", "root"))
    mysql_password: str = field(default_factory=lambda: _env("MYSQL_PASSWORD", ""))
    mysql_database: str = field(default_factory=lambda: _env("MYSQL_DATABASE", "order_center"))

    # ---------------- 对象存储（MinIO / S3 兼容） ----------------
    # storage_backend:
    #   minio  → 真实 MinIO（boto3 S3 API）
    #   local  → 本地文件系统（无 MinIO 时的开发回退，接口与 MinIO 一致）
    storage_backend: str = field(default_factory=lambda: _env("STORAGE_BACKEND", "minio"))
    s3_endpoint: str = field(default_factory=lambda: _env("S3_ENDPOINT", "http://127.0.0.1:9000"))
    s3_region: str = field(default_factory=lambda: _env("S3_REGION", "us-east-1"))
    s3_access_key: str = field(default_factory=lambda: _env("S3_ACCESS_KEY", "minioadmin"))
    s3_secret_key: str = field(default_factory=lambda: _env("S3_SECRET_KEY", "minioadmin"))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", "material-center"))
    # 对外可访问的地址（用于生成前端可直接使用的 uri）；留空则用 endpoint + bucket
    s3_public_base_url: str = field(default_factory=lambda: _env("S3_PUBLIC_BASE_URL", ""))
    local_storage_root: str = field(default_factory=lambda: _env("LOCAL_STORAGE_ROOT", "./.storage"))

    # ---------------- 上传限制 ----------------
    max_upload_size_bytes: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_SIZE_BYTES", 200 * 1024 * 1024))
    allowed_image_ext: tuple[str, ...] = ("jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff")
    allowed_design_ext: tuple[str, ...] = ("psd", "psb")

    # ---------------- 图片搜索（以图搜图） ----------------
    # 向量检索后端：milvus（Milvus Lite 嵌入式）或 numpy（纯内存余弦，无额外依赖）
    image_search_vector_db: str = field(default_factory=lambda: _env("IMAGE_SEARCH_VECTOR_DB", "milvus"))
    # Milvus Lite 数据文件路径（务必用英文路径：faiss 索引无法写入含中文的目录）
    image_search_milvus_path: str = field(
        default_factory=lambda: _env(
            "IMAGE_SEARCH_MILVUS_PATH",
            os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "mc-milvus", "mc.db"),
        )
    )
    # ResNet-50 预训练权重（本地文件优先；不存在时尝试 torch 官方下载）
    image_search_model_weights: str = field(
        default_factory=lambda: _env("IMAGE_SEARCH_MODEL_WEIGHTS", "models/resnet50-0676ba61.pth")
    )

    # ---------------- 素材平台契约（order-center 对 material-platform） ----------------
    # 设置后走 HTTP（真实环境）；留空回退 Fake 实现（仅测试/开发，不依赖真实素材库）
    material_platform_base_url: str = field(default_factory=lambda: _env("MATERIAL_PLATFORM_BASE_URL", ""))
    # Fake 仅允许本地开发/测试显式使用。production/staging 无论该值为何都禁止 Fake。
    material_platform_allow_fake: bool = field(
        default_factory=lambda: _env_bool("MATERIAL_PLATFORM_ALLOW_FAKE", True)
    )

    @property
    def is_production_like(self) -> bool:
        return self.app_env in {"production", "prod", "staging"}

    # ---------------- 跨域（CORS） ----------------
    # 第2/3条：局域网访问 http://192.168.x.x:3000 时，浏览器 Origin 是局域网 IP，
    # 若白名单只有 localhost，就会出现「Failed to fetch / CORS」。
    # CORS_ORIGINS：逗号分隔的精确来源；CORS_ALLOW_ORIGIN_REGEX：正则（用于任意私网 IP）。
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            item.strip()
            for item in _env(
                "CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000,"
                "http://localhost:4173,http://127.0.0.1:4173,"
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if item.strip()
        )
    )
    cors_allow_origin_regex: str = field(
        default_factory=lambda: _env(
            "CORS_ALLOW_ORIGIN_REGEX",
            # 本机 / 任意私网 IPv4 / 常见隧道域名，端口不限
            r"^http://(localhost|127\.0\.0\.1|0\.0\.0\.0|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
            r"(:\d{1,5})?$",
        )
    )

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def server_database_url(self) -> str:
        """不带库名，用于 CREATE DATABASE。"""
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/?charset=utf8mb4"
        )


def get_settings() -> Settings:
    return Settings()


settings = get_settings()
