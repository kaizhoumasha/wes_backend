"""RuntimeInbox replay source 在消费入口的持久化真实性对抗测试。"""

from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)


def _canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


@pytest.mark.parametrize("tamper", ["stale_payload_hash", "payload", "evidence", "root"])
@pytest.mark.asyncio
async def test_process_claimed_rejects_tampered_replay_before_context_or_effect(
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    root_payload = {"event_type": "SCAN_COMPLETED", "data": {"HHPN": "ROOT"}}
    root = SimpleNamespace(
        id=7,
        kind="DEVICE_EVENT",
        provider_code="PLC",
        event_type="SCAN_COMPLETED",
        source_event_id="root-event",
        payload_hash=_canonical_payload_hash(root_payload),
        payload_json=root_payload,
        workline_id=20,
        device_id=21,
        command_id=None,
        workline_session_id=10,
        execution_session_id=11,
        correlation_id="corr-root",
        trace_id="trace-root",
        event_id="event-root",
        causation_id=None,
        max_retries=5,
    )
    envelope = {
        "request_id": "consume-replay",
        "actor": "42",
        "reason": "retry",
        "immediate_source_inbox_id": 7,
        "root_source_inbox_id": 7,
        "original_kind": root.kind,
        "original_payload": dict(root_payload),
        "original_provider_code": root.provider_code,
        "original_event_type": root.event_type,
        "original_source_event_id": root.source_event_id,
        "original_payload_hash": root.payload_hash,
        "original_workline_id": root.workline_id,
        "original_device_id": root.device_id,
        "original_command_id": root.command_id,
        "original_workline_session_id": root.workline_session_id,
        "original_execution_session_id": root.execution_session_id,
        "original_correlation_id": root.correlation_id,
        "original_trace_id": root.trace_id,
        "original_event_id": root.event_id,
        "original_causation_id": root.causation_id,
    }
    source = SimpleNamespace(
        id=91,
        kind="REPLAY_REQUEST",
        provider_code="RUNTIME",
        event_type="REPLAY_REQUEST",
        source_event_id="replay:7:consume-replay",
        payload_json=envelope,
        payload_hash=_canonical_payload_hash(envelope),
        workline_id=root.workline_id,
        device_id=root.device_id,
        command_id=root.command_id,
        workline_session_id=root.workline_session_id,
        execution_session_id=root.execution_session_id,
        correlation_id=root.correlation_id,
        trace_id=root.trace_id,
        event_id="replay-event",
        causation_id=root.event_id,
        attempt_count=1,
        max_retries=5,
    )
    if tamper == "stale_payload_hash":
        source.payload_hash = "stale-hash"
    elif tamper == "payload":
        envelope["original_payload"] = {"event_type": "SCAN_COMPLETED", "data": {"HHPN": "TAMPERED"}}
        source.payload_hash = _canonical_payload_hash(envelope)
    elif tamper == "evidence":
        envelope["original_workline_session_id"] = 999
        source.payload_hash = _canonical_payload_hash(envelope)
    else:
        envelope["root_source_inbox_id"] = 999
        source.payload_hash = _canonical_payload_hash(envelope)

    class _Repository:
        async def get_by_id(self, *_args: Any) -> Any:
            return source

        async def get_by_id_for_update(self, _db: Any, inbox_id: int, **_kwargs: Any) -> Any:
            return root if inbox_id == root.id else None

    class _InboxService:
        error_message: str | None = None

        async def mark_failed(self, *_args: Any, **kwargs: Any) -> bool:
            self.error_message = kwargs["error_message"]
            return True

    class _Db:
        async def rollback(self) -> None:
            pass

        async def commit(self) -> None:
            pass

    context_calls: list[str] = []
    logged_exceptions: list[str] = []
    logged_warnings: list[str] = []

    async def fail_if_context_loaded(*_args: Any, **_kwargs: Any) -> tuple[object, ...]:
        context_calls.append("loaded")
        raise AssertionError("tampered replay must fail before context loading")

    async def noop_diagnostic(*_args: Any, **_kwargs: Any) -> None:
        return None

    logger_stub = SimpleNamespace(
        exception=lambda message: logged_exceptions.append(str(message)),
        warning=lambda message: logged_warnings.append(str(message)),
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        fail_if_context_loaded,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._record_diagnostic",
        noop_diagnostic,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge.logger",
        logger_stub,
    )
    inbox_service = _InboxService()
    result = await RuntimeInboxProcessorBridge(
        inbox_repository=_Repository(),  # type: ignore[arg-type]
        inbox_service=inbox_service,  # type: ignore[arg-type]
    ).process_claimed(_Db(), claim={"id": 91, "processor_token": "token-91"})

    assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0, "resource_wait": 0}
    assert context_calls == []
    assert inbox_service.error_message is not None
    assert "REPLAY_SOURCE_INTEGRITY_VIOLATION" in inbox_service.error_message
    assert logged_exceptions == []
    assert logged_warnings
    assert all("TAMPERED" not in message for message in logged_warnings)
    assert root.payload_json == root_payload
