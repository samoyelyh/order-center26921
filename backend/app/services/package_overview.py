# ============================================================================
# 整包视图聚合服务（PackageOverview）
#
# DesignPackage 上没有任何缓存业务字段，所有计数 / 运营 / 版本 / 匹配状态
# 都在这里聚合出来。一次请求要能回答（第三十六条）：
#   已经上传了吗？已经匹配了吗？已经确认了吗？已经生成 V1 了吗？
# 前端刷新后据此恢复状态，而不是「重新开始」。
# ============================================================================

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ArchivedPackageError, DesignPackageNotFound
from app.db.models import (
    ActivityLog,
    Asset,
    DerivativeBatch,
    DesignPackage,
    DesignPackageMaterial,
    Material,
    MaterialPsdRevision,
    PackageUpload,
    UploadSession,
)
from app.schemas.dto import (
    AssetUploadLinkDTO,
    DataAnomalyDTO,
    DerivativeBatchDTO,
    MaterialPairingDTO,
    PackageOverviewResponse,
    PackageUploadedFilesDTO,
)
from app.schemas.mappers import (
    asset_content_url,
    build_coverage,
    build_count_check,
    to_activity_log_dto,
    to_asset_dto,
    to_material_dto,
    to_package_dto,
    to_position_dto,
    to_session_dto,
    to_upload_dto,
    to_uploaded_file_dto,
)
from app.services.batch_service import count_variants, list_batches, list_package_variants
from app.services.pairing_service import load_pairings, runtime_anomalies
from app.services.repository import list_upload_asset_links


def build_package_overview(db: Session, design_package_id: str) -> PackageOverviewResponse:
    pkg = db.get(DesignPackage, design_package_id)
    if pkg is None:
        raise DesignPackageNotFound()
    if pkg.archived_at is not None:
        # 已删除（归档）的设计包不再往上上传页返回数据：页面会清掉当前设计包并提示
        raise ArchivedPackageError()

    uploads = list(
        db.execute(
            select(PackageUpload)
            .where(PackageUpload.design_package_id == design_package_id)
            .order_by(PackageUpload.created_at.desc())
        ).scalars()
    )
    latest_upload = uploads[0] if uploads else None

    session = None
    if latest_upload is not None:
        session = db.execute(
            select(UploadSession).where(UploadSession.package_upload_id == latest_upload.id)
        ).scalar_one_or_none()

    positions = list(
        db.execute(
            select(DesignPackageMaterial)
            .where(DesignPackageMaterial.design_package_id == design_package_id)
            .order_by(DesignPackageMaterial.position.asc())
        ).scalars()
    )

    material_ids = [p.material_id for p in positions]
    materials: dict[str, Material] = {}
    if material_ids:
        materials = {
            m.id: m
            for m in db.execute(select(Material).where(Material.id.in_(material_ids))).scalars()
        }

    # 收集所有相关 Asset：
    #   1. 位置源文件 + MAT 预览 + 当前 PSD（业务被引用到的）
    #   2. 本设计包全部上传行为交付的文件（package_upload_assets 多对多关联）
    #      —— 副图只在这里出现（配对关系另存 material_pairings）
    asset_ids: set[str] = set()
    for position in positions:
        asset_ids.add(position.source_asset_id)
    for material in materials.values():
        asset_ids.add(material.preview_asset_id)
        if material.current_psd_revision_id:
            revision = db.get(MaterialPsdRevision, material.current_psd_revision_id)
            if revision is not None:
                asset_ids.add(revision.asset_id)

    upload_ids = [u.id for u in uploads]
    links = list_upload_asset_links(db, package_upload_ids=upload_ids)
    for link in links:
        asset_ids.add(link.asset_id)

    assets: dict[str, Asset] = {}
    if asset_ids:
        assets = {
            a.id: a
            for a in db.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars()
        }

    # ---- 上传文件分组（第9/10条：页面必须能显示真实的主素材/PSD/副图张数） ----
    seen: set[tuple[str, str]] = set()
    package_files = []
    for link in links:
        key = (link.asset_id, link.file_role)
        if key in seen:
            continue
        seen.add(key)
        package_files.append(to_uploaded_file_dto(link, assets.get(link.asset_id)))

    latest_files = [
        to_uploaded_file_dto(link, assets.get(link.asset_id))
        for link in links
        if latest_upload is not None and link.package_upload_id == latest_upload.id
    ]
    uploaded_files = PackageUploadedFilesDTO.build(package_files)
    latest_uploaded_files = PackageUploadedFilesDTO.build(latest_files)

    # 每个 MAT 被多少个设计包位置引用（跨设计包复用计数）
    design_counts: dict[str, int] = {}
    if material_ids:
        rows = db.execute(
            select(DesignPackageMaterial.material_id, func.count(DesignPackageMaterial.id))
            .where(DesignPackageMaterial.material_id.in_(material_ids))
            .group_by(DesignPackageMaterial.material_id)
        ).all()
        design_counts = {material_id: int(count) for material_id, count in rows}

    # MAT 的 PSD 修订历史
    psd_revisions: dict[str, list[MaterialPsdRevision]] = {}
    if material_ids:
        for revision in db.execute(
            select(MaterialPsdRevision)
            .where(MaterialPsdRevision.material_id.in_(material_ids))
            .order_by(MaterialPsdRevision.revision_no.asc())
        ).scalars():
            psd_revisions.setdefault(revision.material_id, []).append(revision)

    material_dtos = []
    for material in materials.values():
        revisions = psd_revisions.get(material.id, [])
        current = next((r for r in revisions if r.id == material.current_psd_revision_id), None)
        material_dtos.append(
            to_material_dto(
                material,
                preview_asset=assets.get(material.preview_asset_id),
                psd_asset=assets.get(current.asset_id) if current else None,
                psd_revisions=revisions,
                design_count=design_counts.get(material.id, 0),
            )
        )
    material_dto_by_id = {dto.id: dto for dto in material_dtos}

    position_dtos = []
    for position in positions:
        material = materials.get(position.material_id)
        material_dto = material_dto_by_id.get(position.material_id)
        psd_uri = None
        if material is not None and material.current_psd_revision_id:
            revision = db.get(MaterialPsdRevision, material.current_psd_revision_id)
            if revision is not None and revision.asset_id in assets:
                psd_uri = asset_content_url(revision.asset_id)
        position_dtos.append(
            to_position_dto(
                position,
                material=material,
                material_dto=material_dto,
                preview_uri=asset_content_url(position.source_asset_id)
                if position.source_asset_id in assets
                else None,
                psd_uri=psd_uri,
            )
        )

    # ---- Phase 2：同名配对结果 / 版本 ----
    # 配对是「按上传」的：页面渲染最新一次上传的配对；没有跑过配对就是空表。
    pairing_dtos: list[MaterialPairingDTO] = []
    pairing_summary_map: dict[str, int] = {}
    pairing_upload_id: str | None = None
    anomalies: list[DataAnomalyDTO] = []
    if latest_upload is not None and positions:
        pairing_inputs, pairing_rows, pairing_dtos, pairing_summary_map = load_pairings(
            db, latest_upload.id
        )
        pairing_upload_id = latest_upload.id
        # 即使还没跑过配对，也要把「主素材数与副图数不一致」等异常算出来，
        # 页面才能解释为什么还生成不了版本
        anomalies = runtime_anomalies(pairing_inputs, pairing_rows)

    variant_upload_count = (
        len(pairing_inputs.variants) if pairing_upload_id and positions else uploaded_files.variantCount
    )
    count_check = build_count_check(
        len(positions),
        variant_upload_count,
        has_pairings=bool(pairing_dtos),
    )

    batches: list[DerivativeBatchDTO] = list_batches(db, design_package_id)
    current_batch = batches[-1] if batches else None
    variant_total = count_variants(db, design_package_id)
    built_variants = current_batch.variants if current_batch else []
    # 整包视图返回**全部版本**的副素材：主素材详情的「副素材」Tab 要能看到 1-1 与 1-2，
    # 只给当前版本会让历史版本的副素材凭空消失。
    package_variants = list_package_variants(db, design_package_id)

    if current_batch is not None:
        coverage = build_coverage(
            current_batch.code,
            batch_id=current_batch.id,
            covered=len([v for v in built_variants if not v.deleted]),
            total=len(positions),
            missing_codes=[],
        )
    else:
        coverage = build_coverage(
            "V1",
            covered=0,
            total=len(positions),
            missing_codes=[f"{p.position}-1" for p in positions],
        )

    # 运营：来自本包所有上传会话（一个包可以先后派发给多个运营）。
    # 运营可能是手工填写的自由文本，此时 operator_id 为空 —— 不能因为没有 userId 就过滤掉。
    operator_rows = db.execute(
        select(UploadSession.operator_id, UploadSession.operator_name)
        .where(
            UploadSession.design_package_id == design_package_id,
            or_(
                UploadSession.operator_id.is_not(None),
                UploadSession.operator_name.is_not(None),
            ),
        )
        .group_by(UploadSession.operator_id, UploadSession.operator_name)
    ).all()
    operators = [
        {"operatorId": operator_id or "", "operatorName": operator_name or operator_id or ""}
        for operator_id, operator_name in operator_rows
        if (operator_id or operator_name)
    ]

    logs = list(
        db.execute(
            select(ActivityLog)
            .where(ActivityLog.design_package_id == design_package_id)
            .order_by(ActivityLog.created_at.asc())
        ).scalars()
    )

    blocking = [anomaly for anomaly in anomalies if anomaly.blocking]
    submitted = session is not None and session.stage == "SUBMITTED"

    return PackageOverviewResponse(
        designPackage=to_package_dto(db, pkg),
        upload=to_upload_dto(latest_upload) if latest_upload else None,
        uploads=[to_upload_dto(u) for u in uploads],
        session=to_session_dto(session) if session else None,
        positions=position_dtos,
        materials=material_dtos,
        assets=[to_asset_dto(a) for a in assets.values()],
        uploadedFiles=uploaded_files,
        latestUploadedFiles=latest_uploaded_files,
        uploadAssetLinks=[AssetUploadLinkDTO.from_entity(link) for link in links],
        pairings=pairing_dtos,
        pairingSummary=pairing_summary_map,
        pairingUploadId=pairing_upload_id,
        currentBatch=current_batch,
        batch=current_batch,
        batches=batches,
        variants=package_variants,
        anomalies=anomalies,
        blockingAnomalies=blocking,
        logs=[to_activity_log_dto(log) for log in logs],
        operatorId=operators[0]["operatorId"] or None if operators else None,
        operatorName=operators[0]["operatorName"] or None if operators else None,
        operators=operators,
        mainMaterialCount=len(positions),
        variantCount=variant_total,
        currentBatchNo=current_batch.versionNo if current_batch else None,
        countCheck=count_check,
        coverage=coverage,
        submitted=submitted,
        phase="PHASE_2",
    )
