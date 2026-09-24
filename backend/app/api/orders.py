from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import OrderAsset, OrderImportBatch, OrderImportBatchItem, OrderItem
from app.db.session import get_db
from app.orders.import_service import OrderImportError, import_order_zip
from app.services.storage import get_storage

router = APIRouter(prefix="/order-imports", tags=["order-imports"])


class OrderImportBatchDTO(BaseModel):
    id: str
    originalFilename: str
    rawZipAssetId: str
    zipBlake3: str
    categoryCode: str
    categoryName: str
    categorySource: str
    categoryConfidence: str
    status: str
    duplicateOfBatchId: str | None = None
    totalJsonCount: int
    totalItemCount: int
    createdBy: str
    createdAt: datetime
    updatedAt: datetime


class OrderImportResponse(BaseModel):
    batch: OrderImportBatchDTO
    summary: dict[str, int]


class OrderItemDTO(BaseModel):
    id: str
    dedupeKey: str
    orderId: str
    orderItemId: str | None
    childAsin: str | None
    sku: str | None
    quantity: int
    categoryCode: str
    categoryName: str
    categorySource: str
    categoryConfidence: str
    parserVersion: str
    parsedAt: datetime
    parseStatus: str
    rawJsonHash: str
    matchedVariantId: str | None
    matchedMaterialId: str | None
    matchMethod: str | None
    matchScore: float | None
    secondMatchScore: float | None
    scoreGap: float | None
    matchReason: str | None
    matchStatus: str
    normalized: dict | None


def _batch_dto(row: OrderImportBatch) -> OrderImportBatchDTO:
    return OrderImportBatchDTO(
        id=row.id, originalFilename=row.original_filename, rawZipAssetId=row.raw_zip_asset_id,
        zipBlake3=row.zip_blake3, categoryCode=row.category_code, categoryName=row.category_name,
        categorySource=row.category_source, categoryConfidence=row.category_confidence,
        status=row.status, duplicateOfBatchId=row.duplicate_of_batch_id,
        totalJsonCount=row.total_json_count, totalItemCount=row.total_item_count,
        createdBy=row.created_by, createdAt=row.created_at, updatedAt=row.updated_at,
    )


def _item_dto(row: OrderItem) -> OrderItemDTO:
    return OrderItemDTO(
        id=row.id, dedupeKey=row.dedupe_key, orderId=row.order_id, orderItemId=row.order_item_id,
        childAsin=row.child_asin, sku=row.sku, quantity=row.quantity,
        categoryCode=row.category_code, categoryName=row.category_name,
        categorySource=row.category_source, categoryConfidence=row.category_confidence,
        parserVersion=row.parser_version, parsedAt=row.parsed_at, parseStatus=row.parse_status,
        rawJsonHash=row.raw_json_hash,
        matchedVariantId=row.matched_variant_id, matchedMaterialId=row.matched_material_id,
        matchMethod=row.match_method, matchScore=row.match_score,
        secondMatchScore=row.second_match_score, scoreGap=row.score_gap,
        matchReason=row.match_reason, matchStatus=row.match_status,
        normalized=row.normalized_payload,
    )


@router.post("", response_model=OrderImportResponse, summary="导入订单 ZIP（保留原始 ZIP/JSON，按 orderItemId 去重）")
async def create_order_import(
    file: UploadFile = File(..., description="领星订单 ZIP"),
    categoryCode: str | None = Form(default=None),
    actor: str = Form(default="system"),
    db: Session = Depends(get_db),
) -> OrderImportResponse:
    data = await file.read()
    try:
        batch, summary = import_order_zip(
            db, filename=file.filename or "orders.zip", data=data,
            actor=actor.strip() or "system", category_code=categoryCode,
        )
    except OrderImportError as exc:
        from app.core.errors import ValidationError
        raise ValidationError(str(exc)) from exc
    return OrderImportResponse(batch=_batch_dto(batch), summary=summary)


@router.get("", response_model=list[OrderImportBatchDTO], summary="查询订单导入批次")
def list_order_imports(db: Session = Depends(get_db)) -> list[OrderImportBatchDTO]:
    rows = db.execute(select(OrderImportBatch).order_by(OrderImportBatch.created_at.desc())).scalars()
    return [_batch_dto(row) for row in rows]


@router.get("/{batch_id}", response_model=OrderImportResponse, summary="查看订单导入批次")
def get_order_import(batch_id: str, db: Session = Depends(get_db)) -> OrderImportResponse:
    from app.core.errors import NotFoundError
    batch = db.get(OrderImportBatch, batch_id)
    if batch is None:
        raise NotFoundError("订单导入批次不存在")
    count = db.execute(select(OrderImportBatchItem).where(OrderImportBatchItem.import_batch_id == batch.id)).scalars().all()
    return OrderImportResponse(batch=_batch_dto(batch), summary={"jsonCount": batch.total_json_count, "itemCount": batch.total_item_count, "linkedItemCount": len(count)})


@router.get("/{batch_id}/items", response_model=list[OrderItemDTO], summary="查看订单导入规范化结果")
def list_order_import_items(
    batch_id: str,
    parseStatus: str | None = None,
    imageType: str | None = None,
    soleColor: str | None = None,
    search: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[OrderItemDTO]:
    from app.core.errors import NotFoundError
    if db.get(OrderImportBatch, batch_id) is None:
        raise NotFoundError("订单导入批次不存在")
    stmt = (
        select(OrderItem).join(OrderImportBatchItem, OrderImportBatchItem.order_item_id == OrderItem.id)
        .where(OrderImportBatchItem.import_batch_id == batch_id)
        .order_by(OrderItem.order_id.asc(), OrderItem.id.asc())
    )
    if parseStatus:
        stmt = stmt.where(OrderItem.parse_status == parseStatus)
    if imageType:
        stmt = stmt.where(OrderItem.normalized_payload["imageType"].as_string() == imageType)
    if soleColor:
        stmt = stmt.where(OrderItem.normalized_payload["soleColor"].as_string() == soleColor)
    if search:
        token = f"%{search.strip()}%"
        stmt = stmt.where(or_(OrderItem.order_id.like(token), OrderItem.order_item_id.like(token), OrderItem.child_asin.like(token), OrderItem.sku.like(token)))
    rows = db.execute(stmt.offset(offset).limit(limit)).scalars()
    return [_item_dto(row) for row in rows]


raw_router = APIRouter(prefix="/order-items", tags=["order-items"])


@raw_router.get("/{item_id}/raw-json", summary="查看订单原始 JSON（只读）")
def get_order_item_raw_json(item_id: str, db: Session = Depends(get_db)) -> dict:
    from app.core.errors import NotFoundError
    item = db.get(OrderItem, item_id)
    if item is None:
        raise NotFoundError("订单不存在")
    snapshot = db.execute(
        select(OrderImportBatchItem)
        .where(OrderImportBatchItem.order_item_id == item_id)
        .order_by(OrderImportBatchItem.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if snapshot is None:
        return {"itemId": item_id, "available": False, "rawJson": None}
    asset = db.get(OrderAsset, snapshot.raw_json_asset_id)
    if asset is None:
        return {"itemId": item_id, "available": False, "rawJson": None}
    try:
        data, _ = get_storage().get(asset.storage_key)
        raw = json.loads(data.decode("utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "原始 JSON 读取失败") from exc
    return {"itemId": item_id, "available": True, "path": snapshot.raw_json_path, "rawJson": raw}
