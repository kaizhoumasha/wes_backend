from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.app.execution.models import InboundEvidenceApplyStatus as Status
from src.app.execution.services import InboundEvidenceAcceptance, InboundEvidenceConflictResult
from src.app.wms_adapter.outbound_picking.cancel_wire import (
    PickingTaskCancelEvent,
    parse_picking_task_cancel_receipt,
)
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.services.picking_task_cancel import PickingTaskCancelService

NOW = datetime(2026, 9, 17)
OPERATION = "outbound.picking_task.cancel@v1"
OPERATION_ID = "019f33f0-58d7-7b4d-a23a-1b90aa5d4473"


class Sessions:
    @asynccontextmanager
    async def begin(self):
        yield object()


def event(*, scope: str = "TASK") -> PickingTaskCancelEvent:
    data: dict[str, object] = {"task_id": "PICK-1", "cancel_scope": scope}
    if scope == "PLAN_MEMBERS":
        data["direct_pick_sources"] = [{"rack_id": "R-1", "rack_face": "A", "slot_ids": ["S-1"]}]
    return PickingTaskCancelEvent.model_validate(
        {
            "operation": OPERATION,
            "operation_id": OPERATION_ID,
            "timestamp": 1,
            "data": data,
        }
    )


def setup_service(
    *,
    task_status: PickingTaskStatus = PickingTaskStatus.QUEUED,
    plan_revision: int = 0,
    evidence_status: Status = Status.PENDING,
    duplicate: bool = False,
):
    receipt = event()
    evidence = SimpleNamespace(
        id=10,
        received_at=NOW,
        apply_status=evidence_status,
        normalized_payload=receipt.model_dump(mode="json", exclude_none=True),
        source_identity=f"{OPERATION}:{OPERATION_ID}",
        picking_task_id=None,
        processed_at=None,
    )
    task = SimpleNamespace(
        id=1,
        status=task_status,
        last_applied_plan_revision=plan_revision,
        increment_version=Mock(),
    )
    evidences = SimpleNamespace(
        accept=AsyncMock(return_value=InboundEvidenceAcceptance(evidence=evidence, duplicate=duplicate)),
        record_conflict=AsyncMock(),
    )
    tasks = SimpleNamespace(
        lock_task_identity=AsyncMock(),
        get_by_task_id_for_update=AsyncMock(return_value=task),
        flush=AsyncMock(),
    )
    cancellations = SimpleNamespace(
        cancel_members=AsyncMock(return_value=(True, ("TRANSPORT-1",))),
        first_rejection=AsyncMock(return_value="STATE_CONFLICT"),
    )
    transport = SimpleNamespace(finalize_unsent_task_in_session=AsyncMock(return_value=True))
    service = PickingTaskCancelService(
        Sessions(),
        evidence_service=evidences,
        task_repository=tasks,
        cancel_repository=cancellations,
        transport_service=transport,
    )
    return service, task, evidence, evidences, tasks, cancellations, transport


async def test_task_cancel_applies_within_the_evidence_transaction() -> None:
    service, task, evidence, _, tasks, _, _ = setup_service()

    result = await service.record(event(), received_at=NOW)

    assert result.code == "RECEIVED"
    assert task.status is PickingTaskStatus.CANCELLED
    task.increment_version.assert_called_once_with()
    assert evidence.picking_task_id == 1
    assert evidence.apply_status is Status.APPLIED
    tasks.flush.assert_awaited_once()


@pytest.mark.parametrize(
    ("status", "plan_revision"),
    [(PickingTaskStatus.EXECUTING, 0), (PickingTaskStatus.QUEUED, 1)],
)
async def test_task_cancel_rejects_disallowed_state_or_existing_plan(status, plan_revision) -> None:
    service, task, evidence, evidences, _, _, _ = setup_service(task_status=status, plan_revision=plan_revision)

    result = await service.record(event(), received_at=NOW)

    assert (result.code, result.reason_code) == ("CONFLICT", "STATE_CONFLICT")
    assert task.status is status
    task.increment_version.assert_not_called()
    assert evidence.apply_status is Status.RECONCILING
    evidences.record_conflict.assert_awaited_once()


@pytest.mark.parametrize("task_status", tuple(PickingTaskStatus))
async def test_member_cancel_marks_all_matches_in_any_task_state_and_finalizes_each_unsent_transport(
    task_status: PickingTaskStatus,
) -> None:
    service, task, evidence, _, _, cancellations, transport = setup_service(task_status=task_status)
    cancellations.cancel_members.return_value = (True, ("TRANSPORT-1", "TRANSPORT-2"))

    result = await service.record(event(scope="PLAN_MEMBERS"), received_at=NOW)

    assert result.code == "RECEIVED"
    task.increment_version.assert_called_once_with()
    assert evidence.apply_status is Status.APPLIED
    assert [call.args[1] for call in transport.finalize_unsent_task_in_session.await_args_list] == [
        "TRANSPORT-1",
        "TRANSPORT-2",
    ]
    assert all(
        call.kwargs == {"reason_code": "TRANSPORT_WITHDRAWN_BEFORE_SEND"}
        for call in transport.finalize_unsent_task_in_session.await_args_list
    )


async def test_member_cancel_skips_selectors_that_do_not_match() -> None:
    service, task, evidence, evidences, _, cancellations, transport = setup_service(
        task_status=PickingTaskStatus.EXECUTING
    )
    cancellations.cancel_members.return_value = (False, ())

    result = await service.record(event(scope="PLAN_MEMBERS"), received_at=NOW)

    assert (result.code, result.reason_code) == ("RECEIVED", None)
    task.increment_version.assert_not_called()
    transport.finalize_unsent_task_in_session.assert_not_awaited()
    assert evidence.apply_status is Status.APPLIED
    evidences.record_conflict.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "expected_code", "expected_reason"),
    [(Status.APPLIED, "DUPLICATE", None), (Status.RECONCILING, "CONFLICT", "STATE_CONFLICT")],
)
async def test_duplicate_cancel_replays_the_first_terminal_result(status, expected_code, expected_reason) -> None:
    service, _, _, _, tasks, cancellations, _ = setup_service(evidence_status=status, duplicate=True)

    result = await service.record(event(), received_at=NOW)

    assert (result.code, result.reason_code) == (expected_code, expected_reason)
    tasks.lock_task_identity.assert_not_awaited()
    if status is Status.RECONCILING:
        cancellations.first_rejection.assert_awaited_once()
        assert cancellations.first_rejection.await_args.args[1] == 10


async def test_invalid_data_is_reliably_recorded_before_rejection() -> None:
    service, _, evidence, _, tasks, _, _ = setup_service(evidence_status=Status.IGNORED)
    invalid = parse_picking_task_cancel_receipt(
        {
            "operation": OPERATION,
            "operation_id": OPERATION_ID,
            "timestamp": 1,
            "data": {"cancel_scope": "TASK"},
        }
    )

    result = await service.record(invalid, received_at=NOW)

    assert (result.code, result.reason_code) == ("REJECTED", "INVALID_DATA")
    assert evidence.processed_at == evidence.received_at
    tasks.lock_task_identity.assert_not_awaited()


async def test_same_identity_with_different_payload_returns_idempotency_conflict() -> None:
    service, _, evidence, evidences, tasks, _, _ = setup_service()
    evidences.accept.return_value = InboundEvidenceConflictResult(
        evidence=evidence,
        conflict=SimpleNamespace(),
        source_identity=evidence.source_identity,
    )

    result = await service.record(event(), received_at=NOW)

    assert (result.code, result.reason_code) == ("CONFLICT", "IDEMPOTENCY_CONFLICT")
    tasks.lock_task_identity.assert_not_awaited()
