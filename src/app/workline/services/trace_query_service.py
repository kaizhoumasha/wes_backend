"""Adapter shim — TraceQueryService 实际实现已迁入
runtime/orchestration/services/trace/。

Phase 2 burn-down 阶段 4 (PR):workline/services/ 保留此 shim 供 v1 API 旧 import 路径兼容。
阶段 6 WorkLine 整体清空时此 shim 删除。
"""

from src.app.runtime.orchestration.services.trace.trace_query_service import (
    TraceQueryResult,
    TraceQueryService,
    trace_query_service,
)

__all__ = ["TraceQueryResult", "TraceQueryService", "trace_query_service"]
