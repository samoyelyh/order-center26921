# ============================================================================
# 主素材 / 设计包位置 API（Phase 1）
#
# POST /api/uploads/{upload_id}/materials
#   一次提交整包位置清单，在**单个事务**内完成：
#     1. 校验 Asset 存在
#     2. 按 BLAKE3 / 已绑定 MAT 复用，或新建 MAT（code_sequences 行级锁并发安全发号）
#     3. 若带 PSD → 建 material_psd_revisions(revision_no=1) 并回填 current_psd_revision_id
#     4. 建 design_package_materials（UNIQUE(design_package_id, position) 冲突明确报错）
#   任一步失败 → 整体回滚
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    AssetNotFound,
    MaterialCreateError,
    MaterialNotFound,
    PositionConflict,
    UploadNotFound,
)
from app.db.models import (
    Asset,
    DesignPackage,
    DesignPackageMaterial,
    Material,
    MaterialPsdRevision,
    MaterialVariant,
    PackageUpload,
    UploadSession,
)
from app.db.session import get_db
from app.schemas.dto import (
    DesignPackageMaterialDTO,
    MaterialDTO,
    SubmitMaterialItem,
    SubmitMaterialsRequest,
    SubmitMaterialsResponse,
    TagBatchRequest,
    TagPatchRequest,
)
from app.schemas.mappers import to_material_dto, to_position_dto
from app.services.activity import write_log
from app.services.files import classify_by_filename, pair_key_of, position_hint_of
from app.services.repository import (
    allocate_material_codes,
    find_material_by_preview_asset,
    link_upload_asset,
    list_assets_by_ids,
    list_materials_by_ids,
    list_positions,
    list_upload_asset_links,
    new_id,
    utcnow,
)
from app.services.storage import get_storage

router = APIRouter(tags=["materials"])


@dataclass
class _PlannedPosition:
    """一个位置的入库计划。"""

    item: SubmitMaterialItem
    preview_asset: Asset
    psd_asset: Asset | None
    source_file_name: str
    material: Material | None = None
    reused: bool = False
    code: str | None = None


def _resolve_assets(db: Session, asset_ids: list[str]) -> dict[str, Asset]:
    assets = list_assets_by_ids(db, asset_ids)
    missing = [asset_id for asset_id in asset_ids if asset_id not in assets]
    if missing:
        raise AssetNotFound(f"文件资产不存在：{', '.join(missing)}")
    return assets


def build_material_dto(db: Session, material: Material, *, reused: bool = False) -> MaterialDTO:
    revisions = list(
        db.execute(
            select(MaterialPsdRevision)
            .where(MaterialPsdRevision.material_id == material.id)
            .order_by(MaterialPsdRevision.revision_no.asc())
        ).scalars()
    )
    current = next((r for r in revisions if r.id == material.current_psd_revision_id), None)
    asset_ids = [material.preview_asset_id] + [r.asset_id for r in revisions]
    assets = _resolve_assets(db, list(dict.fromkeys(asset_ids)))
    design_count = db.execute(
        select(func.count(DesignPackageMaterial.id)).where(
            DesignPackageMaterial.material_id == material.id
        )
    ).scalar_one()
    return to_material_dto(
        material,
        preview_asset=assets.get(material.preview_asset_id),
        psd_asset=assets.get(current.asset_id) if current else None,
        psd_revisions=revisions,
        design_count=int(design_count or 0),
        reused=reused,
    )


def _find_material_for_asset(db: Session, preview_asset: Asset) -> Material | None:
    """
    同一主素材复用判定：
      1) 预览 Asset 本身已绑定 MAT → 直接复用
      2) 预览 Asset 的 BLAKE3 命中另一个已绑定 MAT 的 Asset → 复用那个 MAT
    """
    bound = find_material_by_preview_asset(db, preview_asset.id)
    if bound is not None:
        return bound
    if not preview_asset.blake3:
        return None
    siblings = db.execute(
        select(Asset)
        .where(
            Asset.blake3 == preview_asset.blake3,
            Asset.deleted_at.is_(None),
            Asset.id != preview_asset.id,
        )
        .order_by(Asset.created_at.asc())
    ).scalars()
    for candidate in siblings:
        bound = find_material_by_preview_asset(db, candidate.id)
        if bound is not None:
            return bound
    return None


@router.post(
    "/uploads/{upload_id}/materials",
    response_model=SubmitMaterialsResponse,
    summary="提交整包主素材位置（创建/复用 MAT + 建位置 + PSD Revision）",
)
def submit_upload_materials(
    upload_id: str,
    payload: SubmitMaterialsRequest,
    db: Session = Depends(get_db),
) -> SubmitMaterialsResponse:
    upload = db.get(PackageUpload, upload_id)
    if upload is None:
        raise UploadNotFound(f"上传记录不存在：{upload_id}")
    pkg = db.get(DesignPackage, upload.design_package_id)
    if pkg is None:
        raise UploadNotFound("上传记录对应的设计包不存在")

    actor = payload.actor or upload.uploader_name

    # ---- 入参内位置去重 ----
    seen: set[int] = set()
    for item in payload.materials:
        if item.position in seen:
            raise PositionConflict(f"同一请求内位置 {item.position} 重复提交")
        seen.add(item.position)

    # ---- 位置占用预检：同一个位置不允许换主素材 ----
    # 同一个设计包的第二次上传（新一版副图）常常会连主素材一起重传：
    # BLAKE3 去重后还是同一个 Asset，这时「位置已存在」是正常情况，应当复用；
    # 只有「同一个位置换成了另一张主图」才是真冲突。
    existing_positions = {row.position: row for row in list_positions(db, pkg.id)}
    for item in payload.materials:
        existing = existing_positions.get(item.position)
        if existing is None:
            continue
        if existing.source_asset_id != item.previewAssetId:
            raise PositionConflict(
                f"设计包「{pkg.name}」位置 {item.position} 已经有主素材"
                f"（{existing.source_file_name}），不能用另一张图顶替；"
                "需要换主素材请新建一个设计包"
            )

    # ---- 校验全部 Asset ----
    asset_ids: list[str] = []
    for item in payload.materials:
        asset_ids.append(item.previewAssetId)
        if item.psdAssetId:
            asset_ids.append(item.psdAssetId)
    assets = _resolve_assets(db, list(dict.fromkeys(asset_ids)))

    plans: list[_PlannedPosition] = []
    for item in payload.materials:
        preview_asset = assets[item.previewAssetId]
        psd_asset = assets[item.psdAssetId] if item.psdAssetId else None
        plans.append(
            _PlannedPosition(
                item=item,
                preview_asset=preview_asset,
                psd_asset=psd_asset,
                source_file_name=item.sourceFileName or preview_asset.original_filename,
            )
        )

    created_count = 0
    reused_count = 0
    position_rows: list[DesignPackageMaterial] = []

    try:
        # ---- 第一步：判定复用，收集需要新建的 MAT ----
        pending_new: list[_PlannedPosition] = []
        for plan in plans:
            existing = _find_material_for_asset(db, plan.preview_asset)
            if existing is not None:
                plan.material = existing
                plan.reused = True
                reused_count += 1
            else:
                pending_new.append(plan)

        # ---- 第二步：并发安全发放 MAT 编码（一次锁，批量取号）----
        if pending_new:
            codes = allocate_material_codes(db, len(pending_new))
            for plan, code in zip(pending_new, codes, strict=True):
                plan.code = code

        # ---- 第三步：建 MAT（创建时继承设计包标签，之后独立修改，不会被反向覆盖） ----
        package_tags = list(pkg.tags or [])
        for plan in pending_new:
            assert plan.code is not None
            material = Material(
                id=plan.code,
                material_code=plan.code,
                name=plan.item.displayName
                or plan.source_file_name.rsplit(".", 1)[0],
                preview_asset_id=plan.preview_asset.id,
                tags=list(package_tags),
                source_package_upload_id=upload.id,
                created_by=actor,
            )
            db.add(material)
            db.flush()
            plan.material = material
            plan.reused = False
            created_count += 1

        # ---- 第四步：PSD Revision + 设计包位置 ----
        for plan in plans:
            material = plan.material
            assert material is not None

            if plan.psd_asset is not None:
                revision_one = db.execute(
                    select(MaterialPsdRevision).where(
                        MaterialPsdRevision.material_id == material.id,
                        MaterialPsdRevision.revision_no == 1,
                    )
                ).scalar_one_or_none()
                if revision_one is None:
                    revision = MaterialPsdRevision(
                        id=new_id("psdrev"),
                        material_id=material.id,
                        revision_no=1,
                        asset_id=plan.psd_asset.id,
                        created_by=actor,
                        deleted=False,
                    )
                    db.add(revision)
                    db.flush()
                    material.current_psd_revision_id = revision.id
                    material.updated_at = utcnow()
                    write_log(
                        db,
                        design_package_id=pkg.id,
                        target_type="MATERIAL",
                        target_id=material.id,
                        actor=actor,
                        action="CREATE_PSD_REVISION",
                        summary=(
                            f"{material.material_code} 创建 PSD Revision 1"
                            f"（{plan.psd_asset.original_filename}）"
                        ),
                        after_value=plan.psd_asset.id,
                    )
                elif material.current_psd_revision_id is None:
                    material.current_psd_revision_id = revision_one.id
                    material.updated_at = utcnow()
                else:
                    write_log(
                        db,
                        design_package_id=pkg.id,
                        target_type="MATERIAL",
                        target_id=material.id,
                        actor=actor,
                        action="SKIP_PSD_REVISION",
                        summary=(
                            f"{material.material_code} 已有 PSD Revision，"
                            f"忽略本次重复 PSD（{plan.psd_asset.original_filename}）"
                        ),
                    )

            # 位置已存在（同一个设计包的新一版上传）：沿用原位置与 MAT，
            # 只把「本次上传用了这个位置」写进日志，不新增行、不改编号。
            existing_position = existing_positions.get(plan.item.position)
            if existing_position is not None:
                position_rows.append(existing_position)
                write_log(
                    db,
                    design_package_id=pkg.id,
                    target_type="DESIGN_PACKAGE",
                    target_id=pkg.id,
                    actor=actor,
                    action="REUSE_POSITION",
                    summary=(
                        f"设计包位置 {plan.item.position} 沿用主素材 "
                        f"{material.material_code}（本次上传 {upload.id}）"
                    ),
                    after_value=material.material_code,
                )
                continue

            position = DesignPackageMaterial(
                id=new_id("dpm"),
                design_package_id=pkg.id,
                position=plan.item.position,
                material_id=material.id,
                display_name=plan.item.displayName or material.name,
                source_file_name=plan.source_file_name,
                source_asset_id=plan.preview_asset.id,
                created_from_upload_id=upload.id,
            )
            db.add(position)
            db.flush()
            position_rows.append(position)

            write_log(
                db,
                design_package_id=pkg.id,
                target_type="MATERIAL",
                target_id=material.id,
                actor=actor,
                action="REUSE_MATERIAL" if plan.reused else "CREATE_MATERIAL",
                summary=(
                    f"位置 {plan.item.position} "
                    f"{'复用' if plan.reused else '新建'}主素材 {material.material_code}"
                ),
            )
            write_log(
                db,
                design_package_id=pkg.id,
                target_type="DESIGN_PACKAGE",
                target_id=pkg.id,
                actor=actor,
                action="CREATE_POSITION",
                summary=f"设计包位置 {plan.item.position} → {material.material_code}",
                after_value=material.material_code,
            )

        # ---- 第五步：兜底补齐本次上传的文件归属关联 ----
        # 正常情况下 uploadFile 已经按「用户点的入口」写好了 package_upload_assets。
        # 这里只对**本次上传还没有任何关联行**的 Asset 补一条，
        # 覆盖「先建 Asset 后补归属」的历史/异常路径。
        #
        # 重要：绝不能在这里按文件名重新推断角色 —— 新的命名规则下
        # 主图和副图是**同名**的（main/1.jpg 与 variant/1.jpg 都叫 1.jpg），
        # 文件名推断会把副图判成 MAIN_PREVIEW，进而把主图的关联行改写掉。
        if payload.linkAssetIds:
            existing_asset_ids = {
                link.asset_id
                for link in list_upload_asset_links(db, package_upload_ids=[upload.id])
            }
            for asset_id in dict.fromkeys(payload.linkAssetIds):
                if asset_id in existing_asset_ids:
                    continue
                asset_row = db.get(Asset, asset_id)
                if asset_row is None:
                    continue
                role = classify_by_filename(asset_row.original_filename)
                link = link_upload_asset(
                    db,
                    package_upload_id=upload.id,
                    asset_id=asset_id,
                    file_role=role,
                    original_filename=asset_row.original_filename,
                    pair_key=pair_key_of(asset_row.original_filename),
                    position_hint=position_hint_of(asset_row.original_filename),
                )
                existing_asset_ids.add(asset_id)
                write_log(
                    db,
                    design_package_id=pkg.id,
                    target_type="ASSET",
                    target_id=asset_id,
                    actor=actor,
                    action="LINK_ASSET",
                    summary=f"资产 {asset_row.original_filename} 以 {role} 角色补登到本次上传",
                    after_value=upload.id,
                )
                _ = link

        # ---- 第六步：会话与上传记录状态推进 ----
        session = db.execute(
            select(UploadSession).where(UploadSession.package_upload_id == upload.id)
        ).scalar_one_or_none()
        if session is not None:
            session.stage = "PARSED"
            session.updated_at = utcnow()
        upload.status = "PARSED"
        upload.updated_at = utcnow()

        db.commit()

    except IntegrityError as exc:
        db.rollback()
        message = str(getattr(exc, "orig", exc))
        if "uq_dpm_pkg_position" in message or "design_package_materials" in message:
            raise PositionConflict(
                "设计包位置冲突：同一设计包的同一个位置已经存在，请刷新后重试"
            ) from exc
        if "uq_materials_code" in message:
            raise MaterialCreateError("主素材编码冲突，请重试") from exc
        raise MaterialCreateError(f"主素材入库失败：{message}") from exc
    except (PositionConflict, AssetNotFound):
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise MaterialCreateError(f"主素材入库失败：{exc}") from exc

    return _build_submit_response(db, pkg, upload, position_rows, created_count, reused_count)


def _build_submit_response(
    db: Session,
    pkg: DesignPackage,
    upload: PackageUpload,
    position_rows: list[DesignPackageMaterial],
    created_count: int,
    reused_count: int,
) -> SubmitMaterialsResponse:
    storage = get_storage()
    material_ids = list(dict.fromkeys(p.material_id for p in position_rows))
    materials = list_materials_by_ids(db, material_ids)

    dto_by_material: dict[str, MaterialDTO] = {}
    for material_id, material in materials.items():
        dto_by_material[material_id] = build_material_dto(
            db, material, reused=material.source_package_upload_id != upload.id
        )

    position_dtos: list[DesignPackageMaterialDTO] = []
    for position in sorted(position_rows, key=lambda p: p.position):
        material = materials.get(position.material_id)
        source_asset = db.get(Asset, position.source_asset_id)
        psd_uri = None
        if material is not None and material.current_psd_revision_id:
            revision = db.get(MaterialPsdRevision, material.current_psd_revision_id)
            if revision is not None:
                psd_asset = db.get(Asset, revision.asset_id)
                if psd_asset is not None:
                    psd_uri = storage.public_url(psd_asset.storage_key)
        position_dtos.append(
            to_position_dto(
                position,
                material=material,
                material_dto=dto_by_material.get(position.material_id),
                preview_uri=storage.public_url(source_asset.storage_key) if source_asset else None,
                psd_uri=psd_uri,
            )
        )

    return SubmitMaterialsResponse(
        designPackageId=pkg.id,
        positions=position_dtos,
        createdMaterialCount=created_count,
        reusedMaterialCount=reused_count,
    )


@router.get("/materials/{material_code}", response_model=MaterialDTO, summary="主素材详情")
def get_material(material_code: str, db: Session = Depends(get_db)) -> MaterialDTO:
    material = db.execute(
        select(Material).where(Material.material_code == material_code)
    ).scalar_one_or_none()
    if material is None:
        raise MaterialNotFound(f"主素材不存在：{material_code}")
    return build_material_dto(db, material)


# ---------------------------------------------------------------- 标签 / 流转记录


@router.patch("/materials/{material_code}/tags", response_model=MaterialDTO, summary="修改主素材标签")
def patch_material_tags(
    material_code: str,
    payload: TagPatchRequest,
    db: Session = Depends(get_db),
) -> MaterialDTO:
    from app.services.tags import get_material_or_404, normalize_tags, set_tags

    material = get_material_or_404(db, material_code)
    actor = (payload.actor or "").strip() or material.created_by
    clean = normalize_tags(payload.tags)
    old = normalize_tags(list(material.tags or []))
    # 按前后差集决定记 ADD / REMOVE / REPLACE（写 ActivityLog，前端流转记录可查）
    added = [t for t in clean if t not in old]
    removed = [t for t in old if t not in clean]
    action = "REPLACE_TAG" if (added and removed) else "ADD_TAG" if added else "REMOVE_TAG" if removed else None
    if action is not None:
        # 找到任意一个引用它的设计包，让流转记录能在「设计包历史」里出现
        pkg_id = db.execute(
            select(DesignPackageMaterial.design_package_id)
            .where(DesignPackageMaterial.material_id == material.id)
            .limit(1)
        ).scalar_one_or_none()
        set_tags(
            db,
            target=material,
            tags=clean,
            actor=actor,
            action=action,
            design_package_id=pkg_id,
        )
        db.commit()
        db.refresh(material)
    return build_material_dto(db, material)


@router.get("/materials/{material_code}/history", summary="主素材流转记录（来自现有 ActivityLog）")
def get_material_history(material_code: str, db: Session = Depends(get_db)) -> list[dict]:
    from app.services.tags import material_history

    return material_history(db, material_code)


# ---- 副素材 ----


@router.get("/material-variants/{variant_id}", summary="副素材详情")
def get_variant_detail_endpoint(variant_id: str, db: Session = Depends(get_db)) -> dict:
    from app.services.tags import get_variant_detail

    return get_variant_detail(db, variant_id)


@router.patch("/material-variants/{variant_id}/tags", summary="修改副素材标签")
def patch_variant_tags(
    variant_id: str,
    payload: TagPatchRequest,
    db: Session = Depends(get_db),
) -> dict:
    from app.services.tags import get_variant_detail, get_variant_or_404, normalize_tags, set_tags

    variant = get_variant_or_404(db, variant_id)
    actor = (payload.actor or "").strip() or "素材中心"
    clean = normalize_tags(payload.tags)
    old = normalize_tags(list(variant.tags or []))
    added = [t for t in clean if t not in old]
    removed = [t for t in old if t not in clean]
    action = "REPLACE_TAG" if (added and removed) else "ADD_TAG" if added else "REMOVE_TAG" if removed else None
    if action is not None:
        # 副素材的流转记录挂到它所属的设计包（流转记录查询按 design_package_id 聚合时用得到）
        pkg_id = db.execute(
            select(DesignPackageMaterial.design_package_id)
            .where(DesignPackageMaterial.id == variant.design_package_material_id)
            .limit(1)
        ).scalar_one_or_none()
        set_tags(
            db,
            target=variant,
            tags=clean,
            actor=actor,
            action=action,
            design_package_id=pkg_id,
        )
        db.commit()
        db.refresh(variant)
    return get_variant_detail(db, variant_id)


@router.get("/material-variants/{variant_id}/history", summary="副素材流转记录（来自现有 ActivityLog）")
def get_variant_history(variant_id: str, db: Session = Depends(get_db)) -> list[dict]:
    from app.services.tags import variant_history

    return variant_history(db, variant_id)


# ---- 批量标签 ----


@router.post("/tags/batch", summary="素材中心多选批量调整标签（add / remove / replace）")
def batch_tags(payload: TagBatchRequest, db: Session = Depends(get_db)) -> dict:
    from app.services.tags import normalize_tags, set_tags

    clean_tags = normalize_tags(payload.tags)
    actor = (payload.actor or "").strip() or "素材中心"
    action = {"add": "BATCH_ADD_TAG", "remove": "BATCH_REMOVE_TAG", "replace": "REPLACE_TAG"}[payload.op]

    updated = 0
    for target_id in payload.targetIds:
        target = None
        pkg_id = None
        if payload.targetType == "MATERIAL":
            target = db.execute(
                select(Material).where(Material.material_code == target_id)
            ).scalar_one_or_none()
            if target is None:
                continue
            pkg_id = db.execute(
                select(DesignPackageMaterial.design_package_id)
                .where(DesignPackageMaterial.material_id == target.id)
                .limit(1)
            ).scalar_one_or_none()
        else:
            target = db.get(MaterialVariant, target_id)
            if target is None:
                continue
            pkg_id = db.execute(
                select(DesignPackageMaterial.design_package_id)
                .where(DesignPackageMaterial.id == target.design_package_material_id)
                .limit(1)
            ).scalar_one_or_none()

        old = normalize_tags(list(target.tags or []))
        if payload.op == "add":
            new = normalize_tags([*old, *clean_tags])
        elif payload.op == "remove":
            new = [t for t in old if t not in clean_tags]
        else:
            new = clean_tags
        if new == old:
            continue
        set_tags(db, target=target, tags=new, actor=actor, action=action, design_package_id=pkg_id)
        updated += 1
    db.commit()
    return {"updated": updated, "action": action}


# ---- 标签选择器数据源 ----


@router.get("/tags", summary="全库去重标签（标签选择器用）")
def list_tags(db: Session = Depends(get_db)) -> list[dict]:
    from app.services.tags import list_distinct_tags

    return list_distinct_tags(db)


@router.get("/materials", response_model=list[MaterialDTO], summary="主素材列表")
def list_materials(
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[MaterialDTO]:
    rows = db.execute(
        select(Material)
        .where(Material.archived_at.is_(None))
        .order_by(Material.created_at.desc())
        .limit(min(limit, 500))
        .offset(offset)
    ).scalars()
    return [build_material_dto(db, material) for material in rows]
