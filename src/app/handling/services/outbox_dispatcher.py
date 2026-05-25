"""兼容导出：Handling outbox dispatcher 已迁移为 SystemOutboxEngine。"""

from __future__ import annotations

from src.app.sys.services import (
    DispatchResult,
)
from src.app.sys.services import (
    SystemOutboxEngine as SystemOutboxDispatcher,
)
from src.app.sys.services import (
    system_outbox_engine as system_outbox_dispatcher,
)

__all__ = ["DispatchResult", "SystemOutboxDispatcher", "system_outbox_dispatcher"]
