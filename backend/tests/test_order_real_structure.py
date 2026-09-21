# ============================================================================
# 订单识别：真实领星 JSON 结构（version3.0 + customizationData）解析测试
#
# 覆盖（用户验收清单）：
#   1. MATERIAL_SOURCE（同节点 thumbnailImage != overlayImage）
#   2. FINAL_EFFECT（thumbnailImage == overlayImage + Black/White 鞋底色）
#   3. 买家原始 Logo（ImageCustomization.image.imageName）+
#      SVG（version3.0 ImagePrinting.svgImage）
#   4. Add Your Logo / Front Blank → PREVIEW_ONLY，不进自动匹配
#   5. 普通 Name / Number → custom_name / custom_number
#   6. Front 字段 → front_name / front_number
#   7. BACK 字段 → back_name / back_number
#   8. UNKNOWN → REVIEW_REQUIRED
#   9. 重复 orderItemId 不重复计销量（第二次导入不新增 quantity）
#   10. 买家 Logo 是订单生产附件：不创建 Material / Variant
# ============================================================================

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.db.models import Material, MaterialVariant
from app.orders.categories.blade_shoes import BladeShoesParser
from app.orders.core.category_resolver import CategoryResolver
from app.orders.core.generic_parser import GenericOrderParser
from app.orders.core.normalized_models import CategoryResolution

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "orders" / "blade_shoes"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _zip(payload: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Files/order.json", json.dumps(payload, ensure_ascii=False))
    return out.getvalue()


def _category() -> CategoryResolution:
    return CategoryResolver().resolve(manual_code="BLADE_SHOES")


def _parse(body: dict):
    record = GenericOrderParser().parse(body)[0]
    return BladeShoesParser().parse(record, category=_category())


def test_real_structure_final_effect_with_text():
    """真实领星结构：thumbnail==overlay + Black → FINAL_EFFECT；inputValue 文字提取。"""
    result = _parse(_fixture("lianxing_final_effect.json"))
    assert result.image_type == "FINAL_EFFECT"
    assert result.sole_color == "Black"
    assert result.final_effect_url and result.final_effect_url.endswith("BLACK/thumb.png")
    assert result.material_url is None
    # customizationData 的 TextCustomization 用 inputValue；version3.0 用 text —— 都要能提取
    assert result.custom_name == "岡田"
    assert result.custom_number == "77"


def test_real_structure_material_source():
    """真实领星结构：同一 OptionCustomization thumbnail != overlay → MATERIAL_SOURCE。"""
    result = _parse(_fixture("lianxing_material_source.json"))
    assert result.image_type == "MATERIAL_SOURCE"
    assert result.material_url and result.material_url.endswith("CITY/MATERIAL.png")
    assert result.final_effect_url is None
    assert result.custom_name == "BUD" and result.custom_number == "276"


def test_buyer_logo_original_and_svg_both_kept():
    """ImageCustomization.image.imageName → 原始 Logo；version3.0 ImagePrinting.svgImage → SVG。"""
    result = _parse(_fixture("lianxing_buyer_logo.json"))
    assert result.has_buyer_logo is True
    assert len(result.buyer_logo_original) == 1
    assert result.buyer_logo_original[0]["name"] == "buyer-logo-original.png"
    assert len(result.buyer_logo_svg) == 1
    assert result.buyer_logo_svg[0]["url"].endswith("buyer-logo.svg")
    # 有原始 Logo + FINAL_EFFECT 图片结构（White）
    assert result.image_type == "FINAL_EFFECT"
    assert result.sole_color == "White"


def test_add_your_logo_and_front_blank_are_preview_only():
    """Add Your Logo / Front Blank 入口预览图 → PREVIEW_ONLY，不得进入自动匹配。"""
    add_your_logo = _parse({
        "orderId": "O-AL", "orderItemId": "OI-AL", "asin": "B1",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "ImageCustomization", "name": "上传图片 1", "label": "Add Your Logo",
             "image": {"imageName": "Add Your Logo", "imageUrl": "https://cdn/preview.png"}}
        ]}]}]},
    })
    assert add_your_logo.image_type == "PREVIEW_ONLY"
    assert add_your_logo.parse_status == "PARSED"

    front_blank = _parse({
        "orderId": "O-FB", "orderItemId": "OI-FB", "asin": "B2",
        "OptionCustomization": {"optionSelection": {"thumbnailImage": {"imageUrl": "https://cdn/front-blank.png"}}},
    })
    assert front_blank.image_type == "PREVIEW_ONLY"


def test_plain_front_back_text_fields():
    """普通 Name/Number 与明确 Front/BACK 字段分离；普通字段不复制到 front/back。"""
    result = _parse({
        "orderId": "O-T", "orderItemId": "OI-T", "asin": "B3",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "TextCustomization", "label": "Enter Your Name", "inputValue": "Alex"},
            {"type": "TextCustomization", "label": "Enter Your Number", "inputValue": "7"},
            {"type": "TextCustomization", "label": "Front Name", "inputValue": "Front-A"},
            {"type": "TextCustomization", "label": "BACK Number", "inputValue": "99"},
            {"type": "TextCustomization", "label": "Buyer Message", "inputValue": "Pack carefully"},
        ]}]}]},
    })
    assert result.custom_name == "Alex"
    assert result.custom_number == "7"
    assert result.front_name == "Front-A"
    assert result.back_number == "99"
    assert result.buyer_request == "Pack carefully"
    # 普通 custom_name 不复制到 front/back
    assert result.front_name == "Front-A"  # 独立字段，不是从 custom_name 复制
    assert not (result.front_name == result.custom_name and result.custom_name == "Alex")


def test_unknown_structure_is_review_required():
    """没见过的新结构 → UNKNOWN + REVIEW_REQUIRED，禁止随便取第一张图。"""
    result = _parse({"orderId": "O-U", "orderItemId": "OI-U", "asin": "B4", "customizationData": {"type": "MysteryNode"}})
    assert result.image_type == "UNKNOWN"
    assert result.parse_status == "REVIEW_REQUIRED"
    assert result.material_url is None and result.final_effect_url is None


def test_duplicate_order_item_does_not_double_count(client):
    """重复导入同一 orderItemId：第二次不新增订单行（销量只计一次）。"""
    payload = _fixture("lianxing_material_source.json")
    data = _zip(payload)
    first = client.post("/api/order-imports", files={"file": ("a.zip", data, "application/zip")}, data={"actor": "tester"})
    assert first.status_code == 200
    assert first.json()["summary"]["newItemCount"] == 1
    item_id = first.json()["batch"]["totalItemCount"]

    # 不同文件名、相同 orderItemId → 去重，不新增
    payload2 = dict(payload)
    second = client.post("/api/order-imports", files={"file": ("b.zip", data, "application/zip")}, data={"actor": "tester"})
    assert second.status_code == 200
    body2 = second.json()
    assert body2["summary"]["newItemCount"] == 0
    assert body2["summary"]["duplicateItemCount"] == 1

    # 库里只有一条该 orderItemId（销量不翻倍）
    import pymysql

    from app.core.config import settings

    conn = pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
                           password=settings.mysql_password, database=settings.mysql_database, charset="utf8mb4")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(quantity) FROM order_items WHERE order_item_id=%s", (payload["orderItemId"],))
    count, total_qty = cur.fetchone()
    conn.close()
    assert count == 1
    assert total_qty == payload["quantity"]


def test_buyer_logo_is_order_attachment_not_material(client):
    """买家 Logo 属于订单生产附件，不得写入 Material / Variant 素材库。"""
    from sqlalchemy import func, select

    from app.db.session import SessionLocal

    payload = _fixture("lianxing_buyer_logo.json")
    data = _zip(payload)
    before_m = None
    db = SessionLocal()
    try:
        before_m = db.execute(select(func.count(Material.id))).scalar_one()
        before_v = db.execute(select(func.count(MaterialVariant.id))).scalar_one()
    finally:
        db.close()
    r = client.post("/api/order-imports", files={"file": ("logo.zip", data, "application/zip")}, data={"actor": "tester"})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        after_m = db.execute(select(func.count(Material.id))).scalar_one()
        after_v = db.execute(select(func.count(MaterialVariant.id))).scalar_one()
    finally:
        db.close()
    assert after_m == before_m, "订单导入不得创建 Material"
    assert after_v == before_v, "订单导入不得创建 MaterialVariant"
