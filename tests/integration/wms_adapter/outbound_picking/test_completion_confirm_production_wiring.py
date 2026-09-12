"""零插件保存完成确认结果，不自动重发、完成任务或释放物理资源。"""

import os
from datetime import timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from wes_plugin_sdk import wms_operations

from src.app.device.models import DeviceCommand
from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceKind,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.services import WmsConfirmationService
from src.app.transport.models import TransportTask
from src.app.wms_adapter.outbound_picking.completion_confirm_typed import encode_request
from src.app.wms_diagnostics.config import DiagnosticsConfig
from src.app.wms_diagnostics.contracts import ExchangeQuery
from src.app.wms_diagnostics.repository import DiagnosticsRepository
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    ConfirmationServer,
    confirmation_database,
    picking_confirmation_worker,
)

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.mark.parametrize(
    "data",
    [
        {"result": "COMPLETED"},
        {"result": "PLAN_REVISION_STALE", "current_plan_revision": 1},
        {"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": 1000},
    ],
)
@pytest.mark.parametrize("state", [PickingTaskStatus.PREPARING, PickingTaskStatus.EXECUTING])
async def test_completion_decision_persists_without_automatic_reissue_or_device_action(
    confirmation_database, monkeypatch: pytest.MonkeyPatch, data: dict, state: PickingTaskStatus
) -> None:
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    monkeypatch.setenv("WMS_DIAGNOSTICS_BUDGET_MS", "500")
    _, sessions = confirmation_database
    server = ConfirmationServer(status_code=200, code="DECIDED", data=data)
    async with picking_confirmation_worker(confirmation_database, server=server, status=state) as (
        worker,
        task,
        _workline,
        operation_id,
        now,
    ):
        request = encode_request(
            wms_operations.outbound_picking_task_completion_confirm(
                operation_id=operation_id,
                task_id=task.task_id,
                last_applied_plan_revision=0,
            ),
            timestamp=int(timezone.to_utc(now).timestamp() * 1000),
        )
        async with sessions.begin() as db:
            await WmsConfirmationService().create_or_get(
                db,
                operation="outbound.picking_task.completion_confirm@v1",
                operation_id=operation_id,
                picking_task_id=task.id,
                request_payload=request,
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )
        worker.start()
        health_check = "src.celery_app.tasks.core.health_check"
        health = worker.result(worker.send(health_check))
        assert health["checks"]["redis"]["status"] == "connected"
        dispatch = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
        assert worker.result(worker.send(dispatch)) == 1
        async with sessions() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            assert confirmation.status == WmsConfirmationStatus.COMPLETED
            assert confirmation.response_result == data["result"]
            assert confirmation.request_payload == request
            evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
            assert evidence.kind == InboundEvidenceKind.WMS_RESULT
            assert evidence.operation_id == operation_id
            assert evidence.normalized_payload["data"] == data
            assert evidence.material_execution_id is None
            assert evidence.workline_id is None
            assert (await db.get(PickingTask, task.id)).status == state
            assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
            assert await db.scalar(select(func.count()).select_from(TransportTask)) == 0
            assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 0
        assert worker.result(worker.send(dispatch)) == 0
        assert server.requests == [{"path": "/api/v1/wes/decisions", "envelope": request}]
        # 真实 worker 的技术观察与原可靠义务相互独立，但必须对应同一次请求。
        async with Redis.from_url(os.environ["INTEGRATION_REDIS_URL"], decode_responses=True) as redis:
            repository = DiagnosticsRepository(redis, DiagnosticsConfig())
            page = await repository.list(ExchangeQuery(operation_id=operation_id))
            assert len(page.items) == 1
            detail = await repository.get(page.items[0].exchange_id)
            assert detail is not None
            assert detail.operation_id == operation_id
            assert detail.result == data["result"]
            assert detail.request.source == "WIRE"
            assert detail.contract_status == "PASS"
