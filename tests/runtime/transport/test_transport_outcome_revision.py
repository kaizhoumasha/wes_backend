from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot, RackFace, TransportCaller
from src.app.transport.models import TransportEvidence, TransportResourceBinding, TransportTask
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from tests.support.transport_callbacks import record_valid_callback

if TYPE_CHECKING:
    from src.app.transport.service import TransportService


@pytest.mark.asyncio
async def test_unknown_batch_is_corrected_by_higher_version_and_only_latest_unpublished_outcome_is_sent(
    outcome_service: TransportService,
    outcome_publisher,
    db_engine: object,
) -> None:
    moves = (
        BinMove("bin-version-1", RackBinSlot("rack-version", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),
        BinMove("bin-version-2", RackBinSlot("rack-version", RackFace.A, "2"), HandoffPosition("ROLLER_IN")),
    )
    handle = await outcome_service.move_bins(new_uuid7(), TransportCaller("SORTER"), moves)
    unknown = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-version-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            },
            {
                "container_id": "bin-version-2",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            },
        ],
    }
    corrected = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 2,
        "results": [
            {
                "container_id": move.bin_id,
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
            for move in moves
        ],
    }
    for operation_id, payload in (("operation-version-1", unknown), ("operation-version-2", corrected)):
        await record_valid_callback(
            outcome_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload=payload,
        )
        await outcome_service.process_pending_evidence(1)

    assert await outcome_service.publish_pending_outcomes(1, outcome_publisher) == 1
    assert [(item.outcome_version, item.status.value) for item in outcome_publisher.outcomes] == [(2, "SUCCEEDED")]
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        bindings = list(
            await db.scalars(
                select(TransportResourceBinding).where(
                    TransportResourceBinding.transport_task_id == handle.transport_task_id
                )
            )
        )
    assert task is not None
    assert task.last_applied_wms_outcome_revision == 2
    assert bindings
    assert all(binding.released_at is not None for binding in bindings)


@pytest.mark.asyncio
async def test_higher_revision_cannot_advance_a_definite_terminal_fact_even_when_content_matches(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        new_uuid7(),
        TransportCaller("SORTER"),
        (
            BinMove(
                "bin-terminal-revision", RackBinSlot("rack-terminal-revision", RackFace.A, "1"), HandoffPosition("OUT")
            ),
        ),
    )
    result = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-terminal-revision",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "OUT"},
            }
        ],
    }
    for operation_id, revision in (("terminal-revision-1", 1), ("terminal-revision-2", 2)):
        await record_valid_callback(
            outcome_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=revision,
            payload={**result, "outcome_revision": revision},
        )
        await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "terminal-revision-2")
        )
    assert task is not None
    assert evidence is not None
    assert (task.status, task.last_applied_wms_outcome_revision, task.outcome_version) == ("SUCCEEDED", 1, 1)
    assert (evidence.status, evidence.conflict_code) == ("CONFLICT", "TRANSPORT_EVIDENCE_CONFLICT")


@pytest.mark.asyncio
async def test_result_revision_identity_and_late_lower_revision_never_roll_back_projection(
    outcome_service: TransportService,
    db_engine: object,
) -> None:
    handle = await outcome_service.move_bins(
        new_uuid7(),
        TransportCaller("SORTER"),
        (BinMove("bin-revision", RackBinSlot("rack-revision", RackFace.A, "1"), HandoffPosition("OUT")),),
    )
    latest = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 2,
        "results": [
            {
                "container_id": "bin-revision",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "OUT"},
            }
        ],
    }
    first = await record_valid_callback(
        outcome_service,
        operation_id="revision-2",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=2,
        payload=latest,
    )
    same_revision_other_identity = await record_valid_callback(
        outcome_service,
        operation_id="revision-2-conflict",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=2,
        payload=latest,
    )
    assert first["code"] == "RECEIVED"
    assert same_revision_other_identity["code"] == "CONFLICT"
    await outcome_service.process_pending_evidence(1)

    older = {
        **latest,
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-revision",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            }
        ],
    }
    old_ack = await record_valid_callback(
        outcome_service,
        operation_id="revision-1-late",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=older,
    )
    assert old_ack["code"] == "RECEIVED"
    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        first_evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "revision-2")
        )
        old_evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == "revision-1-late")
        )
    assert task is not None
    assert first_evidence is not None
    assert old_evidence is not None
    assert (task.status, task.last_applied_wms_outcome_revision, task.outcome_version) == ("SUCCEEDED", 2, 1)
    assert first_evidence.conflict_code is None
    assert old_evidence.status == "APPLIED"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_identity", ["kind", "member"])
async def test_late_lower_revision_still_validates_frozen_identity(
    outcome_service: TransportService,
    db_engine: object,
    invalid_identity: str,
) -> None:
    handle = await outcome_service.move_bins(
        new_uuid7(),
        TransportCaller("SORTER"),
        (
            BinMove("bin-late-invalid", RackBinSlot("rack-late-invalid", RackFace.A, "1"), HandoffPosition("OUT")),
            BinMove("bin-late-peer", RackBinSlot("rack-late-invalid", RackFace.A, "2"), HandoffPosition("OUT")),
        ),
    )
    latest = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 2,
        "results": [
            {
                "container_id": "bin-late-invalid",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "OUT"},
            },
            {
                "container_id": "bin-late-peer",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "OUT"},
            },
        ],
    }
    await record_valid_callback(
        outcome_service,
        operation_id=f"revision-2-before-invalid-{invalid_identity}",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=2,
        payload=latest,
    )
    await outcome_service.process_pending_evidence(1)

    older = {**latest, "outcome_revision": 1}
    if invalid_identity == "kind":
        older["kind"] = "BIN_EXCHANGE"
    else:
        older["results"] = [{**latest["results"][0], "container_id": "another-bin"}, latest["results"][1]]
    await record_valid_callback(
        outcome_service,
        operation_id=f"revision-1-invalid-{invalid_identity}",
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=older,
    )
    await outcome_service.process_pending_evidence(1)

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        evidence = await db.scalar(
            select(TransportEvidence).where(TransportEvidence.operation_id == f"revision-1-invalid-{invalid_identity}")
        )
    assert task is not None
    assert evidence is not None
    assert (task.status, task.last_applied_wms_outcome_revision, task.outcome_version) == ("SUCCEEDED", 2, 1)
    assert (evidence.status, evidence.conflict_code) == ("CONFLICT", "TRANSPORT_EVIDENCE_CONFLICT")
