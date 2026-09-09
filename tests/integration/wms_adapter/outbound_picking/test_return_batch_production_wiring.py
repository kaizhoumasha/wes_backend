"""WorkLine 可靠义务、关闭围栏和零插件真实 worker 接入。"""

import os
from datetime import timedelta

import pytest
from sqlalchemy import select
from wes_plugin_sdk import BinReturnCandidate, wms_operations

from src.app.execution.models import InboundEvidence, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services import WmsConfirmationService
from src.app.transport.debug_run_service import TransportDebugReturnBatchOwner
from src.app.transport.models import TransportDebugRun
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_adapter.factory import build_wms_client
from src.app.wms_adapter.outbound_picking.return_batch_typed import encode_request
from src.app.wms_integration.outbound_picking.services import ReturnBatchOwnerService
from src.app.workline.models import WorkLine
from src.app.workline.models.workline import LineType
from src.app.workline.repositories import WorkLineRepository
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.core.exceptions import BusinessException
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import (
    ConfirmationServer,
    confirmation_database,
)
from tests.support.transport_broker import TransportBrokerWorker, close_transport_test_resources

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_inactive_debug_owner_dispatches_with_real_worker(confirmation_database, monkeypatch):
    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", "[]")
    database_url, sessions = confirmation_database
    operation_id = new_uuid7()
    now = timezone.now_for_db()
    server = ConfirmationServer(status_code=200, code="DECIDED", data={"result": "NO_BATCH", "retry_after_ms": 1000})
    worker = TransportBrokerWorker(
        database_url=database_url, redis_url=os.environ["INTEGRATION_REDIS_URL"], wms_base_url=server.url
    )
    service = WmsConfirmationService(workline_owner=TransportDebugReturnBatchOwner(), session_factory=sessions)
    success, primary_error = False, None

    async def cleanup_database():
        pass

    try:
        async with sessions.begin() as db:
            line = WorkLine(
                id=347454468883008,
                line_code=f"DEBUG-{operation_id[-12:]}",
                line_name="debug",
                line_type=LineType.AUTO,
                is_active=False,
            )
            db.add(line)
            await db.flush()
            request = encode_request(
                wms_operations.outbound_bin_return_batch(
                    operation_id=operation_id,
                    workline_code=line.line_code,
                    rack_id="RACK-1",
                    rack_face="A",
                    return_candidates=(BinReturnCandidate(1, "BIN-1", "CNV0302"),),
                ),
                timestamp=1,
            )
            db.add(
                TransportDebugRun(
                    run_id=f"debug-run-{new_uuid7()}",
                    status="RUNNING",
                    active_scope="GLOBAL",
                    rack_id="RACK-1",
                    current_phase="BINS_TO_RACK",
                    created_by_user_id=1,
                    created_at=now,
                    updated_at=now,
                    configuration_json={
                        "workline_id": line.id,
                        "workline_code": line.line_code,
                        "return_requests": {operation_id: request},
                    },
                )
            )
            await service.create_or_get(
                db,
                operation=request["operation"],
                operation_id=operation_id,
                workline_id=line.id,
                request_payload=request,
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )
        server.start()
        worker.start()
        worker.result(worker.send("src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"))
        async with sessions() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            assert confirmation.status == WmsConfirmationStatus.COMPLETED
            assert confirmation.response_result == "NO_BATCH"
            assert server.requests == [{"path": "/api/v1/wes/decisions", "envelope": request}]
        success = True
    except BaseException as exc:
        primary_error = exc
    finally:
        await close_transport_test_resources(
            worker=worker,
            runtime=None,
            server=server,
            cleanup_database=cleanup_database,
            success=success,
            primary_error=primary_error,
        )


@pytest.mark.parametrize("result,closed_workline", [("NO_BATCH", False), ("READY", False), ("NO_BATCH", True)])
async def test_workline_obligation_requires_active_owner(confirmation_database, monkeypatch, result, closed_workline):
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
        workline_owner=ReturnBatchOwnerService(), session_factory=sessions, adapter=WmsConfirmationAdapter(client)
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
                line_code=f"RETURN-{operation_id[-12:]}",
                line_name="WorkLine return",
                line_type=LineType.MANUAL,
                is_active=True,
            )
            db.add(workline)
            await db.flush()
            workline_id = workline.id
            request = encode_request(
                wms_operations.outbound_bin_return_batch(
                    operation_id=operation_id,
                    workline_code=workline.line_code,
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
                workline_id=workline_id,
                request_payload=request,
                deadline_at=now + timedelta(minutes=5),
                created_at=now,
            )
            assert accepted.confirmation.picking_task_id is None
        async with sessions.begin() as db:
            line = await db.get(WorkLine, workline_id)
            with pytest.raises(BusinessException, match="未完成运行负载"):
                await WorkLineConfigurationService(plugins=()).deactivate(
                    db, workline_id=workline_id, version=line.version
                )
            summary = await WorkLineRepository().get_unfinished_workload_summary(db, workline_id)
            assert summary["by_type"]["wms_confirmations"] == 1
        if closed_workline:
            async with sessions.begin() as db:
                cached_workline = await db.get(WorkLine, workline_id)
                assert cached_workline.is_active
                # 另一事务关闭 WorkLine 后，锁定读取必须刷新先前缓存的 ACTIVE 对象。
                # 手动构造恢复现场；正常关闭已由上面的未闭合义务围栏阻止。
                async with sessions.begin() as closing_db:
                    workline = await closing_db.get(WorkLine, workline_id)
                    workline.is_active = False
                with pytest.raises(ValueError, match="WorkLine"):
                    await service.create_or_get(
                        db,
                        operation=request["operation"],
                        operation_id=new_uuid7(),
                        workline_id=workline_id,
                        request_payload=request,
                        deadline_at=now + timedelta(minutes=5),
                        created_at=now,
                    )
        server.start()
        dispatch = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
        if closed_workline:
            worker.start()
            worker.result(worker.send(dispatch))
        else:
            await service.dispatch_batch()
        async with sessions() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation_id))
            if closed_workline:
                assert confirmation.status == WmsConfirmationStatus.RECONCILING
                assert confirmation.response_evidence_id is None
                assert confirmation.operation_id == operation_id
                assert confirmation.request_payload == request
                assert server.requests == []
            else:
                assert confirmation.status == WmsConfirmationStatus.COMPLETED
                assert confirmation.response_result == result
                evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
                assert evidence.workline_id == workline_id
                assert evidence.material_execution_id is None
                assert evidence.normalized_payload["data"] == data
                assert server.requests == [{"path": "/api/v1/wes/decisions", "envelope": request}]
            summary = await WorkLineRepository().get_unfinished_workload_summary(db, workline_id)
            assert summary["by_type"]["wms_confirmations"] == int(closed_workline)
            assert summary["by_type"]["inbound_evidences"] == 0
        if not closed_workline:
            async with sessions.begin() as db:
                line = await db.get(WorkLine, workline_id)
                closed = await WorkLineConfigurationService(plugins=()).deactivate(
                    db, workline_id=workline_id, version=line.version
                )
                assert closed.is_active is False
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
