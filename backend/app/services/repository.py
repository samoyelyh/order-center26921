# ============================================================================
# 仓储层：编码发放 + 幂等查询 + Asset 复用
#
# 关键并发安全点：
#   MAT 编码使用 code_sequences 行级锁（SELECT ... FOR UPDATE + UPDATE 自增），
#   绝不裸跑 SELECT MAX()+1。
# ============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    CodeSequence,
    DesignPackage,
    DesignPackageMaterial,
    Material,
    PackageUpload,
    PackageUploadAsset,
    UploadSession,
)
from app.core.errors import MaterialCodeConflict

MATERIAL_SEQUENCE = "material"
MATERIAL_CODE_PREFIX = "MAT"
MATERIAL_CODE_WIDTH = 6


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_material_code(seq: int) -> str:
    return f"{MATERIAL_CODE_PREFIX}-{seq:0{MATERIAL_CODE_WIDTH}d}"


def _ensure_sequence(db: Session, name: str) -> None:
    exists = db.execute(select(CodeSequence.name).where(CodeSequence.name == name)).scalar_one_or_none()
    if exists is None:
        db.add(CodeSequence(name=name, current_value=0))
        db.flush()


def allocate_material_codes(db: Session, count: int) -> list[str]:
    """
    并发安全地发放 count 个 MAT 编码。

    同一事务内多次调用会拿到连续区段；不同事务通过行级锁串行化。
    调用方必须在事务内使用（本函数会 flush，不 commit）。
    """
    if count <= 0:
        return []

    _ensure_sequence(db, MATERIAL_SEQUENCE)

    # 行级锁：SELECT ... FOR UPDATE
    row = db.execute(
        select(CodeSequence).where(CodeSequence.name == MATERIAL_SEQUENCE).with_for_update()
    ).scalar_one()

    start = row.current_value + 1
    end = start + count - 1
    db.execute(
        update(CodeSequence)
        .where(CodeSequence.name == MATERIAL_SEQUENCE)
        .values(current_value=end, updated_at=utcnow())
    )
    db.flush()
    return [format_material_code(seq) for seq in range(start, end + 1)]


def find_asset_by_blake3(db: Session, blake3: str) -> Asset | None:
    """按 BLAKE3 查未删除的已有 Asset（完全相同文件复用）。"""
    return db.execute(
        select(Asset)
        .where(Asset.blake3 == blake3, Asset.deleted_at.is_(None))
        .order_by(Asset.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def find_material_by_preview_asset(db: Session, asset_id: str) -> Material | None:
    """按预览 Asset 查已有 MAT（同一主素材再次上传时复用）。"""
    return db.execute(
        select(Material)
        .where(Material.preview_asset_id == asset_id, Material.archived_at.is_(None))
        .order_by(Material.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def find_upload_by_session_key(db: Session, design_package_id: str, session_key: str) -> PackageUpload | None:
    """幂等：同一 uploadSessionId 已创建过 PackageUpload 时直接返回。"""
    return db.execute(
        select(PackageUpload).where(
            PackageUpload.upload_session_id == session_key,
            PackageUpload.design_package_id == design_package_id,
        )
    ).scalar_one_or_none()


def get_position(db: Session, design_package_id: str, position: int) -> DesignPackageMaterial | None:
    return db.execute(
        select(DesignPackageMaterial).where(
            DesignPackageMaterial.design_package_id == design_package_id,
            DesignPackageMaterial.position == position,
        )
    ).scalar_one_or_none()


def list_positions(db: Session, design_package_id: str) -> list[DesignPackageMaterial]:
    return list(
        db.execute(
            select(DesignPackageMaterial)
            .where(DesignPackageMaterial.design_package_id == design_package_id)
            .order_by(DesignPackageMaterial.position.asc())
        ).scalars()
    )


def list_uploads(db: Session, design_package_id: str) -> list[PackageUpload]:
    return list(
        db.execute(
            select(PackageUpload)
            .where(PackageUpload.design_package_id == design_package_id)
            .order_by(PackageUpload.created_at.desc())
        ).scalars()
    )


def list_materials_by_ids(db: Session, material_ids: list[str]) -> dict[str, Material]:
    if not material_ids:
        return {}
    rows = db.execute(select(Material).where(Material.id.in_(material_ids))).scalars()
    return {row.id: row for row in rows}


def list_assets_by_ids(db: Session, asset_ids: list[str]) -> dict[str, Asset]:
    if not asset_ids:
        return {}
    rows = db.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars()
    return {row.id: row for row in rows}


def get_session_by_upload(db: Session, package_upload_id: str) -> UploadSession | None:
    return db.execute(
        select(UploadSession).where(UploadSession.package_upload_id == package_upload_id)
    ).scalar_one_or_none()


def link_upload_asset(
    db: Session,
    *,
    package_upload_id: str,
    asset_id: str,
    file_role: str,
    original_filename: str,
    pair_key: str = "",
    position_hint: int | None = None,
) -> PackageUploadAsset:
    """
    建立「这次上传交付了这个文件（角色 X，同名键 Y）」的关联。

    规则：
      - 只新增关联行，**绝不**修改已有上传的归属
      - 同一上传 + 同一角色 + 同一 pair_key 重复投递时复用已有行
        （uq_pua_upload_role_pairkey）
      - 同一份字节可以在同一上传里既当主图又当副图（角色不同 → 两行），
        也可以在不同 pair_key 下当两张副图（重复利用同一 Asset）
    调用方负责 commit。函数内部 flush，保证唯一约束冲突尽早暴露。
    """
    existing = db.execute(
        select(PackageUploadAsset).where(
            PackageUploadAsset.package_upload_id == package_upload_id,
            PackageUploadAsset.file_role == file_role,
            PackageUploadAsset.pair_key == pair_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        # 同一上传重复投递同一角色同一同名键：只刷新展示用的文件名/位置提示
        existing.asset_id = asset_id
        existing.original_filename = original_filename
        existing.position_hint = position_hint
        db.flush()
        return existing

    link = PackageUploadAsset(
        id=new_id("pua"),
        package_upload_id=package_upload_id,
        asset_id=asset_id,
        file_role=file_role,
        original_filename=original_filename,
        pair_key=pair_key,
        position_hint=position_hint,
    )
    db.add(link)
    db.flush()
    return link


def list_upload_asset_links(
    db: Session,
    *,
    package_upload_ids: list[str],
    file_role: str | None = None,
) -> list[PackageUploadAsset]:
    """按上传行为查询文件关联（可按角色过滤）。"""
    if not package_upload_ids:
        return []
    stmt = select(PackageUploadAsset).where(
        PackageUploadAsset.package_upload_id.in_(package_upload_ids)
    )
    if file_role:
        stmt = stmt.where(PackageUploadAsset.file_role == file_role)
    stmt = stmt.order_by(
        PackageUploadAsset.position_hint.asc(),
        PackageUploadAsset.original_filename.asc(),
        PackageUploadAsset.created_at.asc(),
    )
    return list(db.execute(stmt).scalars())


def get_package(db: Session, design_package_id: str) -> DesignPackage | None:
    return db.get(DesignPackage, design_package_id)


def generate_package_code(name: str) -> str:
    """设计包编码 DP-YYYYMMDD-<slug>。唯一约束兜底，冲突时由调用方重试。"""
    now = datetime.now(timezone.utc)
    slug = "".join(ch for ch in name if ch.isalnum())[:6].upper() or uuid.uuid4().hex[:4].upper()
    return f"DP-{now.year:04d}{now.month:02d}{now.day:02d}-{slug}-{uuid.uuid4().hex[:4].upper()}"


__all__ = [
    "MATERIAL_SEQUENCE",
    "MaterialCodeConflict",
    "allocate_material_codes",
    "find_asset_by_blake3",
    "find_material_by_preview_asset",
    "find_upload_by_session_key",
    "format_material_code",
    "generate_package_code",
    "get_package",
    "get_position",
    "get_session_by_upload",
    "link_upload_asset",
    "list_assets_by_ids",
    "list_materials_by_ids",
    "list_positions",
    "list_upload_asset_links",
    "list_uploads",
    "new_id",
    "utcnow",
]
