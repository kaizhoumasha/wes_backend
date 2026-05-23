from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.handling.models import HandlingMoveStatus, HandlingOperationStatus, HandlingStepStatus
from src.app.handling.services import HandlingOperationLifecycleService


class FakeOperationRepository:
    def __init__(self, operation: Any) -> None:
        self.operation = operation

    async def get_by_operation_key(self, _db: Any, operation_key: str) -> Any | None:
        if self.operation.operation_key == operation_key:
            return self.operation
        return None


class FakeStepRepository:
    def __init__(self, steps: list[Any]) -> None:
        self.steps = steps

    async def get_by_dispatch_key(self, _db: Any, dispatch_key: str) -> Any | None:
        for step in self.steps:
            if step.dispatch_key == dispatch_key:
                return step
        return None

    async def list_by_operation_id(self, _db: Any, operation_id: int) -> list[Any]:
        return [step for step in self.steps if step.operation_id == operation_id]


class FakeMoveRepository:
    def __init__(self, moves: list[Any] | None = None) -> None:
        self.moves = moves or []

    async def get_by_id(self, _db: Any, id: int, **_kwargs: Any) -> Any | None:
        for move in self.moves:
            if move.id == id:
                return move
        return None


class FakeSessionRepository:
    def __init__(self, session: Any | None) -> None:
        self.session = session

    async def get_open_session_by_waiting_handling_operation_key(
        self,
        _db: Any,
        *,
        workline_id: int,
        operation_key: str,
    ) -> Any | None:
        if self.session is None:
            return None
        if self.session.workline_id != workline_id:
            return None
        if self.session.context_json.get("waiting_handling_operation_key") != operation_key:
            return None
        return self.session


@pytest.mark.asyncio
async def test_record_callback_updates_step_operation_and_waiting_session() -> None:
    operation = SimpleNamespace(
        id=700,
        operation_key="bin-operation:trace-001",
        operation_status=HandlingOperationStatus.REQUESTED,
        workline_id=45,
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    step = SimpleNamespace(
        id=701,
        operation_id=700,
        operation_key="bin-operation:trace-001",
        move_id=801,
        dispatch_key="handling:bin-operation:trace-001:move:1",
        step_status=HandlingStepStatus.REQUESTED,
        callback_json={},
        result_json={},
        error_code=None,
        error_message=None,
        started_at=None,
        completed_at=None,
    )
    move = SimpleNamespace(id=801, move_status=HandlingMoveStatus.REQUESTED)
    session = SimpleNamespace(
        id=301,
        workline_id=45,
        status="WAITING_EXTERNAL",
        current_wait_type="HANDLING_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=1800,
        awaiting_command_id=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        ended_at=None,
        context_json={
            "waiting_handling_operation_key": "bin-operation:trace-001",
            "handling_operation": {"operation_key": "bin-operation:trace-001", "status": "PENDING"},
        },
    )
    db = SimpleNamespace(add=lambda _obj: None)
    service = HandlingOperationLifecycleService(
        operation_repository=FakeOperationRepository(operation),
        move_repository=FakeMoveRepository([move]),
        step_repository=FakeStepRepository([step]),
        session_repository=FakeSessionRepository(session),
    )

    result = await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": "CTU_BIN_MOVE_COMPLETED",
            "dispatch_key": "handling:bin-operation:trace-001:move:1",
            "status": "SUCCEEDED",
        },
        trace_id="trace-bin-001",
    )

    assert result is step
    assert step.step_status == HandlingStepStatus.SUCCEEDED
    assert move.move_status == HandlingMoveStatus.SUCCEEDED
    assert step.callback_json["callback_type"] == "CTU_BIN_MOVE_COMPLETED"
    assert operation.operation_status == HandlingOperationStatus.SUCCEEDED
    assert session.status == "RUNNING"
    assert session.current_wait_type is None
    assert session.context_json["waiting_handling_operation_key"] is None
    assert session.context_json["handling_operation"]["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_record_callback_marks_failed_operation_manual_hold() -> None:
    operation = SimpleNamespace(
        id=700,
        operation_key="bin-operation:trace-001",
        operation_status=HandlingOperationStatus.REQUESTED,
        workline_id=45,
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    step = SimpleNamespace(
        id=701,
        operation_id=700,
        operation_key="bin-operation:trace-001",
        move_id=801,
        dispatch_key="handling:bin-operation:trace-001:move:1",
        step_status=HandlingStepStatus.REQUESTED,
        callback_json={},
        result_json={},
        error_code=None,
        error_message=None,
        started_at=None,
        completed_at=None,
    )
    move = SimpleNamespace(id=801, move_status=HandlingMoveStatus.REQUESTED)
    session = SimpleNamespace(
        id=301,
        workline_id=45,
        status="WAITING_EXTERNAL",
        current_wait_type="HANDLING_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=1800,
        awaiting_command_id=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        ended_at=None,
        context_json={
            "waiting_handling_operation_key": "bin-operation:trace-001",
            "handling_operation": {"operation_key": "bin-operation:trace-001", "status": "PENDING"},
        },
    )
    db = SimpleNamespace(add=lambda _obj: None)
    service = HandlingOperationLifecycleService(
        operation_repository=FakeOperationRepository(operation),
        move_repository=FakeMoveRepository([move]),
        step_repository=FakeStepRepository([step]),
        session_repository=FakeSessionRepository(session),
    )

    result = await service.record_callback_from_external_http(
        db,
        payload_json={
            "callback_type": "CTU_BIN_MOVE_FAILED",
            "dispatch_key": "handling:bin-operation:trace-001:move:1",
            "status": "FAILED",
            "reason_code": "CTU_REJECTED",
            "reason_message": "CTU 拒绝搬运",
        },
        trace_id="trace-bin-001",
    )

    assert result is step
    assert move.move_status == HandlingMoveStatus.FAILED
    assert operation.operation_status == HandlingOperationStatus.FAILED
    assert session.status == "MANUAL_HOLD"
    assert session.failure_domain == "EXTERNAL"
    assert session.failure_code == "CTU_REJECTED"
    assert session.failure_message == "CTU 拒绝搬运"


@pytest.mark.asyncio
async def test_full_box_exchange_business_completed_can_resume_by_exchange_request_code() -> None:
    operation = SimpleNamespace(
        id=700,
        operation_key="full-box:release-001",
        operation_status=HandlingOperationStatus.REQUESTED,
        workline_id=45,
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    step = SimpleNamespace(
        id=701,
        operation_id=700,
        operation_key="full-box:release-001",
        move_id=801,
        dispatch_key="handling:full-box:release-001:move:1",
        step_status=HandlingStepStatus.REQUESTED,
        callback_json={},
        result_json={},
        error_code=None,
        error_message=None,
        started_at=None,
        completed_at=None,
    )
    move = SimpleNamespace(id=801, move_status=HandlingMoveStatus.REQUESTED)
    session = SimpleNamespace(
        id=301,
        workline_id=45,
        status="WAITING_EXTERNAL",
        current_wait_type="HANDLING_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=1800,
        awaiting_command_id=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        ended_at=None,
        context_json={
            "waiting_handling_operation_key": "full-box:release-001",
            "handling_operation": {"operation_key": "full-box:release-001", "status": "IN_PROGRESS"},
        },
    )
    service = HandlingOperationLifecycleService(
        operation_repository=FakeOperationRepository(operation),
        move_repository=FakeMoveRepository([move]),
        step_repository=FakeStepRepository([step]),
        session_repository=FakeSessionRepository(session),
    )

    result = await service.record_callback_from_external_http(
        SimpleNamespace(add=lambda _obj: None),
        payload_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "exchange_request_code": "handling:full-box:release-001:move:1",
            "exchange_status": "BUSINESS_COMPLETED",
            "wms_rcs_task_id": "RCS-TASK-001",
        },
        trace_id="trace-full-box-001",
    )

    assert result is step
    assert step.step_status == HandlingStepStatus.SUCCEEDED
    assert move.move_status == HandlingMoveStatus.SUCCEEDED
    assert operation.operation_status == HandlingOperationStatus.SUCCEEDED
    assert session.status == "RUNNING"
    assert session.context_json["waiting_handling_operation_key"] is None


@pytest.mark.asyncio
async def test_full_box_exchange_physical_completed_without_relations_enters_reconciling() -> None:
    operation = SimpleNamespace(
        id=700,
        operation_key="full-box:release-002",
        operation_status=HandlingOperationStatus.REQUESTED,
        workline_id=45,
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    step = SimpleNamespace(
        id=701,
        operation_id=700,
        operation_key="full-box:release-002",
        move_id=801,
        dispatch_key="handling:full-box:release-002:move:1",
        step_status=HandlingStepStatus.REQUESTED,
        callback_json={},
        result_json={},
        error_code=None,
        error_message=None,
        started_at=None,
        completed_at=None,
    )
    move = SimpleNamespace(id=801, move_status=HandlingMoveStatus.REQUESTED)
    session = SimpleNamespace(
        id=301,
        workline_id=45,
        status="WAITING_EXTERNAL",
        current_wait_type="HANDLING_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=1800,
        awaiting_command_id=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        ended_at=None,
        context_json={
            "waiting_handling_operation_key": "full-box:release-002",
            "handling_operation": {"operation_key": "full-box:release-002", "status": "IN_PROGRESS"},
        },
    )
    service = HandlingOperationLifecycleService(
        operation_repository=FakeOperationRepository(operation),
        move_repository=FakeMoveRepository([move]),
        step_repository=FakeStepRepository([step]),
        session_repository=FakeSessionRepository(session),
    )

    result = await service.record_callback_from_external_http(
        SimpleNamespace(add=lambda _obj: None),
        payload_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "dispatch_key": "handling:full-box:release-002:move:1",
            "exchange_request_code": "handling:full-box:release-002:move:1",
            "exchange_status": "PHYSICAL_COMPLETED",
            "wms_rcs_task_id": "RCS-TASK-002",
        },
        trace_id="trace-full-box-002",
    )

    assert result is step
    assert step.step_status == HandlingStepStatus.RECONCILING
    assert move.move_status == HandlingMoveStatus.RECONCILING
    assert step.error_code == "POST_EXCHANGE_RELATIONS_MISSING"
    assert operation.operation_status == HandlingOperationStatus.RECONCILING
    assert session.status == "MANUAL_HOLD"
    assert session.failure_code == "POST_EXCHANGE_RELATIONS_MISSING"


@pytest.mark.asyncio
async def test_reconciling_full_box_exchange_can_later_complete_business() -> None:
    operation = SimpleNamespace(
        id=700,
        operation_key="full-box:release-003",
        operation_status=HandlingOperationStatus.RECONCILING,
        workline_id=45,
        error_code="POST_EXCHANGE_RELATIONS_MISSING",
        error_message="missing relations",
        completed_at=None,
    )
    step = SimpleNamespace(
        id=701,
        operation_id=700,
        operation_key="full-box:release-003",
        move_id=801,
        dispatch_key="handling:full-box:release-003:move:1",
        step_status=HandlingStepStatus.RECONCILING,
        callback_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "exchange_status": "PHYSICAL_COMPLETED",
            "source_version": "1",
        },
        result_json={},
        error_code="POST_EXCHANGE_RELATIONS_MISSING",
        error_message="missing relations",
        started_at=None,
        completed_at=None,
    )
    move = SimpleNamespace(id=801, move_status=HandlingMoveStatus.RECONCILING)
    session = SimpleNamespace(
        id=301,
        workline_id=45,
        status="MANUAL_HOLD",
        current_wait_type=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
        awaiting_command_id=None,
        failure_domain="EXTERNAL",
        failure_code="POST_EXCHANGE_RELATIONS_MISSING",
        failure_message="missing relations",
        ended_at=None,
        context_json={
            "waiting_handling_operation_key": "full-box:release-003",
            "handling_operation": {
                "operation_key": "full-box:release-003",
                "status": "RECONCILING",
                "rack_release_id": "rack-release-003",
            },
        },
    )
    service = HandlingOperationLifecycleService(
        operation_repository=FakeOperationRepository(operation),
        move_repository=FakeMoveRepository([move]),
        step_repository=FakeStepRepository([step]),
        session_repository=FakeSessionRepository(session),
    )

    result = await service.record_callback_from_external_http(
        SimpleNamespace(add=lambda _obj: None),
        payload_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "dispatch_key": "handling:full-box:release-003:move:1",
            "exchange_request_code": "handling:full-box:release-003:move:1",
            "rack_release_id": "rack-release-003",
            "exchange_status": "BUSINESS_COMPLETED",
            "source_version": "2",
            "wms_rcs_task_id": "RCS-TASK-003",
        },
        trace_id="trace-full-box-003",
    )

    assert result is step
    assert step.step_status == HandlingStepStatus.SUCCEEDED
    assert move.move_status == HandlingMoveStatus.SUCCEEDED
    assert operation.operation_status == HandlingOperationStatus.SUCCEEDED
    assert session.status == "RUNNING"
    assert session.failure_code is None


@pytest.mark.asyncio
async def test_full_box_exchange_rack_release_mismatch_enters_manual_hold() -> None:
    operation = SimpleNamespace(
        id=700,
        operation_key="full-box:release-004",
        operation_status=HandlingOperationStatus.REQUESTED,
        workline_id=45,
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    step = SimpleNamespace(
        id=701,
        operation_id=700,
        operation_key="full-box:release-004",
        move_id=801,
        dispatch_key="handling:full-box:release-004:move:1",
        step_status=HandlingStepStatus.REQUESTED,
        callback_json={},
        result_json={},
        error_code=None,
        error_message=None,
        started_at=None,
        completed_at=None,
    )
    move = SimpleNamespace(id=801, move_status=HandlingMoveStatus.REQUESTED)
    session = SimpleNamespace(
        id=301,
        workline_id=45,
        status="WAITING_EXTERNAL",
        current_wait_type="HANDLING_OPERATION",
        waiting_since=object(),
        deadline_at=object(),
        current_wait_timeout_seconds=1800,
        awaiting_command_id=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        ended_at=None,
        context_json={
            "waiting_handling_operation_key": "full-box:release-004",
            "handling_operation": {
                "operation_key": "full-box:release-004",
                "status": "PENDING",
                "rack_release_id": "rack-release-expected",
            },
        },
    )
    service = HandlingOperationLifecycleService(
        operation_repository=FakeOperationRepository(operation),
        move_repository=FakeMoveRepository([move]),
        step_repository=FakeStepRepository([step]),
        session_repository=FakeSessionRepository(session),
    )

    result = await service.record_callback_from_external_http(
        SimpleNamespace(add=lambda _obj: None),
        payload_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "dispatch_key": "handling:full-box:release-004:move:1",
            "exchange_request_code": "handling:full-box:release-004:move:1",
            "rack_release_id": "rack-release-wrong",
            "exchange_status": "BUSINESS_COMPLETED",
            "source_version": "1",
            "wms_rcs_task_id": "RCS-TASK-004",
        },
        trace_id="trace-full-box-004",
    )

    assert result is step
    assert step.step_status == HandlingStepStatus.RECONCILING
    assert move.move_status == HandlingMoveStatus.RECONCILING
    assert session.status == "MANUAL_HOLD"
    assert session.failure_code == "RACK_RELEASE_ID_MISMATCH"


@pytest.mark.asyncio
async def test_late_terminal_callback_does_not_pollute_succeeded_operation() -> None:
    operation = SimpleNamespace(
        id=700,
        operation_key="bin-operation:trace-late-terminal",
        operation_status=HandlingOperationStatus.SUCCEEDED,
        workline_id=45,
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    step = SimpleNamespace(
        id=701,
        operation_id=700,
        operation_key="bin-operation:trace-late-terminal",
        move_id=801,
        dispatch_key="handling:bin-operation:trace-late-terminal:move:1",
        step_status=HandlingStepStatus.SUCCEEDED,
        callback_json={"callback_type": "CTU_BIN_MOVE_COMPLETED", "source_version": "2"},
        result_json={"step_status": "SUCCEEDED"},
        error_code=None,
        error_message=None,
        started_at=None,
        completed_at=None,
    )
    move = SimpleNamespace(id=801, move_status=HandlingMoveStatus.SUCCEEDED)
    service = HandlingOperationLifecycleService(
        operation_repository=FakeOperationRepository(operation),
        move_repository=FakeMoveRepository([move]),
        step_repository=FakeStepRepository([step]),
        session_repository=FakeSessionRepository(None),
    )

    result = await service.record_callback_from_external_http(
        SimpleNamespace(add=lambda _obj: None),
        payload_json={
            "callback_type": "CTU_BIN_MOVE_FAILED",
            "dispatch_key": "handling:bin-operation:trace-late-terminal:move:1",
            "status": "FAILED",
            "reason_code": "CTU_REJECTED",
            "reason_message": "迟到失败回调",
            "source_version": "3",
        },
        trace_id="trace-late-terminal",
    )

    assert result is step
    assert step.step_status == HandlingStepStatus.SUCCEEDED
    assert move.move_status == HandlingMoveStatus.SUCCEEDED
    assert operation.operation_status == HandlingOperationStatus.SUCCEEDED
    assert operation.error_code is None
    assert operation.error_message is None


@pytest.mark.asyncio
async def test_stale_full_box_exchange_source_version_is_ignored() -> None:
    operation = SimpleNamespace(
        id=700,
        operation_key="full-box:release-005",
        operation_status=HandlingOperationStatus.IN_PROGRESS,
        workline_id=45,
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    step = SimpleNamespace(
        id=701,
        operation_id=700,
        operation_key="full-box:release-005",
        move_id=801,
        dispatch_key="handling:full-box:release-005:move:1",
        step_status=HandlingStepStatus.IN_PROGRESS,
        callback_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "exchange_status": "IN_PROGRESS",
            "source_version": "3",
        },
        result_json={},
        error_code=None,
        error_message=None,
        started_at=None,
        completed_at=None,
    )
    move = SimpleNamespace(id=801, move_status=HandlingMoveStatus.IN_PROGRESS)
    service = HandlingOperationLifecycleService(
        operation_repository=FakeOperationRepository(operation),
        move_repository=FakeMoveRepository([move]),
        step_repository=FakeStepRepository([step]),
        session_repository=FakeSessionRepository(None),
    )

    result = await service.record_callback_from_external_http(
        SimpleNamespace(add=lambda _obj: None),
        payload_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "dispatch_key": "handling:full-box:release-005:move:1",
            "exchange_request_code": "handling:full-box:release-005:move:1",
            "rack_release_id": "rack-release-005",
            "exchange_status": "BUSINESS_COMPLETED",
            "source_version": "2",
            "wms_rcs_task_id": "RCS-TASK-005",
        },
        trace_id="trace-full-box-005",
    )

    assert result is step
    assert step.step_status == HandlingStepStatus.IN_PROGRESS
    assert move.move_status == HandlingMoveStatus.IN_PROGRESS
    assert operation.operation_status == HandlingOperationStatus.IN_PROGRESS
