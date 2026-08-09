from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, select

from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot, TransportCaller
from src.app.transport.models import TransportEvidence, TransportMember, TransportResourceBinding, TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_wire import RESULT_OPERATION

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.contracts import TransportOutcome

pytestmark = pytest.mark.asyncio


class _UnusedProvider:
    async def submit(self, request: object, *, transport_task_id: str) -> object:
        raise AssertionError("evidence transaction test must not submit")


class _UnusedPublisher:
    async def publish(self, outcome: TransportOutcome) -> None:
        raise AssertionError("evidence transaction test must not publish")


class _FailingProjectionRepository(TransportRepository):
    async def get_projection(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("forced projection failure")


class _ReadBarrier:
    def __init__(self) -> None:
        self.arrivals = 0
        self.ready = asyncio.Event()

    async def wait(self) -> None:
        self.arrivals += 1
        if self.arrivals == 2:
            self.ready.set()
        await self.ready.wait()


class _BarrierRepository(TransportRepository):
    def __init__(self, barrier: _ReadBarrier) -> None:
        self._barrier = barrier
        self._first_lookup = True

    async def get_evidence_by_event_id(self, db: AsyncSession, event_id: str) -> TransportEvidence | None:
        evidence = await super().get_evidence_by_event_id(db, event_id)
        if self._first_lookup:
            self._first_lookup = False
            await self._barrier.wait()
        return evidence


async def test_evidence_application_rolls_back_task_member_and_evidence_together(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
        _UnusedPublisher(),
    )
    handle = await service.move_bins(
        "integration-evidence-rollback",
        TransportCaller("INTEGRATION"),
        (BinMove("bin-rollback", RackBinSlot("rack-rollback", "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
        "event_id": "integration-evidence-rollback-event",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-rollback",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    await service.record_evidence(
        event_id=payload["event_id"],
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload=payload,
    )
    failing_service = TransportService(
        integration_session_factory,
        _FailingProjectionRepository(),
        _UnusedProvider(),
        _UnusedPublisher(),
    )

    try:
        with pytest.raises(RuntimeError, match="forced projection failure"):
            await failing_service.process_pending_evidence(1)

        async with integration_session_factory() as db:
            task = await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id)
            )
            member = await db.scalar(
                select(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
            )
            evidence = await db.scalar(
                select(TransportEvidence).where(TransportEvidence.event_id == payload["event_id"])
            )
        assert task is not None and task.status == "PENDING"
        assert member is not None and member.status == "PENDING" and member.final_position_json is None
        assert evidence is not None and evidence.status == "PENDING" and evidence.processed_at is None
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportEvidence).where(TransportEvidence.transport_task_id == handle.transport_task_id)
            )
            await db.execute(
                delete(TransportResourceBinding).where(
                    TransportResourceBinding.transport_task_id == handle.transport_task_id
                )
            )
            await db.execute(
                delete(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
            )
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))


async def test_concurrent_duplicate_callback_converges_to_received_and_duplicate(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    setup_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
        _UnusedPublisher(),
    )
    handle = await setup_service.move_bins(
        "integration-concurrent-evidence",
        TransportCaller("INTEGRATION"),
        (BinMove("bin-concurrent", RackBinSlot("rack-concurrent", "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
        "event_id": "integration-concurrent-evidence-event",
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-concurrent",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    barrier = _ReadBarrier()
    services = [
        TransportService(
            integration_session_factory,
            _BarrierRepository(barrier),
            _UnusedProvider(),
            _UnusedPublisher(),
        )
        for _ in range(2)
    ]

    try:
        results = await asyncio.gather(
            *(
                service.record_evidence(
                    event_id=payload["event_id"],
                    transport_task_id=handle.transport_task_id,
                    operation=RESULT_OPERATION,
                    payload=payload,
                )
                for service in services
            )
        )
        assert sorted(results) == ["DUPLICATE", "RECEIVED"]
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportEvidence).where(TransportEvidence.transport_task_id == handle.transport_task_id)
            )
            await db.execute(
                delete(TransportResourceBinding).where(
                    TransportResourceBinding.transport_task_id == handle.transport_task_id
                )
            )
            await db.execute(
                delete(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id)
            )
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
