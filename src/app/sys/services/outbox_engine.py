"""SystemOutbox 派发引擎。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from src.app.sys.models import SystemOutboxDispatchType
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.sys.services.endpoint_registry import EndpointRegistry, endpoint_registry
from src.core.logger import logger

ExternalHttpSender = Callable[[str, dict[str, Any]], Awaitable[bool]]
DomainDispatcher = Callable[[Any, int], Awaitable["DispatchResult"]]
WORKLINE_OPERATION_DOMAIN = "WORKLINE"


class DispatchResult(TypedDict):
    dispatched: int
    success: int
    failed: int
    skipped: int


class SystemOutboxEngine:
    """统一派发系统级 outbox。

    派发流水线：

        NEW -> DISPATCHING --send ok--> SENT
          |        |
          |        +--send fail--> NEW(backoff) / FAILED
          +--blocked by runtime/safety/device--> BLOCKED_RESOURCE

    Engine 保证 at-least-once 派发；exactly-once 由 dispatch_key/request_id
    在下游硬件系统和 callback 幂等共同完成。
    """

    MAX_RETRIES = 3

    def __init__(
        self,
        *,
        outbox_repository: SystemOutboxRepository = system_outbox_repository,
        external_http_sender: ExternalHttpSender | None = None,
        endpoint_registry: EndpointRegistry = endpoint_registry,
        workline_domain_dispatcher: DomainDispatcher | None = None,
    ) -> None:
        self.outbox_repository = outbox_repository
        self.external_http_sender = external_http_sender or _send_external_http
        self.endpoint_registry = endpoint_registry
        self.workline_domain_dispatcher = workline_domain_dispatcher or _dispatch_workline_domain

    async def dispatch(self, db: Any, limit: int = 50) -> DispatchResult:
        result: DispatchResult = {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
        workline_result = await self.workline_domain_dispatcher(db, limit)
        _merge_dispatch_result(result, workline_result)

        remaining_limit = max(limit - result["dispatched"], 0)
        if remaining_limit <= 0:
            await _commit_if_supported(db)
            return result

        messages = await self.outbox_repository.get_pending_messages(
            db,
            limit=remaining_limit,
            exclude_operation_domains=(WORKLINE_OPERATION_DOMAIN,),
        )

        for outbox in messages:
            outbox_id = getattr(outbox, "id", None)
            if not isinstance(outbox_id, int):
                result["skipped"] += 1
                continue

            claimed = await self.outbox_repository.mark_as_dispatching(db, outbox_id)
            if claimed is None:
                await _commit_if_supported(db)
                result["skipped"] += 1
                continue
            await _commit_if_supported(db)

            success = await self.dispatch_single(db, claimed)
            if success:
                sent = await self.outbox_repository.mark_as_sent(db, outbox_id)
                await _commit_if_supported(db)
                if sent is None:
                    result["skipped"] += 1
                else:
                    result["success"] += 1
            else:
                _ = await self.outbox_repository.mark_as_failed(
                    db,
                    outbox_id,
                    "Dispatch failed",
                    self.MAX_RETRIES,
                )
                await _commit_if_supported(db)
                result["failed"] += 1
            result["dispatched"] += 1

        await _commit_if_supported(db)
        return result

    async def dispatch_single(self, db: Any, outbox: Any) -> bool:
        dispatch_type = _enum_value(getattr(outbox, "dispatch_type", None))
        if dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP.value:
            return await self.dispatch_external_http(outbox)
        if dispatch_type == SystemOutboxDispatchType.INTERNAL_SIGNAL.value:
            return await self.dispatch_internal_signal(outbox)
        if dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND.value:
            return await self.dispatch_device_command(db, outbox)
        logger.warning(f"未知的 SystemOutbox 派发类型: {dispatch_type}")
        return False

    async def dispatch_external_http(self, outbox: Any) -> bool:
        try:
            endpoint = self.endpoint_registry.resolve(str(getattr(outbox, "target_code", "") or ""))
        except ValueError as exc:
            logger.warning(str(exc))
            return False
        payload = _payload_dict(getattr(outbox, "payload_json", None))
        return await self.external_http_sender(endpoint.url, payload)

    async def dispatch_internal_signal(self, outbox: Any) -> bool:
        try:
            from src.celery_app.app import celery_app

            celery_app.send_task(
                f"src.celery_app.tasks.{outbox.target_code}.process_signal",
                kwargs={"payload": _payload_dict(getattr(outbox, "payload_json", None))},
            )
            return True
        except Exception as exc:
            logger.error(f"SystemOutbox 内部信号派发失败: {exc}")
            return False

    async def dispatch_device_command(self, db: Any, outbox: Any) -> bool:
        # Workline device command governance is still the source of truth for device ACK semantics.
        from src.celery_app.tasks.workline import OutboxDispatcher

        return await OutboxDispatcher._dispatch_device_command(db, outbox)


async def _send_external_http(url: str, payload_json: dict[str, Any]) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload_json)
            return 200 <= response.status_code < 300
    except Exception as exc:
        logger.error(f"SystemOutbox 外部 HTTP 派发失败: {exc}")
        return False


async def _dispatch_workline_domain(db: Any, limit: int) -> DispatchResult:
    from src.celery_app.tasks.workline import OutboxDispatcher

    return await OutboxDispatcher._dispatch(db, limit=limit)


async def _commit_if_supported(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        await commit()


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _merge_dispatch_result(target: DispatchResult, source: DispatchResult) -> None:
    for key in ("dispatched", "success", "failed", "skipped"):
        target[key] += int(source.get(key, 0))


system_outbox_engine = SystemOutboxEngine()
system_outbox_dispatcher = system_outbox_engine

__all__ = ["DispatchResult", "SystemOutboxEngine", "system_outbox_dispatcher", "system_outbox_engine"]
