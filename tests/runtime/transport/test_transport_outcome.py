from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
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
)
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportPositionProjection,
    TransportTask,
)
from src.app.wms_adapter.transport_event_handler import TransportEventHandler
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.transport_callbacks import record_valid_callback

if TYPE_CHECKING:
    from src.app.transport.service import TransportService


@pytest.mark.asyncio
async def test_record_evidence_only_persists_then_batch_applies_and_publishes(
    outcome_service: TransportService,
    outcome_publisher,
) -> None:
    handle = await outcome_service.move_bins(
        new_uuid7(),
        TransportCaller("SORTER", "STATION_A"),
        (BinMove("bin-1", RackBinSlot("rack-1", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }

    ack = await record_valid_callback(
        outcome_service,
        operation_id="event-1",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
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
        new_uuid7(),
        TransportCaller("SORTER"),
        (BinMove("bin-2", RackBinSlot("rack-2", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-2",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            }
        ],
    }

    first = await record_valid_callback(
        outcome_service,
        operation_id="event-2",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )
    duplicate = await record_valid_callback(
        outcome_service,
        operation_id="event-2",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )
    changed = await record_valid_callback(
        outcome_service,
        operation_id="event-2",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={**payload, "outcome_revision": 2},
    )

    assert (first["code"], duplicate["code"], changed["code"]) == ("RECEIVED", "DUPLICATE", "CONFLICT")
    assert await outcome_service.process_pending_evidence(1) == 1

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == "event-2"))
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))

    assert evidence is not None
    assert evidence.status == "APPLIED"
    assert evidence.conflict_code is None
    assert task is not None
    assert (task.status, task.reason_code, task.outcome_version) == ("RECONCILING", "TRANSPORT_POSITION_UNKNOWN", 1)


@pytest.mark.asyncio
async def test_associated_invalid_callback_replays_first_rejection_and_conflicts_on_changed_message(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    message = {
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "operation": POSITION_OPERATION,
        "timestamp": 1,
        "data": {"transport_task_id": "transport-invalid", "container_id": "bin-1", "milestone": "INVALID"},
    }

    first = await outcome_service.record_callback(
        operation_id=message["operation_id"],
        operation=message["operation"],
        message=message,
        payload=None,
        rejection_reason_code="INVALID_EVIDENCE",
    )
    duplicate = await outcome_service.record_callback(
        operation_id=message["operation_id"],
        operation=message["operation"],
        message=message,
        payload=None,
        rejection_reason_code="INVALID_EVIDENCE",
    )
    changed = await outcome_service.record_callback(
        operation_id=message["operation_id"],
        operation=message["operation"],
        message={**message, "timestamp": 2},
        payload=None,
        rejection_reason_code="INVALID_EVIDENCE",
    )

    assert first == duplicate
    assert first["http_status"] == 422
    assert first["code"] == "REJECTED"
    assert (changed["http_status"], changed["code"]) == (409, "CONFLICT")
    assert changed["data"] == {"transport_task_id": "transport-invalid"}
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        receipts = list(await db.scalars(select(TransportCallbackReceipt)))
        evidence = list(await db.scalars(select(TransportEvidence)))
    assert len(receipts) == 1
    assert evidence == []


@pytest.mark.asyncio
async def test_invalid_float_lexemes_with_same_value_conflict(outcome_service: TransportService) -> None:
    operation_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    prefix = f'{{"operation_id":"{operation_id}","operation":"{POSITION_OPERATION}","timestamp":'.encode()
    suffix = b',"data":{}}'

    first = await TransportEventHandler(outcome_service).handle(prefix + b"1.0" + suffix)
    changed = await TransportEventHandler(outcome_service).handle(prefix + b"1e0" + suffix)

    assert (first.http_status, first.body["code"]) == (422, "REJECTED")
    assert (changed.http_status, changed.body["code"]) == (409, "CONFLICT")


@pytest.mark.asyncio
async def test_conflict_without_an_associated_task_has_empty_data(outcome_service: TransportService) -> None:
    message = {
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "operation": POSITION_OPERATION,
        "timestamp": 1,
        "data": {},
    }

    await outcome_service.record_callback(
        operation_id=message["operation_id"],
        operation=message["operation"],
        message=message,
        payload=None,
        rejection_reason_code="INVALID_EVIDENCE",
    )
    changed = await outcome_service.record_callback(
        operation_id=message["operation_id"],
        operation=message["operation"],
        message={**message, "timestamp": 2},
        payload=None,
        rejection_reason_code="INVALID_EVIDENCE",
    )

    assert (changed["http_status"], changed["code"], changed["data"]) == (409, "CONFLICT", {})


@pytest.mark.asyncio
async def test_record_callback_rejects_nul_in_persisted_operation(outcome_service: TransportService) -> None:
    operation_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    operation = "transport.task.unknown@v1\x00"
    message = {"operation_id": operation_id, "operation": operation}

    with pytest.raises(TransportContractError, match="operation must not contain NUL"):
        await outcome_service.record_callback(
            operation_id=operation_id,
            operation=operation,
            message=message,
            payload=None,
            rejection_reason_code="UNSUPPORTED_OPERATION",
        )


@pytest.mark.asyncio
async def test_lone_surrogate_dto_is_durably_rejected(outcome_service: TransportService) -> None:
    raw_body = (
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472","operation":"'
        + POSITION_OPERATION.encode()
        + b'","timestamp":1,"data":{"transport_task_id":"transport-invalid",'
        b'"container_id":"\\ud800","milestone":"SOURCE_PICKED"}}'
    )
    handler = TransportEventHandler(outcome_service)

    first = await handler.handle(raw_body)
    duplicate = await handler.handle(raw_body)

    assert (first.http_status, first.body["code"]) == (422, "REJECTED")
    assert duplicate.body == first.body


@pytest.mark.asyncio
async def test_same_evidence_identity_with_changed_event_timestamp_conflicts(
    outcome_service: TransportService,
    db_engine,
) -> None:
    request_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4471"
    handle = await outcome_service.move_bins(
        request_id,
        TransportCaller("SORTER"),
        (BinMove("bin-timestamp", RackBinSlot("rack-timestamp", RackFace.A, "1"), HandoffPosition("OUT")),),
    )
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-timestamp",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            }
        ],
    }

    first = await record_valid_callback(
        outcome_service,
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )
    changed = await record_valid_callback(
        outcome_service,
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=2,
        payload=payload,
    )

    assert (first["code"], changed["code"]) == ("RECEIVED", "CONFLICT")

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "019f12d0-58d7-7b4d-a23a-1b90aa5d4472")
        )
    assert evidence is not None
    assert evidence.event_timestamp_ms == 1


@pytest.mark.asyncio
async def test_same_operation_id_is_independent_between_callback_operations(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        new_uuid7(),
        TransportCaller("SORTER"),
        (
            BinMove(
                "bin-operation-scoped", RackBinSlot("rack-operation-scoped", RackFace.A, "1"), HandoffPosition("OUT")
            ),
        ),
    )
    operation_id = "shared-operation-id"

    position_code = await record_valid_callback(
        outcome_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=POSITION_OPERATION,
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "container_id": "bin-operation-scoped",
            "milestone": "SOURCE_PICKED",
        },
    )
    result_code = await record_valid_callback(
        outcome_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [
                {
                    "container_id": "bin-operation-scoped",
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
        await record_valid_callback(
            outcome_service,
            operation_id=operation_id,
            transport_task_id=transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
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
        BinMove("bin-atomic-1", RackBinSlot("rack-atomic", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),
        BinMove("bin-atomic-2", RackBinSlot("rack-atomic", RackFace.A, "2"), HandoffPosition("ROLLER_OUT")),
    )
    handle = await outcome_service.move_bins(new_uuid7(), TransportCaller("SORTER"), moves)
    if accepted_before_conflict:
        assert await outcome_service.submit_pending_tasks(1) == 1
    operation_id = "operation-atomic-conflict"
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-atomic-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            },
            {
                "container_id": "bin-atomic-2",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "WRONG_TARGET"},
            },
        ],
    }

    await record_valid_callback(
        outcome_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
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
                "container_id": "bin-member-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            }
        ],
        [
            {
                "container_id": "bin-member-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            },
            {
                "container_id": "bin-member-2",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            },
            {
                "container_id": "bin-member-extra",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            },
        ],
    ],
    ids=["missing", "extra"],
)
async def test_result_members_must_exactly_match_the_frozen_batch(
    outcome_service: TransportService,
    db_engine: object,
    results: list[dict[str, object]],
) -> None:
    handle = await outcome_service.move_bins(
        new_uuid7(),
        TransportCaller("SORTER"),
        (
            BinMove("bin-member-1", RackBinSlot("rack-members", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),
            BinMove("bin-member-2", RackBinSlot("rack-members", RackFace.A, "2"), HandoffPosition("ROLLER_OUT")),
        ),
    )
    operation_id = f"operation-members-{len(results)}-{results[-1]['container_id']}"
    await record_valid_callback(
        outcome_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
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
async def test_move_success_requires_the_frozen_target_face(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_rack(
        new_uuid7(),
        TransportCaller("SORTER"),
        "rack-move-face",
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
        RackFace.B,
    )
    operation_id = "operation-move-wrong-face"
    await record_valid_callback(
        outcome_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": "rack-move-face",
            "status": "SUCCEEDED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
            "arrival_face": "A",
        },
    )

    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))

    assert evidence is not None and evidence.status == "CONFLICT"


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
        new_uuid7(),
        TransportCaller("SORTER"),
        "rack-face",
        RackPosition("ROTATE_POINT"),
        RackFace.B,
    )
    operation_id = "operation-wrong-face"
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "RACK_ROTATE",
        "outcome_revision": 1,
        "rack_id": "rack-face",
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "ROTATE_POINT"},
        "arrival_face": "A",
    }

    await record_valid_callback(
        outcome_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
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
        new_uuid7(),
        TransportCaller("SORTER"),
        (BinMove("bin-order", RackBinSlot("rack-order", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
    )
    target = {
        "transport_task_id": handle.transport_task_id,
        "container_id": "bin-order",
        "milestone": "TARGET_PLACED",
        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
    }
    picked = {
        "transport_task_id": handle.transport_task_id,
        "container_id": "bin-order",
        "milestone": "SOURCE_PICKED",
    }
    await record_valid_callback(
        outcome_service,
        operation_id="event-target",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        timestamp=1,
        payload=target,
    )
    await outcome_service.process_pending_evidence(1)
    await record_valid_callback(
        outcome_service,
        operation_id="event-picked",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        timestamp=1,
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
        new_uuid7(),
        TransportCaller("SORTER"),
        (BinMove("bin-late-unknown", RackBinSlot("rack-late-unknown", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
    )
    target = {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"}
    for operation_id, milestone, final_position in (
        ("operation-late-unknown-target", "TARGET_PLACED", target),
        ("operation-late-unknown", "POSITION_UNKNOWN", None),
    ):
        payload = {
            "transport_task_id": handle.transport_task_id,
            "container_id": "bin-late-unknown",
            "milestone": milestone,
        }
        if final_position is not None:
            payload["final_position"] = final_position
        await record_valid_callback(
            outcome_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation="transport.task.member_position_changed@v1",
            timestamp=1,
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
        new_uuid7(),
        TransportCaller("SORTER"),
        (
            BinMove(
                "bin-confirmed-target", RackBinSlot("rack-confirmed", RackFace.A, "1"), HandoffPosition("ROLLER_IN")
            ),
        ),
    )
    target = {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"}
    await record_valid_callback(
        outcome_service,
        operation_id="event-confirmed-target",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "container_id": "bin-confirmed-target",
            "milestone": "TARGET_PLACED",
            "final_position": target,
        },
    )
    await outcome_service.process_pending_evidence(1)
    await record_valid_callback(
        outcome_service,
        operation_id="event-conflicting-final-position",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [
                {
                    "container_id": "bin-confirmed-target",
                    "status": "FAILED",
                    "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_OUT"},
                    "failure_code": "RCS_EXECUTION_FAILED",
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
        new_uuid7(),
        TransportCaller("SORTER"),
        (
            BinMove(
                "bin-unknown-order", RackBinSlot("rack-unknown-order", RackFace.A, "1"), HandoffPosition("ROLLER_IN")
            ),
        ),
    )
    for operation_id, milestone in (
        ("operation-position-lost", "POSITION_UNKNOWN"),
        ("operation-picked-too-late", "SOURCE_PICKED"),
    ):
        await record_valid_callback(
            outcome_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation="transport.task.member_position_changed@v1",
            timestamp=1,
            payload={
                "transport_task_id": handle.transport_task_id,
                "container_id": "bin-unknown-order",
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
        new_uuid7(),
        TransportCaller("SORTER"),
        (BinMove("bin-terminal", RackBinSlot("rack-terminal", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
    )
    success = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-terminal",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    conflicting = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 2,
        "results": [
            {
                "container_id": "bin-terminal",
                "status": "FAILED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                "failure_code": "RCS_EXECUTION_FAILED",
            }
        ],
    }
    for operation_id, payload in (("operation-success", success), ("operation-conflict", conflicting)):
        await record_valid_callback(
            outcome_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
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
        new_uuid7(),
        TransportCaller("SORTER"),
        (BinMove("bin-position", RackBinSlot("rack-position", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
    )
    success = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-position",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    await record_valid_callback(
        outcome_service,
        operation_id="operation-position-success",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=success,
    )
    await outcome_service.process_pending_evidence(1)
    await record_valid_callback(
        outcome_service,
        operation_id="operation-position-unknown",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "container_id": "bin-position",
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
