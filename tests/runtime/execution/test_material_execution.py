"""MaterialExecution 只拥有通用生命周期与活动 trace 不变量。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.app.execution.models.material_execution import (
    InvalidMaterialExecutionTransitionError,
    MaterialExecution,
    MaterialExecutionStatus,
)
from src.app.execution.services.material_execution_service import (
    ActiveMaterialExecutionExistsError,
    InitialExecutionCorrelationConflictError,
    MaterialExecutionFifoBlockedError,
    MaterialExecutionService,
)


class FakeMaterialExecutionRepository:
    def __init__(self) -> None:
        self.executions: list[MaterialExecution] = []

    async def lock_material_trace(self, _db: object, material_trace_id: str) -> None:
        return None

    async def get_active_by_trace_for_update(
        self,
        _db: object,
        material_trace_id: str,
    ) -> MaterialExecution | None:
        return next(
            (
                execution
                for execution in self.executions
                if execution.material_trace_id == material_trace_id
                and execution.status != MaterialExecutionStatus.CLOSED
            ),
            None,
        )

    async def add(self, _db: object, execution: MaterialExecution) -> MaterialExecution:
        execution.id = len(self.executions) + 1
        self.executions.append(execution)
        return execution

    async def get_admission_head_for_update(
        self,
        _db: object,
        *,
        workline_id: int,
        line_run_epoch_id: int,
    ) -> MaterialExecution | None:
        candidates = [
            execution
            for execution in self.executions
            if execution.workline_id == workline_id
            and execution.line_run_epoch_id == line_run_epoch_id
            and execution.status != MaterialExecutionStatus.CLOSED
        ]
        return min(
            candidates,
            key=lambda execution: (
                execution.admission_received_at,
                execution.admission_evidence_id,
                execution.id,
            ),
            default=None,
        )

    async def flush(self, _db: object) -> None:
        return None


def _service() -> tuple[MaterialExecutionService, FakeMaterialExecutionRepository]:
    repository = FakeMaterialExecutionRepository()
    return MaterialExecutionService(repository=repository), repository


async def _create(service: MaterialExecutionService, *, execution_code: str = "EXEC-001") -> MaterialExecution:
    return await service.create(
        object(),
        execution_code=execution_code,
        material_trace_id="TRACE-MATERIAL-001",
        workline_id=1,
        line_run_epoch_id=11,
        changed_at=datetime(2026, 8, 16),
        reason_code="SCAN_ACCEPTED",
        evidence_id=101,
    )


@pytest.mark.asyncio
async def test_create_freezes_common_identity_without_plugin_state() -> None:
    service, _ = _service()

    execution = await _create(service)

    assert execution.status == MaterialExecutionStatus.CREATED
    assert execution.version == 0
    assert execution.last_transition_reason == "SCAN_ACCEPTED"
    assert execution.last_transition_evidence_id == 101
    assert execution.admission_received_at == datetime(2026, 8, 16)
    assert execution.admission_evidence_id == 101
    assert "plugin_state" not in MaterialExecution.model_fields
    assert {status.value for status in MaterialExecutionStatus} == {
        "CREATED",
        "RUNNING",
        "HOLD",
        "CLOSED",
        "RECONCILING",
    }


@pytest.mark.asyncio
async def test_fifo_head_cannot_be_skipped_by_later_active_material() -> None:
    service, _ = _service()
    head = await _create(service, execution_code="EXEC-HEAD")
    later = await service.create(
        object(),
        execution_code="EXEC-LATER",
        material_trace_id="TRACE-MATERIAL-002",
        workline_id=1,
        line_run_epoch_id=11,
        changed_at=datetime(2026, 8, 16, 0, 1),
        reason_code="SCAN_ACCEPTED",
        evidence_id=102,
    )

    with pytest.raises(MaterialExecutionFifoBlockedError, match="EXEC-HEAD"):
        await service.assert_fifo_head(object(), later)

    assert await service.assert_fifo_head(object(), head) is head


@pytest.mark.asyncio
async def test_one_trace_rejects_a_second_active_execution_but_closed_trace_can_start_again() -> None:
    service, _ = _service()
    first = await _create(service)

    with pytest.raises(ActiveMaterialExecutionExistsError):
        await _create(service, execution_code="EXEC-002")

    await service.transition(
        object(),
        first,
        target=MaterialExecutionStatus.CLOSED,
        changed_at=datetime(2026, 8, 16, 0, 1),
        reason_code="WMS_RECORDED",
        evidence_id=102,
    )
    second = await _create(service, execution_code="EXEC-002")

    assert second.id != first.id


@pytest.mark.asyncio
async def test_initial_evidence_correlation_reuses_only_the_same_frozen_execution_identity() -> None:
    service, _ = _service()
    kwargs = {
        "execution_code": "EXEC-INITIAL",
        "material_trace_id": "TRACE-INITIAL",
        "workline_id": 1,
        "line_run_epoch_id": 11,
        "changed_at": datetime(2026, 8, 17),
        "evidence_id": 101,
    }

    first = await service.create_or_get_for_initial_evidence(object(), **kwargs)
    duplicate = await service.create_or_get_for_initial_evidence(object(), **kwargs)

    assert duplicate is first
    with pytest.raises(InitialExecutionCorrelationConflictError):
        await service.create_or_get_for_initial_evidence(
            object(),
            **{**kwargs, "execution_code": "EXEC-CHANGED"},
        )


@pytest.mark.asyncio
async def test_lifecycle_allows_only_approved_edges_and_same_state_is_idempotent() -> None:
    service, _ = _service()
    execution = await _create(service)

    unchanged = await service.transition(
        object(),
        execution,
        target=MaterialExecutionStatus.CREATED,
        changed_at=datetime(2026, 8, 16, 0, 1),
        reason_code="DUPLICATE_EVENT",
        evidence_id=102,
    )
    assert unchanged is execution
    assert execution.last_transition_evidence_id == 101

    for ordinal, target in enumerate(
        (
            MaterialExecutionStatus.RUNNING,
            MaterialExecutionStatus.HOLD,
            MaterialExecutionStatus.RECONCILING,
            MaterialExecutionStatus.RUNNING,
            MaterialExecutionStatus.CLOSED,
        ),
        start=2,
    ):
        await service.transition(
            object(),
            execution,
            target=target,
            changed_at=datetime(2026, 8, 16, 0, ordinal),
            reason_code=f"EVIDENCE_{ordinal}",
            evidence_id=100 + ordinal,
        )

    assert execution.status == MaterialExecutionStatus.CLOSED
    assert execution.closed_at == datetime(2026, 8, 16, 0, 6)
    with pytest.raises(InvalidMaterialExecutionTransitionError):
        await service.transition(
            object(),
            execution,
            target=MaterialExecutionStatus.RUNNING,
            changed_at=datetime(2026, 8, 16, 0, 7),
            reason_code="ILLEGAL_REOPEN",
            evidence_id=107,
        )


@pytest.mark.asyncio
async def test_transition_requires_explicit_reason_and_persisted_evidence_reference() -> None:
    service, _ = _service()
    execution = await _create(service)

    with pytest.raises(ValueError, match="reason"):
        await service.transition(
            object(),
            execution,
            target=MaterialExecutionStatus.HOLD,
            changed_at=datetime(2026, 8, 16, 0, 1),
            reason_code=" ",
            evidence_id=102,
        )
    with pytest.raises(ValueError, match="evidence"):
        await service.transition(
            object(),
            execution,
            target=MaterialExecutionStatus.HOLD,
            changed_at=datetime(2026, 8, 16, 0, 1),
            reason_code="WMS_WAIT",
            evidence_id=0,
        )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("CREATED", "RUNNING"),
        ("CREATED", "HOLD"),
        ("CREATED", "RECONCILING"),
        ("CREATED", "CLOSED"),
        ("RUNNING", "HOLD"),
        ("RUNNING", "RECONCILING"),
        ("RUNNING", "CLOSED"),
        ("HOLD", "RUNNING"),
        ("HOLD", "RECONCILING"),
        ("HOLD", "CLOSED"),
        ("RECONCILING", "RUNNING"),
        ("RECONCILING", "HOLD"),
        ("RECONCILING", "CLOSED"),
    ],
)
def test_all_approved_transition_edges_are_available(current: str, target: str) -> None:
    execution = MaterialExecution(
        execution_code=f"EXEC-{current}-{target}",
        material_trace_id=f"TRACE-{current}-{target}",
        workline_id=1,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus(current),
        last_transition_reason="SETUP",
        last_transition_evidence_id=101,
        status_changed_at=datetime(2026, 8, 16),
    )

    execution.transition_to(
        MaterialExecutionStatus(target),
        changed_at=datetime(2026, 8, 16, 0, 1),
        reason_code="APPROVED_EDGE",
        evidence_id=102,
    )

    assert execution.status == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("RUNNING", "CREATED"),
        ("HOLD", "CREATED"),
        ("RECONCILING", "CREATED"),
        ("CLOSED", "CREATED"),
        ("CLOSED", "RUNNING"),
        ("CLOSED", "HOLD"),
        ("CLOSED", "RECONCILING"),
    ],
)
def test_unapproved_transition_edges_are_rejected(current: str, target: str) -> None:
    execution = MaterialExecution(
        execution_code=f"EXEC-{current}-{target}",
        material_trace_id=f"TRACE-{current}-{target}",
        workline_id=1,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus(current),
        last_transition_reason="SETUP",
        last_transition_evidence_id=101,
        status_changed_at=datetime(2026, 8, 16),
    )

    with pytest.raises(InvalidMaterialExecutionTransitionError):
        execution.transition_to(
            MaterialExecutionStatus(target),
            changed_at=datetime(2026, 8, 16, 0, 1),
            reason_code="UNAPPROVED_EDGE",
            evidence_id=102,
        )
