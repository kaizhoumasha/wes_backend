from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.transport.contracts import (
    RackPosition,
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
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata

register_required_sqlmodel_metadata()


class _BlockingProvider:
    def __init__(self, code: TransportSubmitCode) -> None:
        self.code = code
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []

    async def submit(
        self,
        *,
        operation_id: str,
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult:
        task_id = str(payload["transport_task_id"])
        self.calls.append(task_id)
        self.started.set()
        await self.release.wait()
        return TransportSubmitResult(self.code, task_id, reason_code="WMS_REJECTED")


class _ImmediateProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def submit(
        self,
        *,
        operation_id: str,
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult:
        task_id = str(payload["transport_task_id"])
        self.calls.append(task_id)
        return TransportSubmitResult(TransportSubmitCode.RECEIVED, task_id)


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


def _service(db_engine: object, provider: object) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return TransportService(sessions, TransportRepository(), provider)  # type: ignore[arg-type]


async def _create_task(service: TransportService, request_id: str, rack_id: str) -> str:
    handle = await service.move_rack(
        new_uuid7(),
        TransportCaller("SORTER", "STATION_A"),
        rack_id,
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
    )
    return handle.transport_task_id


async def _load_task(db_engine: object, task_id: str) -> TransportTask:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == task_id))
        assert task is not None
        return task


@pytest.mark.asyncio
async def test_submit_claims_only_one_task_before_starting_http(db_engine: object) -> None:
    provider = _BlockingProvider(TransportSubmitCode.RECEIVED)
    service = _service(db_engine, provider)
    first_id = await _create_task(service, "single-claim-1", "rack-single-claim-1")
    second_id = await _create_task(service, "single-claim-2", "rack-single-claim-2")

    running = asyncio.create_task(service.submit_pending_tasks(2))
    await provider.started.wait()
    first = await _load_task(db_engine, first_id)
    second = await _load_task(db_engine, second_id)

    assert first.submit_claim_token is not None
    assert first.send_started_at is not None
    assert second.submit_claim_token is None
    assert second.send_started_at is None

    provider.release.set()
    assert await running == 2


@pytest.mark.asyncio
async def test_late_received_converges_without_clearing_replacement_lease(db_engine: object) -> None:
    provider = _BlockingProvider(TransportSubmitCode.RECEIVED)
    service = _service(db_engine, provider)
    task_id = await _create_task(service, "late-received", "rack-late-received")
    running = asyncio.create_task(service.submit_pending_tasks(1))
    await provider.started.wait()
    replacement_started = timezone.now_for_db()
    replacement_retry = replacement_started + timedelta(seconds=30)
    replacement_until = replacement_started + timedelta(seconds=60)
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == task_id)
            .values(
                submit_claim_token="replacement-worker",
                submit_claim_until=replacement_until,
                send_started_at=replacement_started,
                next_submit_at=replacement_retry,
            )
        )

    provider.release.set()
    assert await running == 1
    task = await _load_task(db_engine, task_id)
    assert task.status == "ACCEPTED"
    assert task.submit_claim_token == "replacement-worker"
    assert task.submit_claim_until == replacement_until
    assert task.send_started_at == replacement_started
    assert task.next_submit_at == replacement_retry


@pytest.mark.asyncio
async def test_late_rejection_cannot_regress_an_accepted_task(db_engine: object) -> None:
    provider = _BlockingProvider(TransportSubmitCode.REJECTED)
    service = _service(db_engine, provider)
    task_id = await _create_task(service, "late-rejected", "rack-late-rejected")
    running = asyncio.create_task(service.submit_pending_tasks(1))
    await provider.started.wait()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    result_deadline_at = timezone.now_for_db() + timedelta(minutes=10)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == task_id)
            .values(status="ACCEPTED", result_deadline_at=result_deadline_at, reason_code=None)
        )

    provider.release.set()
    assert await running == 1
    task = await _load_task(db_engine, task_id)
    assert (task.status, task.result_deadline_at, task.reason_code) == ("ACCEPTED", result_deadline_at, None)


@pytest.mark.asyncio
async def test_stale_retry_result_does_not_clear_replacement_attempt_fields(db_engine: object) -> None:
    provider = _BlockingProvider(TransportSubmitCode.NOT_SENT)
    service = _service(db_engine, provider)
    task_id = await _create_task(service, "stale-retry", "rack-stale-retry")
    running = asyncio.create_task(service.submit_pending_tasks(1))
    await provider.started.wait()
    replacement_started = timezone.now_for_db()
    replacement_retry = replacement_started + timedelta(seconds=30)
    replacement_until = replacement_started + timedelta(seconds=60)
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == task_id)
            .values(
                submit_claim_token="replacement-worker",
                submit_claim_until=replacement_until,
                send_started_at=replacement_started,
                next_submit_at=replacement_retry,
            )
        )

    provider.release.set()
    assert await running == 1
    task = await _load_task(db_engine, task_id)
    assert task.status == "PENDING"
    assert task.submit_claim_token == "replacement-worker"
    assert task.submit_claim_until == replacement_until
    assert task.send_started_at == replacement_started
    assert task.next_submit_at == replacement_retry


@pytest.mark.asyncio
async def test_snapshot_digest_mismatch_has_no_writeback(db_engine: object) -> None:
    provider = _BlockingProvider(TransportSubmitCode.RECEIVED)
    service = _service(db_engine, provider)
    task_id = await _create_task(service, "digest-mismatch", "rack-digest-mismatch")
    running = asyncio.create_task(service.submit_pending_tasks(1))
    await provider.started.wait()
    mismatch_updated_at = timezone.now_for_db()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == task_id)
            .values(
                submit_payload_digest="0" * 64,
                status="PENDING",
                reason_code="UNCHANGED",
                updated_at=mismatch_updated_at,
            )
        )

    provider.release.set()
    assert await running == 1
    task = await _load_task(db_engine, task_id)
    assert task.submit_payload_digest == "0" * 64
    assert (task.status, task.reason_code, task.updated_at) == ("PENDING", "UNCHANGED", mismatch_updated_at)


@pytest.mark.asyncio
async def test_submit_stops_claiming_after_monotonic_continue_budget(
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ImmediateProvider()
    service = _service(db_engine, provider)
    first_id = await _create_task(service, "budget-1", "rack-budget-1")
    second_id = await _create_task(service, "budget-2", "rack-budget-2")
    readings = iter((100.0, 105.0))
    monkeypatch.setattr("src.app.transport.service.time", SimpleNamespace(monotonic=lambda: next(readings)))

    assert await service.submit_pending_tasks(2) == 1
    assert provider.calls == [first_id]
    second = await _load_task(db_engine, second_id)
    assert second.submit_attempt_count == 0
    assert second.send_started_at is None
