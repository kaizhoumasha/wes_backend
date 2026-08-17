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
    TransportCallbackReceipt,
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
from tests.support.transport_projections import confirm_rack_faces

register_required_sqlmodel_metadata()


class FakeProvider:
    def __init__(
        self,
        code: TransportSubmitCode = TransportSubmitCode.RECEIVED,
        *,
        transport_task_id_override: str | None = None,
    ) -> None:
        self.code = code
        self.transport_task_id_override = transport_task_id_override
        self.calls: list[str] = []
        self.snapshots: list[dict[str, object]] = []

    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
    ) -> TransportSubmitResult:
        envelope = json.loads(request_body)
        self.calls.append(transport_task_id)
        self.snapshots.append(
            {
                "operation_id": operation_id,
                "transport_task_id": transport_task_id,
                "request_body": request_body,
                "request_body_digest": request_body_digest,
                "envelope": envelope,
            }
        )
        return TransportSubmitResult(
            code=self.code,
            transport_task_id=self.transport_task_id_override or transport_task_id,
        )


class ResultBeforeAckProvider:
    def __init__(self) -> None:
        self.service: TransportService | None = None

    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
    ) -> TransportSubmitResult:
        assert self.service is not None
        message = {
            "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
            "operation": "transport.task.resulted@v1",
            "timestamp": 1,
            "data": {
                "transport_task_id": transport_task_id,
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": "rack-before-ack",
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
                "arrival_face": "A",
            },
        }
        await self.service.record_callback(
            operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
            operation="transport.task.resulted@v1",
            message=message,
            payload=message["data"],
            rejection_reason_code=None,
        )
        await self.service.process_pending_evidence(1)
        return TransportSubmitResult(TransportSubmitCode.RECEIVED, transport_task_id)


class DelayedNotSentProvider:
    def __init__(self, code: TransportSubmitCode = TransportSubmitCode.NOT_SENT) -> None:
        self.code = code
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
    ) -> TransportSubmitResult:
        self.started.set()
        await self.release.wait()
        return TransportSubmitResult(self.code, transport_task_id)


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
            TransportCallbackReceipt,
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
    await confirm_rack_faces(
        db_engine,
        {"rack-2": RackFace.A, "rack-3": RackFace.A, "rack-4": RackFace.A},
    )
    handles = [
        await service.move_rack(
            new_uuid7(),
            _caller(),
            "rack-1",
            RackPosition("A"),
            RackPosition("B"),
            RackFace.A,
        ),
        await service.move_bins(
            new_uuid7(),
            _caller(),
            (BinMove("bin-1", RackBinSlot("rack-2", RackFace.A, "1"), HandoffPosition("IN")),),
        ),
        await service.exchange_bins(
            new_uuid7(),
            _caller(),
            (
                BinExchangePair(
                    "bin-2",
                    RackBinSlot("rack-3", RackFace.A, "1"),
                    "bin-3",
                    RackBinSlot("rack-4", RackFace.A, "1"),
                ),
            ),
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
    first = await service.move_rack(
        request_id, _caller(), "rack-idempotent", RackPosition("A"), RackPosition("B"), RackFace.A
    )
    duplicate = await service.move_rack(
        request_id, _caller(), "rack-idempotent", RackPosition("A"), RackPosition("B"), RackFace.A
    )

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
        await service.move_rack(
            request_id, _caller(), "rack-idempotent", RackPosition("A"), RackPosition("C"), RackFace.A
        )


@pytest.mark.asyncio
async def test_move_rack_can_join_a_caller_owned_transaction(
    service: TransportService,
    db_engine: object,
) -> None:
    request_id = new_uuid7()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with sessions.begin() as db:
        handle = await service.move_rack_in_session(
            db,
            request_id,
            _caller(),
            "rack-same-session",
            RackPosition("A"),
            RackPosition("B"),
            RackFace.A,
        )
        persisted = await db.scalar(select(TransportTask).where(TransportTask.client_request_id == request_id))

    assert persisted is not None
    assert persisted.transport_task_id == handle.transport_task_id


@pytest.mark.asyncio
async def test_move_bins_idempotency_ignores_member_input_order(
    service: TransportService,
    db_engine: object,
) -> None:
    request_id = new_uuid7()
    await confirm_rack_faces(db_engine, {"rack-bin-order": RackFace.A})
    moves = (
        BinMove("bin-order-1", RackBinSlot("rack-bin-order", RackFace.A, "1"), HandoffPosition("IN")),
        BinMove("bin-order-2", RackBinSlot("rack-bin-order", RackFace.A, "2"), HandoffPosition("OUT")),
    )

    first = await service.move_bins(request_id, _caller(), moves)
    duplicate = await service.move_bins(request_id, _caller(), tuple(reversed(moves)))

    assert duplicate == first


@pytest.mark.asyncio
async def test_exchange_idempotency_ignores_pair_order_and_left_right_orientation(
    service: TransportService,
    db_engine: object,
) -> None:
    request_id = new_uuid7()
    await confirm_rack_faces(db_engine, {"rack-exchange-left": RackFace.A, "rack-exchange-right": RackFace.B})
    pairs = (
        BinExchangePair(
            "bin-left-1",
            RackBinSlot("rack-exchange-left", RackFace.A, "1"),
            "bin-right-1",
            RackBinSlot("rack-exchange-right", RackFace.B, "1"),
        ),
        BinExchangePair(
            "bin-left-2",
            RackBinSlot("rack-exchange-left", RackFace.A, "2"),
            "bin-right-2",
            RackBinSlot("rack-exchange-right", RackFace.B, "2"),
        ),
    )
    equivalent = tuple(
        BinExchangePair(pair.right_bin_id, pair.right_location, pair.left_bin_id, pair.left_location)
        for pair in reversed(pairs)
    )

    first = await service.exchange_bins(request_id, _caller(), pairs)
    duplicate = await service.exchange_bins(request_id, _caller(), equivalent)

    assert duplicate == first


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
async def test_active_resource_binding_rejects_overlapping_task(
    service: TransportService,
    db_engine: object,
) -> None:
    await confirm_rack_faces(db_engine, {"rack-resource": RackFace.A})
    await service.move_rack(new_uuid7(), _caller(), "rack-resource", RackPosition("A"), RackPosition("B"), RackFace.A)

    with pytest.raises(TransportResourceConflict):
        await service.move_bins(
            new_uuid7(),
            _caller(),
            (BinMove("bin-resource", RackBinSlot("rack-resource", RackFace.A, "1"), HandoffPosition("IN")),),
        )


@pytest.mark.asyncio
async def test_submit_received_sets_acceptance_and_does_not_resend(
    service: TransportService,
    db_engine: object,
) -> None:
    provider = service.provider
    handle = await service.move_rack(
        new_uuid7(), _caller(), "rack-submit", RackPosition("A"), RackPosition("B"), RackFace.A
    )

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
    handle = await service.move_rack(
        new_uuid7(), _caller(), "rack-unknown", RackPosition("A"), RackPosition("B"), RackFace.A
    )

    assert await service.submit_pending_tasks(1) == 1
    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.status == "RECONCILING"
    assert snapshot.outcome_version == 1
    with pytest.raises(TransportResourceConflict):
        await service.move_rack(
            new_uuid7(), _caller(), "rack-unknown", RackPosition("B"), RackPosition("C"), RackFace.A
        )


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
        RackFace.A,
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
            RackFace.A,
        )


@pytest.mark.asyncio
async def test_expired_claim_after_send_started_reconciles_without_resend(
    service: TransportService,
    db_engine: object,
) -> None:
    handle = await service.move_rack(
        new_uuid7(), _caller(), "rack-crash", RackPosition("A"), RackPosition("B"), RackFace.A
    )
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
        RackFace.A,
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
@pytest.mark.parametrize(
    ("code", "expected_status", "expected_reason", "resource_released"),
    (
        (TransportSubmitCode.REJECTED, "REJECTED", "TRANSPORT_REJECTED", True),
        (TransportSubmitCode.CONFLICT, "RECONCILING", "TRANSPORT_SUBMIT_CONFLICT", False),
    ),
)
async def test_late_deterministic_negative_ack_converges_after_delivery_unknown(
    db_engine: object,
    code: TransportSubmitCode,
    expected_status: str,
    expected_reason: str,
    resource_released: bool,
) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    provider = DelayedNotSentProvider(code)
    service = TransportService(sessions, TransportRepository(), provider)
    rack_id = f"rack-late-{code.value.lower()}"
    handle = await service.move_rack(
        new_uuid7(),
        _caller(),
        rack_id,
        RackPosition("A"),
        RackPosition("B"),
        RackFace.A,
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
    assert (snapshot.status, snapshot.reason_code) == (expected_status, expected_reason)
    if resource_released:
        await service.move_rack(new_uuid7(), _caller(), rack_id, RackPosition("B"), RackPosition("C"), RackFace.A)
    else:
        with pytest.raises(TransportResourceConflict):
            await service.move_rack(new_uuid7(), _caller(), rack_id, RackPosition("B"), RackPosition("C"), RackFace.A)


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
        RackFace.A,
    )

    assert await service.submit_pending_tasks(1) == 1
    snapshot = await _load_task(db_engine, handle.transport_task_id)
    assert snapshot.status == "SUCCEEDED"
    assert snapshot.outcome_version == 1


@pytest.mark.asyncio
async def test_expired_claim_without_send_start_is_reclaimed(service: TransportService, db_engine: object) -> None:
    handle = await service.move_rack(
        new_uuid7(), _caller(), "rack-reclaim", RackPosition("A"), RackPosition("B"), RackFace.A
    )
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
        RackFace.A,
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
        RackFace.A,
    )
    assert replacement.transport_task_id != handle.transport_task_id


@pytest.mark.asyncio
async def test_accepted_result_deadline_is_frozen_and_overdue_task_becomes_unknown(
    service: TransportService,
    db_engine: object,
) -> None:
    await confirm_rack_faces(db_engine, {"rack-deadline": RackFace.A})
    handle = await service.move_bins(
        new_uuid7(),
        _caller(),
        (BinMove("bin-deadline", RackBinSlot("rack-deadline", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
    )
    await service.submit_pending_tasks(1)
    accepted = await _load_task(db_engine, handle.transport_task_id)
    assert accepted.result_deadline_at is not None
    position_message = {
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
        "operation": "transport.task.member_position_changed@v1",
        "timestamp": 1,
        "data": {
            "transport_task_id": handle.transport_task_id,
            "container_id": "bin-deadline",
            "milestone": "SOURCE_PICKED",
        },
    }
    await service.record_callback(
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
        operation="transport.task.member_position_changed@v1",
        message=position_message,
        payload=position_message["data"],
        rejection_reason_code=None,
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
    await confirm_rack_faces(db_engine, {"rack-late-position": RackFace.A})
    handle = await service.move_bins(
        new_uuid7(),
        _caller(),
        (BinMove("bin-late-position", RackBinSlot("rack-late-position", RackFace.A, "1"), HandoffPosition("OUT")),),
    )
    assert await service.submit_pending_tasks(1) == 1
    unknown = await _load_task(db_engine, handle.transport_task_id)
    assert (unknown.status, unknown.reason_code, unknown.result_deadline_at) == (
        "RECONCILING",
        "TRANSPORT_DELIVERY_UNKNOWN",
        None,
    )

    position_message = {
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4474",
        "operation": "transport.task.member_position_changed@v1",
        "timestamp": 1,
        "data": {
            "transport_task_id": handle.transport_task_id,
            "container_id": "bin-late-position",
            "milestone": "SOURCE_PICKED",
        },
    }
    await service.record_callback(
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4474",
        operation="transport.task.member_position_changed@v1",
        message=position_message,
        payload=position_message["data"],
        rejection_reason_code=None,
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
        RackFace.A,
    )
    first = await _load_task(db_engine, handle.transport_task_id)
    expected_payload = {
        "transport_task_id": handle.transport_task_id,
        "kind": "RACK_MOVE",
        "rack_id": "rack-submit-snapshot",
        "source": {"kind": "RACK_POSITION", "location_code": "A"},
        "target": {"kind": "RACK_POSITION", "location_code": "B"},
        "target_face": "A",
    }
    expected_envelope = {
        "operation_id": first.submit_operation_id,
        "operation": "transport.task.submit@v1",
        "timestamp": first.submit_timestamp_ms,
        "data": expected_payload,
    }
    expected_request_body = json.dumps(
        expected_envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert first.client_request_id == "019f12d0-58d7-7b4d-a23a-1b90aa5d4471"
    assert _is_uuid7(getattr(first, "submit_operation_id", None))
    assert getattr(first, "submit_operation_id", None) != first.client_request_id
    assert isinstance(getattr(first, "submit_timestamp_ms", None), int)
    assert first.submit_request_body.encode("utf-8") == expected_request_body
    assert first.submit_request_body_digest == hashlib.sha256(expected_request_body).hexdigest()

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
        retry.submit_request_body,
        retry.submit_request_body_digest,
    ) == (
        first.submit_operation_id,
        first.submit_timestamp_ms,
        first.submit_request_body,
        first.submit_request_body_digest,
    )
    provider = service.provider
    assert provider.snapshots == [
        {
            "operation_id": first.submit_operation_id,
            "transport_task_id": handle.transport_task_id,
            "request_body": expected_request_body,
            "request_body_digest": first.submit_request_body_digest,
            "envelope": expected_envelope,
        },
        {
            "operation_id": first.submit_operation_id,
            "transport_task_id": handle.transport_task_id,
            "request_body": expected_request_body,
            "request_body_digest": first.submit_request_body_digest,
            "envelope": expected_envelope,
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
