# ============================================================================
# order-center 解析测试（纯解析，不依赖 DB / 素材平台）
#
# 覆盖真实领星 JSON 结构（version3.0 + customizationData）：
#   MATERIAL_SOURCE / FINAL_EFFECT / 买家 Logo / 定制文字 / UNKNOWN
# ============================================================================

from __future__ import annotations

import json
from pathlib import Path

from app.orders.categories.blade_shoes import BladeShoesParser
from app.orders.core.category_resolver import CategoryResolver
from app.orders.core.generic_parser import GenericOrderParser
from app.orders.core.normalized_models import CategoryResolution
from app.orders.import_service import _dedupe_key

FIXTURES = Path(__file__).parent / "fixtures" / "orders"

def _parse(body: dict):
    record = GenericOrderParser().parse(body)[0]
    cat = CategoryResolver().resolve(manual_code="BLADE_SHOES")
    return BladeShoesParser().parse(record, category=cat)


def test_final_effect_with_text():
    """thumbnail==overlay + Black → FINAL_EFFECT；inputValue/text 文字提取。"""
    result = _parse({
        "orderId": "O-1", "orderItemId": "OI-1", "asin": "B0F7XMBMTX",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "OptionCustomization", "name": "颜色", "label": "Choose Your Color",
             "optionSelection": {"name": "Black", "displayValue": "Black",
               "thumbnailImage": {"imageUrl": "https://cdn/BLACK/thumb.png"},
               "overlayImage": {"imageUrl": "https://cdn/BLACK/thumb.png"}}}
        ]}]}]},
        "version3.0": {"customizationInfo": {"surfaces": [{"areas": [
            {"customizationType": "Options", "name": "颜色", "label": "Choose Your Color", "optionValue": "Black"},
            {"customizationType": "TextPrinting", "label": "Enter Your Name", "text": "岡田"},
            {"customizationType": "TextPrinting", "label": "Enter Your Number", "text": "77"},
        ]}]}},
    })
    assert result.image_type == "FINAL_EFFECT"
    assert result.sole_color == "BLACK"
    assert result.final_effect_url and result.final_effect_url.endswith("BLACK/thumb.png")
    assert result.custom_name == "岡田"
    assert result.custom_number == "77"


def test_material_source_thumbnail_ne_overlay():
    """同一 OptionCustomization 节点 thumbnail != overlay → MATERIAL_SOURCE。"""
    result = _parse({
        "orderId": "O-2", "orderItemId": "OI-2", "asin": "B0DX24LX44",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "OptionCustomization", "name": "样式", "label": "Style",
             "optionSelection": {"name": "City", "displayValue": "City",
               "thumbnailImage": {"imageUrl": "https://cdn/MATERIAL.png"},
               "overlayImage": {"imageUrl": "https://cdn/OVERLAY.png"}}}
        ]}]}]},
        "version3.0": {"customizationInfo": {"surfaces": [{"areas": [
            {"customizationType": "TextPrinting", "label": "Enter Your Name", "text": "BUD"},
        ]}]}},
    })
    assert result.image_type == "MATERIAL_SOURCE"
    assert result.material_url.endswith("MATERIAL.png")
    assert result.custom_name == "BUD"


def test_buyer_logo_original_and_svg():
    """ImageCustomization.image.imageName → 原始 Logo；version3.0 ImagePrinting.svgImage → SVG。"""
    result = _parse({
        "orderId": "O-3", "orderItemId": "OI-3", "asin": "B1",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "ImageCustomization", "name": "图片", "label": "Add Your Logo",
             "image": {"imageName": "logo.png", "imageUrl": "https://cdn/logo.png"}}
        ]}]}]},
        "version3.0": {"customizationInfo": {"surfaces": [{"areas": [
            {"customizationType": "ImagePrinting", "label": "Logo",
             "svgImage": {"imageName": "logo.svg", "imageUrl": "https://cdn/logo.svg"}}
        ]}]}},
    })
    assert result.has_buyer_logo is True
    assert len(result.buyer_logo_original) == 1
    assert result.buyer_logo_original[0]["name"] == "logo.png"
    assert len(result.buyer_logo_svg) == 1
    assert result.buyer_logo_svg[0]["url"].endswith("logo.svg")


def test_unknown_structure_review_required():
    result = _parse({"orderId": "O-U", "orderItemId": "OI-U", "asin": "B4", "customizationData": {"type": "Mystery"}})
    assert result.image_type == "UNKNOWN"
    assert result.parse_status == "REVIEW_REQUIRED"


def test_plain_front_back_text_separated():
    result = _parse({
        "orderId": "O-T", "orderItemId": "OI-T", "asin": "B3",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "TextCustomization", "label": "Enter Your Name", "inputValue": "Alex"},
            {"type": "TextCustomization", "label": "Enter Your Number", "inputValue": "7"},
            {"type": "TextCustomization", "label": "Front Name", "inputValue": "Front-A"},
            {"type": "TextCustomization", "label": "BACK Number", "inputValue": "99"},
            {"type": "TextCustomization", "label": "Buyer Message", "inputValue": "pack carefully"},
        ]}]}]},
    })
    assert result.custom_name == "Alex"
    assert result.front_name == "Front-A"
    assert result.back_number == "99"
    assert result.buyer_request == "pack carefully"


def test_sanitized_real_export_fixtures_keep_image_rules():
    fixture = json.loads((FIXTURES / "blade_shoes_real_export_sanitized.json").read_text(encoding="utf-8"))
    material = _parse(fixture["cases"]["MATERIAL_SOURCE"])
    final = _parse(fixture["cases"]["FINAL_EFFECT"])
    assert material.image_type == "MATERIAL_SOURCE"
    assert material.material_url == "https://fixture.invalid/material.png"
    assert material.sole_color is None
    assert final.image_type == "FINAL_EFFECT"
    assert final.final_effect_url == "https://fixture.invalid/final-black.png"
    assert final.sole_color == "BLACK"


def _preview_case(label: str) -> dict:
    return {
        "orderId": "O-P", "orderItemId": f"OI-{label}", "asin": "B-P",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "OptionCustomization", "label": label,
             "optionSelection": {"name": label,
               "thumbnailImage": {"imageUrl": "https://fixture.invalid/entry.png"},
               "overlayImage": {"imageUrl": "https://fixture.invalid/preview.png"}}}
        ]}]}]},
    }


def test_add_your_logo_entry_is_preview_not_material():
    result = _parse(_preview_case("Add Your Logo"))
    assert result.image_type == "PREVIEW_ONLY"
    assert result.material_url is None


def test_front_blank_entry_is_preview_not_material():
    result = _parse(_preview_case("Front Blank"))
    assert result.image_type == "PREVIEW_ONLY"
    assert result.material_url is None


def test_logo_alone_does_not_turn_unknown_image_structure_into_preview():
    result = _parse({
        "orderId": "O-L", "orderItemId": "OI-L", "asin": "B-L",
        "customizationData": {"type": "ImageCustomization", "image": {
            "imageName": "buyer-logo.png", "imageUrl": "https://fixture.invalid/buyer-logo.png"
        }},
    })
    assert result.has_buyer_logo is True
    assert result.image_type == "UNKNOWN"
    assert result.parse_status == "REVIEW_REQUIRED"


def test_fallback_dedupe_does_not_depend_on_zip_path():
    body = {"orderId": "O-NO-ITEM", "asin": "B-NO-ITEM", "sku": "BLADE-X", "quantity": 1}
    left = GenericOrderParser().parse(body, source_path="a/one.json")[0]
    right = GenericOrderParser().parse(body, source_path="other/two.json")[0]
    assert _dedupe_key(left, left.raw_path) == _dedupe_key(right, right.raw_path)
