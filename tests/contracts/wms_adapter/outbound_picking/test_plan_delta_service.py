from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTask, PickingTaskBinSourceRack


def test_plan_members_have_exact_business_identity_and_revision_zero_default():
    task = PickingTask(
        task_id="T", task_type="MANUAL", queue_revision=1, dispatch_sequence=1, issued_at_ms=1, issued_evidence_id=1
    )
    assert task.last_applied_plan_revision == 0
    assert task.plan_blocked_evidence_id is None
    for model in (DirectPickExecution, PickingTaskBinSourceRack):
        assert {"picking_task_id", "plan_revision", "source_evidence_id"} <= set(model.__table__.c.keys())


from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.app.execution.models import InboundEvidenceApplyStatus as Status
from src.app.execution.models import InboundEvidenceKind, WmsConfirmationStatus
from src.app.execution.services import InboundEvidenceAcceptance
from src.app.wms_adapter.outbound_picking.plan_delta_wire import PickingTaskPlanDeltaEvent
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PickingTaskPlanDeltaService

NOW = datetime(2026, 9, 6)
OP = "outbound.picking_task.plan_delta@v1"


class Sessions:
    @asynccontextmanager
    async def begin(self):
        yield object()


def event(revision=1, **data):
    return PickingTaskPlanDeltaEvent.model_validate(
        {
            "operation": OP,
            "operation_id": "019c6e27-e55b-73d1-87d8-4e01f1f75043",
            "timestamp": 1,
            "data": {
                "task_id": "T",
                "plan_revision": revision,
                **(
                    {"target_rack": {"rack_id": "R", "rack_face": " A "}}
                    if revision == 1
                    else {"added_bin_source_racks": [{"rack_id": "B", "rack_face": "A"}]}
                ),
                **data,
            },
        }
    )


def setup_service(status=Status.PENDING):
    task = PickingTask(
        id=1,
        task_id="T",
        task_type="MANUAL",
        status="PREPARING",
        workline_id=2,
        line_run_epoch_id=3,
        queue_revision=1,
        dispatch_sequence=1,
        issued_at_ms=1,
        issued_evidence_id=1,
    )
    receipt = event()
    evidence = SimpleNamespace(
        id=10,
        received_at=NOW,
        apply_status=status,
        normalized_payload=receipt.model_dump(mode="json", exclude_none=True),
        source_identity=f"{OP}:{receipt.operation_id}",
    )
    response = SimpleNamespace(
        kind=InboundEvidenceKind.WMS_RESULT,
        operation=PICKING_TASK_PREPARE_OPERATION,
        operation_id="prepare-id",
        normalized_payload={"operation_id": "prepare-id", "code": "PREPARE_ACCEPTED", "data": {}},
    )
    confirmation = SimpleNamespace(
        status=WmsConfirmationStatus.COMPLETED,
        response_result="PREPARE_ACCEPTED",
        response_evidence_id=20,
        operation_id="prepare-id",
        request_payload={
            "operation": PICKING_TASK_PREPARE_OPERATION,
            "operation_id": "prepare-id",
            "data": {"task_id": "T", "workline_code": "L"},
        },
        deadline_at=NOW + timedelta(seconds=5),
    )
    tasks = SimpleNamespace(get_by_task_id_for_update=AsyncMock(return_value=task))
    plans = SimpleNamespace(
        prepare_context=AsyncMock(
            return_value=(
                [confirmation],
                SimpleNamespace(line_code="L"),
                SimpleNamespace(workline_id=2, status="ACTIVE"),
            )
        ),
        get_evidence=AsyncMock(return_value=response),
        source_identities=AsyncMock(return_value=(set(), set())),
        add_members=AsyncMock(),
        first_rejection=AsyncMock(return_value="REVISION_CONFLICT"),
    )
    evidences = SimpleNamespace(
        accept=AsyncMock(return_value=InboundEvidenceAcceptance(evidence=evidence, duplicate=False)),
        record_conflict=AsyncMock(),
    )
    return (
        PickingTaskPlanDeltaService(
            Sessions(), evidence_service=evidences, task_repository=tasks, plan_repository=plans
        ),
        task,
        evidence,
        confirmation,
    )


async def test_revision_one_commits_plan_and_evidence_together():
    service, task, evidence, _ = setup_service()
    result = await service.record(event(), received_at=NOW)
    assert result.code == "RECEIVED"
    assert (
        task.status,
        task.last_applied_plan_revision,
        task.initial_plan_evidence_id,
        task.last_plan_evidence_id,
    ) == ("EXECUTING", 1, 10, 10)
    assert task.target_rack_face == " A "
    assert evidence.apply_status == Status.APPLIED


async def test_pending_prepare_is_rechecked_by_same_identity():
    service, task, evidence, confirmation = setup_service()
    confirmation.status = WmsConfirmationStatus.DISPATCHING
    assert (await service.record(event(), received_at=NOW)).code == "UNAVAILABLE"
    assert task.last_applied_plan_revision == 0
    assert task.plan_blocked_evidence_id is None
    service._plans.add_members.assert_not_awaited()
    confirmation.status = WmsConfirmationStatus.COMPLETED
    assert (await service.record(event(), received_at=NOW)).code == "RECEIVED"
    assert evidence.apply_status == Status.APPLIED


@pytest.mark.parametrize("status", ["QUEUED", "EXECUTION_COMPLETED"])
async def test_disallowed_state_keeps_terminal_task_unblocked(status):
    service, task, evidence, _ = setup_service()
    task.status = status
    result = await service.record(event(), received_at=NOW)
    assert result.reason_code == "STATE_CONFLICT"
    assert evidence.apply_status == Status.RECONCILING
    assert task.plan_blocked_evidence_id == (None if status == "EXECUTION_COMPLETED" else 10)


async def test_success_replay_precedes_later_blocker_and_terminal_state():
    service, task, _, _ = setup_service(Status.APPLIED)
    task.status = "EXECUTION_COMPLETED"
    task.plan_blocked_evidence_id = 99
    assert (await service.record(event(), received_at=NOW)).code == "DUPLICATE"
    service._tasks.get_by_task_id_for_update.assert_not_awaited()


async def test_first_rejection_is_stable_even_after_state_changes():
    service, _, _, _ = setup_service(Status.RECONCILING)
    assert (await service.record(event(), received_at=NOW)).reason_code == "REVISION_CONFLICT"
    service._tasks.get_by_task_id_for_update.assert_not_awaited()


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("revision", "REVISION_CONFLICT"),
        ("binding", "REFERENCE_CONFLICT"),
        ("response", "STATE_CONFLICT"),
        ("expired", "STATE_CONFLICT"),
        ("blocked", "STATE_CONFLICT"),
    ],
)
async def test_conflicts_do_not_write_members(mutation, reason):
    service, task, _, confirmation = setup_service()
    receipt = event()
    if mutation == "revision":
        receipt = event(2)
    elif mutation == "binding":
        confirmation.request_payload["data"]["task_id"] = "OTHER"
    elif mutation == "response":
        confirmation.response_result = "REJECTED"
    elif mutation == "expired":
        confirmation.status = WmsConfirmationStatus.PENDING
        confirmation.deadline_at = NOW
    else:
        task.plan_blocked_evidence_id = 99
    result = await service.record(receipt, received_at=NOW)
    assert result.reason_code == reason
    service._plans.add_members.assert_not_awaited()
    assert task.plan_blocked_evidence_id == (99 if mutation == "blocked" else 10)


@pytest.mark.parametrize("task_type", ["MANUAL", "AUTO"])
async def test_sources_are_append_only_and_task_type_neutral(task_type):
    service, task, evidence, _ = setup_service()
    task.task_type = task_type
    receipt = event(
        added_direct_picks=[
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "SRC", "rack_face": " A ", "slot_id": "1"}}
        ]
    )
    evidence.normalized_payload = receipt.model_dump(mode="json", exclude_none=True)
    assert (await service.record(receipt, received_at=NOW)).code == "RECEIVED"
    service._plans.source_identities.return_value = ({("SRC", " A ", "1")}, set())
    next_data = event(2, added_direct_picks=receipt.model_dump(mode="json")["data"]["added_direct_picks"]).data
    assert await service.validate_plan(object(), task, next_data, received_at=NOW) == "REFERENCE_CONFLICT"
    assert task.initial_plan_evidence_id == task.last_plan_evidence_id == 10


async def test_business_duplicate_uses_full_data_and_preserves_array_order():
    service, task, _, _ = setup_service()
    task.status = "EXECUTING"
    task.last_applied_plan_revision = 2
    task.last_plan_evidence_id = 30
    racks = [{"rack_id": "A", "rack_face": "1"}, {"rack_id": "B", "rack_face": "2"}]
    previous = event(2, added_bin_source_racks=racks)
    service._plans.get_evidence.return_value = SimpleNamespace(
        normalized_payload=previous.model_dump(mode="json", exclude_none=True)
    )
    assert await service.validate_plan(object(), task, previous.data, received_at=NOW) == "DUPLICATE"
    changed = event(2, added_bin_source_racks=list(reversed(racks)))
    assert await service.validate_plan(object(), task, changed.data, received_at=NOW) == "REVISION_CONFLICT"
    task.plan_blocked_evidence_id = 99
    assert await service.validate_plan(object(), task, previous.data, received_at=NOW) == "STATE_CONFLICT"


async def test_unknown_task_does_not_create_or_block_another_task():
    service, task, _, _ = setup_service()
    service._tasks.get_by_task_id_for_update.return_value = None
    assert (await service.record(event(), received_at=NOW)).reason_code == "REFERENCE_CONFLICT"
    assert task.plan_blocked_evidence_id is None


async def test_closed_epoch_cannot_admit_new_revision():
    service, _, _, _ = setup_service()
    service._plans.prepare_context.return_value[2].status = "CLOSED"
    assert (await service.record(event(), received_at=NOW)).reason_code == "STATE_CONFLICT"


async def test_durable_accepted_prepare_is_not_reclassified_after_deadline():
    service, _, _, confirmation = setup_service()
    confirmation.deadline_at = NOW - timedelta(seconds=1)
    assert (await service.record(event(), received_at=NOW)).code == "RECEIVED"


async def test_source_lookup_and_insert_use_bounded_candidate_batches():
    from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
        MEMBER_BATCH_SIZE,
        PickingTaskPlanDeltaRepository,
    )

    repository = PickingTaskPlanDeltaRepository()
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=list)), flush=AsyncMock(), add=Mock())
    picks = [(f"P{i}", "A", "S") for i in range(MEMBER_BATCH_SIZE + 1)]
    racks = [(f"B{i}", "A") for i in range(MEMBER_BATCH_SIZE + 1)]
    assert await repository.source_identities(db, 1, direct_picks=picks, bin_racks=racks) == (set(), set())
    assert db.execute.await_count == 4
    for call in db.execute.await_args_list:
        query = call.args[0].compile()
        candidates = [value for value in query.params.values() if isinstance(value, list)]
        assert len(candidates) == 1 and 1 <= len(candidates[0]) <= MEMBER_BATCH_SIZE
    db.execute.reset_mock()
    assert await repository.source_identities(db, 1, direct_picks=[], bin_racks=[]) == (set(), set())
    db.execute.assert_not_awaited()
    data = event(added_bin_source_racks=[{"rack_id": rack, "rack_face": face} for rack, face in racks]).data
    batch_sizes = []

    async def flush():
        batch_sizes.append(db.add.call_count)
        db.add.reset_mock()

    db.flush.side_effect = flush
    await repository.add_members(db, 1, data, 10)
    assert sum(batch_sizes) == len(racks)
    assert max(batch_sizes) <= MEMBER_BATCH_SIZE


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
async def test_plan_source_indexes_emit_plain_columns_on_both_dialects(dialect):
    from sqlalchemy import create_mock_engine
    from sqlalchemy.schema import CreateIndex

    statements = []
    engine = create_mock_engine(f"{dialect}://", lambda sql, *args, **kwargs: statements.append(sql))
    for model in (DirectPickExecution, PickingTaskBinSourceRack):
        model.__table__.create(engine, checkfirst=False)
    indexes = [statement for statement in statements if isinstance(statement, CreateIndex)]
    assert len(indexes) == 2
    for statement in indexes:
        ddl = str(statement.compile(dialect=engine.dialect))
        assert "CREATE UNIQUE INDEX" in ddl and "rack_face" in ddl and "digest" not in ddl


def test_all_persisted_face_columns_are_varchar_ten():
    from src.app.execution.models import PositionProjection
    from src.app.transport.models import TransportDebugPositionProjection, TransportMember

    for model, name in (
        (TransportMember, "arrival_face"),
        (TransportDebugPositionProjection, "arrival_face"),
        (PositionProjection, "arrival_face"),
        (PickingTask, "target_rack_face"),
        (DirectPickExecution, "rack_face"),
        (PickingTaskBinSourceRack, "rack_face"),
    ):
        assert model.__table__.c[name].type.length == 10
