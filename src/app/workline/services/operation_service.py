"""Adapter shim — OperationService 实际实现已迁入 runtime/orchestration/services/intent/。

Phase 2 burn-down 阶段 4 (PR):workline/services/ 保留此 shim 供 v1 API 旧 import 路径兼容。
阶段 6 WorkLine 整体清空时此 shim 删除。
"""

from src.app.runtime.orchestration.services.intent.operation_service import (
    WorklineOperationService,
    workline_operation_service,
)

__all__ = ["WorklineOperationService", "workline_operation_service"]
