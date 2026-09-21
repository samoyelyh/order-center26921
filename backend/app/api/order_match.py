# ============================================================================
# 订单素材归因 API：自动匹配 / 人工审核 / 销量统计
#
# 审核动作写 order-center 自有的 OrderMatchAction（替代素材库 activity_logs）：
#   confirm  —— 确认当前/指定的 Variant（建 URL 绑定 + ASIN 绑定）
#   change   —— 更换到指定 Variant（同上）
#   unmatch  —— 无法识别 → FAILED
# 销量归因：仅 CONFIRMED 的 OrderItem.quantity 计入正式销量（最小单位）。
# 不直接引用素材库 ORM；Variant 的 material_id 经 MaterialPlatformClient 契约获取。
# ============================================================================

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AsinVariantBinding,
    MaterialUrlBinding,
    OrderImportBatch,
    OrderItem,
    OrderMatchAction,
)
from app.db.session import get_db
from app.orders.matcher import run_matcher
from app.services import material_platform as _mp
from app.services.repository import new_id

router = APIRouter(prefix="/order-imports", tags=["order-match"])

MATCH_STATUS = ("UNMATCHED", "REVIEW_REQUIRED", "CONFIRMED", "FAILED")


class MatchRequest(BaseModel):
    action: Literal["confirm", "change", "unmatch"]
    variantId: str | None = None
    note: str | None = None


class AutoMatchRequest(BaseModel):
    actor: str = "system"


def _item_or_404(db: Session, item_id: str) -> OrderItem:
    item = db.get(OrderItem, item_id)
    if item is None:
        raise HTTPException(404, f"订单不存在：{item_id}")
    return item


def _variant_material_id(variant_id: str) -> str | None:
    """经素材平台契约取 Variant 的 material_id（不直接查素材库 ORM）。"""
    gw = _mp.get_material_platform()
    candidate = gw.get_variant(variant_id)
    return candidate.material_id if candidate else None


def _ensure_bindings(db: Session, item: OrderItem, variant_id: str, actor: str) -> None:
    """确认命中时建立 URL 绑定（物料复用）+ ASIN 绑定（候选定位）。"""
    payload = item.normalized_payload or {}
    material_url = payload.get("materialUrl")
    material_id = _variant_material_id(variant_id)
    if material_url:
        exists = db.execute(
            select(MaterialUrlBinding).where(MaterialUrlBinding.material_url == material_url)
        ).scalar_one_or_none()
        if exists is None:
            db.add(MaterialUrlBinding(
                id=new_id("url-binding"), material_url=material_url,
                variant_id=variant_id, material_id=material_id,
                match_method="MANUAL", created_by=actor,
            ))
    if item.child_asin:
        exists = db.execute(
            select(AsinVariantBinding).where(
                AsinVariantBinding.child_asin == item.child_asin,
                AsinVariantBinding.variant_id == variant_id,
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(AsinVariantBinding(
                id=new_id("asin-binding"), child_asin=item.child_asin,
                variant_id=variant_id, material_id=material_id, created_by=actor,
            ))


def _item_summary(item: OrderItem) -> dict:
    return {
        "id": item.id, "orderId": item.order_id, "orderItemId": item.order_item_id,
        "childAsin": item.child_asin, "sku": item.sku, "quantity": item.quantity,
        "categoryCode": item.category_code, "parserVersion": item.parser_version,
        "matchedVariantId": item.matched_variant_id, "matchedMaterialId": item.matched_material_id,
        "matchMethod": item.match_method, "matchScore": item.match_score,
        "matchStatus": item.match_status, "normalized": item.normalized_payload,
    }


def _record_action(db: Session, item: OrderItem, action: str, actor: str, variant_id: str | None, material_id: str | None, note: str | None) -> None:
    """写订单审核动作记录（order-center 自有表）。"""
    db.add(OrderMatchAction(
        id=new_id("match-action"), order_item_id=item.id, action=action,
        variant_id=variant_id, material_id=material_id, note=note, actor=actor,
    ))
    db.flush()


# ---------------------------------------------------------------- 人工审核


@router.post("/items/{item_id}/match", summary="人工审核：确认 / 更换 Variant / 无法识别")
def review_match(
    item_id: str,
    req: MatchRequest,
    actor: str = "order-reviewer",
    db: Session = Depends(get_db),
) -> dict:
    item = _item_or_404(db, item_id)
    if req.action == "unmatch":
        item.match_status = "FAILED"
        _record_action(db, item, "MATCH_FAILED", actor, None, None, req.note)
    else:
        if not req.variantId:
            raise HTTPException(400, "confirm/change 需要 variantId")
        # variant_id 只存字符串，经素材平台契约校验并取 material_id
        candidate = _mp.get_material_platform().get_variant(req.variantId)
        if candidate is None:
            raise HTTPException(404, f"Variant 不存在：{req.variantId}")
        item.matched_variant_id = req.variantId
        item.matched_material_id = candidate.material_id
        item.match_method = "MANUAL_CONFIRM" if req.action == "confirm" else "MANUAL_CHANGE"
        item.match_score = 1.0
        item.match_status = "CONFIRMED"
        _ensure_bindings(db, item, req.variantId, actor)
        _record_action(
            db, item,
            "MATCH_CONFIRMED" if req.action == "confirm" else "MATCH_CHANGED",
            actor, req.variantId, candidate.material_id, req.note,
        )
    db.commit()
    return _item_summary(item)


# ---------------------------------------------------------------- 自动匹配


@router.post("/items/{item_id}/auto-match", summary="对单个订单跑一次自动素材匹配")
def auto_match_item(item_id: str, req: AutoMatchRequest, db: Session = Depends(get_db)) -> dict:
    item = _item_or_404(db, item_id)
    run_matcher(db, item)
    db.commit()
    return _item_summary(item)


@router.post("/{batch_id}/auto-match", summary="对整个批次跑自动素材匹配")
def auto_match_batch(batch_id: str, req: AutoMatchRequest, db: Session = Depends(get_db)) -> dict:
    batch = db.get(OrderImportBatch, batch_id)
    if batch is None:
        raise HTTPException(404, f"批次不存在：{batch_id}")
    items = db.execute(
        select(OrderItem).where(OrderItem.first_import_batch_id == batch_id)
    ).scalars().all()
    confirmed = review_required = failed = 0
    for item in items:
        if run_matcher(db, item):
            confirmed += 1
        elif item.match_status == "REVIEW_REQUIRED":
            review_required += 1
        elif item.match_status == "FAILED":
            failed += 1
    db.commit()
    return {"batchId": batch_id, "confirmed": confirmed, "reviewRequired": review_required, "failed": failed}


# ---------------------------------------------------------------- 异常审核列表
# 独立前缀：避免与 /order-imports/{batch_id} 路径参数冲突

review_router = APIRouter(prefix="/order-match", tags=["order-match"])


@review_router.get("/review", summary="异常审核列表（REVIEW_REQUIRED / UNMATCHED / FAILED）")
def match_review(
    scope: str = "all", category: str | None = None, limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict]:
    statuses = {
        "all": ("REVIEW_REQUIRED", "UNMATCHED", "FAILED"),
        "review": ("REVIEW_REQUIRED",),
        "unmatched": ("UNMATCHED",),
        "failed": ("FAILED",),
    }.get(scope, ("REVIEW_REQUIRED", "UNMATCHED", "FAILED"))
    stmt = select(OrderItem).where(OrderItem.match_status.in_(statuses))
    if category:
        stmt = stmt.where(OrderItem.category_code == category)
    stmt = stmt.order_by(OrderItem.created_at.desc()).limit(limit)
    return [_item_summary(it) for it in db.execute(stmt).scalars().all()]


# ================================================================ 销量归因

sales_router = APIRouter(prefix="/order-sales", tags=["order-sales"])


@sales_router.get("", summary="正式销量（仅 CONFIRMED 计入）")
def order_sales(
    variantId: str | None = None,
    materialId: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(
        func.coalesce(OrderItem.matched_variant_id, ""),
        func.coalesce(OrderItem.matched_material_id, ""),
        func.coalesce(OrderItem.category_code, ""),
        func.sum(OrderItem.quantity),
        func.count(OrderItem.id),
    ).where(OrderItem.match_status == "CONFIRMED")
    if variantId:
        stmt = stmt.where(OrderItem.matched_variant_id == variantId)
    if materialId:
        stmt = stmt.where(OrderItem.matched_material_id == materialId)
    if category:
        stmt = stmt.where(OrderItem.category_code == category)
    stmt = stmt.group_by(OrderItem.matched_variant_id, OrderItem.matched_material_id, OrderItem.category_code)
    rows = db.execute(stmt).all()

    variant_sales: dict[str, dict] = {}
    material_sales: dict[str, dict] = {}
    category_sales: dict[str, dict] = {}

    def _acc(target: dict, key: str, qty: int, orders: int, category: str) -> None:
        cur = target.setdefault(key, {"quantity": 0, "orders": 0, "categoryCode": category})
        cur["quantity"] += qty
        cur["orders"] += orders

    for variant_id, material_id, cat, qty, orders in rows:
        qty = int(qty or 0)
        orders = int(orders or 0)
        if variant_id:
            _acc(variant_sales, variant_id, qty, orders, cat)
        if material_id:
            _acc(material_sales, material_id, qty, orders, cat)
        if cat:
            _acc(category_sales, cat, qty, orders, cat)
    return {
        "variantSales": variant_sales,
        "materialSales": material_sales,
        "categorySales": category_sales,
        "totalQuantity": sum(v["quantity"] for v in variant_sales.values()),
        "totalOrders": sum(v["orders"] for v in variant_sales.values()),
    }
