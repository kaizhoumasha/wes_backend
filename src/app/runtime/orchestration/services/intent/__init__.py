"""Intent 子目录 — 作业意图解析与落实。

Phase 2 burn-down 阶段 4 (PR):从 workline/services/ 物理迁入。
workline/services/ 保留 re-export shim 兼容 v1 API。
"""

from src.app.runtime.orchestration.services.intent.operation_service import (
    WorklineOperationService,
    workline_operation_service,
)

__all__ = ["WorklineOperationService", "workline_operation_service"]
