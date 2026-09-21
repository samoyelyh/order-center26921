# ============================================================================
# 错误定义
#
# 每个业务失败都有独立 code + 中文可读消息，
# 前端直接把 message 弹给用户，不做「操作失败」这种统一兜底。
# ============================================================================

from __future__ import annotations

from fastapi import HTTPException, status
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict | None = None


class AppError(HTTPException):
    code = "APP_ERROR"
    http_status = status.HTTP_400_BAD_REQUEST
    default_message = "请求失败"

    def __init__(self, message: str | None = None, *, detail: dict | None = None) -> None:
        payload: dict = {"code": self.code, "message": message or self.default_message}
        if detail:
            payload["detail"] = detail
        super().__init__(status_code=self.http_status, detail=payload)


# ---------------------------------------------------------------- 404


class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = status.HTTP_404_NOT_FOUND
    default_message = "资源不存在"


class OrderNotFound(NotFoundError):
    code = "ORDER_NOT_FOUND"
    default_message = "订单不存在"


class BatchNotFound(NotFoundError):
    code = "BATCH_NOT_FOUND"
    default_message = "导入批次不存在"


class AssetNotFound(NotFoundError):
    code = "ASSET_NOT_FOUND"
    default_message = "文件资产不存在"


# ---------------------------------------------------------------- 409 冲突


class ConflictError(AppError):
    code = "CONFLICT"
    http_status = status.HTTP_409_CONFLICT
    default_message = "数据冲突"


# ---------------------------------------------------------------- 400 校验


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_message = "参数校验失败"


class UnsupportedFileType(ValidationError):
    code = "UNSUPPORTED_FILE_TYPE"
    default_message = "文件格式不支持"


class EmptyFileError(ValidationError):
    code = "EMPTY_FILE"
    default_message = "文件内容为空"


class OrderImportError(ValidationError):
    code = "ORDER_IMPORT_FAILED"
    default_message = "订单 ZIP 导入失败"


# ---------------------------------------------------------------- 500 服务端


class ServerError(AppError):
    code = "SERVER_ERROR"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message = "服务端错误"


class HashComputeError(ServerError):
    code = "HASH_COMPUTE_FAILED"
    default_message = "文件 Hash 计算失败"


class StorageWriteError(ServerError):
    code = "STORAGE_WRITE_FAILED"
    default_message = "文件存储写入失败"


class StorageReadError(ServerError):
    code = "STORAGE_READ_FAILED"
    default_message = "文件内容读取失败"
