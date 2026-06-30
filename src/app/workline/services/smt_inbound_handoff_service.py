"""Adapter shim — SmtInboundHandoffService 实际实现已迁入
runtime/orchestration/services/intent/。

Phase 2 burn-down 阶段 4 C4a (PR):workline/services/ 保留此 shim 供 v1 API 旧 import 路径兼容。
阶段 6 WorkLine 整体清空时此 shim 删除。
"""

from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
    SmtInboundHandoffService,
    smt_inbound_handoff_service,
)

__all__ = ["SmtInboundHandoffService", "smt_inbound_handoff_service"]
