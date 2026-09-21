from __future__ import annotations

from collections.abc import Mapping

from .normalized_models import CategoryResolution

CATEGORY_NAMES = {
    "BLADE_SHOES": "刀锋鞋",
    "FOOTBALL_JERSEY": "足球球衣",
    "SOCCER_JERSEY": "足球衫",
    "UNKNOWN": "未知品类",
}


class CategoryResolver:
    """Resolve a stable code from explicit facts; never inspect image pixels."""

    def resolve(
        self,
        *,
        manual_code: str | None = None,
        sku: str | None = None,
        asin: str | None = None,
        asin_bindings: Mapping[str, str] | None = None,
        erp_categories: Mapping[str, str] | None = None,
        design_package_categories: Mapping[str, str] | None = None,
        design_package_id: str | None = None,
    ) -> CategoryResolution:
        # An explicit import override is the safest correction path for operators.
        if manual_code and manual_code.strip():
            return self._result(manual_code, "MANUAL", "1.0")
        if sku:
            normalized = sku.upper()
            for prefix, code in (("BLADE", "BLADE_SHOES"), ("BLADE_SHOES", "BLADE_SHOES"), ("JERSEY", "FOOTBALL_JERSEY")):
                if normalized.startswith(prefix):
                    return self._result(code, "SKU", "0.95")
        if asin and asin_bindings and asin in asin_bindings:
            return self._result(asin_bindings[asin], "ASIN_BINDING", "1.0")
        if sku and erp_categories and sku in erp_categories:
            return self._result(erp_categories[sku], "ERP", "0.95")
        if design_package_id and design_package_categories and design_package_id in design_package_categories:
            return self._result(design_package_categories[design_package_id], "DESIGN_PACKAGE", "0.9")
        return self._result("UNKNOWN", "UNKNOWN", "0")

    def _result(self, code: str, source: str, confidence: str) -> CategoryResolution:
        stable = code.strip().upper() or "UNKNOWN"
        return CategoryResolution(stable, CATEGORY_NAMES.get(stable, stable), source, confidence)
