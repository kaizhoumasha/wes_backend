from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, func, select

from src.app.execution.models import PositionProjection
from src.app.execution.services.position_projection_service import PositionProjectionService
from src.app.transport.contracts import (
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackPosition,
    TransportCaller,
    TransportContractError,
    TransportExecutionAuthority,
    TransportResourceConflict,
    TransportSubmitCode,
    TransportSubmitResult,
)
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.transport_callbacks import record_valid_callback
from tests.support.transport_projections import (
    confirm_rack_faces_with_sessions,
    ensure_projection_authority,
    ensure_projection_authority_with_sessions,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.contracts import TransportOutcome

pytestmark = pytest.mark.asyncio


class _UnusedProvider:
    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
    ) -> object:
        raise AssertionError("evidence transaction test must not submit")


class _RejectedProvider:
    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
    ) -> TransportSubmitResult:
        return TransportSubmitResult(
            TransportSubmitCode.REJECTED,
            transport_task_id,
            reason_code="WMS_REJECTED",
        )


class _BlockingEvidenceInsertRepository(TransportRepository):
    def __init__(self) -> None:
        self.inserted = asyncio.Event()
        self.release = asyncio.Event()

    async def add_evidence(self, db: AsyncSession, evidence: TransportEvidence) -> None:
        await super().add_evidence(db, evidence)
        self.inserted.set()
        await self.release.wait()


class _FailingProjectionPort:
    async def get_current(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("forced projection failure")

    async def apply_transport_result(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("forced projection failure")


class _EvidenceReadRepository(TransportRepository):
    def __init__(self) -> None:
        self.read = asyncio.Event()

    async def get_evidence_by_operation_id(
        self,
        db: AsyncSession,
        operation: str,
        operation_id: str,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        evidence = (
            await super().get_evidence_by_operation_id(db, operation, operation_id, for_update=True)
            if for_update
            else await super().get_evidence_by_operation_id(db, operation, operation_id)
        )
        self.read.set()
        return evidence


class _RotationProjectionPort:
    def __init__(self) -> None:
        self.read = asyncio.Event()
        self.release = asyncio.Event()
        self._delegate = PositionProjectionService()

    async def get_current(
        self,
        db: AsyncSession,
        object_type: str,
        object_id: str,
        *,
        for_update: bool = False,
    ) -> PositionProjection | None:
        projection = await self._delegate.get_current(
            db,
            object_type,
            object_id,
            for_update=for_update,
        )
        if not for_update:
            self.read.set()
            await self.release.wait()
        return projection

    async def apply_transport_result(self, db: object, **kwargs: object) -> object | None:
        return await self._delegate.apply_transport_result(db, **kwargs)


class _BlockedEvidenceReadRepository(TransportRepository):
    def __init__(self) -> None:
        self.before_read = asyncio.Event()
        self.release = asyncio.Event()

    async def get_evidence(
        self,
        db: AsyncSession,
        evidence_id: int,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        self.before_read.set()
        await self.release.wait()
        return await super().get_evidence(db, evidence_id, for_update=for_update)


class _EvidenceThenTaskBarrierRepository(TransportRepository):
    def __init__(self) -> None:
        self.evidence_locked = asyncio.Event()
        self.task_lookup_started = asyncio.Event()
        self.release_evidence = asyncio.Event()

    async def get_evidence(
        self,
        db: AsyncSession,
        evidence_id: int,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        evidence = await super().get_evidence(db, evidence_id, for_update=for_update)
        if for_update and not self.evidence_locked.is_set():
            self.evidence_locked.set()
            await self.release_evidence.wait()
        return evidence

    async def get_task(
        self,
        db: AsyncSession,
        transport_task_id: str,
        *,
        for_update: bool = False,
    ) -> TransportTask | None:
        self.task_lookup_started.set()
        return await super().get_task(db, transport_task_id, for_update=for_update)


class _DuplicateTaskLockRepository(TransportRepository):
    def __init__(self) -> None:
        self.task_locked = asyncio.Event()

    async def get_evidence_by_operation_id(
        self,
        db: AsyncSession,
        operation: str,
        operation_id: str,
        *,
        for_update: bool = False,
    ) -> TransportEvidence | None:
        if for_update:
            self.task_locked.set()
        return await super().get_evidence_by_operation_id(db, operation, operation_id, for_update=for_update)


async def test_concurrent_duplicate_public_calls_share_one_postgresql_aggregate(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    services = [
        TransportService(
            integration_session_factory,
            TransportRepository(),
            _UnusedProvider(),
        )
        for _ in range(2)
    ]
    client_request_id = new_uuid7()

    handles = await asyncio.gather(
        *(
            service.move_rack(
                client_request_id,
                TransportCaller("INTEGRATION"),
                f"rack-duplicate-{suffix}",
                RackPosition("SOURCE"),
                RackPosition("TARGET"),
                "90",
            )
            for service in services
        )
    )

    try:
        assert handles[0] == handles[1]
        async with integration_session_factory() as db:
            tasks = list(
                await db.scalars(select(TransportTask).where(TransportTask.client_request_id == client_request_id))
            )
        assert len(tasks) == 1
    finally:
        task_id = handles[0].transport_task_id
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id == task_id)
            )
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == task_id))
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))


async def test_concurrent_resource_conflict_has_one_postgresql_winner(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    services = [
        TransportService(
            integration_session_factory,
            TransportRepository(),
            _UnusedProvider(),
        )
        for _ in range(2)
    ]
    client_request_ids = [new_uuid7(), new_uuid7()]
    results = await asyncio.gather(
        *(
            service.move_rack(
                client_request_ids[index],
                TransportCaller("INTEGRATION"),
                f"rack-conflict-{suffix}",
                RackPosition("SOURCE"),
                RackPosition("TARGET"),
                "90",
            )
            for index, service in enumerate(services)
        ),
        return_exceptions=True,
    )

    try:
        winners = [result for result in results if not isinstance(result, BaseException)]
        conflicts = [result for result in results if isinstance(result, TransportResourceConflict)]
        assert len(winners) == 1
        assert len(conflicts) == 1
    finally:
        async with integration_session_factory.begin() as db:
            task_ids = list(
                await db.scalars(
                    select(TransportTask.transport_task_id).where(
                        TransportTask.client_request_id.in_(client_request_ids)
                    )
                )
            )
            if task_ids:
                await db.execute(
                    delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id.in_(task_ids))
                )
                await db.execute(delete(TransportMember).where(TransportMember.transport_task_id.in_(task_ids)))
                await db.execute(delete(TransportTask).where(TransportTask.transport_task_id.in_(task_ids)))


async def test_stale_evidence_worker_cannot_overwrite_reclaimed_result(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    operation_id = new_uuid7()
    setup_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
    )
    await record_valid_callback(
        setup_service,
        operation_id=operation_id,
        transport_task_id=f"missing-{suffix}",
        operation=RESULT_OPERATION,
        timestamp=1,
        payload={
            "transport_task_id": f"missing-{suffix}",
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": f"rack-missing-{suffix}",
            "status": "SUCCEEDED",
            "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
            "arrival_face": "90",
        },
    )
    blocked_repository = _BlockedEvidenceReadRepository()
    stale_service = TransportService(
        integration_session_factory,
        blocked_repository,
        _UnusedProvider(),
    )
    winner_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
    )
    stale_task = asyncio.create_task(stale_service.process_pending_evidence(1))
    await blocked_repository.before_read.wait()

    try:
        async with integration_session_factory.begin() as db:
            evidence = await db.scalar(
                select(TransportEvidence).where(TransportEvidence.operation_id == operation_id).with_for_update()
            )
            assert evidence is not None
            evidence.claim_until = timezone.now_for_db() - timedelta(seconds=1)

        assert await winner_service.process_pending_evidence(1) == 1
        blocked_repository.release.set()
        assert await stale_task == 0

        async with integration_session_factory() as db:
            evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        assert evidence is not None
        assert evidence.status == "CONFLICT"
        assert evidence.conflict_code == "TRANSPORT_TASK_NOT_FOUND"
    finally:
        blocked_repository.release.set()
        await asyncio.gather(stale_task, return_exceptions=True)
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id)
            )
            await db.execute(delete(TransportEvidence).where(TransportEvidence.operation_id == operation_id))


async def test_evidence_application_rolls_back_task_member_and_evidence_together(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
    )
    authority = await ensure_projection_authority_with_sessions(integration_session_factory)
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller("INTEGRATION"),
        "rack-rollback",
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
        "90",
        execution_authority=authority,
    )
    operation_id = new_uuid7()
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "rack-rollback",
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
        "arrival_face": "90",
    }
    await record_valid_callback(
        service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )
    failing_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
        position_projections=_FailingProjectionPort(),
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
            evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        assert task is not None and task.status == "PENDING"
        assert member is not None and member.status == "PENDING" and member.final_position_json is None
        assert evidence is not None and evidence.status == "PENDING" and evidence.processed_at is None
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id)
            )
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
    )
    await confirm_rack_faces_with_sessions(integration_session_factory, {"rack-concurrent": "90"})
    handle = await setup_service.move_bins(
        new_uuid7(),
        TransportCaller("INTEGRATION"),
        (BinMove("bin-concurrent", RackBinSlot("rack-concurrent", "90", "1"), HandoffPosition("ROLLER_IN")),),
    )
    operation_id = new_uuid7()
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-concurrent",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    services = [
        TransportService(
            integration_session_factory,
            TransportRepository(),
            _UnusedProvider(),
        )
        for _ in range(2)
    ]

    try:
        results = await asyncio.gather(
            *(
                record_valid_callback(
                    service,
                    operation_id=operation_id,
                    transport_task_id=handle.transport_task_id,
                    operation=RESULT_OPERATION,
                    timestamp=1,
                    payload=payload,
                )
                for service in services
            )
        )
        assert sorted(result["code"] for result in results) == ["DUPLICATE", "RECEIVED"]
        assert results[0]["timestamp"] == results[1]["timestamp"]
        assert results[0]["data"] == results[1]["data"] == {"transport_task_id": handle.transport_task_id}
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id)
            )
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


async def test_concurrent_result_revision_binds_to_only_one_operation(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    setup_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
    )
    await confirm_rack_faces_with_sessions(integration_session_factory, {"rack-revision": "90"})
    handle = await setup_service.move_bins(
        new_uuid7(),
        TransportCaller("INTEGRATION"),
        (BinMove("bin-revision", RackBinSlot("rack-revision", "90", "1"), HandoffPosition("ROLLER_IN")),),
    )
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-revision",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    services = [
        TransportService(
            integration_session_factory,
            TransportRepository(),
            _UnusedProvider(),
        )
        for _ in range(2)
    ]

    callback_operation_ids = [new_uuid7(), new_uuid7()]
    try:
        results = await asyncio.gather(
            *(
                record_valid_callback(
                    service,
                    operation_id=callback_operation_id,
                    transport_task_id=handle.transport_task_id,
                    operation=RESULT_OPERATION,
                    timestamp=1,
                    payload=payload,
                )
                for service, callback_operation_id in zip(services, callback_operation_ids, strict=True)
            )
        )
        assert sorted(result["code"] for result in results) == ["CONFLICT", "RECEIVED"]

        async with integration_session_factory() as db:
            evidence_count = await db.scalar(
                select(func.count())
                .select_from(TransportEvidence)
                .where(
                    TransportEvidence.transport_task_id == handle.transport_task_id,
                    TransportEvidence.outcome_revision == 1,
                )
            )
        assert evidence_count == 1
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(
                    TransportCallbackReceipt.operation_id.in_(callback_operation_ids)
                )
            )
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


async def test_evidence_worker_and_duplicate_callback_share_task_then_evidence_lock_order(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    setup_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
    )
    await confirm_rack_faces_with_sessions(integration_session_factory, {"rack-lock-order": "90"})
    handle = await setup_service.move_bins(
        new_uuid7(),
        TransportCaller("INTEGRATION"),
        (BinMove("bin-lock-order", RackBinSlot("rack-lock-order", "90", "1"), HandoffPosition("ROLLER_IN")),),
    )
    operation_id = new_uuid7()
    payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-lock-order",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    first_ack = await record_valid_callback(
        setup_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )
    worker_repository = _EvidenceThenTaskBarrierRepository()
    callback_repository = _DuplicateTaskLockRepository()
    worker_service = TransportService(
        integration_session_factory,
        worker_repository,
        _UnusedProvider(),
    )
    callback_service = TransportService(
        integration_session_factory,
        callback_repository,
        _UnusedProvider(),
    )
    worker = asyncio.create_task(worker_service.process_pending_evidence(1))
    await worker_repository.evidence_locked.wait()
    callback = asyncio.create_task(
        record_valid_callback(
            callback_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload=payload,
        )
    )

    # 旧顺序在这里构造 evidence->task / task->evidence 环；统一顺序下 worker 已先持有 task。
    if not worker_repository.task_lookup_started.is_set():
        await callback_repository.task_locked.wait()
    worker_repository.release_evidence.set()

    try:
        results = await asyncio.gather(worker, callback, return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        assert errors == []
        assert results[0] == 1
        duplicate_ack = results[1]
        assert isinstance(duplicate_ack, dict)
        assert duplicate_ack == {**first_ack, "http_status": 200, "code": "DUPLICATE"}
    finally:
        worker_repository.release_evidence.set()
        await asyncio.gather(worker, callback, return_exceptions=True)
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id)
            )
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


async def test_uncommitted_callback_serializes_before_rejected_submit_writeback(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    setup_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
    )
    rack_id = f"rack-callback-before-reject-{uuid.uuid4().hex}"
    authority = await ensure_projection_authority_with_sessions(integration_session_factory)
    handle = await setup_service.move_rack(
        new_uuid7(),
        TransportCaller("INTEGRATION"),
        rack_id,
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
        "90",
        execution_authority=authority,
    )
    blocking_repository = _BlockingEvidenceInsertRepository()
    callback_service = TransportService(
        integration_session_factory,
        blocking_repository,
        _UnusedProvider(),
    )
    submit_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _RejectedProvider(),
    )
    operation_id = new_uuid7()
    callback_task = asyncio.create_task(
        record_valid_callback(
            callback_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload={
                "transport_task_id": handle.transport_task_id,
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": rack_id,
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
                "arrival_face": "90",
            },
        )
    )
    await blocking_repository.inserted.wait()
    submit_task = asyncio.create_task(submit_service.submit_pending_tasks(1))

    try:
        first_submit_count = await asyncio.wait_for(asyncio.shield(submit_task), timeout=0.2)
    finally:
        blocking_repository.release.set()

    assert (await callback_task)["code"] == "RECEIVED"
    assert await submit_task == 0
    assert await submit_service.submit_pending_tasks(1) == 1
    async with integration_session_factory() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        binding = await db.scalar(
            select(TransportResourceBinding).where(
                TransportResourceBinding.transport_task_id == handle.transport_task_id
            )
        )
    pre_process = (
        task.status if task is not None else None,
        evidence.status if evidence is not None else None,
        evidence.conflict_code if evidence is not None else None,
        binding is not None,
        binding.released_at if binding is not None else None,
    )
    processed = await setup_service.process_pending_evidence(1)
    async with integration_session_factory() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))
        evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        projection = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == rack_id))
    post_process = (
        task.status if task is not None else None,
        evidence.status if evidence is not None else None,
        evidence.conflict_code if evidence is not None else None,
    )

    async with integration_session_factory.begin() as db:
        await db.execute(delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id))
        await db.execute(
            delete(TransportEvidence).where(TransportEvidence.transport_task_id == handle.transport_task_id)
        )
        await db.execute(delete(PositionProjection).where(PositionProjection.object_id == rack_id))
        await db.execute(
            delete(TransportResourceBinding).where(
                TransportResourceBinding.transport_task_id == handle.transport_task_id
            )
        )
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == handle.transport_task_id))
        await db.execute(delete(TransportTask).where(TransportTask.transport_task_id == handle.transport_task_id))

    assert first_submit_count == 0
    assert pre_process == ("PENDING", "PENDING", None, True, None)
    assert processed == 1
    assert post_process == ("SUCCEEDED", "APPLIED", None)
    assert projection is not None
    assert projection.source_transport_task_id == handle.transport_task_id


async def test_result_updates_existing_projection_source_transport_task_id(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
    )
    rack_id = f"rack-projection-source-{uuid.uuid4().hex}"
    async with integration_session_factory.begin() as db:
        workline_id, line_run_epoch_id = await ensure_projection_authority(db)
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id=rack_id,
                workline_id=workline_id,
                line_run_epoch_id=line_run_epoch_id,
                position_json={"kind": "RACK_POSITION", "location_code": "SOURCE"},
                position_unknown=False,
                arrival_face="90",
                source_operation_id="projection-source-initial",
                source_transport_task_id="projection-source-initial",
                updated_at=timezone.now_for_db(),
            )
        )
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller("INTEGRATION"),
        rack_id,
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
        "90",
        execution_authority=TransportExecutionAuthority(
            workline_id=workline_id,
            line_run_epoch_id=line_run_epoch_id,
        ),
    )
    operation_id = new_uuid7()

    try:
        await record_valid_callback(
            service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload={
                "transport_task_id": handle.transport_task_id,
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": rack_id,
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
                "arrival_face": "90",
            },
        )
        assert await service.process_pending_evidence(1) == 1

        async with integration_session_factory() as db:
            projection = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == rack_id))
        assert projection is not None
        assert projection.source_transport_task_id == handle.transport_task_id
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id)
            )
            await db.execute(delete(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
            await db.execute(delete(PositionProjection).where(PositionProjection.object_id == rack_id))
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
    )
    await confirm_rack_faces_with_sessions(integration_session_factory, {"rack-apply-race": "90"})
    handle = await setup_service.move_bins(
        new_uuid7(),
        TransportCaller("INTEGRATION"),
        (BinMove("bin-apply-race", RackBinSlot("rack-apply-race", "90", "1"), HandoffPosition("ROLLER_IN")),),
    )
    operation_id = new_uuid7()
    original_payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-apply-race",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    await record_valid_callback(
        setup_service,
        operation_id=operation_id,
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=original_payload,
    )
    applied = asyncio.Event()
    release = asyncio.Event()

    async def apply_without_committing() -> None:
        async with integration_session_factory.begin() as db:
            evidence = await db.scalar(
                select(TransportEvidence).where(TransportEvidence.operation_id == operation_id).with_for_update()
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
    )
    apply_task = asyncio.create_task(apply_without_committing())
    await applied.wait()
    conflict_task = asyncio.create_task(
        record_valid_callback(
            conflicting_service,
            operation_id=operation_id,
            transport_task_id=handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload={**original_payload, "outcome_revision": 2},
        )
    )

    try:
        # 旧实现会在 APPLIED 事务提交前读到 PENDING；加锁后读取会等待权威事务提交。
        try:
            await asyncio.wait_for(tracing_repository.read.wait(), timeout=0.1)
        except TimeoutError:
            pass
        release.set()
        assert (await conflict_task)["code"] == "CONFLICT"
        await apply_task

        async with integration_session_factory() as db:
            evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        assert evidence is not None and evidence.status == "APPLIED"
        assert evidence.conflict_code is None
    finally:
        release.set()
        await asyncio.gather(apply_task, conflict_task, return_exceptions=True)
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id)
            )
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
    )
    rack_id = f"rack-rotate-race-{uuid.uuid4().hex}"
    async with integration_session_factory.begin() as db:
        workline_id, line_run_epoch_id = await ensure_projection_authority(db)
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id=rack_id,
                workline_id=workline_id,
                line_run_epoch_id=line_run_epoch_id,
                position_json={"kind": "RACK_POSITION", "location_code": "SOURCE"},
                position_unknown=False,
                arrival_face="90",
                source_operation_id="rotate-race-initial",
                source_transport_task_id="rotate-race-initial",
                updated_at=timezone.now_for_db(),
            )
        )
    authority = TransportExecutionAuthority(workline_id=workline_id, line_run_epoch_id=line_run_epoch_id)
    move_handle = await service.move_rack(
        new_uuid7(),
        TransportCaller("INTEGRATION"),
        rack_id,
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
        "90",
        execution_authority=authority,
    )
    race_projection_port = _RotationProjectionPort()
    rotate_service = TransportService(
        integration_session_factory,
        TransportRepository(),
        _UnusedProvider(),
        position_projections=race_projection_port,
    )
    rotate_task = asyncio.create_task(
        rotate_service.rotate_rack(
            new_uuid7(),
            TransportCaller("INTEGRATION"),
            rack_id,
            RackPosition("SOURCE"),
            "270",
            execution_authority=authority,
        )
    )
    move_operation_id = new_uuid7()
    move_payload = {
        "transport_task_id": move_handle.transport_task_id,
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": rack_id,
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
        "arrival_face": "90",
    }
    try:
        try:
            await asyncio.wait_for(race_projection_port.read.wait(), timeout=0.1)
        except TimeoutError:
            pass
        await record_valid_callback(
            service,
            operation_id=move_operation_id,
            transport_task_id=move_handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload=move_payload,
        )
        await service.process_pending_evidence(1)
        race_projection_port.release.set()
        with pytest.raises(TransportContractError):
            await rotate_task
    finally:
        race_projection_port.release.set()
        await asyncio.gather(rotate_task, return_exceptions=True)
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == move_operation_id)
            )
            await db.execute(delete(TransportEvidence).where(TransportEvidence.operation_id == move_operation_id))
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
            await db.execute(delete(PositionProjection).where(PositionProjection.object_id == rack_id))
