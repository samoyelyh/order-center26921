# ============================================================================
# 标签 / 负责人 / 流转记录
#
# 规则（本轮定稿）：
#   标签存在三处：design_packages.tags / materials.tags / material_variants.tags
#   继承只发生在「创建那一刻」（建包 → 主素材 → 副素材 建版），之后各自独立修改，
#   修改设计包标签**不会**自动覆盖包内素材；用户主动点「同步新增标签到包内素材」才同步。
#
#   所有标签与负责人的修改都写 ActivityLog（统一维护记录）：
#   CHANGE_RESPONSIBLE / ADD_TAG / REMOVE_TAG / REPLACE_TAG / BATCH_ADD_TAG / BATCH_REMOVE_TAG，
#   带 before/after/actor/created_at，查询用现有 target_type/target_id/design_package_id。
# ============================================================================

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    MaterialNotFound,
    ValidationError,
    VariantNotFound,
)
from app.db.models import (
    ActivityLog,
    Asset,
    DesignPackage,
    DesignPackageMaterial,
    DerivativeBatch,
    Material,
    MaterialVariant,
    VariantRevision,
)
from app.schemas.mappers import asset_content_url
from app.services.activity import write_log
from app.services.repository import utcnow

# ---------------------------------------------------------------- 标签基元

MAX_TAGS = 32
MAX_TAG_LENGTH = 32


def normalize_tags(raw: list[str] | None) -> list[str]:
    """去空白、去重、保序。空输入 → []。"""
    if not raw:
        return []
    seen: dict[str, None] = {}
    for item in raw:
        tag = str(item).strip()
        if tag:
            seen[tag] = None
    return list(seen)


def _validate_tags(tags: list[str]) -> None:
    if len(tags) > MAX_TAGS:
        raise ValidationError(f"标签最多 {MAX_TAGS} 个")
    for tag in tags:
        if len(tag) > MAX_TAG_LENGTH:
            raise ValidationError(f"单个标签最多 {MAX_TAG_LENGTH} 个字符：{tag}")


def set_tags(
    db: Session,
    *,
    target,
    tags: list[str],
    actor: str,
    action: str,
    design_package_id: str | None,
) -> list[str]:
    """整组替换 target.tags 并写一条 ActivityLog（before/after 都用 JSON 数组字符串）。"""
    import json

    clean = normalize_tags(tags)
    _validate_tags(clean)
    before = normalize_tags(list(target.tags or []))
    if clean == before:
        return clean
    target.tags = clean
    target.updated_at = utcnow()
    write_log(
        db,
        design_package_id=design_package_id,
        target_type=_target_type_of(target),
        target_id=_target_id_of(target),
        actor=actor,
        action=action,
        summary=f"标签调整：{_target_label(target)}",
        before_value=json.dumps(before, ensure_ascii=False),
        after_value=json.dumps(clean, ensure_ascii=False),
    )
    return clean


def _target_type_of(target) -> str:
    if isinstance(target, Material):
        return "MATERIAL"
    if isinstance(target, MaterialVariant):
        return "MATERIAL_VARIANT"
    return "DESIGN_PACKAGE"


def _target_id_of(target) -> str:
    return target.id


def _target_label(target) -> str:
    if isinstance(target, Material):
        return f"主素材 {target.material_code}"
    if isinstance(target, MaterialVariant):
        return f"副素材 {target.display_code}"
    return f"设计包「{target.name}」"


# ---------------------------------------------------------------- 查询


def get_material_or_404(db: Session, material_code: str) -> Material:
    material = db.execute(
        select(Material).where(Material.material_code == material_code)
    ).scalar_one_or_none()
    if material is None:
        raise MaterialNotFound(f"主素材不存在：{material_code}")
    return material


def get_variant_or_404(db: Session, variant_id: str) -> MaterialVariant:
    variant = db.execute(
        select(MaterialVariant).where(MaterialVariant.id == variant_id)
    ).scalar_one_or_none()
    if variant is None:
        raise VariantNotFound(f"副素材不存在：{variant_id}")
    return variant


def list_distinct_tags(db: Session) -> list[dict]:
    """全库去重标签（素材中心选择器用），带每个标签出现的素材数。"""
    counts: dict[str, int] = {}
    for cls, column in (
        (DesignPackage, DesignPackage.tags),
        (Material, Material.tags),
        (MaterialVariant, MaterialVariant.tags),
    ):
        rows = db.execute(select(column).where(column.is_not(None))).scalars()
        for row in rows:
            for tag in row or []:
                counts[str(tag)] = counts.get(str(tag), 0) + 1
    return [{"tag": tag, "count": counts[tag]} for tag in sorted(counts)]


# ---------------------------------------------------------------- 流转记录


def _activity_dto(row: ActivityLog) -> dict:
    return {
        "id": row.id,
        "designPackageId": row.design_package_id,
        "targetType": row.target_type,
        "targetId": row.target_id,
        "action": row.action,
        "actor": row.actor,
        "summary": row.summary,
        "before": row.before_value,
        "after": row.after_value,
        "createdAt": row.created_at.isoformat(),
    }


def _package_history(db: Session, design_package_id: str) -> list[ActivityLog]:
    return list(
        db.execute(
            select(ActivityLog)
            .where(ActivityLog.design_package_id == design_package_id)
            .order_by(ActivityLog.created_at.asc())
        ).scalars()
    )


def material_history(db: Session, material_code: str) -> list[dict]:
    """主素材流转记录 = MAT 自身事件 + 引用它的设计包的关键事件。"""
    material = get_material_or_404(db, material_code)

    own = db.execute(
        select(ActivityLog).where(
            ActivityLog.target_type == "MATERIAL",
            ActivityLog.target_id == material.id,
        )
    ).scalars().all()

    package_ids = [
        row[0]
        for row in db.execute(
            select(DesignPackageMaterial.design_package_id).where(
                DesignPackageMaterial.material_id == material.id
            )
        ).all()
    ]

    seen_ids = {row.id for row in own}
    merged = list(own)
    for pkg_id in package_ids:
        for log in _package_history(db, pkg_id):
            if log.id in seen_ids:
                continue
            seen_ids.add(log.id)
            merged.append(log)
    merged.sort(key=lambda log: (log.created_at, log.id))
    return [_activity_dto(row) for row in merged]


def variant_history(db: Session, variant_id: str) -> list[dict]:
    """副素材流转记录 = 该副素材自身的事件。"""
    variant = get_variant_or_404(db, variant_id)
    rows = db.execute(
        select(ActivityLog).where(
            ActivityLog.target_type == "MATERIAL_VARIANT",
            ActivityLog.target_id == variant.id,
        )
    ).scalars().all()
    rows.sort(key=lambda log: (log.created_at, log.id))
    return [_activity_dto(row) for row in rows]


def package_history(db: Session, design_package_id: str) -> list[dict]:
    rows = _package_history(db, design_package_id)
    return [_activity_dto(row) for row in rows]


def latest_activity_summary(db: Session, *, target_type: str, target_id: str) -> dict | None:
    """基本信息卡片上的「最近流转」：最近一次流转记录（最近一条）。"""
    row = db.execute(
        select(ActivityLog)
        .where(ActivityLog.target_type == target_type, ActivityLog.target_id == target_id)
        .order_by(ActivityLog.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _activity_dto(row) if row else None


# ---------------------------------------------------------------- 副素材详情


def get_variant_detail(db: Session, variant_id: str) -> dict:
    variant = get_variant_or_404(db, variant_id)
    material = db.get(Material, variant.material_id)
    position = db.get(DesignPackageMaterial, variant.design_package_material_id)
    batch = db.get(DerivativeBatch, variant.batch_id)
    pkg = db.get(DesignPackage, position.design_package_id) if position else None
    revision = db.get(VariantRevision, variant.current_revision_id) if variant.current_revision_id else None
    asset = db.get(Asset, revision.asset_id) if revision else None

    return {
        "id": variant.id,
        "displayCode": variant.display_code,
        "materialId": variant.material_id,
        "materialCode": material.material_code if material else "",
        "materialName": material.name if material else "",
        "designPackageId": pkg.id if pkg else None,
        "designPackageName": pkg.name if pkg else None,
        "designPackageCode": pkg.code if pkg else None,
        "designCode": pkg.design_code if pkg else None,
        "responsibleName": pkg.responsible_name if pkg else None,
        "position": position.position if position else 0,
        "batchId": batch.id if batch else None,
        "batchCode": batch.code if batch else None,
        "versionNo": batch.version_no if batch else None,
        "currentRevisionNo": revision.revision_no if revision else None,
        "assetId": asset.id if asset else None,
        "previewUrl": asset_content_url(asset.id) if asset else None,
        "originalFilename": asset.original_filename if asset else None,
        "tags": normalize_tags(list(variant.tags or [])),
        "createdAt": variant.created_at.isoformat(),
        "deleted": variant.deleted,
    }
