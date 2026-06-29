"""Adapter shim.

RuntimeReconciliationService 实际实现已迁入 runtime/orchestration/services/reconciliation/。

Phase 2 burn-down 阶段 4 (PR):facade 内部委托改本地 impl。workline/services/ 保留此 shim
供 v1 API 旧 import 路径兼容。阶段 6 WorkLine 整体清空时此 shim 删除。
"""

from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
    WorklineRuntimeReconciliationService,
    workline_runtime_reconciliation_service,
)

__all__ = [
    "WorklineRuntimeReconciliationService",
    "workline_runtime_reconciliation_service",
]
