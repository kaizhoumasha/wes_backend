"""Adapter shim — timeline sequence helpers 实际实现已迁入
runtime/orchestration/services/trace/。

Phase 2 burn-down 阶段 4 (PR):workline/services/ 保留此 shim 供 v1 API 旧 import 路径兼容。
阶段 6 WorkLine 整体清空时此 shim 删除。
"""

from src.app.runtime.orchestration.services.trace.timeline_sequence_service import (
    add_timeline_with_sequence,
    allocate_timeline_seq_no,
)

__all__ = ["add_timeline_with_sequence", "allocate_timeline_seq_no"]
