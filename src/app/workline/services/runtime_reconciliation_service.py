"""Adapter shim.

RuntimeReconciliationService 实际实现已迁入 runtime/orchestration/services/reconciliation/。

Phase 2 burn-down 阶段 4 (PR):facade 内部委托改本地 impl。workline/services/ 保留此 shim
供 v1 API 旧 import 路径兼容。阶段 6 WorkLine 整体清空时此 shim 删除。
"""

from __future__ import annotations

import importlib
import sys

_target_module = importlib.import_module(
    "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl"
)

# 旧路径必须与实现路径共享同一个 module object，否则 tests/legacy patch 旧路径时，
# 实现模块里的 globals 仍不会被替换。
sys.modules[__name__] = _target_module
