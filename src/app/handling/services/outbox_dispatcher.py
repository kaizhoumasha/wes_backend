"""SystemOutbox 派发器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from src.app.handling.models import SystemOutboxDispatchType
from src.app.handling.repositories import SystemOutboxRepository, system_outbox_repository
from src.core.logger import logger

ExternalHttpSender = Callable[[str, dict[str, Any]], Awaitable[bool]]


class DispatchResult(TypedDict):
    dispatched: int
    success: int
    failed: int
    skipped: int


class SystemOutboxDispatcher:
    """派发系统级 handling outbox。"""

    MAX_RETRIES = 3

    def __init__(
        self,
        *,
        outbox_repository: SystemOutboxRepository = system_outbox_repository,
        external_http_sender: ExternalHttpSender | None = None,
    ) -> None:
        self.outbox_repository = outbox_repository
        self.external_http_sender = external_http_sender or _send_external_http

    async def dispatch(self, db: Any, limit: int = 50) -> DispatchResult:
        result: DispatchResult = {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
        messages = await self.outbox_repository.get_pending_messages(db, limit=limit)

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
        _ = db
        dispatch_type = _enum_value(getattr(outbox, "dispatch_type", None))
        if dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP.value:
            return await self.dispatch_external_http(outbox)
        if dispatch_type == SystemOutboxDispatchType.INTERNAL_SIGNAL.value:
            return await self.dispatch_internal_signal(outbox)
        logger.warning(f"未知的 SystemOutbox 派发类型: {dispatch_type}")
        return False

    async def dispatch_external_http(self, outbox: Any) -> bool:
        target_code = str(getattr(outbox, "target_code", "") or "")
        if not target_code:
            logger.warning(f"SystemOutbox 缺少 target_code: outbox_id={getattr(outbox, 'id', None)}")
            return False
        payload = _payload_dict(getattr(outbox, "payload_json", None))
        return await self.external_http_sender(target_code, payload)

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


async def _send_external_http(target_code: str, payload_json: dict[str, Any]) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(target_code, json=payload_json)
            return 200 <= response.status_code < 300
    except Exception as exc:
        logger.error(f"SystemOutbox 外部 HTTP 派发失败: {exc}")
        return False


async def _commit_if_supported(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        await commit()


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


system_outbox_dispatcher = SystemOutboxDispatcher()


__all__ = ["DispatchResult", "SystemOutboxDispatcher", "system_outbox_dispatcher"]
