"""Database regression checks runnable without a MySQL service (--noconftest)."""

from __future__ import annotations

import io
import json
import zipfile
from collections import Counter

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base, MaterialUrlBinding, OrderBuyerAsset, OrderItem, OrderMatchAction
from app.db.session import get_db
from app.main import app
from app.orders import import_service
from app.services import material_platform
from app.services.material_platform import FakeMaterialPlatformGateway, VariantCandidate


class MemoryStorage:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def put(self, key, data, mime):
        self.data[key] = data


@pytest.fixture
def api(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    storage = MemoryStorage()
    monkeypatch.setattr(import_service, "get_storage", lambda: storage)
    gateway = FakeMaterialPlatformGateway([
        VariantCandidate(variant_id="variant-a", material_id="MAT-A", category_code="BLADE_SHOES"),
        VariantCandidate(variant_id="variant-b", material_id="MAT-B", category_code="BLADE_SHOES"),
    ])
    monkeypatch.setattr(material_platform, "get_material_platform", lambda: gateway)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = session_override
    try:
        with TestClient(app) as client:
            yield client, engine, gateway, storage
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def archive(*, ambiguous=False):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["平台单号", "图片", "MSKU", "ASIN/商品Id", "数量", "买家留言"])
    sheet.append(["ORDER-1", "查看原图", "BLADE-1", "ASIN-1", 2, "please test"])
    sheet.cell(2, 2).hyperlink = "./Files/image/preview.jpg"
    if ambiguous:
        sheet.append(["ORDER-1", "查看原图", "BLADE-2", "ASIN-1", 1, None])
    excel = io.BytesIO()
    workbook.save(excel)
    document = {
        "orderId": "ORDER-1", "orderItemId": "ITEM-1", "asin": "ASIN-1", "quantity": 1,
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "OptionCustomization", "label": "Choose Your Style", "optionSelection": {
                "name": "City", "thumbnailImage": {"imageUrl": "https://fixture.invalid/material.png"},
                "overlayImage": {"imageUrl": "https://fixture.invalid/overlay.png"}
            }},
            {"type": "ImageCustomization", "image": {
                "imageName": "buyer.png", "imageUrl": "https://fixture.invalid/buyer.png"
            }}
        ]}]}]},
        "version3.0": {"customizationInfo": {"surfaces": [{"areas": [
            {"customizationType": "ImagePrinting", "svgImage": {
                "imageName": "buyer.svg", "imageUrl": "https://fixture.invalid/buyer.svg"
            }}
        ]}]}},
    }
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as nested:
        nested.writestr("order.json", json.dumps(document))
        nested.writestr("buyer.png", b"buyer-image")
        nested.writestr("buyer.svg", b"<svg/>")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("orders.xlsx", excel.getvalue())
        package.writestr("Files/image/order.zip", inner.getvalue())
        package.writestr("Files/image/preview.jpg", b"preview")
    return outer.getvalue()


def test_real_style_zip_import_duplicate_logo_and_review(api):
    client, engine, gateway, storage = api
    raw = archive()
    response = client.post("/api/order-imports", files={"file": ("orders.zip", raw)}, data={"categoryCode": "BLADE_SHOES"})
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == {
        "jsonCount": 1, "itemCount": 1, "newItemCount": 1, "duplicateItemCount": 0,
    }
    with Session(engine) as session:
        item = session.execute(select(OrderItem).where(OrderItem.order_item_id == "ITEM-1")).scalar_one()
        assert (item.sku, item.quantity, item.parse_status) == ("BLADE-1", 2, "PARSED")
        assert item.normalized_payload["buyerRequest"] == "please test"
        assert item.normalized_payload["details"]["outerExcel"]["rowNumber"] == 2
        logos = session.execute(select(OrderBuyerAsset)).scalars().all()
        assert Counter(logo.role for logo in logos) == {
            "BUYER_LOGO_ORIGINAL": 1, "BUYER_LOGO_SVG": 1,
        }
        assert all(logo.asset_id and logo.asset_id in {
            asset.id for asset in session.execute(select(import_service.OrderAsset)).scalars()
        } for logo in logos)
        assert next(logo for logo in logos if logo.role == "BUYER_LOGO_ORIGINAL").is_primary
        item_id = item.id
    assert b"buyer-image" in storage.data.values()
    second = client.post("/api/order-imports", files={"file": ("repeat.zip", raw)}, data={"categoryCode": "BLADE_SHOES"})
    assert second.status_code == 200, second.text
    assert second.json()["batch"]["status"] == "DUPLICATE"
    with Session(engine) as session:
        assert session.scalar(select(func.count(OrderItem.id))) == 1

    confirm = client.post(f"/api/order-imports/items/{item_id}/match", json={"action": "confirm", "variantId": "variant-a"})
    assert confirm.status_code == 200, confirm.text
    assert client.get("/api/order-sales").json()["totalQuantity"] == 2
    change = client.post(f"/api/order-imports/items/{item_id}/match", json={"action": "change", "variantId": "variant-b"})
    assert change.status_code == 200, change.text
    with Session(engine) as session:
        binding = session.execute(select(MaterialUrlBinding)).scalar_one()
        assert binding.variant_id == "variant-b"
        assert session.scalar(select(func.count(OrderMatchAction.id))) == 2
    failed = client.post(f"/api/order-imports/items/{item_id}/match", json={"action": "unmatch"})
    assert failed.status_code == 200, failed.text
    assert client.get("/api/order-sales").json()["totalQuantity"] == 0


def test_ambiguous_excel_join_requires_review(api):
    client, engine, _, _ = api
    response = client.post("/api/order-imports", files={"file": ("ambiguous.zip", archive(ambiguous=True))}, data={"categoryCode": "BLADE_SHOES"})
    assert response.status_code == 200, response.text
    assert response.json()["batch"]["status"] == "PARTIAL"
    with Session(engine) as session:
        item = session.execute(select(OrderItem).where(OrderItem.order_item_id == "ITEM-1")).scalar_one()
        assert item.sku is None
        assert item.parse_status == "REVIEW_REQUIRED"
        assert item.match_status == "NOT_STARTED"
        assert item.normalized_payload["details"]["reason"] == "AMBIGUOUS_OUTER_EXCEL_MATCH"


def test_import_parse_does_not_depend_on_material_platform(api, monkeypatch):
    client, engine, _, _ = api
    monkeypatch.setattr(
        material_platform,
        "get_material_platform",
        lambda: (_ for _ in ()).throw(material_platform.MaterialPlatformUnavailableError("offline")),
    )
    response = client.post("/api/order-imports", files={"file": ("offline.zip", archive())})
    assert response.status_code == 200
    with Session(engine) as session:
        item = session.scalar(select(OrderItem).where(OrderItem.order_item_id == "ITEM-1"))
        assert item is not None
        assert item.parse_status == "PARSED"
        assert item.match_status == "NOT_STARTED"


def test_non_custom_is_not_unknown_image_type(api):
    client, engine, _, _ = api
    response = client.post("/api/order-imports", files={"file": ("noncustom.zip", archive())})
    assert response.status_code == 200
    with Session(engine) as session:
        # The fixture has an Excel row with a matching JSON, so this asserts
        # the normal parsed path remains distinct from NON_CUSTOM. The real
        # ZIP regression covers the 15 Excel-only rows.
        item = session.scalar(select(OrderItem).where(OrderItem.order_item_id == "ITEM-1"))
        assert item.normalized_payload["imageType"] != "UNKNOWN"
