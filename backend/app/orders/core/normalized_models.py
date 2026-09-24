from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenericImageCandidate:
    thumbnail_url: str | None = None
    overlay_url: str | None = None
    image_name: str | None = None
    path: str = ""
    label: str | None = None
    sole_color: str | None = None


@dataclass
class GenericTextField:
    label: str
    value: str
    path: str = ""


@dataclass
class GenericOrderRecord:
    order_id: str
    order_item_id: str | None
    child_asin: str | None
    sku: str | None
    quantity: int
    raw: dict[str, Any]
    raw_path: str
    image_candidates: list[GenericImageCandidate] = field(default_factory=list)
    buyer_logo_original: list[dict[str, str | None]] = field(default_factory=list)
    buyer_logo_svg: list[dict[str, str | None]] = field(default_factory=list)
    text_fields: list[GenericTextField] = field(default_factory=list)
    option_values: list[str] = field(default_factory=list)


@dataclass
class CategoryResolution:
    code: str
    name: str
    source: str
    confidence: str


@dataclass
class NormalizedOrder:
    order_id: str
    order_item_id: str | None
    child_asin: str | None
    sku: str | None
    quantity: int
    category_code: str
    category_name: str
    category_source: str
    category_confidence: str
    parser_version: str
    custom_name: str | None = None
    custom_number: str | None = None
    front_name: str | None = None
    front_number: str | None = None
    back_name: str | None = None
    back_number: str | None = None
    buyer_request: str | None = None
    has_buyer_logo: bool = False
    buyer_logo_original: list[dict[str, str | None]] = field(default_factory=list)
    buyer_logo_svg: list[dict[str, str | None]] = field(default_factory=list)
    sole_color: str | None = None
    image_type: str = "UNKNOWN"
    material_url: str | None = None
    final_effect_url: str | None = None
    image_structure_path: str | None = None
    parse_status: str = "REVIEW_REQUIRED"
    matched_variant_id: str | None = None
    matched_material_id: str | None = None
    match_method: str | None = None
    match_score: float | None = None
    match_status: str = "NOT_STARTED"
    details: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "orderId": self.order_id,
            "orderItemId": self.order_item_id,
            "childAsin": self.child_asin,
            "sku": self.sku,
            "quantity": self.quantity,
            "categoryCode": self.category_code,
            "categoryName": self.category_name,
            "categorySource": self.category_source,
            "categoryConfidence": self.category_confidence,
            "parserVersion": self.parser_version,
            "customName": self.custom_name,
            "customNumber": self.custom_number,
            "frontName": self.front_name,
            "frontNumber": self.front_number,
            "backName": self.back_name,
            "backNumber": self.back_number,
            "buyerRequest": self.buyer_request,
            "hasBuyerLogo": self.has_buyer_logo,
            "buyerLogoOriginal": self.buyer_logo_original,
            "buyerLogoSvg": self.buyer_logo_svg,
            "soleColor": self.sole_color,
            "imageType": self.image_type,
            "materialUrl": self.material_url,
            "finalEffectUrl": self.final_effect_url,
            "imageStructurePath": self.image_structure_path,
            "parseStatus": self.parse_status,
            "matchedVariantId": self.matched_variant_id,
            "matchedMaterialId": self.matched_material_id,
            "matchMethod": self.match_method,
            "matchScore": self.match_score,
            "matchStatus": self.match_status,
            "details": self.details,
        }
