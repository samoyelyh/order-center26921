# ============================================================================
# API DTO
#
# 原则（第十七条）：领域实体 → DTO → 前端页面。
# 绝不把 SQLAlchemy 对象直接序列化给 React。
#
# DTO 字段名刻意与前端 src/types/material-workflow.ts 对齐，
# 前端 store 可以把响应直接 hydrate 进现有结构，不需要理解数据库。
# ============================================================================

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- 设计包


class DesignPackageCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # 设计编码：用户/美工自己的设计编号，上传时手填。
    # 同一设计可以有多个设计包（新一版），所以不要求全局唯一。
    designCode: str = Field(min_length=1, max_length=64)
    categoryCode: str = Field(default="UNKNOWN", min_length=1, max_length=64)
    categoryName: str = Field(default="未知品类", min_length=1, max_length=128)
    # 设计包标签：创建时继承给包内主素材（建版时再继承给副素材），之后各自独立修改
    tags: list[str] = Field(default_factory=list)
    # 负责人：业务上负责这套设计的人（目前允许只填姓名）
    responsibleId: str | None = Field(default=None, max_length=64)
    responsibleName: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=2000)
    createdBy: str | None = Field(default=None, max_length=64)
    # 设计美工：设计包的长期业务属性（与「实际上传人」分开）
    designerId: str | None = Field(default=None, max_length=64)
    designerName: str | None = Field(default=None, max_length=128)


class DesignPackagePatchRequest(BaseModel):
    """设计包可变属性。设计美工 / 负责人 / 标签允许随时手工修正（没有 userId 也能存）。"""

    designCode: str | None = Field(default=None, min_length=1, max_length=64)
    categoryCode: str | None = Field(default=None, min_length=1, max_length=64)
    categoryName: str | None = Field(default=None, min_length=1, max_length=128)
    # 负责人：谁业务负责这套设计（会写 CHANGE_RESPONSIBLE 维护记录）
    responsibleId: str | None = Field(default=None, max_length=64)
    responsibleName: str | None = Field(default=None, min_length=1, max_length=128)
    # 标签：整组替换（写 REPLACE_TAG 维护记录）；
    # syncTagsToMaterials=true 时把「这次新增的标签」同步给包内全部主素材（副素材不碰）
    tags: list[str] | None = Field(default=None)
    syncTagsToMaterials: bool = Field(default=False)
    remark: str | None = Field(default=None, max_length=2000)
    designerId: str | None = Field(default=None, max_length=64)
    designerName: str | None = Field(default=None, max_length=128)


class DesignPackageDTO(BaseModel):
    id: str
    code: str
    name: str
    # 设计编码（用户填写）；素材的「关联设计」按它聚合
    designCode: str = ""
    categoryCode: str = "UNKNOWN"
    categoryName: str = "未知品类"
    # 设计包标签（创建时继承给包内素材，之后独立修改）
    tags: list[str] = Field(default_factory=list)
    # 负责人：业务上负责这套设计的人（与实际上传人 uploader 分开）
    responsibleId: str | None = None
    responsibleName: str = ""
    remark: str | None = None
    designerId: str | None = None
    designerName: str = ""
    createdBy: str
    createdAt: datetime
    updatedAt: datetime
    archivedAt: datetime | None = None

    # ---- 聚合字段（DTO，不在 design_packages 表上） ----
    mainMaterialCount: int = 0
    uploadCount: int = 0

    @classmethod
    def from_entity(
        cls, pkg, *, main_material_count: int = 0, upload_count: int = 0
    ) -> "DesignPackageDTO":
        return cls(
            id=pkg.id,
            code=pkg.code,
            name=pkg.name,
            designCode=getattr(pkg, "design_code", "") or pkg.code,
            categoryCode=getattr(pkg, "category_code", "UNKNOWN") or "UNKNOWN",
            categoryName=getattr(pkg, "category_name", "未知品类") or "未知品类",
            tags=list(pkg.tags or []),
            responsibleId=pkg.responsible_id,
            responsibleName=pkg.responsible_name or pkg.designer_name,
            remark=pkg.remark,
            designerId=pkg.designer_id,
            designerName=pkg.designer_name,
            createdBy=pkg.created_by,
            createdAt=pkg.created_at,
            updatedAt=pkg.updated_at,
            archivedAt=pkg.archived_at,
            mainMaterialCount=main_material_count,
            uploadCount=upload_count,
        )


# ---------------------------------------------------------------- 上传行为


class PackageUploadDTO(BaseModel):
    id: str
    designPackageId: str
    originalPackageName: str
    fileSize: int | None = None
    uploadSessionId: str
    # 实际上传人：未接登录系统时只有姓名，uploaderId 可为空
    uploaderId: str | None = None
    uploaderName: str
    uploadType: Literal["INITIAL", "NEW_BATCH", "SUPPLEMENT", "REVISION"]
    targetBatchId: str | None = None
    status: str
    remark: str | None = None
    createdAt: datetime
    updatedAt: datetime

    @classmethod
    def from_entity(cls, upload) -> "PackageUploadDTO":
        return cls(
            id=upload.id,
            designPackageId=upload.design_package_id,
            originalPackageName=upload.original_package_name,
            fileSize=upload.file_size,
            uploadSessionId=upload.upload_session_id,
            uploaderId=upload.uploader_id,
            uploaderName=upload.uploader_name,
            uploadType=upload.upload_type,
            targetBatchId=upload.target_batch_id,
            status=upload.status,
            remark=upload.remark,
            createdAt=upload.created_at,
            updatedAt=upload.updated_at,
        )


class CreateUploadRequest(BaseModel):
    """创建上传记录。

    uploadSessionId 作为幂等键；不传则后端生成。
    """

    originalPackageName: str = Field(min_length=1, max_length=512)
    fileSize: int | None = Field(default=None, ge=0)
    uploadSessionId: str | None = Field(default=None, max_length=64)
    uploaderId: str | None = Field(default=None, max_length=64)
    uploaderName: str | None = Field(default=None, max_length=128)
    uploadType: Literal["INITIAL", "NEW_BATCH", "SUPPLEMENT", "REVISION"] = "INITIAL"
    targetBatchId: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=2000)
    operatorId: str | None = Field(default=None, max_length=64)
    operatorName: str | None = Field(default=None, max_length=128)


# ---------------------------------------------------------------- 上传会话


class UploadSessionDTO(BaseModel):
    id: str
    designPackageId: str
    packageUploadId: str | None = None
    originalPackageName: str
    fileSize: int
    stage: str
    uploadType: str
    operatorId: str | None = None
    operatorName: str | None = None
    remark: str | None = None
    receivedFiles: list[str] = Field(default_factory=list)
    createdAt: datetime
    updatedAt: datetime

    @classmethod
    def from_entity(cls, session) -> "UploadSessionDTO":
        return cls(
            id=session.id,
            designPackageId=session.design_package_id,
            packageUploadId=session.package_upload_id,
            originalPackageName=session.original_package_name,
            fileSize=session.file_size or 0,
            stage=session.stage,
            uploadType=session.upload_type,
            operatorId=session.operator_id,
            operatorName=session.operator_name,
            remark=session.remark,
            receivedFiles=list(session.received_files or []),
            createdAt=session.created_at,
            updatedAt=session.updated_at,
        )


class CreateUploadResponse(BaseModel):
    """POST /design-packages/{id}/uploads 的响应：幂等返回已有记录。"""

    packageUpload: PackageUploadDTO
    session: UploadSessionDTO | None = None
    designPackage: DesignPackageDTO
    created: bool = Field(description="true=本次新建；false=命中幂等键返回已有记录")


class UploadSessionPatchRequest(BaseModel):
    operatorId: str | None = Field(default=None, max_length=64)
    operatorName: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=2000)
    stage: Literal["PARSING", "PARSED", "MATCHED", "CONFIRMED", "SUBMITTED", "INTERRUPTED", "FAILED"] | None = None


class PackageUploadPatchRequest(BaseModel):
    """上传后仍允许修正美工 / 归属运营（都支持手工自由填写）。"""

    uploaderName: str | None = Field(default=None, max_length=128)
    operatorId: str | None = Field(default=None, max_length=64)
    operatorName: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- Asset


class AssetRefDTO(BaseModel):
    """前端 UI 直接消费的轻量资源引用（assetId → Asset 展开）。"""

    assetId: str
    uri: str
    fileName: str
    mimeType: str | None = None
    sizeBytes: int | None = None
    createdAt: datetime | None = None


class AssetDTO(BaseModel):
    id: str
    storageKey: str
    originalFilename: str
    mimeType: str
    sizeBytes: int
    width: int | None = None
    height: int | None = None
    blake3: str | None = None
    phash: str | None = Field(default=None, description="16 位十六进制（8 字节）")
    phashVersion: int | None = None
    createdBy: str
    createdAt: datetime
    deletedAt: datetime | None = None
    uri: str = Field(default="", description="可直接访问的 URL（后端代理地址）")


class AssetUploadLinkDTO(BaseModel):
    """Asset ↔ 上传行为的关联（package_upload_assets 的一行）。

    一个 Asset 可以被多次上传复用，所以「这个文件属于哪次上传」是**多对多**，
    只能通过这张关联表回答，不能靠 Asset 上的单个字段。
    """

    id: str
    packageUploadId: str
    assetId: str
    fileRole: Literal["MAIN_PREVIEW", "PSD", "VARIANT", "OTHER"]
    originalFilename: str
    positionHint: int | None = None
    createdAt: datetime

    @classmethod
    def from_entity(cls, link) -> "AssetUploadLinkDTO":
        return cls(
            id=link.id,
            packageUploadId=link.package_upload_id,
            assetId=link.asset_id,
            fileRole=link.file_role,
            originalFilename=link.original_filename,
            positionHint=link.position_hint,
            createdAt=link.created_at,
        )


class UploadedFileDTO(BaseModel):
    """整包视图「本次上传结果」里的一个文件。

    previewUrl 由后端生成（/api/assets/{id}/content 代理），
    前端任何情况下都不应拼接 storage_key 当图片地址。
    """

    assetId: str
    fileRole: Literal["MAIN_PREVIEW", "PSD", "VARIANT", "OTHER"]
    originalFilename: str
    previewUrl: str
    mimeType: str
    sizeBytes: int
    width: int | None = None
    height: int | None = None
    # 同名配对键（文件名去扩展名，小写；仅本设计包内有效）
    pairKey: str = ""
    positionHint: int | None = None
    packageUploadId: str | None = None
    createdAt: datetime | None = None


class PackageUploadedFilesDTO(BaseModel):
    """本次上传结果分组。

    第9/10条：页面必须显示「主素材 / PSD / 副图」的真实张数，
    绝不能再出现「副素材 0」这种让用户以为文件丢了的文案。
    """

    main: list[UploadedFileDTO] = Field(default_factory=list)
    psd: list[UploadedFileDTO] = Field(default_factory=list)
    variants: list[UploadedFileDTO] = Field(default_factory=list)
    other: list[UploadedFileDTO] = Field(default_factory=list)

    mainCount: int = 0
    psdCount: int = 0
    variantCount: int = 0
    otherCount: int = 0

    @classmethod
    def build(cls, files: list[UploadedFileDTO]) -> "PackageUploadedFilesDTO":
        main = [f for f in files if f.fileRole == "MAIN_PREVIEW"]
        psd = [f for f in files if f.fileRole == "PSD"]
        variants = [f for f in files if f.fileRole == "VARIANT"]
        other = [f for f in files if f.fileRole == "OTHER"]
        return cls(
            main=main,
            psd=psd,
            variants=variants,
            other=other,
            mainCount=len(main),
            psdCount=len(psd),
            variantCount=len(variants),
            otherCount=len(other),
        )


class UploadFileResponse(BaseModel):
    """POST /uploads/{uploadId}/files 的响应。"""

    assetId: str
    originalFilename: str
    sizeBytes: int
    mimeType: str
    width: int | None = None
    height: int | None = None
    blake3: str | None = Field(
        default=None,
        description="PSD 等不可解码文件为 null（与 phash 同时为空）",
    )
    phash: str | None = None
    phashVersion: int | None = None
    storageKey: str
    uri: str
    reused: bool = Field(description="true=命中相同 BLAKE3，复用了已有 Asset")
    kind: Literal["IMAGE", "DESIGN"]
    fileRole: Literal["MAIN_PREVIEW", "PSD", "VARIANT", "OTHER"] = "OTHER"
    # 同名配对键（文件名去扩展名，小写）；主图/PSD/副图靠它配到一起
    pairKey: str = ""
    linkId: str | None = Field(
        default=None,
        description="package_upload_assets 关联行 id（本次上传对该文件的归属记录）",
    )


# ---------------------------------------------------------------- Material / 位置


class MaterialPsdRevisionDTO(BaseModel):
    id: str
    materialId: str
    revisionNo: int
    assetId: str
    createdBy: str
    createdAt: datetime
    deleted: bool
    note: str | None = None

    @classmethod
    def from_entity(cls, revision) -> "MaterialPsdRevisionDTO":
        return cls(
            id=revision.id,
            materialId=revision.material_id,
            revisionNo=revision.revision_no,
            assetId=revision.asset_id,
            createdBy=revision.created_by,
            createdAt=revision.created_at,
            deleted=revision.deleted,
            note=revision.note,
        )


class MaterialDTO(BaseModel):
    id: str
    materialCode: str
    name: str
    previewAssetId: str
    currentPsdRevisionId: str | None = None
    tags: list[str] = Field(default_factory=list)
    sourcePackageUploadId: str | None = None
    createdBy: str
    createdAt: datetime
    updatedAt: datetime
    archivedAt: datetime | None = None

    # ---- 展开字段（DTO） ----
    previewAsset: AssetRefDTO | None = None
    psdAsset: AssetRefDTO | None = None
    psdRevisions: list[MaterialPsdRevisionDTO] = Field(default_factory=list)
    designCount: int = 0
    reused: bool = Field(default=False, description="true=本次上传复用了已有 MAT")

    @classmethod
    def from_entity(
        cls,
        material,
        *,
        preview_asset: AssetRefDTO | None = None,
        psd_asset: AssetRefDTO | None = None,
        psd_revisions: list[MaterialPsdRevisionDTO] | None = None,
        design_count: int = 0,
        reused: bool = False,
    ) -> "MaterialDTO":
        return cls(
            id=material.id,
            materialCode=material.material_code,
            name=material.name,
            previewAssetId=material.preview_asset_id,
            currentPsdRevisionId=material.current_psd_revision_id,
            tags=list(material.tags or []),
            sourcePackageUploadId=material.source_package_upload_id,
            createdBy=material.created_by,
            createdAt=material.created_at,
            updatedAt=material.updated_at,
            archivedAt=material.archived_at,
            previewAsset=preview_asset,
            psdAsset=psd_asset,
            psdRevisions=psd_revisions or [],
            designCount=design_count,
            reused=reused,
        )


class SubmitMaterialItem(BaseModel):
    """一个设计包位置。position 由前端按上传顺序给出。"""

    position: int = Field(ge=1, le=9999)
    previewAssetId: str = Field(min_length=1, max_length=64)
    psdAssetId: str | None = Field(default=None, max_length=64)
    displayName: str | None = Field(default=None, max_length=255)
    sourceFileName: str | None = Field(default=None, max_length=512)


class SubmitMaterialsRequest(BaseModel):
    # 允许为空：已有设计包「只补传新一版副图」时不需要再提交主素材位置，
    # 这次调用只负责登记文件归属并把上传记录推进到 PARSED。
    materials: list[SubmitMaterialItem] = Field(default_factory=list)
    actor: str | None = Field(default=None, max_length=128)
    linkAssetIds: list[str] = Field(
        default_factory=list,
        description=(
            "本次上传使用到的 Asset（含命中 BLAKE3 而复用的资产）。"
            "后端把这些资产也归集到本次上传，保证整包视图能看到全部文件。"
        ),
    )


class DesignPackageMaterialDTO(BaseModel):
    id: str
    designPackageId: str
    position: int
    materialId: str
    displayName: str | None = None
    sourceFileName: str
    sourceAssetId: str
    createdFromUploadId: str
    createdAt: datetime

    # ---- 展开字段（DTO）：文件信息一律来自 Asset ----
    material: MaterialDTO | None = None
    previewUri: str | None = None
    psdUri: str | None = None

    @classmethod
    def from_entity(
        cls,
        position,
        *,
        material: MaterialDTO | None = None,
        preview_uri: str | None = None,
        psd_uri: str | None = None,
    ) -> "DesignPackageMaterialDTO":
        return cls(
            id=position.id,
            designPackageId=position.design_package_id,
            position=position.position,
            materialId=position.material_id,
            displayName=position.display_name,
            sourceFileName=position.source_file_name,
            sourceAssetId=position.source_asset_id,
            createdFromUploadId=position.created_from_upload_id,
            createdAt=position.created_at,
            material=material,
            previewUri=preview_uri,
            psdUri=psd_uri,
        )


class SubmitMaterialsResponse(BaseModel):
    designPackageId: str
    positions: list[DesignPackageMaterialDTO]
    createdMaterialCount: int
    reusedMaterialCount: int


# ---------------------------------------------------------------- 整包视图（PKG Overview）


class CountCheckDTO(BaseModel):
    """数量核对（第十四、三十一条）。

    mainCount           设计包位置数（主素材数）
    variantUploadCount  本次上传的副图 Asset 张数（来自 package_upload_assets）
    variantCount        已建立的副素材（MaterialVariant）数
    diff                variantUploadCount - mainCount，!= 0 即为 MATERIAL_COUNT_MISMATCH
    blocked             true 时**禁止生成 V1**（不是禁止上传）
    """

    mainCount: int
    variantUploadCount: int = 0
    variantCount: int = 0
    diff: int = 0
    messages: list[str] = Field(default_factory=list)
    blocked: bool = False


# ---------------------------------------------------------------- Phase 2：同名配对 / 版本
#
# 主副素材关系**只由同名 pairKey 决定**：不再有相似度、不再有候选排序、
# 不再有高/中/低匹配分级。


class PairingOptionDTO(BaseModel):
    """「修改配对」弹窗里可选的一张副图（来自本次上传，按文件名排序，无任何打分）。"""

    assetId: str
    originalFilename: str
    pairKey: str
    previewUrl: str
    sizeBytes: int = 0
    width: int | None = None
    height: int | None = None
    # 该副图当前已配对给哪个主素材位置（未配对为 None）
    occupiedByPosition: int | None = None


class MaterialPairingDTO(BaseModel):
    """一个设计包位置在**某一次上传**里的配对结果。"""

    id: str
    designPackageId: str
    packageUploadId: str
    designPackageMaterialId: str
    position: int

    materialId: str
    materialCode: str
    materialName: str

    mainPreviewUrl: str
    mainSourceFileName: str

    # 同名配对键（来自主图文件名；仅本设计包内有效，不是永久身份）
    pairKey: str = ""

    variantAssetId: str | None = None
    variantPreviewUrl: str | None = None
    variantFileName: str | None = None
    variantPairKey: str | None = None

    # 同 pairKey 的 PSD（PSD 与主素材的关联）
    psdAssetId: str | None = None
    psdFileName: str | None = None

    source: Literal["NAME", "MANUAL"] = "NAME"
    status: Literal["PAIRED", "UNPAIRED", "CONFIRMED"] = "UNPAIRED"

    variantId: str | None = None
    confirmedBy: str | None = None
    confirmedAt: datetime | None = None

    # 本次上传的全部副图（供「修改」弹窗直接选择）
    options: list[PairingOptionDTO] = Field(default_factory=list)


class PairingRunResponse(BaseModel):
    """POST /api/uploads/{uploadId}/pair 的响应。"""

    designPackageId: str
    packageUploadId: str
    mainCount: int
    variantUploadCount: int
    pairedCount: int
    unpairedCount: int
    confirmedCount: int
    manualCount: int
    # 本次上传里主图解析出的 pairKey 列表（便于核对与展示）
    pairKeys: list[str] = Field(default_factory=list)
    pairings: list[MaterialPairingDTO] = Field(default_factory=list)
    anomalies: list["DataAnomalyDTO"] = Field(default_factory=list)
    countCheck: CountCheckDTO


class PairingPatchRequest(BaseModel):
    """
    人工修改配对。

    variantAssetId: 目标副图（必须是本次上传的副图）；传空字符串表示取消配对
    pairKey:        允许同时修正同名键（用于修文件命名）
    confirmReassign: 目标副图已配给别的位置时，确认重新分配（原位置退回未配对）
    """

    variantAssetId: str | None = Field(default=None, max_length=64)
    pairKey: str | None = Field(default=None, max_length=255)
    actor: str | None = Field(default=None, max_length=128)
    confirmReassign: bool = False


class PairingConfirmResponse(BaseModel):
    confirmedCount: int
    pairings: list[MaterialPairingDTO] = Field(default_factory=list)
    anomalies: list["DataAnomalyDTO"] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class DerivativeBatchDTO(BaseModel):
    id: str
    designPackageId: str
    versionNo: int
    code: str
    createdFromUploadId: str | None = None
    mainMaterialCountAtCreation: int = 0
    createdBy: str
    createdAt: datetime

    # ---- 展开字段 ----
    variantCount: int = 0
    variants: list["MaterialVariantDTO"] = Field(default_factory=list)
    # 本次建版：新建了几个副素材实体、复用了几个（复用 = 内容与已有副素材完全相同）
    createdVariantCount: int = 0
    reusedVariantCount: int = 0
    reusedNotes: list[str] = Field(default_factory=list)


class MaterialVariantDTO(BaseModel):
    id: str
    materialId: str
    materialCode: str
    designPackageMaterialId: str
    position: int
    batchId: str
    batchCode: str
    displayCode: str

    currentRevisionId: str | None = None
    currentRevisionNo: int | None = None
    assetId: str | None = None
    previewUrl: str | None = None
    originalFilename: str | None = None

    # 该副素材是在更早的版本创建的，本版本通过配对复用它（内容完全相同）
    reusedByBatchCode: str | None = None

    # 副素材标签（建版时从主素材复制，之后独立修改）
    tags: list[str] = Field(default_factory=list)

    duplicateOfVariantId: str | None = None
    duplicateWarning: str | None = None
    deleted: bool = False
    createdAt: datetime


class BatchCreateRequest(BaseModel):
    """确认整包配对并生成下一版（第一次即 V1）。"""

    actor: str | None = Field(default=None, max_length=128)
    uploadId: str | None = Field(
        default=None, max_length=64, description="该版本对应的上传记录，可空（默认最新一次上传）"
    )


# ---------------------------------------------------------------- 标签 / 负责人


class TagPatchRequest(BaseModel):
    """整组替换某个素材的标签（主素材/副素材/设计包通用）。"""

    tags: list[str] = Field(default_factory=list, max_length=32)
    actor: str | None = Field(default=None, max_length=128)


class TagBatchRequest(BaseModel):
    """素材中心多选批量调整标签。op=add/remove/replace。"""

    op: Literal["add", "remove", "replace"]
    tags: list[str] = Field(min_length=1, max_length=32)
    targetType: Literal["MATERIAL", "MATERIAL_VARIANT"]
    targetIds: list[str] = Field(min_length=1, max_length=200)
    actor: str | None = Field(default=None, max_length=128)


class DataAnomalyDTO(BaseModel):
    """页面异常项。blocking=true 的异常未解决时禁止生成版本。"""

    id: str
    code: str
    level: Literal["BLOCKING", "WARNING", "INFO"] = "WARNING"
    message: str
    blocking: bool = False
    position: int | None = None
    pairingId: str | None = None
    resolved: bool = False


class CoverageDTO(BaseModel):
    """当前版本的副素材覆盖度（V1: covered/total）。"""

    versionCode: str
    batchId: str = ""
    covered: int = 0
    total: int = 0
    missingCodes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 整包视图（PKG Overview）


class PackageOverviewResponse(BaseModel):
    """
    上传页整包视图。字段形态与前端 PackageOverview 对齐。

    Phase 2 说明（第三十六条）：刷新后必须能恢复
      「已经上传 / 已经匹配 / 已经确认 / 已经生成 V1」，因此这里一次性返回
      uploadedFiles / countCheck / matches / batch / batches / coverage /
      blockingAnomalies，前端不需要自己拼状态。

    Phase 3 才做：distribution_tasks / ASIN / 订单 URL。
    """

    designPackage: DesignPackageDTO
    upload: PackageUploadDTO | None = None
    uploads: list[PackageUploadDTO] = Field(default_factory=list)
    session: UploadSessionDTO | None = None

    positions: list[DesignPackageMaterialDTO] = Field(default_factory=list)
    materials: list[MaterialDTO] = Field(default_factory=list)
    assets: list[AssetDTO] = Field(default_factory=list)

    # ---- 上传文件（多对多归属 + 文件角色） ----
    # uploadedFiles：整个设计包累计交付过的文件（按 assetId+fileRole 去重）
    # latestUploadedFiles：仅最新一次上传交付的文件（页面「本次上传结果」）
    uploadedFiles: PackageUploadedFilesDTO = Field(default_factory=PackageUploadedFilesDTO)
    latestUploadedFiles: PackageUploadedFilesDTO = Field(default_factory=PackageUploadedFilesDTO)
    uploadAssetLinks: list[AssetUploadLinkDTO] = Field(default_factory=list)

    # ---- Phase 2：同名配对与版本 ----
    # pairings：**最新一次上传**的配对结果（页面表格直接渲染）
    pairings: list[MaterialPairingDTO] = Field(default_factory=list)
    pairingSummary: dict[str, int] = Field(default_factory=dict)
    pairingUploadId: str | None = None
    currentBatch: DerivativeBatchDTO | None = None
    batch: DerivativeBatchDTO | None = None
    batches: list[DerivativeBatchDTO] = Field(default_factory=list)
    variants: list[MaterialVariantDTO] = Field(default_factory=list)

    anomalies: list[DataAnomalyDTO] = Field(default_factory=list)
    blockingAnomalies: list[DataAnomalyDTO] = Field(default_factory=list)
    logs: list["ActivityLogDTO"] = Field(default_factory=list)

    # ---- 聚合字段 ----
    operatorId: str | None = None
    operatorName: str | None = None
    operators: list[dict[str, str]] = Field(default_factory=list)
    mainMaterialCount: int = 0
    variantCount: int = 0
    currentBatchNo: int | None = None
    countCheck: CountCheckDTO
    coverage: CoverageDTO | None = None
    submitted: bool = False
    phase: str = Field(default="PHASE_2", description="当前后端阶段标识")


# ---------------------------------------------------------------- 维护记录


class ActivityLogDTO(BaseModel):
    id: str
    designPackageId: str | None = None
    targetType: str
    targetId: str
    actor: str
    action: str
    summary: str
    before: str | None = None
    after: str | None = None
    createdAt: datetime

    @classmethod
    def from_entity(cls, log) -> "ActivityLogDTO":
        return cls(
            id=log.id,
            designPackageId=log.design_package_id,
            targetType=log.target_type,
            targetId=log.target_id,
            actor=log.actor,
            action=log.action,
            summary=log.summary,
            before=log.before_value,
            after=log.after_value,
            createdAt=log.created_at,
        )


# ---------------------------------------------------------------- 错误


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] | None = None


# PackageOverviewResponse / PairingRunResponse / DerivativeBatchDTO 使用前向引用，
# 定义在其后，这里重建一次。
PackageOverviewResponse.model_rebuild()
PairingRunResponse.model_rebuild()
PairingConfirmResponse.model_rebuild()
DerivativeBatchDTO.model_rebuild()
