from collections.abc import Awaitable, Callable
from typing import Any

from src.core.logger import logger

ExternalHttpSender = Callable[[str, dict[str, Any]], Awaitable[bool]]
ALLOWED_INTERNAL_SIGNALS = frozenset({"core", "handling", "sys", "workline"})


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def dispatch_external_http(outbox: Any, endpoint_registry: Any, http_sender: ExternalHttpSender) -> bool:
    try:
        endpoint = endpoint_registry.resolve(str(getattr(outbox, "target_code", "") or ""))
    except ValueError as exc:
        logger.warning(str(exc))
        return False
    payload = _payload_dict(getattr(outbox, "payload_json", None))
    return await http_sender(endpoint.url, payload)


async def dispatch_internal_signal(outbox: Any) -> bool:
    target_code = getattr(outbox, "target_code", None)
    if not isinstance(target_code, str) or target_code not in ALLOWED_INTERNAL_SIGNALS:
        logger.error(f"SystemOutbox 内部信号派发失败: 未知的目标服务 {target_code}")
        return False

    try:
        from src.celery_app.app import celery_app

        celery_app.send_task(
            f"src.celery_app.tasks.{target_code}.process_signal",
            kwargs={"payload": _payload_dict(getattr(outbox, "payload_json", None))},
        )
        return True
    except Exception as exc:
        logger.error(f"SystemOutbox 内部信号派发失败: {exc}")
        return False
