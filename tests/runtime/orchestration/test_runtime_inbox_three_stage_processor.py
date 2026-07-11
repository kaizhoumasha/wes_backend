"""Tests for RuntimeInbox 三阶段 Processor 拆分 services (Task 5).

覆盖:
- RuntimeInboxValidationService: SCAN gate + ESTOP/TIMER 路由
- RuntimeInboxOrchestratorDelegate: pure delegate 透传
- RuntimeInboxWriteBackService: WRITE 锁回调
- RuntimeInboxProcessorBridge (composition): 单条 claim-and-process
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_result import (
    RuntimeIntentEffectResult,
    WriteBackDisposition,
)
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
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
    _record_late_command_result_archive_timeline,
    _result_requires_outbox_dispatch,
    _session_write_snapshot,
)

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


# ============================================================
# Internal helpers
# ============================================================
