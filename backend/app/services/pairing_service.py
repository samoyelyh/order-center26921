# ============================================================================
# 主素材 ↔ 副素材 配对服务（按同名 pairKey，**不使用任何相似度**）
#
# 规则：
#   同一次上传里，主图 / PSD / 副图按**同名 pairKey**直接关联：
#       main/1.jpg   psd/1.psd   variant/1.jpg   →  pair_key = "1"
#   配对只发生在「本次上传」范围内；pairKey 不是永久身份，MAT-xxxxxx 才是。
#
# 异常（必须让用户看到）：
#   MISSING_VARIANT      主素材有 pairKey，但本次上传没有同名副图
#   EXTRA_VARIANT        有副图但找不到同名主素材（多余副图）
#   DUPLICATE_PAIR_KEY   两个位置解析到同一个 pairKey（同一张副图不能给两个主素材）
#   UNRESOLVED_PAIR_KEY  文件名解析不出 pairKey
#
# 人工修改：PATCH 换一张本次上传的副图；一个副图只能属于一个主素材。
# 确认：整包确认（PAIRED → CONFIRMED），有阻断异常时不允许确认/建版。
#
# 配对只使用确定性的文件名关系；不根据图片内容推断关系。
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    ArchivedPackageError,
    DesignPackageNotFound,
    PairingConflict,
    PairingIncomplete,
    PairingNotFound,
    UploadNotFound,
    ValidationError,
)
from app.db.models import (
    Asset,
    DesignPackage,
    DesignPackageMaterial,
    Material,
    MaterialPairing,
    PackageUpload,
    PackageUploadAsset,
)
from app.schemas.dto import (
    DataAnomalyDTO,
    MaterialPairingDTO,
    PairingOptionDTO,
)
from app.schemas.mappers import asset_content_url
from app.services.activity import write_log
from app.services.repository import new_id, utcnow

MAIN_ROLE = "MAIN_PREVIEW"
VARIANT_ROLE = "VARIANT"
PSD_ROLE = "PSD"


@dataclass
class PairingInputs:
    """本次上传的配对输入。"""

    upload: PackageUpload
    package: DesignPackage
    positions: list[DesignPackageMaterial] = field(default_factory=list)
    main_by_asset: dict[str, PackageUploadAsset] = field(default_factory=dict)
    variants_by_key: dict[str, PackageUploadAsset] = field(default_factory=dict)
    psds_by_key: dict[str, PackageUploadAsset] = field(default_factory=dict)
    variants: list[PackageUploadAsset] = field(default_factory=list)
    assets: dict[str, Asset] = field(default_factory=dict)
    materials: dict[str, Material] = field(default_factory=dict)
    # 位置 id → 该位置的 pairKey（优先取本次上传的主图，其次取历史）
    pair_key_by_position: dict[str, str] = field(default_factory=dict)
    # 主图/PSD/副图 关联行里解析不出 pairKey 的
    unresolved: list[PackageUploadAsset] = field(default_factory=list)


@dataclass
class PairingRunResult:
    inputs: PairingInputs
    rows: list[MaterialPairing]
    anomalies: list[DataAnomalyDTO]
    paired_count: int
    unpaired_count: int
    variant_count: int


# ---------------------------------------------------------------- 输入收集


def _links(db: Session, upload_id: str) -> list[PackageUploadAsset]:
    rows = list(
        db.execute(
            select(PackageUploadAsset)
            .where(PackageUploadAsset.package_upload_id == upload_id)
            .order_by(
                PackageUploadAsset.file_role.asc(),
                PackageUploadAsset.pair_key.asc(),
                PackageUploadAsset.created_at.asc(),
            )
        ).scalars()
    )
    return rows


def gather_pairing_inputs(db: Session, upload_id: str) -> PairingInputs:
    upload = db.get(PackageUpload, upload_id)
    if upload is None:
        raise UploadNotFound(f"上传记录不存在：{upload_id}")
    package = db.get(DesignPackage, upload.design_package_id)
    if package is None:
        raise DesignPackageNotFound()
    if package.archived_at is not None:
        # 已删除（归档）的设计包不能再改配对 / 生成版本
        raise ArchivedPackageError()

    positions = list(
        db.execute(
            select(DesignPackageMaterial)
            .where(DesignPackageMaterial.design_package_id == package.id)
            .order_by(DesignPackageMaterial.position.asc())
        ).scalars()
    )

    links = _links(db, upload_id)
    inputs = PairingInputs(upload=upload, package=package, positions=positions)

    asset_ids = {link.asset_id for link in links} | {p.source_asset_id for p in positions}
    if asset_ids:
        inputs.assets = {
            a.id: a
            for a in db.execute(
                select(Asset).where(Asset.id.in_(asset_ids), Asset.deleted_at.is_(None))
            ).scalars()
        }
    material_ids = {p.material_id for p in positions}
    if material_ids:
        inputs.materials = {
            m.id: m
            for m in db.execute(select(Material).where(Material.id.in_(material_ids))).scalars()
        }

    for link in links:
        if link.file_role == MAIN_ROLE:
            inputs.main_by_asset[link.asset_id] = link
        elif link.file_role == VARIANT_ROLE:
            inputs.variants.append(link)
            if link.pair_key:
                inputs.variants_by_key.setdefault(link.pair_key, link)
        elif link.file_role == PSD_ROLE:
            if link.pair_key:
                inputs.psds_by_key.setdefault(link.pair_key, link)
        if link.file_role in (MAIN_ROLE, PSD_ROLE, VARIANT_ROLE) and not link.pair_key:
            inputs.unresolved.append(link)

    # 位置 → pairKey：先看本次上传的主图，再看本设计包历史上该主图的关联行，
    # 最后看该位置已有的配对记录（这样「第二次只传副图」也能按同名配对）
    for position in positions:
        key = ""
        link = inputs.main_by_asset.get(position.source_asset_id)
        if link is not None and link.pair_key:
            key = link.pair_key
        if not key:
            key = _historical_pair_key(db, package.id, position)
        if key:
            inputs.pair_key_by_position[position.id] = key

    return inputs


def _historical_pair_key(db: Session, package_id: str, position: DesignPackageMaterial) -> str:
    """从本设计包历史上传里找出该位置主图的 pairKey（用于「只传副图」的第二次上传）。"""
    row = db.execute(
        select(PackageUploadAsset.pair_key)
        .join(PackageUpload, PackageUpload.id == PackageUploadAsset.package_upload_id)
        .where(
            PackageUpload.design_package_id == package_id,
            PackageUploadAsset.file_role == MAIN_ROLE,
            PackageUploadAsset.asset_id == position.source_asset_id,
            PackageUploadAsset.pair_key != "",
        )
        .order_by(PackageUploadAsset.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    if row:
        return row

    existing = db.execute(
        select(MaterialPairing.pair_key)
        .where(
            MaterialPairing.design_package_material_id == position.id,
            MaterialPairing.pair_key != "",
        )
        .order_by(MaterialPairing.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    return existing or ""


# ---------------------------------------------------------------- 跑一次同名配对


def run_pairing(db: Session, upload_id: str) -> PairingRunResult:
    """
    整包一次配对。已人工修改过（source=MANUAL）或已确认（CONFIRMED）的行不会被覆盖。
    """
    inputs = gather_pairing_inputs(db, upload_id)
    existing = {
        row.design_package_material_id: row
        for row in db.execute(
            select(MaterialPairing).where(MaterialPairing.package_upload_id == upload_id)
        ).scalars()
    }

    anomalies: list[DataAnomalyDTO] = []
    rows: list[MaterialPairing] = []
    used_variant_assets: dict[str, int] = {}
    pair_key_owner: dict[str, int] = {}

    # 第一阶段：先算出每个位置"应该"配哪张副图，并把会被自动改写的行的
    # variant_asset_id 先清空。
    # 为什么要清空后再赋值：UNIQUE(package_upload_id, variant_asset_id) 在
    # 「副图从位置2 挪到位置1」这种交换里，SQLAlchemy 一次 flush 的更新顺序不可控，
    # 中间态会出现两行指向同一张副图而撞唯一键。
    desired: dict[str, str | None] = {}
    for position in inputs.positions:
        row = existing.get(position.id)
        key = inputs.pair_key_by_position.get(position.id, "")
        variant_link = inputs.variants_by_key.get(key) if key else None

        if row is not None and (row.source == "MANUAL" or row.status == "CONFIRMED"):
            desired[position.id] = row.variant_asset_id
            continue

        if not key:
            desired[position.id] = None
        elif key in pair_key_owner:
            first_position = pair_key_owner[key]
            desired[position.id] = None
            anomalies.append(
                DataAnomalyDTO(
                    id=f"anomaly-DUPLICATE_PAIR_KEY-{position.position}",
                    code="DUPLICATE_PAIR_KEY",
                    level="BLOCKING",
                    message=(
                        f"位置 {position.position} 与位置 {first_position} 的 pairKey 都是「{key}」，"
                        f"同一张副图不能配给两个主素材。请重新命名后重传。"
                    ),
                    blocking=True,
                    position=position.position,
                )
            )
        elif variant_link is None:
            desired[position.id] = None
        else:
            desired[position.id] = variant_link.asset_id
            pair_key_owner[key] = position.position

    # 先释放，再赋值：避免唯一键中间态冲突
    for position in inputs.positions:
        row = existing.get(position.id)
        if row is not None and row.variant_asset_id != desired[position.id]:
            row.variant_asset_id = None
    db.flush()

    # 第二阶段：写入 pair_key / PSD / 副图 / 状态
    for position in inputs.positions:
        key = inputs.pair_key_by_position.get(position.id, "")
        psd_link = inputs.psds_by_key.get(key) if key else None
        target = desired[position.id]

        row = existing.get(position.id)
        if row is None:
            row = MaterialPairing(
                id=new_id("pair"),
                design_package_id=inputs.package.id,
                package_upload_id=upload_id,
                design_package_material_id=position.id,
                pair_key=key,
            )
            db.add(row)
            existing[position.id] = row

        row.pair_key = key or row.pair_key
        row.psd_asset_id = psd_link.asset_id if psd_link is not None else None

        if row.source == "MANUAL" or row.status == "CONFIRMED":
            # 人工结论优先，同名配对不覆盖它
            pass
        elif target is None:
            row.variant_asset_id = None
            row.status = "UNPAIRED"
            row.source = "NAME"
        else:
            row.variant_asset_id = target
            row.status = "PAIRED"
            row.source = "NAME"

        if key and row.status != "UNPAIRED":
            pair_key_owner.setdefault(key, position.position)
        if row.variant_asset_id:
            used_variant_assets[row.variant_asset_id] = position.position
        row.updated_at = utcnow()
        rows.append(row)

    db.flush()

    # ---- 异常：缺副图 / 多余副图 / 无法解析 pairKey ----
    for row in rows:
        position = next(p for p in inputs.positions if p.id == row.design_package_material_id)
        if row.status == "UNPAIRED" and not any(
            a.code == "DUPLICATE_PAIR_KEY" and a.position == position.position for a in anomalies
        ):
            if not row.pair_key:
                anomalies.append(
                    DataAnomalyDTO(
                        id=f"anomaly-UNRESOLVED_PAIR_KEY-{position.position}",
                        code="UNRESOLVED_PAIR_KEY",
                        level="BLOCKING",
                        message=(
                            f"位置 {position.position} 的主素材文件名解析不出同名配对键，"
                            f"无法自动配对，请手动选择副图。"
                        ),
                        blocking=True,
                        position=position.position,
                        pairingId=row.id,
                    )
                )
            else:
                anomalies.append(
                    DataAnomalyDTO(
                        id=f"anomaly-MISSING_VARIANT-{position.position}",
                        code="MISSING_VARIANT",
                        level="BLOCKING",
                        message=(
                            f"位置 {position.position}（pairKey「{row.pair_key}」）缺少同名副图，"
                            f"请上传名为 {row.pair_key}.jpg 的副素材。"
                        ),
                        blocking=True,
                        position=position.position,
                        pairingId=row.id,
                    )
                )

    for link in inputs.variants:
        if link.asset_id in used_variant_assets:
            continue
        if not link.pair_key:
            anomalies.append(
                DataAnomalyDTO(
                    id=f"anomaly-UNRESOLVED_PAIR_KEY-VARIANT-{link.id}",
                    code="UNRESOLVED_PAIR_KEY",
                    level="BLOCKING",
                    message=f"副图 {link.original_filename} 解析不出同名配对键，无法配对，请重命名后重传。",
                    blocking=True,
                )
            )
            continue
        anomalies.append(
            DataAnomalyDTO(
                id=f"anomaly-EXTRA_VARIANT-{link.id}",
                code="EXTRA_VARIANT",
                level="WARNING",
                message=(
                    f"副图 {link.original_filename}（pairKey「{link.pair_key}」）没有同名主素材，"
                    f"属于多余副图，不会参与建版。"
                ),
                blocking=False,
            )
        )

    # ---- 数量核对：主素材数 vs 本次上传副图数 ----
    main_count = len(inputs.positions)
    variant_count = len(inputs.variants)
    if main_count and variant_count != main_count:
        diff = variant_count - main_count
        detail = f"缺少 {abs(diff)} 张副图" if diff < 0 else f"多了 {diff} 张副图"
        anomalies.insert(
            0,
            DataAnomalyDTO(
                id="anomaly-MATERIAL_COUNT_MISMATCH",
                code="MATERIAL_COUNT_MISMATCH",
                level="BLOCKING",
                message=(
                    f"主素材 {main_count} 个、副图 {variant_count} 张（{detail}），"
                    f"数量不一致，不允许生成版本。"
                ),
                blocking=True,
            ),
        )

    unconfirmed = [row for row in rows if row.status != "CONFIRMED"]
    if unconfirmed:
        anomalies.append(
            DataAnomalyDTO(
                id="anomaly-PAIRING_NOT_CONFIRMED",
                code="PAIRING_NOT_CONFIRMED",
                level="BLOCKING",
                message=f"还有 {len(unconfirmed)} 个位置没有确认配对，确认后才能生成版本。",
                blocking=True,
            )
        )

    paired_count = sum(1 for row in rows if row.status in {"PAIRED", "CONFIRMED"})
    return PairingRunResult(
        inputs=inputs,
        rows=rows,
        anomalies=anomalies,
        paired_count=paired_count,
        unpaired_count=len(rows) - paired_count,
        variant_count=len(inputs.variants),
    )


# ---------------------------------------------------------------- DTO


def build_pairing_dtos(
    db: Session, inputs: PairingInputs, rows: list[MaterialPairing]
) -> list[MaterialPairingDTO]:
    row_by_position = {row.design_package_material_id: row for row in rows}
    variant_by_asset = {link.asset_id: link for link in inputs.variants}
    occupied_by_asset = {
        row.variant_asset_id: next(
            (p.position for p in inputs.positions if p.id == row.design_package_material_id), 0
        )
        for row in rows
        if row.variant_asset_id
    }

    options = [
        PairingOptionDTO(
            assetId=link.asset_id,
            originalFilename=link.original_filename,
            pairKey=link.pair_key,
            previewUrl=asset_content_url(link.asset_id),
            sizeBytes=inputs.assets[link.asset_id].size_bytes if link.asset_id in inputs.assets else 0,
            width=inputs.assets[link.asset_id].width if link.asset_id in inputs.assets else None,
            height=inputs.assets[link.asset_id].height if link.asset_id in inputs.assets else None,
            occupiedByPosition=occupied_by_asset.get(link.asset_id),
        )
        for link in inputs.variants
    ]

    dtos: list[MaterialPairingDTO] = []
    for position in inputs.positions:
        row = row_by_position.get(position.id)
        if row is None:
            continue
        variant_link = variant_by_asset.get(row.variant_asset_id or "")
        psd_link = next(
            (link for link in inputs.psds_by_key.values() if link.asset_id == row.psd_asset_id),
            None,
        )
        main_link = inputs.main_by_asset.get(position.source_asset_id)
        material = inputs.materials.get(position.material_id)
        dtos.append(
            MaterialPairingDTO(
                id=row.id,
                designPackageId=row.design_package_id,
                packageUploadId=row.package_upload_id,
                designPackageMaterialId=position.id,
                position=position.position,
                materialId=position.material_id,
                materialCode=material.material_code if material else "",
                materialName=material.name if material else "",
                mainPreviewUrl=asset_content_url(position.source_asset_id),
                mainSourceFileName=(
                    main_link.original_filename
                    if main_link is not None
                    else (inputs.assets[position.source_asset_id].original_filename
                          if position.source_asset_id in inputs.assets else "")
                ),
                pairKey=row.pair_key,
                variantAssetId=row.variant_asset_id,
                variantPreviewUrl=(
                    asset_content_url(row.variant_asset_id) if row.variant_asset_id else None
                ),
                variantFileName=variant_link.original_filename if variant_link else None,
                variantPairKey=variant_link.pair_key if variant_link else None,
                psdAssetId=row.psd_asset_id,
                psdFileName=psd_link.original_filename if psd_link else None,
                source=row.source,
                status=row.status,
                variantId=row.variant_id,
                confirmedBy=row.confirmed_by,
                confirmedAt=row.confirmed_at,
                options=options,
            )
        )
    return dtos


def pairing_summary(dtos: list[MaterialPairingDTO]) -> dict[str, int]:
    summary = {"total": len(dtos), "PAIRED": 0, "UNPAIRED": 0, "CONFIRMED": 0}
    for dto in dtos:
        summary[dto.status] = summary.get(dto.status, 0) + 1
    summary["manual"] = sum(1 for dto in dtos if dto.source == "MANUAL")
    return summary


def load_pairings(
    db: Session, upload_id: str
) -> tuple[PairingInputs, list[MaterialPairing], list[MaterialPairingDTO], dict[str, int]]:
    """只读查询：已有配对行直接返回，没有则返回空列表（不自动写入）。"""
    inputs = gather_pairing_inputs(db, upload_id)
    rows = list(
        db.execute(
            select(MaterialPairing).where(MaterialPairing.package_upload_id == upload_id)
        ).scalars()
    )
    dtos = build_pairing_dtos(db, inputs, rows)
    return inputs, rows, dtos, pairing_summary(dtos)


def runtime_anomalies(
    inputs: PairingInputs, rows: list[MaterialPairing]
) -> list[DataAnomalyDTO]:
    """
    只读地把异常重算一遍（刷新后页面状态一致），不写库。
    """
    anomalies: list[DataAnomalyDTO] = []
    position_by_id = {p.id: p for p in inputs.positions}
    used = {row.variant_asset_id: row for row in rows if row.variant_asset_id}

    for row in rows:
        position = position_by_id.get(row.design_package_material_id)
        if position is None:
            continue
        if row.status == "UNPAIRED":
            code = "UNRESOLVED_PAIR_KEY" if not row.pair_key else "MISSING_VARIANT"
            message = (
                f"位置 {position.position} 的主素材文件名解析不出同名配对键，无法自动配对，请手动选择副图。"
                if code == "UNRESOLVED_PAIR_KEY"
                else f"位置 {position.position}（pairKey「{row.pair_key}」）缺少同名副图。"
            )
            anomalies.append(
                DataAnomalyDTO(
                    id=f"anomaly-{code}-{position.position}",
                    code=code,
                    level="BLOCKING",
                    message=message,
                    blocking=True,
                    position=position.position,
                    pairingId=row.id,
                )
            )
    for link in inputs.variants:
        if link.asset_id in used:
            continue
        if not link.pair_key:
            anomalies.append(
                DataAnomalyDTO(
                    id=f"anomaly-UNRESOLVED_PAIR_KEY-VARIANT-{link.id}",
                    code="UNRESOLVED_PAIR_KEY",
                    level="BLOCKING",
                    message=f"副图 {link.original_filename} 解析不出同名配对键，无法配对。",
                    blocking=True,
                )
            )
        else:
            anomalies.append(
                DataAnomalyDTO(
                    id=f"anomaly-EXTRA_VARIANT-{link.id}",
                    code="EXTRA_VARIANT",
                    level="WARNING",
                    message=(
                        f"副图 {link.original_filename}（pairKey「{link.pair_key}」）没有同名主素材，属于多余副图。"
                    ),
                    blocking=False,
                )
            )
    if rows and any(row.status != "CONFIRMED" for row in rows):
        pending = sum(1 for row in rows if row.status != "CONFIRMED")
        anomalies.append(
            DataAnomalyDTO(
                id="anomaly-PAIRING_NOT_CONFIRMED",
                code="PAIRING_NOT_CONFIRMED",
                level="BLOCKING",
                message=f"还有 {pending} 个位置没有确认配对，确认后才能生成版本。",
                blocking=True,
            )
        )
    main_count = len(inputs.positions)
    variant_count = len(inputs.variants)
    if main_count and variant_count != main_count:
        diff = variant_count - main_count
        detail = f"缺少 {abs(diff)} 张副图" if diff < 0 else f"多了 {diff} 张副图"
        anomalies.insert(
            0,
            DataAnomalyDTO(
                id="anomaly-MATERIAL_COUNT_MISMATCH",
                code="MATERIAL_COUNT_MISMATCH",
                level="BLOCKING",
                message=(
                    f"主素材 {main_count} 个、副图 {variant_count} 张（{detail}），"
                    f"数量不一致，不允许生成版本。"
                ),
                blocking=True,
            ),
        )
    return anomalies


# ---------------------------------------------------------------- 人工修改


def update_pairing(
    db: Session,
    upload_id: str,
    pairing_id: str,
    *,
    variant_asset_id: str | None,
    pair_key: str | None,
    actor: str,
    confirm_reassign: bool,
) -> tuple[MaterialPairingDTO, dict[str, int]]:
    """
    人工修改配对：换一张**本次上传**里的副图（或清空）。

    一个副图只能属于一个主素材：若目标副图已配给别的位置，
    返回 409 要求确认重新分配；确认后原位置退回待配对。
    """
    inputs = gather_pairing_inputs(db, upload_id)
    rows = list(
        db.execute(
            select(MaterialPairing).where(MaterialPairing.package_upload_id == upload_id)
        ).scalars()
    )
    row = next((r for r in rows if r.id == pairing_id), None)
    if row is None:
        raise PairingNotFound()

    position = next(
        (p for p in inputs.positions if p.id == row.design_package_material_id), None
    )
    if position is None:
        raise ValidationError("该配对对应的设计包位置已不存在")

    if pair_key is not None:
        # 允许人工修正 pairKey（用于修文件命名），不能与别的位置撞
        clash = next(
            (
                other
                for other in rows
                if other.id != row.id and other.pair_key == pair_key and pair_key
            ),
            None,
        )
        if clash is not None:
            clash_position = next(
                (p.position for p in inputs.positions if p.id == clash.design_package_material_id),
                0,
            )
            raise ValidationError(f"pairKey「{pair_key}」已经被位置 {clash_position} 使用")
        row.pair_key = pair_key

    if variant_asset_id is not None:
        if variant_asset_id == "":
            # 显式清空
            row.variant_asset_id = None
            row.status = "UNPAIRED"
            row.source = "MANUAL"
            row.confirmed_by = None
            row.confirmed_at = None
            row.updated_at = utcnow()
            db.flush()
            write_log(
                db,
                design_package_id=inputs.package.id,
                target_type="MATERIAL_VARIANT",
                target_id=row.id,
                actor=actor,
                action="UPDATE_PAIRING",
                summary=f"位置 {position.position} 取消副图配对",
            )
            dtos = build_pairing_dtos(db, inputs, rows)
            return next(d for d in dtos if d.id == row.id), pairing_summary(dtos)

        link = next((l for l in inputs.variants if l.asset_id == variant_asset_id), None)
        if link is None:
            raise ValidationError("只能选择本次上传的副图，请刷新后重试")

        owner = next(
            (
                other
                for other in rows
                if other.id != row.id and other.variant_asset_id == variant_asset_id
            ),
            None,
        )
        if owner is not None:
            owner_position = next(
                (p.position for p in inputs.positions if p.id == owner.design_package_material_id),
                0,
            )
            if not confirm_reassign:
                raise PairingConflict(
                    f"该副图当前已配对位置 {owner_position}，是否重新分配？",
                    detail={"occupiedByPosition": owner_position},
                )
            owner.variant_asset_id = None
            owner.status = "UNPAIRED"
            owner.source = "MANUAL"
            owner.confirmed_by = None
            owner.confirmed_at = None
            owner.updated_at = utcnow()
            # 先释放再赋值：UNIQUE(package_upload_id, variant_asset_id) 不允许
            # 中间态出现两行指向同一张副图（一次 flush 的更新顺序不可控）
            db.flush()
            write_log(
                db,
                design_package_id=inputs.package.id,
                target_type="MATERIAL_VARIANT",
                target_id=owner.id,
                actor=actor,
                action="REASSIGN_OUT",
                summary=(
                    f"位置 {owner_position} 的副图 {link.original_filename} "
                    f"被重新分配给位置 {position.position}，该位置退回未配对"
                ),
            )

        before = row.variant_asset_id
        row.variant_asset_id = variant_asset_id
        row.status = "PAIRED"
        row.source = "MANUAL"
        row.confirmed_by = None
        row.confirmed_at = None
        row.updated_at = utcnow()
        db.flush()
        write_log(
            db,
            design_package_id=inputs.package.id,
            target_type="MATERIAL_VARIANT",
            target_id=row.id,
            actor=actor,
            action="UPDATE_PAIRING",
            summary=(
                f"位置 {position.position}（{row.pair_key or '无 pairKey'}）"
                f"人工改配副图 {link.original_filename}（pairKey「{link.pair_key}」）"
            ),
            before_value=before,
            after_value=variant_asset_id,
        )

    db.flush()
    dtos = build_pairing_dtos(db, inputs, rows)
    return next(d for d in dtos if d.id == row.id), pairing_summary(dtos)


def confirm_pairings(
    db: Session, upload_id: str, *, actor: str
) -> tuple[int, list[MaterialPairingDTO], dict[str, int], list[DataAnomalyDTO]]:
    """
    确认整包配对：所有已配上的位置 → CONFIRMED。
    有阻断异常（缺副图 / 多余副图阻断项 / 未解析）时拒绝确认。
    """
    inputs, rows, _dtos, _summary = load_pairings(db, upload_id)
    if not rows:
        raise PairingNotFound()

    anomalies = runtime_anomalies(inputs, rows)
    blocking = [
        a for a in anomalies if a.blocking and a.code in {"MISSING_VARIANT", "UNRESOLVED_PAIR_KEY"}
    ]
    if blocking:
        raise PairingIncomplete(
            "还有配对问题没有解决：" + "；".join(a.message for a in blocking[:3])
        )

    confirmed = 0
    for row in rows:
        if row.status == "CONFIRMED":
            continue
        if not row.variant_asset_id:
            continue
        row.status = "CONFIRMED"
        row.confirmed_by = actor
        row.confirmed_at = utcnow()
        row.updated_at = utcnow()
        confirmed += 1
        position = next(
            (p.position for p in inputs.positions if p.id == row.design_package_material_id), 0
        )
        write_log(
            db,
            design_package_id=inputs.package.id,
            target_type="MATERIAL_VARIANT",
            target_id=row.id,
            actor=actor,
            action="CONFIRM_PAIRING",
            summary=f"确认位置 {position} ↔ 副图配对（pairKey「{row.pair_key}」）",
            after_value="CONFIRMED",
        )
    db.flush()

    _inputs, _rows, dtos, summary = load_pairings(db, upload_id)
    return confirmed, dtos, summary, runtime_anomalies(inputs, rows)


__all__ = [
    "MAIN_ROLE",
    "PSD_ROLE",
    "VARIANT_ROLE",
    "PairingInputs",
    "PairingRunResult",
    "build_pairing_dtos",
    "confirm_pairings",
    "gather_pairing_inputs",
    "load_pairings",
    "pairing_summary",
    "run_pairing",
    "runtime_anomalies",
    "update_pairing",
]
