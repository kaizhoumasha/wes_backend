"""通用 Runtime reconciliation 会话防护。

DeviceCommand 的 delivery-unknown、ACK/CALLBACK deadline 和人工对账由
``src.app.device`` 最终聚合独立拥有；本模块只保留与设备无关的会话隔离断言。
"""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.models.session import RuntimeReconciliationState


class WorklineRuntimeReconciliationService:
    """阻止仍处于人工对账隔离中的会话被旧 runtime 操作推进。"""

    @staticmethod
    def assert_not_pending_reconciliation(session: Any) -> None:
        if getattr(session, "reconciliation_state", None) == RuntimeReconciliationState.PENDING:
            raise ValueError("session is pending runtime reconciliation")


workline_runtime_reconciliation_service = WorklineRuntimeReconciliationService()


__all__ = ["WorklineRuntimeReconciliationService", "workline_runtime_reconciliation_service"]
