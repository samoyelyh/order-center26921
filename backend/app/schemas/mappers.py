# ============================================================================
# 领域实体 → API DTO 映射
#
# 这是「SQLAlchemy 对象绝不直接 JSON 给 React」的落点。
# ============================================================================

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ActivityLog,
    Asset,
    DesignPackage,
    DesignPackageMaterial,
    Material,
    MaterialPsdRevision,
    PackageUpload,
    UploadSession,
)
from app.schemas.dto import (
    ActivityLogDTO,
    AssetDTO,
    AssetRefDTO,
    AssetUploadLinkDTO,
    CountCheckDTO,
    CoverageDTO,
    DesignPackageDTO,
    DesignPackageMaterialDTO,
    MaterialDTO,
    MaterialPairingDTO,
    MaterialPsdRevisionDTO,
    MaterialVariantDTO,
    PackageUploadDTO,
    PairingOptionDTO,
    UploadedFileDTO,
    UploadSessionDTO,
)
from app.services.fingerprint import phash_to_hex


def asset_content_url(asset_id: str) -> str:
    """
    浏览器可访问的图片地址：走后端代理，而不是把 storage_key 拼成 URL。

    第7条：
      - 本地回退（STORAGE_BACKEND=local）与 MinIO 都能用同一个地址
      - 不需要给 MinIO 桶开公共读，也不会把内网 endpoint 暴露到浏览器
      - 前端负责把它拼到 API 主机上（相对路径，局域网访问同样正确）
    """
    return f"/api/assets/{asset_id}/content"


def asset_uri(asset: Asset) -> str:
    return asset_content_url(asset.id)


def to_uploaded_file_dto(link, asset: Asset | None) -> UploadedFileDTO:
    return UploadedFileDTO(
        assetId=link.asset_id,
        fileRole=link.file_role,
        originalFilename=link.original_filename or (asset.original_filename if asset else ""),
        previewUrl=asset_content_url(link.asset_id),
        mimeType=asset.mime_type if asset else "application/octet-stream",
        sizeBytes=asset.size_bytes if asset else 0,
        width=asset.width if asset else None,
        height=asset.height if asset else None,
        positionHint=link.position_hint,
        pairKey=getattr(link, "pair_key", "") or "",
        packageUploadId=link.package_upload_id,
        createdAt=link.created_at,
    )


def to_asset_dto(asset: Asset) -> AssetDTO:
    return AssetDTO(
        id=asset.id,
        storageKey=asset.storage_key,
        originalFilename=asset.original_filename,
        mimeType=asset.mime_type,
        sizeBytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        blake3=asset.blake3,
        phash=phash_to_hex(asset.phash),
        phashVersion=asset.phash_version,
        createdBy=asset.created_by,
        createdAt=asset.created_at,
        deletedAt=asset.deleted_at,
        uri=asset_uri(asset),
    )


def to_asset_ref_dto(asset: Asset | None) -> AssetRefDTO | None:
    if asset is None:
        return None
    return AssetRefDTO(
        assetId=asset.id,
        uri=asset_uri(asset),
        fileName=asset.original_filename,
        mimeType=asset.mime_type,
        sizeBytes=asset.size_bytes,
        createdAt=asset.created_at,
    )


def to_upload_dto(upload: PackageUpload) -> PackageUploadDTO:
    return PackageUploadDTO.from_entity(upload)


def to_session_dto(session: UploadSession) -> UploadSessionDTO:
    return UploadSessionDTO.from_entity(session)


def to_package_dto(db: Session, pkg: DesignPackage) -> DesignPackageDTO:
    main_count = db.execute(
        select(func.count(DesignPackageMaterial.id)).where(
            DesignPackageMaterial.design_package_id == pkg.id
        )
    ).scalar_one()
    upload_count = db.execute(
        select(func.count(PackageUpload.id)).where(PackageUpload.design_package_id == pkg.id)
    ).scalar_one()
    return DesignPackageDTO.from_entity(
        pkg,
        main_material_count=int(main_count or 0),
        upload_count=int(upload_count or 0),
    )


def _to_psd_revision_dto(revision: MaterialPsdRevision) -> MaterialPsdRevisionDTO:
    return MaterialPsdRevisionDTO.from_entity(revision)


def to_material_dto(
    material: Material,
    *,
    preview_asset: Asset | None = None,
    psd_asset: Asset | None = None,
    psd_revisions: list[MaterialPsdRevision] | None = None,
    design_count: int = 0,
    reused: bool = False,
) -> MaterialDTO:
    return MaterialDTO.from_entity(
        material,
        preview_asset=to_asset_ref_dto(preview_asset),
        psd_asset=to_asset_ref_dto(psd_asset),
        psd_revisions=[_to_psd_revision_dto(r) for r in (psd_revisions or [])],
        design_count=design_count,
        reused=reused,
    )


def to_position_dto(
    position: DesignPackageMaterial,
    *,
    material: Material | None = None,
    material_dto: MaterialDTO | None = None,
    preview_uri: str | None = None,
    psd_uri: str | None = None,
) -> DesignPackageMaterialDTO:
    del material  # 位置 DTO 只带 material_dto
    return DesignPackageMaterialDTO.from_entity(
        position,
        material=material_dto,
        preview_uri=preview_uri,
        psd_uri=psd_uri,
    )


def to_activity_log_dto(log: ActivityLog) -> ActivityLogDTO:
    return ActivityLogDTO.from_entity(log)


# ---------------------------------------------------------------- 数量核对


def build_count_check(
    main_count: int,
    variant_upload_count: int,
    *,
    variant_count: int = 0,
    has_pairings: bool = False,
) -> CountCheckDTO:
    """
    数量核对：比较 **主素材数 vs 本次上传的副图张数**。

      一致  → 允许继续配对、确认、生成版本
      不一致 → 记 MATERIAL_COUNT_MISMATCH，**禁止生成版本**（不是禁止上传）

    注意：这里从不输出「副素材 0 张」这类会让用户以为文件丢了的文案。
    """
    diff = variant_upload_count - main_count
    messages: list[str] = []

    if main_count == 0:
        messages.append("尚未建立主素材。")
    elif variant_upload_count == 0:
        messages.append(f"已建立主素材 {main_count} 个；本次没有上传副图。")
    elif diff == 0:
        messages.append(f"主素材 {main_count} 个、副图 {variant_upload_count} 张，数量一致。")
    elif diff < 0:
        messages.append(
            f"主素材 {main_count} 个，副图只有 {variant_upload_count} 张，缺少 {abs(diff)} 张副图。"
        )
    else:
        messages.append(
            f"主素材 {main_count} 个，副图 {variant_upload_count} 张，多了 {diff} 张副图。"
        )

    if has_pairings:
        messages.append("已按同名 pairKey 完成配对，可在下表确认或修改。")

    return CountCheckDTO(
        mainCount=main_count,
        variantUploadCount=variant_upload_count,
        variantCount=variant_count,
        diff=diff,
        messages=messages,
        blocked=main_count > 0 and diff != 0,
    )


def build_coverage(
    version_code: str,
    *,
    batch_id: str = "",
    covered: int,
    total: int,
    missing_codes: list[str] | None = None,
) -> CoverageDTO:
    """当前版本的副素材覆盖度。"""
    return CoverageDTO(
        versionCode=version_code,
        batchId=batch_id,
        covered=covered,
        total=total,
        missingCodes=missing_codes if missing_codes is not None else [],
    )
