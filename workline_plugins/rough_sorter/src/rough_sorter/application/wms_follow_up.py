"""粗分 WMS WAIT 响应的可靠后继请求规划。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from src.app.execution.services.wms_confirmation_service import WmsConfirmationFollowUp
from src.app.wms_adapter.inbound_wire import parse_outbound_request
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

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
        request_payload = parse_outbound_request(confirmation.request_payload).model_dump(
            mode="json",
            exclude_none=True,
        )
        request_payload["operation_id"] = operation_id
        request_payload["timestamp"] = int(timezone.to_utc(received_at).timestamp() * 1000)
        return WmsConfirmationFollowUp(
            operation=confirmation.operation,
            operation_id=operation_id,
            request_payload=request_payload,
            next_attempt_at=received_at + timedelta(milliseconds=retry_after_ms),
        )


__all__ = ["RoughSorterWmsFollowUpPlanner"]
