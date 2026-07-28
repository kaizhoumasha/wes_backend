"""RuntimeInbox 三阶段 Processor 的 characterization 测试。"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxManualHoldEvidence
from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge as bridge_module
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
    RuntimeInboxValidationService,
)
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet, WriteDisposition


@dataclass(frozen=True, slots=True)
class ParityCase:
    """同一输入表中的单个 characterization case。"""

    name: str
    kind: str = "DEVICE_EVENT"
    payload: dict[str, Any] | None = None
    session_status: str | None = "RUNNING"
    workline_present: bool = True
    device_id: int | None = None
    awaiting_command: str | None = None
    current_wait_type: str | None = None
    failure_code: str | None = None
    session_context: dict[str, Any] | None = None
    plugin_key: str = "default"
    command_status: str | None = None
    orchestration: Literal["success", "exception", "failure"] = "success"
    writeback: Literal["processed", "resource_wait"] = "processed"
    expected: tuple[int, int, int, int, int] = (1, 1, 0, 0, 0)
    expected_archive: str | None = None
    expected_terminal: str | None = "processed"
    expected_error: str | None = None
    expected_diagnostic: str | None = None
    expected_source_device_id: int | None = None
    expected_late_command_id: int | None = None
    expected_reconciliation: bool = False
    stale_session_on_write: bool = False
    expected_writeback_calls: int | None = None


def _canonical_replay_payload(original_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": "parity-replay",
        "actor": "42",
        "reason": "retry invalid payload",
        "immediate_source_inbox_id": 8,
        "root_source_inbox_id": 7,
        "original_kind": "DEVICE_EVENT",
        "original_payload": original_payload,
        "original_provider_code": "ECS",
        "original_event_type": "SCAN_COMPLETED",
        "original_source_event_id": "evt-invalid",
        "original_payload_hash": "hash-invalid",
        "original_workline_id": 20,
        "original_device_id": None,
        "original_command_id": None,
        "original_workline_session_id": 10,
        "original_execution_session_id": None,
        "original_correlation_id": None,
        "original_trace_id": "trace-parity",
        "original_event_id": "evt-invalid",
        "original_causation_id": None,
    }


PARITY_CASES = (
    ParityCase(
        name="scan_invalid",
        payload={"event_type": "SCAN_COMPLETED", "data": {}},
        expected=(1, 0, 1, 0, 0),
        expected_terminal="failed",
        expected_error="barcode",
    ),
    ParityCase(
        name="scan_valid",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "ABC123"}},
        device_id=77,
        expected_source_device_id=77,
    ),
    ParityCase(
        name="estop_missing_workline",
        payload={"event_type": "ESTOP_PRESSED", "data": {}},
        workline_present=False,
        expected=(1, 0, 1, 0, 0),
        expected_terminal="failed",
        expected_error="ESTOP_PRESSED missing workline context",
    ),
    ParityCase(
        name="estop_with_device_and_command",
        payload={"event_type": "ESTOP_PRESSED", "data": {}},
        device_id=77,
        command_status="PENDING",
        expected_source_device_id=77,
    ),
    ParityCase(
        name="timer_timeout",
        kind="TIMER_TIMEOUT",
        payload={"event_type": "TIMER_TIMEOUT", "data": {}},
        expected_terminal="processed",
        expected_reconciliation=True,
    ),
    ParityCase(
        name="missing_context",
        kind="EXTERNAL_HTTP",
        payload={"event_type": "EXTERNAL_CALLBACK", "data": {}},
        session_status=None,
        workline_present=False,
        expected_writeback_calls=0,
    ),
    ParityCase(
        name="external_runtime_capability",
        kind="EXTERNAL_HTTP",
        payload={
            "event_type": "EXTERNAL_CALLBACK",
            "runtime_capability": "rough_sorter_inbound",
            "data": {},
        },
        expected_writeback_calls=1,
    ),
    ParityCase(
        name="duplicate_entry",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "DUP"}},
        session_status="WAITING_DEVICE_RESULT",
        awaiting_command="CMD-001",
        expected_archive="DUPLICATE_ENTRY_ARCHIVED",
    ),
    ParityCase(
        name="payload_invalid_manual_replay",
        kind="REPLAY_REQUEST",
        payload=_canonical_replay_payload(
            {
                "event_type": "SCAN_COMPLETED",
                "data": {"HHPN": "REPLAY"},
            }
        ),
        session_status="MANUAL_HOLD",
        failure_code="PAYLOAD_INVALID",
    ),
    ParityCase(
        name="resource_wait_retry_same_inbox",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "RETRY"}},
        session_status="WAITING_DEVICE_RESULT",
        current_wait_type="RESOURCE_WAIT",
        session_context={"resource_wait": {"inbox_id": 1}},
    ),
    ParityCase(
        name="duplicate_material_conflict",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "NEW"}},
        session_status="WAITING_DEVICE_RESULT",
        plugin_key="rough_sorter",
        session_context={
            "initial_payload": {
                "event_type": "SCAN_COMPLETED",
                "data": {"HHPN": "OLD"},
            },
        },
        expected=(1, 0, 1, 0, 0),
        expected_terminal="dead_letter",
        expected_error="ENTRY_MATERIAL_IDENTITY_CONFLICT",
        expected_diagnostic="CALLBACK_SCHEMA_INVALID",
    ),
    ParityCase(
        name="late_command_result",
        kind="COMMAND_RESULT",
        payload={"event_type": "COMMAND_RESULT", "data": {}},
        session_status="COMPLETED",
        command_status="COMPLETED",
        expected_archive="LATE_COMMAND_RESULT_ARCHIVED",
        expected_late_command_id=99,
    ),
    ParityCase(
        name="orchestrator_exception",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "ERR"}},
        orchestration="exception",
        expected=(1, 0, 1, 0, 0),
        expected_terminal="failed",
        expected_error="simulated orchestrator failure",
    ),
    ParityCase(
        name="stale_session_snapshot",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "STALE"}},
        stale_session_on_write=True,
        expected=(1, 0, 1, 0, 0),
        expected_terminal="failed",
        expected_error="Session state changed before WRITE apply",
        expected_diagnostic="UNKNOWN",
        expected_writeback_calls=0,
    ),
    ParityCase(
        name="resource_wait",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "WAIT"}},
        writeback="resource_wait",
        expected=(1, 0, 0, 0, 1),
        expected_terminal="failed",
        expected_error="PLUGIN_SNAPSHOT_STALE",
    ),
    ParityCase(
        name="orchestrator_failure",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "FAIL"}},
        orchestration="failure",
        expected=(1, 0, 1, 0, 0),
        expected_terminal="processed",
    ),
)


class _FakeDb:
    def __init__(self, *, stale_session_on_write: bool = False) -> None:
        self.committed = 0
        self.rolled_back = 0
        self.stale_session_on_write = stale_session_on_write
        self.safety_effects: set[int] = set()

    async def refresh(self, value: object) -> None:
        if self.stale_session_on_write:
            value.status = "WAITING_DEVICE_RESULT"

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rolled_back += 1


class _StatefulSafetyFake:
    """模拟 active incident repository，重复 ESTOP 复用当前 incident。"""

    def __init__(self) -> None:
        self.active_incident: SimpleNamespace | None = None
        self.create_count = 0
        self.call_count = 0

    async def handle_estop(self, db: _FakeDb, **kwargs: object) -> SimpleNamespace:
        _ = kwargs
        self.call_count += 1
        if self.active_incident is None:
            self.create_count += 1
            self.active_incident = SimpleNamespace(id=122 + self.create_count)
        db.safety_effects.add(self.active_incident.id)
        await db.commit()
        return self.active_incident


class _Repository:
    def __init__(self, inbox: object) -> None:
        self.inbox = inbox

    async def get_by_id(self, db: object, inbox_id: int) -> object:
        _ = db
        assert inbox_id == 1
        return self.inbox


class _TerminalRecorder:
    """记录 RuntimeInboxService fenced 终态写入。"""

    def __init__(self, inbox: object, *, accept_updates: bool = True) -> None:
        self.repo = _Repository(inbox)
        self.actions: list[dict[str, Any]] = []
        self.accept_updates = accept_updates

    def _record(self, action: str, args: tuple[object, ...], kwargs: dict[str, Any]) -> None:
        self.actions.append(
            {
                "action": action,
                "inbox_id": kwargs.get("inbox_id") or (args[1] if len(args) > 1 else None),
                "token": kwargs.get("lease_token") or kwargs.get("processor_token"),
                "error": kwargs.get("error_message") or (args[2] if len(args) > 2 else None),
                "retryable": kwargs.get("retryable"),
            }
        )

    async def mark_as_processed(self, *args: object, **kwargs: object) -> object:
        self._record("processed", args, kwargs)
        return SimpleNamespace(id=1)

    async def mark_as_failed(self, *args: object, **kwargs: object) -> object:
        self._record("failed", args, kwargs)
        return SimpleNamespace(id=1)

    async def mark_as_dead_letter(self, *args: object, **kwargs: object) -> object:
        self._record("dead_letter", args, kwargs)
        return SimpleNamespace(id=1)

    async def park_for_retry(self, *args: object, **kwargs: object) -> object:
        self._record("resource_wait", args, kwargs)
        return SimpleNamespace(id=1)

    async def mark_processed(self, *args: object, **kwargs: object) -> bool:
        self._record("processed", args, kwargs)
        return self.accept_updates

    async def mark_failed(self, *args: object, **kwargs: object) -> bool:
        action = "resource_wait" if kwargs.get("error_code") == "RESOURCE_WAIT" else "failed"
        self._record(action, args, kwargs)
        return self.accept_updates

    async def mark_dead_letter(self, *args: object, **kwargs: object) -> bool:
        self._record("dead_letter", args, kwargs)
        return self.accept_updates


def _build_entities(
    case: ParityCase,
) -> tuple[SimpleNamespace, object | None, object | None, object | None, object | None]:
    payload = case.payload or {}
    event_type = "REPLAY_REQUEST" if case.kind == "REPLAY_REQUEST" else str(payload.get("event_type") or case.kind)
    inbox = SimpleNamespace(
        id=1,
        kind=case.kind,
        event_type=event_type,
        payload_json=case.payload,
        source_message_id="msg-parity",
        trace_id="trace-parity",
        event_id="evt-parity",
        causation_id=None,
        workline_id=20 if case.workline_present else None,
        session_id=10 if case.session_status is not None else None,
        execution_session_id=10 if case.session_status is not None else None,
        device_id=case.device_id,
        command_id=99 if case.command_status else None,
        attempt_count=0,
    )
    session = None
    if case.session_status is not None:
        session = SimpleNamespace(
            id=10,
            workline_id=20,
            status=case.session_status,
            awaiting_device_command_code=case.awaiting_command,
            current_wait_type=case.current_wait_type,
            failure_code=case.failure_code,
            plugin_key=case.plugin_key,
            plugin_binding_id=17,
            plugin_binding_version=4,
            plugin_identity="rough_sorter@rough_sorter.v2:" + "a" * 64,
            plugin_config_hash="c" * 64,
            plugin_index_digest="b" * 64,
            plugin_state_json={"phase": "READY"},
            plugin_state_version=1,
            version=7,
            context_json=case.session_context or {},
        )
    workline = SimpleNamespace(id=20, plugin_key=case.plugin_key) if case.workline_present else None
    device = SimpleNamespace(id=case.device_id, device_code="DEVICE-77") if case.device_id is not None else None
    command = None
    if case.command_status is not None:
        command = SimpleNamespace(id=99, command_code="CMD-001", status=case.command_status)
    return inbox, session, workline, device, command


def _as_tuple(result: dict[str, int]) -> tuple[int, int, int, int, int]:
    return (
        result["processed"],
        result["success"],
        result["failed"],
        result["skipped"],
        result["resource_wait"],
    )


def _install_test_runner(processor: RuntimeInboxProcessorBridge, runner: object) -> None:
    processor._generated_attempt_runner = runner  # type: ignore[assignment]

    async def _build_request(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(snapshot=SimpleNamespace())

    async def _pin_runtime(*_args: object, **_kwargs: object) -> None:
        return None

    processor._build_generated_dispatch_request = _build_request  # type: ignore[method-assign]
    processor._pin_attempt_runtime_to_dispatch_snapshot = _pin_runtime  # type: ignore[method-assign]


async def _run_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: ParityCase,
    accept_updates: bool = True,
    safety_state: _StatefulSafetyFake | None = None,
) -> tuple[
    dict[str, int],
    list[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    _FakeDb,
]:
    inbox, session, workline, device, command = _build_entities(case)
    db = _FakeDb(stale_session_on_write=case.stale_session_on_write)
    terminal = _TerminalRecorder(inbox, accept_updates=accept_updates)
    archives: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    safety_state = safety_state or _StatefulSafetyFake()

    async def load_related(*args: object, **kwargs: object) -> dict[str, object]:
        _ = args, kwargs
        return {
            "session": session,
            "workline": workline,
            "device": device,
            "command": command,
            "devices_by_role": {},
            "services": SimpleNamespace(),
            "safety_checked": True,
        }

    async def load_related_tuple(*args: object, **kwargs: object) -> tuple[object, ...]:
        loaded = await load_related(*args, **kwargs)
        return (
            loaded["session"],
            loaded["workline"],
            loaded["device"],
            loaded["command"],
            loaded["devices_by_role"],
            loaded["services"],
            loaded["safety_checked"],
        )

    async def record_diagnostic(*args: object, **kwargs: object) -> None:
        _ = args
        error_code = kwargs.get("error_code")
        diagnostics.append(
            {
                "error_code": getattr(error_code, "value", error_code),
                "message": kwargs.get("message"),
                "extra": kwargs.get("extra"),
            }
        )

    async def record_duplicate(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        archives.append("DUPLICATE_ENTRY_ARCHIVED")

    async def record_late(*args: object, **kwargs: object) -> None:
        _ = args
        archives.append("LATE_COMMAND_RESULT_ARCHIVED")
        interactions.append({"kind": "late_archive", "command_id": getattr(kwargs.get("command"), "id", None)})

    async def timer_handler(*args: object, **kwargs: object) -> None:
        _ = args
        interactions.append(
            {
                "kind": "reconciliation",
                "inbox_id": kwargs.get("inbox_id"),
            }
        )

    async def estop_handler(*args: object, **kwargs: object) -> object:
        estop_db = args[0]
        incident = await safety_state.handle_estop(estop_db, **kwargs)
        interactions.append(
            {
                "kind": "estop",
                "source_device_id": kwargs.get("source_device_id"),
                "source_command_id": kwargs.get("source_command_id"),
            }
        )
        return incident

    class _Runner:
        async def run(self, _context: object) -> AttemptWriteSet:
            if case.orchestration == "exception":
                raise RuntimeError("simulated orchestrator failure")
            if case.orchestration == "failure":
                return AttemptWriteSet(
                    evidence=(),
                    next_state={"phase": "READY"},
                    intents=(),
                    outcome_code="HOLD",
                    hold_reason="simulated failure",
                )
            return AttemptWriteSet(evidence=(), next_state={"phase": "READY"}, intents=(), outcome_code="ROUTE_A")

    class _PlatformWriteBack:
        async def commit_plugin_attempt(self, db: object, **kwargs: object) -> WriteDisposition:
            if case.stale_session_on_write:
                raise RuntimeError("Session state changed before WRITE apply")
            interactions.append({"kind": "writeback", "source_device_id": case.device_id})
            if case.writeback == "resource_wait":
                return WriteDisposition.SAFE_RETRY
            accepted = await terminal.mark_processed(
                db,
                inbox_id=1,
                lease_token=kwargs["expected_snapshot"].processor_token,  # type: ignore[index]
            )
            if not accepted:
                raise RuntimeError("RuntimeInbox lost fencing during plugin writeback")
            await db.commit()  # type: ignore[attr-defined]
            return WriteDisposition.COMMITTED

    class _RecordedReplay:
        async def load(self, *_args: object, **_kwargs: object) -> object:
            from src.app.runtime.system_capabilities.replay import RecordedReplayResolution

            return RecordedReplayResolution(
                decision={
                    "outcome_code": "REPLAY_ACCEPTED_NOOP",
                    "hold_reason": None,
                    "intents": [],
                    "next_state": {"phase": "READY"},
                }
            )

    class _LoggerRecorder:
        def __getattr__(self, level: str) -> object:
            def record(message: object) -> None:
                interactions.append({"kind": "log", "level": level, "message": str(message)})

            return record

    monkeypatch.setattr(bridge_module, "_load_related_entities", load_related_tuple)
    monkeypatch.setattr(bridge_module, "_record_diagnostic", record_diagnostic)
    monkeypatch.setattr(bridge_module, "_record_duplicate_entry_archive_timeline", record_duplicate, raising=False)
    monkeypatch.setattr(bridge_module, "_record_late_command_result_archive_timeline", record_late, raising=False)
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_runtime_reconciliation_service.handle_timer_timeout",
        timer_handler,
    )
    monkeypatch.setattr(bridge_module, "logger", _LoggerRecorder())
    monkeypatch.setattr(
        "src.app.workline.services.safety_service.workline_safety_service.handle_estop",
        estop_handler,
    )

    class _ReplaySourceValidator:
        async def validate_for_consumption(self, _db: Any, *, source: Any) -> SimpleNamespace:
            # Parity 只锁定验真后的下游行为；真实性由专门的对抗测试覆盖。
            return SimpleNamespace(envelope=source.payload_json, root_source=SimpleNamespace())

    processor = RuntimeInboxProcessorBridge(
        validation_service=RuntimeInboxValidationService(
            inbox_repository=SimpleNamespace(
                get_latest_manual_hold_evidence=AsyncMock(
                    return_value=(
                        RuntimeInboxManualHoldEvidence(
                            session_id=10,
                            action_type="MANUAL_HOLD",
                            timeline_status="PENDING",
                            reason_code="PAYLOAD_INVALID",
                            related_inbox_id=8,
                            source_session_id=10,
                            source_status="DEAD_LETTER",
                        )
                        if case.name == "payload_invalid_manual_replay"
                        else None
                    )
                )
            )  # type: ignore[arg-type]
        ),
        writeback_service=_PlatformWriteBack(),  # type: ignore[arg-type]
        inbox_service=terminal,  # type: ignore[arg-type]
        inbox_repository=_Repository(inbox),  # type: ignore[arg-type]
        replay_source_validator=_ReplaySourceValidator(),  # type: ignore[arg-type]
        recorded_replay_service=_RecordedReplay(),  # type: ignore[arg-type]
    )
    _install_test_runner(processor, _Runner())
    result = await processor.process_claimed(db, claim={"id": 1, "processor_token": "token-parity"})
    return result, archives, terminal.actions, diagnostics, interactions, db


@pytest.mark.asyncio
@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda case: case.name)
async def test_runtime_processor_characterization(
    monkeypatch: pytest.MonkeyPatch,
    case: ParityCase,
) -> None:
    """已验证的生产行为由 RuntimeInbox 三阶段入口持续承载。"""
    result, archives, terminal_actions, diagnostics, interactions, _ = await _run_case(
        monkeypatch,
        case=case,
    )

    assert _as_tuple(result) == case.expected
    assert archives == ([case.expected_archive] if case.expected_archive is not None else [])
    assert [call["action"] for call in terminal_actions] == (
        [case.expected_terminal] if case.expected_terminal is not None else []
    )
    for call in terminal_actions:
        assert call["inbox_id"] == 1
        assert call["token"] == "token-parity"
    if case.expected_error is not None:
        assert case.expected_error.lower() in str(terminal_actions[0]["error"]).lower()
    if case.name == "orchestrator_exception":
        assert terminal_actions[0]["retryable"] is True
    if case.expected_diagnostic is not None:
        assert diagnostics[-1]["error_code"] == case.expected_diagnostic
    if case.expected_source_device_id is not None:
        source_calls = [call for call in interactions if call["kind"] in {"writeback", "estop"}]
        assert source_calls[-1]["source_device_id"] == case.expected_source_device_id
    if case.expected_late_command_id is not None:
        late_calls = [call for call in interactions if call["kind"] == "late_archive"]
        assert late_calls == [{"kind": "late_archive", "command_id": case.expected_late_command_id}]
    reconciliation_calls = [call for call in interactions if call["kind"] == "reconciliation"]
    assert reconciliation_calls == ([{"kind": "reconciliation", "inbox_id": 1}] if case.expected_reconciliation else [])
    if case.expected_writeback_calls is not None:
        assert len([call for call in interactions if call["kind"] == "writeback"]) == case.expected_writeback_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "first_action"),
    (
        ("duplicate_entry", "processed"),
        ("scan_invalid", "failed"),
        ("duplicate_material_conflict", "dead_letter"),
        ("scan_valid", "processed"),
        ("resource_wait", "failed"),
        ("missing_context", "processed"),
    ),
)
async def test_three_stage_lost_fencing_rolls_back_without_success(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    first_action: str,
) -> None:
    """RuntimeInbox lease 丢失时不得提交 effects/timeline/diagnostic 或报告成功。"""
    case = next(item for item in PARITY_CASES if item.name == case_name)

    result, _, terminal_actions, _, _, db = await _run_case(
        monkeypatch,
        case=case,
        accept_updates=False,
    )

    assert result["success"] == 0
    assert result["resource_wait"] == 0
    assert result["failed"] == 1
    assert terminal_actions[0]["action"] == first_action
    # 平台 attempt 在无 DB 决策前会提交 Stage 1 snapshot；fencing 丢失只禁止 Stage 3 提交。
    assert db.committed == (1 if case_name in {"scan_valid", "resource_wait"} else 0)
    assert db.rolled_back >= 1


@pytest.mark.asyncio
async def test_estop_lost_fencing_preserves_fail_safe_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ESTOP safety effects 已内部提交，外层 rollback 只清理当前未提交事务。"""
    case = next(item for item in PARITY_CASES if item.name == "estop_with_device_and_command")

    result, _, terminal_actions, _, interactions, db = await _run_case(
        monkeypatch,
        case=case,
        accept_updates=False,
    )

    assert db.safety_effects == {123}
    assert db.committed == 1
    assert db.rolled_back >= 1
    assert result["success"] == 0
    assert result["failed"] == 1
    assert terminal_actions[0]["action"] == "processed"
    assert any("lease lost" in call["message"] for call in interactions if call["kind"] == "log")


@pytest.mark.asyncio
async def test_repeated_estop_reuses_active_fail_safe_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重复 ESTOP 使用同一 active incident effect，仍分别执行 fenced 终态。"""
    case = next(item for item in PARITY_CASES if item.name == "estop_with_device_and_command")

    safety_state = _StatefulSafetyFake()
    first = await _run_case(
        monkeypatch,
        case=case,
        safety_state=safety_state,
    )
    second = await _run_case(
        monkeypatch,
        case=case,
        safety_state=safety_state,
    )

    assert safety_state.call_count == 2
    assert safety_state.create_count == 1
    assert first[-1].safety_effects == {123}
    assert second[-1].safety_effects == {123}
    assert first[0]["success"] == 1
    assert second[0]["success"] == 1
    assert first[2][0]["action"] == "processed"
    assert second[2][0]["action"] == "processed"
