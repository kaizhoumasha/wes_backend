"""WMS EFFECT 稳定观测属性的薄投影入口。"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from src.app.runtime.orchestration.observability import (
    RuntimeObservabilityEvent,
    RuntimeObservabilityRegistry,
    runtime_observability_registry,
)
from src.app.wms_integration.operation_registry import EFFECT_OPERATION_IDENTITIES

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


def emit_wms_effect_observation(
    signal_name: str,
    *,
    attributes: Mapping[str, object],
    operation_identity: str | None = None,
    dispatch_key: str | None = None,
    registry: RuntimeObservabilityRegistry = runtime_observability_registry,
) -> RuntimeObservabilityEvent | None:
    """投影低基数属性并 best-effort 发射；业务键只保留不可逆短摘要。"""

    projected: dict[str, object] = dict(attributes)
    if operation_identity in EFFECT_OPERATION_IDENTITIES:
        projected["operation_identity"] = operation_identity
    if isinstance(dispatch_key, str) and dispatch_key:
        projected["dispatch_key_hash"] = hashlib.sha256(dispatch_key.encode()).hexdigest()[:16]
    try:
        return registry.emit(signal_name, projected)
    except Exception as exc:  # 观测失败不得改变 submit、status、callback 或 recovery 事务。
        logger.warning("WMS EFFECT observability emission failed: %s", type(exc).__name__)
        return None


__all__ = ["emit_wms_effect_observation"]
