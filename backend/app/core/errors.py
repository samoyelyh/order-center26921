# ============================================================================
# 错误定义
#
# 每个业务失败都有独立 code + 中文可读消息，
# 前端直接把 message 弹给用户，不做「操作失败」这种统一兜底。
# ============================================================================

from __future__ import annotations

from fastapi import HTTPException, status


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


class DesignPackageNotFound(NotFoundError):
    code = "DESIGN_PACKAGE_NOT_FOUND"
    default_message = "设计包不存在"


class UploadNotFound(NotFoundError):
    code = "UPLOAD_NOT_FOUND"
    default_message = "上传记录不存在"


class UploadSessionNotFound(NotFoundError):
    code = "UPLOAD_SESSION_NOT_FOUND"
    default_message = "上传会话不存在或已失效"


class MaterialNotFound(NotFoundError):
    code = "MATERIAL_NOT_FOUND"
    default_message = "主素材不存在"


class MatchNotFound(NotFoundError):
    code = "MATCH_NOT_FOUND"
    default_message = "匹配记录不存在，请先执行整包自动匹配"


class PairingNotFound(NotFoundError):
    code = "PAIRING_NOT_FOUND"
    default_message = "配对记录不存在，请先执行同名配对"


class BatchNotFound(NotFoundError):
    code = "BATCH_NOT_FOUND"
    default_message = "上架版本不存在"


class VariantNotFound(NotFoundError):
    code = "VARIANT_NOT_FOUND"
    default_message = "副素材不存在"


class AssetNotFound(NotFoundError):
    code = "ASSET_NOT_FOUND"
    default_message = "文件资产不存在"


# ---------------------------------------------------------------- 409 冲突


class ConflictError(AppError):
    code = "CONFLICT"
    http_status = status.HTTP_409_CONFLICT
    default_message = "数据冲突"


class PositionConflict(ConflictError):
    code = "POSITION_CONFLICT"
    default_message = "该设计包位置已存在，不能重复创建"


class MaterialCodeConflict(ConflictError):
    code = "MATERIAL_CODE_CONFLICT"
    default_message = "主素材编码冲突，请重试"


class MatchConflict(ConflictError):
    code = "VARIANT_ALREADY_ASSIGNED"
    default_message = "该副图已被其他主素材占用"


class MatchStateConflict(ConflictError):
    code = "MATCH_STATE_CONFLICT"
    default_message = "当前匹配状态不允许该操作"


class BatchAlreadyExists(ConflictError):
    code = "BATCH_ALREADY_EXISTS"
    default_message = "该版本已生成，请勿重复建版"


class BlockingAnomalyError(ConflictError):
    code = "BLOCKING_ANOMALY"
    default_message = "存在未解决的阻断异常，无法生成版本"


class MaterialCountMismatchError(ConflictError):
    code = "MATERIAL_COUNT_MISMATCH"
    default_message = "主素材数与副图数不一致，无法生成版本"


class ArchivedPackageError(ConflictError):
    code = "DESIGN_PACKAGE_ARCHIVED"
    default_message = "设计包已归档，不能继续上传"


# ---------------------------------------------------------------- 400 校验


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_message = "参数校验失败"


class DuplicatePairKey(ValidationError):
    code = "DUPLICATE_PAIR_KEY"
    default_message = "同一次上传里出现了重复的同名配对键"


class PairingConflict(ConflictError):
    code = "VARIANT_ALREADY_PAIRED"
    default_message = "该副图已经配对给其他主素材"


class PairingIncomplete(ConflictError):
    code = "PAIRING_INCOMPLETE"
    default_message = "还有主素材没有配对副图，无法生成版本"


class UnsupportedFileType(ValidationError):
    code = "UNSUPPORTED_FILE_TYPE"
    default_message = "文件格式不支持"


class FileTooLarge(ValidationError):
    code = "FILE_TOO_LARGE"
    default_message = "文件超过大小限制"


class EmptyFileError(ValidationError):
    code = "EMPTY_FILE"
    default_message = "文件内容为空"


class UploadSessionStateError(ConflictError):
    code = "UPLOAD_SESSION_INVALID"
    default_message = "上传会话状态不允许该操作"


class AssetKindMismatch(ValidationError):
    code = "ASSET_KIND_MISMATCH"
    default_message = "文件类型与用途不匹配"


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
    default_message = "文件存储写入失败（MinIO）"


class StorageReadError(ServerError):
    code = "STORAGE_READ_FAILED"
    default_message = "文件内容读取失败"


class MaterialCreateError(ServerError):
    code = "MATERIAL_CREATE_FAILED"
    default_message = "主素材创建失败"


class PsdRevisionError(ServerError):
    code = "PSD_REVISION_FAILED"
    default_message = "PSD 关联失败"
