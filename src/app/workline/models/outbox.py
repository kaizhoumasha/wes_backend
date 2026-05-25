"""兼容导出：Workline Outbox 已迁移为系统级 SystemOutbox。"""

from __future__ import annotations

from src.app.sys.models import (
    SystemOutbox as WorklineOutbox,
)
from src.app.sys.models import (
    SystemOutboxBase as WorklineOutboxBase,
)
from src.app.sys.models import (
    SystemOutboxCreate as WorklineOutboxCreate,
)
from src.app.sys.models import (
    SystemOutboxDispatchType as DispatchType,
)
from src.app.sys.models import (
    SystemOutboxStatus as OutboxStatus,
)
from src.app.sys.models import (
    SystemOutboxTargetType as TargetType,
)
from src.app.sys.models import (
    SystemOutboxUpdate as WorklineOutboxUpdate,
)

__all__ = [
    "DispatchType",
    "OutboxStatus",
    "TargetType",
    "WorklineOutbox",
    "WorklineOutboxBase",
    "WorklineOutboxCreate",
    "WorklineOutboxUpdate",
]
