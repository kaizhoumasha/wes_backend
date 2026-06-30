"""Adapter shim — InboxBatchProcessor 实际实现已迁入 runtime/orchestration/services/inbox/。

Phase 2 burn-down 阶段 4 (PR):workline/services/ 保留此 shim 供 v1 API 旧 import 路径兼容。
阶段 6 WorkLine 整体清空时此 shim 删除。

直接按文件路径加载目标模块(spec_from_file_location),绕过
runtime.orchestration.services.inbox 包 __init__,打破与 workline.services.__init__
加载链的循环引用(runtime.orchestration.services.inbox.inbox_batch_processor
反向依赖 workline.domain,workline.domain 反向依赖 workline.services.__init__)。
"""

from __future__ import annotations

import importlib.util
from typing import Any

_TARGET_FILE = "src/app/runtime/orchestration/services/inbox/inbox_batch_processor.py"


def _load_target_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "src.app.runtime.orchestration.services.inbox.inbox_batch_processor_shim",
        _TARGET_FILE,
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load spec for {_TARGET_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_target_module = _load_target_module()

InboxBatchProcessor = _target_module.InboxBatchProcessor
process_inbox_payload = _target_module.process_inbox_payload
build_workline_runtime_session_updated_event_payload = (
    _target_module.build_workline_runtime_session_updated_event_payload
)

__all__ = [
    "InboxBatchProcessor",
    "build_workline_runtime_session_updated_event_payload",
    "process_inbox_payload",
]
