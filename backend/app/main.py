# ============================================================================
# order-center 订单中心 FastAPI 应用入口
#
# 独立项目：只提供订单识别与素材归因 API。
# 对素材库的依赖通过 MaterialPlatformClient（HTTP 契约），不挂素材域路由。
# ============================================================================

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import material_contract, order_match, orders
from app.core.config import settings
from app.core.errors import AppError, ErrorResponse
from app.services.storage import storage_root
from app.services.material_platform import MaterialPlatformUnavailableError

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="order-center（订单识别与素材归因）",
    version="0.3.0",
    description=(
        "独立订单中心：\n"
        "- 领星订单 ZIP 导入（OrderImportBatch / 原始 ZIP/JSON 保留 / orderItemId 去重）\n"
        "- 解析：GenericOrderParser → CategoryResolver → ParserRegistry → BladeShoesParser(BLADE_SHOES_V1)\n"
        "- 素材归因：MaterialMatcher（URL 绑定 / ASIN 候选图片匹配 / 品类隔离）\n"
        "- 人工审核（confirm/change/unmatch）+ 销量归因（仅 CONFIRMED 计入）\n"
        "对素材库依赖走 MaterialPlatformClient（稳定契约），不直接查素材库 ORM。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


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


@app.exception_handler(MaterialPlatformUnavailableError)
async def material_platform_error_handler(_: Request, exc: MaterialPlatformUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"code": "MATERIAL_PLATFORM_UNAVAILABLE", "message": str(exc)},
    )


# ---------------------------------------------------------------- 路由（仅订单域）

app.include_router(orders.router, prefix=settings.api_prefix)
app.include_router(orders.raw_router, prefix=settings.api_prefix)
app.include_router(order_match.router, prefix=settings.api_prefix)
app.include_router(order_match.review_router, prefix=settings.api_prefix)
app.include_router(order_match.sales_router, prefix=settings.api_prefix)
app.include_router(material_contract.router, prefix=settings.api_prefix)


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

    material_platform_ok = True
    material_platform_error = None
    material_platform_mode = "http" if settings.material_platform_base_url else "fake"
    try:
        from app.services.material_platform import get_material_platform

        get_material_platform().check_available()
    except Exception as exc:  # noqa: BLE001
        material_platform_ok = False
        material_platform_error = str(exc)

    return {
        "status": "ok" if db_ok and storage_ok and material_platform_ok else "degraded",
        "phase": "ORDER_CENTER",
        "database": {"ok": db_ok, "error": db_error},
        "storage": {"backend": settings.storage_backend, "ok": storage_ok, "error": storage_error},
        "materialPlatform": {
            "mode": material_platform_mode,
            "ok": material_platform_ok,
            "error": material_platform_error,
        },
    }


# 本地存储回退时，通过后端静态路由暴露文件（订单 ZIP/JSON/Logo）
if settings.storage_backend == "local":
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    app.mount("/storage", StaticFiles(directory=str(root)), name="storage")
    logger.warning("静态文件路由已挂载：/storage → %s", root)
