"""人工出库联调 run 的人工推进与可靠对象关联。"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

import wes_plugin_sdk as sdk
from pydantic import ValidationError

from src.app.device.endpoint import validate_device_endpoint_base_url
from src.app.device.models.command import MANUAL_DEBUG_REF_TYPE, DeviceCommandRequestData
from src.app.device.services import device_service
from src.app.execution.models import InboundEvidenceApplyStatus, WmsConfirmationStatus
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationAcceptance,
    WmsConfirmationLifecycleService,
)
from src.app.sys.services.event_stream_service import event_stream_service
from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot
from src.app.wms_adapter.outbound_picking.arrival_report_typed import encode_request as encode_arrival_report
from src.app.wms_adapter.outbound_picking.arrival_report_wire import RETURN_RACK_ARRIVAL_REPORT_OPERATION
from src.app.wms_adapter.outbound_picking.completion_confirm_typed import encode_request as encode_completion_confirm
from src.app.wms_adapter.outbound_picking.completion_confirm_wire import (
    COMPLETION_CONFIRM_OPERATION,
    CompletionConfirmData,
)
from src.app.wms_adapter.outbound_picking.departure_typed import encode_request as encode_departure
from src.app.wms_adapter.outbound_picking.departure_wire import RACK_DEPARTURE_OPERATION, RackDepartureData
from src.app.wms_adapter.outbound_picking.inbound_batch_typed import encode_request as encode_inbound_batch
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import (
    BIN_INBOUND_BATCH_OPERATION,
    BinInboundBatchData,
    BinInboundBatchReady,
)
from src.app.wms_adapter.outbound_picking.manual_bin_admission_wire import (
    MANUAL_BIN_ADMISSION_OPERATION,
    ManualBinAdmissionData,
)
from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import MANUAL_BIN_COMPLETED_OPERATION
from src.app.wms_adapter.outbound_picking.manual_bin_typed import encode_admission
from src.app.wms_adapter.outbound_picking.return_batch_typed import encode_request as encode_return_batch
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION, BinReturnBatchData
from src.app.wms_adapter.outbound_picking.typed import encode_request as encode_prepare_request
from src.app.wms_adapter.outbound_picking.wire import (
    BUSINESS_IDENTIFIER_PATTERN,
    PICKING_TASK_PREPARE_OPERATION,
    PickingTaskPrepareData,
)
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareNoopReason
from src.app.workline_integration_debug.contracts import (
    MANUAL_OUTBOUND_SITE_CONFIGURATION,
    IntegrationDebugPhase,
    IntegrationDebugProfile,
    IntegrationDebugRunStatus,
    IntegrationDebugScenario,
    IntegrationTransportAction,
    IntegrationTransportActionKind,
    profile_uses_real_ecs,
    profile_uses_real_transport,
)
from src.app.workline_integration_debug.models import IntegrationRun, IntegrationRunStep
from src.app.workline_integration_debug.repository import IntegrationRunRepository, integration_run_repository
from src.app.workline_integration_debug.transport import build_transport_request
from src.core.task_queue_gateway import task_queue_gateway
from src.core.transaction_wakeup import defer_wakeup
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.device.services import DeviceCommandService
    from src.app.transport.service import TransportService
    from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareCoordinator

logger = logging.getLogger(__name__)
INTEGRATION_DEBUG_STREAM_CHANNEL = "workline:integration-debug:stream"


class IntegrationDebugContractError(ValueError):
    pass


class IntegrationDebugConflict(ValueError):
    pass


class IntegrationDebugNotFound(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreateIntegrationRun:
    workline_code: str
    profile: IntegrationDebugProfile
    environment_label: str
    device_code: str | None = None
    rack_id: str | None = None


class EventPublisherPort(Protocol):
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool: ...


class IntegrationRunWorkLineOwner:
    """只允许已由联调 run 冻结的两个人工 WMS 请求继续收敛。"""

    def __init__(self, repository: IntegrationRunRepository | None = None) -> None:
        self._runs = repository or integration_run_repository

    async def validate_owner(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        request_payload: dict[str, Any],
    ) -> bool:
        operation = request_payload.get("operation")
        operation_id = request_payload.get("operation_id")
        if operation != MANUAL_BIN_ADMISSION_OPERATION:
            return False
        return isinstance(operation_id, str) and await self._runs.owns_operation(
            db,
            workline_id=workline_id,
            operation_id=operation_id,
        )


class IntegrationDebugService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        repository: IntegrationRunRepository | None = None,
        confirmations: WmsConfirmationLifecycleService,
        transport: TransportService,
        device_commands: DeviceCommandService,
        prepare: PickingTaskPrepareCoordinator | None = None,
        publisher: EventPublisherPort | None = None,
    ) -> None:
        self._sessions = session_factory
        self._runs = repository or integration_run_repository
        self._confirmations = confirmations
        self._transport = transport
        self._device_commands = device_commands
        self._prepare = prepare
        self._publisher = publisher or event_stream_service

    async def create_run(self, request: CreateIntegrationRun, *, actor_id: int) -> dict[str, Any]:
        if not request.workline_code.strip() or actor_id <= 0 or not request.environment_label.strip():
            raise IntegrationDebugContractError("workline、环境和操作员必须有效")
        if not request.device_code:
            raise IntegrationDebugContractError("必须选择已登记的真实或模拟 ECS 设备")
        async with self._sessions.begin() as db:
            workline = await self._runs.get_workline_by_code(db, request.workline_code.strip(), for_update=True)
            if workline is None:
                raise IntegrationDebugNotFound(f"WorkLine {request.workline_code} 不存在")
            if workline.id is None:
                raise RuntimeError("已持久化 WorkLine 缺少 id")
            if workline.line_code != "KT16":
                raise IntegrationDebugContractError("当前临时联调能力仅支持 KT16")
            if await self._runs.get_active_for_workline(db, workline.id, for_update=True) is not None:
                raise IntegrationDebugConflict("该 WorkLine 已有活动联调 run")
            run_id = new_uuid7()
            run = IntegrationRun(
                run_id=run_id,
                workline_id=workline.id,
                workline_code=workline.line_code,
                scenario_key=IntegrationDebugScenario.MANUAL_OUTBOUND_PICKING_V1,
                expected_plugin_key="manual_bin_processing",
                profile=request.profile,
                environment_label=request.environment_label.strip(),
                operator_user_id=actor_id,
                active_scope=f"WORKLINE:{workline.id}",
                status=IntegrationDebugRunStatus.WAITING_TASK,
                current_phase=IntegrationDebugPhase.BIND_TASK,
                device_code=request.device_code,
                rack_id=request.rack_id,
                configuration_json={"site_configuration": deepcopy(MANUAL_OUTBOUND_SITE_CONFIGURATION)},
                created_by=actor_id,
            )
            step = IntegrationRunStep(
                run_id=run_id,
                ordinal=0,
                phase=IntegrationDebugPhase.BIND_TASK,
                status="PENDING",
                created_by=actor_id,
            )
            await self._runs.add_run(db, run, [step])
            snapshot = self._snapshot(run, [step])
        await self._publish(snapshot)
        return snapshot

    async def get_run(self, run_id: str) -> dict[str, Any]:
        async with self._sessions() as db:
            run = await self._require_run(db, run_id)
            return self._snapshot(run, await self._runs.list_steps(db, run_id))

    async def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise IntegrationDebugContractError("limit 必须为 1..100")
        async with self._sessions() as db:
            runs = await self._runs.list_recent(db, limit=limit)
            return [self._snapshot(run, await self._runs.list_steps(db, run.run_id)) for run in runs]

    async def bind_task(
        self,
        run_id: str,
        *,
        task_id: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.BIND_TASK)
            task = await self._runs.get_picking_task(db, task_id)
            if (
                task is None
                or task.task_type != PickingTaskType.MANUAL
                or task.status != PickingTaskStatus.QUEUED
                or task.id is None
            ):
                raise IntegrationDebugContractError("只能选择已可靠接收且处于 QUEUED 的 MANUAL PickingTask")
            evidence = await self._runs.get_evidence(db, task.issued_evidence_id)
            if evidence is None or not evidence.operation_id:
                raise IntegrationDebugContractError("PickingTask 缺少 issued Evidence")
            run.picking_task_id = task.id
            run.task_id = task.task_id
            run.issued_operation_id = evidence.operation_id
            run.status = IntegrationDebugRunStatus.ACTIVE
            run.current_phase = IntegrationDebugPhase.TASK_PREPARE
            run.updated_by = actor_id
            run.increment_version()
            step = await self._runs.get_step_for_update(db, run_id, IntegrationDebugPhase.BIND_TASK)
            if step is None:
                raise RuntimeError("联调 run 缺少绑定任务步骤")
            step.status = "SUCCEEDED"
            step.result_summary_json = {"task_id": task.task_id, "issued_operation_id": evidence.operation_id}
            step.updated_by = actor_id
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_task_prepare(
        self,
        run_id: str,
        *,
        client_request_id: str,
        request_data: PickingTaskPrepareData,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        if self._prepare is None:
            raise IntegrationDebugConflict("PickingTask prepare runtime 未装配")
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.TASK_PREPARE)
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("必须先选择已接收的 PickingTask")
            workline_id, selected_task_id = run.workline_id, run.task_id
            if request_data.task_id != selected_task_id:
                raise IntegrationDebugContractError("prepare data.task_id 必须等于本 run 已绑定的 PickingTask")
            wms_workline_code = request_data.workline_code
            task = await self._runs.get_picking_task(db, selected_task_id, for_update=True)
            if task is None or task.id != run.picking_task_id or task.task_id != selected_task_id:
                raise IntegrationDebugConflict("所选 PickingTask 已变化，请重新选择")
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None:
                self._assert_matching_wms_replay(
                    existing,
                    run_id=run_id,
                    operation=PICKING_TASK_PREPARE_OPERATION,
                    request={"task_id": selected_task_id, "workline_code": wms_workline_code},
                )
                return self._snapshot(run, await self._runs.list_steps(db, run_id))
            confirmation = await self._runs.get_prepare_confirmation(db, run.picking_task_id)
            if confirmation is not None:
                if confirmation.id is None:
                    raise RuntimeError("已持久化 prepare confirmation 缺少 id")
                request_data = confirmation.request_payload.get("data")
                if (
                    task.status not in {PickingTaskStatus.PREPARING, PickingTaskStatus.EXECUTING}
                    or task.workline_id != workline_id
                    or not isinstance(request_data, dict)
                    or request_data.get("task_id") != selected_task_id
                    or request_data.get("workline_code") != wms_workline_code
                ):
                    raise IntegrationDebugConflict("prepare confirmation 已绑定其它 WorkLine、任务或 WMS 参数")
                await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase.TASK_PREPARE,
                    status="WAITING",
                    actor_id=actor_id,
                    client_request_id=client_request_id,
                    operation=PICKING_TASK_PREPARE_OPERATION,
                    operation_id=confirmation.operation_id,
                    request=request_data,
                    wms_confirmation_id=confirmation.id,
                )
                run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                run.updated_by = actor_id
                run.increment_version()
                snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
            else:
                if task.status != PickingTaskStatus.QUEUED or task.workline_id is not None:
                    raise IntegrationDebugConflict("所选 PickingTask 已被其它 WorkLine 占用")
                snapshot = None

        if snapshot is not None:
            await self._publish(snapshot)
            return snapshot

        prepared = await self._prepare.prepare_next_for_workline(
            workline_id,
            expected_task_id=selected_task_id,
            wms_workline_code=wms_workline_code,
        )
        if not prepared.prepared or prepared.task is None or prepared.confirmation is None:
            reason = prepared.reason or PickingTaskPrepareNoopReason.WORKLINE_NOT_READY
            if reason is PickingTaskPrepareNoopReason.SELECTED_TASK_NOT_NEXT:
                async with self._sessions.begin() as db:
                    run = await self._require_run(db, run_id, for_update=True)
                    self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.TASK_PREPARE)
                    bind_step = await self._runs.get_step_for_update(db, run_id, IntegrationDebugPhase.BIND_TASK)
                    if bind_step is None:
                        raise RuntimeError("联调 run 缺少绑定任务步骤")
                    run.picking_task_id = None
                    run.task_id = None
                    run.issued_operation_id = None
                    run.status = IntegrationDebugRunStatus.WAITING_TASK
                    run.current_phase = IntegrationDebugPhase.BIND_TASK
                    run.updated_by = actor_id
                    run.increment_version()
                    bind_step.status = "PENDING"
                    bind_step.result_summary_json = {}
                    bind_step.updated_by = actor_id
                    snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
                await self._publish(snapshot)
                return snapshot
            messages = {
                PickingTaskPrepareNoopReason.SELECTED_TASK_NOT_NEXT: "所选任务不是当前 MANUAL 队头，请按 dispatch_sequence 重新选择",
                PickingTaskPrepareNoopReason.NO_ELIGIBLE_TASK: "当前没有可 prepare 的 MANUAL PickingTask",
                PickingTaskPrepareNoopReason.WORKLINE_NOT_READY: "WorkLine 未满足 prepare 条件或已有未完成工作",
            }
            raise IntegrationDebugConflict(messages[reason])
        if prepared.task.task_id != selected_task_id or prepared.confirmation.id is None:
            raise RuntimeError("prepare 结果与联调选择不一致或缺少持久身份")

        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.TASK_PREPARE)
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None:
                raise IntegrationDebugConflict("client_request_id 已用于其它联调动作")
            await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase.TASK_PREPARE,
                status="WAITING",
                actor_id=actor_id,
                client_request_id=client_request_id,
                operation=PICKING_TASK_PREPARE_OPERATION,
                operation_id=prepared.confirmation.operation_id,
                request=prepared.confirmation.request_payload["data"],
                wms_confirmation_id=prepared.confirmation.id,
            )
            run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def refresh_plan_resources(
        self,
        run_id: str,
        *,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            current_phase = IntegrationDebugPhase(run.current_phase)
            refreshable_phases = {
                IntegrationDebugPhase.PLAN_RECEIPT,
                IntegrationDebugPhase.RACK_TRANSPORT,
                IntegrationDebugPhase.RACK_ARRIVAL,
                IntegrationDebugPhase.BIN_INBOUND_BATCH,
                IntegrationDebugPhase.BIN_TRANSPORT,
                IntegrationDebugPhase.POINT1_ARRIVAL,
                IntegrationDebugPhase.POINT2_SCAN,
                IntegrationDebugPhase.WORK_ADMISSION,
                IntegrationDebugPhase.WORK_COMPLETION,
                IntegrationDebugPhase.POINT2_RELEASE,
                IntegrationDebugPhase.POINT3_ROUTE,
                IntegrationDebugPhase.RETURN_BUFFER,
                IntegrationDebugPhase.BIN_RETURN_BATCH,
                IntegrationDebugPhase.BIN_RETURN_TRANSPORT,
                IntegrationDebugPhase.RACK_DEPARTURE,
                IntegrationDebugPhase.TASK_COMPLETION,
            }
            if current_phase not in refreshable_phases:
                raise IntegrationDebugConflict(f"当前步骤 {current_phase} 不能同步 plan_delta 资源")
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("联调 run 尚未选择 PickingTask")
            task = await self._runs.get_picking_task(db, run.task_id)
            if (
                task is None
                or task.id != run.picking_task_id
                or task.workline_id != run.workline_id
                or task.status != PickingTaskStatus.EXECUTING
                or task.last_applied_plan_revision < 1
                or task.target_rack_id is None
                or task.target_rack_face is None
            ):
                raise IntegrationDebugConflict("尚未收到并应用该任务的初始 plan_delta")
            if task.plan_blocked_evidence_id is not None:
                raise IntegrationDebugConflict("plan_delta 处于冲突对账，不能启动资源搬运")
            resources = await self._runs.list_plan_resources(db, task.id)
            plan = {
                "plan_revision": task.last_applied_plan_revision,
                "target_rack": {"rack_id": task.target_rack_id, "rack_face": task.target_rack_face},
                **resources,
            }
            run.configuration_json = {**run.configuration_json, "plan_resources": plan}
            if current_phase is IntegrationDebugPhase.PLAN_RECEIPT:
                run.current_phase = IntegrationDebugPhase.RACK_TRANSPORT
                run.status = IntegrationDebugRunStatus.ACTIVE
            run.updated_by = actor_id
            run.increment_version()
            await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase.PLAN_RECEIPT,
                status="SUCCEEDED",
                actor_id=actor_id,
                result=plan,
            )
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_bin_inbound_batch(
        self,
        run_id: str,
        *,
        client_request_id: str,
        request_data: BinInboundBatchData,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.BIN_INBOUND_BATCH)
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("inbound_batch 缺少 PickingTask")
            if request_data.task_id != run.task_id:
                raise IntegrationDebugContractError("inbound_batch data.task_id 必须等于本 run 已绑定的 PickingTask")
            rack_id = request_data.rack_id
            rack_face = request_data.rack_face
            plan = run.configuration_json.get("plan_resources")
            sources = plan.get("bin_source_racks") if isinstance(plan, dict) else None
            if not isinstance(sources, list) or not any(
                isinstance(item, dict) and item.get("rack_id") == rack_id and item.get("rack_face") == rack_face
                for item in sources
            ):
                raise IntegrationDebugContractError("inbound_batch 必须引用 plan_delta 中的五层料箱架及朝向")
            if profile_uses_real_transport(IntegrationDebugProfile(run.profile)):
                self._assert_rack_arrived_for_inbound_batch(
                    await self._runs.list_steps(db, run_id),
                    rack_id=rack_id,
                    rack_face=rack_face,
                )
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None and not isinstance(existing.operation_id, str):
                raise IntegrationDebugConflict("原 inbound_batch 联调步骤缺少 operation_id")
            operation_id = existing.operation_id if existing is not None else new_uuid7()
            intent = sdk.wms_operations.outbound_bin_inbound_batch(
                operation_id=operation_id,
                task_id=run.task_id,
                rack_id=rack_id,
                rack_face=rack_face,
                max_bin_count=request_data.max_bin_count,
            )
            payload = encode_inbound_batch(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
            if existing is None:
                self._assert_no_open_wms_action(
                    await self._runs.list_steps(db, run_id),
                    IntegrationDebugPhase.BIN_INBOUND_BATCH,
                    BIN_INBOUND_BATCH_OPERATION,
                )
                step = await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase.BIN_INBOUND_BATCH,
                    status="WAITING",
                    actor_id=actor_id,
                    client_request_id=client_request_id,
                    operation=BIN_INBOUND_BATCH_OPERATION,
                    operation_id=operation_id,
                    request=payload["data"],
                )
                acceptance = await self._confirmations.create_or_get(
                    db,
                    operation=BIN_INBOUND_BATCH_OPERATION,
                    operation_id=operation_id,
                    picking_task_id=run.picking_task_id,
                    request_payload=payload,
                    deadline_at=now + timedelta(minutes=30),
                    created_at=now,
                )
                if not isinstance(acceptance, WmsConfirmationAcceptance):
                    raise IntegrationDebugConflict("inbound_batch identity 内容冲突")
                step.wms_confirmation_id = acceptance.confirmation.id
                defer_wakeup(db, task_queue_gateway.enqueue_wms_confirmations)
                run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                run.updated_by = actor_id
                run.increment_version()
            else:
                self._assert_matching_wms_replay(
                    existing,
                    run_id=run_id,
                    operation=BIN_INBOUND_BATCH_OPERATION,
                    request=payload["data"],
                )
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_bin_return_batch(
        self,
        run_id: str,
        *,
        client_request_id: str,
        request_data: BinReturnBatchData,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.BIN_RETURN_BATCH)
            if run.bin_code is None:
                raise IntegrationDebugContractError("return_batch 缺少已完成作业的 Bin")
            if request_data.workline_code != run.workline_code:
                raise IntegrationDebugContractError("return_batch data.workline_code 必须等于本 run 的 WorkLine")
            if len(request_data.return_candidates) != 1:
                raise IntegrationDebugContractError("当前临时联调页面每次 return_batch 只支持一个 Bin")
            candidate = request_data.return_candidates[0]
            if candidate.bin_code != run.bin_code:
                raise IntegrationDebugContractError(
                    "return_batch data.return_candidates[0].bin_code 必须等于本 run 的 Bin"
                )
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None and not isinstance(existing.operation_id, str):
                raise IntegrationDebugConflict("原 return_batch 联调步骤缺少 operation_id")
            operation_id = existing.operation_id if existing is not None else new_uuid7()
            intent = sdk.wms_operations.outbound_bin_return_batch(
                operation_id=operation_id,
                workline_code=request_data.workline_code,
                rack_id=request_data.rack_id,
                rack_face=request_data.rack_face,
                return_candidates=(
                    sdk.BinReturnCandidate(candidate.sequence_no, candidate.bin_code, candidate.source.location_code),
                ),
            )
            payload = encode_return_batch(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
            if existing is None:
                self._assert_no_open_wms_action(
                    await self._runs.list_steps(db, run_id),
                    IntegrationDebugPhase.BIN_RETURN_BATCH,
                    BIN_RETURN_BATCH_OPERATION,
                )
                step = await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase.BIN_RETURN_BATCH,
                    status="WAITING",
                    actor_id=actor_id,
                    client_request_id=client_request_id,
                    operation=BIN_RETURN_BATCH_OPERATION,
                    operation_id=operation_id,
                    request=payload["data"],
                )
                acceptance = await self._confirmations.create_or_get(
                    db,
                    operation=BIN_RETURN_BATCH_OPERATION,
                    operation_id=operation_id,
                    workline_id=run.workline_id,
                    request_payload=payload,
                    deadline_at=now + timedelta(minutes=30),
                    created_at=now,
                )
                if not isinstance(acceptance, WmsConfirmationAcceptance):
                    raise IntegrationDebugConflict("return_batch identity 内容冲突")
                step.wms_confirmation_id = acceptance.confirmation.id
                defer_wakeup(db, task_queue_gateway.enqueue_wms_confirmations)
                run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                run.updated_by = actor_id
                run.increment_version()
            else:
                self._assert_matching_wms_replay(
                    existing,
                    run_id=run_id,
                    operation=BIN_RETURN_BATCH_OPERATION,
                    request=payload["data"],
                )
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_rack_departure(
        self,
        run_id: str,
        *,
        client_request_id: str,
        request_data: RackDepartureData,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.RACK_DEPARTURE)
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("departure_decide 缺少 PickingTask")
            if request_data.task_id != run.task_id:
                raise IntegrationDebugContractError("departure_decide data.task_id 必须等于本 run 已绑定的 PickingTask")
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None and not isinstance(existing.operation_id, str):
                raise IntegrationDebugConflict("原 departure_decide 联调步骤缺少 operation_id")
            operation_id = existing.operation_id if existing is not None else new_uuid7()
            intent = sdk.wms_operations.outbound_rack_departure_decide(
                operation_id=operation_id,
                task_id=request_data.task_id,
                rack_id=request_data.rack_id,
                current_location=sdk.TransportRackPosition(request_data.current_location.location_code),
                current_face=request_data.current_face,
            )
            payload = encode_departure(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
            if existing is not None:
                self._assert_matching_wms_replay(
                    existing,
                    run_id=run_id,
                    operation=RACK_DEPARTURE_OPERATION,
                    request=payload["data"],
                )
                return self._snapshot(run, await self._runs.list_steps(db, run_id))
            self._assert_no_open_wms_action(
                await self._runs.list_steps(db, run_id),
                IntegrationDebugPhase.RACK_DEPARTURE,
                RACK_DEPARTURE_OPERATION,
            )
            step = await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase.RACK_DEPARTURE,
                status="WAITING",
                actor_id=actor_id,
                client_request_id=client_request_id,
                operation=RACK_DEPARTURE_OPERATION,
                operation_id=operation_id,
                request=payload["data"],
            )
            acceptance = await self._confirmations.create_or_get(
                db,
                operation=RACK_DEPARTURE_OPERATION,
                operation_id=operation_id,
                picking_task_id=run.picking_task_id,
                request_payload=payload,
                deadline_at=now + timedelta(minutes=30),
                created_at=now,
            )
            if not isinstance(acceptance, WmsConfirmationAcceptance):
                raise IntegrationDebugConflict("departure_decide identity 内容冲突")
            step.wms_confirmation_id = acceptance.confirmation.id
            defer_wakeup(db, task_queue_gateway.enqueue_wms_confirmations)
            run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_task_completion_confirm(
        self,
        run_id: str,
        *,
        client_request_id: str,
        request_data: CompletionConfirmData,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.TASK_COMPLETION)
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("completion_confirm 缺少 PickingTask")
            if request_data.task_id != run.task_id:
                raise IntegrationDebugContractError(
                    "completion_confirm data.task_id 必须等于本 run 已绑定的 PickingTask"
                )
            task = await self._runs.get_picking_task(db, run.task_id)
            if task is None:
                raise IntegrationDebugNotFound("PickingTask 不存在")
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None and not isinstance(existing.operation_id, str):
                raise IntegrationDebugConflict("原 completion_confirm 联调步骤缺少 operation_id")
            operation_id = existing.operation_id if existing is not None else new_uuid7()
            intent = sdk.wms_operations.outbound_picking_task_completion_confirm(
                operation_id=operation_id,
                task_id=request_data.task_id,
                last_applied_plan_revision=request_data.last_applied_plan_revision,
            )
            payload = encode_completion_confirm(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
            if existing is not None:
                self._assert_matching_wms_replay(
                    existing,
                    run_id=run_id,
                    operation=COMPLETION_CONFIRM_OPERATION,
                    request=payload["data"],
                )
                return self._snapshot(run, await self._runs.list_steps(db, run_id))
            self._assert_no_open_wms_action(
                await self._runs.list_steps(db, run_id),
                IntegrationDebugPhase.TASK_COMPLETION,
                COMPLETION_CONFIRM_OPERATION,
            )
            step = await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase.TASK_COMPLETION,
                status="WAITING",
                actor_id=actor_id,
                client_request_id=client_request_id,
                operation=COMPLETION_CONFIRM_OPERATION,
                operation_id=operation_id,
                request=payload["data"],
            )
            acceptance = await self._confirmations.create_or_get(
                db,
                operation=COMPLETION_CONFIRM_OPERATION,
                operation_id=operation_id,
                picking_task_id=run.picking_task_id,
                request_payload=payload,
                deadline_at=now + timedelta(minutes=30),
                created_at=now,
            )
            if not isinstance(acceptance, WmsConfirmationAcceptance):
                raise IntegrationDebugConflict("completion_confirm identity 内容冲突")
            step.wms_confirmation_id = acceptance.confirmation.id
            defer_wakeup(db, task_queue_gateway.enqueue_wms_confirmations)
            run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def record_point2_scan(
        self,
        run_id: str,
        *,
        bin_code: str,
        scanned_at: int,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now_ms = int(timezone.now_utc().timestamp() * 1000)
        if re.fullmatch(BUSINESS_IDENTIFIER_PATTERN, bin_code) is None or scanned_at <= 0 or scanned_at > now_ms:
            raise IntegrationDebugContractError("point2 扫码 Bin 和发生时间无效")
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.POINT2_SCAN)
            run.bin_code = bin_code
            run.configuration_json = {**run.configuration_json, "point2_scanned_at": scanned_at}
            run.current_phase = IntegrationDebugPhase.WORK_ADMISSION
            run.updated_by = actor_id
            run.increment_version()
            await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase.POINT2_SCAN,
                status="SUCCEEDED",
                actor_id=actor_id,
                result={"bin_code": bin_code, "scanned_at": scanned_at},
            )
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_work_admission(
        self,
        run_id: str,
        *,
        client_request_id: str,
        request_data: ManualBinAdmissionData,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        timestamp = int(timezone.to_utc(now).timestamp() * 1000)
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.WORK_ADMISSION)
            if run.task_id is None or request_data.task_id != run.task_id:
                raise IntegrationDebugContractError("准入 task_id 必须等于本 run 已绑定的 PickingTask")
            if run.bin_code is None:
                raise IntegrationDebugContractError("必须先记录 point2 实际扫码 Bin")
            run.bin_code = request_data.bin_code
            run.configuration_json = {
                **run.configuration_json,
                "point2_scanned_at": request_data.scanned_at,
            }
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None and not isinstance(existing.operation_id, str):
                raise IntegrationDebugConflict("原 work_admission 联调步骤缺少 operation_id")
            operation_id = existing.operation_id if existing is not None else new_uuid7()
            intent = sdk.wms_operations.outbound_manual_bin_work_admission(
                operation_id=operation_id,
                task_id=run.task_id,
                bin_code=request_data.bin_code,
                scanned_at=request_data.scanned_at,
            )
            payload = encode_admission(intent, timestamp=timestamp)
            if existing is None:
                self._assert_no_open_wms_action(
                    await self._runs.list_steps(db, run_id),
                    IntegrationDebugPhase.WORK_ADMISSION,
                    MANUAL_BIN_ADMISSION_OPERATION,
                )
                step = await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase.WORK_ADMISSION,
                    status="WAITING",
                    actor_id=actor_id,
                    client_request_id=client_request_id,
                    operation=MANUAL_BIN_ADMISSION_OPERATION,
                    operation_id=operation_id,
                    request=payload["data"],
                )
                acceptance = await self._confirmations.create_or_get(
                    db,
                    operation=MANUAL_BIN_ADMISSION_OPERATION,
                    operation_id=operation_id,
                    workline_id=run.workline_id,
                    request_payload=payload,
                    deadline_at=now + timedelta(minutes=30),
                    created_at=now,
                )
                if not isinstance(acceptance, WmsConfirmationAcceptance):
                    raise IntegrationDebugConflict("WMS operation identity 内容冲突")
                step.wms_confirmation_id = acceptance.confirmation.id
                defer_wakeup(db, task_queue_gateway.enqueue_wms_confirmations)
                run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                run.updated_by = actor_id
                run.increment_version()
            else:
                self._assert_matching_wms_replay(
                    existing,
                    run_id=run_id,
                    operation=MANUAL_BIN_ADMISSION_OPERATION,
                    request=payload["data"],
                )
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def create_transport_action(
        self,
        run_id: str,
        *,
        action: IntegrationTransportAction,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            transport_phases = {
                IntegrationDebugPhase.RACK_TRANSPORT,
                IntegrationDebugPhase.BIN_TRANSPORT,
                IntegrationDebugPhase.BIN_RETURN_TRANSPORT,
                IntegrationDebugPhase.RACK_DEPARTURE,
            }
            if IntegrationDebugPhase(run.current_phase) not in transport_phases:
                raise IntegrationDebugConflict(f"当前步骤 {run.current_phase} 不能创建 Transport")
            expected_request = {
                "kind": action.kind,
                "rack_id": action.rack_id,
                "bin_code": action.bin_code,
                "source": action.source,
                "target": action.target,
                "rcs_template_id": action.rcs_template_id,
                "target_face": action.target_face,
            }
            if IntegrationDebugPhase(run.current_phase) is IntegrationDebugPhase.BIN_TRANSPORT:
                expected_request["inbound_bins"] = deepcopy(run.configuration_json.get("inbound_bins"))
            real_transport = profile_uses_real_transport(IntegrationDebugProfile(run.profile))
            existing = await self._runs.get_step_by_client_request_id(db, action.client_request_id, for_update=True)
            if existing is not None:
                if (
                    existing.run_id != run_id
                    or existing.request_summary_json != expected_request
                    or (real_transport and existing.transport_task_id is None)
                ):
                    raise IntegrationDebugConflict("client_request_id 已用于其它联调动作或请求内容已变化")
                return self._snapshot(run, await self._runs.list_steps(db, run_id))
            self._assert_transport_member_not_created(
                await self._runs.list_steps(db, run_id),
                IntegrationDebugPhase(run.current_phase),
                action,
            )
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("Transport 缺少已绑定 PickingTask")
            task = await self._runs.get_picking_task(db, run.task_id, for_update=True)
            if (
                task is None
                or task.id != run.picking_task_id
                or task.workline_id != run.workline_id
                or task.status != PickingTaskStatus.EXECUTING
                or task.plan_blocked_evidence_id is not None
            ):
                raise IntegrationDebugConflict("PickingTask 不可执行或 plan_delta 正在冲突对账，不能创建新 Transport")
            plan = run.configuration_json.get("plan_resources")
            planned_racks: set[str] = set()
            if isinstance(plan, dict):
                target = plan.get("target_rack")
                if isinstance(target, dict) and isinstance(target.get("rack_id"), str):
                    planned_racks.add(target["rack_id"])
                for key in ("direct_picks", "bin_source_racks"):
                    rows = plan.get(key)
                    if isinstance(rows, list):
                        planned_racks.update(
                            row["rack_id"]
                            for row in rows
                            if isinstance(row, dict) and isinstance(row.get("rack_id"), str)
                        )
            if action.rack_id not in planned_racks:
                raise IntegrationDebugContractError("Transport rack_id 必须来自已应用的 plan_delta 资源")
            if IntegrationDebugPhase(run.current_phase) is IntegrationDebugPhase.RACK_DEPARTURE:
                ready_rack_id = run.configuration_json.get("departure_ready_rack_id")
                ready_destination = run.configuration_json.get("rack_destination")
                if ready_rack_id != action.rack_id or ready_destination != action.target:
                    raise IntegrationDebugContractError(
                        "回库 Transport 必须先取得该货架的 departure_decide READY，并使用 WMS 返回的 rack_destination"
                    )
            bin_moves = self._validate_batch_transport(
                IntegrationDebugPhase(run.current_phase), run.configuration_json, action
            )
            if run.workline_code == "KT16":
                self._validate_manual_outbound_transport(IntegrationDebugPhase(run.current_phase), action, plan)
            request = build_transport_request(action, bin_moves=bin_moves)
            handle = await self._transport.create_debug_task_in_session(db, request) if real_transport else None
            await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase(run.current_phase),
                status="WAITING" if real_transport else "SUCCEEDED",
                actor_id=actor_id,
                client_request_id=action.client_request_id,
                request=expected_request,
                result={} if real_transport else {"simulated": True},
                transport_task_id=handle.transport_task_id if handle is not None else None,
            )
            run.status = (
                IntegrationDebugRunStatus.WAITING_EXTERNAL if real_transport else IntegrationDebugRunStatus.ACTIVE
            )
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def refresh_transport_action(
        self,
        run_id: str,
        *,
        client_request_id: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions() as db:
            run = await self._require_run(db, run_id)
            self._assert_operator_and_version(run, expected_version, actor_id)
            step = await self._runs.get_step_by_client_request_id(db, client_request_id)
            if step is None or step.run_id != run_id or step.transport_task_id is None:
                raise IntegrationDebugNotFound("未找到该 run 的真实 Transport 动作")
            transport_task_id = step.transport_task_id
        transport = await self._transport.get_task_snapshot(transport_task_id)
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            step = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if step is None or step.run_id != run_id or step.transport_task_id != transport_task_id:
                raise IntegrationDebugConflict("Transport 联调步骤已变化")
            previous_reason_code = step.reason_code
            updates_current_run = step.phase == run.current_phase and (
                run.status in {IntegrationDebugRunStatus.ACTIVE, IntegrationDebugRunStatus.WAITING_EXTERNAL}
                or (
                    run.status == IntegrationDebugRunStatus.NEEDS_ATTENTION
                    and run.attention_code == previous_reason_code
                )
            )
            step.result_summary_json = transport.result or {"status": transport.status}
            if transport.status == "SUCCEEDED":
                step.status = "SUCCEEDED"
                step.reason_code = None
                arrival_report_frozen = await self._freeze_return_rack_arrival_report(
                    db,
                    run,
                    step,
                    actor_id=actor_id,
                )
                if updates_current_run:
                    if arrival_report_frozen:
                        run.current_phase = IntegrationDebugPhase.RACK_ARRIVAL
                        run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                    else:
                        run.status = IntegrationDebugRunStatus.ACTIVE
                    run.attention_code = None
            elif transport.status in {"FAILED", "REJECTED", "RECONCILING"}:
                step.status = "NEEDS_ATTENTION"
                step.reason_code = transport.reason_code or f"TRANSPORT_{transport.status}"
                if updates_current_run:
                    run.status = IntegrationDebugRunStatus.NEEDS_ATTENTION
                    run.attention_code = step.reason_code
            else:
                step.status = "WAITING"
                step.reason_code = None
                if updates_current_run:
                    run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                    run.attention_code = None
            step.updated_by = actor_id
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def refresh_wms_action(
        self,
        run_id: str,
        *,
        client_request_id: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            step = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if step is None or step.run_id != run_id or step.wms_confirmation_id is None:
                raise IntegrationDebugNotFound("未找到该 run 的 WMS 动作")
            confirmation = await self._runs.get_confirmation(db, step.wms_confirmation_id)
            if confirmation is None:
                raise IntegrationDebugNotFound("WmsConfirmation 不存在")
            if confirmation.status == WmsConfirmationStatus.RECONCILING:
                step.status = "NEEDS_ATTENTION"
                step.reason_code = "WMS_CONFIRMATION_RECONCILING"
                run.status = IntegrationDebugRunStatus.NEEDS_ATTENTION
                run.attention_code = step.reason_code
            elif confirmation.status == WmsConfirmationStatus.COMPLETED:
                should_advance = step.status != "SUCCEEDED" and step.phase == run.current_phase
                step.status = "SUCCEEDED"
                response = (
                    await self._runs.get_evidence(db, confirmation.response_evidence_id)
                    if confirmation.response_evidence_id is not None
                    else None
                )
                response_payload = response.normalized_payload if response is not None else {}
                response_data = response_payload.get("data") if isinstance(response_payload, dict) else None
                result_summary = (
                    response_payload
                    if isinstance(response_payload, dict) and response_payload
                    else {"response_result": confirmation.response_result}
                )
                replacement_history = step.result_summary_json.get("request_replacement_history")
                if isinstance(replacement_history, list) and replacement_history:
                    result_summary = {**result_summary, "request_replacement_history": replacement_history}
                step.result_summary_json = result_summary
                if should_advance:
                    if step.operation == COMPLETION_CONFIRM_OPERATION and confirmation.response_result == "COMPLETED":
                        if run.task_id is None or run.picking_task_id is None:
                            raise IntegrationDebugContractError("completion_confirm 缺少已绑定 PickingTask")
                        task = await self._runs.get_picking_task(db, run.task_id, for_update=True)
                        if (
                            task is None
                            or task.id != run.picking_task_id
                            or task.status not in {PickingTaskStatus.EXECUTING, PickingTaskStatus.EXECUTION_COMPLETED}
                        ):
                            raise IntegrationDebugConflict(
                                "completion_confirm 返回 COMPLETED，但本地 PickingTask 状态不匹配"
                            )
                        task.status = PickingTaskStatus.EXECUTION_COMPLETED
                    self._advance_completed_wms_action(
                        run,
                        step,
                        response_result=confirmation.response_result,
                        response_data=response_data,
                    )
                    if (
                        step.operation == MANUAL_BIN_ADMISSION_OPERATION
                        and confirmation.response_result == "WORK_REQUIRED"
                    ):
                        wait_started_at = getattr(response, "received_at", None)
                        if not isinstance(wait_started_at, datetime):
                            wait_started_at = timezone.now_for_db()
                        run.configuration_json = {
                            **run.configuration_json,
                            "work_completion_wait_started_at": int(timezone.to_utc(wait_started_at).timestamp() * 1000),
                        }
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def retry_wms_action(
        self,
        run_id: str,
        *,
        client_request_id: str,
        wms_original_prepare_voided_confirmed: bool,
        request_data: PickingTaskPrepareData,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        if not wms_original_prepare_voided_confirmed:
            raise IntegrationDebugContractError("必须先确认 WMS 已作废原 prepare 且不会再发送对应 plan_delta")
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            if (
                run.current_phase != IntegrationDebugPhase.TASK_PREPARE
                or run.status != IntegrationDebugRunStatus.NEEDS_ATTENTION
                or run.attention_code != "WMS_CONFIRMATION_RECONCILING"
            ):
                raise IntegrationDebugConflict("当前 run 不处于 prepare WMS 对账状态")
            if request_data.task_id != run.task_id:
                raise IntegrationDebugContractError("prepare data.task_id 必须等于本 run 已绑定的 PickingTask")
            step = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if (
                step is None
                or step.run_id != run_id
                or step.phase != IntegrationDebugPhase.TASK_PREPARE
                or step.operation != PICKING_TASK_PREPARE_OPERATION
                or step.wms_confirmation_id is None
            ):
                raise IntegrationDebugNotFound("未找到该 run 的 prepare WMS 动作")
            confirmation = await self._runs.get_confirmation(db, step.wms_confirmation_id, for_update=True)
            if (
                confirmation is None
                or confirmation.operation != PICKING_TASK_PREPARE_OPERATION
                or confirmation.operation_id != step.operation_id
            ):
                raise IntegrationDebugConflict("prepare step 与 WmsConfirmation 身份不匹配")
            persisted_data = confirmation.request_payload.get("data")
            if not isinstance(persisted_data, dict) or persisted_data.get("task_id") != run.task_id:
                raise IntegrationDebugConflict("prepare confirmation 请求正文与当前任务不匹配")
            if run.picking_task_id is None or run.task_id is None:
                raise IntegrationDebugConflict("prepare run 缺少 PickingTask 身份")
            old_history = step.result_summary_json.get("request_replacement_history")
            history = list(old_history) if isinstance(old_history, list) else []
            history.append(
                {
                    "operation_id": confirmation.operation_id,
                    "request": confirmation.request_payload,
                    "attempt_count": confirmation.attempt_count,
                    "last_dispatch_at": (
                        confirmation.last_dispatch_at.isoformat()
                        if isinstance(confirmation.last_dispatch_at, datetime)
                        else None
                    ),
                    "reason": "WMS_CONFIRMED_ORIGINAL_PREPARE_VOIDED",
                }
            )
            await self._confirmations.supersede_after_wms_void(
                db,
                confirmation,
                changed_at=now,
            )
            operation_id = new_uuid7()
            request = encode_prepare_request(
                sdk.wms_operations.outbound_picking_task_prepare(
                    operation_id=operation_id,
                    task_id=run.task_id,
                    work_line_code=request_data.workline_code,
                ),
                timestamp=int(timezone.to_utc(now).timestamp() * 1000),
            )
            replacement = await self._confirmations.create_or_get(
                db,
                operation=PICKING_TASK_PREPARE_OPERATION,
                operation_id=operation_id,
                picking_task_id=run.picking_task_id,
                request_payload=request,
                deadline_at=now + timedelta(seconds=30),
                created_at=now,
            )
            if not isinstance(replacement, WmsConfirmationAcceptance) or replacement.duplicate:
                raise RuntimeError("重发的 prepare identity 未创建唯一 WmsConfirmation")
            if replacement.confirmation.id is None:
                raise RuntimeError("重发的 prepare confirmation 缺少持久身份")
            step.operation_id = replacement.confirmation.operation_id
            step.wms_confirmation_id = replacement.confirmation.id
            step.request_summary_json = request["data"]
            step.result_summary_json = {"request_replacement_history": history}
            defer_wakeup(db, task_queue_gateway.enqueue_wms_confirmations)
            step.status = "WAITING"
            step.reason_code = None
            step.updated_by = actor_id
            run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
            run.attention_code = None
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    @staticmethod
    def _advance_completed_wms_action(
        run: IntegrationRun,
        step: IntegrationRunStep,
        *,
        response_result: str | None,
        response_data: object,
    ) -> None:
        if step.operation == PICKING_TASK_PREPARE_OPERATION:
            if response_result != "PREPARE_ACCEPTED":
                raise IntegrationDebugConflict("prepare 响应必须为 PREPARE_ACCEPTED")
            run.current_phase = IntegrationDebugPhase.PLAN_RECEIPT
            run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
        elif step.operation == MANUAL_BIN_ADMISSION_OPERATION:
            phases = {
                "WORK_REQUIRED": (IntegrationDebugPhase.WORK_COMPLETION, IntegrationDebugRunStatus.WAITING_EXTERNAL),
                "NO_WORK": (IntegrationDebugPhase.POINT2_RELEASE, IntegrationDebugRunStatus.ACTIVE),
                "WAIT": (IntegrationDebugPhase.WORK_ADMISSION, IntegrationDebugRunStatus.ACTIVE),
            }
            try:
                run.current_phase, run.status = phases[response_result]
            except KeyError as error:
                raise IntegrationDebugConflict("任务准入响应结果不在固定联合内") from error
            run.configuration_json = {
                **run.configuration_json,
                "manual_bin_admission_result": response_result,
            }
            if response_result == "WORK_REQUIRED":
                task_id = response_data.get("task_id") if isinstance(response_data, dict) else None
                if not isinstance(task_id, str) or not task_id:
                    raise IntegrationDebugConflict("WORK_REQUIRED 响应缺少 task_id")
                run.configuration_json = {**run.configuration_json, "admission_task_id": task_id}
        elif step.operation == BIN_INBOUND_BATCH_OPERATION:
            IntegrationDebugService._advance_inbound_batch(run, response_result, response_data)
        elif step.operation == BIN_RETURN_BATCH_OPERATION:
            IntegrationDebugService._advance_return_batch(run, response_result, response_data)
        elif step.operation == RACK_DEPARTURE_OPERATION:
            IntegrationDebugService._advance_departure(run, step, response_result, response_data)
        elif step.operation == RETURN_RACK_ARRIVAL_REPORT_OPERATION:
            run.status = IntegrationDebugRunStatus.ACTIVE
        elif step.operation == COMPLETION_CONFIRM_OPERATION:
            IntegrationDebugService._advance_completion(run, response_result)

    @staticmethod
    def _advance_inbound_batch(run: IntegrationRun, response_result: str | None, response_data: object) -> None:
        if response_result == "READY" and isinstance(response_data, dict):
            try:
                batch = BinInboundBatchReady.model_validate({"result": "READY", "bins": response_data.get("bins")})
            except ValidationError as exc:
                raise IntegrationDebugConflict("inbound_batch READY 必须包含 1–4 个有效且不重复的料箱") from exc
            run.configuration_json = {**run.configuration_json, "inbound_bins": batch.model_dump(mode="json")["bins"]}
            run.current_phase = IntegrationDebugPhase.BIN_TRANSPORT
            run.status = IntegrationDebugRunStatus.ACTIVE
        elif response_result == "NO_BATCH":
            run.status = IntegrationDebugRunStatus.ACTIVE
        elif response_result == "RACK_FACE_DONE":
            run.status = IntegrationDebugRunStatus.NEEDS_ATTENTION
            run.attention_code = "RACK_FACE_DONE"
            run.attention_detail = "WMS 已关闭当前来源货架面；当前联调不自动调度下一来源面，请现场协调后关闭本 run。"
        else:
            raise IntegrationDebugConflict("inbound_batch 响应结果不在固定联合内")

    @staticmethod
    def _advance_return_batch(run: IntegrationRun, response_result: str | None, response_data: object) -> None:
        if response_result == "READY" and isinstance(response_data, dict):
            moves = response_data.get("moves")
            if not isinstance(moves, list) or len(moves) != 1:
                raise IntegrationDebugConflict("当前临时联调页面要求 return_batch READY 恰好返回 1 个 move")
            run.configuration_json = {**run.configuration_json, "return_moves": moves}
            run.current_phase = IntegrationDebugPhase.BIN_RETURN_TRANSPORT
            run.status = IntegrationDebugRunStatus.ACTIVE
        elif response_result == "NO_BATCH":
            run.status = IntegrationDebugRunStatus.ACTIVE
        else:
            raise IntegrationDebugConflict("return_batch 响应结果不在固定联合内")

    @staticmethod
    def _advance_departure(
        run: IntegrationRun,
        step: IntegrationRunStep,
        response_result: str | None,
        response_data: object,
    ) -> None:
        if response_result == "READY" and isinstance(response_data, dict):
            destination = response_data.get("rack_destination")
            if (
                not isinstance(destination, dict)
                or destination.get("type") != "RACK_POSITION"
                or not isinstance(destination.get("location_code"), str)
            ):
                raise IntegrationDebugConflict("departure_decide READY 缺少 rack_destination")
            if (
                run.workline_code == "KT16"
                and destination["location_code"] != MANUAL_OUTBOUND_SITE_CONFIGURATION["return_zone_code"]
            ):
                raise IntegrationDebugContractError("KT16 departure_decide READY 的 rack_destination 必须为 WH05")
            rack_id = step.request_summary_json.get("rack_id")
            if not isinstance(rack_id, str) or not rack_id:
                raise IntegrationDebugConflict("departure_decide READY 缺少原请求 rack_id")
            run.configuration_json = {
                **run.configuration_json,
                # KT16 现场约定 CTU03 目标使用库区代码；WMS 的业务位置类型只在本适配层转换。
                "rack_destination": {"kind": "ZONE", "location_code": destination["location_code"]},
                "departure_ready_rack_id": rack_id,
            }
            run.status = IntegrationDebugRunStatus.ACTIVE
        elif response_result == "WAIT":
            run.configuration_json = {
                key: value
                for key, value in run.configuration_json.items()
                if key not in {"rack_destination", "departure_ready_rack_id"}
            }
            run.status = IntegrationDebugRunStatus.ACTIVE
        else:
            raise IntegrationDebugConflict("departure_decide 响应结果不在固定联合内")

    @staticmethod
    def _advance_completion(run: IntegrationRun, response_result: str | None) -> None:
        if response_result == "COMPLETED":
            run.current_phase = IntegrationDebugPhase.CLEANUP
            run.status = IntegrationDebugRunStatus.COMPLETED
            run.completed_at = timezone.now_for_db()
        elif response_result in {"BUSINESS_IN_PROGRESS", "PLAN_REVISION_STALE"}:
            run.status = IntegrationDebugRunStatus.ACTIVE
        else:
            raise IntegrationDebugConflict("completion_confirm 响应结果不在固定联合内")

    async def bind_work_completion(
        self,
        run_id: str,
        *,
        operation_id: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.WORK_COMPLETION)
            evidence = await self._runs.get_evidence_by_operation(db, MANUAL_BIN_COMPLETED_OPERATION, operation_id)
            payload = evidence.normalized_payload if evidence is not None else None
            data = payload.get("data") if isinstance(payload, dict) else None
            completed_at = data.get("completed_at") if isinstance(data, dict) else None
            scanned_at = run.configuration_json.get("point2_scanned_at")
            admission_task_id = run.configuration_json.get("admission_task_id")
            if (
                evidence is None
                or evidence.apply_status != InboundEvidenceApplyStatus.PENDING
                or not isinstance(data, dict)
                or not isinstance(admission_task_id, str)
                or data.get("task_id") != admission_task_id
                or data.get("bin_code") != run.bin_code
                or data.get("result") not in {"NORMAL", "NG"}
                or not isinstance(completed_at, int)
                or not isinstance(scanned_at, int)
                or completed_at < scanned_at
            ):
                raise IntegrationDebugContractError(
                    "完成决定必须匹配本 run 的 task_id、实际 bin_code，且 completed_at 不得早于 point2 扫码"
                )
            wait_started_at = run.configuration_json.get("work_completion_wait_started_at")
            evidence_received_at = getattr(evidence, "received_at", None)
            received_at_ms = (
                int(timezone.to_utc(evidence_received_at).timestamp() * 1000)
                if isinstance(evidence_received_at, datetime)
                else None
            )
            arrived_before_wait = (
                isinstance(wait_started_at, int)
                and not isinstance(wait_started_at, bool)
                and received_at_ms is not None
                and received_at_ms < wait_started_at
            )
            await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase.WORK_COMPLETION,
                status="NEEDS_ATTENTION" if arrived_before_wait else "SUCCEEDED",
                actor_id=actor_id,
                operation=MANUAL_BIN_COMPLETED_OPERATION,
                operation_id=operation_id,
                result={"result": data.get("result"), "completed_at": completed_at},
            )
            if arrived_before_wait:
                evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
                evidence.processed_at = timezone.now_for_db()
                run.status = IntegrationDebugRunStatus.NEEDS_ATTENTION
                run.attention_code = "FIRST_COMPLETION_OUT_OF_WINDOW"
                run.attention_detail = "完成通知早于当前 point2 等待窗口，禁止自动绑定和放行"
            else:
                run.current_phase = IntegrationDebugPhase.POINT2_RELEASE
                run.status = IntegrationDebugRunStatus.ACTIVE
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def refresh_device_action(
        self,
        run_id: str,
        *,
        client_request_id: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions() as db:
            run = await self._require_run(db, run_id)
            self._assert_operator_and_version(run, expected_version, actor_id)
            step = await self._runs.get_step_by_client_request_id(db, client_request_id)
            if step is None or step.run_id != run_id or step.device_command_code is None:
                raise IntegrationDebugNotFound("未找到该 run 的真实 DeviceCommand 动作")
            command_code = step.device_command_code
        command = await self._device_commands.get_command_snapshot(command_code)
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            step = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if step is None or step.run_id != run_id or step.device_command_code != command_code:
                raise IntegrationDebugConflict("DeviceCommand 联调步骤已变化")
            previous_reason_code = step.reason_code
            updates_current_run = step.phase == run.current_phase and (
                run.status in {IntegrationDebugRunStatus.ACTIVE, IntegrationDebugRunStatus.WAITING_EXTERNAL}
                or (
                    run.status == IntegrationDebugRunStatus.NEEDS_ATTENTION
                    and run.attention_code == previous_reason_code
                )
            )
            status = command.status.value
            step.result_summary_json = {
                key: value
                for key, value in {
                    "status": status,
                    "failure_code": command.failure_code,
                    "reconciliation_reason": command.reconciliation_reason,
                }.items()
                if value is not None
            }
            if status == "SUCCEEDED":
                step.status = "SUCCEEDED"
                step.reason_code = None
                if updates_current_run:
                    run.status = IntegrationDebugRunStatus.ACTIVE
                    run.attention_code = None
            elif status in {"FAILED", "TIMED_OUT", "RECONCILING"}:
                step.status = "NEEDS_ATTENTION"
                step.reason_code = command.failure_code or command.reconciliation_reason or f"DEVICE_COMMAND_{status}"
                if updates_current_run:
                    run.status = IntegrationDebugRunStatus.NEEDS_ATTENTION
                    run.attention_code = step.reason_code
            else:
                step.status = "WAITING"
                step.reason_code = None
                if updates_current_run:
                    run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                    run.attention_code = None
            step.updated_by = actor_id
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def create_device_action(
        self,
        run_id: str,
        *,
        client_request_id: str,
        device_code: str,
        task_type: str,
        params: dict[str, object],
        timeout_ms: int,
        reason: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        simulated_snapshot: dict[str, Any] | None = None
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            current = IntegrationDebugPhase(run.current_phase)
            if current not in {IntegrationDebugPhase.POINT2_RELEASE, IntegrationDebugPhase.POINT3_ROUTE}:
                raise IntegrationDebugConflict("ECS 指令只允许在 point2 释放或 point3 分流节点创建")
            site_configuration = run.configuration_json.get("site_configuration")
            scan_device_codes = (
                site_configuration.get("scan_device_codes") if isinstance(site_configuration, dict) else None
            )
            if (
                not isinstance(scan_device_codes, list)
                or len(scan_device_codes) != 4
                or device_code not in scan_device_codes
            ):
                raise IntegrationDebugContractError("ECS device_code 必须来自本 run 冻结的扫码位")
            phase_steps = await self._runs.list_steps(db, run_id)
            real_ecs = profile_uses_real_ecs(IntegrationDebugProfile(run.profile))
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is None and any(
                step.phase == current and isinstance(step.request_summary_json.get("task_type"), str)
                for step in phase_steps
            ):
                raise IntegrationDebugConflict("本节点已创建 ECS 指令；请刷新原动作，禁止换 identity 重发")
            if existing is not None:
                stored_request = existing.request_summary_json
                original_creator = stored_request.get("created_by")
                endpoint = stored_request.get("endpoint_base_url")
                if not isinstance(original_creator, int):
                    raise IntegrationDebugConflict("原 DeviceCommand 联调步骤缺少创建者")
                if real_ecs and (not isinstance(endpoint, str) or not endpoint):
                    raise IntegrationDebugConflict("原 DeviceCommand 联调步骤缺少冻结 endpoint")
            else:
                original_creator = actor_id
                if real_ecs:
                    device = await device_service.get_device_by_code(db, device_code)
                    if device is None or not device.is_active or not device.endpoint_base_url:
                        raise IntegrationDebugContractError("Run 选择的设备未登记、未启用或缺少 endpoint")
                    endpoint = device.endpoint_base_url
                else:
                    endpoint = site_configuration.get("ecs_endpoint_base_url")
            if not isinstance(endpoint, str):
                raise IntegrationDebugContractError("KT16 缺少 ECS endpoint")
            endpoint = validate_device_endpoint_base_url(endpoint)
            validated_request = DeviceCommandRequestData.model_validate(
                {
                    "device_code": device_code,
                    "workline_id": None,
                    "execution_ref_type": MANUAL_DEBUG_REF_TYPE,
                    "execution_ref_id": client_request_id,
                    "material_execution_id": None,
                    "contract_key": "ecs.manual-debug.command",
                    "contract_version": "1.0",
                    "task_type": task_type,
                    "params": params,
                    "deadline_at": timezone.now_for_db() + timedelta(milliseconds=timeout_ms),
                    "trace_id": run_id,
                    "endpoint_base_url": endpoint,
                    "command_timeout_ms": timeout_ms,
                    "execution_reason": reason,
                }
            )
            request_without_creator: dict[str, object] = {
                "device_code": validated_request.device_code,
                "task_type": validated_request.task_type,
                "params": validated_request.params,
                "timeout_ms": timeout_ms,
                "reason": validated_request.execution_reason or "",
            }
            expected_request = {
                **request_without_creator,
                "created_by": original_creator,
                **({"endpoint_base_url": endpoint} if real_ecs else {}),
            }
            if existing is not None:
                if existing.run_id != run_id or stored_request != expected_request:
                    raise IntegrationDebugConflict("client_request_id 已用于其它联调动作或请求内容已变化")
                if existing.device_command_code is not None or not real_ecs:
                    return self._snapshot(run, await self._runs.list_steps(db, run_id))
            elif not real_ecs:
                await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase(run.current_phase),
                    status="SUCCEEDED",
                    actor_id=actor_id,
                    client_request_id=client_request_id,
                    request=expected_request,
                    result={"simulated": True},
                )
                run.status = IntegrationDebugRunStatus.ACTIVE
                run.updated_by = actor_id
                run.increment_version()
                simulated_snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
            elif existing is None:
                await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase(run.current_phase),
                    status="WAITING",
                    actor_id=actor_id,
                    client_request_id=client_request_id,
                    request=expected_request,
                )
        if simulated_snapshot is not None:
            await self._publish(simulated_snapshot)
            return simulated_snapshot
        handle = await self._device_commands.create_manual_debug_command(
            client_request_id=client_request_id,
            endpoint_base_url=endpoint,
            device_code=device_code,
            contract_key="ecs.manual-debug.command",
            contract_version="1.0",
            command_timeout_ms=timeout_ms,
            task_type=task_type,
            params=params,
            trace_id=run_id,
            execution_reason=reason,
            created_by=original_creator,
        )
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            step = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if step is None:
                raise RuntimeError("DeviceCommand 已创建但联调步骤缺失")
            step.device_command_code = handle.command_code
            step.updated_by = actor_id
            run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def close_run(
        self,
        run_id: str,
        *,
        wms_cleanup_confirmed: bool,
        site_cleanup_confirmed: bool,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        if not wms_cleanup_confirmed or not site_cleanup_confirmed:
            raise IntegrationDebugContractError("必须记录 WMS 团队处理状态，并确认现场已人工清理")
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            steps = await self._runs.list_steps(db, run_id)
            empty_waiting_run = (
                run.status == IntegrationDebugRunStatus.WAITING_TASK
                and run.current_phase == IntegrationDebugPhase.BIND_TASK
                and all(
                    step.client_request_id is None
                    and step.operation is None
                    and step.wms_confirmation_id is None
                    and step.transport_task_id is None
                    and step.device_command_code is None
                    for step in steps
                )
            )
            if (
                run.status not in {IntegrationDebugRunStatus.COMPLETED, IntegrationDebugRunStatus.NEEDS_ATTENTION}
                and not empty_waiting_run
            ):
                raise IntegrationDebugConflict("只有未启动、COMPLETED 或 NEEDS_ATTENTION run 可人工关闭")
            now = timezone.now_for_db()
            run.status = IntegrationDebugRunStatus.CLOSED_BY_OPERATOR
            run.current_phase = IntegrationDebugPhase.CLEANUP
            run.active_scope = None
            run.wms_cleanup_confirmed = True
            run.site_cleanup_confirmed = True
            run.closed_at = now
            run.updated_by = actor_id
            run.increment_version()
            await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase.CLEANUP,
                status="SUCCEEDED",
                actor_id=actor_id,
                result={"wms_cleanup_confirmed": True, "site_cleanup_confirmed": True},
            )
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def mark_completed(
        self,
        run_id: str,
        *,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            if run.status not in {IntegrationDebugRunStatus.ACTIVE, IntegrationDebugRunStatus.WAITING_EXTERNAL}:
                raise IntegrationDebugConflict("当前 run 不能标记完成")
            if run.current_phase != IntegrationDebugPhase.CLEANUP:
                raise IntegrationDebugConflict("必须推进到 CLEANUP 后才能标记完成")
            run.status = IntegrationDebugRunStatus.COMPLETED
            run.current_phase = IntegrationDebugPhase.CLEANUP
            run.completed_at = timezone.now_for_db()
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def confirm_current_phase(
        self,
        run_id: str,
        *,
        note: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        next_phases = {
            IntegrationDebugPhase.RACK_TRANSPORT: IntegrationDebugPhase.RACK_ARRIVAL,
            IntegrationDebugPhase.RACK_ARRIVAL: IntegrationDebugPhase.BIN_INBOUND_BATCH,
            IntegrationDebugPhase.BIN_TRANSPORT: IntegrationDebugPhase.POINT1_ARRIVAL,
            IntegrationDebugPhase.POINT1_ARRIVAL: IntegrationDebugPhase.POINT2_SCAN,
            IntegrationDebugPhase.POINT3_ROUTE: IntegrationDebugPhase.RETURN_BUFFER,
            IntegrationDebugPhase.RETURN_BUFFER: IntegrationDebugPhase.BIN_RETURN_BATCH,
            IntegrationDebugPhase.BIN_RETURN_TRANSPORT: IntegrationDebugPhase.RACK_DEPARTURE,
            IntegrationDebugPhase.RACK_DEPARTURE: IntegrationDebugPhase.TASK_COMPLETION,
        }
        if not note.strip():
            raise IntegrationDebugContractError("现场步骤确认必须填写简短记录")
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_operator_and_version(run, expected_version, actor_id)
            current = IntegrationDebugPhase(run.current_phase)
            target = next_phases.get(current)
            if target is None and current is not IntegrationDebugPhase.POINT2_RELEASE:
                raise IntegrationDebugConflict(f"当前步骤 {current} 不能人工确认推进")
            if current in {
                IntegrationDebugPhase.RACK_TRANSPORT,
                IntegrationDebugPhase.BIN_TRANSPORT,
                IntegrationDebugPhase.BIN_RETURN_TRANSPORT,
                IntegrationDebugPhase.RACK_DEPARTURE,
            }:
                phase_steps = [
                    step
                    for step in await self._runs.list_steps(db, run_id)
                    if step.phase == current
                    and (
                        step.transport_task_id is not None
                        or step.request_summary_json.get("kind") in {"MOVE_RACK", "ROTATE_RACK", "MOVE_BINS"}
                    )
                ]
                if not phase_steps or any(step.status != "SUCCEEDED" for step in phase_steps):
                    raise IntegrationDebugConflict("本阶段 Transport 尚未全部取得 SUCCEEDED 终态")
            if current is IntegrationDebugPhase.RACK_ARRIVAL and profile_uses_real_transport(
                IntegrationDebugProfile(run.profile)
            ):
                arrival_steps = [
                    step
                    for step in await self._runs.list_steps(db, run_id)
                    if step.phase == current and step.operation == RETURN_RACK_ARRIVAL_REPORT_OPERATION
                ]
                # 到位上报只适用于 direct_picks 退料货架；纯五层货架计划没有此 WMS 义务。
                plan = run.configuration_json.get("plan_resources")
                missing_required_report = not arrival_steps and (
                    not isinstance(plan, dict) or plan.get("direct_picks") != []
                )
                if missing_required_report or any(step.status != "SUCCEEDED" for step in arrival_steps):
                    raise IntegrationDebugConflict("货架到位上报尚未取得 WMS 完成确认")
            if current is IntegrationDebugPhase.POINT2_RELEASE:
                phase_steps = [
                    step
                    for step in await self._runs.list_steps(db, run_id)
                    if step.phase == IntegrationDebugPhase.POINT2_RELEASE
                ]
                profile = IntegrationDebugProfile(run.profile)
                release_succeeded = any(self._device_action_succeeded(step, profile) for step in phase_steps)
                if not release_succeeded:
                    raise IntegrationDebugConflict(
                        "point2 释放前必须取得本 run 的释放 DeviceCommand 或模拟动作 SUCCEEDED"
                    )
                admission_result = run.configuration_json.get("manual_bin_admission_result")
                if admission_result in {"NO_WORK", "WORK_REQUIRED"}:
                    target = IntegrationDebugPhase.POINT3_ROUTE
                else:
                    raise IntegrationDebugConflict("point2 释放缺少已冻结的 WORK_REQUIRED 或 NO_WORK 准入结果")
            if current is IntegrationDebugPhase.POINT2_RELEASE and admission_result == "WORK_REQUIRED":
                completion_step = next(
                    (
                        step
                        for step in reversed(await self._runs.list_steps(db, run_id))
                        if step.operation == MANUAL_BIN_COMPLETED_OPERATION and step.status == "SUCCEEDED"
                    ),
                    None,
                )
                if completion_step is None or completion_step.operation_id is None:
                    raise IntegrationDebugConflict("point2 释放缺少已绑定的完成决定")
                evidence = await self._runs.get_evidence_by_operation(
                    db,
                    MANUAL_BIN_COMPLETED_OPERATION,
                    completion_step.operation_id,
                    for_update=True,
                )
                if evidence is None or evidence.apply_status != InboundEvidenceApplyStatus.PENDING:
                    raise IntegrationDebugConflict("完成 Evidence 已被其它流程处理或不存在")
                evidence.apply_status = InboundEvidenceApplyStatus.APPLIED
                evidence.processed_at = timezone.now_for_db()
            if current is IntegrationDebugPhase.POINT3_ROUTE:
                profile = IntegrationDebugProfile(run.profile)
                steps = await self._runs.list_steps(db, run_id)
                phase_steps = [step for step in steps if step.phase == IntegrationDebugPhase.POINT3_ROUTE]
                if not any(self._device_action_succeeded(step, profile) for step in phase_steps):
                    raise IntegrationDebugConflict("point3 分流前必须取得本 run 的 DeviceCommand 或模拟动作 SUCCEEDED")
                task_type = self._expected_device_task_type(current, run.configuration_json, steps)
                target = (
                    IntegrationDebugPhase.RACK_DEPARTURE
                    if task_type == "MOVE_LEFT"
                    else IntegrationDebugPhase.RETURN_BUFFER
                )
            if target is None:
                raise RuntimeError("联调步骤缺少下一阶段")
            await self._append_step(
                db,
                run,
                phase=current,
                status="SUCCEEDED",
                actor_id=actor_id,
                result={"operator_confirmation": note.strip()},
            )
            run.current_phase = target
            run.status = IntegrationDebugRunStatus.ACTIVE
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def takeover(
        self,
        run_id: str,
        *,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            if run.version != expected_version:
                raise IntegrationDebugConflict("run version 已变化，请刷新后重试")
            if run.status == IntegrationDebugRunStatus.CLOSED_BY_OPERATOR:
                raise IntegrationDebugConflict("联调 run 已关闭")
            run.operator_user_id = actor_id
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def _require_run(self, db: AsyncSession, run_id: str, *, for_update: bool = False) -> IntegrationRun:
        run = await self._runs.get_run(db, run_id, for_update=for_update)
        if run is None:
            raise IntegrationDebugNotFound(f"IntegrationRun {run_id} 不存在")
        return run

    @staticmethod
    def _assert_operator_and_version(run: IntegrationRun, expected_version: int, actor_id: int) -> None:
        if run.operator_user_id != actor_id:
            raise IntegrationDebugConflict("当前用户不是该 run 的操作员；请先接管")
        if run.version != expected_version:
            raise IntegrationDebugConflict("run version 已变化，请刷新后重试")
        if run.status == IntegrationDebugRunStatus.CLOSED_BY_OPERATOR:
            raise IntegrationDebugConflict("联调 run 已关闭")

    def _assert_action(
        self,
        run: IntegrationRun,
        expected_version: int,
        actor_id: int,
        phase: IntegrationDebugPhase,
    ) -> None:
        self._assert_operator_and_version(run, expected_version, actor_id)
        if run.current_phase != phase:
            raise IntegrationDebugConflict(f"当前步骤为 {run.current_phase}，不能执行 {phase}")

    @staticmethod
    def _assert_no_open_wms_action(
        steps: list[IntegrationRunStep],
        phase: IntegrationDebugPhase,
        operation: str,
    ) -> None:
        if any(step.phase == phase and step.operation == operation and step.status != "SUCCEEDED" for step in steps):
            raise IntegrationDebugConflict("本节点已有未闭合的 WMS 请求；请刷新原请求，禁止换 identity 重发")

    @staticmethod
    def _assert_matching_wms_replay(
        step: IntegrationRunStep,
        *,
        run_id: str,
        operation: str,
        request: dict[str, Any],
    ) -> None:
        if step.run_id != run_id or step.operation != operation or step.request_summary_json != request:
            raise IntegrationDebugConflict("client_request_id 已用于其它联调动作或 WMS 请求内容已变化")

    @staticmethod
    def _assert_transport_member_not_created(
        steps: list[IntegrationRunStep],
        phase: IntegrationDebugPhase,
        action: IntegrationTransportAction,
    ) -> None:
        for step in steps:
            request = step.request_summary_json
            if (
                step.phase == phase
                and request.get("kind") == action.kind
                and request.get("rack_id") == action.rack_id
                and request.get("bin_code") == action.bin_code
            ):
                raise IntegrationDebugConflict("本节点已为该批次成员创建 Transport；请刷新原动作，禁止换 identity 重发")

    @staticmethod
    def _assert_rack_arrived_for_inbound_batch(
        steps: list[IntegrationRunStep],
        *,
        rack_id: str,
        rack_face: str,
    ) -> None:
        allowed_positions = set(MANUAL_OUTBOUND_SITE_CONFIGURATION["bin_rack_positions"])
        for step in steps:
            request = step.request_summary_json
            result = step.result_summary_json
            target = request.get("target")
            members = result.get("members")
            member = (
                next(
                    (item for item in members if isinstance(item, dict) and item.get("object_id") == rack_id),
                    None,
                )
                if isinstance(members, list)
                else None
            )
            if (
                step.phase == IntegrationDebugPhase.RACK_TRANSPORT
                and step.status == "SUCCEEDED"
                and request.get("kind") == IntegrationTransportActionKind.MOVE_RACK
                and request.get("rack_id") == rack_id
                and request.get("target_face") == rack_face
                and isinstance(target, dict)
                and target.get("kind") == "RACK_POSITION"
                and target.get("location_code") in allowed_positions
                and isinstance(member, dict)
                and member.get("status") == "SUCCEEDED"
                and member.get("final_position") == target
                and member.get("arrival_face") == rack_face
            ):
                return
        raise IntegrationDebugConflict("真实模式必须先取得该来源货架在 KT16/KT17 且朝向一致的 Transport 成功终态")

    async def _freeze_return_rack_arrival_report(
        self,
        db: AsyncSession,
        run: IntegrationRun,
        transport_step: IntegrationRunStep,
        *,
        actor_id: int,
    ) -> bool:
        if (
            transport_step.phase != IntegrationDebugPhase.RACK_TRANSPORT
            or transport_step.transport_task_id is None
            or run.task_id is None
            or run.picking_task_id is None
        ):
            return False
        request = transport_step.request_summary_json
        rack_id = request.get("rack_id")
        rack_face = request.get("target_face")
        target = request.get("target")
        plan = run.configuration_json.get("plan_resources")
        direct_picks = plan.get("direct_picks") if isinstance(plan, dict) else None
        if not (
            isinstance(rack_id, str)
            and isinstance(rack_face, str)
            and isinstance(target, dict)
            and target.get("kind") == "RACK_POSITION"
            and isinstance(target.get("location_code"), str)
            and isinstance(direct_picks, list)
            and any(
                isinstance(item, dict) and item.get("rack_id") == rack_id and item.get("rack_face") == rack_face
                for item in direct_picks
            )
        ):
            return False
        result = transport_step.result_summary_json
        outcome_revision = result.get("outcome_version")
        members = result.get("members")
        member = (
            next(
                (item for item in members if isinstance(item, dict) and item.get("object_id") == rack_id),
                None,
            )
            if isinstance(members, list)
            else None
        )
        if (
            not isinstance(outcome_revision, int)
            or isinstance(outcome_revision, bool)
            or outcome_revision <= 0
            or not isinstance(member, dict)
            or member.get("status") != "SUCCEEDED"
            or member.get("final_position") != target
            or member.get("arrival_face") != rack_face
        ):
            raise IntegrationDebugConflict("退料货架 Transport 成功终态缺少匹配的位置、朝向或 outcome_version")
        steps = await self._runs.list_steps(db, run.run_id)
        if any(
            step.operation == RETURN_RACK_ARRIVAL_REPORT_OPERATION
            and step.request_summary_json.get("transport_task_id") == transport_step.transport_task_id
            for step in steps
        ):
            return True
        now = timezone.now_for_db()
        operation_id = new_uuid7()
        intent = sdk.wms_operations.outbound_return_rack_arrival_report(
            operation_id=operation_id,
            task_id=run.task_id,
            transport_task_id=transport_step.transport_task_id,
            outcome_revision=outcome_revision,
            rack_id=rack_id,
            final_position=sdk.TransportRackPosition(target["location_code"]),
            arrival_face=rack_face,
        )
        payload = encode_arrival_report(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
        step = await self._append_step(
            db,
            run,
            phase=IntegrationDebugPhase.RACK_ARRIVAL,
            status="WAITING",
            actor_id=actor_id,
            client_request_id=operation_id,
            operation=RETURN_RACK_ARRIVAL_REPORT_OPERATION,
            operation_id=operation_id,
            request=payload["data"],
        )
        acceptance = await self._confirmations.create_or_get(
            db,
            operation=RETURN_RACK_ARRIVAL_REPORT_OPERATION,
            operation_id=operation_id,
            picking_task_id=run.picking_task_id,
            request_payload=payload,
            deadline_at=now + timedelta(minutes=30),
            created_at=now,
        )
        if not isinstance(acceptance, WmsConfirmationAcceptance):
            raise IntegrationDebugConflict("return_rack arrival_report identity 内容冲突")
        step.wms_confirmation_id = acceptance.confirmation.id
        defer_wakeup(db, task_queue_gateway.enqueue_wms_confirmations)
        return True

    async def _append_step(
        self,
        db: AsyncSession,
        run: IntegrationRun,
        *,
        phase: IntegrationDebugPhase,
        status: str,
        actor_id: int,
        client_request_id: str | None = None,
        operation: str | None = None,
        operation_id: str | None = None,
        request: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        transport_task_id: str | None = None,
        wms_confirmation_id: int | None = None,
    ) -> IntegrationRunStep:
        step = IntegrationRunStep(
            run_id=run.run_id,
            ordinal=await self._runs.next_ordinal(db, run.run_id),
            phase=phase,
            status=status,
            client_request_id=client_request_id,
            operation=operation,
            operation_id=operation_id,
            request_summary_json=request or {},
            result_summary_json=result or {},
            transport_task_id=transport_task_id,
            wms_confirmation_id=wms_confirmation_id,
            created_by=actor_id,
        )
        await self._runs.add_step(db, step)
        return step

    @staticmethod
    def _validate_manual_outbound_transport(
        phase: IntegrationDebugPhase,
        action: IntegrationTransportAction,
        plan_resources: dict[str, Any] | None,
    ) -> None:
        if phase is IntegrationDebugPhase.RACK_TRANSPORT:
            if action.kind is not IntegrationTransportActionKind.MOVE_RACK:
                raise IntegrationDebugContractError("KT16 出库货架步骤只允许 MOVE_RACK")
            if action.source != {"kind": "RACK", "location_code": action.rack_id}:
                raise IntegrationDebugContractError("KT16 出库来源必须直接使用货架号")
            if not isinstance(plan_resources, dict):
                raise IntegrationDebugContractError("出库搬运缺少已应用的 plan_delta 资源")
            target_rack = plan_resources.get("target_rack")
            if isinstance(target_rack, dict) and target_rack.get("rack_id") == action.rack_id:
                resource = target_rack
                template = "F01"
                positions = [MANUAL_OUTBOUND_SITE_CONFIGURATION["outbound_transfer_position"]]
            else:
                resource = next(
                    (row for row in plan_resources.get("bin_source_racks", []) if row["rack_id"] == action.rack_id),
                    None,
                )
                if resource is None:
                    raise IntegrationDebugContractError("出库货架必须是 plan_delta 的转运货架或五层来源货架")
                template = MANUAL_OUTBOUND_SITE_CONFIGURATION["outbound_rcs_template"]
                positions = MANUAL_OUTBOUND_SITE_CONFIGURATION["bin_rack_positions"]
            if action.rcs_template_id != template:
                raise IntegrationDebugContractError(f"该资源的出库搬运必须使用 {template}")
            if action.target.get("kind") != "RACK_POSITION" or action.target.get("location_code") not in positions:
                raise IntegrationDebugContractError(f"该资源的目标工作位只能是 {'、'.join(positions)}")
            if action.target_face != resource.get("rack_face"):
                raise IntegrationDebugContractError("出库目标面必须与 plan_delta 的 rack_face 完全一致")
        elif phase is IntegrationDebugPhase.BIN_TRANSPORT:
            if action.kind is not IntegrationTransportActionKind.MOVE_BINS or action.target != {
                "kind": "HANDOFF_POSITION",
                "location_code": MANUAL_OUTBOUND_SITE_CONFIGURATION["infeed_position"],
            }:
                raise IntegrationDebugContractError("KT16 入站料箱必须搬到 CNV0301")
        elif phase is IntegrationDebugPhase.BIN_RETURN_TRANSPORT:
            if action.kind is not IntegrationTransportActionKind.MOVE_BINS or action.source != {
                "kind": "HANDOFF_POSITION",
                "location_code": MANUAL_OUTBOUND_SITE_CONFIGURATION["outfeed_position"],
            }:
                raise IntegrationDebugContractError("KT16 退箱必须从 CNV0302 发起")
        elif phase is IntegrationDebugPhase.RACK_DEPARTURE:
            if action.kind is not IntegrationTransportActionKind.MOVE_RACK:
                raise IntegrationDebugContractError("KT16 回库货架步骤只允许 MOVE_RACK")
            if action.rcs_template_id != MANUAL_OUTBOUND_SITE_CONFIGURATION["return_rcs_template"]:
                raise IntegrationDebugContractError("KT16 回库必须使用 CTU03")
            if action.source != {"kind": "RACK", "location_code": action.rack_id}:
                raise IntegrationDebugContractError("KT16 回库来源必须直接使用货架号")
            if action.target != {
                "kind": "ZONE",
                "location_code": MANUAL_OUTBOUND_SITE_CONFIGURATION["return_zone_code"],
            }:
                raise IntegrationDebugContractError("KT16 回库目标库区必须是 WH05")

    @staticmethod
    def _validate_batch_transport(
        phase: IntegrationDebugPhase,
        configuration: dict[str, Any],
        action: IntegrationTransportAction,
    ) -> tuple[BinMove, ...] | None:
        if phase is IntegrationDebugPhase.BIN_TRANSPORT:
            try:
                batch = BinInboundBatchReady.model_validate(
                    {"result": "READY", "bins": configuration.get("inbound_bins")}
                )
            except ValidationError as exc:
                raise IntegrationDebugContractError("缺少有效的 WMS inbound_batch READY 完整批次") from exc
            site = configuration.get("site_configuration", MANUAL_OUTBOUND_SITE_CONFIGURATION)
            expected_target = {"kind": "HANDOFF_POSITION", "location_code": site["infeed_position"]}
            if (
                action.kind is not IntegrationTransportActionKind.MOVE_BINS
                or action.bin_code is not None
                or action.source != {"kind": "RACK", "location_code": action.rack_id}
                or action.target != expected_target
                or any(member.source_locator.rack_id != action.rack_id for member in batch.bins)
                or len({member.source_locator.rack_face for member in batch.bins}) != 1
            ):
                raise IntegrationDebugContractError(
                    "投料 Transport 必须引用 WMS inbound_batch READY 整批料箱并送至投料口"
                )
            return tuple(
                BinMove(
                    member.bin_code,
                    RackBinSlot(
                        member.source_locator.rack_id,
                        member.source_locator.rack_face,
                        member.source_locator.slot_id,
                    ),
                    HandoffPosition(site["infeed_position"]),
                )
                for member in batch.bins
            )
        if phase is IntegrationDebugPhase.BIN_RETURN_TRANSPORT:
            moves = configuration.get("return_moves")
            move = moves[0] if isinstance(moves, list) and len(moves) == 1 else None
            locator = move.get("target") if isinstance(move, dict) else None
            expected_target = (
                {"kind": "RACK_BIN_SLOT", **{key: locator[key] for key in ("rack_id", "rack_face", "slot_id")}}
                if isinstance(locator, dict)
                and all(isinstance(locator.get(key), str) for key in ("rack_id", "rack_face", "slot_id"))
                else None
            )
            if (
                not isinstance(move, dict)
                or move.get("sequence_no") != 1
                or action.bin_code != move.get("bin_code")
                or expected_target is None
                or action.target != expected_target
                or action.rack_id != expected_target["rack_id"]
            ):
                raise IntegrationDebugContractError("退箱 Transport 必须逐字段匹配 WMS return_batch READY 队首 move")
        return None

    @staticmethod
    def _device_action_succeeded(step: IntegrationRunStep, profile: IntegrationDebugProfile) -> bool:
        if profile_uses_real_ecs(profile):
            return step.device_command_code is not None and step.status == "SUCCEEDED"
        return step.status == "SUCCEEDED" and step.result_summary_json.get("simulated") is True

    @staticmethod
    def _expected_device_task_type(
        phase: IntegrationDebugPhase,
        configuration: dict[str, Any],
        steps: list[IntegrationRunStep],
    ) -> str:
        admission_result = configuration.get("manual_bin_admission_result")
        completion_step = next(
            (
                step
                for step in reversed(steps)
                if step.operation == MANUAL_BIN_COMPLETED_OPERATION and step.status == "SUCCEEDED"
            ),
            None,
        )
        if phase is IntegrationDebugPhase.POINT2_RELEASE:
            if admission_result == "WORK_REQUIRED" and completion_step is None:
                raise IntegrationDebugConflict("WORK_REQUIRED 尚未绑定 WMS 完成决定，不能释放 point2")
            if admission_result not in {"WORK_REQUIRED", "NO_WORK"}:
                raise IntegrationDebugConflict("point2 释放缺少已冻结的 WMS 准入决定")
            return "MOVE_FORWARD"
        if admission_result == "NO_WORK":
            return "MOVE_FORWARD"
        if admission_result != "WORK_REQUIRED" or completion_step is None:
            raise IntegrationDebugConflict("point3 分流缺少已冻结的料箱处置决定")
        completion_result = completion_step.result_summary_json.get("result")
        if completion_result not in {"NORMAL", "NG"}:
            raise IntegrationDebugConflict("point3 分流缺少 NORMAL 或 NG 完成决定")
        return "MOVE_LEFT" if completion_result == "NG" else "MOVE_FORWARD"

    @staticmethod
    def _snapshot(run: IntegrationRun, steps: list[IntegrationRunStep]) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "workline_id": run.workline_id,
            "workline_code": run.workline_code,
            "scenario_key": str(run.scenario_key),
            "expected_plugin_key": run.expected_plugin_key,
            "profile": str(run.profile),
            "environment_label": run.environment_label,
            "operator_user_id": run.operator_user_id,
            "status": str(run.status),
            "current_phase": str(run.current_phase),
            "version": run.version,
            "task_id": run.task_id,
            "issued_operation_id": run.issued_operation_id,
            "bin_code": run.bin_code,
            "device_code": run.device_code,
            "rack_id": run.rack_id,
            "plan_resources": run.configuration_json.get("plan_resources"),
            "site_configuration": run.configuration_json.get("site_configuration", {}),
            "operation_context": {
                key: run.configuration_json[key]
                for key in (
                    "inbound_bins",
                    "return_moves",
                    "rack_destination",
                    "admission_task_id",
                    "manual_bin_admission_result",
                    "departure_ready_rack_id",
                )
                if key in run.configuration_json
            },
            "attention_code": run.attention_code,
            "attention_detail": run.attention_detail,
            "wms_cleanup_confirmed": run.wms_cleanup_confirmed,
            "site_cleanup_confirmed": run.site_cleanup_confirmed,
            "created_at": run.created_at.isoformat(),
            "updated_at": (run.updated_at or run.created_at).isoformat(),
            "steps": [
                {
                    "ordinal": step.ordinal,
                    "phase": str(step.phase),
                    "status": step.status,
                    "client_request_id": step.client_request_id,
                    "operation": step.operation,
                    "operation_id": step.operation_id,
                    "wms_confirmation_id": step.wms_confirmation_id,
                    "transport_task_id": step.transport_task_id,
                    "device_command_code": step.device_command_code,
                    "request": step.request_summary_json,
                    "result": step.result_summary_json,
                    "reason_code": step.reason_code,
                    "created_at": step.created_at.isoformat(),
                }
                for step in steps
            ],
        }

    async def _publish(self, snapshot: dict[str, Any]) -> None:
        try:
            _ = await self._publisher.publish_to(
                INTEGRATION_DEBUG_STREAM_CHANNEL,
                "workline_integration_debug.updated",
                cast("dict[str, object]", snapshot),
            )
        except Exception:
            logger.exception("workline.integration_debug.publish_failed", extra={"run_id": snapshot["run_id"]})


__all__ = [
    "INTEGRATION_DEBUG_STREAM_CHANNEL",
    "CreateIntegrationRun",
    "IntegrationDebugConflict",
    "IntegrationDebugContractError",
    "IntegrationDebugNotFound",
    "IntegrationDebugService",
    "IntegrationRunWorkLineOwner",
]
