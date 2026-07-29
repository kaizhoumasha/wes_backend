"""16 项 WMS EFFECT 共用的事务内 preparation Port。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel

    from src.app.runtime.system_capabilities.wms.effect_runtime import WmsEffectDispatchAccepted
    from src.app.wms_integration.operation_contract import WmsOperationDefinition


class WmsEffectPreparationPort(Protocol):
    """把 typed EFFECT request 与冻结执行上下文写入现有 Intent/Outbox 双账本。"""

    async def prepare(
        self,
        operation: WmsOperationDefinition,
        request: BaseModel,
        *,
        execution: Any,
    ) -> WmsEffectDispatchAccepted: ...


__all__ = ["WmsEffectPreparationPort"]
