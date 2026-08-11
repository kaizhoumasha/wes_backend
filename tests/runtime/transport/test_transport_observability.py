from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, update
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
from src.utils.timezone import timezone
from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata

register_required_sqlmodel_metadata()

_CONTEXT_KEYS = {
    "transport.submit.batch_completed": {"event", "processed_count", "requested_limit"},
    "transport.task.reconciling": {"event", "transport_task_id", "operation_id", "reason"},
    "transport.submit.late_writeback": {"event", "transport_task_id", "operation_id", "reason"},
    "transport.submit.lease_replaced": {"event", "transport_task_id", "operation_id", "reason"},
    "transport.outcome.publish_failed": {"event", "transport_task_id", "outcome_version", "reason"},
}


class _Provider:
    def __init__(self, code: TransportSubmitCode, *, block: bool = False) -> None:
        self.code = code
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not block:
            self.release.set()

    async def submit(
        self,
        *,
        operation_id: str,
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult:
        self.started.set()
        await self.release.wait()
        return TransportSubmitResult(
            self.code,
            str(payload["transport_task_id"]),
            reason_code="WMS_REJECTED",
        )


class _Publisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def publish(self, outcome: TransportOutcome) -> None:
        if self.fail:
            raise RuntimeError("publisher unavailable")


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


def _service(
    db_engine: object,
    provider: _Provider,
) -> TransportService:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return TransportService(sessions, TransportRepository(), provider)


async def _create_task(service: TransportService, request_id: str, rack_id: str) -> str:
    handle = await service.move_rack(
        request_id,
        TransportCaller("SORTER", "STATION_A"),
        rack_id,
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
    )
    return handle.transport_task_id


def _event(caplog: pytest.LogCaptureFixture, name: str) -> logging.LogRecord:
    matching = [record for record in caplog.records if getattr(record, "event", None) == name]
    assert len(matching) == 1
    record = matching[0]
    assert _CONTEXT_KEYS[name] <= record.__dict__.keys()
    assert "payload" not in record.__dict__
    assert "claim_token" not in record.__dict__
    return record


@pytest.mark.asyncio
async def test_submit_batch_completed_log_has_stable_summary(
    db_engine: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.app.transport.service")
    service = _service(db_engine, _Provider(TransportSubmitCode.RECEIVED))

    assert await service.submit_pending_tasks(7) == 0

    record = _event(caplog, "transport.submit.batch_completed")
    assert (record.processed_count, record.requested_limit) == (0, 7)


@pytest.mark.asyncio
async def test_reconciling_log_has_task_operation_and_reason_context(
    db_engine: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.app.transport.service")
    service = _service(db_engine, _Provider(TransportSubmitCode.RECEIVED))
    task_id = await _create_task(service, "log-reconciling", "rack-log-reconciling")
    expired = timezone.now_for_db() - timedelta(seconds=1)
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == task_id)
            .values(send_started_at=expired, submit_claim_token="expired-worker", submit_claim_until=expired)
        )

    assert await service.reconcile_overdue_tasks(1) == 1

    record = _event(caplog, "transport.task.reconciling")
    assert record.transport_task_id == task_id
    assert record.reason == "TRANSPORT_DELIVERY_UNKNOWN"


@pytest.mark.asyncio
async def test_late_deterministic_ack_log_has_stable_context(
    db_engine: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.app.transport.service")
    provider = _Provider(TransportSubmitCode.REJECTED, block=True)
    service = _service(db_engine, provider)
    task_id = await _create_task(service, "log-late-rejected", "rack-log-late-rejected")
    running = asyncio.create_task(service.submit_pending_tasks(1))
    await provider.started.wait()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == task_id)
            .values(
                submit_claim_token="replacement-worker",
                submit_claim_until=timezone.now_for_db() + timedelta(seconds=60),
            )
        )

    provider.release.set()
    assert await running == 1

    record = _event(caplog, "transport.submit.late_writeback")
    assert (record.transport_task_id, record.reason) == (task_id, "REJECTED")


@pytest.mark.asyncio
async def test_lease_replaced_log_has_stable_context(
    db_engine: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.app.transport.service")
    provider = _Provider(TransportSubmitCode.NOT_SENT, block=True)
    service = _service(db_engine, provider)
    task_id = await _create_task(service, "log-lease-replaced", "rack-log-lease-replaced")
    running = asyncio.create_task(service.submit_pending_tasks(1))
    await provider.started.wait()
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions.begin() as db:
        await db.execute(
            update(TransportTask)
            .where(TransportTask.transport_task_id == task_id)
            .values(
                submit_claim_token="replacement-worker",
                submit_claim_until=timezone.now_for_db() + timedelta(seconds=60),
            )
        )

    provider.release.set()
    assert await running == 1

    record = _event(caplog, "transport.submit.lease_replaced")
    assert (record.transport_task_id, record.reason) == (task_id, "NOT_SENT")


@pytest.mark.asyncio
async def test_outcome_publish_failed_log_has_stable_context(
    db_engine: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.app.transport.service")
    publisher = _Publisher(fail=True)
    service = _service(db_engine, _Provider(TransportSubmitCode.REJECTED))
    task_id = await _create_task(service, "log-publish-failed", "rack-log-publish-failed")
    assert await service.submit_pending_tasks(1) == 1
    caplog.clear()

    assert await service.publish_pending_outcomes(1, publisher) == 0

    record = _event(caplog, "transport.outcome.publish_failed")
    assert (record.transport_task_id, record.outcome_version, record.reason) == (task_id, 1, "PUBLISH_ERROR")
