"""Tests for RuntimeInbox 三阶段 Processor 拆分 services (Task 5).

覆盖:
- RuntimeInboxValidationService: SCAN gate + ESTOP/TIMER 路由
- RuntimeInboxOrchestratorDelegate: pure delegate 透传
- RuntimeInboxWriteBackService: WRITE 锁回调
- RuntimeInboxProcessorBridge (composition): 单条 claim-and-process
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_result import (
    RuntimeIntentEffectResult,
    WriteBackDisposition,
)
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_context_loader as context_loader
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
    _load_related_entities,
    _project_replay_request,
    _snapshot_inbox_for_diagnostic,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_processor_service import (
    RuntimeInboxOrchestratorDelegate,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
    RuntimeInboxValidationService,
    ValidationOutcome,
    _entry_event_types_for_workline,
    _scan_completed_has_any_barcode_payload,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
    WriteBackState,
    _is_late_or_duplicate_command_result_for_session,
    _record_duplicate_entry_archive_timeline,
    _record_late_command_result_archive_timeline,
    _result_requires_outbox_dispatch,
    _session_write_snapshot,
)
from src.app.workline.constants import WORKLINE_INBOX_PROCESSING_STALE_SECONDS
from src.app.workline.trace_context import TraceContext

# ============================================================
# Helpers
# ============================================================


def _make_inbox(
    *,
    inbox_id: int = 1,
    kind: str = "DEVICE_EVENT",
    payload_json: dict[str, Any] | None = None,
    session_id: int = 10,
    workline_id: int = 20,
    device_id: int | None = None,
    command_id: int | None = None,
    trace_id: str = "trace-test",
    event_id: str | None = "evt-test",
    causation_id: str | None = None,
    attempt_count: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=inbox_id,
        kind=kind,
        payload_json=payload_json or {"event_type": "SCAN_COMPLETED"},
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
        workline_id=workline_id,
        execution_session_id=session_id,
        device_id=device_id,
        command_id=command_id,
        attempt_count=attempt_count,
    )


@pytest.mark.asyncio
async def test_claim_one_uses_injected_runtime_inbox_repository() -> None:
    """claim 必须服从构造器注入，不能旁路到全局 singleton。"""

    calls: list[dict[str, Any]] = []

    class _InjectedRepository:
        async def claim_received_with_token(self, db: Any, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append({"db": db, **kwargs})
            return [{"id": 71, "processor_token": kwargs["processor_token"]}]

    db = object()
    bridge = RuntimeInboxProcessorBridge(inbox_repository=_InjectedRepository())  # type: ignore[arg-type]

    claim = await bridge._claim_one(db, processor_token="injected-token")  # type: ignore[arg-type]

    assert claim == {"id": 71, "processor_token": "injected-token"}
    assert calls == [
        {
            "db": db,
            "limit": 1,
            "processor_token": "injected-token",
            "stale_after_seconds": WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
        }
    ]


def _make_session(
    *,
    session_id: int = 10,
    status: str = "RUNNING",
    workline_id: int = 20,
    awaiting_device_command_code: str | None = None,
    current_wait_type: str | None = None,
    context_json: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        workline_id=workline_id,
        status=status,
        awaiting_device_command_code=awaiting_device_command_code,
        current_wait_type=current_wait_type,
        context_json=context_json or {},
    )


def _make_workline(workline_id: int = 20) -> SimpleNamespace:
    return SimpleNamespace(id=workline_id, plugin_key="default")


# ============================================================
# Stage 1: Validation service
# ============================================================


class TestScanCompletedGate:
    @pytest.mark.asyncio
    async def test_scan_with_barcode_passes(self) -> None:
        """SCAN_COMPLETED + barcode → 继续走 orchestrator."""
        inbox = _make_inbox(
            payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "ABC"}},
        )
        outcome = await RuntimeInboxValidationService().pre_gate(
            _EmptyDb(),
            inbox=inbox,
            resolved_event_type="SCAN_COMPLETED",
            workline=None,
        )
        assert outcome.proceed_to_orchestrator is True

    @pytest.mark.asyncio
    async def test_scan_without_barcode_fails(self) -> None:
        """SCAN_COMPLETED 缺条码 → FAILED."""
        inbox = _make_inbox(payload_json={"event_type": "SCAN_COMPLETED", "data": {}})
        outcome = await RuntimeInboxValidationService().pre_gate(
            _EmptyDb(),
            inbox=inbox,
            resolved_event_type="SCAN_COMPLETED",
            workline=None,
        )
        assert outcome.proceed_to_orchestrator is False
        assert outcome.error_code is not None
        assert outcome.error_code.value == "CALLBACK_SCHEMA_INVALID"
        assert "barcode" in (outcome.error_message or "").lower() or "条码" in (outcome.error_message or "")

    @pytest.mark.asyncio
    async def test_non_scan_event_passes(self) -> None:
        """非 SCAN_COMPLETED 事件 → 直接通过 (由 ESTOP/TIMER 路由或 orchestrator 判定)."""
        inbox = _make_inbox(payload_json={"event_type": "COMMAND_RESULT", "data": {}})
        outcome = await RuntimeInboxValidationService().pre_gate(
            _EmptyDb(),
            inbox=inbox,
            resolved_event_type="COMMAND_RESULT",
            workline=None,
        )
        assert outcome.proceed_to_orchestrator is True


class _EmptyDb:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


class TestRelatedEntitiesContract:
    @pytest.mark.parametrize(("session_id", "expected"), ((41, 41), (None, None)))
    def test_diagnostic_snapshot_uses_only_canonical_workline_session_id(
        self,
        session_id: int | None,
        expected: int | None,
    ) -> None:
        data = {"session_id": session_id} if session_id is not None else {}
        inbox = RuntimeInbox(
            id=1,
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            kind="INTERNAL_EVENT",
            payload_json={"event_type": "INTERNAL_EVENT", "data": data},
            execution_session_id=999,
        )

        snapshot = _snapshot_inbox_for_diagnostic(inbox)
        trace = TraceContext.from_runtime(inbox=snapshot)

        assert snapshot.session_id == expected
        assert trace.session_id == expected

    @pytest.mark.parametrize("kind", ("INTERNAL_EVENT", "TIMER_TIMEOUT", "MANUAL_HOLD"))
    def test_workline_session_id_uses_explicit_column_with_canonical_consistency(self, kind: str) -> None:
        inbox = RuntimeInbox(
            provider_code="TEST",
            event_type=kind,
            kind=kind,
            payload_json={"event_type": kind, "data": {"session_id": 41}},
            workline_session_id=41,
            execution_session_id=999,
        )

        assert context_loader._canonical_workline_session_id(inbox) == 41

    def test_workline_session_id_rejects_explicit_canonical_mismatch(self) -> None:
        inbox = RuntimeInbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            kind="INTERNAL_EVENT",
            payload_json={"data": {"session_id": 41}},
            workline_session_id=42,
        )

        with pytest.raises(ValueError, match="workline_session_id mismatch"):
            context_loader._canonical_workline_session_id(inbox)

    def test_execution_session_id_is_not_a_workline_session_fallback(self) -> None:
        inbox = RuntimeInbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            kind="INTERNAL_EVENT",
            payload_json={"data": {}},
            execution_session_id=999,
        )

        assert context_loader._canonical_workline_session_id(inbox) is None

    def test_source_device_does_not_read_dynamic_normalized_input(self) -> None:
        device = SimpleNamespace(device_code="DYNAMIC-ONLY")
        inbox = SimpleNamespace(payload_json={}, normalized_input=SimpleNamespace(device_code="DYNAMIC-ONLY"))

        resolved = context_loader._resolve_effect_source_device(
            inbox, SimpleNamespace(context_json={}), {"R": [device]}
        )

        assert resolved is None

    @pytest.mark.asyncio
    async def test_wrapper_returns_device_before_command_with_distinct_sentinels(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RuntimeInbox context loader 合同固定为 session/workline/device/command。"""

        class _DeviceSentinel:
            pass

        class _CommandSentinel:
            pass

        device = _DeviceSentinel()
        command = _CommandSentinel()

        async def runtime_loader(*args: object, **kwargs: object) -> dict[str, object]:
            _ = args, kwargs
            return {
                "session": "session",
                "workline": "workline",
                "device": device,
                "command": command,
                "devices_by_role": {},
                "services": "services",
                "safety_checked": True,
            }

        monkeypatch.setattr(
            "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_context_loader.load_related_entities",
            runtime_loader,
        )

        loaded = await _load_related_entities(SimpleNamespace(), SimpleNamespace(id=1))

        assert loaded[2] is device
        assert loaded[3] is command


def test_processor_default_writeback_uses_injected_runtime_inbox_service() -> None:
    """bridge 与默认 write-back 必须共享同一 fenced terminal service。"""
    inbox_service = SimpleNamespace()

    processor = RuntimeInboxProcessorBridge(inbox_service=inbox_service)

    assert processor._writeback_service.inbox_service is inbox_service


def _replay_envelope(*, original_kind: str, original_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": "req-1",
        "actor": "42",
        "reason": "retry",
        "immediate_source_inbox_id": 8,
        "root_source_inbox_id": 7,
        "original_kind": original_kind,
        "original_payload": original_payload,
        "original_provider_code": "WMS",
        "original_event_type": original_payload.get("event_type", original_kind),
        "original_source_event_id": "source-7",
        "original_payload_hash": "hash-7",
        "original_workline_id": 20,
        "original_device_id": 21,
        "original_command_id": 22,
        "original_workline_session_id": 10,
        "original_execution_session_id": 11,
        "original_correlation_id": "corr-7",
        "original_trace_id": "trace-7",
        "original_event_id": "event-7",
        "original_causation_id": "cause-7",
    }


@pytest.mark.parametrize(
    ("original_kind", "original_payload"),
    [
        ("COMMAND_RESULT", {"event_type": "COMMAND_RESULT", "command_code": "CMD-1"}),
        ("DEVICE_EVENT", {"event_type": "SCAN_COMPLETED", "data": {"HHPN": "A"}}),
        ("EXTERNAL_HTTP", {"event_type": "WMS_EXCHANGE_COMPLETED"}),
        ("INTERNAL_EVENT", {"event_type": "SESSION_RESUME", "data": {"session_id": 10}}),
        ("TIMER_TIMEOUT", {"event_type": "TIMER_TIMEOUT", "data": {"session_id": 10}}),
    ],
)
def test_replay_request_projects_one_layer_to_original_semantics(
    original_kind: str,
    original_payload: dict[str, Any],
) -> None:
    inbox = _make_inbox(
        inbox_id=9,
        kind="REPLAY_REQUEST",
        payload_json=_replay_envelope(original_kind=original_kind, original_payload=original_payload),
    )

    projected = _project_replay_request(inbox)

    assert projected.id == 9
    assert projected.kind == original_kind
    assert projected.payload_json == original_payload
    assert projected.workline_id == 20
    assert projected.device_id == 21
    assert projected.command_id == 22
    assert projected.workline_session_id == 10
    assert projected.trace_id == "trace-7"
    assert inbox.kind == "REPLAY_REQUEST"


def test_replay_request_projection_rejects_invalid_envelope() -> None:
    inbox = _make_inbox(kind="REPLAY_REQUEST", payload_json={"original_kind": "REPLAY_REQUEST"})

    with pytest.raises(Exception, match="INVALID_REPLAY_ENVELOPE"):
        _project_replay_request(inbox)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("original_provider_code", ""),
        ("original_workline_id", "20"),
        ("original_trace_id", 123),
    ],
)
def test_replay_request_projection_rejects_invalid_routing_evidence(field: str, value: object) -> None:
    envelope = _replay_envelope(original_kind="INTERNAL_EVENT", original_payload={"event_type": "SESSION_RESUME"})
    envelope[field] = value

    with pytest.raises(Exception, match="INVALID_REPLAY_ENVELOPE"):
        _project_replay_request(_make_inbox(kind="REPLAY_REQUEST", payload_json=envelope))


@pytest.mark.asyncio
async def test_process_claimed_marks_invalid_replay_envelope_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = _make_inbox(inbox_id=91, kind="REPLAY_REQUEST", payload_json={"original_kind": "UNKNOWN"})

    class _Repository:
        async def get_by_id(self, *_args: Any) -> Any:
            return inbox

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

    service = _InboxService()

    async def _noop_diagnostic(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._record_diagnostic",
        _noop_diagnostic,
    )
    result = await RuntimeInboxProcessorBridge(
        inbox_repository=_Repository(),  # type: ignore[arg-type]
        inbox_service=service,  # type: ignore[arg-type]
    ).process_claimed(_Db(), claim={"id": 91, "processor_token": "token-91"})

    assert result["failed"] == 1
    assert result["processed"] == 1
    assert service.error_message is not None
    assert "INVALID_REPLAY_ENVELOPE" in service.error_message


class TestEstopTimerRouting:
    def test_estop_routes_to_estop(self) -> None:
        outcome = RuntimeInboxValidationService().classify_estop_or_timer(
            resolved_event_type="ESTOP_PRESSED",
            inbox_kind="DEVICE_EVENT",
        )
        assert outcome.estop_event is True
        assert outcome.terminal_disposition == WriteBackDisposition.PROCESSED

    def test_timer_routes_to_timer(self) -> None:
        outcome = RuntimeInboxValidationService().classify_estop_or_timer(
            resolved_event_type="TIMER_TIMEOUT",
            inbox_kind="TIMER_TIMEOUT",
        )
        assert outcome.timer_timeout_event is True

    def test_normal_event_continues(self) -> None:
        outcome = RuntimeInboxValidationService().classify_estop_or_timer(
            resolved_event_type="SCAN_COMPLETED",
            inbox_kind="DEVICE_EVENT",
        )
        assert outcome.proceed_to_orchestrator is True


class TestScanBarcodeHelper:
    def test_hhpn_field(self) -> None:
        assert _scan_completed_has_any_barcode_payload({"data": {"HHPN": "X"}}) is True

    def test_qty_string_field(self) -> None:
        assert _scan_completed_has_any_barcode_payload({"data": {"Qty": "1"}}) is True

    def test_empty_data(self) -> None:
        assert _scan_completed_has_any_barcode_payload({"data": {}}) is False

    def test_no_data_field(self) -> None:
        assert _scan_completed_has_any_barcode_payload({}) is False

    def test_no_string_value(self) -> None:
        assert _scan_completed_has_any_barcode_payload({"data": {"HHPN": ""}}) is False
        assert _scan_completed_has_any_barcode_payload({"data": {"HHPN": 123}}) is False


class TestEntryEventTypes:
    def test_default_with_no_plugin(self) -> None:
        assert "SCAN_COMPLETED" in _entry_event_types_for_workline(None)

    def test_default_with_unknown_plugin(self) -> None:
        workline = SimpleNamespace(plugin_key="non-existent")
        assert "SCAN_COMPLETED" in _entry_event_types_for_workline(workline)


# ============================================================
# Stage 2: Orchestrator delegate
# ============================================================


class TestOrchestratorDelegate:
    @pytest.mark.asyncio
    async def test_delegate_passes_through(self) -> None:
        """纯 delegate: 应直接转发到 OrchestratorService.process_inbox."""

        class _StubOrchestrator:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def process_inbox(
                self,
                *,
                session: object,
                workline: object,
                inbox: object,
                devices_by_role: dict[str, list[Any]],
                services: object,
                trace_id: str,
                write_callback: object,
            ) -> OrchestratorResult:
                return OrchestratorResult(
                    success=True,
                    intents=[],
                    error=None,
                    error_code=None,
                    error_domain=None,
                )

        delegate = RuntimeInboxOrchestratorDelegate(orchestrator_factory=_StubOrchestrator)
        result = await delegate.process(
            db=SimpleNamespace(),
            session=SimpleNamespace(),
            workline=SimpleNamespace(),
            inbox=SimpleNamespace(id=1),
            devices_by_role={},
            services=SimpleNamespace(),
            trace_id="trace-1",
            write_callback=None,
        )
        assert result.success is True
        assert result.intents == []

    @pytest.mark.asyncio
    async def test_delegate_preserves_failure_result(self) -> None:
        """Stage 2 失败结果必须原样返回，由 composition 决定终态。"""

        class _FailingOrchestrator:
            def __init__(self, *args: object, **kwargs: object) -> None:
                _ = args, kwargs

            async def process_inbox(self, **kwargs: object) -> OrchestratorResult:
                _ = kwargs
                return OrchestratorResult(success=False, error="business rejected", error_code="BIZ_REJECTED")

        result = await RuntimeInboxOrchestratorDelegate(orchestrator_factory=_FailingOrchestrator).process(
            db=SimpleNamespace(),
            session=SimpleNamespace(),
            workline=SimpleNamespace(),
            inbox=SimpleNamespace(id=1),
            devices_by_role={},
            services=SimpleNamespace(),
            trace_id="trace-failure",
        )

        assert result.success is False
        assert result.error == "business rejected"
        assert result.error_code == "BIZ_REJECTED"

    @pytest.mark.asyncio
    async def test_delegate_enforces_timeout_boundary(self) -> None:
        """Stage 2 超时边界必须抛 TimeoutError 交给 composition 统一失败处理。"""

        class _SlowOrchestrator:
            def __init__(self, *args: object, **kwargs: object) -> None:
                _ = args, kwargs

            async def process_inbox(self, **kwargs: object) -> OrchestratorResult:
                _ = kwargs
                await asyncio.sleep(0.05)
                return OrchestratorResult(success=True, intents=[])

        delegate = RuntimeInboxOrchestratorDelegate(
            orchestrator_factory=_SlowOrchestrator,
            timeout_seconds=0.001,
        )
        with pytest.raises(TimeoutError):
            await delegate.process(
                db=SimpleNamespace(),
                session=SimpleNamespace(),
                workline=SimpleNamespace(),
                inbox=SimpleNamespace(id=1),
                devices_by_role={},
                services=SimpleNamespace(),
                trace_id="trace-timeout",
            )


# ============================================================
# Stage 3: Write-back service
# ============================================================


class TestSessionWriteSnapshot:
    def test_snapshot_extracts_status_and_awaiting(self) -> None:
        session = _make_session(status="RUNNING", awaiting_device_command_code="CMD-1")
        snap = _session_write_snapshot(session)
        assert snap[0] == "RUNNING"
        assert snap[1] == "CMD-1"

    def test_snapshot_change_detected(self) -> None:
        snap_a = _session_write_snapshot(_make_session(status="RUNNING", awaiting_device_command_code="CMD-1"))
        snap_b = _session_write_snapshot(
            _make_session(status="WAITING_DEVICE_RESULT", awaiting_device_command_code="CMD-1")
        )
        assert snap_a != snap_b


class TestIsLateOrDuplicateCommandResult:
    def test_terminal_session_is_late(self) -> None:
        inbox = _make_inbox(kind="COMMAND_RESULT")
        session = _make_session(status="COMPLETED")
        command = SimpleNamespace(command_code="CMD-1", status="COMPLETED")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=command)
            is True
        )

    def test_running_session_with_matching_command(self) -> None:
        inbox = _make_inbox(kind="COMMAND_RESULT")
        session = _make_session(
            status="WAITING_DEVICE_RESULT",
            awaiting_device_command_code="CMD-1",
        )
        command = SimpleNamespace(command_code="CMD-1", status="COMPLETED")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=command)
            is False
        )

    def test_non_command_result_kind_never_late(self) -> None:
        inbox = _make_inbox(kind="DEVICE_EVENT")
        session = _make_session(status="COMPLETED")
        command = SimpleNamespace(command_code="CMD-1", status="COMPLETED")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=command)
            is False
        )

    def test_non_terminal_command_status(self) -> None:
        inbox = _make_inbox(kind="COMMAND_RESULT")
        session = _make_session(status="RUNNING", awaiting_device_command_code="CMD-1")
        command = SimpleNamespace(command_code="CMD-1", status="PENDING")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=command)
            is False
        )


class TestArchiveTimelineSequence:
    @pytest.mark.asyncio
    async def test_duplicate_and_late_archives_delegate_sequence_allocation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同 session 多次归档必须由 timeline service 分配 seq_no。"""
        requested_seq_nos: list[int | None] = []

        async def add_with_sequence(db: object, timeline: object, *, seq_no: int | None = None) -> int:
            _ = db, timeline
            requested_seq_nos.append(seq_no)
            return len(requested_seq_nos)

        monkeypatch.setattr(
            "src.app.runtime.orchestration.services.trace.timeline_sequence_service.add_timeline_with_sequence",
            add_with_sequence,
        )
        session = _make_session()
        workline = _make_workline()
        command = SimpleNamespace(id=99, command_code="CMD-1", status="COMPLETED")
        for inbox_id in (1, 2):
            inbox = _make_inbox(inbox_id=inbox_id)
            await _record_duplicate_entry_archive_timeline(
                SimpleNamespace(),
                session=session,
                workline=workline,
                inbox=inbox,
                payload=inbox.payload_json,
                reason="DUPLICATE",
            )
            await _record_late_command_result_archive_timeline(
                SimpleNamespace(),
                session=session,
                workline=workline,
                inbox=inbox,
                command=command,
                payload=inbox.payload_json,
                reason="LATE",
            )

        assert requested_seq_nos == [None, None, None, None]


class TestResultRequiresOutboxDispatch:
    def test_empty_result(self) -> None:
        assert _result_requires_outbox_dispatch(OrchestratorResult(success=True, intents=[])) is False

    def test_command_intent(self) -> None:
        from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind

        intent = RuntimeIntent(kind=RuntimeIntentKind.COMMAND, action="PICK", payload={"x": 1})
        assert _result_requires_outbox_dispatch(OrchestratorResult(success=True, intents=[intent])) is True

    def test_continue_next_with_action(self) -> None:
        from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind

        intent = RuntimeIntent(kind=RuntimeIntentKind.CONTINUE_NEXT, action="RESUME")
        assert _result_requires_outbox_dispatch(OrchestratorResult(success=True, intents=[intent])) is True


class TestBuildWriteCallback:
    @pytest.mark.asyncio
    async def test_write_callback_orchestrator_writes_processed(self) -> None:
        """write-back 正常路径: 业务 effect 返回 PROCESSED → mark_as_processed."""
        from contextlib import suppress

        inbox = _make_inbox()
        session = _make_session()
        workline = _make_workline()
        command = SimpleNamespace(command_code="CMD-1", status="PENDING")

        class _FakeDb:
            async def refresh(self, value: object) -> None:
                _ = value

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                pass

        class _FakeWriteBack:
            async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
                return RuntimeIntentEffectResult.processed()

        class _FakeInboxService:
            def __init__(self) -> None:
                self.mark_processed_calls: list[dict[str, Any]] = []

            async def mark_processed(
                self,
                db: object,
                *,
                inbox_id: int,
                lease_token: str,
            ) -> object:
                self.mark_processed_calls.append({"inbox_id": inbox_id, "lease_token": lease_token})
                return SimpleNamespace(id=inbox_id)

        state = WriteBackState()
        write_callback = RuntimeInboxWriteBackService(
            write_back_service=_FakeWriteBack(),
            inbox_service=_FakeInboxService(),
        ).build_write_callback(
            db=_FakeDb(),
            session=session,
            workline=workline,
            inbox=inbox,
            devices_by_role={},
            device=None,
            command=command,
            inbox_pk=1,
            session_snapshot=_session_write_snapshot(session),
            sse_workline_id=20,
            sse_session_id=10,
            processor_token="token-1",
            state=state,
        )

        result = OrchestratorResult(success=True, intents=[])
        with suppress(Exception):
            await write_callback(result)

        # 验证: state.disposition == PROCESSED + write_effects_applied = True
        assert state.disposition == WriteBackDisposition.PROCESSED
        assert state.write_effects_applied is True

    @pytest.mark.asyncio
    async def test_write_callback_resource_retry_marks_retryable_failure(self) -> None:
        """Stage 3 RESOURCE_RETRY 必须携带 lease token 写 retryable FAILED。"""
        inbox = _make_inbox()
        session = _make_session()
        calls: list[dict[str, Any]] = []

        class _Db:
            async def refresh(self, value: object) -> None:
                _ = value

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                pass

        class _WriteBack:
            async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
                _ = args, kwargs
                return RuntimeIntentEffectResult.resource_retry()

        class _InboxService:
            async def mark_failed(self, db: object, **kwargs: object) -> bool:
                _ = db
                calls.append(dict(kwargs))
                return True

        state = WriteBackState()
        callback = RuntimeInboxWriteBackService(
            write_back_service=_WriteBack(),
            inbox_service=_InboxService(),
        ).build_write_callback(
            _Db(),
            session=session,
            workline=_make_workline(),
            inbox=inbox,
            devices_by_role={},
            device=None,
            command=None,
            inbox_pk=1,
            session_snapshot=_session_write_snapshot(session),
            sse_workline_id=20,
            sse_session_id=10,
            processor_token="lease-resource",
            state=state,
        )

        await callback(OrchestratorResult(success=True, intents=[]))

        assert calls == [
            {
                "inbox_id": 1,
                "lease_token": "lease-resource",
                "error_message": "RESOURCE_WAIT",
                "retryable": True,
                "consume_attempt": False,
            }
        ]
        assert state.disposition == WriteBackDisposition.RESOURCE_RETRY
        assert state.write_effects_applied is True

    @pytest.mark.asyncio
    async def test_write_callback_rejects_stale_session_before_effects(self) -> None:
        """Stage 3 stale snapshot 必须在业务 effect 和终态写入前拒绝。"""
        inbox = _make_inbox()
        session = _make_session(status="RUNNING")
        writeback_called = False
        rollbacks = 0

        class _Db:
            async def refresh(self, value: object) -> None:
                value.status = "WAITING_DEVICE_RESULT"

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                nonlocal rollbacks
                rollbacks += 1

        class _WriteBack:
            async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
                nonlocal writeback_called
                _ = args, kwargs
                writeback_called = True
                return RuntimeIntentEffectResult.processed()

        state = WriteBackState()
        callback = RuntimeInboxWriteBackService(
            write_back_service=_WriteBack(),
            inbox_service=SimpleNamespace(),
        ).build_write_callback(
            _Db(),
            session=session,
            workline=_make_workline(),
            inbox=inbox,
            devices_by_role={},
            device=None,
            command=None,
            inbox_pk=1,
            session_snapshot=("RUNNING", None),
            sse_workline_id=20,
            sse_session_id=10,
            processor_token="lease-stale",
            state=state,
        )

        with pytest.raises(RuntimeError, match="refusing stale orchestrator effects"):
            await callback(OrchestratorResult(success=True, intents=[]))

        assert writeback_called is False
        assert rollbacks == 1
        assert state.write_effects_applied is False

    @pytest.mark.asyncio
    async def test_write_callback_rolls_back_effect_failure(self) -> None:
        """Stage 3 业务 effect 失败必须回滚且不伪造终态。"""
        session = _make_session()
        rollbacks = 0

        class _Db:
            async def refresh(self, value: object) -> None:
                _ = value

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                nonlocal rollbacks
                rollbacks += 1

        class _WriteBack:
            async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
                _ = args, kwargs
                raise RuntimeError("effect failed")

        state = WriteBackState()
        callback = RuntimeInboxWriteBackService(
            write_back_service=_WriteBack(),
            inbox_service=SimpleNamespace(),
        ).build_write_callback(
            _Db(),
            session=session,
            workline=_make_workline(),
            inbox=_make_inbox(),
            devices_by_role={},
            device=None,
            command=None,
            inbox_pk=1,
            session_snapshot=_session_write_snapshot(session),
            sse_workline_id=20,
            sse_session_id=10,
            processor_token="lease-effect-failure",
            state=state,
        )

        with pytest.raises(RuntimeError, match="effect failed"):
            await callback(OrchestratorResult(success=True, intents=[]))

        assert rollbacks == 1
        assert state.write_effects_applied is False


# ============================================================
# Internal helpers
# ============================================================
