# ============================================================================
# 设计包 API（Phase 1/2）
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    ArchivedPackageError,
    ConflictError,
    DesignPackageNotFound,
    ValidationError,
)
from app.db.models import ActivityLog, DerivativeBatch, DesignPackage, DesignPackageMaterial, Material
from app.db.session import get_db
from app.schemas.dto import (
    CreateUploadRequest,
    CreateUploadResponse,
    DesignPackageCreateRequest,
    DesignPackageDTO,
    DesignPackagePatchRequest,
    PackageOverviewResponse,
)
from app.schemas.mappers import to_activity_log_dto, to_package_dto, to_session_dto, to_upload_dto
from app.services.activity import write_log
from app.services.package_overview import build_package_overview
from app.services.tags import normalize_tags
from app.services.repository import (
    find_upload_by_session_key,
    generate_package_code,
    list_uploads,
    new_id,
    utcnow,
)

router = APIRouter(tags=["design-packages"])

DEFAULT_ACTOR = "肖芸"
DEFAULT_UPLOADER_ID = "u-design-001"


# ---------------------------------------------------------------- 创建设计包


@router.post("/design-packages", response_model=DesignPackageDTO, summary="创建设计包")
def create_design_package(
    payload: DesignPackageCreateRequest,
    db: Session = Depends(get_db),
) -> DesignPackageDTO:
    actor = payload.createdBy or DEFAULT_ACTOR
    name = payload.name.strip()
    if not name:
        raise ConflictError("设计包名称不能为空")

    # 设计美工是设计包的长期属性，必须显式提供（可只有姓名，没有 userId）
    designer_name = (payload.designerName or "").strip()
    if not designer_name:
        raise ValidationError("请填写设计美工")

    # 设计编码：用户/美工自己的设计编号（素材的「关联设计」按它聚合）。
    # 同一个设计可以有多个设计包（新一版），所以只校验非空，不做唯一约束。
    design_code = (payload.designCode or "").strip()
    if not design_code:
        raise ValidationError("请填写设计编码")

    # 负责人：业务上负责这套设计的人。不传时用设计美工兜底（两者通常是同一个人）。
    responsible_name = (payload.responsibleName or designer_name).strip()
    responsible_id = (payload.responsibleId or "").strip() or None
    tags = normalize_tags(payload.tags)

    last_error: Exception | None = None
    for _ in range(5):
        pkg = DesignPackage(
            id=new_id("pkg"),
            code=generate_package_code(name),
            name=name,
            design_code=design_code,
            category_code=(payload.categoryCode or "UNKNOWN").strip().upper(),
            category_name=(payload.categoryName or "未知品类").strip(),
            tags=tags,
            responsible_id=responsible_id,
            responsible_name=responsible_name,
            remark=payload.remark,
            designer_id=(payload.designerId or "").strip() or None,
            designer_name=designer_name,
            created_by=actor,
        )
        db.add(pkg)
        try:
            db.flush()
        except IntegrityError as exc:  # code 冲突重试
            db.rollback()
            last_error = exc
            continue

        write_log(
            db,
            design_package_id=pkg.id,
            target_type="DESIGN_PACKAGE",
            target_id=pkg.id,
            actor=actor,
            action="CREATE_PACKAGE",
            summary=f"创建设计包「{name}」（设计编码：{design_code}；负责人：{responsible_name}；标签：{'、'.join(tags) or '无'}）",
        )
        db.commit()
        db.refresh(pkg)
        return to_package_dto(db, pkg)

    raise ConflictError(f"设计包编码生成失败，请重试：{last_error}")


@router.patch(
    "/design-packages/{design_package_id}",
    response_model=DesignPackageDTO,
    summary="修改设计包（设计编码 / 设计美工 / 备注，均支持手工填写）",
)
def patch_design_package(
    design_package_id: str,
    payload: DesignPackagePatchRequest,
    db: Session = Depends(get_db),
) -> DesignPackageDTO:
    pkg = db.get(DesignPackage, design_package_id)
    if pkg is None:
        raise DesignPackageNotFound()

    if payload.designerName is not None:
        name = payload.designerName.strip()
        if not name:
            raise ValidationError("请填写设计美工")
        before = pkg.designer_name
        pkg.designer_name = name
        # 有 userId 就一起存，没有就置空 —— 不因为没有 ID 拒绝保存
        pkg.designer_id = (payload.designerId or "").strip() or None
        if before != name:
            write_log(
                db,
                design_package_id=pkg.id,
                target_type="DESIGN_PACKAGE",
                target_id=pkg.id,
                actor=name,
                action="UPDATE_DESIGNER",
                summary=f"设计美工由「{before}」改为「{name}」",
                before_value=before,
                after_value=name,
            )
    if payload.designCode is not None:
        # 设计编码允许事后修正（例如美工改编号）；只要求非空
        code_text = payload.designCode.strip()
        if not code_text:
            raise ValidationError("设计编码不能为空")
        before_code = pkg.design_code
        pkg.design_code = code_text
        if before_code != code_text:
            write_log(
                db,
                design_package_id=pkg.id,
                target_type="DESIGN_PACKAGE",
                target_id=pkg.id,
                actor=payload.designerName or pkg.designer_name,
                action="UPDATE_DESIGN_CODE",
                summary=f"设计编码由「{before_code}」改为「{code_text}」",
                before_value=before_code,
                after_value=code_text,
            )

    if payload.categoryCode is not None:
        category_code = payload.categoryCode.strip().upper()
        if not category_code:
            raise ValidationError("品类编码不能为空")
        pkg.category_code = category_code
    if payload.categoryName is not None:
        category_name = payload.categoryName.strip()
        if not category_name:
            raise ValidationError("品类名称不能为空")
        pkg.category_name = category_name

    # 负责人修改：写 CHANGE_RESPONSIBLE 维护记录（修改前/修改后/操作人/时间）
    if payload.responsibleName is not None:
        new_responsible = payload.responsibleName.strip()
        if not new_responsible:
            raise ValidationError("负责人不能为空")
        before_responsible = pkg.responsible_name
        if before_responsible != new_responsible:
            pkg.responsible_name = new_responsible
            pkg.responsible_id = (payload.responsibleId or "").strip() or pkg.responsible_id
            write_log(
                db,
                design_package_id=pkg.id,
                target_type="DESIGN_PACKAGE",
                target_id=pkg.id,
                actor=(payload.responsibleName or "").strip() or pkg.created_by,
                action="CHANGE_RESPONSIBLE",
                summary=f"负责人由「{before_responsible}」改为「{new_responsible}」",
                before_value=before_responsible,
                after_value=new_responsible,
            )
    elif payload.responsibleId is not None:
        pkg.responsible_id = payload.responsibleId.strip() or None

    # 标签：整组替换写 REPLACE_TAG；勾选同步时把「这次新增的标签」补到包内主素材
    if payload.tags is not None:
        from app.services.tags import normalize_tags, set_tags

        old_tags = normalize_tags(list(pkg.tags or []))
        set_tags(
            db,
            target=pkg,
            tags=payload.tags,
            actor=payload.designerName or pkg.responsible_name or pkg.created_by,
            action="REPLACE_TAG",
            design_package_id=pkg.id,
        )
        if payload.syncTagsToMaterials:
            new_tags = [t for t in normalize_tags(payload.tags) if t not in old_tags]
            if new_tags:
                _sync_new_tags_to_materials(db, pkg, new_tags, payload.designerName or pkg.responsible_name)
    if payload.remark is not None:
        pkg.remark = payload.remark
    pkg.updated_at = utcnow()
    db.commit()
    db.refresh(pkg)
    return to_package_dto(db, pkg)


@router.get("/design-packages", response_model=list[DesignPackageDTO], summary="设计包列表")
def list_design_packages(
    include_archived: bool = Query(default=False, alias="includeArchived"),
    with_batch: bool = Query(
        default=False,
        alias="withBatch",
        description="只返回已经生成过版本（V1 及以上）的设计包；未确认整包生成的草稿不返回",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[DesignPackageDTO]:

    stmt = select(DesignPackage)
    if with_batch:
        # 素材中心只认「已经确认整包并生成过版本」的设计包：
        # 只上传、还没生成 V1 的草稿不能出现在前端素材列表里。
        stmt = stmt.join(
            DerivativeBatch, DerivativeBatch.design_package_id == DesignPackage.id
        ).distinct()
    stmt = stmt.order_by(DesignPackage.created_at.desc()).limit(limit).offset(offset)
    if not include_archived:
        stmt = stmt.where(DesignPackage.archived_at.is_(None))
    return [to_package_dto(db, pkg) for pkg in db.execute(stmt).scalars()]


@router.delete(
    "/design-packages/{design_package_id}",
    response_model=DesignPackageDTO,
    summary="删除设计包（软删除 / 归档：列表与素材中心不再显示，数据保留可恢复）",
)
def delete_design_package(
    design_package_id: str,
    actor: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> DesignPackageDTO:
    pkg = db.get(DesignPackage, design_package_id)
    if pkg is None:
        raise DesignPackageNotFound()

    # 软删除：只打归档时间戳。
    # 绝不物理删除 —— 主素材 MAT、已上传文件、维护记录都要留着，
    # 而且同一份文件可能被别的设计包复用。
    if pkg.archived_at is None:
        pkg.archived_at = utcnow()
        pkg.updated_at = utcnow()
        write_log(
            db,
            design_package_id=pkg.id,
            target_type="DESIGN_PACKAGE",
            target_id=pkg.id,
            actor=(actor or pkg.created_by or DEFAULT_ACTOR).strip(),
            action="ARCHIVE_PACKAGE",
            summary=f"删除（归档）设计包「{pkg.name}」（设计编码 {pkg.design_code}）",
            after_value=pkg.archived_at.isoformat(),
        )
        db.commit()
        db.refresh(pkg)
    return to_package_dto(db, pkg)


@router.post(
    "/design-packages/{design_package_id}/restore",
    response_model=DesignPackageDTO,
    summary="恢复被删除（归档）的设计包",
)
def restore_design_package(
    design_package_id: str,
    actor: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> DesignPackageDTO:
    pkg = db.get(DesignPackage, design_package_id)
    if pkg is None:
        raise DesignPackageNotFound()

    if pkg.archived_at is not None:
        before = pkg.archived_at.isoformat()
        pkg.archived_at = None
        pkg.updated_at = utcnow()
        write_log(
            db,
            design_package_id=pkg.id,
            target_type="DESIGN_PACKAGE",
            target_id=pkg.id,
            actor=(actor or pkg.created_by or DEFAULT_ACTOR).strip(),
            action="RESTORE_PACKAGE",
            summary=f"恢复设计包「{pkg.name}」",
            before_value=before,
        )
        db.commit()
        db.refresh(pkg)
    return to_package_dto(db, pkg)


@router.get(
    "/design-packages/{design_package_id}",
    response_model=DesignPackageDTO,
    summary="设计包详情",
)
def get_design_package(
    design_package_id: str,
    db: Session = Depends(get_db),
) -> DesignPackageDTO:
    pkg = db.get(DesignPackage, design_package_id)
    if pkg is None:
        raise DesignPackageNotFound()
    return to_package_dto(db, pkg)


@router.get(
    "/design-packages/{design_package_id}/overview",
    response_model=PackageOverviewResponse,
    summary="上传页整包视图（聚合 DTO）",
)
def get_package_overview(
    design_package_id: str,
    db: Session = Depends(get_db),
) -> PackageOverviewResponse:
    return build_package_overview(db, design_package_id)


@router.get(
    "/design-packages/{design_package_id}/logs",
    summary="设计包维护记录",
)
def get_package_logs(
    design_package_id: str,
    db: Session = Depends(get_db),
) -> list[dict]:

    if db.get(DesignPackage, design_package_id) is None:
        raise DesignPackageNotFound()
    logs = db.execute(
        select(ActivityLog)
        .where(ActivityLog.design_package_id == design_package_id)
        .order_by(ActivityLog.created_at.asc())
    ).scalars()
    return [to_activity_log_dto(log).model_dump(mode="json") for log in logs]


# ---------------------------------------------------------------- 创建上传记录（幂等）


@router.post(
    "/design-packages/{design_package_id}/uploads",
    response_model=CreateUploadResponse,
    summary="创建上传记录 + 上传会话（Idempotency-Key / uploadSessionId 幂等）",
)
def create_package_upload(
    design_package_id: str,
    payload: CreateUploadRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> CreateUploadResponse:
    pkg = db.get(DesignPackage, design_package_id)
    if pkg is None:
        raise DesignPackageNotFound()
    if pkg.archived_at is not None:
        raise ArchivedPackageError()

    session_key = (payload.uploadSessionId or idempotency_key or "").strip() or None
    actor = payload.uploaderName or DEFAULT_ACTOR

    # 幂等：命中已有 uploadSessionId 直接返回，不重复创建
    if session_key:
        existing_upload = find_upload_by_session_key(db, design_package_id, session_key)
        if existing_upload is not None:
            from app.services.repository import get_session_by_upload

            existing_session = get_session_by_upload(db, existing_upload.id)
            write_log(
                db,
                design_package_id=design_package_id,
                target_type="UPLOAD",
                target_id=existing_upload.id,
                actor=actor,
                action="RESUME_UPLOAD_SESSION",
                summary=f"恢复上传会话（幂等命中 {session_key}）",
            )
            db.commit()
            return CreateUploadResponse(
                packageUpload=to_upload_dto(existing_upload),
                session=to_session_dto(existing_session) if existing_session else None,  # type: ignore[arg-type]
                designPackage=to_package_dto(db, pkg),
                created=False,
            )

    from app.db.models import PackageUpload, UploadSession

    upload_id = new_id("upload")
    session_id = new_id("session")
    session_key = session_key or new_id("uploadkey")

    upload = PackageUpload(
        id=upload_id,
        design_package_id=design_package_id,
        original_package_name=payload.originalPackageName,
        file_size=payload.fileSize,
        upload_session_id=session_key,
        # 实际上传人：没有登录系统时只有姓名，uploader_id 允许为空
        uploader_id=(payload.uploaderId or "").strip() or None,
        uploader_name=actor,
        upload_type=payload.uploadType,
        target_batch_id=payload.targetBatchId,
        status="PARSING",
        remark=payload.remark,
    )
    session = UploadSession(
        id=session_id,
        design_package_id=design_package_id,
        package_upload_id=upload_id,
        original_package_name=payload.originalPackageName,
        file_size=payload.fileSize or 0,
        stage="PARSING",
        upload_type=payload.uploadType,
        operator_id=payload.operatorId,
        operator_name=payload.operatorName,
        remark=payload.remark,
        received_files=[],
    )
    db.add(upload)
    db.add(session)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # 并发下同一 session_key 被抢先创建：回读返回
        existing_upload = find_upload_by_session_key(db, design_package_id, session_key)
        if existing_upload is not None:
            from app.services.repository import get_session_by_upload

            existing_session = get_session_by_upload(db, existing_upload.id)
            return CreateUploadResponse(
                packageUpload=to_upload_dto(existing_upload),
                session=to_session_dto(existing_session) if existing_session else None,  # type: ignore[arg-type]
                designPackage=to_package_dto(db, pkg),
                created=False,
            )
        raise ConflictError(f"上传记录创建冲突：{exc}") from exc

    write_log(
        db,
        design_package_id=design_package_id,
        target_type="UPLOAD",
        target_id=upload_id,
        actor=actor,
        action="UPLOAD_PACKAGE",
        summary=f"上传原始文件包 {payload.originalPackageName}（{payload.uploadType}）",
    )
    db.commit()
    db.refresh(upload)
    db.refresh(session)

    return CreateUploadResponse(
        packageUpload=to_upload_dto(upload),
        session=to_session_dto(session),
        designPackage=to_package_dto(db, pkg),
        created=True,
    )


@router.get(
    "/design-packages/{design_package_id}/uploads",
    summary="设计包上传历史",
)
def list_package_uploads(
    design_package_id: str,
    db: Session = Depends(get_db),
) -> list[dict]:
    if db.get(DesignPackage, design_package_id) is None:
        raise DesignPackageNotFound()
    return [
        to_upload_dto(u).model_dump(mode="json") for u in list_uploads(db, design_package_id)
    ]


def _sync_new_tags_to_materials(db: Session, pkg: DesignPackage, new_tags: list[str], actor: str) -> None:
    """把「这次新增的设计包标签」补到包内全部主素材（副素材不碰——建版时已继承）。

    这是用户主动选择的同步（勾选「同步新增标签到包内素材」），
    不是修改设计包标签时的自动覆盖。
    """
    from app.services.tags import normalize_tags, set_tags

    material_ids = [
        row[0]
        for row in db.execute(
            select(DesignPackageMaterial.material_id).where(
                DesignPackageMaterial.design_package_id == pkg.id
            )
        ).all()
    ]
    if not material_ids:
        return
    materials = db.execute(select(Material).where(Material.id.in_(material_ids))).scalars().all()
    for material in materials:
        current = normalize_tags(list(material.tags or []))
        merged = normalize_tags([*current, *new_tags])
        if merged != current:
            set_tags(
                db,
                target=material,
                tags=merged,
                actor=actor,
                action="BATCH_ADD_TAG",
                design_package_id=pkg.id,
            )


@router.get("/activity-logs", summary="按业务目标查询维护记录")
def query_activity_logs(
    target_type: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    design_package_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:

    stmt = select(ActivityLog)
    if design_package_id:
        stmt = stmt.where(ActivityLog.design_package_id == design_package_id)
    if target_type:
        stmt = stmt.where(ActivityLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(ActivityLog.target_id == target_id)
    logs = db.execute(stmt.order_by(ActivityLog.created_at.asc())).scalars()
    return [to_activity_log_dto(log).model_dump(mode="json") for log in logs]


__all__ = ["router", "write_log", "utcnow"]
