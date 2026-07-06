"""Callback -> RuntimeInbox 最小入站包络写入器。"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
    RuntimeInboxAcceptResult,
    RuntimeInboxService,
    runtime_inbox_service,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _resolve_first_str(payload: dict[str, Any], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        value = payload.get(alias)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_source_event_id(payload: dict[str, Any], request_id: str | None) -> str | None:
    return _resolve_first_str(payload, ("event_id", "source_event_id", "request_id")) or request_id


class CallbackRuntimeInboxWriter:
    """将 callback/result/event 三类回调最小包络落入 RuntimeInbox。"""

    def __init__(self, service: RuntimeInboxService = runtime_inbox_service) -> None:
        self._service = service

    async def write_result_callback(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
        request_id: str | None,
        canonical_result_type: str,
        correlation_id: str | None = None,
    ) -> RuntimeInboxAcceptResult:
        return await self._service.accept_received(
            db,
            provider_code="ECS",
            event_type=canonical_result_type,
            source_event_id=_resolve_source_event_id(payload, request_id),
            payload_hash=_canonical_payload_hash(payload),
            correlation_id=correlation_id or _resolve_first_str(payload, ("command_code", "request_id")),
        )

    async def write_event_callback(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
        request_id: str | None,
        canonical_event_type: str,
    ) -> RuntimeInboxAcceptResult:
        device_code = _resolve_first_str(payload, ("device_code",))
        correlation_id = None
        if device_code and canonical_event_type:
            correlation_id = f"event:{device_code}:{canonical_event_type}"
        return await self._service.accept_received(
            db,
            provider_code="ECS",
            event_type=canonical_event_type,
            source_event_id=_resolve_source_event_id(payload, request_id),
            payload_hash=_canonical_payload_hash(payload),
            correlation_id=correlation_id,
        )

    async def write_external_callback(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> RuntimeInboxAcceptResult:
        provider_code = _resolve_first_str(payload, ("source_system",))
        callback_type = _resolve_first_str(payload, ("callback_type",)) or "UNKNOWN"
        normalized_provider = provider_code or callback_type.split("_", 1)[0] or "UNKNOWN"
        correlation_id = _resolve_first_str(
            payload,
            ("dispatch_key", "command_code", "exchange_request_code", "request_id", "trace_id"),
        )
        return await self._service.accept_received(
            db,
            provider_code=normalized_provider,
            event_type=callback_type,
            source_event_id=_resolve_source_event_id(payload, request_id),
            payload_hash=_canonical_payload_hash(payload),
            correlation_id=correlation_id,
        )


callback_runtime_inbox_writer = CallbackRuntimeInboxWriter()


__all__ = ["CallbackRuntimeInboxWriter", "callback_runtime_inbox_writer"]
