# ============================================================================
# FastAPI 应用入口（Phase 1：设计包 → 上传 → Asset → 主素材 → 位置）
# ============================================================================

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import batches, design_packages, image_search, materials, order_match, orders, pairings, uploads
from app.core.config import settings
from app.core.errors import AppError
from app.schemas.dto import ErrorResponse
from app.services.storage import storage_root

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "素材中心 V2 后端 Phase 2：\n"
        "- 设计包（设计美工）/ 每次上传记录（实际上传人）/ 上传会话（归属运营，幂等）\n"
        "- Asset（MinIO）+ BLAKE3 完全相同文件去重；上传行为 ↔ Asset 多对多（file_role / pair_key）\n"
        "- 主素材 MAT + 设计包位置 + PSD Revision\n"
        "- 副素材按本次上传内同名 pair_key 一对一配对（不做内容相似度推断）\n"
        "- 人工确认 / 修改 / 防双重占用\n"
        "- 确认整包后单事务生成上架版本 V1（DerivativeBatch + MaterialVariant + VariantRevision）\n\n"
        "本阶段**不包含**：派发运营（DistributionTask）、ASIN、订单 URL 识别（均为 Phase 3）。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    # 第2/3条：局域网（192.168.x.x:3000）访问也必须放行，
    # 否则浏览器直接报 CORS / Failed to fetch，页面以为「后端不可用」。
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# ---------------------------------------------------------------- 统一错误响应


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常")
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(code="SERVER_ERROR", message=f"服务端内部错误：{exc}").model_dump(),
    )


# ---------------------------------------------------------------- 路由

app.include_router(design_packages.router, prefix=settings.api_prefix)
app.include_router(uploads.router, prefix=settings.api_prefix)
app.include_router(materials.router, prefix=settings.api_prefix)
app.include_router(pairings.router, prefix=settings.api_prefix)
app.include_router(batches.router, prefix=settings.api_prefix)
app.include_router(image_search.router, prefix=settings.api_prefix)
app.include_router(orders.router, prefix=settings.api_prefix)
app.include_router(order_match.router, prefix=settings.api_prefix)
app.include_router(order_match.review_router, prefix=settings.api_prefix)
app.include_router(order_match.sales_router, prefix=settings.api_prefix)


@app.get("/api/health", tags=["system"], summary="健康检查")
def health() -> dict:
    from sqlalchemy import text

    from app.db.session import engine

    db_ok = True
    db_error = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = str(exc)

    storage_ok = True
    storage_error = None
    try:
        from app.services.storage import get_storage

        get_storage()
    except Exception as exc:  # noqa: BLE001
        storage_ok = False
        storage_error = str(exc)

    return {
        "status": "ok" if db_ok and storage_ok else "degraded",
        # 与 overview.phase 保持一致：当前已进入 Phase 2（副素材匹配与建版）
        "phase": "PHASE_2",
        "database": {"ok": db_ok, "error": db_error},
        "storage": {"backend": settings.storage_backend, "ok": storage_ok, "error": storage_error},
    }


# 本地存储回退时，通过后端静态路由暴露文件，保证前端 <img src> 可直接访问
if settings.storage_backend == "local":
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    app.mount("/storage", StaticFiles(directory=str(root)), name="storage")
    logger.warning("静态文件路由已挂载：/storage → %s", root)
