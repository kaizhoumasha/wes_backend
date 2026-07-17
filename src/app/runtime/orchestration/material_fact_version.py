"""MaterialUnit 可变事实版本的稳定提取。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.utils.value_normalization import optional_int


def material_unit_fact_version(material_unit: Any | None) -> int | None:
    """优先使用乐观锁版本；旧表以 updated_at 的 UTC 毫秒值作为事实版本。"""

    if material_unit is None:
        return None
    version = optional_int(getattr(material_unit, "version", None))
    if version is not None:
        return version
    updated_at = getattr(material_unit, "updated_at", None)
    if not isinstance(updated_at, datetime):
        return None
    aware_updated_at = updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=UTC)
    return int(aware_updated_at.timestamp() * 1000)


__all__ = ["material_unit_fact_version"]
