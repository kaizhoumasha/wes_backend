from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from src.app.sys.models.api_access_log import APIAccessLog
from src.app.sys.services.api_access_log_service import APIAccessLogService
from src.app.transport.models import TransportCallbackReceipt, TransportEvidence
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.callback_receipt_service import WmsCallbackReceiptService
from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.transport_event_handler import TransportEventHandler
from src.app.wms_adapter.v1.events import router as events_router
from src.core.conf import settings
from src.core.uuid7 import new_uuid7
from src.utils.background_tasks import inject_background_tasks

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
        observation=None,
    ) -> object:
        raise AssertionError("callback receipt test must not submit")


class _FailingEvidenceInsertRepository(TransportRepository):
    async def add_evidence(self, db: AsyncSession, evidence: TransportEvidence) -> None:
        await super().add_evidence(db, evidence)
        raise RuntimeError("forced evidence insert failure")


@pytest.mark.parametrize("diagnostic_fails", [False, True])
async def test_http_receipt_commit_and_original_rejection_survive_diagnostic_write_failure(
    integration_session_factory, monkeypatch, diagnostic_fails
):
    operation_id = new_uuid7()
    message = {
        "operation_id": operation_id,
        "operation": "transport.task.member_position_changed@v1",
        "timestamp": 1,
        "data": {"transport_task_id": "transport-invalid", "container_id": "bin-1", "milestone": "INVALID"},
    }
    raw_body = json.dumps(message, separators=(",", ":")).encode()
    logs = APIAccessLogService(session_context=integration_session_factory)
    record = logs.record
    # SQL/事务 owner 使用合法最大预算；严格默认100ms由 FAST timeout owner 验证。
    monkeypatch.setattr(settings, "API_ACCESS_LOG_WRITE_TIMEOUT_MS", 1000)
    if diagnostic_fails:
        logs.record = AsyncMock(side_effect=RuntimeError("forced diagnostic failure"))
    else:
        logs.record = AsyncMock(wraps=record)
    app = FastAPI(dependencies=[Depends(inject_background_tasks)])
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
    app.state.wms_callback_receipt_service = WmsCallbackReceiptService(log_service=logs)
    app.state.transport_runtime = SimpleNamespace(
        handler=TransportEventHandler(
            TransportService(
                integration_session_factory,
                TransportRepository(),
                _UnusedProvider(),
                result_timeout=timedelta(seconds=420),
            )
        )
    )
    app.state.transport_event_stream_service = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
    app.include_router(events_router, prefix="/api/v1/wms")
    request_id = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/wms/events", content=raw_body, headers={"Content-Type": "application/json"}
            )
        assert response.status_code == 422
        assert response.json()["code"] == "REJECTED"
        assert response.json()["operation_id"] == operation_id
        assert "retry-after" not in response.headers
        logs.record.assert_awaited_once()
        request_id = logs.record.await_args.args[0].request_id
        async with integration_session_factory() as db:
            receipt = await db.scalar(
                select(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id)
            )
            assert receipt is not None
            rows = list(await db.scalars(select(APIAccessLog).where(APIAccessLog.request_id == request_id)))
        if diagnostic_fails:
            logs.record.assert_awaited_once()
            assert rows == []
        else:
            assert len(rows) == 1
            assert (rows[0].system_id, rows[0].direction, rows[0].status_code) == ("wms", "INBOUND", 422)
            assert rows[0].details["raw_body_base64"] == base64.b64encode(raw_body).decode()
    finally:
        async with integration_session_factory.begin() as db:
            if request_id is not None:
                await db.execute(delete(APIAccessLog).where(APIAccessLog.request_id == request_id))
            await db.execute(
                delete(TransportCallbackReceipt).where(TransportCallbackReceipt.operation_id == operation_id)
            )


async def test_non_utf8_operation_is_rejected_before_postgresql_receipt(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = TransportService(
        integration_session_factory, TransportRepository(), _UnusedProvider(), result_timeout=timedelta(seconds=420)
    )
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


async def test_nul_payload_is_durably_rejected_and_replayed_from_postgresql(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    operation_id = new_uuid7()
    operation = "transport.task.member_position_changed@v1"
    raw_body = (
        f'{{"operation_id":"{operation_id}","operation":"{operation}","timestamp":1,'
        '"data":{"transport_task_id":"transport-invalid","container_id":"bin\\u0000one",'
        '"milestone":"SOURCE_PICKED"}}'
    ).encode()
    handler = TransportEventHandler(
        TransportService(
            integration_session_factory, TransportRepository(), _UnusedProvider(), result_timeout=timedelta(seconds=420)
        )
    )

    try:
        first = await handler.handle(raw_body)
        replay = await handler.handle(raw_body)

        assert (first.http_status, first.body["code"]) == (422, "REJECTED")
        assert replay == first
        async with integration_session_factory() as db:
            receipt = await db.scalar(
                select(TransportCallbackReceipt).where(
                    TransportCallbackReceipt.operation == operation,
                    TransportCallbackReceipt.operation_id == operation_id,
                )
            )
        assert receipt is not None
        assert "\x00" not in str(receipt.message_json)
        assert "\\u0000" in receipt.message_json["canonical_message_json"]
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(
                    TransportCallbackReceipt.operation == operation,
                    TransportCallbackReceipt.operation_id == operation_id,
                )
            )


async def test_nul_arrival_face_is_durably_rejected_before_postgresql_projection(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    operation_id = new_uuid7()
    operation = "transport.task.resulted@v1"
    raw_body = (
        f'{{"operation_id":"{operation_id}","operation":"{operation}","timestamp":1,'
        '"data":{"transport_task_id":"transport-invalid","kind":"RACK_MOVE","outcome_revision":1,'
        '"rack_id":"rack-1","status":"SUCCEEDED","final_position":'
        '{"kind":"RACK_POSITION","location_code":"KT16"},"arrival_face":"\\u0000"}}'
    ).encode()
    handler = TransportEventHandler(
        TransportService(
            integration_session_factory, TransportRepository(), _UnusedProvider(), result_timeout=timedelta(seconds=420)
        )
    )

    try:
        response = await handler.handle(raw_body)

        assert (response.http_status, response.body["code"]) == (422, "REJECTED")
        async with integration_session_factory() as db:
            receipt = await db.scalar(
                select(TransportCallbackReceipt).where(
                    TransportCallbackReceipt.operation == operation,
                    TransportCallbackReceipt.operation_id == operation_id,
                )
            )
        assert receipt is not None
        assert "\x00" not in str(receipt.message_json)
        assert "\\u0000" in receipt.message_json["canonical_message_json"]
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(
                    TransportCallbackReceipt.operation == operation,
                    TransportCallbackReceipt.operation_id == operation_id,
                )
            )


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
        TransportService(
            integration_session_factory, TransportRepository(), _UnusedProvider(), result_timeout=timedelta(seconds=420)
        )
        for _ in range(2)
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
    service = TransportService(
        integration_session_factory,
        _FailingEvidenceInsertRepository(),
        _UnusedProvider(),
        result_timeout=timedelta(seconds=420),
    )

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


@pytest.mark.parametrize(
    "raw,status",
    [
        (b"\xff\x00", 400),
        (b'{"x":1,"x":2}', 400),
        (b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472","operation":"unknown@v1"}', 422),
    ],
    ids=["invalid-bytes", "duplicate-keys", "unsupported-operation"],
)
async def test_wms_http_ingress_persists_every_rejected_attempt_to_postgresql(
    integration_session_factory,
    monkeypatch,
    raw: bytes,
    status: int,
) -> None:
    app = FastAPI(dependencies=[Depends(inject_background_tasks)])
    logs = APIAccessLogService(session_context=integration_session_factory)
    logs.record = AsyncMock(wraps=logs.record)
    monkeypatch.setattr(settings, "API_ACCESS_LOG_WRITE_TIMEOUT_MS", 1000)
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
    app.state.wms_callback_receipt_service = WmsCallbackReceiptService(log_service=logs)
    app.state.wms_diagnostics_service = SimpleNamespace(start=AsyncMock(return_value=None), finish=AsyncMock())
    app.state.wms_event_stream_service = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app.include_router(events_router, prefix="/api/v1/wms")
    request_ids = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(2):
                response = await client.post(
                    "/api/v1/wms/events", content=raw, headers={"Content-Type": "application/json"}
                )
                assert response.status_code == status
                request_ids.append(logs.record.await_args.args[0].request_id)
        async with integration_session_factory() as db:
            rows = list(await db.scalars(select(APIAccessLog).where(APIAccessLog.request_id.in_(request_ids))))
        assert len(rows) == 2
        assert len({log.request_id for log in rows}) == 2
        assert all((log.system_id, log.direction, log.status_code) == ("wms", "INBOUND", status) for log in rows)
        assert all(base64.b64decode(log.details["raw_body_base64"]) == raw for log in rows)
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(APIAccessLog).where(APIAccessLog.request_id.in_(request_ids)))
