from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, func, select, update

from src.app.transport.contracts import RackFace, RackPosition, TransportCaller, TransportContractError
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportPositionProjection,
    TransportResourceBinding,
    TransportTask,
)
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.transport_callbacks import record_valid_callback

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
        raise AssertionError("debug reset tests must not submit")


class _BlockingResetRepository(TransportRepository):
    def __init__(self) -> None:
        self.task_locked = asyncio.Event()
        self.release = asyncio.Event()

    async def get_debug_reset_counts(self, db: AsyncSession, transport_task_id: str) -> tuple[int, ...]:
        counts = await super().get_debug_reset_counts(db, transport_task_id)
        self.task_locked.set()
        await self.release.wait()
        return counts


class _CallbackLockProbeRepository(TransportRepository):
    def __init__(self) -> None:
        self.task_lookup_started = asyncio.Event()

    async def get_task(
        self,
        db: AsyncSession,
        transport_task_id: str,
        *,
        for_update: bool = False,
    ) -> TransportTask | None:
        if for_update:
            self.task_lookup_started.set()
        return await super().get_task(db, transport_task_id, for_update=for_update)


def _service(
    sessions: async_sessionmaker[AsyncSession],
    repository: TransportRepository | None = None,
) -> TransportService:
    return TransportService(sessions, repository or TransportRepository(), _UnusedProvider())


async def _create_reconciling_task(
    sessions: async_sessionmaker[AsyncSession],
    *,
    resource_id: str,
) -> str:
    service = _service(sessions)
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller("TRANSPORT_DEBUG", "STATION-INTEGRATION"),
        resource_id,
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
        RackFace.A,
    )
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(status="RECONCILING", reason_code="TRANSPORT_DELIVERY_UNKNOWN")
        )
    return handle.transport_task_id


async def _cleanup(
    sessions: async_sessionmaker[AsyncSession],
    *,
    task_ids: tuple[str, ...],
    operation_ids: tuple[str, ...] = (),
) -> None:
    async with sessions.begin() as db:
        if operation_ids:
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id.in_(operation_ids))
            )
            await db.execute(
                delete(TransportPositionProjection).where(
                    TransportPositionProjection.source_operation_id.in_(operation_ids)
                )
            )
            await db.execute(delete(TransportEvidence).where(TransportEvidence.operation_id.in_(operation_ids)))
        await db.execute(
            delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id.in_(task_ids))
        )
        await db.execute(delete(TransportMember).where(TransportMember.transport_task_id.in_(task_ids)))
        await db.execute(delete(TransportTask).where(TransportTask.transport_task_id.in_(task_ids)))


async def _aggregate_counts(
    sessions: async_sessionmaker[AsyncSession],
    transport_task_id: str,
) -> tuple[int, int, int]:
    async with sessions() as db:
        task_count = await db.scalar(
            select(func.count()).select_from(TransportTask).where(TransportTask.transport_task_id == transport_task_id)
        )
        member_count = await db.scalar(
            select(func.count())
            .select_from(TransportMember)
            .where(TransportMember.transport_task_id == transport_task_id)
        )
        binding_count = await db.scalar(
            select(func.count())
            .select_from(TransportResourceBinding)
            .where(TransportResourceBinding.transport_task_id == transport_task_id)
        )
    return int(task_count or 0), int(member_count or 0), int(binding_count or 0)


async def test_debug_reset_deletes_only_selected_postgresql_aggregate(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    target_id = await _create_reconciling_task(
        integration_session_factory,
        resource_id=f"rack-reset-target-{suffix}",
    )
    keep_id = await _create_reconciling_task(
        integration_session_factory,
        resource_id=f"rack-reset-keep-{suffix}",
    )
    service = _service(integration_session_factory)

    try:
        preview = await service.preview_debug_task_reset(target_id)
        assert (preview.member_count, preview.binding_count, preview.active_binding_count) == (1, 1, 1)

        result = await service.reset_debug_task(target_id)

        assert (
            result.deleted_callback_receipt_count,
            result.deleted_evidence_count,
            result.deleted_position_projection_count,
            result.deleted_member_count,
            result.deleted_binding_count,
        ) == (0, 0, 0, 1, 1)
        assert await _aggregate_counts(integration_session_factory, target_id) == (0, 0, 0)
        assert await _aggregate_counts(integration_session_factory, keep_id) == (1, 1, 1)
    finally:
        await _cleanup(integration_session_factory, task_ids=(target_id, keep_id))


async def test_debug_reset_deletes_postgresql_task_with_evidence_outcome_receipt_and_projection(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    task_id = await _create_reconciling_task(
        integration_session_factory,
        resource_id=f"rack-reset-ineligible-{suffix}",
    )
    operation_id = new_uuid7()
    now = timezone.now_for_db()
    async with integration_session_factory.begin() as db:
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == task_id).with_for_update()
        )
        assert task is not None
        task.outcome_version = 1
        task.outcome_json = {"status": "UNKNOWN"}
        db.add(
            TransportCallbackReceipt(
                operation_id=operation_id,
                operation="transport.task.member_position_changed@v1",
                message_digest="a" * 64,
                message_json={"data": {"transport_task_id": task_id}},
                response_http_status=202,
                response_code="RECEIVED",
                response_timestamp_ms=2,
                response_data_json={"transport_task_id": task_id},
                received_at=now,
            )
        )
        db.add(
            TransportEvidence(
                operation_id=operation_id,
                transport_task_id=task_id,
                operation="transport.task.member_position_changed@v1",
                outcome_revision=None,
                event_timestamp_ms=1,
                message_digest="a" * 64,
                payload_json={"transport_task_id": task_id},
                ack_timestamp_ms=2,
                ack_data_json={"transport_task_id": task_id},
                received_at=now,
            )
        )
        db.add(
            TransportPositionProjection(
                object_type="RACK",
                object_id=f"rack-reset-ineligible-{suffix}",
                position_json={"kind": "RACK_POSITION", "location_code": "TARGET"},
                position_unknown=False,
                arrival_face="A",
                source_operation_id=operation_id,
                source_transport_task_id=task_id,
                updated_at=now,
            )
        )
    service = _service(integration_session_factory)

    try:
        preview = await service.preview_debug_task_reset(task_id)
        assert preview.status == "RECONCILING"
        assert preview.outcome_version == 1
        assert preview.evidence_count == 1
        assert preview.callback_receipt_count == 1
        assert preview.position_projection_count == 1

        result = await service.reset_debug_task(task_id)

        assert (
            result.deleted_callback_receipt_count,
            result.deleted_evidence_count,
            result.deleted_position_projection_count,
            result.deleted_member_count,
            result.deleted_binding_count,
        ) == (1, 1, 1, 1, 1)
        assert await _aggregate_counts(integration_session_factory, task_id) == (0, 0, 0)
        async with integration_session_factory() as db:
            receipt_count = await db.scalar(
                select(func.count())
                .select_from(TransportCallbackReceipt)
                .where(TransportCallbackReceipt.operation_id == operation_id)
            )
            evidence_count = await db.scalar(
                select(func.count())
                .select_from(TransportEvidence)
                .where(TransportEvidence.operation_id == operation_id)
            )
            projection_count = await db.scalar(
                select(func.count())
                .select_from(TransportPositionProjection)
                .where(TransportPositionProjection.source_operation_id == operation_id)
            )
        assert (receipt_count, evidence_count, projection_count) == (0, 0, 0)
    finally:
        await _cleanup(integration_session_factory, task_ids=(task_id,), operation_ids=(operation_id,))


async def test_debug_reset_does_not_delete_another_task_projection_when_operation_id_is_reused(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    target_id = await _create_reconciling_task(
        integration_session_factory,
        resource_id=f"rack-reset-collision-target-{suffix}",
    )
    keep_id = await _create_reconciling_task(
        integration_session_factory,
        resource_id=f"rack-reset-collision-keep-{suffix}",
    )
    operation_id = str(new_uuid7())
    now = timezone.now_for_db()
    async with integration_session_factory.begin() as db:
        db.add_all(
            [
                TransportEvidence(
                    operation_id=operation_id,
                    transport_task_id=target_id,
                    operation="transport.task.member_position_changed@v1",
                    event_timestamp_ms=1,
                    message_digest="a" * 64,
                    payload_json={"transport_task_id": target_id},
                    ack_timestamp_ms=2,
                    ack_data_json={"transport_task_id": target_id},
                    received_at=now,
                ),
                TransportEvidence(
                    operation_id=operation_id,
                    transport_task_id=keep_id,
                    operation=RESULT_OPERATION,
                    outcome_revision=1,
                    event_timestamp_ms=1,
                    message_digest="b" * 64,
                    payload_json={"transport_task_id": keep_id},
                    ack_timestamp_ms=2,
                    ack_data_json={"transport_task_id": keep_id},
                    received_at=now,
                ),
                TransportPositionProjection(
                    object_type="RACK",
                    object_id=f"rack-reset-collision-keep-{suffix}",
                    position_json={"kind": "RACK_POSITION", "location_code": "TARGET"},
                    position_unknown=False,
                    arrival_face="A",
                    source_operation_id=operation_id,
                    source_transport_task_id=keep_id,
                    updated_at=now,
                ),
            ]
        )

    service = _service(integration_session_factory)
    try:
        await service.reset_debug_task(target_id)

        async with integration_session_factory() as db:
            projection_count = await db.scalar(
                select(func.count())
                .select_from(TransportPositionProjection)
                .where(TransportPositionProjection.source_operation_id == operation_id)
            )
        assert projection_count == 1
    finally:
        await _cleanup(
            integration_session_factory,
            task_ids=(target_id, keep_id),
            operation_ids=(operation_id,),
        )


async def test_callback_waiting_on_debug_reset_lock_is_retained_as_missing_task_conflict(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    task_id = await _create_reconciling_task(
        integration_session_factory,
        resource_id=f"rack-reset-callback-{suffix}",
    )
    operation_id = new_uuid7()
    reset_repository = _BlockingResetRepository()
    callback_repository = _CallbackLockProbeRepository()
    reset_service = _service(integration_session_factory, reset_repository)
    callback_service = _service(integration_session_factory, callback_repository)
    reset_call: asyncio.Task[object] | None = None
    callback_call: asyncio.Task[object] | None = None

    try:
        reset_call = asyncio.create_task(reset_service.reset_debug_task(task_id))
        await asyncio.wait_for(reset_repository.task_locked.wait(), timeout=2)
        callback_call = asyncio.create_task(
            record_valid_callback(
                callback_service,
                operation_id=operation_id,
                transport_task_id=task_id,
                operation=RESULT_OPERATION,
                timestamp=1,
                payload={
                    "transport_task_id": task_id,
                    "kind": "RACK_MOVE",
                    "outcome_revision": 1,
                    "rack_id": f"rack-reset-callback-{suffix}",
                    "status": "SUCCEEDED",
                    "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
                    "arrival_face": "A",
                },
            )
        )
        await asyncio.wait_for(callback_repository.task_lookup_started.wait(), timeout=2)
        assert callback_call.done() is False
        reset_repository.release.set()
        await reset_call
        await callback_call
        assert await callback_service.process_pending_evidence(1) == 1

        async with integration_session_factory() as db:
            evidence = await db.scalar(select(TransportEvidence).where(TransportEvidence.operation_id == operation_id))
        assert evidence is not None
        assert (evidence.status, evidence.conflict_code) == ("CONFLICT", "TRANSPORT_TASK_NOT_FOUND")
        assert await _aggregate_counts(integration_session_factory, task_id) == (0, 0, 0)
    finally:
        reset_repository.release.set()
        calls = [call for call in (reset_call, callback_call) if call is not None]
        for call in calls:
            if not call.done():
                call.cancel()
        try:
            if calls:
                await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), timeout=5)
        finally:
            await _cleanup(integration_session_factory, task_ids=(task_id,), operation_ids=(operation_id,))
