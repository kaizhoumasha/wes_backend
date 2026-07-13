"""Callback -> RuntimeInbox 最小入站包络写入器。"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.services.runtime_inbox import (
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


def _resolve_nested_str(payload: dict[str, Any], path: tuple[str, ...]) -> str | None:
    value: Any = payload
    for segment in path:
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_payload_source_event_id(payload: dict[str, Any], *, fallback_prefix: str) -> str:
    return _resolve_first_str(payload, ("event_id", "source_event_id")) or (
        f"{fallback_prefix}:{_canonical_payload_hash(payload)}"
    )


def _resolve_external_source_event_id(payload: dict[str, Any], request_id: str | None) -> str | None:
    _ = request_id
    callback_type = _resolve_first_str(payload, ("callback_type",)) or "UNKNOWN"
    return (
        _resolve_first_str(payload, ("event_id", "source_event_id"))
        or _resolve_nested_str(payload, ("data", "source_event_id"))
        or _resolve_first_str(payload, ("request_id",))
        or f"callback-external:{callback_type}:{_canonical_payload_hash(payload)}"
    )


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
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
    ) -> RuntimeInboxAcceptResult:
        _ = request_id
        return await self._service.accept_received(
            db,
            provider_code="ECS",
            event_type=canonical_result_type,
            source_event_id=_resolve_payload_source_event_id(payload, fallback_prefix="callback-result"),
            payload_hash=_canonical_payload_hash(payload),
            kind="COMMAND_RESULT",
            payload_json=dict(payload),
            payload_schema_version=1,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    async def write_event_callback(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
        request_id: str | None,
        canonical_event_type: str,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
    ) -> RuntimeInboxAcceptResult:
        _ = request_id
        return await self._service.accept_received(
            db,
            provider_code="ECS",
            event_type=canonical_event_type,
            source_event_id=_resolve_payload_source_event_id(payload, fallback_prefix="callback-event"),
            payload_hash=_canonical_payload_hash(payload),
            kind="DEVICE_EVENT",
            payload_json=dict(payload),
            payload_schema_version=1,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            correlation_id=None,
        )

    async def write_external_callback(
        self,
        db: AsyncSession,
        *,
        payload: dict[str, Any],
        request_id: str | None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
    ) -> RuntimeInboxAcceptResult:
        provider_code = _resolve_first_str(payload, ("source_system",))
        callback_type = _resolve_first_str(payload, ("callback_type",)) or "UNKNOWN"
        normalized_provider = provider_code or callback_type.split("_", 1)[0] or "UNKNOWN"
        return await self._service.accept_received(
            db,
            provider_code=normalized_provider,
            event_type=callback_type,
            source_event_id=_resolve_external_source_event_id(payload, request_id),
            payload_hash=_canonical_payload_hash(payload),
            kind="EXTERNAL_HTTP",
            payload_json=dict(payload),
            payload_schema_version=1,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            correlation_id=None,
        )


callback_runtime_inbox_writer = CallbackRuntimeInboxWriter()


__all__ = ["CallbackRuntimeInboxWriter", "callback_runtime_inbox_writer"]
