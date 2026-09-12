from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareRuntimeFacts, PrepareTaskType

from src.app.device.models import Device, DeviceStatusObservation
from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.repositories import InboundEvidenceRepository
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.services import (
    PickingTaskConfirmationOwnerService,
    PickingTaskPrepareCoordinator,
)
from src.app.workline.models import (
    LineType,
    WorkLine,
    WorkLineRunMode,
)
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.core.exceptions import BusinessException
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

pytest_plugins = ("tests.integration.conftest",)


class _Queue:
    def __init__(self) -> None:
        self.calls = 0

    def enqueue_wms_confirmations(self) -> None:
        self.calls += 1


class _Adapter:
    async def dispatch(self, *, operation_id: str, **_kwargs: object) -> object:
        return SimpleNamespace(
            code=WmsDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": operation_id,
                "code": "PREPARE_ACCEPTED",
                "timestamp": 2,
                "data": {},
            },
            response_result="PREPARE_ACCEPTED",
            retry_after_ms=None,
        )


class _FailingConfirmations:
    async def create_or_get(self, _db: object, **_kwargs: object) -> object:
        raise RuntimeError("confirmation insert failed")


async def _seed_ready_workline(db, *, now: datetime):  # type: ignore[no-untyped-def]
    identity = new_uuid7()
    workline = WorkLine(
        line_code=f"MANUAL-{identity[-12:]}",
        line_name="Manual outbound picking",
        line_type=LineType.MANUAL,
        run_mode=WorkLineRunMode.AUTO,
        is_active=True,
        plugin_key="manual_bin_processing",
        plugin_version="0.1.0",
        flow_mode="MANUAL_BIN_PROCESSING",
    )
    db.add(workline)
    await db.flush()
    device = Device(
        device_code=f"MANUAL-DEVICE-{identity[-12:]}",
        device_name="Manual line conveyor",
        work_line_id=workline.id,
        endpoint_base_url="http://manual-ecs:8080",
    )
    db.add(device)
    await db.flush()
    workline.config = {"device_bindings": {"CONVEYOR": device.device_code}}
    workline.device_contracts = {
        device.device_code: {
            "device_id": device.id,
            "endpoint_base_url": "http://manual-ecs:8080",
            "contract_key": "manual.conveyor",
            "contract_version": "1.0",
            "status_max_age_ms": 5_000,
            "command_timeout_ms": 30_000,
        }
    }
    workline.position_bindings = {
        "MANUAL_WORK_STATION": {
            "location_id": f"MANUAL-POSITION-{identity[-12:]}",
            "location_type": "MANUAL_WORK_STATION",
        }
    }
    observation = DeviceStatusObservation(
        device_code=device.device_code,
        contract_key="manual.conveyor",
        contract_version="1.0",
        mode="AUTO",
        status="IDLE",
        current_command_code=None,
        device_timestamp=int(timezone.to_utc(now).timestamp() * 1000),
        received_at=now,
        payload_digest="c" * 64,
        raw_payload={},
    )
    db.add(observation)
    await db.flush()
    return workline, device


async def _seed_task(
    db,  # type: ignore[no-untyped-def]
    *,
    suffix: str,
    task_type: PickingTaskType,
    dispatch_sequence: int,
    not_before_ms: int | None,
) -> PickingTask:
    operation_id = new_uuid7()
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.WMS_EVENT,
        source_identity=f"outbound.picking_task.issued@v1:{operation_id}",
        payload_digest=suffix * 64,
        normalized_payload={},
        received_at=datetime(2026, 9, 4),
        operation="outbound.picking_task.issued@v1",
        operation_id=operation_id,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
        processed_at=datetime(2026, 9, 4),
    )
    db.add(evidence)
    await db.flush()
    task = PickingTask(
        task_id=f"PICK-{suffix}-{operation_id}",
        task_type=task_type,
        queue_revision=1,
        dispatch_sequence=dispatch_sequence,
        not_before_ms=not_before_ms,
        issued_at_ms=1,
        issued_evidence_id=evidence.id,
    )
    db.add(task)
    await db.flush()
    return task


@pytest.mark.asyncio
async def test_prepare_filters_queue_and_concurrent_callers_claim_at_most_one_task(
    integration_session_factory,
) -> None:
    now = datetime(2026, 9, 4, 0, 0)
    now_ms = int(timezone.to_utc(now).timestamp() * 1000)
    queue = _Queue()
    async with integration_session_factory.begin() as db:
        workline, device = await _seed_ready_workline(db, now=now)
        future = await _seed_task(
            db,
            suffix="d",
            task_type=PickingTaskType.MANUAL,
            dispatch_sequence=9_000_000_001,
            not_before_ms=now_ms + 60_000,
        )
        auto = await _seed_task(
            db,
            suffix="e",
            task_type=PickingTaskType.AUTO,
            dispatch_sequence=9_000_000_002,
            not_before_ms=None,
        )
        eligible = await _seed_task(
            db,
            suffix="f",
            task_type=PickingTaskType.MANUAL,
            dispatch_sequence=9_000_000_003,
            not_before_ms=now_ms,
        )
        second_eligible = await _seed_task(
            db,
            suffix="1",
            task_type=PickingTaskType.MANUAL,
            dispatch_sequence=9_000_000_004,
            not_before_ms=None,
        )
        historical = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"historical:{new_uuid7()}",
            device_code=device.device_code,
            payload_digest="a" * 64,
            normalized_payload={"diagnosis": "old execution unresolved"},
            received_at=now - timedelta(days=1),
            workline_id=workline.id,
            apply_status=InboundEvidenceApplyStatus.RECONCILING,
        )
        db.add(historical)
        await db.flush()
        ids = {
            "workline": workline.id,
            "device": device.id,
            "tasks": (future.id, auto.id, eligible.id, second_eligible.id),
            "historical_evidence": historical.id,
        }

    services = [
        PickingTaskPrepareCoordinator(integration_session_factory, policy=_Policy(), task_queue_gateway=queue)  # type: ignore[arg-type]
        for _ in range(2)
    ]
    results = await asyncio.gather(
        *(service.prepare_next_for_workline(ids["workline"], now=now) for service in services)
    )

    assert sum(result.prepared for result in results) == 1
    dispatch_service = WmsConfirmationService(
        session_factory=integration_session_factory,
        adapter=_Adapter(),  # type: ignore[arg-type]
        picking_task_owner=PickingTaskConfirmationOwnerService(),
    )
    assert await dispatch_service.dispatch_batch(limit=1, now=now) == 1
    async with integration_session_factory() as db:
        tasks = list((await db.scalars(select(PickingTask).where(PickingTask.id.in_(ids["tasks"])))).all())
        confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.picking_task_id.in_(ids["tasks"])))
        assert confirmation is not None
        response_evidence = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.id == confirmation.response_evidence_id)
        )
        persisted_history = await db.get(InboundEvidence, ids["historical_evidence"])
        assert persisted_history is not None
        assert persisted_history.apply_status == InboundEvidenceApplyStatus.RECONCILING
        assert persisted_history.normalized_payload == {"diagnosis": "old execution unresolved"}
        assert persisted_history.processed_at is None
    by_id = {task.id: task for task in tasks}
    assert PickingTaskStatus(by_id[eligible.id].status) is PickingTaskStatus.PREPARING
    assert by_id[eligible.id].workline_id == ids["workline"]
    assert all(
        PickingTaskStatus(by_id[task_id].status) is PickingTaskStatus.QUEUED
        for task_id in (future.id, auto.id, second_eligible.id)
    )
    assert WmsConfirmationStatus(confirmation.status) is WmsConfirmationStatus.COMPLETED
    assert response_evidence is not None
    assert response_evidence.kind == InboundEvidenceKind.WMS_RESULT
    assert response_evidence.workline_id is None
    assert response_evidence.material_execution_id is None
    assert queue.calls == 1
    # 原 PickingTask 仍有效，新的独立 prepare 不能越过当前业务任务。
    assert not (await services[0].prepare_next_for_workline(ids["workline"], now=now)).prepared
    assert queue.calls == 1

    # WMS 已接纳 prepare，但任务仍在等待计划；完成的确认不能解除 WorkLine 停用围栏。
    async with integration_session_factory() as db:
        line = await db.get(WorkLine, ids["workline"])
        assert line is not None
        with pytest.raises(BusinessException, match="未完成运行负载"):
            await WorkLineConfigurationService(definitions=()).deactivate(db, workline_id=line.id, version=line.version)
        await db.rollback()
        persisted_line = await db.get(WorkLine, ids["workline"])
        assert persisted_line is not None and persisted_line.is_active

    async with integration_session_factory.begin() as db:
        claimed = await InboundEvidenceRepository().claim_decision_batch(
            db,
            now=now,
            claim_token=new_uuid7(),
            claim_expires_at=now + timedelta(minutes=1),
            limit=100,
        )
        assert response_evidence.id not in {evidence.id for evidence in claimed}

    async with integration_session_factory.begin() as db:
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.picking_task_id.in_(ids["tasks"])))
        evidence_ids = [task.issued_evidence_id for task in tasks] + [response_evidence.id, ids["historical_evidence"]]
        await db.execute(delete(PickingTask).where(PickingTask.id.in_(ids["tasks"])))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(
            delete(DeviceStatusObservation).where(DeviceStatusObservation.device_code == device.device_code)
        )
        await db.execute(delete(Device).where(Device.id == ids["device"]))
        await db.execute(delete(WorkLine).where(WorkLine.id == ids["workline"]))


@pytest.mark.asyncio
async def test_prepare_skip_locked_allows_only_one_workline_to_claim_one_task(
    integration_session_factory,
) -> None:
    now = datetime(2026, 9, 4, 0, 0)
    queue = _Queue()
    async with integration_session_factory.begin() as db:
        first_line, first_device = await _seed_ready_workline(db, now=now)
        second_line, second_device = await _seed_ready_workline(db, now=now)
        task = await _seed_task(
            db,
            suffix="3",
            task_type=PickingTaskType.MANUAL,
            dispatch_sequence=9_000_000_006,
            not_before_ms=None,
        )
        workline_ids = (first_line.id, second_line.id)
        device_ids = (first_device.id, second_device.id)
        device_codes = (first_device.device_code, second_device.device_code)
        task_id = task.id
        evidence_id = task.issued_evidence_id

    results = await asyncio.gather(
        *(
            PickingTaskPrepareCoordinator(
                integration_session_factory, policy=_Policy(), task_queue_gateway=queue
            ).prepare_next_for_workline(
                workline_id,
                now=now,
            )
            for workline_id in workline_ids
        )
    )

    assert sum(result.prepared for result in results) == 1
    async with integration_session_factory() as db:
        persisted = await db.get(PickingTask, task_id)
        confirmations = list(
            (await db.scalars(select(WmsConfirmation).where(WmsConfirmation.picking_task_id == task_id))).all()
        )
    assert persisted is not None
    assert PickingTaskStatus(persisted.status) is PickingTaskStatus.PREPARING
    assert persisted.workline_id in workline_ids
    assert len(confirmations) == 1
    assert queue.calls == 1

    async with integration_session_factory.begin() as db:
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.picking_task_id == task_id))
        await db.execute(delete(PickingTask).where(PickingTask.id == task_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == evidence_id))
        await db.execute(delete(DeviceStatusObservation).where(DeviceStatusObservation.device_code.in_(device_codes)))
        await db.execute(delete(Device).where(Device.id.in_(device_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_(workline_ids)))


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_values", [(None, None), (1, 1)])
async def test_wms_confirmation_database_rejects_zero_or_multiple_owners(
    integration_session_factory,
    owner_values: tuple[int | None, int | None],
) -> None:
    workline_id, picking_task_id = owner_values
    confirmation = WmsConfirmation(
        operation="outbound.picking_task.prepare@v1",
        operation_id=new_uuid7(),
        workline_id=workline_id,
        picking_task_id=picking_task_id,
        request_digest="d" * 64,
        request_payload={"data": {}},
        deadline_at=datetime(2026, 9, 4, 0, 1),
    )
    async with integration_session_factory() as db:
        db.add(confirmation)
        with pytest.raises(IntegrityError) as exc_info:
            await db.flush()
        assert exc_info.value.orig.__cause__.constraint_name == (  # type: ignore[union-attr]
            "ck_wms_confirmations_wms_confirmation_exactly_one_owner"
        )
        await db.rollback()


@pytest.mark.asyncio
async def test_prepare_rolls_back_task_binding_when_confirmation_creation_fails(
    integration_session_factory,
) -> None:
    now = datetime(2026, 9, 4, 0, 0)
    async with integration_session_factory.begin() as db:
        workline, device = await _seed_ready_workline(db, now=now)
        task = await _seed_task(
            db,
            suffix="2",
            task_type=PickingTaskType.MANUAL,
            dispatch_sequence=9_000_000_005,
            not_before_ms=None,
        )
        ids = (workline.id, device.id, task.id, task.issued_evidence_id)

    service = PickingTaskPrepareCoordinator(
        integration_session_factory,
        policy=_Policy(),
        confirmation_service=_FailingConfirmations(),  # type: ignore[arg-type]
        task_queue_gateway=_Queue(),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="confirmation insert failed"):
        await service.prepare_next_for_workline(workline.id, now=now)

    async with integration_session_factory() as db:
        persisted = await db.get(PickingTask, task.id)
        confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.picking_task_id == task.id))
    assert persisted is not None
    assert PickingTaskStatus(persisted.status) is PickingTaskStatus.QUEUED
    assert persisted.workline_id is None
    assert confirmation is None

    async with integration_session_factory.begin() as db:
        await db.execute(delete(PickingTask).where(PickingTask.id == ids[2]))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == ids[3]))
        await db.execute(
            delete(DeviceStatusObservation).where(DeviceStatusObservation.device_code == device.device_code)
        )
        await db.execute(delete(Device).where(Device.id == ids[1]))
        await db.execute(delete(WorkLine).where(WorkLine.id == ids[0]))


class _Policy:
    """核心数据库测试只选择队列类型；人工准入由插件纯测试拥有。"""

    def select_task_type(self, context: PrepareContext) -> PrepareTaskType:
        return PrepareTaskType.MANUAL

    def is_ready(self, facts: PrepareRuntimeFacts, *, now: datetime) -> bool:
        return True
