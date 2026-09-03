from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, select, update

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceConflict,
    InboundEvidenceKind,
)
from src.app.transport.debug_run_contracts import TransportDebugRunPhase
from src.app.transport.debug_run_repository import TransportDebugRunRepository
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportDebugRun,
    TransportDebugRunStep,
    TransportEvidence,
    TransportTask,
)
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.asyncio


def _aggregate(run_id: str) -> tuple[TransportDebugRun, TransportDebugRunStep]:
    now = timezone.now_for_db()
    run = TransportDebugRun(
        run_id=run_id,
        status="RUNNING",
        active_scope="GLOBAL",
        rack_id="510056",
        configuration_json={"rack_id": "510056", "face_groups": []},
        current_phase=TransportDebugRunPhase.RACK_TO_STATION.value,
        created_by_user_id=1,
        created_at=now,
        updated_at=now,
    )
    step = TransportDebugRunStep(
        run_id=run_id,
        ordinal=0,
        phase=TransportDebugRunPhase.RACK_TO_STATION.value,
        status="PENDING",
        client_request_id=new_uuid7(),
        created_at=now,
        updated_at=now,
    )
    return run, step


async def test_debug_run_repository_claims_once_and_recovers_expired_lease(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    run_id = f"debug-run-{uuid.uuid4().hex}"
    now = timezone.now_for_db()
    run, step = _aggregate(run_id)
    async with integration_session_factory.begin() as setup_db:
        await repository.add_run(setup_db, run, step)

    try:
        async with integration_session_factory.begin() as first_db:
            first = await repository.claim_active_runs(
                first_db,
                token="worker-1",
                now=now,
                claim_until=now + timedelta(seconds=30),
                limit=1,
            )
            async with integration_session_factory.begin() as second_db:
                second = await repository.claim_active_runs(
                    second_db,
                    token="worker-2",
                    now=now,
                    claim_until=now + timedelta(seconds=30),
                    limit=1,
                )
        async with integration_session_factory.begin() as recovered_db:
            recovered = await repository.claim_active_runs(
                recovered_db,
                token="worker-3",
                now=now + timedelta(seconds=31),
                claim_until=now + timedelta(seconds=61),
                limit=1,
            )

        assert first == [(run_id, "worker-1")]
        assert second == []
        assert recovered == [(run_id, "worker-3")]
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))


async def test_debug_run_repository_reads_current_step_and_stable_history(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    run_id = f"debug-history-{uuid.uuid4().hex}"
    run, first_step = _aggregate(run_id)
    now = timezone.now_for_db()
    second_step = TransportDebugRunStep(
        run_id=run_id,
        ordinal=1,
        phase=TransportDebugRunPhase.WAIT_SCAN12.value,
        status="WAITING",
        evidence_high_watermark=100,
        evidence_not_before_ms=1_725_000_000_000,
        created_at=now,
        updated_at=now,
    )
    async with integration_session_factory.begin() as setup_db:
        await repository.add_run(setup_db, run, first_step)
        await repository.add_step(setup_db, second_step)
        run.current_step_ordinal = 1

    try:
        async with integration_session_factory() as db:
            stored_run = await repository.get_run(db, run_id)
            assert stored_run is not None
            current = await repository.get_current_step(db, stored_run)
            history = await repository.list_steps(db, run_id)

        assert current is not None and current.ordinal == 1
        assert [item.ordinal for item in history] == [0, 1]
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))


async def test_list_current_steps_uses_the_frozen_parent_cursor(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    run_id = f"debug-list-snapshot-{uuid.uuid4().hex}"
    run, first_step = _aggregate(run_id)
    second_step = TransportDebugRunStep(
        run_id=run_id,
        ordinal=1,
        phase=TransportDebugRunPhase.WAIT_SCAN12.value,
        status="WAITING",
        evidence_high_watermark=100,
        evidence_not_before_ms=1_725_000_000_000,
        created_at=timezone.now_for_db(),
        updated_at=timezone.now_for_db(),
    )
    async with integration_session_factory.begin() as setup_db:
        await repository.add_run(setup_db, run, first_step)
        await repository.add_step(setup_db, second_step)

    try:
        async with integration_session_factory() as read_db:
            frozen_runs = await repository.list_recent_runs(read_db, limit=100)
            frozen = next(item for item in frozen_runs if item.run_id == run_id)
            assert frozen.current_step_ordinal == 0
            async with integration_session_factory.begin() as update_db:
                await update_db.execute(
                    update(TransportDebugRun)
                    .where(TransportDebugRun.run_id == run_id)
                    .values(current_step_ordinal=1, current_phase=TransportDebugRunPhase.WAIT_SCAN12.value)
                )
            current_steps = await repository.list_current_steps(read_db, [frozen])

        assert current_steps[run_id].ordinal == 0
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))


async def test_debug_run_attention_fences_pending_transport_dispatch(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    suffix = uuid.uuid4().hex
    run_id = f"debug-dispatch-fence-{suffix}"
    task_id = f"transport-dispatch-fence-{suffix}"
    now = timezone.now_for_db()
    run, step = _aggregate(run_id)
    run.status = "NEEDS_ATTENTION"
    run.attention_code = "EVIDENCE_SOURCE_EVENT_CONFLICT"
    step.status = "NEEDS_ATTENTION"
    step.transport_task_id = task_id
    task = TransportTask(
        transport_task_id=task_id,
        client_request_id=step.client_request_id or new_uuid7(),
        request_digest="0" * 64,
        kind="BIN_MOVE",
        caller_json={"workline_id": "TRANSPORT_DEBUG"},
        request_json={},
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status="PENDING",
        created_at=now,
        updated_at=now,
    )
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(task)
        await setup_db.flush()
        await repository.add_run(setup_db, run, step)

    try:
        async with integration_session_factory.begin() as db:
            assert await repository.is_task_dispatch_allowed(db, task_id) is False
            assert await repository.is_task_dispatch_allowed(db, f"unrelated-{suffix}") is True
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
            await cleanup_db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))


async def test_dispatch_guard_does_not_wait_on_abort_run_row_lock(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    suffix = uuid.uuid4().hex
    run_id = f"debug-dispatch-lock-order-{suffix}"
    task_id = f"transport-dispatch-lock-order-{suffix}"
    now = timezone.now_for_db()
    run, step = _aggregate(run_id)
    step.transport_task_id = task_id
    task = TransportTask(
        transport_task_id=task_id,
        client_request_id=step.client_request_id or new_uuid7(),
        request_digest="0" * 64,
        kind="BIN_MOVE",
        caller_json={"workline_id": "TRANSPORT_DEBUG"},
        request_json={},
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status="PENDING",
        created_at=now,
        updated_at=now,
    )
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(task)
        await setup_db.flush()
        await repository.add_run(setup_db, run, step)

    try:
        async with integration_session_factory.begin() as abort_db:
            locked = await repository.get_run(abort_db, run_id, for_update=True)
            assert locked is not None
            async with integration_session_factory.begin() as dispatch_db:
                assert (
                    await asyncio.wait_for(
                        repository.is_task_dispatch_allowed(dispatch_db, task_id),
                        timeout=0.5,
                    )
                    is True
                )
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
            await cleanup_db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))


async def test_committed_transport_conflict_fences_dispatch_before_run_scanner_updates_status(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    suffix = uuid.uuid4().hex
    run_id = f"debug-transport-conflict-fence-{suffix}"
    task_id = f"transport-conflict-fence-{suffix}"
    now = timezone.now_for_db()
    run, step = _aggregate(run_id)
    step.transport_task_id = task_id
    task = TransportTask(
        transport_task_id=task_id,
        client_request_id=step.client_request_id or new_uuid7(),
        request_digest="0" * 64,
        kind="BIN_MOVE",
        caller_json={"workline_id": "TRANSPORT_DEBUG"},
        request_json={},
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status="PENDING",
        created_at=now,
        updated_at=now,
    )
    evidence = TransportEvidence(
        operation_id=new_uuid7(),
        transport_task_id=task_id,
        operation="transport.task.resulted@v1",
        outcome_revision=1,
        event_timestamp_ms=1,
        message_digest="a" * 64,
        payload_json={},
        ack_timestamp_ms=1,
        ack_data_json={},
        status="CONFLICT",
        received_at=now,
    )
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(task)
        await setup_db.flush()
        await repository.add_run(setup_db, run, step)
        setup_db.add(evidence)

    try:
        async with integration_session_factory.begin() as db:
            assert await repository.is_task_dispatch_allowed(db, task_id) is False
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(delete(TransportEvidence).where(TransportEvidence.transport_task_id == task_id))
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
            await cleanup_db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))


async def test_pending_transport_evidence_fences_dispatch_until_processing_finishes(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    suffix = uuid.uuid4().hex
    run_id = f"debug-pending-evidence-fence-{suffix}"
    task_id = f"transport-pending-evidence-fence-{suffix}"
    now = timezone.now_for_db()
    run, step = _aggregate(run_id)
    step.transport_task_id = task_id
    task = TransportTask(
        transport_task_id=task_id,
        client_request_id=step.client_request_id or new_uuid7(),
        request_digest="0" * 64,
        kind="RACK_MOVE",
        caller_json={"workline_id": "TRANSPORT_DEBUG"},
        request_json={},
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status="PENDING",
        created_at=now,
        updated_at=now,
    )
    evidence = TransportEvidence(
        operation_id=new_uuid7(),
        transport_task_id=task_id,
        operation="transport.task.resulted@v1",
        outcome_revision=1,
        event_timestamp_ms=1,
        message_digest="a" * 64,
        payload_json={},
        ack_timestamp_ms=1,
        ack_data_json={},
        status="PENDING",
        received_at=now,
    )
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(task)
        await setup_db.flush()
        await repository.add_run(setup_db, run, step)
        setup_db.add(evidence)

    try:
        async with integration_session_factory.begin() as db:
            assert await repository.has_pending_transport_evidence(db, run_id) is True
            assert await repository.is_task_dispatch_allowed(db, task_id) is False
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(delete(TransportEvidence).where(TransportEvidence.transport_task_id == task_id))
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
            await cleanup_db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))


async def test_committed_callback_receipt_conflict_fences_dispatch_and_advancement(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    suffix = uuid.uuid4().hex
    run_id = f"debug-receipt-conflict-fence-{suffix}"
    task_id = f"transport-receipt-conflict-fence-{suffix}"
    now = timezone.now_for_db()
    run, step = _aggregate(run_id)
    step.transport_task_id = task_id
    task = TransportTask(
        transport_task_id=task_id,
        client_request_id=step.client_request_id or new_uuid7(),
        request_digest="0" * 64,
        kind="RACK_MOVE",
        caller_json={"workline_id": "TRANSPORT_DEBUG"},
        request_json={},
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status="PENDING",
        created_at=now,
        updated_at=now,
    )
    receipt = TransportCallbackReceipt(
        operation_id=new_uuid7(),
        operation="transport.task.resulted@v1",
        message_digest="a" * 64,
        message_json={},
        response_http_status=409,
        response_code="CONFLICT",
        response_timestamp_ms=1,
        response_data_json={"transport_task_id": task_id},
        received_at=now,
    )
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(task)
        await setup_db.flush()
        await repository.add_run(setup_db, run, step)
        setup_db.add(receipt)

    try:
        async with integration_session_factory.begin() as db:
            assert await repository.has_transport_evidence_conflict(db, run_id) is True
            assert await repository.is_task_dispatch_allowed(db, task_id) is False
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == receipt.operation_id)
            )
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
            await cleanup_db.execute(delete(TransportTask).where(TransportTask.transport_task_id == task_id))


async def test_committed_scan12_conflict_fences_dispatch_before_run_scanner_updates_status(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = TransportDebugRunRepository()
    suffix = uuid.uuid4().hex
    run_id = f"debug-scan-conflict-fence-{suffix}"
    task_id = f"transport-scan-conflict-fence-{suffix}"
    source_id = f"scan-conflict-{suffix}"
    now = timezone.now_for_db()
    run, step = _aggregate(run_id)
    step.transport_task_id = task_id
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=source_id,
        payload_digest="a" * 64,
        normalized_payload={"source_event_id": source_id, "data": {"barcode": "BIN-1"}},
        received_at=now,
        device_code="SCAN12",
        contract_key="device.event",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(evidence)
        await setup_db.flush()
        assert evidence.id is not None
        step.observed_bins_json = [{"bin_id": "BIN-1", "evidence_id": evidence.id, "source_event_id": source_id}]
        await repository.add_run(setup_db, run, step)
        setup_db.add(
            InboundEvidenceConflict(
                source_identity=source_id,
                first_evidence_id=evidence.id,
                conflicting_digest="b" * 64,
                normalized_payload={"source_event_id": source_id, "data": {"barcode": "OTHER"}},
                reason_code="SOURCE_IDENTITY_PAYLOAD_CONFLICT",
                received_at=now,
            )
        )

    try:
        async with integration_session_factory.begin() as db:
            assert await repository.is_task_dispatch_allowed(db, task_id) is False
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            await cleanup_db.execute(
                delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id == evidence.id)
            )
            await cleanup_db.execute(delete(TransportDebugRunStep).where(TransportDebugRunStep.run_id == run_id))
            await cleanup_db.execute(delete(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
            await cleanup_db.execute(delete(InboundEvidence).where(InboundEvidence.id == evidence.id))
