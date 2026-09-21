from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .normalized_models import GenericImageCandidate, GenericOrderRecord, GenericTextField


def _walk(value: Any, path: str = "$", key: str | None = None) -> Iterator[tuple[str, str | None, Any]]:
    yield path, key, value
    if isinstance(value, dict):
        for child_key, child in value.items():
            child_path = f"{path}.{child_key}"
            yield from _walk(child, child_path, str(child_key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]", key)


def _scalar(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _value(root: Any, names: set[str]) -> tuple[str | None, str]:
    lowered = {name.lower() for name in names}
    for path, key, value in _walk(root):
        if key is not None and key.lower() in lowered:
            text = _scalar(value)
            if text is not None:
                return text, path
    return None, ""


def _url(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("imageUrl", "imageURL", "url", "src", "href"):
            if key in value and _scalar(value[key]):
                return _scalar(value[key])
    return None


def _labelled_value(node: dict[str, Any]) -> tuple[str | None, str | None]:
    label = None
    for key in ("label", "name", "field", "title", "optionName", "customizationName"):
        if _scalar(node.get(key)):
            label = _scalar(node[key])
            break
    value = None
    # Amazon 定制 JSON 的值字段随版本不同：customizationData 用 inputValue，
    # version3.0 规范化输出用 text；两类都必须能取到。
    for key in ("value", "text", "content", "printText", "inputValue", "selection", "selectedValue"):
        if _scalar(node.get(key)):
            value = _scalar(node[key])
            break
    return label, value


class GenericOrderParser:
    """Amazon-generic JSON extraction only; no product-specific image rules."""

    parser_version = "GENERIC_V1"

    def parse(self, document: Any, *, source_path: str = "$") -> list[GenericOrderRecord]:
        if isinstance(document, list):
            return [self._parse_one(item, source_path=f"{source_path}[{i}]") for i, item in enumerate(document) if isinstance(item, dict)]

        if not isinstance(document, dict):
            return []
        order_lists = {
            "orderitems", "orderitemlist", "items", "orderlines", "orderlineslist",
        }
        candidates: list[tuple[str, dict[str, Any]]] = []
        for path, key, value in _walk(document):
            if key and key.lower() in order_lists and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        candidates.append((f"{path}[{index}]", item))
                if candidates:
                    break
        if not candidates:
            candidates = [(source_path, document)]
        return [self._parse_one(item, source_path=path, parent=document) for path, item in candidates]

    def _parse_one(self, item: dict[str, Any], *, source_path: str, parent: dict[str, Any] | None = None) -> GenericOrderRecord:
        root = {**(parent or {}), **item}
        order_id, _ = _value(root, {"orderId", "amazonOrderId", "orderNumber"})
        item_id, _ = _value(item, {"orderItemId", "order_item_id", "itemId"})
        asin, _ = _value(item, {"asin", "childAsin", "childASIN"})
        sku, _ = _value(item, {"sku", "sellerSku", "sellerSKU"})
        quantity_text, _ = _value(item, {"quantity", "itemQuantity", "qty"})
        try:
            quantity = max(0, int(float(quantity_text or "1")))
        except ValueError:
            quantity = 1

        candidates: list[GenericImageCandidate] = []
        # Preserve all generic ImageCustomization / ImagePrinting nodes for category parsers.
        buyer_original: list[dict[str, str | None]] = []
        buyer_svg: list[dict[str, str | None]] = []
        text_fields: list[GenericTextField] = []
        option_values: list[str] = []
        for path, key, value in _walk(item):
            if not isinstance(value, dict):
                continue
            # 领星 JSON 的节点名在 `type` 字段（{"type":"OptionCustomization"}），
            # 部分历史结构才用 key 名；两种都要兼容。
            node_type = str(value.get("type") or key or "").lower()
            if node_type == "optioncustomization":
                selections = value.get("optionSelection") or value.get("optionSelections") or value.get("selection") or value
                nodes = selections if isinstance(selections, list) else [selections]
                for selection in nodes:
                    if not isinstance(selection, dict):
                        continue
                    thumb = selection.get("thumbnailImage")
                    overlay = selection.get("overlayImage")
                    thumb_url = _url(thumb)
                    overlay_url = _url(overlay)
                    image_name = _scalar(thumb.get("imageName")) if isinstance(thumb, dict) else None
                    if thumb_url or overlay_url or image_name:
                        label, _ = _labelled_value(selection)
                        candidates.append(GenericImageCandidate(
                            thumbnail_url=thumb_url,
                            overlay_url=overlay_url,
                            image_name=image_name,
                            path=f"{path}.optionSelection",
                            label=label,
                        ))
            elif node_type == "imagecustomization":
                image = value.get("image") or value.get("imageUrl")
                image_url = _url(image)
                image_name = _scalar(image.get("imageName")) if isinstance(image, dict) else _scalar(value.get("imageName"))
                if image_url or image_name:
                    buyer_original.append({"url": image_url, "name": image_name, "path": path})
            elif node_type == "imageprinting":
                svg = value.get("svgImage") or value.get("svg")
                svg_url = _url(svg)
                svg_name = _scalar(svg.get("imageName")) if isinstance(svg, dict) else None
                if svg_url or svg_name:
                    buyer_svg.append({"url": svg_url, "name": svg_name, "path": path})
            elif node_type in {"textprinting", "textcustomization"}:
                # customizationData 的 TextCustomization 用 inputValue；version3.0 的 TextPrinting 用 text
                label, text = _labelled_value(value)
                if label and text:
                    text_fields.append(GenericTextField(label=label, value=text, path=path))
            elif node_type in {"option", "optionselection", "selectedoption"}:
                label, text = _labelled_value(value)
                if text:
                    option_values.append(text)
                if label and text:
                    text_fields.append(GenericTextField(label=label, value=text, path=path))

        # 领星 version3.0 规范化输出：surfaces[].areas[] 用 customizationType 标记节点类型。
        # 这份结构比 customizationData 规整，是文字/选项/买家附件的可靠来源。
        v3 = self._v3_areas(item)
        for area in v3:
            ctype = str(area.get("customizationType") or "").lower()
            label = _scalar(area.get("label"))
            if ctype == "textprinting":
                text = _scalar(area.get("text")) or _scalar(area.get("inputValue")) or _scalar(area.get("printText"))
                if label and text:
                    text_fields.append(GenericTextField(label=label, value=text, path="$.version3.0"))
            elif ctype == "options":
                value = _scalar(area.get("optionValue")) or _scalar(area.get("selectedValue"))
                if value:
                    option_values.append(value)
            elif ctype == "imageprinting":
                svg = area.get("svgImage") or area.get("svg")
                svg_url = _url(svg)
                svg_name = _scalar(svg.get("imageName")) if isinstance(svg, dict) else None
                if svg_url or svg_name:
                    buyer_svg.append({"url": svg_url, "name": svg_name, "path": "$.version3.0"})
            elif ctype == "imagecustomization":
                image = area.get("image") or area.get("imageUrl")
                image_url = _url(image)
                image_name = _scalar(image.get("imageName")) if isinstance(image, dict) else None
                if image_url or image_name:
                    buyer_original.append({"url": image_url, "name": image_name, "path": "$.version3.0"})

        return GenericOrderRecord(
            order_id=order_id or "UNKNOWN_ORDER",
            order_item_id=item_id,
            child_asin=asin,
            sku=sku,
            quantity=quantity,
            raw=item,
            raw_path=source_path,
            image_candidates=candidates,
            buyer_logo_original=buyer_original,
            buyer_logo_svg=buyer_svg,
            text_fields=text_fields,
            option_values=option_values,
        )

    def _v3_areas(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        """领星 version3.0.customizationInfo.surfaces[].areas[] 展开成平铺列表。"""
        out: list[dict[str, Any]] = []
        v3 = item.get("version3.0") if isinstance(item, dict) else None
        if not isinstance(v3, dict):
            return out
        info = v3.get("customizationInfo")
        if not isinstance(info, dict):
            return out
        surfaces = info.get("surfaces")
        if not isinstance(surfaces, list):
            return out
        for surface in surfaces:
            if not isinstance(surface, dict):
                continue
            areas = surface.get("areas")
            if isinstance(areas, list):
                out.extend(a for a in areas if isinstance(a, dict))
        return out
