"""Epoch 可靠义务、关闭围栏和零插件真实 worker 接入。"""

import os
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from wes_plugin_sdk import BinReturnCandidate, wms_operations

from src.app.execution.models import InboundEvidence, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_adapter.factory import build_wms_client
from src.app.wms_adapter.outbound_picking.return_batch_typed import encode_request
from src.app.wms_integration.outbound_picking.services import ReturnBatchOwnerService
from src.app.workline.models import LineRunEpoch, WorkLine
from src.app.workline.models.line_run_epoch import LineRunEpochStatus
from src.app.workline.models.workline import LineType
from src.app.workline.repositories import WorkLineRepository
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError, LineRunEpochService
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    ConfirmationServer,
    confirmation_database,
)
from tests.support.transport_broker import TransportBrokerWorker, close_transport_test_resources

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.mark.parametrize("result,closed_epoch", [("NO_BATCH", False), ("READY", False), ("NO_BATCH", True)])
async def test_epoch_obligation_requires_active_owner(confirmation_database, monkeypatch, result, closed_epoch):
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    database_url, sessions = confirmation_database
    now = timezone.now_for_db()
    operation_id = new_uuid7()
    data = (
        {"result": "NO_BATCH", "retry_after_ms": 1000}
        if result == "NO_BATCH"
        else {
            "result": "READY",
            "moves": [
                {
                    "sequence_no": 1,
                    "bin_code": "BIN-1",
                    "target": {"type": "RACK_BIN_SLOT", "rack_id": "RACK-1", "rack_face": "A", "slot_id": "A-01"},
                }
            ],
        }
    )
    server = ConfirmationServer(status_code=200, code="DECIDED", data=data)
    client = build_wms_client(base_url=server.url, timeout_seconds=5)
    service = WmsConfirmationService(
        epoch_owner=ReturnBatchOwnerService(), session_factory=sessions, adapter=WmsConfirmationAdapter(client)
    )
    worker = TransportBrokerWorker(
        database_url=database_url, redis_url=os.environ["INTEGRATION_REDIS_URL"], wms_base_url=server.url
    )
    success = False
    primary_error = None

    async def cleanup_database():
        # 独占 module 数据库由 confirmation_database 最终销毁；worker 必须先关闭。
        pass

    try:
        async with sessions.begin() as db:
            workline = WorkLine(
                line_code=f"RETURN-{operation_id[-12:]}", line_name="Epoch return", line_type=LineType.MANUAL
            )
            db.add(workline)
            await db.flush()
            epoch = LineRunEpoch(
                epoch_code=f"RETURN-EPOCH-{operation_id}",
                workline_id=workline.id,
                plugin_key="manual_bin_processing",
                plugin_version="0.1.0",
                flow_mode="MANUAL_BIN_PROCESSING",
                topology_digest="a" * 64,
                configuration_digest="b" * 64,
                configuration_snapshot_json={},
                status=LineRunEpochStatus.ACTIVE,
                started_at=now,
            )
            db.add(epoch)
            await db.flush()
            epoch_id, workline_id = epoch.id, workline.id
            request = encode_request(
                wms_operations.outbound_bin_return_batch(
                    operation_id=operation_id,
                    workline_code=workline.line_code,
                    line_run_epoch_id=epoch.epoch_code,
                    rack_id="RACK-1",
                    rack_face="A",
                    return_candidates=(BinReturnCandidate(1, "BIN-1", "RETURN_BUFFER_01"),),
                ),
                timestamp=1,
            )
            accepted = await service.create_or_get(
                db,
                operation=request["operation"],
                operation_id=operation_id,
                line_run_epoch_id=epoch_id,
                request_payload=request,
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )
            assert accepted.confirmation.picking_task_id is None
        async with sessions.begin() as db:
            commands = AsyncMock()
            commands.has_unclosed_for_epoch_for_update.return_value = False
            with pytest.raises(ActiveLineRunEpochExistsError, match="WMS"):
                await LineRunEpochService().close_active_epoch(
                    db, workline_id=workline_id, closed_at=now, command_repository=commands
                )
            summary = await WorkLineRepository().get_unfinished_workload_summary(db, workline_id)
            assert summary["by_type"]["wms_confirmations"] == 1
        if closed_epoch:
            async with sessions.begin() as db:
                cached_epoch = await db.get(LineRunEpoch, epoch_id)
                assert cached_epoch.status == LineRunEpochStatus.ACTIVE
                # 另一事务关闭 Epoch 后，锁定读取必须刷新先前缓存的 ACTIVE 对象。
                # 手动构造恢复现场；正常关闭已由上面的未闭合义务围栏阻止。
                async with sessions.begin() as closing_db:
                    epoch = await closing_db.get(LineRunEpoch, epoch_id)
                    epoch.status, epoch.closed_at = LineRunEpochStatus.CLOSED, now
                with pytest.raises(ValueError, match="Epoch"):
                    await service.create_or_get(
                        db,
                        operation=request["operation"],
                        operation_id=new_uuid7(),
                        line_run_epoch_id=epoch_id,
                        request_payload=request,
                        deadline_at=now + timedelta(minutes=5),
                        created_at=now,
                    )
        server.start()
        dispatch = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
        if closed_epoch:
            worker.start()
            worker.result(worker.send(dispatch))
        else:
            await service.dispatch_batch()
        async with sessions() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            if closed_epoch:
                assert confirmation.status == WmsConfirmationStatus.RECONCILING
                assert confirmation.response_evidence_id is None
                assert confirmation.operation_id == operation_id
                assert confirmation.request_payload == request
                assert server.requests == []
            else:
                assert confirmation.status == WmsConfirmationStatus.COMPLETED
                assert confirmation.response_result == result
                evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
                assert evidence.line_run_epoch_id == epoch_id
                assert evidence.material_execution_id is None
                assert evidence.normalized_payload["data"] == data
                assert server.requests == [{"path": "/api/v1/wes/decisions", "envelope": request}]
            summary = await WorkLineRepository().get_unfinished_workload_summary(db, workline_id)
            assert summary["by_type"]["wms_confirmations"] == int(closed_epoch)
            assert summary["by_type"]["inbound_evidences"] == 0
        if not closed_epoch:
            async with sessions.begin() as db:
                closed = await LineRunEpochService().close_active_epoch(
                    db, workline_id=workline_id, closed_at=now, command_repository=commands
                )
                assert closed.status == LineRunEpochStatus.CLOSED
        assert await service.dispatch_batch() == 0
        success = True
    except BaseException as exc:
        primary_error = exc
    finally:
        await client.aclose()
        await close_transport_test_resources(
            worker=worker,
            runtime=None,
            server=server,
            cleanup_database=cleanup_database,
            success=success,
            primary_error=primary_error,
        )
