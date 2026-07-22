from typing import Any

from src.app.sys.canonical_dispatch import ExternalHttpDispatchRequest
from src.app.sys.external_http_transport import (
    ExternalHttpSender,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.core.logger import logger
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway

ALLOWED_INTERNAL_SIGNALS = frozenset({"core", "handling", "sys", "workline"})


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def dispatch_external_http(
    outbox: Any,
    endpoint_registry: Any,
    http_sender: ExternalHttpSender,
) -> ExternalHttpTransportResult:
    try:
        endpoint = endpoint_registry.resolve(str(getattr(outbox, "target_code", "") or ""))
        request = ExternalHttpDispatchRequest.from_persisted(
            endpoint=endpoint,
            canonical_payload_bytes=getattr(outbox, "canonical_payload_bytes", None),
            payload_hash=getattr(outbox, "payload_hash", None),
        )
    except ValueError as exc:
        logger.warning(str(exc))
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.PREPARING,
            safe_to_retry=False,
            error_code="DISPATCH_PREPARATION_FAILED",
            error_message=str(exc),
        )
    try:
        result = await http_sender(request)
    except Exception as exc:
        logger.error(f"EXTERNAL_HTTP sender 未返回 typed result: {exc}")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.SENDING,
            error_code="SENDER_RAISED",
            error_message=str(exc),
        )
    if not isinstance(result, ExternalHttpTransportResult):
        logger.error("EXTERNAL_HTTP sender 违反 typed result 合同")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.SENDING,
            error_code="SENDER_CONTRACT_VIOLATION",
            error_message=f"unexpected sender result type: {type(result).__name__}",
        )
    return result


async def dispatch_internal_signal(outbox: Any, queue_gateway: TaskQueueGateway = task_queue_gateway) -> bool:
    target_code = getattr(outbox, "target_code", None)
    if not isinstance(target_code, str) or target_code not in ALLOWED_INTERNAL_SIGNALS:
        logger.error(f"SystemOutbox 内部信号派发失败: 未知的目标服务 {target_code}")
        return False

    try:
        queue_gateway.enqueue_internal_signal(target_code, _payload_dict(getattr(outbox, "payload_json", None)))
        return True
    except Exception as exc:
        logger.error(f"SystemOutbox 内部信号派发失败: {exc}")
        return False
