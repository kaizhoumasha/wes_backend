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
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import (
    RuntimeInboxReplaySourceValidator,
)


def _canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def _legal_replay_pair() -> tuple[SimpleNamespace, SimpleNamespace]:
    root_payload = {
        "callback_type": "AGV_TASK_RESULT",
        "source_system": "AGV",
        "trace_id": "trace-root",
        "command_code": "AGV-001",
        "result": "SUCCESS",
        "data": {"session_id": 10},
    }
    root = SimpleNamespace(
        id=7,
        kind="EXTERNAL_HTTP",
        provider_code="AGV",
        event_type="AGV_TASK_RESULT",
        source_event_id="root-event",
        payload_hash=_canonical_payload_hash(root_payload),
        payload_json=root_payload,
        workline_id=20,
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
        workline_session_id=root.workline_session_id,
        execution_session_id=root.execution_session_id,
        correlation_id=root.correlation_id,
        trace_id=root.trace_id,
        event_id="replay-event",
        causation_id=root.event_id,
        attempt_count=1,
        max_retries=5,
    )
    return root, source


@pytest.mark.asyncio
async def test_replay_source_validator_keeps_locking_root_read_for_creation() -> None:
    root, source = _legal_replay_pair()
    locked_reads: list[int] = []
    nonlocking_reads: list[int] = []

    class _Repository:
        async def get_by_id_for_update(self, _db: Any, inbox_id: int, **_kwargs: Any) -> Any:
            locked_reads.append(inbox_id)
            return root

        async def get_by_id_refreshed(self, _db: Any, inbox_id: int) -> Any:
            nonlocking_reads.append(inbox_id)
            return root

    await RuntimeInboxReplaySourceValidator(_Repository()).validate_for_creation(  # type: ignore[arg-type]
        SimpleNamespace(),
        source=source,
    )

    assert locked_reads == [root.id]
    assert nonlocking_reads == []


@pytest.mark.parametrize("tamper", ["stale_payload_hash", "payload", "evidence", "root"])
@pytest.mark.asyncio
async def test_process_claimed_rejects_tampered_replay_before_context_or_effect(
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    root, source = _legal_replay_pair()
    root_payload = root.payload_json
    envelope = source.payload_json
    if tamper == "stale_payload_hash":
        source.payload_hash = "stale-hash"
    elif tamper == "payload":
        envelope["original_payload"] = {**root_payload, "command_code": "TAMPERED"}
        source.payload_hash = _canonical_payload_hash(envelope)
    elif tamper == "evidence":
        envelope["original_workline_session_id"] = 999
        source.payload_hash = _canonical_payload_hash(envelope)
    else:
        envelope["root_source_inbox_id"] = 999
        source.payload_hash = _canonical_payload_hash(envelope)

    nonlocking_reads: list[int] = []

    class _Repository:
        async def get_by_id(self, *_args: Any) -> Any:
            return source

        async def get_by_id_for_update(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("consumer replay validation must not lock the root row")

        async def get_by_id_refreshed(self, _db: Any, inbox_id: int) -> Any:
            nonlocking_reads.append(inbox_id)
            return root if inbox_id == root.id else None

    class _InboxService:
        mark_failed_kwargs: dict[str, Any] | None = None

        async def mark_failed(self, *_args: Any, **kwargs: Any) -> bool:
            self.mark_failed_kwargs = kwargs
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
    assert inbox_service.mark_failed_kwargs is not None
    assert inbox_service.mark_failed_kwargs["error_code"] == "REPLAY_SOURCE_INTEGRITY_VIOLATION"
    assert inbox_service.mark_failed_kwargs["error_message"].startswith("REPLAY_SOURCE_INTEGRITY_VIOLATION")
    assert inbox_service.mark_failed_kwargs["retryable"] is False
    assert logged_exceptions == []
    assert logged_warnings
    assert all("TAMPERED" not in message for message in logged_warnings)
    assert root.payload_json == root_payload
    expected_reads = [] if tamper == "stale_payload_hash" else [int(source.payload_json["root_source_inbox_id"])]
    assert nonlocking_reads == expected_reads
