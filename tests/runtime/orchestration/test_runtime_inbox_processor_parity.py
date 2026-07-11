"""Legacy InboxBatchProcessor 与三阶段 RuntimeInbox Processor 的同表 parity 测试。"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
from src.app.runtime.orchestration.repositories.inbox_repository import WorklineInboxClaim
from src.app.runtime.orchestration.services.inbox import inbox_batch_processor as legacy_module
from src.app.runtime.orchestration.services.inbox.inbox_batch_processor import InboxBatchProcessor
from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge as bridge_module
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
)

ProcessorKind = Literal["legacy", "three_stage"]


@dataclass(frozen=True, slots=True)
class ParityCase:
    """同一输入表中的单个 characterization case。"""

    name: str
    kind: str = "DEVICE_EVENT"
    payload: dict[str, Any] | None = None
    session_status: str | None = "RUNNING"
    workline_present: bool = True
    awaiting_command: str | None = None
    command_status: str | None = None
    orchestration: Literal["success", "exception", "failure"] = "success"
    writeback: Literal["processed", "resource_wait"] = "processed"
    expected: tuple[int, int, int, int, int] = (1, 1, 0, 0, 0)
    expected_archive: str | None = None


PARITY_CASES = (
    ParityCase(
        name="scan_invalid",
        payload={"event_type": "SCAN_COMPLETED", "data": {}},
        expected=(1, 0, 1, 0, 0),
    ),
    ParityCase(
        name="scan_valid",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "ABC123"}},
    ),
    ParityCase(
        name="estop_missing_workline",
        payload={"event_type": "ESTOP_PRESSED", "data": {}},
        workline_present=False,
        expected=(1, 0, 1, 0, 0),
    ),
    ParityCase(
        name="timer_timeout",
        kind="TIMER_TIMEOUT",
        payload={"event_type": "TIMER_TIMEOUT", "data": {}},
    ),
    ParityCase(
        name="missing_context",
        kind="EXTERNAL_HTTP",
        payload={"event_type": "EXTERNAL_CALLBACK", "data": {}},
        session_status=None,
        workline_present=False,
        expected=(1, 0, 1, 0, 0),
    ),
    ParityCase(
        name="duplicate_entry",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "DUP"}},
        session_status="WAITING_DEVICE_RESULT",
        awaiting_command="CMD-001",
        expected_archive="DUPLICATE_ENTRY_ARCHIVED",
    ),
    ParityCase(
        name="late_command_result",
        kind="COMMAND_RESULT",
        payload={"event_type": "COMMAND_RESULT", "data": {}},
        session_status="COMPLETED",
        command_status="COMPLETED",
        expected_archive="LATE_COMMAND_RESULT_ARCHIVED",
    ),
    ParityCase(
        name="orchestrator_exception",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "ERR"}},
        orchestration="exception",
        expected=(1, 0, 1, 0, 0),
    ),
    ParityCase(
        name="resource_wait",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "WAIT"}},
        writeback="resource_wait",
        expected=(1, 0, 0, 0, 1),
    ),
    ParityCase(
        name="orchestrator_failure",
        payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "FAIL"}},
        orchestration="failure",
        expected=(1, 0, 1, 0, 0),
    ),
)


class _FakeDb:
    def __init__(self) -> None:
        self.committed = 0
        self.rolled_back = 0

    async def refresh(self, value: object) -> None:
        _ = value

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rolled_back += 1


class _Repository:
    def __init__(self, inbox: object) -> None:
        self.inbox = inbox

    async def get_by_id(self, db: object, inbox_id: int) -> object:
        _ = db
        assert inbox_id == 1
        return self.inbox


class _TerminalRecorder:
    """同时适配 legacy 与 RuntimeInboxService 的终态方法。"""

    def __init__(self, inbox: object) -> None:
        self.repo = _Repository(inbox)
        self.actions: list[str] = []

    async def mark_as_processed(self, *args: object, **kwargs: object) -> object:
        self.actions.append("processed")
        return SimpleNamespace(id=1)

    async def mark_as_failed(self, *args: object, **kwargs: object) -> object:
        self.actions.append("failed")
        return SimpleNamespace(id=1)

    async def mark_as_dead_letter(self, *args: object, **kwargs: object) -> object:
        self.actions.append("dead_letter")
        return SimpleNamespace(id=1)

    async def park_for_retry(self, *args: object, **kwargs: object) -> object:
        self.actions.append("resource_wait")
        return SimpleNamespace(id=1)

    async def mark_processed(self, *args: object, **kwargs: object) -> bool:
        self.actions.append("processed")
        return True

    async def mark_failed(self, *args: object, **kwargs: object) -> bool:
        self.actions.append("resource_wait" if kwargs.get("retryable") else "failed")
        return True

    async def mark_dead_letter(self, *args: object, **kwargs: object) -> bool:
        self.actions.append("dead_letter")
        return True


def _build_entities(case: ParityCase) -> tuple[SimpleNamespace, object | None, object | None, object | None]:
    inbox = SimpleNamespace(
        id=1,
        kind=case.kind,
        payload_json=case.payload,
        source_message_id="msg-parity",
        trace_id="trace-parity",
        event_id="evt-parity",
        causation_id=None,
        workline_id=20 if case.workline_present else None,
        session_id=10 if case.session_status is not None else None,
        execution_session_id=10 if case.session_status is not None else None,
        device_id=None,
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
            current_wait_type=None,
            context_json={},
        )
    workline = SimpleNamespace(id=20, plugin_key="default") if case.workline_present else None
    command = None
    if case.command_status is not None:
        command = SimpleNamespace(id=99, command_code="CMD-001", status=case.command_status)
    return inbox, session, workline, command


def _as_tuple(result: dict[str, int]) -> tuple[int, int, int, int, int]:
    return (
        result["processed"],
        result["success"],
        result["failed"],
        result["skipped"],
        result["resource_wait"],
    )


async def _run_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    processor_kind: ProcessorKind,
    case: ParityCase,
) -> tuple[dict[str, int], list[str]]:
    inbox, session, workline, command = _build_entities(case)
    db = _FakeDb()
    terminal = _TerminalRecorder(inbox)
    archives: list[str] = []

    async def load_related(*args: object, **kwargs: object) -> dict[str, object]:
        _ = args, kwargs
        return {
            "session": session,
            "workline": workline,
            "device": None,
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
            loaded["command"],
            loaded["device"],
            loaded["devices_by_role"],
            loaded["services"],
            loaded["safety_checked"],
        )

    async def record_diagnostic(*args: object, **kwargs: object) -> None:
        _ = args, kwargs

    async def record_duplicate(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        archives.append("DUPLICATE_ENTRY_ARCHIVED")

    async def record_late(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        archives.append("LATE_COMMAND_RESULT_ARCHIVED")

    async def timer_handler(*args: object, **kwargs: object) -> None:
        _ = args, kwargs

    effect = (
        RuntimeIntentEffectResult.resource_retry()
        if case.writeback == "resource_wait"
        else RuntimeIntentEffectResult.processed()
    )

    class _WriteBack:
        async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
            _ = args, kwargs
            return effect

    class _Orchestrator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _ = args, kwargs

        async def process_inbox(self, *args: object, write_callback: object, **kwargs: object) -> OrchestratorResult:
            _ = args, kwargs
            if case.orchestration == "exception":
                raise RuntimeError("simulated orchestrator failure")
            if case.orchestration == "failure":
                return OrchestratorResult(success=False, error="simulated failure", error_code="BIZ_001")
            result = OrchestratorResult(success=True, intents=[])
            await write_callback(result)  # type: ignore[operator]
            return result

    if processor_kind == "legacy":
        inbox_service_module = __import__(
            "src.app.runtime.orchestration.services.inbox.inbox_service",
            fromlist=["inbox_service"],
        )
        monkeypatch.setattr(inbox_service_module, "inbox_service", terminal)
        monkeypatch.setattr(legacy_module, "_load_related_entities", load_related)
        monkeypatch.setattr(legacy_module, "_record_diagnostic", record_diagnostic)
        monkeypatch.setattr(legacy_module, "_record_duplicate_entry_archive_timeline", record_duplicate)
        monkeypatch.setattr(legacy_module, "_record_late_command_result_archive_timeline", record_late)
        monkeypatch.setattr(legacy_module, "OrchestratorService", _Orchestrator)
        monkeypatch.setattr(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_runtime_reconciliation_service.handle_timer_timeout",
            timer_handler,
        )
        result = await InboxBatchProcessor(write_back_service=_WriteBack())._process_claimed_message(
            db,
            WorklineInboxClaim(
                id=1,
                processor_token="token-parity",
                received_at=None,
                session_id=inbox.session_id,
                workline_id=inbox.workline_id,
                device_id=None,
                kind=case.kind,
                payload_json=case.payload or {},
                trace_id=inbox.trace_id,
            ),
        )
        return result, archives

    class _Delegate:
        async def process(self, *args: object, write_callback: object, **kwargs: object) -> OrchestratorResult:
            return await _Orchestrator().process_inbox(*args, write_callback=write_callback, **kwargs)

    monkeypatch.setattr(bridge_module, "_load_related_entities", load_related_tuple)
    monkeypatch.setattr(bridge_module, "_record_diagnostic", record_diagnostic)
    monkeypatch.setattr(bridge_module, "_record_duplicate_entry_archive_timeline", record_duplicate, raising=False)
    monkeypatch.setattr(bridge_module, "_record_late_command_result_archive_timeline", record_late, raising=False)
    monkeypatch.setattr(bridge_module, "_handle_timer_timeout", timer_handler)
    processor = RuntimeInboxProcessorBridge(
        processor_service=_Delegate(),  # type: ignore[arg-type]
        writeback_service=RuntimeInboxWriteBackService(write_back_service=_WriteBack(), inbox_service=terminal),
        inbox_service=terminal,  # type: ignore[arg-type]
        inbox_repository=_Repository(inbox),  # type: ignore[arg-type]
    )
    result = await processor.process_claimed(db, claim={"id": 1, "processor_token": "token-parity"})
    return result, archives


@pytest.mark.asyncio
@pytest.mark.parametrize("processor_kind", ("legacy", "three_stage"))
@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda case: case.name)
async def test_processor_characterization_parity(
    monkeypatch: pytest.MonkeyPatch,
    processor_kind: ProcessorKind,
    case: ParityCase,
) -> None:
    """同一 characterization table 必须约束 legacy 与 three-stage 两个入口。"""
    result, archives = await _run_case(monkeypatch, processor_kind=processor_kind, case=case)

    assert _as_tuple(result) == case.expected
    if case.expected_archive is not None:
        assert archives == [case.expected_archive]
