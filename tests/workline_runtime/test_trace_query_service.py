"""TraceQueryService 测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.services.trace_query_service import TraceQueryService


class _ResultStub:
    def __init__(self, *, scalar: object | None = None, rows: list[object] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> object | None:
        return self._scalar

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self._rows)


def _db_with_execute_results(*results: _ResultStub) -> SimpleNamespace:
    return SimpleNamespace(execute=AsyncMock(side_effect=list(results)))


@pytest.fixture
def callback_log_1() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        request_id="req-1",
        correlation_id="corr-1",
        callback_type="result",
        ingress_outcome="ACCEPTED",
        failure_stage=None,
        response_status=200,
        response_time_ms=12,
    )


@pytest.fixture
def callback_log_2() -> SimpleNamespace:
    return SimpleNamespace(
        id=2,
        request_id="req-2",
        correlation_id="corr-1",
        callback_type="event",
        ingress_outcome="ACCEPTED",
        failure_stage=None,
        response_status=200,
        response_time_ms=18,
    )


@pytest.fixture
def session_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=11,
        correlation_id="corr-1",
        workline_id=22,
        status="RUNNING",
        current_wait_type="COMMAND_RESULT",
        awaiting_command_id=33,
        plugin_key="smt_classifier",
        contract_version="1.0",
        last_request_id="req-1",
    )


@pytest.fixture
def command_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=33,
        command_code="CMD-1",
        correlation_id="corr-1",
        session_id="11",
        device_id=77,
        workline_id=22,
        status="COMPLETED",
        result="SUCCESS",
        task_type="PICK",
    )


@pytest.fixture
def outbox_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=44,
        session_id=11,
        workline_id=22,
        dispatch_key="dispatch-1",
        dispatch_type="DEVICE_COMMAND",
        target_code="ARM-01",
        status="SENT",
        created_at=1,
    )


@pytest.fixture
def inbox_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=55,
        session_id=11,
        workline_id=22,
        correlation_id="corr-1",
        source_message_id="req-1",
        status="PROCESSED",
        received_at=1,
        kind="DEVICE_EVENT",
        attempt_count=0,
    )


@pytest.fixture
def timeline_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=66,
        session_id=11,
        workline_id=22,
        correlation_id="corr-1",
        seq_no=1,
        occurred_at=1,
        stage="DECISION",
        action_type="DECISION_MADE",
        actor_type="PLUGIN",
        actor_code="smt_classifier",
        status="SUCCESS",
        payload_json={
            "request_id": "req-1",
            "correlation_id": "corr-1",
            "canonical_event_type": "SCAN_COMPLETED",
        },
    )


@pytest.fixture
def service(callback_log_1, callback_log_2, session_obj, command_obj) -> TraceQueryService:
    callback_repo = SimpleNamespace(
        get_by_request_id=AsyncMock(return_value=callback_log_1),
        get_by_correlation_id=AsyncMock(return_value=[callback_log_1, callback_log_2]),
    )
    session_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=session_obj),
        get_by_correlation_id=AsyncMock(return_value=session_obj),
    )
    command_repo = SimpleNamespace(
        get_by_command_code=AsyncMock(return_value=command_obj),
    )
    return TraceQueryService(
        callback_log_repo=callback_repo,
        session_repo=session_repo,
        command_repo=command_repo,
    )


@pytest.mark.asyncio
async def test_by_request_id_aggregates_full_chain(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_request_id(db, "req-1")

    assert result.trace.request_id == "req-1"
    assert result.trace.correlation_id == "corr-1"
    assert result.session is not None and result.session.id == session_obj.id
    assert result.commands and result.commands[0].command_code == "CMD-1"
    assert result.outboxes and result.outboxes[0].dispatch_key == "dispatch-1"
    assert result.inboxes and result.inboxes[0].source_message_id == "req-1"
    assert result.timelines and result.timelines[0].seq_no == 1
    assert result.summary["callback_logs"] == 2
    assert result.summary["timelines"] == 1
    assert any(d.extra.get("source") == "session_snapshot" for d in result.diagnostics)
    assert any(d.extra.get("source") == "timeline" for d in result.diagnostics)
    assert db.execute.await_count == 4


@pytest.mark.asyncio
async def test_by_correlation_id_uses_correlation_anchor(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_correlation_id(db, "corr-1")

    assert result.trace.correlation_id == "corr-1"
    assert result.trace.request_id == "req-1"
    assert result.session is not None and result.session.id == session_obj.id
    assert result.summary["callback_logs"] == 2
    assert any(d.extra.get("source") == "outbox" for d in result.diagnostics)
    assert db.execute.await_count == 4


@pytest.mark.asyncio
async def test_by_session_id_uses_session_anchor(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_session_id(db, 11)

    assert result.trace.session_id == 11
    assert result.trace.correlation_id == "corr-1"
    assert result.session is not None and result.session.id == session_obj.id
    assert result.commands and result.commands[0].command_code == "CMD-1"
    assert any(d.extra.get("source") == "session_snapshot" for d in result.diagnostics)
    assert db.execute.await_count == 4


@pytest.mark.asyncio
async def test_by_command_code_uses_command_anchor(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_command_code(db, "CMD-1")

    assert result.trace.command_code == "CMD-1"
    assert result.trace.session_id == 11
    assert result.session is not None and result.session.id == session_obj.id
    assert any(command.command_code == "CMD-1" for command in result.commands)
    assert db.execute.await_count == 4


@pytest.mark.asyncio
async def test_by_dispatch_key_uses_outbox_anchor(
    callback_log_1: SimpleNamespace,
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    service.callback_log_repo.get_by_correlation_id = AsyncMock(return_value=[callback_log_1])
    db = _db_with_execute_results(
        _ResultStub(scalar=outbox_obj),
        _ResultStub(rows=[]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_dispatch_key(db, "dispatch-1")

    assert result.trace.dispatch_key == "dispatch-1"
    assert result.trace.session_id == 11
    assert result.outboxes and result.outboxes[0].dispatch_key == "dispatch-1"
    assert any(d.extra.get("source") == "outbox" for d in result.diagnostics)
    assert db.execute.await_count == 5
