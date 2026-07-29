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


def _install_test_runner(bridge: RuntimeInboxProcessorBridge, runner: object) -> None:
    bridge._generated_attempt_runner = runner  # type: ignore[assignment]

    async def _build_request(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(snapshot=SimpleNamespace())

    async def _pin_runtime(*_args: object, **_kwargs: object) -> None:
        return None

    bridge._build_generated_dispatch_request = _build_request  # type: ignore[method-assign]
    bridge._pin_attempt_runtime_to_dispatch_snapshot = _pin_runtime  # type: ignore[method-assign]


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
        writeback_service=WriteBack(),  # type: ignore[arg-type]
    )
    _install_test_runner(bridge, Runner())
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
        writeback_service=WriteBack(),  # type: ignore[arg-type]
    )
    _install_test_runner(bridge, Runner())
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
async def test_pre_attempt_blocked_skips_runner_and_commits_fail_closed_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge as module

    class Db:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}

        async def flush(self) -> None:
            pass

        async def commit(self) -> None:
            pass

    class Runner:
        async def run(self, _context: object) -> AttemptWriteSet:
            raise AssertionError("BLOCKED pre-attempt must skip Stage 2 plugin runner")

    captured: list[AttemptWriteSet] = []

    class WriteBack:
        async def commit_plugin_attempt(self, db: Db, **kwargs: object) -> WriteDisposition:
            captured.append(kwargs["write_set"])  # type: ignore[arg-type]
            await db.commit()
            return WriteDisposition.COMMITTED

    async def blocked(*_args: object, **_kwargs: object) -> object:
        from src.app.runtime.workline_plugins.pre_attempt import PreAttemptResolution

        return PreAttemptResolution.blocked("WMS_Q19_REPLAY_REQUEST_MISMATCH")

    monkeypatch.setattr(module, "resolve_plugin_pre_attempt_facts", blocked)
    bridge = RuntimeInboxProcessorBridge(writeback_service=WriteBack())  # type: ignore[arg-type]
    _install_test_runner(bridge, Runner())
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
        Db(),  # type: ignore[arg-type]
        inbox=inbox,
        session=SimpleNamespace(
            id=10,
            version=7,
            plugin_state_version=3,
            plugin_state_json={"phase": "READY"},
            plugin_binding_id=17,
            current_material_unit_id=None,
            status="RUNNING",
        ),
        workline=SimpleNamespace(id=20),
        resolved_event_type="SCAN_COMPLETED",
        processor_token="lease-blocked",
        attempt_runtime=bridge.create_attempt_runtime("lease-blocked"),
    )

    assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0, "resource_wait": 0}
    assert len(captured) == 1
    assert captured[0].outcome_code == "HOLD"
    assert captured[0].hold_reason == "WMS_Q19_REPLAY_REQUEST_MISMATCH"
    assert captured[0].intents == ()
    assert captured[0].preserve_plugin_state is True


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
