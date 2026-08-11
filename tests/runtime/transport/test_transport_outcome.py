from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.transport.contracts import (
    BinMove,
    HandoffPosition,
    MoveRackRequest,
    RackBinSlot,
    RackFace,
    RackPosition,
    TransportCaller,
    TransportContractError,
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
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION
from src.utils.timezone import timezone
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata

register_required_sqlmodel_metadata()


class FakeProvider:
    async def submit(
        self,
        *,
        operation_id: str,
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult:
        transport_task_id = str(payload["transport_task_id"])
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
    return TransportService(sessions, TransportRepository(), FakeProvider())


@pytest.fixture
def outcome_publisher() -> FakePublisher:
    return FakePublisher()


@pytest.mark.asyncio
async def test_record_evidence_only_persists_then_batch_applies_and_publishes(
    outcome_service: TransportService,
    outcome_publisher: FakePublisher,
) -> None:
    handle = await outcome_service.move_bins(
        "request-1",
        TransportCaller("SORTER", "STATION_A"),
        (BinMove("bin-1", RackBinSlot("rack-1", "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
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

    ack = await outcome_service.record_evidence(
        operation_id="event-1",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload=payload,
    )
    assert ack["code"] == "RECEIVED"
    assert outcome_publisher.outcomes == []
    assert await outcome_service.process_pending_evidence(10) == 1
    assert await outcome_service.publish_pending_outcomes(10, outcome_publisher) == 1
    assert outcome_publisher.outcomes[0].status.value == "SUCCEEDED"


@pytest.mark.asyncio
async def test_same_event_is_idempotent_and_changed_payload_conflicts(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        "request-2",
        TransportCaller("SORTER"),
        (BinMove("bin-2", RackBinSlot("rack-2", "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
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
        operation_id="event-2", transport_task_id=handle.transport_task_id, operation=RESULT_OPERATION, payload=payload
    )
    duplicate = await outcome_service.record_evidence(
        operation_id="event-2", transport_task_id=handle.transport_task_id, operation=RESULT_OPERATION, payload=payload
    )
    changed = await outcome_service.record_evidence(
        operation_id="event-2",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload={**payload, "kind": "BIN_EXCHANGE"},
    )

    assert (first["code"], duplicate["code"], changed["code"]) == ("RECEIVED", "DUPLICATE", "CONFLICT")
    assert await outcome_service.process_pending_evidence(1) == 1

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == "event-2"))
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))

    assert evidence is not None
    assert evidence.status == "APPLIED"
    assert evidence.conflict_code == "OPERATION_PAYLOAD_CONFLICT"
    assert task is not None
    assert (task.status, task.reason_code, task.outcome_version) == ("RECONCILING", "TRANSPORT_POSITION_UNKNOWN", 1)


@pytest.mark.asyncio
async def test_same_operation_id_is_independent_between_callback_operations(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        "request-operation-scoped-event",
        TransportCaller("SORTER"),
        (BinMove("bin-operation-scoped", RackBinSlot("rack-operation-scoped", "1"), HandoffPosition("OUT")),),
    )
    operation_id = "shared-operation-id"

    position_code = await outcome_service.record_evidence(
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=POSITION_OPERATION,
        payload={
            "transport_task_id": handle.transport_task_id,
            "bin_id": "bin-operation-scoped",
            "milestone": "SOURCE_PICKED",
        },
    )
    result_code = await outcome_service.record_evidence(
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload={
            "transport_task_id": handle.transport_task_id,
            "kind": "BIN_MOVE",
            "results": [
                {
                    "object_id": "bin-operation-scoped",
                    "status": "SUCCEEDED",
                    "final_position": {"kind": "HANDOFF_POSITION", "location_code": "OUT"},
                }
            ],
        },
    )

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = list(
            await db.scalars(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        )

    assert (position_code["code"], result_code["code"]) == ("RECEIVED", "RECEIVED")
    assert {item.operation for item in evidence} == {POSITION_OPERATION, RESULT_OPERATION}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_id", "transport_task_id"),
    [("e" * 121, "transport-1"), ("event-1", "t" * 81)],
    ids=["operation-id", "transport-task-id"],
)
async def test_record_evidence_rejects_identifiers_larger_than_persistence_columns(
    outcome_service: TransportService,
    operation_id: str,
    transport_task_id: str,
) -> None:
    with pytest.raises(TransportContractError):
        await outcome_service.record_evidence(
            operation_id=operation_id,
            transport_task_id=transport_task_id,
            operation=RESULT_OPERATION,
            payload={},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted_before_conflict", [False, True], ids=["pending", "accepted"])
async def test_conflicting_batch_result_does_not_partially_update_members(
    outcome_service: TransportService,
    db_engine: object,
    accepted_before_conflict: bool,
) -> None:
    moves = (
        BinMove("bin-atomic-1", RackBinSlot("rack-atomic", "1"), HandoffPosition("ROLLER_IN")),
        BinMove("bin-atomic-2", RackBinSlot("rack-atomic", "2"), HandoffPosition("ROLLER_OUT")),
    )
    handle = await outcome_service.move_bins("request-atomic", TransportCaller("SORTER"), moves)
    if accepted_before_conflict:
        assert await outcome_service.submit_pending_tasks(1) == 1
    operation_id = "operation-atomic-conflict"
    payload = {
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
        operation_id=operation_id,
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
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))

    assert evidence is not None
    assert task is not None
    assert evidence.status == "CONFLICT"
    assert task.status == "RECONCILING"
    assert task.outcome_version == 1
    assert task.reason_code == "TRANSPORT_EVIDENCE_CONFLICT"
    assert task.outcome_json is not None
    assert [(member.status, member.final_position_json, member.position_unknown) for member in members] == [
        ("PENDING", None, False),
        ("PENDING", None, False),
    ]
    assert projections == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "results",
    [
        [
            {
                "object_id": "bin-member-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "FAILED",
            }
        ],
        [
            {
                "object_id": "bin-member-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "FAILED",
            },
            {
                "object_id": "bin-member-2",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "FAILED",
            },
            {
                "object_id": "bin-member-extra",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "FAILED",
            },
        ],
        [
            {
                "object_id": "bin-member-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "FAILED",
            },
            {
                "object_id": "bin-member-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "FAILED",
            },
        ],
    ],
    ids=["missing", "extra", "duplicate"],
)
async def test_result_members_must_exactly_match_the_frozen_batch(
    outcome_service: TransportService,
    db_engine: object,
    results: list[dict[str, object]],
) -> None:
    handle = await outcome_service.move_bins(
        f"request-members-{len(results)}-{results[-1]['object_id']}",
        TransportCaller("SORTER"),
        (
            BinMove("bin-member-1", RackBinSlot("rack-members", "1"), HandoffPosition("ROLLER_IN")),
            BinMove("bin-member-2", RackBinSlot("rack-members", "2"), HandoffPosition("ROLLER_OUT")),
        ),
    )
    operation_id = f"operation-members-{len(results)}-{results[-1]['object_id']}"
    await outcome_service.record_evidence(
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload={
            "transport_task_id": handle.transport_task_id,
            "kind": "BIN_MOVE",
            "results": results,
        },
    )

    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        members = list(
            await db.scalars(
                select(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
            )
        )
    assert evidence is not None and evidence.status == "CONFLICT"
    assert all(member.status == "PENDING" and member.final_position_json is None for member in members)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rack_request", "result"),
    [
        (
            MoveRackRequest(
                "request-rack-position-type",
                TransportCaller("SORTER"),
                "rack-position-type",
                RackPosition("SOURCE"),
                RackPosition("TARGET"),
            ),
            {
                "object_id": "rack-position-type",
                "status": "FAILED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                "failure_code": "FAILED",
                "arrival_face": "A",
            },
        ),
        (
            None,
            {
                "object_id": "bin-position-type",
                "status": "FAILED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
                "failure_code": "FAILED",
            },
        ),
    ],
    ids=["rack", "bin"],
)
async def test_result_position_type_must_match_the_frozen_member_type(
    outcome_service: TransportService,
    db_engine: object,
    rack_request: MoveRackRequest | None,
    result: dict[str, object],
) -> None:
    if rack_request is None:
        handle = await outcome_service.move_bins(
            "request-bin-position-type",
            TransportCaller("SORTER"),
            (BinMove("bin-position-type", RackBinSlot("rack-position-type", "1"), HandoffPosition("ROLLER_IN")),),
        )
        kind = "BIN_MOVE"
    else:
        handle = await outcome_service.move_rack(
            rack_request.client_request_id,
            rack_request.caller,
            rack_request.rack_id,
            rack_request.source,
            rack_request.target,
        )
        kind = "RACK_MOVE"
    operation_id = f"operation-position-type-{kind}"
    await outcome_service.record_evidence(
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload={
            "transport_task_id": handle.transport_task_id,
            "kind": kind,
            "results": [result],
        },
    )

    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
        )
    assert evidence is not None and evidence.status == "CONFLICT"
    assert member is not None and member.status == "PENDING" and member.final_position_json is None


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
                source_operation_id="initial-face",
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
    operation_id = "operation-wrong-face"
    payload = {
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
        operation_id=operation_id,
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
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))

    assert task is not None
    assert member is not None
    assert evidence is not None
    assert evidence.status == "CONFLICT"
    assert (task.status, task.reason_code, task.outcome_version) == (
        "RECONCILING",
        "TRANSPORT_EVIDENCE_CONFLICT",
        1,
    )
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
        "transport_task_id": handle.transport_task_id,
        "bin_id": "bin-order",
        "milestone": "TARGET_PLACED",
        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
    }
    picked = {
        "transport_task_id": handle.transport_task_id,
        "bin_id": "bin-order",
        "milestone": "SOURCE_PICKED",
    }
    await outcome_service.record_evidence(
        operation_id="event-target",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        payload=target,
    )
    await outcome_service.process_pending_evidence(1)
    await outcome_service.record_evidence(
        operation_id="event-picked",
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
async def test_late_position_unknown_does_not_regress_confirmed_target_position(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        "request-late-unknown",
        TransportCaller("SORTER"),
        (BinMove("bin-late-unknown", RackBinSlot("rack-late-unknown", "1"), HandoffPosition("ROLLER_IN")),),
    )
    target = {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"}
    for operation_id, milestone, final_position in (
        ("operation-late-unknown-target", "TARGET_PLACED", target),
        ("operation-late-unknown", "POSITION_UNKNOWN", None),
    ):
        payload = {
            "transport_task_id": handle.transport_task_id,
            "bin_id": "bin-late-unknown",
            "milestone": milestone,
        }
        if final_position is not None:
            payload["final_position"] = final_position
        await outcome_service.record_evidence(
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation="transport.task.member_position_changed@v1",
            payload=payload,
        )
        await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "operation-late-unknown")
        )
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
        )
        projection = await db.scalar(
            select(TransportPositionProjection).where(TransportPositionProjection.object_id == "bin-late-unknown")
        )

    assert evidence is not None and evidence.status == "CONFLICT"
    assert member is not None and member.final_position_json == target
    assert member.position_unknown is False
    assert projection is not None and projection.position_json == target
    assert projection.position_unknown is False


@pytest.mark.asyncio
async def test_result_cannot_replace_a_confirmed_target_with_a_different_known_position(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        "request-confirmed-target",
        TransportCaller("SORTER"),
        (BinMove("bin-confirmed-target", RackBinSlot("rack-confirmed", "1"), HandoffPosition("ROLLER_IN")),),
    )
    target = {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"}
    await outcome_service.record_evidence(
        operation_id="event-confirmed-target",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        payload={
            "transport_task_id": handle.transport_task_id,
            "bin_id": "bin-confirmed-target",
            "milestone": "TARGET_PLACED",
            "final_position": target,
        },
    )
    await outcome_service.process_pending_evidence(1)
    await outcome_service.record_evidence(
        operation_id="event-conflicting-final-position",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload={
            "transport_task_id": handle.transport_task_id,
            "kind": "BIN_MOVE",
            "results": [
                {
                    "object_id": "bin-confirmed-target",
                    "status": "FAILED",
                    "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_OUT"},
                    "failure_code": "LATE_FAILURE",
                }
            ],
        },
    )

    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "event-conflicting-final-position")
        )
        member = await db.scalar(
            select(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
        )
        projection = await db.scalar(
            select(TransportPositionProjection).where(TransportPositionProjection.object_id == "bin-confirmed-target")
        )
    assert evidence is not None and evidence.status == "CONFLICT"
    assert member is not None and member.final_position_json == target
    assert projection is not None and projection.position_json == target


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
    for operation_id, milestone in (
        ("operation-position-lost", "POSITION_UNKNOWN"),
        ("operation-picked-too-late", "SOURCE_PICKED"),
    ):
        await outcome_service.record_evidence(
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation="transport.task.member_position_changed@v1",
            payload={
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
            select(TransportEvidence).where(TransportEvidence.operation_id == "operation-picked-too-late")
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
    for operation_id, payload in (("operation-success", success), ("operation-conflict", conflicting)):
        await outcome_service.record_evidence(
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            payload=payload,
        )
        await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "operation-conflict")
        )
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
        operation_id="operation-position-success",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload=success,
    )
    await outcome_service.process_pending_evidence(1)
    await outcome_service.record_evidence(
        operation_id="operation-position-unknown",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        payload={
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
            select(TransportEvidence).where(TransportEvidence.operation_id == "operation-position-unknown")
        )
    assert task is not None
    assert evidence is not None
    assert task.status == "SUCCEEDED"
    assert evidence.status == "CONFLICT"


@pytest.mark.asyncio
async def test_unknown_batch_is_corrected_by_higher_version_and_only_latest_unpublished_outcome_is_sent(
    outcome_service: TransportService,
    outcome_publisher: FakePublisher,
    db_engine: object,
) -> None:
    moves = (
        BinMove("bin-version-1", RackBinSlot("rack-version", "1"), HandoffPosition("ROLLER_IN")),
        BinMove("bin-version-2", RackBinSlot("rack-version", "2"), HandoffPosition("ROLLER_IN")),
    )
    handle = await outcome_service.move_bins("request-version", TransportCaller("SORTER"), moves)
    unknown = {
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
    for operation_id, payload in (("operation-version-1", unknown), ("operation-version-2", corrected)):
        await outcome_service.record_evidence(
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            payload=payload,
        )
        await outcome_service.process_pending_evidence(1)

    assert await outcome_service.publish_pending_outcomes(1, outcome_publisher) == 1
    assert [(item.outcome_version, item.status.value) for item in outcome_publisher.outcomes] == [(2, "SUCCEEDED")]
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
