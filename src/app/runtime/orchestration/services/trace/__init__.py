"""Trace 子目录 — Trace 视图构建/查询/时间线。

runtime migration 阶段 4 (PR):从 workline/services/ 物理迁入。
workline/services/ 保留 re-export shim 兼容 v1 API。
"""

from src.app.runtime.orchestration.services.trace.timeline_sequence_service import (
    add_timeline_with_sequence,
    allocate_timeline_seq_no,
)
from src.app.runtime.orchestration.services.trace.trace_query_service import (
    TraceQueryResult,
    TraceQueryService,
    trace_query_service,
)
from src.app.runtime.orchestration.services.trace.trace_resource_view_builder import (
    build_trace_resource_view,
)
from src.app.runtime.orchestration.services.trace.trace_response_builder import (
    build_failed_command_evidence,
    build_trace_response,
    build_trace_session_item,
    build_trace_timeline_item,
)

__all__ = [
    "TraceQueryResult",
    "TraceQueryService",
    "add_timeline_with_sequence",
    "allocate_timeline_seq_no",
    "build_failed_command_evidence",
    "build_trace_resource_view",
    "build_trace_response",
    "build_trace_session_item",
    "build_trace_timeline_item",
    "trace_query_service",
]
