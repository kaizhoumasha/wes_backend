"""自动联调真实 WorkLine / WmsConfirmation 测试装配；WMS 决策为显式测试替身。"""

from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.execution.services import WmsConfirmationService
from src.app.transport.models import TransportDebugRun
from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult
from src.app.wms_integration.outbound_picking.services import ReturnBatchOwnerService
from src.app.workline.models.workline import LineType, WorkLine


@pytest.fixture
async def debug_workline(integration_session_factory):
    async with integration_session_factory.begin() as db:
        line = WorkLine(
            line_code="DEBUG-LINE", line_name="Debug allocation integration", line_type=LineType.MANUAL, is_active=True
        )
        db.add(line)
        await db.flush()
        line_id = line.id
    yield line_id
    async with integration_session_factory.begin() as db:
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.workline_id == line_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.workline_id == line_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


async def freeze_return_allocation(service, run_id, *, max_count=4):
    """保留生产生命周期/证据持久化，返回当前面选箱槽位作为可预测的测试 WMS 分配。"""
    snapshot = await service.get_run(run_id)
    assert snapshot.current_phase == "BINS_TO_RACK"
    assert await service.advance_run(run_id)
    async with service._sessions() as db:
        run = await db.scalar(select(TransportDebugRun).where(TransportDebugRun.run_id == run_id))
        slots = {
            item["bin_code"]: item["slot_id"]
            for group in run.configuration_json["face_groups"]
            for item in group["bins"]
        }

    class Adapter:
        async def dispatch(self, *, operation, operation_id, request_payload, request_digest, observation=None):
            data = request_payload["data"]
            return WmsDispatchResult(
                WmsDispatchCode.DETERMINATE,
                response_result="READY",
                normalized_response={
                    "operation_id": operation_id,
                    "code": "DECIDED",
                    "timestamp": request_payload["timestamp"],
                    "data": {
                        "result": "READY",
                        "moves": [
                            {
                                "sequence_no": item["sequence_no"],
                                "bin_code": item["bin_code"],
                                "target": {
                                    "type": "RACK_BIN_SLOT",
                                    "rack_id": data["rack_id"],
                                    "rack_face": data["rack_face"],
                                    "slot_id": slots[item["bin_code"]],
                                },
                            }
                            for item in data["return_candidates"][:max_count]
                        ],
                    },
                },
            )

    gateway = Mock()
    dispatcher = WmsConfirmationService(
        session_factory=service._sessions,
        adapter=Adapter(),
        workline_owner=ReturnBatchOwnerService(),
        task_queue_gateway=gateway,
    )
    assert await dispatcher.dispatch_batch(limit=1) == 1
    gateway.enqueue_execution_facts.assert_not_called()
    assert await service.advance_run(run_id)
