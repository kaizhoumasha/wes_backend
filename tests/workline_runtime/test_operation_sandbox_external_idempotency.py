"""Sandbox external callback 的 RuntimeInbox 幂等与时间合同。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from src.app.runtime.orchestration.models.session import SessionStatus
from src.app.runtime.orchestration.services.intent.operation_service import WorklineOperationService
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxAcceptResult
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus
from src.app.workline.models.workline import WorkLineRunMode


class _NestedTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Db:
    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction()


class _InboxRepository:
    def __init__(self) -> None:
        self.record: Any | None = None
        self.add_count = 0

    async def get_by_source_event_identity(self, _db: Any, **_identity: Any) -> Any | None:
        return self.record

    async def add_received(self, _db: Any, data: dict[str, Any]) -> Any:
        self.add_count += 1
        self.record = SimpleNamespace(id=81, **data)
        return self.record


def _build_service(*, inbox_repo: Any, lifecycle: Any | None = None) -> tuple[WorklineOperationService, Any]:
    outbox = SimpleNamespace(
        id=71,
        dispatch_key="external:smt:release-001:RACK_OPERATION:1",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        status=SystemOutboxStatus.SENT,
        sent_at=None,
        next_retry_at=None,
        last_error=None,
        session_id=53,
        workline_id=45,
        created_at=datetime(2026, 7, 6, 8, 0, 0, 456000),
        payload_json={"trace_id": "trace-001", "callback_type": "RACK_OPERATION"},
    )
    session = SimpleNamespace(
        id=53,
        workline_id=45,
        status=SessionStatus.WAITING_EXTERNAL,
        current_wait_type="RACK_OPERATION",
        trace_id="trace-001",
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    lifecycle = lifecycle or SimpleNamespace(record_callback_from_external_http=AsyncMock())
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        outbox_repo=cast("Any", SimpleNamespace(get_by_dispatch_key=AsyncMock(return_value=outbox))),
        session_repo=cast("Any", SimpleNamespace(get_by_id=AsyncMock(return_value=session))),
        workline_repo=cast("Any", SimpleNamespace(get_for_update=AsyncMock(return_value=workline))),
        rack_task_lifecycle_service=cast("Any", lifecycle),
    )
    return service, lifecycle


@pytest.mark.asyncio
async def test_default_external_callback_retry_is_stable_and_records_lifecycle_once() -> None:
    repo = _InboxRepository()
    service, lifecycle = _build_service(inbox_repo=repo)

    first = await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key="external:smt:release-001:RACK_OPERATION:1",
        callback_type="RACK_OPERATION",
        auto_commit=False,
    )
    second = await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key="external:smt:release-001:RACK_OPERATION:1",
        callback_type="RACK_OPERATION",
        auto_commit=False,
    )

    assert first is second
    assert repo.add_count == 1
    lifecycle.record_callback_from_external_http.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_accept_result_skips_external_lifecycle_side_effect() -> None:
    existing = SimpleNamespace(id=81)
    repo = SimpleNamespace(get_by_source_event_identity=AsyncMock(return_value=None))
    service, lifecycle = _build_service(inbox_repo=repo)
    service._accept_runtime_message = AsyncMock(  # type: ignore[method-assign]
        return_value=RuntimeInboxAcceptResult(record=existing, created=False)
    )

    result = await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key="external:smt:release-001:RACK_OPERATION:1",
        callback_type="RACK_OPERATION",
        auto_commit=False,
    )

    assert result is existing
    lifecycle.record_callback_from_external_http.assert_not_awaited()


@pytest.mark.asyncio
async def test_accept_runtime_message_preserves_received_at_milliseconds() -> None:
    repo = SimpleNamespace()
    service = WorklineOperationService(inbox_repo=cast("Any", repo))
    record = SimpleNamespace(id=81)
    service.runtime_inbox_service.accept_received = AsyncMock(
        return_value=RuntimeInboxAcceptResult(record=record, created=True)
    )
    received_at = datetime(2026, 7, 6, 8, 0, 0, 456000, tzinfo=UTC)

    result = await service._accept_runtime_message(
        _Db(),
        kind="EXTERNAL_HTTP",
        event_type="RACK_OPERATION",
        payload={"message_type": "EXTERNAL_HTTP"},
        source_event_id="event-001",
        received_at=received_at,
    )

    assert result.record is record
    kwargs = service.runtime_inbox_service.accept_received.await_args.kwargs
    assert kwargs["now_ms"] == 1783324800456


@pytest.mark.asyncio
async def test_external_callback_forwards_explicit_received_at_to_accept_helper() -> None:
    existing = SimpleNamespace(id=81)
    repo = SimpleNamespace()
    service, _lifecycle = _build_service(inbox_repo=repo)
    service._accept_runtime_message = AsyncMock(  # type: ignore[method-assign]
        return_value=RuntimeInboxAcceptResult(record=existing, created=True)
    )
    received_at = datetime(2026, 7, 6, 8, 0, 0, 456000, tzinfo=UTC)

    await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key="external:smt:release-001:RACK_OPERATION:1",
        callback_type="RACK_OPERATION",
        timestamp=received_at,
        auto_commit=False,
    )

    assert service._accept_runtime_message.await_args.kwargs["received_at"] is received_at


@pytest.mark.parametrize("operation", ["HOLD", "RESUME", "CANCEL"])
@pytest.mark.asyncio
async def test_manual_operation_uses_internal_event_kind(operation: str) -> None:
    """人工操作使用目标态 INTERNAL_EVENT，具体动作只由 event_type/payload 表达。"""

    session = SimpleNamespace(id=53, workline_id=45, status=SessionStatus.RUNNING, trace_id="trace-manual")
    record = SimpleNamespace(id=82)
    service = WorklineOperationService(
        session_repo=cast("Any", SimpleNamespace(get_by_id=AsyncMock(return_value=session))),
        workline_repo=cast(
            "Any",
            SimpleNamespace(get_for_update=AsyncMock(return_value=SimpleNamespace(id=45, is_active=True))),
        ),
    )
    service._accept_runtime_message = AsyncMock(  # type: ignore[method-assign]
        return_value=RuntimeInboxAcceptResult(record=record, created=True)
    )

    with patch(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl."
        "workline_runtime_reconciliation_service.assert_not_pending_reconciliation"
    ):
        result = await service.create_manual_operation(
            _Db(),
            session_id=53,
            operation=operation,
            operator_id="operator-a",
            reason="现场操作",
            auto_commit=False,
        )

    assert result is record
    kwargs = service._accept_runtime_message.await_args.kwargs
    assert kwargs["kind"] == "INTERNAL_EVENT"
    assert kwargs["event_type"] == f"MANUAL_{operation}"
    assert kwargs["payload"]["operation"] == operation
