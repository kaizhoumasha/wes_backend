"""Transport callback 测试辅助。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.app.transport.service import TransportService

from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION, validate_callback_envelope
from src.core.uuid7 import new_uuid7


async def record_valid_callback(
    service: TransportService,
    *,
    operation_id: str,
    transport_task_id: str,
    operation: str,
    timestamp: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload = {**payload, "transport_task_id": transport_task_id}
    message = {
        "operation_id": operation_id,
        "operation": operation,
        "timestamp": timestamp,
        "data": payload,
    }
    validated = (
        validate_callback_envelope({**message, "operation_id": new_uuid7()})
        if operation in {POSITION_OPERATION, RESULT_OPERATION}
        else message
    )
    validated = {**validated, "operation_id": operation_id}
    return await service.record_callback(
        operation_id=operation_id,
        operation=operation,
        message=validated,
        payload=validated["data"],
        rejection_reason_code=None,
    )


__all__ = ["record_valid_callback"]
