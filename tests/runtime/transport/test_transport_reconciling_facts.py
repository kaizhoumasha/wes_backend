from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.transport.contracts import (
    BinMove,
    HandoffPosition,
    RackBinSlot,
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
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION
from src.core.uuid7 import new_uuid7
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
        return TransportSubmitResult(TransportSubmitCode.RECEIVED, str(payload["transport_task_id"]))


@pytest_asyncio.fixture
async def reconciling_service(db_engine: object) -> TransportService:
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


@pytest.mark.asyncio
async def test_late_target_placed_cannot_rewrite_a_confirmed_member_position_while_reconciling(
    reconciling_service: TransportService,
    db_engine: object,
) -> None:
    moves = (
        BinMove("bin-confirmed-source", RackBinSlot("rack-reconciling", "1"), HandoffPosition("OUT_1")),
        BinMove("bin-unknown-peer", RackBinSlot("rack-reconciling", "2"), HandoffPosition("OUT_2")),
    )
    handle = await reconciling_service.move_bins(new_uuid7(), TransportCaller("SORTER"), moves)
    source = {"kind": "RACK_BIN_SLOT", "rack_id": "rack-reconciling", "slot_id": "1"}
    operation_id = "operation-confirmed-source"
    result = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-confirmed-source",
                "status": "FAILED",
                "final_position": source,
                "failure_code": "PICK_FAILED",
            },
            {
                "object_id": "bin-unknown-peer",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_LOST",
            },
        ],
    }
    await reconciling_service.record_evidence(
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=result,
    )
    await reconciling_service.process_pending_evidence(1)
    await reconciling_service.record_evidence(
        operation_id="operation-late-target",
        transport_task_id=handle.transport_task_id,
        operation=POSITION_OPERATION,
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "bin_id": "bin-confirmed-source",
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
            select(TransportPositionProjection).where(TransportPositionProjection.object_id == "bin-confirmed-source")
        )

    assert evidence is not None and evidence.status == "CONFLICT"
    assert member is not None and member.final_position_json == source
    assert member.status == "FAILED" and member.failure_code == "PICK_FAILED"
    assert projection is not None and projection.position_json == source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confirmed_status", "confirmed_failure_code", "late_status", "late_failure_code"),
    [
        ("SUCCEEDED", None, "FAILED", "LATE_FAILURE"),
        ("FAILED", "INITIAL_FAILURE", "SUCCEEDED", None),
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
        BinMove("bin-confirmed-result", RackBinSlot("rack-result", "1"), HandoffPosition("OUT_1")),
        BinMove("bin-unknown-result", RackBinSlot("rack-result", "2"), HandoffPosition("OUT_2")),
    )
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
        "results": [
            {
                "object_id": "bin-confirmed-result",
                "status": confirmed_status,
                "final_position": confirmed_target,
                **({"failure_code": confirmed_failure_code} if confirmed_failure_code is not None else {}),
            },
            {
                "object_id": "bin-unknown-result",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_LOST",
            },
        ],
    }
    late_operation_id = f"operation-result-late-{confirmed_status.lower()}"
    late_result = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-confirmed-result",
                "status": late_status,
                "final_position": confirmed_target,
                **({"failure_code": late_failure_code} if late_failure_code is not None else {}),
            },
            {
                "object_id": "bin-unknown-result",
                "status": "FAILED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "OUT_2"},
                "failure_code": "POSITION_LOST",
            },
        ],
    }
    for operation_id, payload in (
        (initial_operation_id, initial_result),
        (late_operation_id, late_result),
    ):
        await reconciling_service.record_evidence(
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
        "POSITION_LOST",
        True,
    )
