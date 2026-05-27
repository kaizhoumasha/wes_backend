"""SystemOutbox 派发引擎。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any, TypedDict

from src.app.sys.models import SystemOutboxDispatchType
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.sys.services.endpoint_registry import EndpointRegistry, endpoint_registry
from src.core.logger import logger
from src.utils.value_normalization import enum_value

ExternalHttpSender = Callable[[str, dict[str, Any]], Awaitable[bool]]
DomainDispatcher = Callable[[Any, int], Awaitable["DispatchResult"]]
WORKLINE_OPERATION_DOMAIN = "WORKLINE"
ALLOWED_INTERNAL_SIGNALS = frozenset({"core", "handling", "sys", "workline"})


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
        device_command_dispatcher: Callable[[Any, Any], Awaitable[bool]] | None = None,
    ) -> None:
        self.outbox_repository = outbox_repository
        self.external_http_sender = external_http_sender or _send_external_http
        self.endpoint_registry = endpoint_registry
        self.workline_domain_dispatcher = workline_domain_dispatcher or _dispatch_workline_domain
        self.device_command_dispatcher = device_command_dispatcher or _dispatch_device_command

    async def dispatch(self, db: Any, limit: int = 50) -> DispatchResult:
        result: DispatchResult = {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}

        if limit <= 0:
            return result

        # 为了跨域公平调度，先给 Workline 分配最多一半的额度，避免单一域积压饿死其他域
        workline_limit = max(1, limit // 2)
        workline_result = await self.workline_domain_dispatcher(db, workline_limit)
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

        # 如果还有剩余额度，且 Workline 之前已经跑满了分配给它的额度，
        # 则再次派发 Workline 避免吞吐量浪费。
        final_remaining_limit = max(limit - result["dispatched"], 0)
        if final_remaining_limit > 0 and workline_result["dispatched"] >= workline_limit:
            extra_workline_result = await self.workline_domain_dispatcher(db, final_remaining_limit)
            _merge_dispatch_result(result, extra_workline_result)

        await _commit_if_supported(db)
        return result

    async def dispatch_single(self, db: Any, outbox: Any) -> bool:
        from src.app.sys.services.outbox_delivery import dispatch_external_http, dispatch_internal_signal

        dispatch_type = enum_value(getattr(outbox, "dispatch_type", None))
        if dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP.value:
            return await dispatch_external_http(outbox, self.endpoint_registry, self.external_http_sender)
        if dispatch_type == SystemOutboxDispatchType.INTERNAL_SIGNAL.value:
            return await dispatch_internal_signal(outbox)
        if dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND.value:
            return await self.device_command_dispatcher(db, outbox)
        logger.warning(f"未知的 SystemOutbox 派发类型: {dispatch_type}")
        return False


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
    from src.app.workline.services.outbox_dispatch_service import outbox_dispatch_service

    return await outbox_dispatch_service.dispatch(db, limit=limit)


async def _dispatch_device_command(db: Any, outbox: Any) -> bool:
    from src.app.workline.services.device_command_gateway import device_command_gateway

    return await device_command_gateway.dispatch(db, outbox)


async def _commit_if_supported(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        result = commit()
        if isawaitable(result):
            await result


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _merge_dispatch_result(target: DispatchResult, source: DispatchResult) -> None:
    for key in ("dispatched", "success", "failed", "skipped"):
        target[key] += int(source.get(key, 0))


system_outbox_engine = SystemOutboxEngine()
system_outbox_dispatcher = system_outbox_engine

__all__ = ["DispatchResult", "SystemOutboxEngine", "system_outbox_dispatcher", "system_outbox_engine"]
