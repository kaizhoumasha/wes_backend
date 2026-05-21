"""WorkLine 满箱交换任务兼容服务。

Phase B 后满箱交换的链路证据由 WorkLine Inbox / Outbox / Timeline / RuntimeHold 承载，
resource 域不再镜像 FullBoxExchangeTask 或 WMS 回写证据表。
"""

from __future__ import annotations

from typing import Any


class WorklineFullBoxExchangeTaskService:
    """兼容旧编排调用点的无持久化服务。"""

    async def record_requested_from_external_request(self, **kwargs: Any) -> None:
        _ = kwargs

    async def record_callback_from_external_http(self, **kwargs: Any) -> None:
        _ = kwargs


workline_full_box_exchange_task_service = WorklineFullBoxExchangeTaskService()


__all__ = ["WorklineFullBoxExchangeTaskService", "workline_full_box_exchange_task_service"]
