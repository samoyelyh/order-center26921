# ============================================================================
# order-center 通用工具（new_id / utcnow）
# ============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = ["new_id", "utcnow"]
