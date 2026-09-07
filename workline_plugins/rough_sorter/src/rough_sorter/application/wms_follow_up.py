"""粗分 WMS WAIT 响应的可靠后继请求规划。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from src.app.execution.services.wms_confirmation_service import WmsConfirmationFollowUp
from src.app.wms_adapter.inbound_material.typed import decode_request
from src.core.uuid7 import new_uuid7

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from src.app.execution.models import WmsConfirmation


class RoughSorterWmsFollowUpPlanner:
    """为已验证的粗分 WAIT 生成新身份，并保留原业务请求。"""

    def __init__(self, operation_id_factory: Callable[[], str] = new_uuid7) -> None:
        self._operation_id_factory = operation_id_factory

    async def plan(
        self,
        _db: object,
        confirmation: WmsConfirmation,
        *,
        response_result: str,
        retry_after_ms: int,
        received_at: datetime,
    ) -> WmsConfirmationFollowUp | None:
        if response_result != "WAIT":
            return None
        if not isinstance(retry_after_ms, int) or isinstance(retry_after_ms, bool) or retry_after_ms <= 0:
            return None
        operation_id = self._operation_id_factory()
        intent = replace(
            decode_request(confirmation.request_payload, fact_id="wms-follow-up"), operation_id=operation_id
        )
        return WmsConfirmationFollowUp(
            intent=intent,
            next_attempt_at=received_at + timedelta(milliseconds=retry_after_ms),
        )


__all__ = ["RoughSorterWmsFollowUpPlanner"]
