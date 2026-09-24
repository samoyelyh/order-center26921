from __future__ import annotations

import re

from app.orders.core.normalized_models import GenericOrderRecord, NormalizedOrder


class BladeShoesParser:
    """BLADE_SHOES_V1 rules; generic JSON extraction stays outside this class."""

    parser_version = "BLADE_SHOES_V1"
    _preview_pattern = re.compile(r"add\s+your\s+logo|front\s+blank", re.IGNORECASE)

    def parse(self, record: GenericOrderRecord, *, category) -> NormalizedOrder:
        normalized = NormalizedOrder(
            order_id=record.order_id,
            order_item_id=record.order_item_id,
            child_asin=record.child_asin,
            sku=record.sku,
            quantity=record.quantity,
            category_code=category.code,
            category_name=category.name,
            category_source=category.source,
            category_confidence=category.confidence,
            parser_version=self.parser_version,
            buyer_logo_original=record.buyer_logo_original,
            buyer_logo_svg=record.buyer_logo_svg,
            has_buyer_logo=bool(record.buyer_logo_original or record.buyer_logo_svg),
            details={"rawPath": record.raw_path},
        )

        self._text_fields(normalized, record)
        material = next(
            (candidate for candidate in record.image_candidates
             if not self._is_preview(candidate)
             and candidate.thumbnail_url and candidate.overlay_url
             and candidate.thumbnail_url != candidate.overlay_url),
            None,
        )
        if material is not None:
            normalized.image_type = "MATERIAL_SOURCE"
            normalized.material_url = material.thumbnail_url
            normalized.image_structure_path = material.path
            normalized.parse_status = "PARSED"
            normalized.details["materialOverlayUrl"] = material.overlay_url
            normalized.details["source"] = {
                "label": material.label, "optionValue": self._sole_color(record),
                "surface": None, "jsonPath": material.path,
            }
            return normalized

        final = next(
            (candidate for candidate in record.image_candidates
             if not self._is_preview(candidate)
             and candidate.thumbnail_url and candidate.overlay_url and candidate.thumbnail_url == candidate.overlay_url
             and self._sole_color(record)),
            None,
        )
        if final is not None:
            normalized.image_type = "FINAL_EFFECT"
            normalized.final_effect_url = final.thumbnail_url
            normalized.image_structure_path = final.path
            normalized.sole_color = self._sole_color(record)
            normalized.details["source"] = {
                "label": final.label, "optionValue": normalized.sole_color,
                "surface": None, "jsonPath": final.path,
            }
            normalized.parse_status = "PARSED"
            return normalized

        preview = next((candidate for candidate in record.image_candidates if self._is_preview(candidate)), None)
        if preview is not None:
            normalized.image_type = "PREVIEW_ONLY"
            normalized.parse_status = "PARSED"
            normalized.image_structure_path = preview.path
            normalized.details["source"] = {
                "label": preview.label, "optionValue": None,
                "surface": None, "jsonPath": preview.path,
            }
            return normalized

        normalized.image_type = "UNKNOWN"
        normalized.parse_status = "REVIEW_REQUIRED"
        normalized.details["reason"] = "NO_KNOWN_IMAGE_STRUCTURE"
        normalized.details["source"] = {
            "label": None, "optionValue": None, "surface": None, "jsonPath": None,
        }
        return normalized

    def _is_preview(self, candidate) -> bool:
        return bool(self._preview_pattern.search(candidate.label or ""))

    def _sole_color(self, record: GenericOrderRecord) -> str | None:
        values = [*record.option_values, *(field.value for field in record.text_fields)]
        for value in values:
            match = re.search(r"\b(black|white)\b", value, flags=re.IGNORECASE)
            if match:
                return match.group(1).upper()
        return None

    def _text_fields(self, normalized: NormalizedOrder, record: GenericOrderRecord) -> None:
        for field in record.text_fields:
            label = re.sub(r"\s+", " ", field.label.strip()).lower()
            value = field.value.strip()
            if not value:
                continue
            if "request" in label or "message" in label or "留言" in label:
                normalized.buyer_request = value
            side = "front" if re.search(r"\bfront\b", label) else "back" if re.search(r"\bback\b", label) else None
            is_number = bool(re.search(r"number|号码|数字", label))
            is_name = bool(re.search(r"name|姓名", label))
            if side == "front" and is_name:
                normalized.front_name = value
            elif side == "front" and is_number:
                normalized.front_number = value
            elif side == "back" and is_name:
                normalized.back_name = value
            elif side == "back" and is_number:
                normalized.back_number = value
            elif is_name:
                normalized.custom_name = value
            elif is_number:
                normalized.custom_number = value
