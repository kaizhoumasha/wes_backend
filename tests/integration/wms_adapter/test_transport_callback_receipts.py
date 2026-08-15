from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, func, select

from src.app.transport.models import TransportCallbackReceipt, TransportEvidence
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_event_handler import TransportEventHandler
from src.core.uuid7 import new_uuid7

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
        raise AssertionError("callback receipt test must not submit")


class _FailingEvidenceInsertRepository(TransportRepository):
    async def add_evidence(self, db: AsyncSession, evidence: TransportEvidence) -> None:
        await super().add_evidence(db, evidence)
        raise RuntimeError("forced evidence insert failure")


async def test_non_utf8_operation_is_rejected_before_postgresql_receipt(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = TransportService(integration_session_factory, TransportRepository(), _UnusedProvider())
    handler = TransportEventHandler(service)
    async with integration_session_factory() as db:
        receipt_count_before = await db.scalar(select(func.count()).select_from(TransportCallbackReceipt))

    response = await handler.handle(
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472","operation":"\\ud800","timestamp":1,"data":{}}'
    )

    assert response.http_status == 400
    assert response.body == {}
    async with integration_session_factory() as db:
        receipt_count_after = await db.scalar(select(func.count()).select_from(TransportCallbackReceipt))
    assert receipt_count_after == receipt_count_before


async def test_concurrent_invalid_callback_replays_share_one_postgresql_receipt(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    operation_id = new_uuid7()
    operation = "transport.task.member_position_changed@v1"
    message = {
        "operation_id": operation_id,
        "operation": operation,
        "timestamp": 1,
        "data": {"transport_task_id": "transport-invalid", "container_id": "bin-1", "milestone": "INVALID"},
    }
    services = [
        TransportService(integration_session_factory, TransportRepository(), _UnusedProvider()) for _ in range(2)
    ]

    try:
        responses = await asyncio.gather(
            *(
                service.record_callback(
                    operation_id=operation_id,
                    operation=operation,
                    message=message,
                    payload=None,
                    rejection_reason_code="INVALID_EVIDENCE",
                )
                for service in services
            )
        )
        assert responses[0] == responses[1]
        assert responses[0]["http_status"] == 422
        async with integration_session_factory() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(TransportCallbackReceipt)
                .where(
                    TransportCallbackReceipt.operation == operation,
                    TransportCallbackReceipt.operation_id == operation_id,
                )
            )
        assert count == 1
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(
                    TransportCallbackReceipt.operation == operation,
                    TransportCallbackReceipt.operation_id == operation_id,
                )
            )


async def test_callback_receipt_and_evidence_roll_back_in_one_transaction(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    operation_id = new_uuid7()
    operation = "transport.task.member_position_changed@v1"
    payload = {
        "transport_task_id": "transport-rollback-missing",
        "container_id": "bin-1",
        "milestone": "SOURCE_PICKED",
    }
    message = {
        "operation_id": operation_id,
        "operation": operation,
        "timestamp": 1,
        "data": payload,
    }
    service = TransportService(integration_session_factory, _FailingEvidenceInsertRepository(), _UnusedProvider())

    with pytest.raises(RuntimeError, match="forced evidence insert failure"):
        await service.record_callback(
            operation_id=operation_id,
            operation=operation,
            message=message,
            payload=payload,
            rejection_reason_code=None,
        )

    async with integration_session_factory() as db:
        receipt_count = await db.scalar(
            select(func.count())
            .select_from(TransportCallbackReceipt)
            .where(
                TransportCallbackReceipt.operation == operation,
                TransportCallbackReceipt.operation_id == operation_id,
            )
        )
        evidence_count = await db.scalar(
            select(func.count())
            .select_from(TransportEvidence)
            .where(
                TransportEvidence.operation == operation,
                TransportEvidence.operation_id == operation_id,
            )
        )
    assert (receipt_count, evidence_count) == (0, 0)
