from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.transport.contracts import (
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackFace,
    RackPosition,
    TransportCaller,
    TransportOutcome,
    TransportSubmitCode,
    TransportSubmitResult,
)
from src.app.transport.models import (
    TransportEvidence,
    TransportMember,
    TransportPositionProjection,
    TransportResourceBinding,
    TransportTask,
)
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.utils.timezone import timezone
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata

register_required_sqlmodel_metadata()


class FakeProvider:
    async def submit(self, request: object, *, transport_task_id: str) -> TransportSubmitResult:
        return TransportSubmitResult(TransportSubmitCode.RECEIVED, transport_task_id)


class FakePublisher:
    def __init__(self) -> None:
        self.outcomes: list[TransportOutcome] = []

    async def publish(self, outcome: TransportOutcome) -> None:
        self.outcomes.append(outcome)


@pytest_asyncio.fixture
async def outcome_service(db_engine: object) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        for model in (
            TransportEvidence,
            TransportResourceBinding,
            TransportMember,
            TransportPositionProjection,
            TransportTask,
        ):
            await db.execute(delete(model))
    return TransportService(sessions, TransportRepository(), FakeProvider(), FakePublisher())


@pytest.mark.asyncio
async def test_record_evidence_only_persists_then_batch_applies_and_publishes(
    outcome_service: TransportService,
) -> None:
    handle = await outcome_service.move_bins(
        "request-1",
        TransportCaller("SORTER", "STATION_A"),
        (BinMove("bin-1", RackBinSlot("rack-1", "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
        "event_id": "event-1",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }

    assert (
        await outcome_service.record_evidence(
            event_id="event-1",
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            payload=payload,
        )
        == "RECEIVED"
    )
    assert outcome_service._outcome_publisher.outcomes == []
    assert await outcome_service.process_pending_evidence(10) == 1
    assert await outcome_service.publish_pending_outcomes(10) == 1
    assert outcome_service._outcome_publisher.outcomes[0].status.value == "SUCCEEDED"


@pytest.mark.asyncio
async def test_same_event_is_idempotent_and_changed_payload_conflicts(outcome_service: TransportService) -> None:
    handle = await outcome_service.move_bins(
        "request-2",
        TransportCaller("SORTER"),
        (BinMove("bin-2", RackBinSlot("rack-2", "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
        "event_id": "event-2",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-2",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "CTU_ERROR",
            }
        ],
    }

    first = await outcome_service.record_evidence(
        event_id="event-2", transport_task_id=handle.transport_task_id, operation=RESULT_OPERATION, payload=payload
    )
    duplicate = await outcome_service.record_evidence(
        event_id="event-2", transport_task_id=handle.transport_task_id, operation=RESULT_OPERATION, payload=payload
    )
    changed = await outcome_service.record_evidence(
        event_id="event-2",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload={**payload, "kind": "BIN_EXCHANGE"},
    )

    assert (first, duplicate, changed) == ("RECEIVED", "DUPLICATE", "CONFLICT")


@pytest.mark.asyncio
async def test_conflicting_batch_result_does_not_partially_update_members(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    moves = (
        BinMove("bin-atomic-1", RackBinSlot("rack-atomic", "1"), HandoffPosition("ROLLER_IN")),
        BinMove("bin-atomic-2", RackBinSlot("rack-atomic", "2"), HandoffPosition("ROLLER_OUT")),
    )
    handle = await outcome_service.move_bins("request-atomic", TransportCaller("SORTER"), moves)
    payload = {
        "event_id": "event-atomic-conflict",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-atomic-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            },
            {
                "object_id": "bin-atomic-2",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "WRONG_TARGET"},
            },
        ],
    }

    await outcome_service.record_evidence(
        event_id=payload["event_id"],
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload=payload,
    )
    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        members = list(
            await db.scalars(
                select(TransportMember)
                .where(TransportMember.transport_task_id == handle.transport_task_id)
                .order_by(TransportMember.ordinal)
            )
        )
        projections = list(
            await db.scalars(
                select(TransportPositionProjection).where(
                    TransportPositionProjection.object_id.in_({"bin-atomic-1", "bin-atomic-2"})
                )
            )
        )
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.event_id == "event-atomic-conflict")
        )
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))

    assert evidence is not None
    assert task is not None
    assert evidence.status == "CONFLICT"
    assert (task.status, task.outcome_version, task.outcome_json, task.reason_code) == ("PENDING", 0, None, None)
    assert [(member.status, member.final_position_json, member.position_unknown) for member in members] == [
        ("PENDING", None, False),
        ("PENDING", None, False),
    ]
    assert projections == []


@pytest.mark.asyncio
async def test_rotate_success_requires_the_frozen_target_face(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportPositionProjection(
                object_type="RACK",
                object_id="rack-face",
                position_json={"kind": "RACK_POSITION", "location_code": "ROTATE_POINT"},
                position_unknown=False,
                arrival_face="A",
                source_event_id="initial-face",
                updated_at=timezone.now_for_db(),
            )
        )
    handle = await outcome_service.rotate_rack(
        "request-face",
        TransportCaller("SORTER"),
        "rack-face",
        RackPosition("ROTATE_POINT"),
        RackFace.B,
    )
    payload = {
        "event_id": "event-wrong-face",
        "transport_task_id": handle.transport_task_id,
        "kind": "RACK_ROTATE",
        "results": [
            {
                "object_id": "rack-face",
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "ROTATE_POINT"},
                "arrival_face": "A",
            }
        ],
    }

    await outcome_service.record_evidence(
        event_id=payload["event_id"],
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload=payload,
    )
    await outcome_service.process_pending_evidence(1)

    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
        )
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.event_id == "event-wrong-face"))

    assert task is not None
    assert member is not None
    assert evidence is not None
    assert evidence.status == "CONFLICT"
    assert task.status == "PENDING"
    assert member.status == "PENDING"


@pytest.mark.asyncio
async def test_late_source_picked_does_not_regress_confirmed_target_position(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        "request-order",
        TransportCaller("SORTER"),
        (BinMove("bin-order", RackBinSlot("rack-order", "1"), HandoffPosition("ROLLER_IN")),),
    )
    target = {
        "event_id": "event-target",
        "transport_task_id": handle.transport_task_id,
        "bin_id": "bin-order",
        "milestone": "TARGET_PLACED",
        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
    }
    picked = {
        "event_id": "event-picked",
        "transport_task_id": handle.transport_task_id,
        "bin_id": "bin-order",
        "milestone": "SOURCE_PICKED",
    }
    await outcome_service.record_evidence(
        event_id="event-target",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        payload=target,
    )
    await outcome_service.process_pending_evidence(1)
    await outcome_service.record_evidence(
        event_id="event-picked",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        payload=picked,
    )
    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        projection = await db.scalar(
            select(TransportPositionProjection).where(TransportPositionProjection.object_id == "bin-order")
        )
    assert projection is not None
    assert projection.position_json == target["final_position"]


@pytest.mark.asyncio
async def test_late_source_picked_does_not_overwrite_unknown_position(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        "request-unknown-order",
        TransportCaller("SORTER"),
        (BinMove("bin-unknown-order", RackBinSlot("rack-unknown-order", "1"), HandoffPosition("ROLLER_IN")),),
    )
    for event_id, milestone in (
        ("event-position-lost", "POSITION_UNKNOWN"),
        ("event-picked-too-late", "SOURCE_PICKED"),
    ):
        await outcome_service.record_evidence(
            event_id=event_id,
            transport_task_id=handle.transport_task_id,
            operation="transport.task.member_position_changed@v1",
            payload={
                "event_id": event_id,
                "transport_task_id": handle.transport_task_id,
                "bin_id": "bin-unknown-order",
                "milestone": milestone,
            },
        )
        await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
        )
        projection = await db.scalar(
            select(TransportPositionProjection).where(TransportPositionProjection.object_id == "bin-unknown-order")
        )
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.event_id == "event-picked-too-late")
        )

    assert member is not None
    assert projection is not None
    assert evidence is not None
    assert evidence.status == "CONFLICT"
    assert member.position_unknown is True
    assert member.final_position_json is None
    assert projection.position_unknown is True
    assert projection.position_json is None


@pytest.mark.asyncio
async def test_conflicting_result_cannot_rewrite_a_definite_terminal_fact(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        "request-terminal",
        TransportCaller("SORTER"),
        (BinMove("bin-terminal", RackBinSlot("rack-terminal", "1"), HandoffPosition("ROLLER_IN")),),
    )
    success = {
        "event_id": "event-success",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-terminal",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    conflicting = {
        "event_id": "event-conflict",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-terminal",
                "status": "FAILED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                "failure_code": "LATE_CONTRADICTION",
            }
        ],
    }
    for payload in (success, conflicting):
        await outcome_service.record_evidence(
            event_id=payload["event_id"],
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            payload=payload,
        )
        await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.event_id == "event-conflict"))
    assert task is not None
    assert evidence is not None
    assert task.status == "SUCCEEDED"
    assert task.outcome_version == 1
    assert evidence.status == "CONFLICT"


@pytest.mark.asyncio
async def test_position_unknown_cannot_reopen_a_definite_terminal_fact(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        "request-terminal-position",
        TransportCaller("SORTER"),
        (BinMove("bin-position", RackBinSlot("rack-position", "1"), HandoffPosition("ROLLER_IN")),),
    )
    success = {
        "event_id": "event-position-success",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-position",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    await outcome_service.record_evidence(
        event_id=success["event_id"],
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload=success,
    )
    await outcome_service.process_pending_evidence(1)
    await outcome_service.record_evidence(
        event_id="event-position-unknown",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        payload={
            "event_id": "event-position-unknown",
            "transport_task_id": handle.transport_task_id,
            "bin_id": "bin-position",
            "milestone": "POSITION_UNKNOWN",
        },
    )
    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.event_id == "event-position-unknown")
        )
    assert task is not None
    assert evidence is not None
    assert task.status == "SUCCEEDED"
    assert evidence.status == "CONFLICT"


@pytest.mark.asyncio
async def test_unknown_batch_is_corrected_by_higher_version_and_only_latest_unpublished_outcome_is_sent(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    moves = (
        BinMove("bin-version-1", RackBinSlot("rack-version", "1"), HandoffPosition("ROLLER_IN")),
        BinMove("bin-version-2", RackBinSlot("rack-version", "2"), HandoffPosition("ROLLER_IN")),
    )
    handle = await outcome_service.move_bins("request-version", TransportCaller("SORTER"), moves)
    unknown = {
        "event_id": "event-version-1",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-version-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            },
            {
                "object_id": "bin-version-2",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_LOST",
            },
        ],
    }
    corrected = {
        "event_id": "event-version-2",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": move.bin_id,
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
            for move in moves
        ],
    }
    for payload in (unknown, corrected):
        await outcome_service.record_evidence(
            event_id=payload["event_id"],
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            payload=payload,
        )
        await outcome_service.process_pending_evidence(1)

    assert await outcome_service.publish_pending_outcomes(1) == 1
    assert [(item.outcome_version, item.status.value) for item in outcome_service._outcome_publisher.outcomes] == [
        (2, "SUCCEEDED")
    ]
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        bindings = list(
            await db.scalars(
                select(TransportResourceBinding).where(
                    TransportResourceBinding.transport_task_id == handle.transport_task_id
                )
            )
        )
    assert bindings
    assert all(binding.released_at is not None for binding in bindings)
