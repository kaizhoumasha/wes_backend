"""插件拥有的事务、恢复与真实 Confirmation worker 验收。"""

import os
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
from automatic_picking.application.feed_flow import AutomaticPickingFeedFlow
from automatic_picking.application.feed_model import AutomaticPickingFeed
from automatic_picking.application.feed_repository import FeedRepository
from automatic_picking.application.flow import AutomaticPickingWorkFlow
from automatic_picking.application.scan_flow import AutomaticPickingScanFlow
from automatic_picking.application.work_model import AutomaticPickingWork
from automatic_picking.application.work_repository import WorkRepository
from automatic_picking.definition import DEFINITION
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from deployment.plugin_composition import build_deployment_runtime
from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services import BinWorkPlanResultReader, BinWorkPlanScheduler
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import ConfirmationServer, seed_picking_owner
from tests.support.postgresql_heavy import migrated_database
from tests.support.transport_broker import TransportBrokerWorker, close_transport_test_resources

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def database():
    async with migrated_database() as value:
        yield value


async def seed(sessions):
    now = timezone.now_for_db()
    async with sessions.begin() as db:
        task, line = await seed_picking_owner(
            db,
            operation_id=new_uuid7(),
            issued_operation_id=new_uuid7(),
            task_key="AUTO-" + new_uuid7(),
            now=now,
            status="EXECUTING",
        )
        task.task_type, line.line_type = "AUTO", "AUTO"
        line.plugin_key, line.plugin_version = DEFINITION.plugin_key, DEFINITION.plugin_version
        member = PickingTaskBinSourceRack(
            picking_task_id=task.id,
            rack_id="RACK-1",
            rack_face="A",
            plan_revision=1,
            source_evidence_id=task.issued_evidence_id,
        )
        db.add(member)
        await db.flush()
        feed = AutomaticPickingFeed(
            workline_id=line.id,
            picking_task_id=task.id,
            source_member_id=member.id,
            operation_id=new_uuid7(),
            phase="REQUESTED",
        )
        db.add(feed)
        scan = InboundEvidence(
            kind="DEVICE_EVENT",
            source_identity="scan:" + new_uuid7(),
            payload_digest="b" * 64,
            normalized_payload={},
            received_at=now,
            processed_at=now,
            published_at=now,
            decision_digest="b" * 64,
            apply_status="APPLIED",
            device_code="TEST-SCAN2",
            contract_key="TEST-SCAN",
            contract_version="1",
            source_event_id=new_uuid7(),
            workline_id=line.id,
        )
        db.add(scan)
        await db.flush()
        return task, line, feed, scan, now


def workflow():
    return AutomaticPickingWorkFlow(WorkRepository(), BinWorkPlanScheduler(), BinWorkPlanResultReader())


async def test_work_and_request_roll_back_together_and_open_bin_is_unique(database):
    _, sessions = database
    task, line, feed, scan, now = await seed(sessions)
    with pytest.raises(RuntimeError, match="crash"):
        async with sessions.begin() as db:
            await workflow().admit_in_session(
                db,
                line_id=line.id,
                task=task,
                feed=feed,
                bin_code="A123456789",
                evidence_id=scan.id,
                scanned_at=123,
                now=now,
            )
            raise RuntimeError("crash before commit")
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingWork)) == 0
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 0
    async with sessions.begin() as db:
        work = await workflow().admit_in_session(
            db,
            line_id=line.id,
            task=task,
            feed=feed,
            bin_code="A123456789",
            evidence_id=scan.id,
            scanned_at=123,
            now=now,
        )
        first_id, first_op = work.id, work.operation_id
    async with sessions.begin() as db:
        again = await workflow().admit_in_session(
            db,
            line_id=line.id,
            task=task,
            feed=feed,
            bin_code="A123456789",
            evidence_id=scan.id,
            scanned_at=124,
            now=now,
        )
        assert (again.id, again.operation_id, again.scanned_at) == (first_id, first_op, 123)
    with pytest.raises(IntegrityError):
        async with sessions.begin() as db:
            db.add(
                AutomaticPickingWork(
                    workline_id=line.id,
                    picking_task_id=task.id,
                    feed_id=feed.id,
                    bin_code="A123456789",
                    scan2_evidence_id=scan.id,
                    scanned_at=124,
                    operation_id=new_uuid7(),
                    phase="PLAN_PENDING",
                )
            )
            await db.flush()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
        for name in ("automatic_picking_works", "automatic_picking_feeds", "automatic_picking_feed_scans"):
            assert await db.scalar(text("SELECT to_regclass(:name)"), {"name": "wes_biz." + name}) is not None


@pytest.mark.parametrize(
    "data,phase",
    [
        (
            {
                "result": "READY",
                "cell_id": "CELL-1",
                "cell_index": 1,
                "bin_type": 2,
                "reel_layer": 1,
                "reel_thickness": 0.5,
                "reel_diameter": 15.0,
            },
            "READY",
        ),
        ({"result": "NO_WORK"}, "EXIT_PENDING"),
        ({"result": "WAIT", "retry_after_ms": 1000}, "WAIT"),
    ],
)
async def test_worker_result_recovered_and_published_with_work_in_one_transaction(database, data, phase):
    from src.app.device.models import Device
    from src.app.device.services.device_command_service import DeviceCommandService

    database_url, sessions = database
    task, line, feed, scan, now = await seed(sessions)
    async with sessions.begin() as db:
        work = await workflow().admit_in_session(
            db,
            line_id=line.id,
            task=task,
            feed=feed,
            bin_code="A123456789",
            evidence_id=scan.id,
            scanned_at=123,
            now=now,
        )
        work_id, original_op = work.id, work.operation_id
    server = ConfirmationServer(status_code=200, code="DECIDED", data=data)
    worker = TransportBrokerWorker(
        database_url=database_url, redis_url=os.environ["INTEGRATION_REDIS_URL"], wms_base_url=server.url
    )
    success, primary_error = False, None
    try:
        server.start()
        worker.start()
        dispatch = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
        assert worker.result(worker.send(dispatch)) == 1
        async with sessions() as db:
            confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == original_op))
            assert confirmation.status == "COMPLETED" and confirmation.workline_id is None
            result_id = confirmation.response_evidence_id
            result = await db.get(InboundEvidence, result_id)
            assert result.workline_id is None and result.published_at is None
        # 不依赖一次性通知：重新装配原插件，从原 Work 绑定结果路由。
        runtime = build_deployment_runtime(
            session_factory=sessions,
            enabled_plugin_keys=("automatic-picking",),
            transport_runtime=N(service=object(), position_projection_service=object(), client=object()),
            device_command_service=DeviceCommandService(session_factory=sessions),
        )
        plugin = runtime.plugins[0]
        async with sessions.begin() as db:
            line_row = await db.get(WorkLine, line.id)
            line_row.is_active = True
            # 真实组合现已安装搬运：补齐本线配置，仍不创建来源架或投料义务。
            line_row.position_bindings = {
                "FIVE_RACK": {"location_type": "RACK_POSITION", "location_id": "WORK-ONLY-FIVE"},
                "OUTLET": {"location_type": "HANDOFF_POSITION", "location_id": "WORK-ONLY-OUTLET"},
            }
            device = Device(device_code="TEST-SCAN2", device_name="Work offline", work_line_id=line.id)
            db.add(device)
            await db.flush()
            line_row.config = {"device_bindings": {r.role_key: r.role_key for r in DEFINITION.device_roles}}
            line_row.config = {"device_bindings": {**line_row.config["device_bindings"], "SCAN2": "TEST-SCAN2"}}
            line_row.device_contracts = {
                "TEST-SCAN2": {
                    "device_id": device.id,
                    "endpoint_base_url": "http://127.0.0.1:1",
                    "contract_key": "TEST-SCAN",
                    "contract_version": "1",
                    "command_timeout_ms": 10000,
                    "status_max_age_ms": 10000,
                }
            }
            # 本测试只有 Work 恢复，不创建或伪造来源义务。
            feed_row = await db.get(AutomaticPickingFeed, feed.id)
            feed_row.archived_at = now
            assert await plugin.picking_task_batch_driver.advance_completed_in_session(db, line_row) == 1
        # 模拟业务应用事务在提交前崩溃，授权/WAIT 与发布均不得单独留下。
        with pytest.raises(RuntimeError, match="crash"):
            async with sessions.begin() as db:
                applied = await plugin.runtime_binding.business_evidence_consumer.apply_in_session(
                    db, result_id, workline_id=line.id
                )
                assert applied.disposition == "APPLIED"
                raise RuntimeError("crash before publish")
        async with sessions() as db:
            assert (await db.get(AutomaticPickingWork, work_id)).phase == "PLAN_PENDING"
            assert (await db.get(InboundEvidence, result_id)).published_at is None
        assert await runtime.execution.fact_processor.process_batch() == 1
        async with sessions() as db:
            saved = await db.get(AutomaticPickingWork, work_id)
            evidence = await db.get(InboundEvidence, result_id)
            assert saved.phase == phase and saved.response_evidence_id == result_id
            assert evidence.published_at is not None and evidence.decision_digest is not None
            assert saved.authorization_evidence_id == (result_id if phase == "READY" else None)
            due = saved.retry_at
        if phase == "WAIT":
            driver = plugin.picking_task_batch_driver
            driver.clock = lambda: due
            async with sessions.begin() as db:
                assert await driver.advance_completed_in_session(db, await db.get(WorkLine, line.id)) == 1
            async with sessions.begin() as db:
                assert await driver.advance_completed_in_session(db, await db.get(WorkLine, line.id)) == 0
            async with sessions() as db:
                saved = await db.get(AutomaticPickingWork, work_id)
                assert saved.operation_id != original_op and saved.scanned_at == 123 and saved.phase == "PLAN_PENDING"
                assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 2
                assert (await db.get(InboundEvidence, result_id)).published_at is not None
        assert len(server.requests) == 1
        success = True
    except BaseException as exc:
        primary_error = exc
    finally:
        await close_transport_test_resources(
            worker=worker,
            runtime=None,
            server=server,
            cleanup_database=None,
            success=success,
            primary_error=primary_error,
        )


async def test_cancelled_entry_and_new_revision_keep_their_own_batch_identity(database):
    from datetime import timedelta

    from wes_plugin_sdk import wms_operations

    from src.app.wms_adapter.outbound_picking.inbound_batch_typed import encode_request
    from src.utils.canonical_json import canonical_json_digest

    _, sessions = database
    task, line, old_feed, scan, now = await seed(sessions)
    async with sessions.begin() as db:
        new_member = PickingTaskBinSourceRack(
            picking_task_id=task.id,
            rack_id="RACK-1",
            rack_face="A",
            plan_revision=2,
            source_evidence_id=task.issued_evidence_id,
            cancelled_evidence_id=task.issued_evidence_id,
        )
        db.add(new_member)
        await db.flush()
        new_feed = AutomaticPickingFeed(
            workline_id=line.id,
            picking_task_id=task.id,
            source_member_id=new_member.id,
            operation_id=new_uuid7(),
            phase="ALLOCATED",
        )
        db.add(new_feed)
        await db.flush()
        for row, revision in ((await db.get(AutomaticPickingFeed, old_feed.id), 1), (new_feed, 2)):
            payload = encode_request(
                wms_operations.outbound_bin_inbound_batch(
                    operation_id=row.operation_id,
                    task_id=task.task_id,
                    plan_revision=revision,
                    rack_id="RACK-1",
                    rack_face="A",
                ),
                timestamp=123,
            )
            response = {
                "operation_id": row.operation_id,
                "code": "DECIDED",
                "timestamp": 123,
                "data": {
                    "result": "READY",
                    "bins": [
                        {
                            "bin_code": "A123456789",
                            "source_locator": {
                                "type": "RACK_BIN_SLOT",
                                "rack_id": "RACK-1",
                                "rack_face": "A",
                                "slot_id": "1",
                            },
                        }
                    ],
                },
            }
            evidence = InboundEvidence(
                kind="WMS_RESULT",
                source_identity="result:" + row.operation_id,
                operation="outbound.bin.inbound_batch@v1",
                operation_id=row.operation_id,
                payload_digest=canonical_json_digest(response),
                normalized_payload=response,
                received_at=now,
                apply_status="APPLIED",
                workline_id=line.id,
                published_at=now,
                decision_digest="e" * 64,
            )
            db.add(evidence)
            await db.flush()
            row.response_evidence_id = evidence.id
            db.add(
                WmsConfirmation(
                    operation=evidence.operation,
                    operation_id=row.operation_id,
                    workline_id=line.id,
                    request_payload=payload,
                    request_digest=canonical_json_digest(payload),
                    deadline_at=now + timedelta(seconds=30),
                    status="COMPLETED",
                    response_evidence_id=evidence.id,
                    response_result="READY",
                    completed_at=now,
                )
            )
        db.add(
            AutomaticPickingWork(
                workline_id=line.id,
                picking_task_id=task.id,
                feed_id=old_feed.id,
                bin_code="A123456789",
                scan2_evidence_id=scan.id,
                scanned_at=123,
                operation_id=new_uuid7(),
                phase="CLOSED",
            )
        )
        old_entry = await FeedRepository().record_entry(
            db, await db.get(AutomaticPickingFeed, old_feed.id), "A123456789", scan.id
        )
        old_entry.work_admitted = True
    async with sessions.begin() as db:
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.phase == "CLOSED"))
        # 清理旧 Work 后仍由当前批次消费标记防止复用，取消的新成员尚未入线也不得先行准入。
        assert await FeedRepository().scan_candidates(db, line.id, task, "A123456789") == []
        current = await db.get(AutomaticPickingFeed, new_feed.id)
        await FeedRepository().record_entry(db, current, "A123456789", scan.id)
    async with sessions.begin() as db:
        candidates = await FeedRepository().scan_candidates(db, line.id, task, "A123456789")
        assert [row.id for row in candidates] == [new_feed.id]
        assert await WorkRepository().current_work(db, line.id, "A123456789") is None
        assert await FeedRepository().scan_candidates(db, line.id, task, "A987654321") == []


@pytest.mark.parametrize("phase,offset", [("CANCELLED", 0), ("CANCELLED", 1), ("FEED_COMPLETE", 1)])
async def test_terminal_feed_exits_after_publication_and_work_handoff_without_confirmation(database, phase, offset):
    from unittest.mock import AsyncMock

    _, sessions = database
    task, line, feed, scan, now = await seed(sessions)
    repo = FeedRepository()
    async with sessions.begin() as db:
        saved = await db.get(AutomaticPickingFeed, feed.id)
        saved.phase, saved.offset, saved.response_evidence_id = phase, offset, scan.id
        result = await db.get(InboundEvidence, scan.id)
        result.published_at = None
        entry = await repo.record_entry(db, saved, "A123456789", scan.id)
        assert len(await repo.list_admission_pending(db, line.id)) == 1
        result.published_at = now
        assert len(await repo.list_admission_pending(db, line.id)) == 1
        entry.work_admitted = True
    # 不保存原 Confirmation；终态 Feed 不应使独立 Work 的持久恢复失败。
    work = N(picking_task_id=task.id, phase="SKIP_WAIT")
    flow = N(advance_in_session=AsyncMock(return_value=1))
    driver = AutomaticPickingBatchDriver(
        N(list_current=AsyncMock(return_value=[work])), repo, flow, None, None, clock=lambda: now
    )
    async with sessions.begin() as db:
        assert await driver.advance_completed_in_session(db, await db.get(WorkLine, line.id)) == 1
        assert (await db.get(AutomaticPickingFeed, feed.id)).archived_at is None
        assert await repo.list_admission_pending(db, line.id) == []
        assert await repo.by_member(db, feed.source_member_id) is not None
        assert (await WorkRepository().get_unfinished_workload_summary(db, line.id))["count"] == 0
    flow.advance_in_session.assert_awaited_once()


async def freeze_ready(db, task, feed, now, *, count=1):
    from datetime import timedelta

    from wes_plugin_sdk import wms_operations

    from src.app.wms_adapter.outbound_picking.inbound_batch_typed import encode_request
    from src.utils.canonical_json import canonical_json_digest

    payload = encode_request(
        wms_operations.outbound_bin_inbound_batch(
            operation_id=feed.operation_id, task_id=task.task_id, plan_revision=1, rack_id="RACK-1", rack_face="A"
        ),
        timestamp=123,
    )
    response = {
        "operation_id": feed.operation_id,
        "code": "DECIDED",
        "timestamp": 123,
        "data": {
            "result": "READY",
            "bins": [
                {
                    "bin_code": f"A{i:09}",
                    "source_locator": {
                        "type": "RACK_BIN_SLOT",
                        "rack_id": "RACK-1",
                        "rack_face": "A",
                        "slot_id": str(i),
                    },
                }
                for i in range(1, count + 1)
            ],
        },
    }
    evidence = InboundEvidence(
        kind="WMS_RESULT",
        source_identity="result:" + feed.operation_id,
        operation="outbound.bin.inbound_batch@v1",
        operation_id=feed.operation_id,
        payload_digest=canonical_json_digest(response),
        normalized_payload=response,
        received_at=now,
        apply_status="APPLIED",
        workline_id=feed.workline_id,
        published_at=now,
        decision_digest="e" * 64,
    )
    db.add(evidence)
    await db.flush()
    feed.phase, feed.response_evidence_id = "ALLOCATED", evidence.id
    db.add(
        WmsConfirmation(
            operation=evidence.operation,
            operation_id=feed.operation_id,
            workline_id=feed.workline_id,
            request_payload=payload,
            request_digest=canonical_json_digest(payload),
            deadline_at=now + timedelta(seconds=30),
            status="COMPLETED",
            response_evidence_id=evidence.id,
            response_result="READY",
            completed_at=now,
        )
    )
    await db.flush()


@pytest.mark.parametrize("other_state", [None, "FAILED", "UNKNOWN"])
async def test_cancelled_scan_before_transport_publication_resumes_original_feed_and_hands_off(database, other_state):
    from src.app.execution.services.reliable_rack_transport import ReliableBinTransportCreator
    from src.app.transport.models import TransportTask
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.wms_adapter.transport_wire import RESULT_OPERATION
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher
    from tests.support.transport_callbacks import record_valid_callback

    _, sessions = database
    task, line, feed, scan, now = await seed(sessions)
    service = TransportService(sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    runtime = build_deployment_runtime(
        session_factory=sessions,
        enabled_plugin_keys=("automatic-picking",),
        transport_runtime=N(service=service, position_projection_service=object(), client=object()),
        device_command_service=object(),
    )
    publisher = InstalledPluginTransportOutcomePublisher(
        sessions, runtime.plugins, queue_gateway=N(enqueue_picking_task_plans=lambda: None)
    )
    repo = FeedRepository()
    flow = AutomaticPickingFeedFlow(repo, ReliableBinTransportCreator(service))
    scans = AutomaticPickingScanFlow(WorkRepository(), repo, workflow(), None)
    async with sessions.begin() as db:
        saved = await db.get(AutomaticPickingFeed, feed.id)
        line_row = await db.get(WorkLine, line.id)
        line_row.is_active = True
        line_row.position_bindings = {"INLET": {"location_id": "AUTO-IN"}}
        await freeze_ready(db, task, saved, now, count=2 if other_state else 1)
        ready = (await repo.inbound(db, saved))[1]
        assert await flow.advance_in_session(db, line=line_row, feed=saved, ready=ready) == 1
        transport_id = saved.transport_task_id
        (await db.get(PickingTaskBinSourceRack, feed.source_member_id)).cancelled_evidence_id = task.issued_evidence_id
    async with sessions.begin() as db:
        assert (
            await scans.apply_scan_in_session(
                db, line=line, evidence=scan, role="SCAN2", raw="A000000001-C", timestamp=123, now=now
            )
            == "WAIT_FOR_FEED"
        )
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingWork)) == 0
    results = [
        {
            "container_id": "A000000001",
            "status": "SUCCEEDED",
            "final_position": {"kind": "HANDOFF_POSITION", "location_code": "AUTO-IN"},
        }
    ]
    if other_state:
        results.append(
            {
                "container_id": "A000000002",
                "status": "FAILED",
                "failure_code": "TEST_FAILED",
                **(
                    {"position_unknown": True}
                    if other_state == "UNKNOWN"
                    else {
                        "final_position": {
                            "kind": "RACK_BIN_SLOT",
                            "rack_id": "RACK-1",
                            "rack_face": "A",
                            "slot_id": "2",
                        }
                    }
                ),
            }
        )
    await record_valid_callback(
        service,
        operation_id=new_uuid7(),
        transport_task_id=transport_id,
        operation=RESULT_OPERATION,
        timestamp=123,
        payload={
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": results,
        },
    )
    assert await service.process_pending_evidence(10) == 1
    async with sessions.begin() as db:
        assert (
            await scans.apply_scan_in_session(
                db, line=line, evidence=scan, role="SCAN2", raw="A000000001-C", timestamp=123, now=now
            )
            == "WAIT_FOR_FEED"
        )
    assert await service.publish_pending_outcomes(10, publisher) == 1
    assert await service.publish_pending_outcomes(10, publisher) == 0
    async with sessions.begin() as db:
        saved = await db.get(AutomaticPickingFeed, feed.id)
        transport = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == transport_id))
        assert transport.published_outcome_version == transport.outcome_version > 0
        assert await flow.advance_in_session(db, line=line, feed=saved, ready=(await repo.inbound(db, saved))[1]) == 1
        assert saved.phase == ("RECONCILING" if other_state else "FEED_COMPLETE")
        assert saved.transport_task_id == transport_id
        assert len(await repo.list_admission_pending(db, line.id)) == 1
        assert (
            await scans.apply_scan_in_session(
                db, line=line, evidence=scan, role="SCAN2", raw="A000000001-C", timestamp=123, now=now
            )
            == "WORK_ADMITTED"
        )
        await db.flush()
        work = await WorkRepository().current_work(db, line.id, "A000000001")
        assert work.scan2_evidence_id == scan.id and work.feed_id == feed.id
        if other_state:
            assert len(await repo.list_admission_pending(db, line.id)) == 1
        else:
            assert await repo.list_admission_pending(db, line.id) == []
            await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == saved.operation_id))
    async with sessions.begin() as db:
        assert await repo.list_recoverable(db, line.id) == []
        assert len(await repo.list_admission_pending(db, line.id)) == int(bool(other_state))
        assert await WorkRepository().current_work(db, line.id, "A000000001") is not None
        assert await repo.scan_candidates(db, line.id, task, "A999999999") == []


async def test_source_rack_result_publication_enables_one_frozen_inbound_request(database):
    from wes_plugin_sdk import TransportRackPosition, TransportRackReference, TransportRcsTemplateId

    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.wms_adapter.transport_wire import RESULT_OPERATION
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher
    from tests.support.transport_callbacks import record_valid_callback

    _, sessions = database
    task, line, feed, _, _now = await seed(sessions)
    service = TransportService(sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    runtime = build_deployment_runtime(
        session_factory=sessions,
        enabled_plugin_keys=("automatic-picking",),
        transport_runtime=N(service=service, position_projection_service=object(), client=object()),
        device_command_service=object(),
    )
    publisher = InstalledPluginTransportOutcomePublisher(
        sessions, runtime.plugins, queue_gateway=N(enqueue_picking_task_plans=lambda: None)
    )
    repo = FeedRepository()
    driver = runtime.plugins[0].picking_task_batch_driver
    async with sessions.begin() as db:
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.id == feed.id))
        line_row = await db.get(WorkLine, line.id)
        line_row.is_active = True
        line_row.position_bindings = {"FIVE_RACK": {"location_id": "AUTO-FIVE"}}
        handle = await ReliableRackTransportCreator(service).create(
            db,
            workline_id=line.id,
            picking_task_id=task.id,
            source_evidence_id=task.issued_evidence_id,
            correlation_id="source:1",
            step="PICKING_TASK_BIN_SOURCE_RACK_IN",
            resource_fence_id="RACK-1",
            intent=N(
                rack_id="RACK-1",
                source=TransportRackReference("RACK-1"),
                target=TransportRackPosition("AUTO-FIVE"),
                target_face="A",
                rcs_template_id=TransportRcsTemplateId.F01,
            ),
        )
    await record_valid_callback(
        service,
        operation_id=new_uuid7(),
        transport_task_id=handle.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=123,
        payload={
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": "RACK-1",
            "status": "SUCCEEDED",
            "arrival_face": "A",
            "final_position": {"kind": "RACK_POSITION", "location_code": "AUTO-FIVE"},
        },
    )
    assert await service.process_pending_evidence(10) == 1
    async with sessions.begin() as db:
        line_row = await db.get(WorkLine, line.id)
        member = await db.get(PickingTaskBinSourceRack, feed.source_member_id)
        assert not await repo.source_ready(db, line_row, member)
        assert await driver.advance_in_session(db, line_row, task) == 0
    assert await service.publish_pending_outcomes(10, publisher) == 1
    async with sessions.begin() as db:
        line_row = await db.get(WorkLine, line.id)
        assert await repo.source_ready(db, line_row, await db.get(PickingTaskBinSourceRack, feed.source_member_id))
        assert await driver.advance_in_session(db, line_row, task) == 1
        assert await driver.advance_in_session(db, line_row, task) == 0
        receipt = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == handle.transport_task_id)
        )
        assert receipt.workline_id == line.id and receipt.picking_task_id == task.id
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1


async def test_published_transport_rejection_stops_only_its_feed_without_reserving_channel(database):
    from src.app.execution.services.reliable_rack_transport import ReliableBinTransportCreator
    from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher

    class Provider:
        async def submit(self, *, transport_task_id, **_kwargs):
            return TransportSubmitResult(TransportSubmitCode.REJECTED, transport_task_id, "TEST_REJECTED")

    _, sessions = database
    task, line, feed, _, now = await seed(sessions)
    service = TransportService(sessions, TransportRepository(), Provider(), result_timeout=timedelta(seconds=420))
    runtime = build_deployment_runtime(
        session_factory=sessions,
        enabled_plugin_keys=("automatic-picking",),
        transport_runtime=N(service=service, position_projection_service=object(), client=object()),
        device_command_service=object(),
    )
    publisher = InstalledPluginTransportOutcomePublisher(
        sessions, runtime.plugins, queue_gateway=N(enqueue_picking_task_plans=lambda: None)
    )
    repo = FeedRepository()
    flow = AutomaticPickingFeedFlow(repo, ReliableBinTransportCreator(service))
    async with sessions.begin() as db:
        saved = await db.get(AutomaticPickingFeed, feed.id)
        line_row = await db.get(WorkLine, line.id)
        line_row.is_active = True
        line_row.position_bindings = {"INLET": {"location_id": "AUTO-IN"}}
        await freeze_ready(db, task, saved, now)
        assert (
            await flow.advance_in_session(db, line=line_row, feed=saved, ready=(await repo.inbound(db, saved))[1]) == 1
        )
        original = saved.transport_task_id
    assert await service.submit_pending_tasks(10) == 1
    async with sessions.begin() as db:
        assert await repo.channel_busy(db, line.id)
    assert await service.publish_pending_outcomes(10, publisher) == 1
    async with sessions.begin() as db:
        saved = await db.get(AutomaticPickingFeed, feed.id)
        assert await flow.advance_in_session(db, line=line, feed=saved, ready=(await repo.inbound(db, saved))[1]) == 1
        assert saved.phase == "RECONCILING" and saved.transport_task_id == original
        assert not await repo.channel_busy(db, line.id)


async def test_due_work_wait_recovers_through_actual_host_scan_once(database, monkeypatch, caplog):
    from unittest.mock import AsyncMock

    from src.app.device.models.command import DeviceCommand
    from src.app.device.services.device_command_service import DeviceCommandService
    from src.app.execution.services.inbound_evidence_service import InboundEvidenceService
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
    from src.app.transport.models import TransportTask
    from src.app.transport.repository import TransportRepository
    from src.app.transport.service import TransportService
    from src.app.wms_adapter.outbound_picking.work_plan_wire import (
        parse_bin_work_plan_request,
        parse_bin_work_plan_response,
    )

    _, sessions = database
    task, line, feed, scan, now = await seed(sessions)
    monkeypatch.setattr("deployment.plugin_composition.task_queue_gateway", None)
    provider = N(submit=AsyncMock(side_effect=AssertionError("offline must not dispatch")))
    transport = TransportService(sessions, TransportRepository(), provider, result_timeout=timedelta(seconds=420))

    def assembled(at):
        app = build_deployment_runtime(
            session_factory=sessions,
            enabled_plugin_keys=("automatic-picking",),
            transport_runtime=N(service=transport, position_projection_service=object(), client=object()),
            device_command_service=DeviceCommandService(session_factory=sessions),
        )
        app.plugins[0].picking_task_batch_driver.clock = lambda: at
        return app

    app = assembled(now)
    async with sessions.begin() as db:
        live = await db.get(WorkLine, line.id)
        live.is_active = True
        live.position_bindings = {
            "FIVE_RACK": {"location_type": "RACK_POSITION", "location_id": "WORK-ONLY-FIVE"},
            "OUTLET": {"location_type": "HANDOFF_POSITION", "location_id": "WORK-ONLY-OUTLET"},
        }
        # 已交接的投料检查点；本片只恢复当前 Work，不重做投料/扫码。
        (await db.get(AutomaticPickingFeed, feed.id)).archived_at = now
        work = await app.plugins[0].picking_task_batch_driver.work_flow.admit_in_session(
            db,
            line_id=line.id,
            task=task,
            feed=feed,
            bin_code="A123456789",
            evidence_id=scan.id,
            scanned_at=123,
            now=now,
        )
        work_id, original_op = work.id, work.operation_id
        request = (await db.scalars(select(WmsConfirmation))).one()
        payload = {
            "operation_id": original_op,
            "code": "DECIDED",
            "timestamp": 123,
            "data": {"result": "WAIT", "retry_after_ms": 1000},
        }
        parse_bin_work_plan_response(200, payload, request=parse_bin_work_plan_request(request.request_payload))
        accepted = await InboundEvidenceService().accept(
            db,
            kind="WMS_RESULT",
            source_identity=request.operation + ":" + original_op,
            normalized_payload=payload,
            received_at=now,
            picking_task_id=task.id,
            workline_id=line.id,
            operation=request.operation,
            operation_id=original_op,
            apply_status="APPLIED",
        )
        result_id = accepted.evidence.id
        await WmsConfirmationLifecycleService().complete(
            db, request, response_evidence_id=result_id, response_result="WAIT", completed_at=now
        )
    assert await app.execution.fact_processor.process_batch() == 1
    async with sessions() as db:
        saved = await db.get(AutomaticPickingWork, work_id)
        due = saved.retry_at
        assert saved.phase == "WAIT" and due == now + timedelta(milliseconds=1000)
        assert saved.response_evidence_id == result_id
        assert (await db.get(InboundEvidence, result_id)).published_at is not None
    # 只改测试时钟，宿主扫描真实的当前工作线；不直接调用 batch/Work 的推进方法。
    early = assembled(due - timedelta(microseconds=1))
    assert await early.picking_task_plan_activation_service.activate_batch() == 0
    assert await early.picking_task_plan_activation_service.activate_batch() == 0
    async with sessions() as db:
        saved = await db.get(AutomaticPickingWork, work_id)
        assert (saved.phase, saved.operation_id, saved.retry_at, saved.response_evidence_id) == (
            "WAIT",
            original_op,
            due,
            result_id,
        )
        assert len((await db.scalars(select(WmsConfirmation))).all()) == 1
    due_app = assembled(due)
    assert await due_app.picking_task_plan_activation_service.activate_batch() == 1
    async with sessions() as db:
        saved = (await db.scalars(select(AutomaticPickingWork))).one()
        assert saved.id == work_id and saved.operation_id != original_op
        assert saved.phase == "PLAN_PENDING" and saved.retry_at is None and saved.response_evidence_id is None
        assert (
            saved.picking_task_id,
            saved.workline_id,
            saved.feed_id,
            saved.bin_code,
            saved.scan2_evidence_id,
            saved.scanned_at,
        ) == (task.id, line.id, feed.id, "A123456789", scan.id, 123)
        requests = (await db.scalars(select(WmsConfirmation).order_by(WmsConfirmation.id))).all()
        assert len(requests) == 2
        original, current = requests
        assert original.operation_id == original_op and original.status == "COMPLETED"
        assert original.response_evidence_id == result_id and original.completed_at == now
        assert current.operation_id == saved.operation_id and current.status == "PENDING"
        assert current.picking_task_id == task.id and current.response_evidence_id is None
        assert (
            current.request_payload["data"]
            == original.request_payload["data"]
            == {"task_id": task.task_id, "bin_code": "A123456789", "scanned_at": 123}
        )
        current_identity = current.id, current.operation_id, current.request_digest, current.request_payload
    assert await due_app.picking_task_plan_activation_service.activate_batch() == 0
    restarted = assembled(due + timedelta(seconds=1))
    assert await restarted.picking_task_plan_activation_service.activate_batch() == 0
    assert await restarted.picking_task_plan_activation_service.activate_batch() == 0
    async with sessions() as db:
        saved = (await db.scalars(select(AutomaticPickingWork))).one()
        requests = (await db.scalars(select(WmsConfirmation).order_by(WmsConfirmation.id))).all()
        assert len(requests) == 2
        current = requests[-1]
        assert (current.id, current.operation_id, current.request_digest, current.request_payload) == current_identity
        assert current.status == "PENDING" and current.response_evidence_id is None
        assert (saved.phase, saved.operation_id, saved.scanned_at) == ("PLAN_PENDING", current.operation_id, 123)
        assert saved.authorization_evidence_id is None and saved.retry_at is None and saved.response_evidence_id is None
        assert (await db.get(InboundEvidence, result_id)).published_at is not None
        assert not (await db.scalars(select(TransportTask))).all()
        assert not (await db.scalars(select(DeviceCommand))).all()
    provider.submit.assert_not_awaited()
    assert not any(
        record.name.endswith("picking_task_plan_activation") and record.levelno >= 40 for record in caplog.records
    ), "不能把宿主吞掉异常后的返回0当作防重通过"
