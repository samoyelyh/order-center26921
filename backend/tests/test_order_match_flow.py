# ============================================================================
# order-center 导入 + 匹配 + 审核 + 销量测试
#
# 用 FakeMaterialPlatformGateway 作为素材平台（不依赖真实素材库）。
# 验证：
#   1. ZIP 导入去重（orderItemId 不重复计销量）
#   2. URL 绑定直接命中（不跑图匹配）
#   3. ASIN 候选内图片匹配 + 品类隔离
#   4. 人工审核（confirm/change/unmatch）+ OrderMatchAction 记录
#   5. 销量归因仅 CONFIRMED 计入
# ============================================================================

from __future__ import annotations

import io
import json
import zipfile
import openpyxl
from sqlalchemy import func, select

from app.db.models import (
    AsinVariantBinding,
    MaterialUrlBinding,
    OrderBuyerAsset,
    OrderItem,
    OrderMatchAction,
)
from app.services import material_platform
from app.services.material_platform import FakeMaterialPlatformGateway, VariantCandidate
from tests.conftest import make_order_zip


def _fake_gw(monkeypatch, candidates: list[VariantCandidate]) -> FakeMaterialPlatformGateway:
    gw = FakeMaterialPlatformGateway(candidates)
    monkeypatch.setattr(material_platform, "get_material_platform", lambda: gw)
    return gw


def _material_source_payload(order_item_id: str, url: str, asin: str = "B0TEST01") -> dict:
    return {
        "orderId": f"O-{order_item_id}", "orderItemId": order_item_id, "asin": asin,
        "quantity": 2, "sku": "BLADE-TEST",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "OptionCustomization", "name": "样式", "label": "Style",
             "optionSelection": {"name": "City", "displayValue": "City",
               "thumbnailImage": {"imageUrl": url},
               "overlayImage": {"imageUrl": url.replace(".png", "-ov.png")}}}
        ]}]}]},
    }


def _real_style_zip(payload: dict, *, sku: str, files: dict[str, bytes] | None = None) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["平台单号", "图片", "MSKU", "ASIN/商品Id", "数量", "买家留言", "商品备注"])
    sheet.append([payload["orderId"], "查看原图", sku, payload["asin"], payload.get("quantity", 1), "outer request", "remark"])
    sheet.cell(2, 2).hyperlink = "./Files/image/order-preview.jpg"
    excel = io.BytesIO()
    workbook.save(excel)

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as nested:
        nested.writestr("order.json", json.dumps(payload, ensure_ascii=False))
        for name, content in (files or {}).items():
            nested.writestr(name, content)

    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("订单.xlsx", excel.getvalue())
        archive.writestr("Files/image/customization.zip", inner.getvalue())
        archive.writestr("Files/image/order-preview.jpg", b"preview")
    return outer.getvalue()


def test_import_dedupes_order_item(client, db_session):
    payload = _material_source_payload("OI-DUP", "https://cdn.example.com/m.png")
    data = make_order_zip([payload])
    first = client.post("/api/order-imports", files={"file": ("a.zip", data, "application/zip")})
    assert first.status_code == 200
    assert first.json()["summary"]["newItemCount"] == 1
    second = client.post("/api/order-imports", files={"file": ("b.zip", data, "application/zip")})
    assert second.json()["summary"]["newItemCount"] == 0
    assert second.json()["summary"]["duplicateItemCount"] == 1
    db_session.rollback()
    count = db_session.execute(select(func.count(OrderItem.id)).where(OrderItem.order_item_id == "OI-DUP")).scalar_one()
    assert count == 1


def test_real_style_outer_excel_enriches_json_and_saves_buyer_assets(client, db_session):
    payload = {
        "orderId": "O-EXCEL", "orderItemId": "OI-EXCEL", "asin": "B0EXCEL", "quantity": 1,
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "ImageCustomization", "label": "Logo", "image": {
                "imageName": "buyer.png", "imageUrl": "https://fixture.invalid/buyer.png"
            }}
        ]}]}]},
        "version3.0": {"customizationInfo": {"surfaces": [{"areas": [
            {"customizationType": "ImagePrinting", "label": "Logo", "svgImage": {
                "imageName": "buyer.svg", "imageUrl": "https://fixture.invalid/buyer.svg"
            }}
        ]}]}},
    }
    data = _real_style_zip(payload, sku="BLADE-EXCEL", files={
        "buyer.png": b"buyer-png", "buyer.svg": b"<svg>buyer</svg>",
    })
    response = client.post("/api/order-imports", files={"file": ("real.zip", data, "application/zip")})
    assert response.status_code == 200, response.text
    db_session.rollback()
    item = db_session.execute(select(OrderItem).where(OrderItem.order_item_id == "OI-EXCEL")).scalar_one()
    assert item.sku == "BLADE-EXCEL"
    assert item.category_code == "BLADE_SHOES"
    assert item.normalized_payload["buyerRequest"] == "outer request"
    assert item.normalized_payload["details"]["outerExcel"]["rowNumber"] == 2
    buyer_assets = db_session.execute(
        select(OrderBuyerAsset).where(OrderBuyerAsset.order_item_id == item.id).order_by(OrderBuyerAsset.role)
    ).scalars().all()
    assert len(buyer_assets) == 2
    assert all(asset.asset_id for asset in buyer_assets)
    original = next(asset for asset in buyer_assets if asset.role == "BUYER_LOGO_ORIGINAL")
    svg = next(asset for asset in buyer_assets if asset.role == "BUYER_LOGO_SVG")
    assert original.is_primary is True
    assert svg.is_primary is False


def test_url_binding_hits_without_image_match(client, db_session, monkeypatch):
    from app.services import image_embedder

    calls = {"similarity": 0}
    monkeypatch.setattr(image_embedder, "get_embedder", lambda: _StubEmbedder(calls))
    gw = _fake_gw(monkeypatch, [VariantCandidate(variant_id="v1", material_id="MAT-1", category_code="BLADE_SHOES", display_code="1-1")])
    gw.seed_material_source("v1", b"img")

    url = "https://cdn.example.com/bound.png"
    payload = _material_source_payload("OI-URL", url, asin="B0TEST01")
    resp = client.post("/api/order-imports", files={"file": ("o.zip", make_order_zip([payload]), "application/zip")})
    batch_id = resp.json()["batch"]["id"]
    db_session.rollback()
    item = db_session.execute(select(OrderItem).where(OrderItem.first_import_batch_id == batch_id)).scalar_one()

    # 预建 URL 绑定 → 自动匹配直接命中，不跑图片相似度
    db_session.add(MaterialUrlBinding(id="ub-1", material_url=url, variant_id="v1", material_id="MAT-1", match_method="MANUAL", created_by="t"))
    db_session.commit()
    calls["similarity"] = 0
    db_session.rollback()
    db_session.expire_all()
    from app.orders.matcher import run_matcher

    assert run_matcher(db_session, item) is True
    assert item.match_method == "URL_BINDING"
    assert item.matched_variant_id == "v1"
    assert calls["similarity"] == 0, "URL 绑定命中不应跑图片匹配"


class _StubEmbedder:
    """固定相似度=0.9（> 阈值 0.85）的 embedder，不依赖模型。"""

    def __init__(self, calls: dict) -> None:
        self._calls = calls
        self.dim = 8

    def embed(self, data: bytes):
        import numpy as np

        self._calls["similarity"] += 1
        return np.array([1.0, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32)


def test_asin_candidate_match_and_category_isolation(client, db_session, monkeypatch):
    from app.orders import matcher as matcher_module
    from app.orders.matcher import run_matcher

    from app.services import image_embedder

    calls = {"similarity": 0}
    monkeypatch.setattr(image_embedder, "get_embedder", lambda: _StubEmbedder(calls))
    monkeypatch.setattr(matcher_module, "_download", lambda url, timeout=15.0: b"query-img")
    # 候选 Variant 品类 = UNKNOWN（与订单 BLADE_SHOES 不符 → 品类隔离触发）
    gw = _fake_gw(monkeypatch, [VariantCandidate(variant_id="v2", material_id="MAT-2", category_code="UNKNOWN", display_code="2-1")])
    gw.seed_material_source("v2", b"candidate-img")

    # 建 ASIN 绑定
    db_session.add(AsinVariantBinding(id="ab-1", child_asin="B0ISO", variant_id="v2", material_id="MAT-2", created_by="t"))
    db_session.commit()

    url = "https://cdn.example.com/iso.png"
    payload = _material_source_payload("OI-ISO", url, asin="B0ISO")
    resp = client.post("/api/order-imports", files={"file": ("o.zip", make_order_zip([payload]), "application/zip")})
    batch_id = resp.json()["batch"]["id"]
    db_session.rollback()
    item = db_session.execute(select(OrderItem).where(OrderItem.first_import_batch_id == batch_id)).scalar_one()
    db_session.expire_all()

    assert run_matcher(db_session, item) is False
    assert item.match_status == "REVIEW_REQUIRED"
    assert calls["similarity"] == 0, "品类不符的候选不应进入图片匹配"


def test_platform_asin_candidates_take_precedence_over_temporary_binding(client, db_session, monkeypatch):
    from app.orders import matcher as matcher_module
    from app.orders.matcher import run_matcher
    from app.services import image_embedder

    calls = {"similarity": 0}
    monkeypatch.setattr(image_embedder, "get_embedder", lambda: _StubEmbedder(calls))
    monkeypatch.setattr(matcher_module, "_download", lambda url, timeout=15.0: b"query")
    gw = _fake_gw(monkeypatch, [
        VariantCandidate(variant_id="platform-v", material_id="MAT-P", category_code="BLADE_SHOES"),
        VariantCandidate(variant_id="local-v", material_id="MAT-L", category_code="BLADE_SHOES"),
    ])
    gw.seed_asin_candidates("B0SCOPED", ["platform-v"])
    gw.seed_material_source("platform-v", b"platform")
    gw.seed_material_source("local-v", b"local")
    db_session.add(AsinVariantBinding(
        id="ab-local", child_asin="B0SCOPED", variant_id="local-v",
        material_id="MAT-L", created_by="test",
    ))
    db_session.commit()

    payload = _material_source_payload("OI-SCOPED", "https://fixture.invalid/scoped.png", asin="B0SCOPED")
    response = client.post("/api/order-imports", files={"file": ("o.zip", make_order_zip([payload]), "application/zip")})
    batch_id = response.json()["batch"]["id"]
    db_session.rollback()
    item = db_session.execute(select(OrderItem).where(OrderItem.first_import_batch_id == batch_id)).scalar_one()
    assert run_matcher(db_session, item) is True
    assert item.matched_variant_id == "platform-v"


def test_review_and_sales_only_confirmed(client, db_session, monkeypatch):
    from app.orders.matcher import run_matcher

    gw = _fake_gw(monkeypatch, [VariantCandidate(variant_id="v3", material_id="MAT-3", category_code="BLADE_SHOES", display_code="3-1")])
    gw.seed_material_source("v3", b"img")

    payload = _material_source_payload("OI-REV", "https://cdn.example.com/rev.png", asin="B0REV")
    resp = client.post("/api/order-imports", files={"file": ("o.zip", make_order_zip([payload]), "application/zip")})
    batch_id = resp.json()["batch"]["id"]
    db_session.rollback()
    item = db_session.execute(select(OrderItem).where(OrderItem.first_import_batch_id == batch_id)).scalar_one()
    item_id = item.id

    # 人工确认 → CONFIRMED + 建 URL/ASIN 绑定 + OrderMatchAction
    r = client.post(f"/api/order-imports/items/{item_id}/match", json={"action": "confirm", "variantId": "v3"})
    assert r.status_code == 200
    assert r.json()["matchStatus"] == "CONFIRMED"
    db_session.rollback()
    db_session.expire_all()
    assert db_session.execute(select(func.count(MaterialUrlBinding.id))).scalar_one() == 1
    assert db_session.execute(select(func.count(AsinVariantBinding.id))).scalar_one() == 1
    assert db_session.execute(select(func.count(OrderMatchAction.id)).where(OrderMatchAction.action == "MATCH_CONFIRMED")).scalar_one() == 1

    # 第二个订单保持 UNMATCHED（不确认）→ 不计销量
    payload2 = dict(payload)
    payload2["orderItemId"] = "OI-REV2"
    resp2 = client.post("/api/order-imports", files={"file": ("o2.zip", make_order_zip([payload2]), "application/zip")})
    batch2 = resp2.json()["batch"]["id"]
    db_session.rollback()

    sales = client.get("/api/order-sales").json()
    # 仅 CONFIRMED 的 quantity=2 计入
    assert sales["totalQuantity"] == 2
    assert sales["variantSales"]["v3"]["quantity"] == 2

    # 无法识别 → FAILED
    item3 = db_session.execute(select(OrderItem).where(OrderItem.first_import_batch_id == batch2)).scalar_one()
    r3 = client.post(f"/api/order-imports/items/{item3.id}/match", json={"action": "unmatch", "note": "无法识别"})
    assert r3.json()["matchStatus"] == "FAILED"
    db_session.rollback()
    assert db_session.execute(select(func.count(OrderMatchAction.id)).where(OrderMatchAction.action == "MATCH_FAILED")).scalar_one() == 1
