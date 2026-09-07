"""静态 Event route 经真实 Service 提交计划后应答，与插件安装解耦。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus
from src.app.wms_adapter import WmsInboundAuthPolicy
from src.app.wms_integration.outbound_picking.composition import build_outbound_picking_runtime
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.register import register_routers
from tests.integration.wms_adapter.outbound_picking.test_plan_delta_postgresql import _event, prepared

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_static_route_commits_plan_then_replays_with_no_plugin(integration_session_factory, prepared):
    task_name, _ = prepared
    runtime = build_outbound_picking_runtime(session_factory=integration_session_factory)
    app = FastAPI()
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
    app.state.outbound_picking_runtime = runtime
    app.state.wms_picking_task_issued_handler = runtime.picking_task_issued_handler
    app.state.wms_picking_task_plan_delta_handler = runtime.picking_task_plan_delta_handler
    app.state.wms_event_stream_service = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    register_routers(app)
    payload = _event(task_name).model_dump(mode="json", exclude_none=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://wes.test") as client:
        received = await client.post("/api/v1/wms/events", json=payload)
        assert received.status_code == 202
        assert received.json()["code"] == "RECEIVED"
        async with integration_session_factory() as db:
            task = await db.scalar(select(PickingTask).where(PickingTask.task_id == task_name))
            assert task.last_applied_plan_revision == 1
            evidence = await db.get(InboundEvidence, task.last_plan_evidence_id)
            assert evidence.apply_status == InboundEvidenceApplyStatus.APPLIED
            assert evidence.line_run_epoch_id is None
        duplicate = await client.post("/api/v1/wms/events", json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json()["code"] == "DUPLICATE"
        assert duplicate.json()["timestamp"] == received.json()["timestamp"]
