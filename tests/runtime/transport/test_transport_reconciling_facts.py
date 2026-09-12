from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, create_autospec

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.execution.models import PositionProjection
from src.app.transport.contracts import (
    TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackPosition,
    TransportCaller,
    TransportContractError,
    TransportOutcome,
    TransportSubmitCode,
    TransportSubmitResult,
)
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportDebugPositionProjection,
    TransportEvidence,
    TransportMember,
    TransportTask,
)
from src.app.transport.repository import TransportRepository
from src.app.transport.service import PositionProjectionPort, TransportService
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata
from tests.support.transport_callbacks import record_valid_callback
from tests.support.transport_projections import confirm_rack_faces, ensure_projection_authority_with_sessions


@pytest.mark.asyncio
async def test_projection_port_fake_can_invalidate_existing_normal_projection(reconciling_service):
    service = reconciling_service
    handle = await service.move_rack(
        new_uuid7(), TransportCaller("SORTER"), "port-rack", RackPosition("A"), RackPosition("B"), "90"
    )
    projection = SimpleNamespace(source_transport_task_id="other-task", position_unknown=False)
    port = create_autospec(PositionProjectionPort, instance=True, spec_set=True)
    port.get_current.return_value = projection
    service._position_projections = port
    async with service._sessions.begin() as db:
        task = await service._repository.get_task(db, handle.transport_task_id)
        member = (await service._repository.list_members(db, handle.transport_task_id))[0]
        await service._repository.lock_position_result(db, member.object_type, member.object_id)
        await service._invalidate_other_task_positions(db, task, member)
        assert projection.position_unknown is True
        assert projection.source_transport_task_id == "other-task"
        port.get_current.assert_awaited_once_with(db, "RACK", "port-rack", for_update=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("milestone", ["POSITION_UNKNOWN", "TARGET_PLACED", "SOURCE_PICKED"])
async def test_position_fact_without_final_result_invalidates_other_task_aggregate(reconciling_service, milestone):
    service = reconciling_service
    caller = TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID)
    move = (BinMove("fact-bin", RackBinSlot("fact-rack", "90", "1"), HandoffPosition("B")),)
    old = await service.move_bins(new_uuid7(), caller, move)
    new = await service.move_bins(new_uuid7(), caller, move)
    await record_valid_callback(
        service,
        operation_id="old-bin-result",
        transport_task_id=old.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [
                {
                    "container_id": "fact-bin",
                    "status": "SUCCEEDED",
                    "final_position": {"kind": "HANDOFF_POSITION", "location_code": "B"},
                }
            ],
        },
    )
    assert await service.process_pending_evidence(1) == 1
    async with service._sessions() as db:
        projection = await service._repository.get_debug_position_projection(db, "BIN", "fact-bin")
        assert projection.position_unknown is False
    await record_valid_callback(
        service,
        operation_id="new-bin-position",
        transport_task_id=new.transport_task_id,
        operation=POSITION_OPERATION,
        timestamp=2,
        payload={
            "container_id": "fact-bin",
            "milestone": milestone,
            **(
                {"final_position": {"kind": "HANDOFF_POSITION", "location_code": "B"}}
                if milestone == "TARGET_PLACED"
                else {}
            ),
        },
    )
    service._repository.lock_position_result.reset_mock()
    assert await service.process_pending_evidence(1) == 1
    async with service._sessions() as db:
        projection = await service._repository.get_debug_position_projection(db, "BIN", "fact-bin")
        assert projection.position_unknown is True
        assert projection.source_transport_task_id == old.transport_task_id
        assert projection.source_operation_id == "old-bin-result"
        member = (await service._repository.list_members(db, new.transport_task_id))[0]
        assert member.last_operation_id == "new-bin-position"
        assert (await service._repository.get_task(db, new.transport_task_id)).last_applied_wms_outcome_revision == 0
    service._repository.lock_position_result.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("directions", [("normal", "debug"), ("debug", "normal"), ("normal", "no_owner")])
async def test_cross_projection_domains_invalidate_existing_aggregate(reconciling_service, monkeypatch, directions):
    from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository

    monkeypatch.setattr(PositionProjectionRepository, "lock_projection", AsyncMock())
    service = reconciling_service
    authority = await ensure_projection_authority_with_sessions(service._sessions)
    handles = []
    for index, domain in enumerate(directions):
        handle = await service.move_rack(
            new_uuid7(),
            TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID if domain == "debug" else "SORTER"),
            "mixed-rack",
            RackPosition("A"),
            RackPosition("B"),
            "90",
            execution_authority=authority if domain == "normal" else None,
        )
        handles.append(handle)
        await record_valid_callback(
            service,
            operation_id=f"mixed-{index}",
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=index + 1,
            payload={
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": "mixed-rack",
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
                "arrival_face": "90",
            },
        )
        assert await service.process_pending_evidence(1) == 1
        async with service._sessions() as db:
            for model in (PositionProjection, TransportDebugPositionProjection):
                projection = await db.scalar(select(model).where(model.object_id == "mixed-rack"))
                if projection is not None:
                    assert projection.position_unknown is (index == 1)
                    source_index = 0 if (model is PositionProjection) == (directions[0] == "normal") else 1
                    assert projection.source_transport_task_id == handles[source_index].transport_task_id
                    assert projection.source_operation_id == f"mixed-{source_index}"
            if index == 1 and "debug" in directions:
                with pytest.raises(TransportContractError):
                    await service.assert_debug_rack_position_in_session(db, "mixed-rack", RackPosition("B"), "90")


register_required_sqlmodel_metadata()


@pytest.mark.asyncio
async def test_callback_before_task_is_quiet_then_recovers_exact_registered_work_after_lost_wake(
    reconciling_service,
    db_engine,
    monkeypatch,
):
    from src.core import transaction_wakeup

    fixed_uuid = uuid.uuid4()
    task_id = f"transport-{fixed_uuid}"
    payload = {
        "transport_task_id": task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "late-rack",
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
        "arrival_face": "90",
    }
    queue = Mock()
    reconciling_service._task_queue = queue
    await record_valid_callback(
        reconciling_service,
        operation_id="before-task-result",
        transport_task_id=task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )
    await asyncio.gather(*tuple(transaction_wakeup._pending))
    queue.enqueue_transport_evidence.assert_not_called()
    assert await reconciling_service.process_pending_evidence(10) == 0
    assert await reconciling_service.process_pending_evidence(10) == 0

    queue.enqueue_transport_evidence.side_effect = RuntimeError("lost broker notification")
    with monkeypatch.context() as patch:
        patch.setattr("src.app.transport.service.uuid.uuid4", lambda: fixed_uuid)
        handle = await reconciling_service.move_rack(
            new_uuid7(),
            TransportCaller("SORTER"),
            "late-rack",
            RackPosition("A"),
            RackPosition("B"),
            "90",
        )
    await asyncio.gather(*tuple(transaction_wakeup._pending))
    queue.enqueue_transport_submit.assert_not_called()
    assert handle.transport_task_id == task_id
    restarted = TransportService(
        reconciling_service._sessions, TransportRepository(), FakeProvider(), result_timeout=timedelta(seconds=420)
    )
    assert await restarted.process_pending_evidence(10) == 1
    assert await restarted.process_pending_evidence(10) == 0
    snapshot = await restarted.get_task_snapshot(task_id)
    assert snapshot.status == "SUCCEEDED"
    assert snapshot.outcome_version == 1

    async with reconciling_service._sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.transport_task_id == task_id))
        assert evidence.status == "APPLIED"
        assert evidence.payload_json == payload


@pytest.mark.asyncio
async def test_cross_task_position_stays_unconfirmed_when_original_task_later_refines_unknown(
    reconciling_service,
    db_engine,
):
    caller = TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID)
    old = await reconciling_service.move_rack(
        new_uuid7(), caller, "unordered-rack", RackPosition("A"), RackPosition("B"), "90"
    )
    new = await reconciling_service.move_rack(
        new_uuid7(), caller, "unordered-rack", RackPosition("A"), RackPosition("C"), "90"
    )
    for handle, revision, target in [(old, 1, None), (new, 1, "C"), (old, 2, "B")]:
        payload = {
            "kind": "RACK_MOVE",
            "outcome_revision": revision,
            "rack_id": "unordered-rack",
            "status": "SUCCEEDED" if target else "FAILED",
            **(
                {"final_position": {"kind": "RACK_POSITION", "location_code": target}, "arrival_face": "90"}
                if target
                else {"position_unknown": True, "failure_code": "POSITION_UNKNOWN"}
            ),
        }
        await record_valid_callback(
            reconciling_service,
            operation_id=f"cross-{handle.transport_task_id[-8:]}-{revision}",
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=revision,
            payload=payload,
        )
        assert await reconciling_service.process_pending_evidence(1) == 1
    async with reconciling_service._sessions() as db:
        projection = await db.scalar(
            select(TransportDebugPositionProjection).where(
                TransportDebugPositionProjection.object_id == "unordered-rack"
            )
        )
        assert projection.position_unknown is True
        assert projection.source_transport_task_id == old.transport_task_id
    assert (await reconciling_service.get_task_snapshot(old.transport_task_id)).status == "SUCCEEDED"
    assert (await reconciling_service.get_task_snapshot(new.transport_task_id)).status == "SUCCEEDED"


class FakeProvider:
    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
        observation: object = None,
    ) -> TransportSubmitResult:
        return TransportSubmitResult(TransportSubmitCode.RECEIVED, transport_task_id)


@pytest_asyncio.fixture
async def reconciling_service(db_engine: object) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        for model in (
            TransportEvidence,
            TransportCallbackReceipt,
            TransportMember,
            PositionProjection,
            TransportDebugPositionProjection,
            TransportTask,
        ):
            await db.execute(delete(model))
    return TransportService(sessions, TransportRepository(), FakeProvider(), result_timeout=timedelta(seconds=420))


@pytest.mark.asyncio
async def test_late_target_placed_cannot_rewrite_a_confirmed_member_position_while_reconciling(
    reconciling_service: TransportService,
    db_engine: object,
) -> None:
    moves = (
        BinMove("bin-confirmed-source", RackBinSlot("rack-reconciling", "90", "1"), HandoffPosition("OUT_1")),
        BinMove("bin-unknown-peer", RackBinSlot("rack-reconciling", "90", "2"), HandoffPosition("OUT_2")),
    )
    await confirm_rack_faces(db_engine, {"rack-reconciling": "90"})
    handle = await reconciling_service.move_bins(new_uuid7(), TransportCaller("SORTER"), moves)
    source = {"kind": "RACK_BIN_SLOT", "rack_id": "rack-reconciling", "rack_face": "A", "slot_id": "1"}
    operation_id = "operation-confirmed-source"
    result = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-confirmed-source",
                "status": "FAILED",
                "final_position": source,
                "failure_code": "RCS_EXECUTION_FAILED",
            },
            {
                "container_id": "bin-unknown-peer",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            },
        ],
    }
    await record_valid_callback(
        reconciling_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=result,
    )
    await reconciling_service.process_pending_evidence(1)
    await record_valid_callback(
        reconciling_service,
        operation_id="operation-late-target",
        transport_task_id=handle.transport_task_id,
        operation=POSITION_OPERATION,
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "container_id": "bin-confirmed-source",
            "milestone": "TARGET_PLACED",
            "final_position": {"kind": "HANDOFF_POSITION", "location_code": "OUT_1"},
        },
    )

    await reconciling_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "operation-late-target")
        )
        member = await db.scalar(
            select(TransportMember).where(
                TransportMember.transport_task_id == handle.transport_task_id,
                TransportMember.object_id == "bin-confirmed-source",
            )
        )
        projection = await db.scalar(
            select(PositionProjection).where(PositionProjection.object_id == "bin-confirmed-source")
        )

    assert evidence is not None and evidence.status == "CONFLICT"
    assert member is not None and member.final_position_json == source
    assert member.status == "FAILED" and member.failure_code == "RCS_EXECUTION_FAILED"
    assert projection is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confirmed_status", "confirmed_failure_code", "late_status", "late_failure_code"),
    [
        ("SUCCEEDED", None, "FAILED", "RCS_EXECUTION_FAILED"),
        ("FAILED", "RCS_EXECUTION_FAILED", "SUCCEEDED", None),
    ],
    ids=["success-to-failure", "failure-to-success"],
)
async def test_late_result_cannot_flip_a_confirmed_member_while_peer_position_is_unknown(
    reconciling_service: TransportService,
    db_engine: object,
    confirmed_status: str,
    confirmed_failure_code: str | None,
    late_status: str,
    late_failure_code: str | None,
) -> None:
    moves = (
        BinMove("bin-confirmed-result", RackBinSlot("rack-result", "90", "1"), HandoffPosition("OUT_1")),
        BinMove("bin-unknown-result", RackBinSlot("rack-result", "90", "2"), HandoffPosition("OUT_2")),
    )
    await confirm_rack_faces(db_engine, {"rack-result": "90"})
    handle = await reconciling_service.move_bins(
        new_uuid7(),
        TransportCaller("SORTER"),
        moves,
    )
    confirmed_target = {"kind": "HANDOFF_POSITION", "location_code": "OUT_1"}
    initial_operation_id = f"operation-result-initial-{confirmed_status.lower()}"
    initial_result = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-confirmed-result",
                "status": confirmed_status,
                "final_position": confirmed_target,
                **({"failure_code": confirmed_failure_code} if confirmed_failure_code is not None else {}),
            },
            {
                "container_id": "bin-unknown-result",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            },
        ],
    }
    late_operation_id = f"operation-result-late-{confirmed_status.lower()}"
    late_result = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 2,
        "results": [
            {
                "container_id": "bin-confirmed-result",
                "status": late_status,
                "final_position": confirmed_target,
                **({"failure_code": late_failure_code} if late_failure_code is not None else {}),
            },
            {
                "container_id": "bin-unknown-result",
                "status": "FAILED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "OUT_2"},
                "failure_code": "RCS_EXECUTION_FAILED",
            },
        ],
    }
    for operation_id, payload in (
        (initial_operation_id, initial_result),
        (late_operation_id, late_result),
    ):
        await record_valid_callback(
            reconciling_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload=payload,
        )
        await reconciling_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == late_operation_id))
        members = list(
            await db.scalars(
                select(TransportMember)
                .where(TransportMember.transport_task_id == handle.transport_task_id)
                .order_by(TransportMember.ordinal)
            )
        )

    assert evidence is not None and evidence.status == "CONFLICT"
    assert (members[0].status, members[0].failure_code, members[0].final_position_json) == (
        confirmed_status,
        confirmed_failure_code,
        confirmed_target,
    )
    assert (members[1].status, members[1].failure_code, members[1].position_unknown) == (
        "FAILED",
        "POSITION_UNKNOWN",
        True,
    )
