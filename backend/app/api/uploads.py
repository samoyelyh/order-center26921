# ============================================================================
# 上传 / Asset API（Phase 1）
#
# POST /api/uploads/{upload_id}/files
#   1. 校验会话与文件类型
#   2. 读字节 → BLAKE3 → pHash → 宽高
#   3. 命中相同 BLAKE3 则复用已有 Asset（不新建）
#   4. 否则写对象存储 → 建 Asset（失败则整体回滚，不落 DB）
#   5. **无论新建还是复用**，都在 package_upload_assets 新建一条关联行
#      （第5/6条：Asset 不再持有 upload_session_id，归属改为多对多，
#        复用已有 Asset 时绝不破坏它原来的上传归属）
#
# GET  /api/uploads/{upload_id}/assets?role=VARIANT   本次上传交付的文件
# GET  /api/assets/{asset_id}                           Asset 元数据
# GET  /api/assets/{asset_id}/content                   Asset 内容代理（浏览器出图）
# ============================================================================

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    AssetNotFound,
    DuplicatePairKey,
    EmptyFileError,
    FileTooLarge,
    HashComputeError,
    StorageReadError,
    StorageWriteError,
    UnsupportedFileType,
    UploadNotFound,
    UploadSessionStateError,
    ValidationError,
)
from app.db.models import Asset, PackageUpload, PackageUploadAsset, UploadSession
from app.db.session import get_db
from app.schemas.dto import (
    AssetDTO,
    PackageUploadDTO,
    PackageUploadPatchRequest,
    UploadedFileDTO,
    UploadFileResponse,
    UploadSessionDTO,
    UploadSessionPatchRequest,
)
from app.schemas.mappers import (
    asset_uri,
    to_asset_dto,
    to_session_dto,
    to_upload_dto,
    to_uploaded_file_dto,
)
from app.services.activity import write_log
from app.services.files import ALL_FILE_ROLES, normalize_role, pair_key_of, position_hint_of
from app.services.fingerprint import (
    PHASH_VERSION,
    compute_blake3,
    compute_phash,
    extract_image_metadata,
    is_image_mime,
    phash_to_hex,
)
from app.services.repository import (
    find_asset_by_blake3,
    get_session_by_upload,
    link_upload_asset,
    list_assets_by_ids,
    list_upload_asset_links,
    new_id,
    utcnow,
)
from app.services.storage import StorageError, build_storage_key, get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["uploads"])

DESIGN_MIME_TYPES = {
    "application/vnd.adobe.photoshop",
    "image/vnd.adobe.photoshop",
    "image/x-photoshop",
    "application/x-photoshop",
}


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _classify(filename: str, content_type: str | None) -> tuple[str, str]:
    """返回 (kind, mime_type)。kind ∈ IMAGE / DESIGN。"""
    ext = _extension(filename)
    if ext in settings.allowed_design_ext:
        return "DESIGN", "image/vnd.adobe.photoshop"
    if ext in settings.allowed_image_ext:
        mime = content_type if content_type and content_type.startswith("image/") else None
        if ext in {"jpg", "jpeg"}:
            mime = mime or "image/jpeg"
        elif ext == "png":
            mime = mime or "image/png"
        elif ext == "webp":
            mime = mime or "image/webp"
        else:
            mime = mime or "application/octet-stream"
        return "IMAGE", mime
    raise UnsupportedFileType(
        f"不支持的文件格式：{filename}（支持 {', '.join(settings.allowed_image_ext + settings.allowed_design_ext)}）"
    )


def _load_upload_context(db: Session, upload_id: str) -> tuple[PackageUpload, UploadSession | None]:
    upload = db.get(PackageUpload, upload_id)
    if upload is None:
        raise UploadNotFound(f"上传记录不存在：{upload_id}")
    session = db.execute(
        __import__("sqlalchemy").select(UploadSession).where(UploadSession.package_upload_id == upload_id)
    ).scalar_one_or_none()
    if session is not None and session.stage in {"SUBMITTED", "FAILED"}:
        raise UploadSessionStateError(f"上传会话状态为 {session.stage}，不能继续上传文件")
    return upload, session


def _ensure_pair_key_available(
    db: Session, upload_id: str, file_role: str, pair_key: str, filename: str
) -> None:
    """
    同一次上传里，同一角色下同一个 pairKey 只能有一个文件。

    重复就是异常（第四条第 3 项），必须在**上传时**就明确告诉用户，
    而不是等到配对阶段才发现两个文件抢同一个位置。
    """
    clash = db.execute(
        select(PackageUploadAsset).where(
            PackageUploadAsset.package_upload_id == upload_id,
            PackageUploadAsset.file_role == file_role,
            PackageUploadAsset.pair_key == pair_key,
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise DuplicatePairKey(
            f"同名重复：本次上传的{_role_label(file_role)}里已经有一个 pairKey 为「{pair_key}」的文件"
            f"（{clash.original_filename}），不能再上传 {filename}。请改用不同的文件名。"
        )


def _role_label(file_role: str) -> str:
    return {
        "MAIN_PREVIEW": "主素材",
        "PSD": "PSD",
        "VARIANT": "副素材",
        "OTHER": "其他文件",
    }.get(file_role, file_role)


@router.post(
    "/uploads/{upload_id}/files",
    response_model=UploadFileResponse,
    summary="上传单个文件并生成 Asset（BLAKE3 完全文件去重 + pairKey 记录）",
)
async def upload_file(
    upload_id: str,
    file: UploadFile = File(..., description="图片或 PSD 文件"),
    kind: str | None = Form(default=None, description="MAIN / VARIANT / PSD，仅记录用途"),
    fileRole: str | None = Form(  # noqa: N803 - 前端字段名
        default=None,
        description="主素材 MAIN_PREVIEW / PSD / 副图 VARIANT / 其他 OTHER；不传则按文件名推断",
    ),
    db: Session = Depends(get_db),
) -> UploadFileResponse:
    upload, session = _load_upload_context(db, upload_id)
    actor = upload.uploader_name

    filename = file.filename or "unnamed"
    file_kind, mime_type = _classify(filename, file.content_type)
    if kind == "PSD" and file_kind != "DESIGN":
        raise UnsupportedFileType("PSD 槽位只接受 .psd / .psb 文件")

    try:
        role = normalize_role(fileRole, filename)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if role == "PSD" and file_kind != "DESIGN":
        raise UnsupportedFileType("PSD 角色只接受 .psd / .psb 文件")

    # 同名配对键：主图 / PSD / 副图靠它配到一起（不再使用任何相似度判断）
    pair_key = pair_key_of(filename)
    if not pair_key:
        raise ValidationError(f"无法从文件名解析出同名配对键：{filename}")
    _ensure_pair_key_available(db, upload_id, role, pair_key, filename)

    try:
        data = await file.read()
    except OSError as exc:
        raise StorageWriteError(f"读取上传内容失败：{exc}") from exc

    if not data:
        raise EmptyFileError(f"文件内容为空：{filename}")
    if len(data) > settings.max_upload_size_bytes:
        raise FileTooLarge(
            f"文件超过大小限制（{len(data)} > {settings.max_upload_size_bytes} 字节）：{filename}"
        )

    try:
        blake3_hex = compute_blake3(data)
    except Exception as exc:  # noqa: BLE001
        raise HashComputeError(f"BLAKE3 计算失败：{filename}（{exc}）") from exc

    storage = get_storage()
    position_hint = position_hint_of(filename)
    existing = find_asset_by_blake3(db, blake3_hex)

    if existing is not None:
        # 完全相同文件：复用已有 Asset，不新建、不重复占存储。
        # 归属关系只新增 package_upload_assets 行 —— 绝不改写已有上传的归属。
        link = link_upload_asset(
            db,
            package_upload_id=upload.id,
            asset_id=existing.id,
            file_role=role,
            original_filename=filename,
            pair_key=pair_key,
            position_hint=position_hint,
        )
        if session is not None:
            received = list(session.received_files or [])
            if filename not in received:
                received.append(filename)
                session.received_files = received
                session.stage = "PARSED"
                session.updated_at = utcnow()
        write_log(
            db,
            design_package_id=upload.design_package_id,
            target_type="ASSET",
            target_id=existing.id,
            actor=actor,
            action="REUSE_ASSET",
            summary=(
                f"文件 {filename} 与已有资产 {existing.original_filename} BLAKE3 完全相同，"
                f"复用该资产并以 {role} 角色关联到本次上传"
            ),
            after_value=blake3_hex,
        )
        db.commit()
        db.refresh(existing)
        return UploadFileResponse(
            assetId=existing.id,
            originalFilename=existing.original_filename,
            sizeBytes=existing.size_bytes,
            mimeType=existing.mime_type,
            width=existing.width,
            height=existing.height,
            blake3=existing.blake3,
            phash=phash_to_hex(existing.phash),
            phashVersion=existing.phash_version,
            storageKey=existing.storage_key,
            uri=asset_uri(existing),
            reused=True,
            kind="DESIGN" if existing.mime_type in DESIGN_MIME_TYPES else "IMAGE",
            fileRole=role,
            pairKey=pair_key,
            linkId=link.id,
        )

    # 新文件：先落对象存储，再写 DB。存储失败直接抛错，DB 不留脏数据。
    storage_key = build_storage_key(filename)
    try:
        storage.put(storage_key, data, mime_type)
    except StorageError as exc:
        write_log(
            db,
            design_package_id=upload.design_package_id,
            target_type="UPLOAD",
            target_id=upload.id,
            actor=actor,
            action="UPLOAD_FAILED",
            summary=f"文件 {filename} 写入对象存储失败，已终止入库",
            after_value=str(exc),
        )
        db.commit()
        raise StorageWriteError(str(exc)) from exc

    phash_bytes = None
    width = height = None
    if file_kind == "IMAGE" or is_image_mime(mime_type):
        phash_bytes = compute_phash(data)
        width, height = extract_image_metadata(data)

    asset = Asset(
        id=new_id("asset"),
        storage_key=storage_key,
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        width=width,
        height=height,
        # 约束：blake3 与 phash 必须同时有或同时无
        blake3=blake3_hex if phash_bytes is not None else None,
        phash=phash_bytes,
        phash_version=PHASH_VERSION if phash_bytes is not None else None,
        created_by=actor,
    )
    db.add(asset)
    db.flush()

    # 上传归属写进关联表（Asset 本身不再记 upload_session_id）
    link = link_upload_asset(
        db,
        package_upload_id=upload.id,
        asset_id=asset.id,
        file_role=role,
        original_filename=filename,
        pair_key=pair_key,
        position_hint=position_hint,
    )

    if session is not None:
        received = list(session.received_files or [])
        if filename not in received:
            received.append(filename)
            session.received_files = received
            session.stage = "PARSED"
            session.updated_at = utcnow()

    write_log(
        db,
        design_package_id=upload.design_package_id,
        target_type="ASSET",
        target_id=asset.id,
        actor=actor,
        action="CREATE_ASSET",
        summary=f"上传文件 {filename} → Asset（{role}, {mime_type}, {len(data)} 字节）",
        after_value=blake3_hex,
    )
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # DB 写失败时回滚已上传对象，避免孤儿文件
        try:
            storage.delete(storage_key)
        except StorageError:
            logger.exception("回滚对象存储失败：%s", storage_key)
        raise HashComputeError(f"Asset 入库失败：{exc}") from exc

    db.refresh(asset)

    # ---- 图片搜索向量：入库后自动生成并写入索引（失败不阻断上传） ----
    if file_kind == "IMAGE" or is_image_mime(mime_type):
        try:
            from app.services.image_search import index_asset

            index_asset(db, asset)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            # 索引失败只记录，不影响素材入库事务（用户要求：索引失败不导致整体失败）
            try:
                write_log(
                    db,
                    design_package_id=upload.design_package_id,
                    target_type="ASSET",
                    target_id=asset.id,
                    actor=actor,
                    action="IMAGE_INDEX_FAILED",
                    summary=f"图片搜索向量生成失败：{filename}（{exc}）",
                )
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()

    return UploadFileResponse(
        assetId=asset.id,
        originalFilename=asset.original_filename,
        sizeBytes=asset.size_bytes,
        mimeType=asset.mime_type,
        width=asset.width,
        height=asset.height,
        # 与落库值保持一致：PSD 等不可解码文件 blake3/phash 同时为 null
        blake3=asset.blake3,
        phash=phash_to_hex(asset.phash),
        phashVersion=asset.phash_version,
        storageKey=asset.storage_key,
        uri=asset_uri(asset),
        reused=False,
        kind=file_kind,
        fileRole=role,
        pairKey=pair_key,
        linkId=link.id,
    )


@router.get(
    "/uploads/{upload_id}/assets",
    response_model=list[UploadedFileDTO],
    summary="查询某次上传交付的文件（可按角色过滤）",
)
def list_upload_files(
    upload_id: str,
    role: str | None = Query(default=None, description="MAIN_PREVIEW / PSD / VARIANT / OTHER"),
    db: Session = Depends(get_db),
) -> list[UploadedFileDTO]:
    """
    第8条：整包视图之外，前端还可以按角色拉取某次上传的文件。
    例：GET /api/uploads/{uploadId}/assets?role=VARIANT → 本次上传的副图。
    """
    upload = db.get(PackageUpload, upload_id)
    if upload is None:
        raise UploadNotFound(f"上传记录不存在：{upload_id}")

    normalized: str | None = None
    if role:
        candidate = role.strip().upper()
        if candidate not in ALL_FILE_ROLES:
            raise ValidationError(f"未知的文件角色：{role}（可选 {', '.join(ALL_FILE_ROLES)}）")
        normalized = candidate

    links = list_upload_asset_links(db, package_upload_ids=[upload_id], file_role=normalized)
    assets = list_assets_by_ids(db, [link.asset_id for link in links])
    return [to_uploaded_file_dto(link, assets.get(link.asset_id)) for link in links]


@router.get("/assets/{asset_id}", response_model=AssetDTO, summary="查询 Asset")
def get_asset(asset_id: str, db: Session = Depends(get_db)) -> AssetDTO:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFound()
    return to_asset_dto(asset)


@router.get(
    "/assets/{asset_id}/content",
    summary="读取 Asset 内容（后端代理出图）",
    response_class=Response,
)
def get_asset_content(
    asset_id: str,
    download: int = Query(default=0, description="1=作为附件下载"),
    db: Session = Depends(get_db),
) -> Response:
    """
    第7条：前端 <img src> 用这个地址，而不是 MinIO/本地 storage_key。
    好处：本地回退与 MinIO 同一地址、不需要桶公共读、不泄露内网 endpoint。
    """
    asset = db.get(Asset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise AssetNotFound()

    try:
        data, _stored_mime = get_storage().get(asset.storage_key)
    except StorageError as exc:
        raise StorageReadError(f"读取文件内容失败：{asset.original_filename}（{exc}）") from exc

    headers = {
        # Asset 内容不可变（storage_key 唯一且按内容复用），可长期缓存
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{asset.id}{_ext_suffix(asset.storage_key)}"'
    return Response(content=data, media_type=asset.mime_type, headers=headers)


def _ext_suffix(storage_key: str) -> str:
    return f".{storage_key.rsplit('.', 1)[-1]}" if "." in storage_key else ""


@router.get("/upload-sessions/{session_id}", response_model=UploadSessionDTO, summary="查询上传会话")
def get_upload_session(session_id: str, db: Session = Depends(get_db)) -> UploadSessionDTO:
    from app.core.errors import UploadSessionNotFound

    session = db.get(UploadSession, session_id)
    if session is None:
        raise UploadSessionNotFound()
    return to_session_dto(session)


@router.patch("/upload-sessions/{session_id}", response_model=UploadSessionDTO, summary="更新上传会话")
def patch_upload_session(
    session_id: str,
    payload: UploadSessionPatchRequest,
    db: Session = Depends(get_db),
) -> UploadSessionDTO:
    from app.core.errors import UploadSessionNotFound

    session = db.get(UploadSession, session_id)
    if session is None:
        raise UploadSessionNotFound()

    if payload.operatorName is not None:
        # 第7条：归属运营允许是自由文本。
        # 有对应 userId 就一起存，没有就把 operator_id 置空 —— 绝不因为没有 ID 拒绝保存。
        session.operator_name = payload.operatorName.strip() or None
        session.operator_id = (payload.operatorId or "").strip() or None
    elif payload.operatorId is not None:
        session.operator_id = payload.operatorId.strip() or None
    if payload.remark is not None:
        session.remark = payload.remark
    if payload.stage is not None:
        session.stage = payload.stage
    session.updated_at = utcnow()

    upload = db.get(PackageUpload, session.package_upload_id) if session.package_upload_id else None
    if upload is not None:
        if payload.remark is not None:
            upload.remark = payload.remark
        upload.updated_at = utcnow()

    write_log(
        db,
        design_package_id=session.design_package_id,
        target_type="UPLOAD",
        target_id=session.package_upload_id or session.id,
        actor=session.operator_name or session.id,
        action="UPDATE_UPLOAD_SESSION",
        summary=f"更新上传会话（stage={session.stage}, operator={session.operator_name}）",
    )
    db.commit()
    db.refresh(session)
    return to_session_dto(session)


@router.patch("/uploads/{upload_id}", response_model=PackageUploadDTO, summary="更新上传记录（美工/运营/备注）")
def patch_upload(
    upload_id: str,
    payload: PackageUploadPatchRequest,
    db: Session = Depends(get_db),
) -> PackageUploadDTO:
    """
    第7条：美工（uploader_name）与归属运营（upload_sessions.operator_name）都允许手工填写。

    上传后仍可修改：美工写入 package_uploads.uploader_name，
    归属运营写入对应 upload_sessions.operator_id / operator_name。
    """
    upload = db.get(PackageUpload, upload_id)
    if upload is None:
        raise UploadNotFound(f"上传记录不存在：{upload_id}")

    if payload.uploaderName is not None:
        name = payload.uploaderName.strip()
        if not name:
            raise ValidationError("美工姓名不能为空")
        upload.uploader_name = name
    if payload.remark is not None:
        upload.remark = payload.remark
    upload.updated_at = utcnow()

    session = get_session_by_upload(db, upload_id)
    if session is not None and (payload.operatorName is not None or payload.operatorId is not None):
        if payload.operatorName is not None:
            session.operator_name = payload.operatorName.strip() or None
            session.operator_id = (payload.operatorId or "").strip() or None
        else:
            session.operator_id = (payload.operatorId or "").strip() or None
        session.updated_at = utcnow()

    write_log(
        db,
        design_package_id=upload.design_package_id,
        target_type="UPLOAD",
        target_id=upload.id,
        actor=upload.uploader_name,
        action="UPDATE_UPLOAD",
        summary=(
            f"更新上传记录（美工={upload.uploader_name}, "
            f"归属运营={session.operator_name if session else None}）"
        ),
    )
    db.commit()
    db.refresh(upload)
    return to_upload_dto(upload)


@router.get("/uploads/{upload_id}", summary="查询上传记录")
def get_upload(upload_id: str, db: Session = Depends(get_db)) -> dict:
    upload = db.get(PackageUpload, upload_id)
    if upload is None:
        raise UploadNotFound()
    return to_upload_dto(upload).model_dump(mode="json")
