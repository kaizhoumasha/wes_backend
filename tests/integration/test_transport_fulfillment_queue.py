"""Transport 与既有 WMS fulfillment 扫描共享单并发队列的最坏竞争。"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from celery.exceptions import TaskRevokedError
from sqlalchemy import delete, select

from src.app.execution.models import PositionProjection
from src.app.transport.composition import build_transport_runtime
from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot, RackFace, TransportCaller
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportResourceBinding,
    TransportTask,
)
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from tests.support.transport_broker import (
    MockWmsHttpServer,
    TransportBrokerWorker,
    close_transport_test_resources,
)
from tests.support.transport_callbacks import record_valid_callback
from tests.support.transport_projections import confirm_rack_faces_with_sessions

pytestmark = pytest.mark.integration

SUBMIT_TASK = "src.celery_app.tasks.transport.submit_transport_tasks_batch"
EVIDENCE_TASK = "src.celery_app.tasks.transport.process_transport_evidence_batch"


async def test_slow_submit_drops_stale_scan_and_next_wakeups_process_all_persisted_facts(
    integration_session_factory,
) -> None:
    redis_url = os.environ["INTEGRATION_REDIS_URL"]
    database_url = os.environ["INTEGRATION_DATABASE_URL"]
    task_ids: list[str] = []
    callback_operation_ids: list[str] = []
    projection_object_ids: list[str] = []
    server: MockWmsHttpServer | None = None
    runtime = None
    worker: TransportBrokerWorker | None = None
    success = False
    primary_error: BaseException | None = None

    async def _cleanup_database() -> None:
        if not task_ids and not projection_object_ids:
            return
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(TransportCallbackReceipt).where(
                    TransportCallbackReceipt.operation_id.in_(callback_operation_ids)
                )
            )
            await db.execute(delete(TransportEvidence).where(TransportEvidence.transport_task_id.in_(task_ids)))
            await db.execute(delete(PositionProjection).where(PositionProjection.object_id.in_(projection_object_ids)))
            await db.execute(
                delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id.in_(task_ids))
            )
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id.in_(task_ids)))
            await db.execute(delete(TransportTask).where(TransportTask.transport_task_id.in_(task_ids)))

    try:
        # 首次响应为受限 CI 的数据库/调度开销留足 5 秒领取预算，第二次请求仍占满 10 秒 HTTP 预算。
        server = MockWmsHttpServer((3.0, 10.5)).start()
        runtime = await build_transport_runtime(
            wms_base_url=server.url,
            transport_submit_path="/api/v1/wes/transport-requests",
            session_factory=integration_session_factory,
        )
        worker = TransportBrokerWorker(database_url, redis_url, server.url)
        worker.start()
        suffix = uuid.uuid4().hex
        rack_ids = [f"rack-submit-{index}-{suffix}" for index in range(2)] + [f"rack-evidence-{suffix}"]
        projection_object_ids.extend([*rack_ids, f"bin-evidence-{suffix}"])
        await confirm_rack_faces_with_sessions(
            integration_session_factory,
            dict.fromkeys(rack_ids, RackFace.A),
        )
        for index in range(2):
            handle = await runtime.service.move_bins(
                new_uuid7(),
                TransportCaller("TRANSPORT_QUEUE_TEST"),
                (
                    BinMove(
                        f"bin-submit-{index}-{suffix}",
                        RackBinSlot(rack_ids[index], RackFace.A, "1"),
                        HandoffPosition(f"HANDOFF-{index}-{suffix}"),
                    ),
                ),
            )
            task_ids.append(handle.transport_task_id)

        evidence_handle = await runtime.service.move_bins(
            new_uuid7(),
            TransportCaller("TRANSPORT_QUEUE_TEST"),
            (
                BinMove(
                    f"bin-evidence-{suffix}",
                    RackBinSlot(rack_ids[2], RackFace.A, "1"),
                    HandoffPosition(f"HANDOFF-EVIDENCE-{suffix}"),
                ),
            ),
        )
        task_ids.append(evidence_handle.transport_task_id)
        evidence_operation_id = new_uuid7()
        callback_operation_ids.append(evidence_operation_id)
        await record_valid_callback(
            runtime.service,
            operation_id=evidence_operation_id,
            transport_task_id=evidence_handle.transport_task_id,
            operation=RESULT_OPERATION,
            timestamp=1,
            payload={
                "kind": "BIN_MOVE",
                "outcome_revision": 1,
                "results": [
                    {
                        "container_id": f"bin-evidence-{suffix}",
                        "status": "SUCCEEDED",
                        "final_position": {
                            "kind": "HANDOFF_POSITION",
                            "location_code": f"HANDOFF-EVIDENCE-{suffix}",
                        },
                    }
                ],
            },
        )

        started = time.monotonic()
        submit_result = worker.send(SUBMIT_TASK, kwargs={"limit": 100})
        server.wait_for_requests(1)
        stale_evidence_result = worker.send(EVIDENCE_TASK, kwargs={"limit": 100}, expires=5)
        assert worker.result(submit_result, timeout=25) == 2
        submit_finished = time.monotonic()
        assert 12 <= submit_finished - started < 18
        with pytest.raises(TaskRevokedError):
            worker.result(stale_evidence_result, timeout=5)

        next_evidence = worker.send(EVIDENCE_TASK, kwargs={"limit": 100}, expires=10)
        assert worker.result(next_evidence, timeout=10) >= 1
        assert time.monotonic() - submit_finished < 10

        async with integration_session_factory() as db:
            evidence = await db.scalar(
                select(TransportEvidence).where(TransportEvidence.operation_id == evidence_operation_id)
            )
            task = await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == evidence_handle.transport_task_id)
            )
        assert evidence is not None and evidence.status == "APPLIED"
        assert task is not None and task.status == "SUCCEEDED"
        success = True
    except BaseException as exc:
        primary_error = exc
    finally:
        await close_transport_test_resources(
            worker=worker,
            runtime=runtime,
            server=server,
            cleanup_database=_cleanup_database,
            success=success,
            primary_error=primary_error,
        )


async def test_real_worker_rejects_non_fixed_transport_batches_before_database_scan(
    integration_guard: None,
) -> None:
    redis_url = os.environ["INTEGRATION_REDIS_URL"]
    database_url = os.environ["INTEGRATION_DATABASE_URL"]
    worker: TransportBrokerWorker | None = None
    success = False
    primary_error: BaseException | None = None

    async def _cleanup_database() -> None:
        return None

    try:
        worker = TransportBrokerWorker(database_url, redis_url, "http://127.0.0.1:9")
        worker.start()
        for task_name in (
            SUBMIT_TASK,
            EVIDENCE_TASK,
            "src.celery_app.tasks.transport.reconcile_transport_tasks_batch",
        ):
            for invalid_limit in (99, 101):
                result = worker.send(task_name, kwargs={"limit": invalid_limit})
                with pytest.raises(ValueError, match="Transport batch limit must be 100"):
                    worker.result(result)
        success = True
    except BaseException as exc:
        primary_error = exc
    finally:
        await close_transport_test_resources(
            worker=worker,
            runtime=None,
            server=None,
            cleanup_database=_cleanup_database,
            success=success,
            primary_error=primary_error,
        )
