from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, select

from src.app.transport.contracts import (
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackFace,
    RackPosition,
    TransportCaller,
    TransportContractError,
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

    async def get_evidence_by_event_id(
        self,
        db: AsyncSession,
        operation: str,
        event_id: str,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        evidence = (
            await super().get_evidence_by_event_id(db, operation, event_id, for_update=True)
            if for_update
            else await super().get_evidence_by_event_id(db, operation, event_id)
        )
        if self._first_lookup:
            self._first_lookup = False
            await self._barrier.wait()
        return evidence


class _EvidenceReadRepository(TransportRepository):
    def __init__(self) -> None:
        self.read = asyncio.Event()

    async def get_evidence_by_event_id(
        self,
        db: AsyncSession,
        operation: str,
        event_id: str,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        evidence = (
            await super().get_evidence_by_event_id(db, operation, event_id, for_update=True)
            if for_update
            else await super().get_evidence_by_event_id(db, operation, event_id)
        )
        self.read.set()
        return evidence


class _RotationReadRepository(TransportRepository):
    def __init__(self) -> None:
        self.read = asyncio.Event()
        self.release = asyncio.Event()

    async def get_projection(
        self,
        db: AsyncSession,
        object_type: str,
        object_id: str,
        *,
        for_update: bool = False,
    ) -> TransportPositionProjection | None:
        projection = await super().get_projection(
            db,
            object_type,
            object_id,
            for_update=for_update,
        )
        if not for_update:
            self.read.set()
            await self.release.wait()
        return projection


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


async def test_conflicting_callback_cannot_overwrite_concurrently_applied_evidence(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    setup_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
        _UnusedPublisher(),
    )
    handle = await setup_service.move_bins(
        "integration-evidence-apply-race",
        TransportCaller("INTEGRATION"),
        (BinMove("bin-apply-race", RackBinSlot("rack-apply-race", "1"), HandoffPosition("ROLLER_IN")),),
    )
    event_id = "integration-evidence-apply-race-event"
    original_payload = {
        "event_id": event_id,
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-apply-race",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    await setup_service.record_evidence(
        event_id=event_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        payload=original_payload,
    )
    applied = asyncio.Event()
    release = asyncio.Event()

    async def apply_without_committing() -> None:
        async with integration_session_factory.begin() as db:
            evidence = await db.scalar(
                select(TransportEvidence).where(TransportEvidence.event_id == event_id).with_for_update()
            )
            assert evidence is not None
            evidence.status = "APPLIED"
            applied.set()
            await release.wait()

    tracing_repository = _EvidenceReadRepository()
    conflicting_service = TransportService(
        integration_session_factory,
        tracing_repository,
        _UnusedProvider(),
        _UnusedPublisher(),
    )
    apply_task = asyncio.create_task(apply_without_committing())
    await applied.wait()
    conflict_task = asyncio.create_task(
        conflicting_service.record_evidence(
            event_id=event_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            payload={**original_payload, "kind": "BIN_EXCHANGE"},
        )
    )

    try:
        # 旧实现会在 APPLIED 事务提交前读到 PENDING；加锁后读取会等待权威事务提交。
        try:
            await asyncio.wait_for(tracing_repository.read.wait(), timeout=0.1)
        except TimeoutError:
            pass
        release.set()
        assert await conflict_task == "CONFLICT"
        await apply_task

        async with integration_session_factory() as db:
            evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.event_id == event_id))
        assert evidence is not None and evidence.status == "APPLIED"
        assert evidence.conflict_code is None
    finally:
        release.set()
        await asyncio.gather(apply_task, conflict_task, return_exceptions=True)
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


async def test_rotate_creation_cannot_use_a_projection_changed_by_an_active_move(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
        _UnusedPublisher(),
    )
    rack_id = "rack-rotate-race"
    async with integration_session_factory.begin() as db:
        db.add(
            TransportPositionProjection(
                object_type="RACK",
                object_id=rack_id,
                position_json={"kind": "RACK_POSITION", "location_code": "SOURCE"},
                position_unknown=False,
                arrival_face="A",
                source_event_id="rotate-race-initial",
                updated_at=timezone.now_for_db(),
            )
        )
    move_handle = await service.move_rack(
        "integration-move-before-rotate",
        TransportCaller("INTEGRATION"),
        rack_id,
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
    )
    race_repository = _RotationReadRepository()
    rotate_service = TransportService(
        integration_session_factory,
        race_repository,
        _UnusedProvider(),
        _UnusedPublisher(),
    )
    rotate_task = asyncio.create_task(
        rotate_service.rotate_rack(
            "integration-stale-rotate",
            TransportCaller("INTEGRATION"),
            rack_id,
            RackPosition("SOURCE"),
            RackFace.B,
        )
    )
    move_payload = {
        "event_id": "integration-move-before-rotate-result",
        "transport_task_id": move_handle.transport_task_id,
        "kind": "RACK_MOVE",
        "results": [
            {
                "object_id": rack_id,
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
                "arrival_face": "B",
            }
        ],
    }
    try:
        try:
            await asyncio.wait_for(race_repository.read.wait(), timeout=0.1)
        except TimeoutError:
            pass
        await service.record_evidence(
            event_id=move_payload["event_id"],
            transport_task_id=move_handle.transport_task_id,
            operation=RESULT_OPERATION,
            payload=move_payload,
        )
        await service.process_pending_evidence(1)
        race_repository.release.set()
        with pytest.raises(TransportContractError):
            await rotate_task
    finally:
        race_repository.release.set()
        await asyncio.gather(rotate_task, return_exceptions=True)
        async with integration_session_factory.begin() as db:
            await db.execute(delete(TransportEvidence).where(TransportEvidence.event_id == move_payload["event_id"]))
            task_ids = [move_handle.transport_task_id]
            stale_task = await db.scalar(
                select(TransportTask).where(TransportTask.client_request_id == "integration-stale-rotate")
            )
            if stale_task is not None:
                task_ids.append(stale_task.transport_task_id)
            await db.execute(
                delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id.in_(task_ids))
            )
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id.in_(task_ids)))
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id.in_(task_ids)))
            await db.execute(
                delete(TransportPositionProjection).where(TransportPositionProjection.object_id == rack_id)
            )
