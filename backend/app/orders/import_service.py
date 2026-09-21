from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    OrderAsset,
    OrderBuyerAsset,
    OrderImportBatch,
    OrderImportBatchItem,
    OrderItem,
)
from app.orders.categories.blade_shoes import BladeShoesParser
from app.orders.core.category_resolver import CategoryResolver
from app.orders.core.generic_parser import GenericOrderParser
from app.orders.core.parser_registry import ParserRegistry
from app.services.fingerprint import compute_blake3
from app.services.repository import new_id, utcnow
from app.services.storage import StorageError, build_storage_key, get_storage


class OrderImportError(ValueError):
    pass


def default_registry() -> ParserRegistry:
    registry = ParserRegistry()
    registry.register("BLADE_SHOES", BladeShoesParser())
    return registry


def _mime(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".zip"):
        return "application/zip"
    if lower.endswith(".svg"):
        return "image/svg+xml"
    return "application/octet-stream"


def _ensure_asset(db: Session, filename: str, data: bytes, actor: str) -> OrderAsset:
    """把订单原始文件存为 OrderAsset（order-center 自有表，不用素材库 assets）。"""
    digest = compute_blake3(data)
    existing = db.execute(
        select(OrderAsset).where(OrderAsset.blake3 == digest)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    storage = get_storage()
    storage_key = build_storage_key(filename)
    try:
        storage.put(storage_key, data, _mime(filename))
    except StorageError as exc:
        raise OrderImportError(f"原始文件写入存储失败：{filename}") from exc
    asset = OrderAsset(
        id=new_id("order-asset"),
        storage_key=storage_key,
        original_filename=filename,
        mime_type=_mime(filename),
        size_bytes=len(data),
        blake3=digest,
        created_by=actor,
    )
    db.add(asset)
    db.flush()
    return asset


def _safe_json_files(data: bytes, filename: str) -> list[tuple[str, bytes]]:
    """Read nested ZIPs without extracting to disk or allowing path traversal."""
    if filename.lower().endswith(".json"):
        return [(filename, data)]
    if not filename.lower().endswith(".zip"):
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            out: list[tuple[str, bytes]] = []
            for info in archive.infolist():
                if info.is_dir() or info.file_size > 50 * 1024 * 1024:
                    continue
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                content = archive.read(info)
                if name.lower().endswith(".json"):
                    out.append((name, content))
                elif name.lower().endswith(".zip"):
                    out.extend(_safe_json_files(content, name))
            return out
    except zipfile.BadZipFile as exc:
        raise OrderImportError(f"订单文件不是有效 ZIP：{filename}") from exc


def _dedupe_key(record, raw_path: str) -> str:
    if record.order_item_id:
        return f"ITEM:{record.order_item_id.strip()}"
    stable = json.dumps(
        {"orderId": record.order_id, "asin": record.child_asin, "sku": record.sku, "path": raw_path},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"FALLBACK:{compute_blake3(stable)}"


def _json_documents(data: bytes, filename: str) -> Iterable[tuple[str, bytes, Any]]:
    for path, raw in _safe_json_files(data, filename):
        try:
            document = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        yield path, raw, document


def import_order_zip(
    db: Session,
    *,
    filename: str,
    data: bytes,
    actor: str,
    category_code: str | None = None,
    registry: ParserRegistry | None = None,
) -> tuple[OrderImportBatch, dict[str, int]]:
    if not data:
        raise OrderImportError("订单 ZIP 不能为空")
    if not filename.lower().endswith(".zip"):
        raise OrderImportError("订单导入只接受 ZIP 文件")
    registry = registry or default_registry()
    resolver = CategoryResolver()
    batch_category = resolver.resolve(manual_code=category_code)
    zip_hash = compute_blake3(data)
    raw_zip = _ensure_asset(db, filename, data, actor)
    canonical = db.execute(
        select(OrderImportBatch)
        .where(OrderImportBatch.zip_blake3 == zip_hash)
        .order_by(OrderImportBatch.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    batch = OrderImportBatch(
        id=new_id("order-import"),
        original_filename=filename,
        raw_zip_asset_id=raw_zip.id,
        zip_blake3=zip_hash,
        category_code=batch_category.code,
        category_name=batch_category.name,
        category_source=batch_category.source,
        category_confidence=batch_category.confidence,
        status="DUPLICATE" if canonical is not None else "PARSED",
        duplicate_of_batch_id=canonical.id if canonical is not None else None,
        created_by=actor,
    )
    db.add(batch)
    db.flush()
    if canonical is not None:
        batch.total_json_count = canonical.total_json_count
        batch.total_item_count = canonical.total_item_count
        db.commit()
        return batch, {"jsonCount": batch.total_json_count, "itemCount": batch.total_item_count, "newItemCount": 0, "duplicateItemCount": batch.total_item_count}

    json_count = item_count = new_count = duplicate_count = 0
    review_count = 0
    seen_item_ids: set[str] = set()
    for json_path, raw_json, document in _json_documents(data, filename):
        json_count += 1
        json_asset = _ensure_asset(db, json_path, raw_json, actor)
        records = GenericOrderParser().parse(document, source_path=json_path)
        for record in records:
            item_count += 1
            category = resolver.resolve(manual_code=category_code, sku=record.sku, asin=record.child_asin)
            normalized = registry.parse(record, category=category)
            if normalized.parse_status == "REVIEW_REQUIRED":
                review_count += 1
            key = _dedupe_key(record, record.raw_path)
            item = db.execute(select(OrderItem).where(OrderItem.dedupe_key == key)).scalar_one_or_none()
            if item is None:
                item = OrderItem(
                    id=new_id("order-item"),
                    dedupe_key=key,
                    first_import_batch_id=batch.id,
                    order_id=normalized.order_id,
                    order_item_id=normalized.order_item_id,
                    child_asin=normalized.child_asin,
                    sku=normalized.sku,
                    quantity=normalized.quantity,
                    category_code=normalized.category_code,
                    category_name=normalized.category_name,
                    category_source=normalized.category_source,
                    category_confidence=normalized.category_confidence,
                    parser_version=normalized.parser_version,
                    raw_json_hash=compute_blake3(raw_json),
                    normalized_payload=normalized.as_payload(),
                    match_status=normalized.match_status,
                )
                db.add(item)
                db.flush()
                new_count += 1
                for role, entries in (("BUYER_LOGO_ORIGINAL", normalized.buyer_logo_original), ("BUYER_LOGO_SVG", normalized.buyer_logo_svg)):
                    for index, entry in enumerate(entries):
                        db.add(OrderBuyerAsset(
                            id=new_id("buyer-asset"), order_item_id=item.id, role=role,
                            source_url=entry.get("url"), source_path=entry.get("path"), is_primary=index == 0,
                        ))
            else:
                duplicate_count += 1
            if item.id not in seen_item_ids:
                db.add(OrderImportBatchItem(
                    id=new_id("order-import-item"), import_batch_id=batch.id, order_item_id=item.id,
                    raw_json_asset_id=json_asset.id, raw_json_path=record.raw_path,
                    raw_json_hash=compute_blake3(raw_json), parser_version=normalized.parser_version,
                    parse_status=normalized.parse_status, normalized_payload=normalized.as_payload(),
                ))
                seen_item_ids.add(item.id)
    batch.total_json_count = json_count
    batch.total_item_count = item_count
    if json_count == 0:
        batch.status = "FAILED"
    elif review_count:
        batch.status = "PARTIAL"
    db.commit()
    return batch, {"jsonCount": json_count, "itemCount": item_count, "newItemCount": new_count, "duplicateItemCount": duplicate_count}
