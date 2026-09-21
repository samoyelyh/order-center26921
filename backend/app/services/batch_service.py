# ============================================================================
# 上架版本（DerivativeBatch）生成 —— 确认整包配对后创建 V1 / V2 / V3
#
# 一个事务内完成：
#   DerivativeBatch
#   + 每个位置一条 MaterialVariant（除非可以复用已有副素材，见下）
#   + 每个新建 Variant 一条 VariantRevision(revision_no = 1)
#   + 回填 MaterialVariant.current_revision_id
#   + MaterialPairing.variant_id 正式关联
#   + ActivityLog
# 任何一条失败 → 整套回滚。绝不允许「V1 建了但只有 17 个 Variant」。
#
# 复用规则（用户修正后的第八条）：
#   如果本次要配给某位置的副图，与该位置**已有副素材**的当前 Revision 是同一个文件
#   （BLAKE3 相同 → 同一个 Asset），说明这一版这张图没有变化：
#   **不新建副素材实体**，直接复用原来的 MaterialVariant（配对记录 variant_id 指向它，
#   该版本通过配对继续引用同一 Asset）。只有内容确实变了才新建 MaterialVariant + Revision。
#
# 版本号**只能**在这里发：SELECT ... FOR UPDATE 锁住本包最大的 version_no，
# 不允许每个 MaterialVariant 自己算 MAX(version)+1。
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    ArchivedPackageError,
    BatchAlreadyExists,
    DesignPackageNotFound,
    MaterialCountMismatchError,
    PairingIncomplete,
    UploadNotFound,
    ValidationError,
)
from app.db.models import (
    Asset,
    DerivativeBatch,
    DesignPackage,
    DesignPackageMaterial,
    Material,
    MaterialPairing,
    MaterialVariant,
    PackageUpload,
    VariantRevision,
)
from app.schemas.dto import DerivativeBatchDTO, MaterialPairingDTO, MaterialVariantDTO
from app.schemas.mappers import asset_content_url, build_count_check
from app.services.activity import write_log
from app.services.pairing_service import load_pairings
from app.services.repository import new_id, utcnow

BATCH_CODE_PREFIX = "V"


@dataclass
class BatchCreateResult:
    batch: DerivativeBatch
    variants: list[MaterialVariant] = field(default_factory=list)
    revision_count: int = 0
    created_count: int = 0
    reused_count: int = 0
    reused_notes: list[str] = field(default_factory=list)


def next_version_no(db: Session, design_package_id: str) -> int:
    """
    并发安全地取下一个版本号：锁住本包当前最大 version_no 那一行（FOR UPDATE），
    两个并发的建版请求会串行化，不会同时拿到 V2。
    """
    row = db.execute(
        select(DerivativeBatch)
        .where(DerivativeBatch.design_package_id == design_package_id)
        .order_by(DerivativeBatch.version_no.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        # 还没有任何版本：用设计包行本身当锁，避免两个请求同时创建 V1
        db.execute(
            select(DesignPackage.id).where(DesignPackage.id == design_package_id).with_for_update()
        ).scalar_one_or_none()
        return 1
    return row.version_no + 1


def _resolve_upload(db: Session, design_package_id: str, upload_id: str | None) -> PackageUpload:
    if upload_id:
        upload = db.get(PackageUpload, upload_id)
        if upload is None:
            raise UploadNotFound(f"上传记录不存在：{upload_id}")
        if upload.design_package_id != design_package_id:
            raise ValidationError("该上传记录不属于这个设计包")
        return upload
    upload = db.execute(
        select(PackageUpload)
        .where(PackageUpload.design_package_id == design_package_id)
        .order_by(PackageUpload.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if upload is None:
        raise ValidationError("该设计包还没有任何上传记录")
    return upload


def validate_ready_for_batch(
    db: Session, design_package_id: str, upload_id: str | None
) -> tuple[PackageUpload, list[MaterialPairingDTO]]:
    """
    生成版本前的校验：
      1. 这次上传已经跑过配对
      2. 主素材数量与本次上传副图数量一致
      3. 每个位置都已确认配对且指向一张副图
      4. 每张副图只属于一个位置（DB 唯一键 + 这里再确认一次）
    """
    upload = _resolve_upload(db, design_package_id, upload_id)
    inputs, _rows, dtos, _summary = load_pairings(db, upload.id)

    # 先报最根本的问题：数量对不上时，说什么都白搭
    count_check = build_count_check(len(inputs.positions), len(inputs.variants))
    if count_check.blocked:
        raise MaterialCountMismatchError(
            f"主素材 {count_check.mainCount} 个、副图 {count_check.variantUploadCount} 张，"
            f"数量不一致，不允许生成版本"
        )

    if not dtos:
        raise PairingIncomplete("这次上传还没有做同名配对，请先点「开始配对」")

    unconfirmed = [dto for dto in dtos if dto.status != "CONFIRMED"]
    if unconfirmed:
        raise PairingIncomplete(
            "还有位置没有确认配对："
            + "、".join(str(dto.position) for dto in unconfirmed[:10])
            + "。请确认后再生成版本"
        )

    missing = [dto for dto in dtos if not dto.variantAssetId]
    if missing:
        raise PairingIncomplete(
            "还有位置没有副图：" + "、".join(str(dto.position) for dto in missing[:10])
        )

    used: dict[str, int] = {}
    for dto in dtos:
        if dto.variantAssetId in used:
            raise PairingIncomplete(
                f"副图 {dto.variantFileName or dto.variantAssetId} 同时被位置 "
                f"{used[dto.variantAssetId]} 和位置 {dto.position} 使用，请重新配对"
            )
        used[dto.variantAssetId] = dto.position

    return upload, dtos


def create_batch(
    db: Session,
    design_package_id: str,
    *,
    actor: str,
    upload_id: str | None = None,
) -> BatchCreateResult:
    """
    确认整包配对并生成下一版（第一次 = V1）。整个函数在调用方的事务里，失败整体回滚。
    """
    package = db.get(DesignPackage, design_package_id)
    if package is None:
        raise DesignPackageNotFound()
    if package.archived_at is not None:
        # 已删除（归档）的设计包不能再生成新版本
        raise ArchivedPackageError()

    upload, dtos = validate_ready_for_batch(db, design_package_id, upload_id)

    version_no = next_version_no(db, design_package_id)
    batch = DerivativeBatch(
        id=new_id("batch"),
        design_package_id=design_package_id,
        version_no=version_no,
        code=f"{BATCH_CODE_PREFIX}{version_no}",
        created_from_upload_id=upload.id,
        main_material_count_at_creation=len(dtos),
        created_by=actor,
    )
    db.add(batch)
    db.flush()

    variants: list[MaterialVariant] = []
    created_count = 0
    reused_count = 0
    reused_notes: list[str] = []
    revision_count = 0

    # 该位置已有的副素材（含当前 Revision 的 Asset），用于「内容没变就复用」
    existing_by_position = _existing_variants_by_position(
        db, [dto.designPackageMaterialId for dto in dtos]
    )

    for dto in dtos:
        asset = db.get(Asset, dto.variantAssetId or "")
        if asset is None:
            raise PairingIncomplete(f"位置 {dto.position} 的副图文件已不存在，无法生成版本")

        position_row = db.get(DesignPackageMaterial, dto.designPackageMaterialId)
        if position_row is None:
            raise PairingIncomplete(f"设计包位置 {dto.position} 已不存在")

        reusable = _find_reusable_variant(db, existing_by_position.get(position_row.id, []), asset)

        if reusable is not None:
            # 内容与已有副素材完全相同：不新建实体，本版本直接引用它
            variant = reusable
            reused_count += 1
            reused_notes.append(
                f"位置 {position_row.position} 的副图 {asset.original_filename} 与已有副素材 "
                f"{variant.display_code} 完全相同，{batch.code} 继续引用它（不新建副素材）"
            )
            write_log(
                db,
                design_package_id=design_package_id,
                target_type="MATERIAL_VARIANT",
                target_id=variant.id,
                actor=actor,
                action="REUSE_VARIANT",
                summary=(
                    f"{batch.code} 位置 {position_row.position} 复用已有副素材 "
                    f"{variant.display_code}（{asset.original_filename}）"
                ),
                after_value=batch.code,
            )
        else:
            # 建版时从所属主素材复制标签（创建时继承），之后副素材标签独立修改
            material_row = db.get(Material, dto.materialId)
            variant = MaterialVariant(
                id=new_id("variant"),
                material_id=dto.materialId,
                design_package_material_id=position_row.id,
                batch_id=batch.id,
                display_code=f"{position_row.position}-{version_no}",
                tags=list(material_row.tags or []) if material_row is not None else [],
                deleted=False,
            )
            db.add(variant)
            db.flush()

            revision = VariantRevision(
                id=new_id("vrevision"),
                variant_id=variant.id,
                revision_no=1,
                asset_id=asset.id,
                created_by=actor,
                deleted=False,
                note=None,
            )
            db.add(revision)
            db.flush()
            variant.current_revision_id = revision.id
            revision_count += 1
            created_count += 1
            write_log(
                db,
                design_package_id=design_package_id,
                target_type="MATERIAL_VARIANT",
                target_id=variant.id,
                actor=actor,
                action="CREATE_VARIANT",
                summary=(
                    f"{batch.code} 位置 {position_row.position} → {dto.materialCode} "
                    f"→ 副素材 {variant.display_code}（{asset.original_filename}，Revision 1）"
                ),
                after_value=variant.display_code,
            )

        pairing_row = db.get(MaterialPairing, dto.id)
        if pairing_row is not None:
            pairing_row.variant_id = variant.id
            pairing_row.updated_at = utcnow()

        variants.append(variant)
        existing_by_position.setdefault(position_row.id, []).append(variant)

    write_log(
        db,
        design_package_id=design_package_id,
        target_type="DERIVATIVE_BATCH",
        target_id=batch.id,
        actor=actor,
        action="CREATE_BATCH",
        summary=(
            f"确认整包并生成 {batch.code}：新建副素材 {created_count} 个、"
            f"复用已有副素材 {reused_count} 个、Revision {revision_count} 个"
        ),
        after_value=batch.code,
    )

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        message = str(getattr(exc, "orig", exc))
        if "uq_variants_batch_position" in message:
            raise BatchAlreadyExists("同一版本内该位置已存在副素材，请勿重复建版") from exc
        if "uq_batches_pkg_version" in message:
            raise BatchAlreadyExists() from exc
        raise PairingIncomplete(f"生成版本失败：{message}") from exc

    return BatchCreateResult(
        batch=batch,
        variants=variants,
        revision_count=revision_count,
        created_count=created_count,
        reused_count=reused_count,
        reused_notes=reused_notes,
    )


def _existing_variants_by_position(
    db: Session, position_ids: list[str]
) -> dict[str, list[MaterialVariant]]:
    if not position_ids:
        return {}
    rows = db.execute(
        select(MaterialVariant)
        .where(
            MaterialVariant.design_package_material_id.in_(position_ids),
            MaterialVariant.deleted.is_(False),
        )
        .order_by(MaterialVariant.created_at.asc())
    ).scalars()
    grouped: dict[str, list[MaterialVariant]] = {}
    for variant in rows:
        grouped.setdefault(variant.design_package_material_id, []).append(variant)
    return grouped


def _find_reusable_variant(
    db: Session, candidates: list[MaterialVariant], asset: Asset
) -> MaterialVariant | None:
    """
    只有「同一个文件」才复用：Asset id 相同，或 BLAKE3 相同（同一个物理文件）。
    BLAKE3 为空（PSD 之类的不可解码文件）时退化为 Asset id 比较。
    """
    for variant in candidates:
        if not variant.current_revision_id:
            continue
        revision = db.get(VariantRevision, variant.current_revision_id)
        if revision is None or revision.deleted:
            continue
        if revision.asset_id == asset.id:
            return variant
        if asset.blake3:
            current_asset = db.get(Asset, revision.asset_id)
            if current_asset is not None and current_asset.blake3 == asset.blake3:
                return variant
    return None


# ---------------------------------------------------------------- 查询 / DTO


def to_variant_dto(
    variant: MaterialVariant,
    *,
    material_code: str,
    position: int,
    batch_code: str,
    revision: VariantRevision | None,
    asset: Asset | None,
    reused_by_batch_code: str | None = None,
) -> MaterialVariantDTO:
    return MaterialVariantDTO(
        id=variant.id,
        materialId=variant.material_id,
        materialCode=material_code,
        designPackageMaterialId=variant.design_package_material_id,
        position=position,
        batchId=variant.batch_id,
        batchCode=batch_code,
        displayCode=variant.display_code,
        currentRevisionId=variant.current_revision_id,
        currentRevisionNo=revision.revision_no if revision else None,
        assetId=asset.id if asset else None,
        previewUrl=asset_content_url(asset.id) if asset else None,
        originalFilename=asset.original_filename if asset else None,
        reusedByBatchCode=reused_by_batch_code,
        tags=list(variant.tags or []),
        deleted=variant.deleted,
        createdAt=variant.created_at,
    )


def list_batch_variants(db: Session, batch: DerivativeBatch) -> list[MaterialVariantDTO]:
    """
    一个版本实际使用的副素材 = 本版本新建的 + 通过配对复用来的。

    复用来的副素材行属于更早的版本（MaterialVariant.batch_id 不变，也**不会**改），
    这里一起返回并标上 reusedByBatchCode，页面才能看到本版本完整的一整套。
    """
    created = list(
        db.execute(select(MaterialVariant).where(MaterialVariant.batch_id == batch.id)).scalars()
    )
    reused: list[MaterialVariant] = []
    if batch.created_from_upload_id:
        reused = list(
            db.execute(
                select(MaterialVariant)
                .join(MaterialPairing, MaterialPairing.variant_id == MaterialVariant.id)
                .where(
                    MaterialPairing.package_upload_id == batch.created_from_upload_id,
                    MaterialVariant.batch_id != batch.id,
                )
            ).scalars()
        )

    created_ids = {variant.id for variant in created}
    seen: set[str] = set()
    merged: list[MaterialVariant] = []
    for variant in [*created, *reused]:
        if variant.id in seen:
            continue
        seen.add(variant.id)
        merged.append(variant)

    position_ids = {v.design_package_material_id for v in merged}
    material_ids = {v.material_id for v in merged}
    positions = {
        row.id: row
        for row in db.execute(
            select(DesignPackageMaterial).where(DesignPackageMaterial.id.in_(position_ids))
        ).scalars()
    } if position_ids else {}
    materials = {
        row.id: row
        for row in db.execute(select(Material).where(Material.id.in_(material_ids))).scalars()
    } if material_ids else {}
    revision_ids = {v.current_revision_id for v in merged if v.current_revision_id}
    revisions = {
        row.id: row
        for row in db.execute(
            select(VariantRevision).where(VariantRevision.id.in_(revision_ids))
        ).scalars()
    } if revision_ids else {}
    asset_ids = {r.asset_id for r in revisions.values()}
    assets = {
        row.id: row
        for row in db.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars()
    } if asset_ids else {}

    dtos: list[MaterialVariantDTO] = []
    for variant in merged:
        revision = revisions.get(variant.current_revision_id or "")
        dtos.append(
            to_variant_dto(
                variant,
                material_code=materials[variant.material_id].material_code
                if variant.material_id in materials
                else "",
                position=positions[variant.design_package_material_id].position
                if variant.design_package_material_id in positions
                else 0,
                batch_code=batch.code,
                revision=revision,
                asset=assets.get(revision.asset_id) if revision else None,
                reused_by_batch_code=None if variant.id in created_ids else batch.code,
            )
        )
    dtos.sort(key=lambda dto: (dto.position, dto.displayCode))
    return dtos


def list_package_variants(db: Session, design_package_id: str) -> list[MaterialVariantDTO]:
    """
    本设计包**全部版本**的副素材（素材中心 / 主素材抽屉的「副素材」Tab 用）。

    与 list_batch_variants 的区别：
      * list_batch_variants(batch) 回答「这个版本实际用了哪一整套」（含复用来的，batchCode 记本版本）
      * 这里回答「这个设计包一共产生过哪些副素材实体」，每条副素材带**自己所属**的版本号
        （1-1 属于 V1、1-2 属于 V2），并且如果它被更晚的版本复用，就标上 reusedByBatchCode。

    没有这个区分时，主素材详情只能看到当前版本的副素材，历史版本会凭空消失。
    """
    variants = list(
        db.execute(
            select(MaterialVariant)
            .join(
                DesignPackageMaterial,
                DesignPackageMaterial.id == MaterialVariant.design_package_material_id,
            )
            .where(DesignPackageMaterial.design_package_id == design_package_id)
        ).scalars()
    )
    if not variants:
        return []

    batch_ids = {v.batch_id for v in variants}
    batches = {
        row.id: row
        for row in db.execute(select(DerivativeBatch).where(DerivativeBatch.id.in_(batch_ids))).scalars()
    }

    owner_batch = {v.id: v.batch_id for v in variants}

    # 谁被更晚的版本复用：配对记录里的 variant_id 指向已有副素材，且它属于更早的版本
    reuse_rows = db.execute(
        select(
            MaterialPairing.variant_id,
            DerivativeBatch.id,
            DerivativeBatch.code,
            DerivativeBatch.version_no,
        )
        .join(DerivativeBatch, DerivativeBatch.created_from_upload_id == MaterialPairing.package_upload_id)
        .where(
            MaterialPairing.design_package_id == design_package_id,
            MaterialPairing.variant_id.is_not(None),
        )
    ).all()
    reused_by: dict[str, str] = {}
    reused_version: dict[str, int] = {}
    for variant_id, batch_id, batch_code, version_no in reuse_rows:
        if not variant_id or owner_batch.get(variant_id) == batch_id:
            continue
        previous = reused_version.get(variant_id)
        if previous is None or int(version_no) < previous:
            reused_version[variant_id] = int(version_no)
            reused_by[variant_id] = batch_code

    position_ids = {v.design_package_material_id for v in variants}
    positions = {
        row.id: row
        for row in db.execute(
            select(DesignPackageMaterial).where(DesignPackageMaterial.id.in_(position_ids))
        ).scalars()
    }
    material_ids = {v.material_id for v in variants}
    materials = {
        row.id: row
        for row in db.execute(select(Material).where(Material.id.in_(material_ids))).scalars()
    }
    revision_ids = {v.current_revision_id for v in variants if v.current_revision_id}
    revisions = {
        row.id: row
        for row in db.execute(
            select(VariantRevision).where(VariantRevision.id.in_(revision_ids))
        ).scalars()
    } if revision_ids else {}
    asset_ids = {r.asset_id for r in revisions.values()}
    assets = {
        row.id: row
        for row in db.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars()
    } if asset_ids else {}

    dtos: list[MaterialVariantDTO] = []
    for variant in variants:
        revision = revisions.get(variant.current_revision_id or "")
        own_batch = batches.get(variant.batch_id)
        dtos.append(
            to_variant_dto(
                variant,
                material_code=materials[variant.material_id].material_code
                if variant.material_id in materials
                else "",
                position=positions[variant.design_package_material_id].position
                if variant.design_package_material_id in positions
                else 0,
                batch_code=own_batch.code if own_batch else "",
                revision=revision,
                asset=assets.get(revision.asset_id) if revision else None,
                reused_by_batch_code=reused_by.get(variant.id),
            )
        )
    dtos.sort(key=lambda dto: (dto.position, dto.batchCode, dto.displayCode))
    return dtos


def to_batch_dto(
    batch: DerivativeBatch, variants: list[MaterialVariantDTO] | None = None
) -> DerivativeBatchDTO:
    return DerivativeBatchDTO(
        id=batch.id,
        designPackageId=batch.design_package_id,
        versionNo=batch.version_no,
        code=batch.code,
        createdFromUploadId=batch.created_from_upload_id,
        mainMaterialCountAtCreation=batch.main_material_count_at_creation,
        createdBy=batch.created_by,
        createdAt=batch.created_at,
        variantCount=len(variants or []),
        variants=variants or [],
    )


def list_batches(db: Session, design_package_id: str) -> list[DerivativeBatchDTO]:
    batches = list(
        db.execute(
            select(DerivativeBatch)
            .where(DerivativeBatch.design_package_id == design_package_id)
            .order_by(DerivativeBatch.version_no.asc())
        ).scalars()
    )
    return [to_batch_dto(batch, list_batch_variants(db, batch)) for batch in batches]


def count_variants(db: Session, design_package_id: str) -> int:
    """本设计包真正创建出来的副素材实体数（复用的不重复计）。"""
    return int(
        db.execute(
            select(func.count(MaterialVariant.id))
            .join(
                DesignPackageMaterial,
                DesignPackageMaterial.id == MaterialVariant.design_package_material_id,
            )
            .where(
                DesignPackageMaterial.design_package_id == design_package_id,
                MaterialVariant.deleted.is_(False),
            )
        ).scalar_one()
        or 0
    )


__all__ = [
    "BatchCreateResult",
    "count_variants",
    "create_batch",
    "list_batch_variants",
    "list_batches",
    "next_version_no",
    "to_batch_dto",
    "to_variant_dto",
    "validate_ready_for_batch",
]
