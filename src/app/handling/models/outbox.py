"""兼容导出：Handling Outbox 已迁移为系统级 SystemOutbox。"""

from __future__ import annotations

from src.app.sys.models import (
    SystemOutbox,
    SystemOutboxBase,
    SystemOutboxCreate,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
    SystemOutboxUpdate,
)

__all__ = [
    "SystemOutbox",
    "SystemOutboxBase",
    "SystemOutboxCreate",
    "SystemOutboxDispatchType",
    "SystemOutboxStatus",
    "SystemOutboxTargetType",
    "SystemOutboxUpdate",
]
