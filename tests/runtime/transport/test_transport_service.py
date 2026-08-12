from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.transport.contracts import (
    BinExchangePair,
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackFace,
    RackPosition,
    TransportCaller,
    TransportIdempotencyConflict,
    TransportOutcome,
    TransportResourceConflict,
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
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata

register_required_sqlmodel_metadata()


class FakeProvider:
    def __init__(
        self,
        code: TransportSubmitCode = TransportSubmitCode.RECEIVED,
        *,
        retry_after_ms: int | None = None,
        transport_task_id_override: str | None = None,
    ) -> None:
        self.code = code
        self.retry_after_ms = retry_after_ms
        self.transport_task_id_override = transport_task_id_override
        self.calls: list[str] = []
        self.snapshots: list[dict[str, object]] = []

    async def submit(
        self,
        *,
        operation_id: str,
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult:
        transport_task_id = str(payload["transport_task_id"])
        self.calls.append(transport_task_id)
        self.snapshots.append(
            {
                "operation_id": operation_id,
                "timestamp": timestamp,
                "transport_task_id": transport_task_id,
                "payload": payload,
                "payload_digest": payload_digest,
            }
        )
        return TransportSubmitResult(
            code=self.code,
            transport_task_id=self.transport_task_id_override or transport_task_id,
            retry_after_ms=self.retry_after_ms,
        )


class ResultBeforeAckProvider:
    def __init__(self) -> None:
        self.service: TransportService | None = None

    async def submit(
        self,
        *,
        operation_id: str,
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult:
        transport_task_id = str(payload["transport_task_id"])
        assert self.service is not None
        await self.service.record_evidence(
            operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
            transport_task_id=transport_task_id,
            operation="transport.task.resulted@v1",
            timestamp=1,
            payload={
                "transport_task_id": transport_task_id,
                "kind": "RACK_MOVE",
                "results": [
                    {
                        "object_id": "rack-before-ack",
                        "status": "SUCCEEDED",
                        "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
                        "arrival_face": "A",
                    }
                ],
            },
        )
        await self.service.process_pending_evidence(1)
        return TransportSubmitResult(TransportSubmitCode.RECEIVED, transport_task_id)


class DelayedNotSentProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def submit(
        self,
        *,
        operation_id: str,
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult:
        transport_task_id = str(payload["transport_task_id"])
        self.started.set()
        await self.release.wait()
        return TransportSubmitResult(TransportSubmitCode.NOT_SENT, transport_task_id)


@pytest.fixture
def service(db_engine: object) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return TransportService(sessions, TransportRepository(), FakeProvider())


@pytest_asyncio.fixture(autouse=True)
async def _clean_transport_tables(db_engine: object) -> None:
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


def _caller() -> TransportCaller:
    return TransportCaller("SORTER", "STATION_A")


@pytest.mark.asyncio
async def test_four_public_methods_create_one_reliable_task_each(
    service: TransportService,
    db_engine: object,
) -> None:
    handles = [
        await service.move_rack(new_uuid7(), _caller(), "rack-1", RackPosition("A"), RackPosition("B")),
        await service.move_bins(
            new_uuid7(),
            _caller(),
            (BinMove("bin-1", RackBinSlot("rack-2", "1"), HandoffPosition("IN")),),
        ),
        await service.exchange_bins(
            new_uuid7(),
            _caller(),
            (BinExchangePair("bin-2", RackBinSlot("rack-3", "1"), "bin-3", RackBinSlot("rack-4", "1")),),
        ),
    ]
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportPositionProjection(
                object_type="RACK",
                object_id="rack-5",
                position_json={"kind": "RACK_POSITION", "location_code": "ROTATE"},
                arrival_face="A",
                source_operation_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )
    handles.append(await service.rotate_rack(new_uuid7(), _caller(), "rack-5", RackPosition("ROTATE"), RackFace.B))

    assert len({handle.transport_task_id for handle in handles}) == 4


@pytest.mark.asyncio
async def test_same_client_request_is_idempotent_but_changed_payload_conflicts(
    service: TransportService,
    db_engine: object,
) -> None:
    request_id = new_uuid7()
    first = await service.move_rack(request_id, _caller(), "rack-idempotent", RackPosition("A"), RackPosition("B"))
    duplicate = await service.move_rack(request_id, _caller(), "rack-idempotent", RackPosition("A"), RackPosition("B"))

    assert duplicate == first
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task_ids = list(
            await db.scalars(
                select(TransportTask.transport_task_id).where(TransportTask.client_request_id == request_id)
            )
        )
    assert task_ids == [first.transport_task_id]
    with pytest.raises(TransportIdempotencyConflict):
        await service.move_rack(request_id, _caller(), "rack-idempotent", RackPosition("A"), RackPosition("C"))


@pytest.mark.asyncio
async def test_rotate_retry_returns_original_handle_after_projection_reaches_target_face(
    service: TransportService,
    db_engine: object,
) -> None:
    request_id = new_uuid7()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        db.add(
            TransportPositionProjection(
                object_type="RACK",
                object_id="rack-rotate-idempotent",
                position_json={"kind": "RACK_POSITION", "location_code": "ROTATE"},
                arrival_face="A",
                source_operation_id="seed",
                updated_at=timezone.now_for_db(),
            )
        )
    first = await service.rotate_rack(
        request_id,
        _caller(),
        "rack-rotate-idempotent",
        RackPosition("ROTATE"),
        RackFace.B,
    )
    async with sessions.begin() as db:
        await db.execute(
            update(TransportPositionProjection)
            .where(TransportPositionProjection.object_id == "rack-rotate-idempotent")
            .values(arrival_face="B")
        )

    duplicate = await service.rotate_rack(
        request_id,
        _caller(),
        "rack-rotate-idempotent",
        RackPosition("ROTATE"),
        RackFace.B,
    )

    assert duplicate == first


@pytest.mark.asyncio
async def test_active_resource_binding_rejects_overlapping_task(service: TransportService) -> None:
    await service.move_rack(new_uuid7(), _caller(), "rack-resource", RackPosition("A"), RackPosition("B"))

    with pytest.raises(TransportResourceConflict):
        await service.move_bins(
            new_uuid7(),
            _caller(),
            (BinMove("bin-resource", RackBinSlot("rack-resource", "1"), HandoffPosition("IN")),),
        )


@pytest.mark.asyncio
async def test_submit_received_sets_acceptance_and_does_not_resend(
    service: TransportService,
    db_engine: object,
) -> None:
    provider = service.provider
    handle = await service.move_rack(new_uuid7(), _caller(), "rack-submit", RackPosition("A"), RackPosition("B"))

    assert await service.submit_pending_tasks(10) == 1
    assert await service.submit_pending_tasks(10) == 0
    assert provider.calls == [handle.transport_task_id]
    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.status == "ACCEPTED"
    assert snapshot.submit_attempt_count == 1
    assert snapshot.result_deadline_at is not None


@pytest.mark.asyncio
async def test_delivery_unknown_enters_reconciling_and_keeps_resource(
    service: TransportService,
    db_engine: object,
) -> None:
    service.provider.code = TransportSubmitCode.DELIVERY_UNKNOWN
    handle = await service.move_rack(new_uuid7(), _caller(), "rack-unknown", RackPosition("A"), RackPosition("B"))

    assert await service.submit_pending_tasks(1) == 1
    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.status == "RECONCILING"
    assert snapshot.outcome_version == 1
    with pytest.raises(TransportResourceConflict):
        await service.move_rack(new_uuid7(), _caller(), "rack-unknown", RackPosition("B"), RackPosition("C"))


@pytest.mark.asyncio
async def test_submit_result_with_foreign_task_id_fails_closed_and_keeps_resource(
    service: TransportService,
    db_engine: object,
) -> None:
    service.provider.code = TransportSubmitCode.REJECTED
    service.provider.transport_task_id_override = "transport-foreign"
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-foreign-ack",
        RackPosition("A"),
        RackPosition("B"),
    )

    assert await service.submit_pending_tasks(1) == 1
    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.status == "RECONCILING"
    assert snapshot.reason_code == "TRANSPORT_SUBMIT_CONFLICT"
    with pytest.raises(TransportResourceConflict):
        await service.move_rack(
            new_uuid7(),
            _caller(),
            "rack-foreign-ack",
            RackPosition("B"),
            RackPosition("C"),
        )


@pytest.mark.asyncio
async def test_expired_claim_after_send_started_reconciles_without_resend(
    service: TransportService,
    db_engine: object,
) -> None:
    handle = await service.move_rack(new_uuid7(), _caller(), "rack-crash", RackPosition("A"), RackPosition("B"))
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    expired = timezone.now_for_db()
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(send_started_at=expired, submit_claim_until=expired, submit_claim_token="dead-worker")
        )

    assert await service.reconcile_overdue_tasks(1) == 1
    assert service.provider.calls == []
    assert (await _load_task(db_engine, handle.transport_task_id)).status == "RECONCILING"


@pytest.mark.asyncio
async def test_late_deterministic_ack_converges_after_claim_expiry(db_engine: object) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    provider = DelayedNotSentProvider()
    service = TransportService(sessions, TransportRepository(), provider)
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-late-not-sent",
        RackPosition("A"),
        RackPosition("B"),
    )
    submit = asyncio.create_task(service.submit_pending_tasks(1))
    await provider.started.wait()
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(submit_claim_until=timezone.now_for_db() - timedelta(seconds=1))
        )

    assert await service.reconcile_overdue_tasks(1) == 1
    provider.release.set()
    assert await submit == 1

    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.status == "RECONCILING"
    assert snapshot.reason_code == "TRANSPORT_DELIVERY_UNKNOWN"
    assert snapshot.send_started_at is not None
    assert snapshot.next_submit_at is None
    assert snapshot.outcome_json is not None


@pytest.mark.asyncio
async def test_result_arriving_during_submit_is_not_regressed_by_late_ack(db_engine: object) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    provider = ResultBeforeAckProvider()
    service = TransportService(sessions, TransportRepository(), provider)
    provider.service = service
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-before-ack",
        RackPosition("A"),
        RackPosition("B"),
    )

    assert await service.submit_pending_tasks(1) == 1
    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.status == "SUCCEEDED"
    assert snapshot.outcome_version == 1


@pytest.mark.asyncio
async def test_expired_claim_without_send_start_is_reclaimed(service: TransportService, db_engine: object) -> None:
    handle = await service.move_rack(new_uuid7(), _caller(), "rack-reclaim", RackPosition("A"), RackPosition("B"))
    expired = timezone.now_for_db() - timedelta(seconds=1)
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(submit_claim_token="dead-worker", submit_claim_until=expired)
        )

    assert await service.submit_pending_tasks(1) == 1
    assert service.provider.calls == [handle.transport_task_id]


@pytest.mark.asyncio
async def test_confirmed_not_sent_stops_after_three_attempts_and_releases_resource(
    service: TransportService,
    db_engine: object,
) -> None:
    service.provider.code = TransportSubmitCode.NOT_SENT
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-retry-budget",
        RackPosition("A"),
        RackPosition("B"),
    )
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    for attempt in range(3):
        assert await service.submit_pending_tasks(1) == 1
        if attempt < 2:
            async with sessions.begin() as db:
                await db.execute(
                    update(TransportTask)
                    .where(TransportTask.transport_task_id == handle.transport_task_id)
                    .values(next_submit_at=timezone.now_for_db() - timedelta(seconds=1))
                )

    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.submit_attempt_count == 3
    assert snapshot.status == "REJECTED"
    assert len(service.provider.calls) == 3
    assert await service.submit_pending_tasks(1) == 0
    replacement = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-retry-budget",
        RackPosition("A"),
        RackPosition("C"),
    )
    assert replacement.transport_task_id != handle.transport_task_id


@pytest.mark.asyncio
async def test_busy_uses_positive_retry_after_from_ack(service: TransportService, db_engine: object) -> None:
    service.provider.code = TransportSubmitCode.BUSY
    service.provider.retry_after_ms = 1500
    handle = await service.move_rack(new_uuid7(), _caller(), "rack-busy", RackPosition("A"), RackPosition("B"))

    assert await service.submit_pending_tasks(1) == 1
    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.next_submit_at is not None
    assert snapshot.updated_at + timedelta(milliseconds=1500) == snapshot.next_submit_at


@pytest.mark.asyncio
async def test_busy_with_unrepresentable_retry_after_falls_back_without_stranding_task(
    service: TransportService,
    db_engine: object,
) -> None:
    service.provider.code = TransportSubmitCode.BUSY
    service.provider.retry_after_ms = 10**20
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        "rack-busy-overflow",
        RackPosition("A"),
        RackPosition("B"),
    )

    assert await service.submit_pending_tasks(1) == 1
    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.status == "PENDING"
    assert snapshot.send_started_at is None
    assert snapshot.next_submit_at == snapshot.updated_at + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_accepted_result_deadline_is_frozen_and_overdue_task_becomes_unknown(
    service: TransportService,
    db_engine: object,
) -> None:
    handle = await service.move_bins(
        new_uuid7(),
        _caller(),
        (BinMove("bin-deadline", RackBinSlot("rack-deadline", "1"), HandoffPosition("ROLLER_IN")),),
    )
    await service.submit_pending_tasks(1)
    accepted = await _load_task(db_engine, handle.transport_task_id)
    assert accepted.result_deadline_at is not None
    await service.record_evidence(
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "bin_id": "bin-deadline",
            "milestone": "SOURCE_PICKED",
        },
    )
    await service.process_pending_evidence(1)
    after_position = await _load_task(db_engine, handle.transport_task_id)
    assert after_position.result_deadline_at == accepted.result_deadline_at

    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(result_deadline_at=timezone.now_for_db() - timedelta(seconds=1))
        )
    assert await service.reconcile_overdue_tasks(1) == 1
    reconciled = await _load_task(db_engine, handle.transport_task_id)
    assert reconciled.status == "RECONCILING"
    assert reconciled.reason_code == "TRANSPORT_RESULT_TIMEOUT"
    assert reconciled.outcome_version == 1


@pytest.mark.asyncio
async def test_position_fact_converges_delivery_unknown_to_accepted(
    service: TransportService,
    db_engine: object,
) -> None:
    service.provider.code = TransportSubmitCode.DELIVERY_UNKNOWN
    handle = await service.move_bins(
        new_uuid7(),
        _caller(),
        (BinMove("bin-late-position", RackBinSlot("rack-late-position", "1"), HandoffPosition("OUT")),),
    )
    assert await service.submit_pending_tasks(1) == 1
    unknown = await _load_task(db_engine, handle.transport_task_id)
    assert (unknown.status, unknown.reason_code, unknown.result_deadline_at) == (
        "RECONCILING",
        "TRANSPORT_DELIVERY_UNKNOWN",
        None,
    )

    await service.record_evidence(
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4474",
        transport_task_id=handle.transport_task_id,
        operation="transport.task.member_position_changed@v1",
        timestamp=1,
        payload={
            "transport_task_id": handle.transport_task_id,
            "bin_id": "bin-late-position",
            "milestone": "SOURCE_PICKED",
        },
    )
    assert await service.process_pending_evidence(1) == 1

    accepted = await _load_task(db_engine, handle.transport_task_id)
    assert accepted.status == "ACCEPTED"
    assert accepted.reason_code is None
    assert accepted.result_deadline_at is not None
    assert accepted.outcome_json is None


@pytest.mark.asyncio
async def test_local_client_identity_freezes_a_distinct_submit_snapshot_before_retry(
    service: TransportService,
    db_engine: object,
) -> None:
    service.provider.code = TransportSubmitCode.NOT_SENT
    handle = await service.move_rack(
        "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
        TransportCaller("SORTER", "STATION_A"),
        "rack-submit-snapshot",
        RackPosition("A"),
        RackPosition("B"),
    )
    first = await _load_task(db_engine, handle.transport_task_id)
    expected_payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "RACK_MOVE",
        "rack_id": "rack-submit-snapshot",
        "source": {"kind": "RACK_POSITION", "location_code": "A"},
        "target": {"kind": "RACK_POSITION", "location_code": "B"},
    }

    assert first.client_request_id == "019f12d0-58d7-7b4d-a23a-1b90aa5d4471"
    assert _is_uuid7(getattr(first, "submit_operation_id", None))
    assert getattr(first, "submit_operation_id", None) != first.client_request_id
    assert isinstance(getattr(first, "submit_timestamp_ms", None), int)
    assert getattr(first, "submit_payload_json", None) == expected_payload
    assert getattr(first, "submit_payload_digest", None) == _submit_payload_digest(
        first.submit_operation_id,
        first.submit_timestamp_ms,
        expected_payload,
    )

    assert await service.submit_pending_tasks(1) == 1
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == handle.transport_task_id)
            .values(next_submit_at=timezone.now_for_db() - timedelta(seconds=1))
        )
    assert await service.submit_pending_tasks(1) == 1

    retry = await _load_task(db_engine, handle.transport_task_id)
    assert (
        retry.submit_operation_id,
        retry.submit_timestamp_ms,
        retry.submit_payload_json,
        retry.submit_payload_digest,
    ) == (
        first.submit_operation_id,
        first.submit_timestamp_ms,
        first.submit_payload_json,
        first.submit_payload_digest,
    )
    provider = service.provider
    assert provider.snapshots == [
        {
            "operation_id": first.submit_operation_id,
            "timestamp": first.submit_timestamp_ms,
            "transport_task_id": handle.transport_task_id,
            "payload": first.submit_payload_json,
            "payload_digest": first.submit_payload_digest,
        },
        {
            "operation_id": first.submit_operation_id,
            "timestamp": first.submit_timestamp_ms,
            "transport_task_id": handle.transport_task_id,
            "payload": first.submit_payload_json,
            "payload_digest": first.submit_payload_digest,
        },
    ]


async def _load_task(db_engine: object, transport_task_id: str) -> TransportTask:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == transport_task_id))
        assert task is not None
        return task


def _is_uuid7(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        from uuid import UUID

        candidate = UUID(value)
    except ValueError:
        return False
    return candidate.version == 7 and candidate.variant == "specified in RFC 4122"


def _submit_payload_digest(operation_id: str, timestamp: int, payload: dict[str, object]) -> str:
    normalized = json.dumps(
        {
            "operation_id": operation_id,
            "operation": "transport.task.submit@v1",
            "timestamp": timestamp,
            "data": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(normalized).hexdigest()
