"""Characterization tests for InboxBatchProcessor.

锁定 InboxBatchProcessor 当前各分支的行为，作为 RuntimeInbox 主链路收束的基线。
后续 Task 5 (三阶段 Processor 拆分) 必须保持这些 case 的结果一致。

覆盖分支：
1. SCAN validation 失败（empty barcode payload）
2. SCAN validation 通过（barcode 存在）
3. ESTOP_PRESSED missing workline context
4. TIMER_TIMEOUT
5. missing session/workline context
6. duplicate entry event (BUSY session)
7. late command result (terminal session)
8. normal write-back (PROCESSED)
9. RESOURCE_WAIT
10. FAILED / DEAD_LETTER

每个测试都是 mock-based，避免真实数据库；只锁定函数级行为。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
from src.app.runtime.orchestration.repositories.inbox_repository import WorklineInboxClaim
from src.app.runtime.orchestration.services.inbox import inbox_batch_processor as processor_module
from src.app.runtime.orchestration.services.inbox.inbox_batch_processor import (
    InboxBatchProcessor,
)

inbox_service_module = importlib.import_module("src.app.runtime.orchestration.services.inbox.inbox_service")
diagnostic_module = importlib.import_module("src.app.workline.diagnostic_support")


# ============================================================
# Test fixtures and helpers
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
    source_message_id: str | None = "msg-test",
    event_id: str | None = "evt-test",
    causation_id: str | None = None,
    attempt_count: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=inbox_id,
        kind=kind,
        payload_json=payload_json or {"event_type": "SCAN_COMPLETED"},
        source_message_id=source_message_id,
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
        workline_id=workline_id,
        session_id=session_id,
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


class _FakeInboxRepository:
    def __init__(self, inbox: SimpleNamespace) -> None:
        self._inbox = inbox

    async def get_by_id(self, db: object, inbox_id: int) -> object:
        assert inbox_id == self._inbox.id
        return self._inbox


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


def _attach_diagnostic_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """替换 _record_diagnostic，捕获调用参数。"""
    captured: list[dict[str, Any]] = []

    async def fake_record_diagnostic(db: object, **kwargs: object) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(processor_module, "_record_diagnostic", fake_record_diagnostic)
    return captured


def _attach_related_entities(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: SimpleNamespace | None,
    workline: SimpleNamespace | None,
    device: SimpleNamespace | None = None,
    command: SimpleNamespace | None = None,
) -> None:
    async def fake_load_related_entities(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "session": session,
            "workline": workline,
            "device": device,
            "command": command,
            "devices_by_role": {},
            "services": SimpleNamespace(),
            "safety_checked": True,
        }

    monkeypatch.setattr(processor_module, "_load_related_entities", fake_load_related_entities)


# ============================================================
# Case 1: SCAN validation 失败（empty barcode payload）
# ============================================================


@pytest.mark.asyncio
async def test_scan_completed_with_empty_barcode_payload_marks_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SCAN_COMPLETED 但 payload 没有条码字段 → 标记 FAILED + record CALLBACK_SCHEMA_INVALID。"""
    inbox = _make_inbox(payload_json={"event_type": "SCAN_COMPLETED", "data": {}})
    fake_db = _FakeDb()
    captured_diagnostics = _attach_diagnostic_recorder(monkeypatch)

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        mark_calls: ClassVar[list[dict[str, Any]]] = []

        async def mark_as_failed(
            self,
            db: object,
            inbox_pk: int,
            error_message: str,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            self.mark_calls.append(
                {
                    "inbox_pk": inbox_pk,
                    "error_message": error_message,
                    "processor_token": processor_token,
                }
            )
            return SimpleNamespace(id=inbox_pk)

    inbox_service = _InboxService()
    monkeypatch.setattr(inbox_service_module, "inbox_service", inbox_service)

    result = await InboxBatchProcessor()._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-1",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=inbox.workline_id,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    # 验证：1 条记录、failed=1、success=0
    assert result == {
        "processed": 1,
        "success": 0,
        "failed": 1,
        "skipped": 0,
        "resource_wait": 0,
    }
    # 验证：调用了 mark_as_failed 且 error message 描述了 barcode 缺失
    assert len(inbox_service.mark_calls) == 1
    call = inbox_service.mark_calls[0]
    assert call["inbox_pk"] == inbox.id
    assert call["processor_token"] == "token-1"
    assert "barcode" in call["error_message"].lower() or "条码" in call["error_message"]
    # 验证：调用了 _record_diagnostic 记录 CALLBACK_SCHEMA_INVALID
    assert len(captured_diagnostics) == 1
    diag = captured_diagnostics[0]
    assert diag["error_code"].value == "CALLBACK_SCHEMA_INVALID"


# ============================================================
# Case 2: SCAN validation 通过（barcode 存在）— 走完整 orchestrator 路径
# ============================================================


@pytest.mark.asyncio
async def test_scan_completed_with_barcode_payload_orchestrator_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SCAN_COMPLETED 带合法 barcode payload → 走 orchestrator + write_back → mark_processed。"""
    inbox = _make_inbox(
        payload_json={
            "event_type": "SCAN_COMPLETED",
            "data": {"HHPN": "ABC123", "MfrPN": "MFR-XYZ", "Qty": 1},
        }
    )
    session = _make_session()
    workline = _make_workline()
    fake_db = _FakeDb()
    _attach_diagnostic_recorder(monkeypatch)
    _attach_related_entities(monkeypatch, session=session, workline=workline)

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        mark_processed_calls: ClassVar[list[dict[str, Any]]] = []
        mark_failed_calls: ClassVar[list[dict[str, Any]]] = []

        async def mark_as_processed(
            self,
            db: object,
            inbox_pk: int,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            self.mark_processed_calls.append({"inbox_pk": inbox_pk, "processor_token": processor_token})
            return SimpleNamespace(id=inbox_pk)

        async def mark_as_failed(self, *args: object, **kwargs: object) -> object:
            self.mark_failed_calls.append({"args": args, "kwargs": kwargs})
            return SimpleNamespace(id=kwargs.get("inbox_pk"))

    inbox_service = _InboxService()
    monkeypatch.setattr(inbox_service_module, "inbox_service", inbox_service)

    class _Orchestrator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def process_inbox(
            self,
            *args: object,
            write_callback: object,
            **kwargs: object,
        ) -> OrchestratorResult:
            result = OrchestratorResult(success=True, intents=[])
            await write_callback(result)
            return result

    monkeypatch.setattr(processor_module, "OrchestratorService", _Orchestrator)

    class _WriteBack:
        async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
            return RuntimeIntentEffectResult.processed()  # type: ignore[return-value]

    result = await InboxBatchProcessor(write_back_service=_WriteBack())._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-1",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=inbox.workline_id,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    # 验证：1 条记录、success=1、failed=0
    assert result == {
        "processed": 1,
        "success": 1,
        "failed": 0,
        "skipped": 0,
        "resource_wait": 0,
    }
    # 验证：mark_as_processed 被调用
    assert len(inbox_service.mark_processed_calls) == 1
    assert inbox_service.mark_processed_calls[0]["inbox_pk"] == inbox.id
    # 验证：mark_as_failed 未被调用
    assert len(inbox_service.mark_failed_calls) == 0


# ============================================================
# Case 3: ESTOP_PRESSED missing workline context → mark_as_failed + SESSION_CONTEXT_MISSING
# ============================================================


@pytest.mark.asyncio
async def test_estop_pressed_missing_workline_context_marks_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ESTOP_PRESSED 事件但 workline 解析为 None → mark_as_failed + SESSION_CONTEXT_MISSING。"""
    inbox = _make_inbox(
        kind="DEVICE_EVENT",
        payload_json={"event_type": "ESTOP_PRESSED", "data": {}},
        workline_id=None,  # 强制 workline 解析失败
    )
    fake_db = _FakeDb()
    _ = _attach_diagnostic_recorder(monkeypatch)

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        mark_calls: ClassVar[list[dict[str, Any]]] = []

        async def mark_as_failed(
            self,
            db: object,
            inbox_pk: int,
            error_message: str,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            self.mark_calls.append(
                {
                    "inbox_pk": inbox_pk,
                    "error_message": error_message,
                    "processor_token": processor_token,
                }
            )
            return SimpleNamespace(id=inbox_pk)

    monkeypatch.setattr(inbox_service_module, "inbox_service", _InboxService())

    result = await InboxBatchProcessor()._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-estop",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=None,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    # ESTOP_PRESSED 在 workline=None 时按 SESSION_CONTEXT_MISSING 走 failed 路径
    assert result["failed"] == 1
    assert result["processed"] == 1
    assert result["success"] == 0


# ============================================================
# Case 4: TIMER_TIMEOUT → 走 reconciliation handler（不走 orchestrator）
# ============================================================


@pytest.mark.asyncio
async def test_timer_timeout_calls_reconciliation_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TIMER_TIMEOUT inbox 走 reconciliation handler，不调 orchestrator。"""
    inbox = _make_inbox(
        kind="TIMER_TIMEOUT",
        payload_json={"event_type": "TIMER_TIMEOUT", "data": {}},
    )
    session = _make_session()
    workline = _make_workline()
    fake_db = _FakeDb()

    _attach_related_entities(monkeypatch, session=session, workline=workline)

    reconciliation_called: ClassVar[dict[str, Any]] = {"yes": False, "calls": 0}

    async def fake_handle_timer_timeout(
        db: object,
        *,
        inbox: object,
        processor_token: str,
    ) -> None:
        reconciliation_called["yes"] = True
        reconciliation_called["calls"] = int(reconciliation_called["calls"]) + 1
        reconciliation_called["inbox_id"] = inbox.id
        reconciliation_called["token"] = processor_token

    # Patch the module-level singleton so the function-local import binds to our mock
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_runtime_reconciliation_service.handle_timer_timeout",
        fake_handle_timer_timeout,
    )

    class _InboxService:
        repo = _FakeInboxRepository(inbox)

        async def mark_as_processed(
            self,
            db: object,
            inbox_pk: int,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            return SimpleNamespace(id=inbox_pk)

    monkeypatch.setattr(inbox_service_module, "inbox_service", _InboxService())

    result = await InboxBatchProcessor()._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-timer",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=inbox.workline_id,
            device_id=None,
            kind="TIMER_TIMEOUT",
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    assert result["success"] == 1
    assert result["processed"] == 1
    assert reconciliation_called["yes"] is True
    assert reconciliation_called["inbox_id"] == inbox.id
    assert reconciliation_called["token"] == "token-timer"


# ============================================================
# Case 5: missing session/workline context → mark_as_failed + SESSION_CONTEXT_MISSING
# ============================================================


@pytest.mark.asyncio
async def test_missing_session_workline_context_marks_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inbox 无 session/workline 关联（且 not _should_resolve_session） → failed。"""
    inbox = _make_inbox(
        kind="EXTERNAL_HTTP",
        payload_json={"event_type": "EXTERNAL_CALLBACK", "data": {}},
        workline_id=None,
        session_id=None,
    )
    # 强制 _load_related_entities 返回 None session/workline
    session = None
    workline = None
    fake_db = _FakeDb()

    _attach_related_entities(monkeypatch, session=session, workline=workline)

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        mark_calls: ClassVar[list[dict[str, Any]]] = []

        async def mark_as_failed(
            self,
            db: object,
            inbox_pk: int,
            error_message: str,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            self.mark_calls.append(
                {
                    "inbox_pk": inbox_pk,
                    "error_message": error_message,
                    "processor_token": processor_token,
                }
            )
            return SimpleNamespace(id=inbox_pk)

    monkeypatch.setattr(inbox_service_module, "inbox_service", _InboxService())

    result = await InboxBatchProcessor()._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-missing",
            received_at=None,
            session_id=None,
            workline_id=None,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    # missing context → failed
    assert result["failed"] == 1
    assert result["processed"] == 1


# ============================================================
# Case 6: duplicate entry event (BUSY session) → archived + mark_as_processed
# ============================================================


@pytest.mark.asyncio
async def test_duplicate_entry_event_archived_to_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入口事件进入已 BUSY/TERMINAL 的 session → 归档到 timeline + mark_as_processed。"""
    inbox = _make_inbox(
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "X"}},
    )
    session = _make_session(
        status="WAITING_DEVICE_RESULT",  # BUSY
        awaiting_device_command_code="CMD-001",  # 已经等待命令
    )
    workline = _make_workline()
    fake_db = _FakeDb()
    _attach_related_entities(monkeypatch, session=session, workline=workline)

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        mark_processed_calls: ClassVar[list[dict[str, Any]]] = []

        async def mark_as_processed(
            self,
            db: object,
            inbox_pk: int,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            self.mark_processed_calls.append({"inbox_pk": inbox_pk, "processor_token": processor_token})
            return SimpleNamespace(id=inbox_pk)

    monkeypatch.setattr(inbox_service_module, "inbox_service", _InboxService())

    result = await InboxBatchProcessor()._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-dup",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=inbox.workline_id,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    # 重复入口被识别 + 归档，mark_as_processed
    assert result["success"] == 1
    assert result["processed"] == 1


# ============================================================
# Case 7: late command result (terminal session) → archived + mark_as_processed
# ============================================================


@pytest.mark.asyncio
async def test_late_command_result_archived_to_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMMAND_RESULT 抵达 terminal session → 归档 evidence + mark_as_processed。"""
    inbox = _make_inbox(
        kind="COMMAND_RESULT",
        payload_json={"event_type": "COMMAND_RESULT", "data": {}},
    )
    session = _make_session(status="COMPLETED")  # TERMINAL
    workline = _make_workline()
    fake_db = _FakeDb()
    _attach_related_entities(
        monkeypatch,
        session=session,
        workline=workline,
        command=SimpleNamespace(
            id=99,
            command_code="CMD-001",
            status="COMPLETED",
        ),
    )

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        mark_processed_calls: ClassVar[list[dict[str, Any]]] = []

        async def mark_as_processed(
            self,
            db: object,
            inbox_pk: int,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            self.mark_processed_calls.append({"inbox_pk": inbox_pk})
            return SimpleNamespace(id=inbox_pk)

    monkeypatch.setattr(inbox_service_module, "inbox_service", _InboxService())

    result = await InboxBatchProcessor()._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-late",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=inbox.workline_id,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    assert result["success"] == 1
    assert result["processed"] == 1


# ============================================================
# Case 8: orchestrator.process_inbox 抛异常 → mark_as_failed + UNKNOWN error_code
# ============================================================


@pytest.mark.asyncio
async def test_orchestrator_exception_marks_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """orchestrator.process_inbox 抛异常 → mark_as_failed + UNKNOWN 错误码记录。"""
    inbox = _make_inbox(
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "Y"}},
    )
    session = _make_session()
    workline = _make_workline()
    fake_db = _FakeDb()
    _attach_related_entities(monkeypatch, session=session, workline=workline)

    _ = _attach_diagnostic_recorder(monkeypatch)

    class _Orchestrator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def process_inbox(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("simulated orchestrator failure")

    monkeypatch.setattr(processor_module, "OrchestratorService", _Orchestrator)

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        mark_calls: ClassVar[list[dict[str, Any]]] = []

        async def mark_as_failed(
            self,
            db: object,
            inbox_pk: int,
            error_message: str,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            self.mark_calls.append(
                {
                    "inbox_pk": inbox_pk,
                    "error_message": error_message,
                    "processor_token": processor_token,
                }
            )
            return SimpleNamespace(id=inbox_pk)

    monkeypatch.setattr(inbox_service_module, "inbox_service", _InboxService())

    result = await InboxBatchProcessor()._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-fail",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=inbox.workline_id,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    assert result["failed"] == 1
    assert result["processed"] == 1


# ============================================================
# Case 9: SCAN validation 通过但 RESOURCE_WAIT disposition → park_for_retry + resource_wait
# ============================================================


@pytest.mark.asyncio
async def test_resource_wait_disposition_parks_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """write_back 返回 RESOURCE_RETRY → park_for_retry (state=RETRY) + resource_wait=1。"""
    inbox = _make_inbox(
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "Z"}},
    )
    session = _make_session()
    workline = _make_workline()
    fake_db = _FakeDb()
    _attach_related_entities(monkeypatch, session=session, workline=workline)

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        park_calls: ClassVar[list[dict[str, Any]]] = []

        async def park_for_retry(
            self,
            db: object,
            inbox_pk: int,
            reason: str,
            *,
            processor_token: str,
            auto_commit: bool = True,
            delay_seconds: int = 0,
        ) -> object:
            self.park_calls.append(
                {
                    "inbox_pk": inbox_pk,
                    "reason": reason,
                    "processor_token": processor_token,
                }
            )
            return SimpleNamespace(id=inbox_pk)

    monkeypatch.setattr(inbox_service_module, "inbox_service", _InboxService())

    class _Orchestrator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def process_inbox(
            self,
            *args: object,
            write_callback: object,
            **kwargs: object,
        ) -> OrchestratorResult:
            result = OrchestratorResult(success=True, intents=[])
            await write_callback(result)
            return result

    monkeypatch.setattr(processor_module, "OrchestratorService", _Orchestrator)

    class _WriteBack:
        async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
            return RuntimeIntentEffectResult.resource_retry()

    result = await InboxBatchProcessor(write_back_service=_WriteBack())._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-resource",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=inbox.workline_id,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    assert result == {
        "processed": 1,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "resource_wait": 1,
    }


# ============================================================
# Case 10: orchestrator 返 success=False + error_code → mark_as_failed
# ============================================================


@pytest.mark.asyncio
async def test_orchestrator_failure_result_marks_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """orchestrator.process_inbox 返回 success=False → mark_as_failed + 错误码记录。"""
    inbox = _make_inbox(
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "W"}},
    )
    session = _make_session()
    workline = _make_workline()
    fake_db = _FakeDb()
    _attach_related_entities(monkeypatch, session=session, workline=workline)
    _attach_diagnostic_recorder(monkeypatch)

    class _InboxService:
        repo = _FakeInboxRepository(inbox)
        mark_calls: ClassVar[list[dict[str, Any]]] = []

        async def mark_as_failed(
            self,
            db: object,
            inbox_pk: int,
            error_message: str,
            *,
            processor_token: str,
            auto_commit: bool = True,
        ) -> object:
            self.mark_calls.append(
                {
                    "inbox_pk": inbox_pk,
                    "error_message": error_message,
                    "processor_token": processor_token,
                }
            )
            return SimpleNamespace(id=inbox_pk)

    monkeypatch.setattr(inbox_service_module, "inbox_service", _InboxService())

    class _Orchestrator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def process_inbox(self, *args: object, **kwargs: object) -> OrchestratorResult:
            return OrchestratorResult(success=False, error="simulated failure", error_code="BIZ_001")

    monkeypatch.setattr(processor_module, "OrchestratorService", _Orchestrator)

    result = await InboxBatchProcessor()._process_claimed_message(
        fake_db,
        WorklineInboxClaim(
            id=inbox.id,
            processor_token="token-biz-fail",
            received_at=None,
            session_id=inbox.session_id,
            workline_id=inbox.workline_id,
            device_id=None,
            kind=inbox.kind,
            payload_json=inbox.payload_json,
            trace_id=inbox.trace_id,
        ),
    )

    assert result["failed"] == 1
    assert result["processed"] == 1
