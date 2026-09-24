from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .normalized_models import GenericOrderRecord, NormalizedOrder


class CategoryParser(Protocol):
    parser_version: str

    def parse(self, record: GenericOrderRecord, *, category) -> NormalizedOrder: ...


class ParserRegistry:
    def __init__(self, parsers: Mapping[str, CategoryParser] | None = None) -> None:
        self._parsers = dict(parsers or {})

    def register(self, category_code: str, parser: CategoryParser) -> None:
        self._parsers[category_code.upper()] = parser

    def get(self, category_code: str) -> CategoryParser | None:
        return self._parsers.get(category_code.upper())

    def parse(self, record: GenericOrderRecord, *, category) -> NormalizedOrder:
        parser = self.get(category.code)
        if parser is None:
            return NormalizedOrder(
                order_id=record.order_id,
                order_item_id=record.order_item_id,
                child_asin=record.child_asin,
                sku=record.sku,
                quantity=record.quantity,
                category_code=category.code,
                category_name=category.name,
                category_source=category.source,
                category_confidence=category.confidence,
                parser_version="GENERIC_V1",
                parse_status="REVIEW_REQUIRED",
                details={"reason": "NO_CATEGORY_PARSER", "rawPath": record.raw_path},
            )
        return parser.parse(record, category=category)
