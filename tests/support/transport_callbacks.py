"""Transport callback 测试辅助。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.app.transport.service import TransportService


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
    return await service.record_callback(
        operation_id=operation_id,
        operation=operation,
        message=message,
        payload=message["data"],
        rejection_reason_code=None,
    )


__all__ = ["record_valid_callback"]
