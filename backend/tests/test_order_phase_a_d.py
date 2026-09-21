from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.orders.categories.blade_shoes import BladeShoesParser
from app.orders.core.category_resolver import CategoryResolver
from app.orders.core.generic_parser import GenericOrderParser
from app.orders.core.parser_registry import ParserRegistry
from app.orders.core.normalized_models import CategoryResolution

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "orders" / "blade_shoes"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _zip(payload: dict, name: str = "Files/order.json") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, json.dumps(payload, ensure_ascii=False))
    return out.getvalue()


def _category() -> CategoryResolution:
    return CategoryResolver().resolve(manual_code="BLADE_SHOES")


def _parse(body: dict):
    record = GenericOrderParser().parse(body)[0]
    return BladeShoesParser().parse(record, category=_category())


def test_blade_shoes_material_source_and_buyer_logo():
    result = _parse(_fixture("material_source.json"))
    assert result.image_type == "MATERIAL_SOURCE"
    assert result.material_url.endswith("material.png")
    assert result.final_effect_url is None
    assert result.has_buyer_logo is True
    assert result.buyer_logo_original[0]["url"].endswith("logo.png")
    assert result.buyer_logo_svg[0]["url"].endswith("logo.svg")
    assert result.custom_name == "Alex" and result.custom_number == "7"
    assert result.parser_version == "BLADE_SHOES_V1"


def test_blade_shoes_final_effect_sole_color_is_not_material_source():
    result = _parse(_fixture("final_effect.json"))
    assert result.image_type == "FINAL_EFFECT"
    assert result.final_effect_url.endswith("final.png")
    assert result.sole_color == "White"
    assert result.material_url is None


def test_preview_and_unknown_are_explicit_review_states():
    preview = _parse({"orderId": "O-3", "orderItemId": "OI-3", "ImageCustomization": {"imageName": "Add Your Logo"}})
    assert preview.image_type == "PREVIEW_ONLY" and preview.parse_status == "PARSED"
    front_blank = _parse({"orderId": "O-3B", "orderItemId": "OI-3B", "OptionCustomization": {"optionSelection": {"thumbnailImage": {"imageName": "Front Blank"}}}})
    assert front_blank.image_type == "PREVIEW_ONLY" and front_blank.parse_status == "PARSED"
    unknown = _parse(_fixture("unknown.json"))
    assert unknown.image_type == "UNKNOWN" and unknown.parse_status == "REVIEW_REQUIRED"


def test_generic_parser_does_not_contain_blade_rules():
    record = GenericOrderParser().parse({"orderId": "O-5", "orderItemId": "OI-5", "TextPrinting": {"label": "Front Name", "value": "Sam"}})[0]
    assert record.text_fields[0].label == "Front Name"
    # Category parser, not generic parser, decides where the text belongs.
    result = BladeShoesParser().parse(record, category=_category())
    assert result.front_name == "Sam"


def test_blade_text_fields_keep_plain_and_side_specific_values():
    result = _parse({
        "orderId": "O-TEXT", "orderItemId": "OI-TEXT",
        "TextPrinting": [
            {"label": "Enter Your Name", "value": "Alex"},
            {"label": "Front Name", "value": "Front-A"},
            {"label": "BACK Number", "value": "99"},
            {"label": "Buyer Message", "value": "Pack carefully"},
        ],
    })
    assert result.custom_name == "Alex"
    assert result.front_name == "Front-A"
    assert result.back_number == "99"
    assert result.buyer_request == "Pack carefully"


def test_parser_registry_unknown_category_is_review_required():
    registry = ParserRegistry()
    record = GenericOrderParser().parse({"orderId": "O-6", "orderItemId": "OI-6"})[0]
    category = CategoryResolver().resolve(manual_code="UNKNOWN")
    result = registry.parse(record, category=category)
    assert result.parser_version == "GENERIC_V1"
    assert result.parse_status == "REVIEW_REQUIRED"


def test_order_import_keeps_raw_assets_and_dedupes_exact_repeat(client):
    payload = {
        "orderId": "O-IMPORT-1", "orderItemId": "OI-IMPORT-1", "sku": "BLADE-RED",
        "OptionCustomization": {"optionSelection": {"thumbnailImage": {"imageUrl": "https://cdn/m.png"}, "overlayImage": {"imageUrl": "https://cdn/e.png"}}},
    }
    data = _zip(payload)
    first = client.post(
        "/api/order-imports",
        files={"file": ("orders.zip", data, "application/zip")},
        data={"actor": "tester"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["batch"]["status"] == "PARSED"
    assert first_body["summary"]["newItemCount"] == 1
    items = client.get(f"/api/order-imports/{first_body['batch']['id']}/items")
    assert items.status_code == 200 and items.json()[0]["categoryCode"] == "BLADE_SHOES"
    assert items.json()[0]["parserVersion"] == "BLADE_SHOES_V1"

    second = client.post(
        "/api/order-imports",
        files={"file": ("orders-copy.zip", data, "application/zip")},
        data={"actor": "tester"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["batch"]["status"] == "DUPLICATE"
    assert second.json()["summary"]["newItemCount"] == 0
    assert second.json()["summary"]["duplicateItemCount"] == 1
