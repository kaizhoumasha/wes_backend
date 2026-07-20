"""平台插件 RuntimeInbox 写回后的 SSE 通知合同。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet, WriteDisposition
from src.app.sys.services.event_stream_service import (
    DEFERRED_SSE_EVENTS_KEY,
    WORKLINE_RUNTIME_CHANGED_EVENT,
    defer_sse_event,
    event_stream_service,
)


@pytest.mark.asyncio
async def test_platform_committed_writeback_defers_runtime_sse_event() -> None:
    class Db:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}

        async def flush(self) -> None:
            pass

        async def commit(self) -> None:
            pass

    class Runner:
        async def run(self, _context: object) -> AttemptWriteSet:
            return AttemptWriteSet(evidence=(), next_state={"step": 2}, intents=(), outcome_code="ROUTE_A")

    class WriteBack:
        async def commit_plugin_attempt(self, db: Db, **_kwargs: object) -> WriteDisposition:
            await db.commit()
            return WriteDisposition.COMMITTED

    db = Db()
    bridge = RuntimeInboxProcessorBridge(
        plugin_attempt_runner=Runner(),
        writeback_service=WriteBack(),  # type: ignore[arg-type]
    )
    inbox = SimpleNamespace(
        id=91,
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED"},
        trace_id="trace-test",
        event_id="evt-test",
        causation_id=None,
        workline_id=20,
        execution_session_id=10,
        device_id=None,
        command_id=None,
        attempt_count=0,
        event_type="SCAN_COMPLETED",
    )

    result = await bridge._process_platform_plugin_attempt(
        db,  # type: ignore[arg-type]
        inbox=inbox,
        session=SimpleNamespace(
            id=10,
            version=7,
            plugin_state_version=3,
            plugin_state_json={},
            plugin_binding_id=17,
            current_material_unit_id=None,
            status="RUNNING",
        ),
        workline=SimpleNamespace(id=20),
        resolved_event_type="SCAN_COMPLETED",
        processor_token="lease-1",
        attempt_runtime=bridge.create_attempt_runtime("lease-1"),
    )

    assert result["success"] == 1
    assert db.info[DEFERRED_SSE_EVENTS_KEY] == [
        (
            WORKLINE_RUNTIME_CHANGED_EVENT,
            {
                "domain": "workline_runtime",
                "entity": "session",
                "action": "updated",
                "keys": {"workline_id": 20, "session_id": 10},
            },
        )
    ]


@pytest.mark.asyncio
async def test_platform_terminal_failure_reports_failure_and_defers_runtime_sse_event() -> None:
    from src.celery_app.tasks.runtime_inbox import _processing_outcome

    class Db:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}

        async def flush(self) -> None:
            pass

        async def commit(self) -> None:
            pass

    class Runner:
        async def run(self, _context: object) -> AttemptWriteSet:
            return AttemptWriteSet(evidence=(), next_state={"step": 2}, intents=(), outcome_code="ROUTE_A")

    class WriteBack:
        async def commit_plugin_attempt(self, db: Db, **_kwargs: object) -> WriteDisposition:
            await db.commit()
            return WriteDisposition.TERMINAL_FAILURE

    db = Db()
    bridge = RuntimeInboxProcessorBridge(
        plugin_attempt_runner=Runner(),
        writeback_service=WriteBack(),  # type: ignore[arg-type]
    )
    inbox = SimpleNamespace(
        id=91,
        kind="DEVICE_EVENT",
        payload_json={"event_type": "CAPABILITY_EFFECT_RESULT"},
        trace_id="trace-test",
        event_id="evt-test",
        causation_id=None,
        workline_id=20,
        execution_session_id=10,
        device_id=None,
        command_id=None,
        attempt_count=0,
        event_type="CAPABILITY_EFFECT_RESULT",
    )

    result = await bridge._process_platform_plugin_attempt(
        db,  # type: ignore[arg-type]
        inbox=inbox,
        session=SimpleNamespace(
            id=10,
            version=7,
            plugin_state_version=3,
            plugin_state_json={},
            plugin_binding_id=17,
            current_material_unit_id=None,
            status="RUNNING",
        ),
        workline=SimpleNamespace(id=20),
        resolved_event_type="CAPABILITY_EFFECT_RESULT",
        processor_token="lease-1",
        attempt_runtime=bridge.create_attempt_runtime("lease-1"),
    )

    assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0, "resource_wait": 0}
    assert _processing_outcome(result) == "failed"
    assert db.info[DEFERRED_SSE_EVENTS_KEY][0][0] == WORKLINE_RUNTIME_CHANGED_EVENT


@pytest.mark.asyncio
async def test_process_claimed_discards_deferred_sse_after_business_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Db:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}
            self.transaction_open = True

        async def rollback(self) -> None:
            self.transaction_open = False

        async def commit(self) -> None:
            self.transaction_open = False

        def in_transaction(self) -> bool:
            return self.transaction_open

    inbox = SimpleNamespace(
        id=91,
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED"},
        trace_id="trace-test",
        event_id="evt-test",
        causation_id=None,
        workline_id=20,
        execution_session_id=10,
        device_id=None,
        command_id=None,
        attempt_count=0,
        event_type="SCAN_COMPLETED",
    )

    class InboxRepository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class InboxService:
        async def mark_failed(self, *_args: object, **_kwargs: object) -> bool:
            return True

    async def load_related(db: Db, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        defer_sse_event(db, WORKLINE_RUNTIME_CHANGED_EVENT, {"rolled_back": True})
        raise RuntimeError("effect write failed after defer")

    async def record_diagnostic(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._record_diagnostic",
        record_diagnostic,
    )
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(event_stream_service, "publish", publish)
    db = Db()

    result = await RuntimeInboxProcessorBridge(
        inbox_repository=InboxRepository(),  # type: ignore[arg-type]
        inbox_service=InboxService(),  # type: ignore[arg-type]
    ).process_claimed(db, claim={"id": 91, "processor_token": "lease-1"})  # type: ignore[arg-type]

    assert result["failed"] == 1
    publish.assert_not_awaited()
    assert DEFERRED_SSE_EVENTS_KEY not in db.info


@pytest.mark.asyncio
async def test_process_claimed_discards_deferred_sse_when_open_transaction_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Db:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}

        def in_transaction(self) -> bool:
            return True

    inbox = SimpleNamespace(
        id=91,
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED"},
        trace_id="trace-test",
        event_id="evt-test",
        causation_id=None,
        workline_id=20,
        execution_session_id=10,
        device_id=None,
        command_id=None,
        attempt_count=0,
        event_type="SCAN_COMPLETED",
    )

    class InboxRepository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    async def load_related(db: Db, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        defer_sse_event(db, WORKLINE_RUNTIME_CHANGED_EVENT, {"cancelled": True})
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(event_stream_service, "publish", publish)
    db = Db()

    with pytest.raises(asyncio.CancelledError):
        await RuntimeInboxProcessorBridge(
            inbox_repository=InboxRepository(),  # type: ignore[arg-type]
        ).process_claimed(db, claim={"id": 91, "processor_token": "lease-1"})  # type: ignore[arg-type]

    publish.assert_not_awaited()
    assert DEFERRED_SSE_EVENTS_KEY not in db.info
