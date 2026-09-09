"""人工出库联调 run 的人工推进与可靠对象关联。"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

import wes_plugin_sdk as sdk

from src.app.device.services import device_service
from src.app.execution.models import InboundEvidenceApplyStatus, WmsConfirmationStatus
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationAcceptance,
    WmsConfirmationLifecycleService,
)
from src.app.sys.services.event_stream_service import event_stream_service
from src.app.wms_adapter.outbound_picking.completion_confirm_typed import encode_request as encode_completion_confirm
from src.app.wms_adapter.outbound_picking.completion_confirm_wire import COMPLETION_CONFIRM_OPERATION
from src.app.wms_adapter.outbound_picking.departure_typed import encode_request as encode_departure
from src.app.wms_adapter.outbound_picking.departure_wire import RACK_DEPARTURE_OPERATION
from src.app.wms_adapter.outbound_picking.inbound_batch_typed import encode_request as encode_inbound_batch
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.manual_bin_admission_wire import (
    MANUAL_BIN_ADMISSION_OPERATION,
)
from src.app.wms_adapter.outbound_picking.manual_bin_apply_report_wire import (
    MANUAL_BIN_APPLY_REPORT_OPERATION,
)
from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import MANUAL_BIN_COMPLETED_OPERATION
from src.app.wms_adapter.outbound_picking.manual_bin_typed import encode_admission, encode_apply_report
from src.app.wms_adapter.outbound_picking.return_batch_typed import encode_request as encode_return_batch
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareNoopReason
from src.app.workline_integration_debug.contracts import (
    SORTING_3_SITE_CONFIGURATION,
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
        if operation not in {MANUAL_BIN_ADMISSION_OPERATION, MANUAL_BIN_APPLY_REPORT_OPERATION}:
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
                configuration_json={
                    "site_configuration": deepcopy(SORTING_3_SITE_CONFIGURATION)
                    if workline.line_code == "sorting-3"
                    else {}
                },
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
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None:
                if existing.run_id != run_id or existing.operation != PICKING_TASK_PREPARE_OPERATION:
                    raise IntegrationDebugConflict("client_request_id 已用于其它联调动作")
                return self._snapshot(run, await self._runs.list_steps(db, run_id))
            confirmation = await self._runs.get_prepare_confirmation(db, run.picking_task_id)
            if confirmation is not None:
                if confirmation.id is None:
                    raise RuntimeError("已持久化 prepare confirmation 缺少 id")
                await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase.TASK_PREPARE,
                    status="WAITING",
                    actor_id=actor_id,
                    client_request_id=client_request_id,
                    operation=PICKING_TASK_PREPARE_OPERATION,
                    operation_id=confirmation.operation_id,
                    request=confirmation.request_payload["data"],
                    wms_confirmation_id=confirmation.id,
                )
                run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                run.updated_by = actor_id
                run.increment_version()
                snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
            else:
                snapshot = None

        if snapshot is not None:
            await self._publish(snapshot)
            return snapshot

        prepared = await self._prepare.prepare_next_for_workline(
            workline_id,
            expected_task_id=selected_task_id,
        )
        if not prepared.prepared or prepared.task is None or prepared.confirmation is None:
            reason = prepared.reason or PickingTaskPrepareNoopReason.WORKLINE_NOT_READY
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
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.PLAN_RECEIPT)
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("联调 run 尚未选择 PickingTask")
            task = await self._runs.get_picking_task(db, run.task_id)
            if (
                task is None
                or task.id != run.picking_task_id
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
        rack_id: str,
        rack_face: str,
        max_bin_count: int,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.BIN_INBOUND_BATCH)
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("inbound_batch 缺少 PickingTask")
            plan = run.configuration_json.get("plan_resources")
            sources = plan.get("bin_source_racks") if isinstance(plan, dict) else None
            if not isinstance(sources, list) or not any(
                isinstance(item, dict) and item.get("rack_id") == rack_id and item.get("rack_face") == rack_face
                for item in sources
            ):
                raise IntegrationDebugContractError("inbound_batch 必须引用 plan_delta 中的五层料箱架及朝向")
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is None:
                operation_id = new_uuid7()
                intent = sdk.wms_operations.outbound_bin_inbound_batch(
                    operation_id=operation_id,
                    task_id=run.task_id,
                    rack_id=rack_id,
                    rack_face=rack_face,
                    max_bin_count=max_bin_count,
                )
                payload = encode_inbound_batch(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
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
            elif existing.run_id != run_id or existing.operation != BIN_INBOUND_BATCH_OPERATION:
                raise IntegrationDebugConflict("client_request_id 已用于其它联调动作")
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_bin_return_batch(
        self,
        run_id: str,
        *,
        client_request_id: str,
        rack_id: str,
        rack_face: str,
        source_location_code: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.BIN_RETURN_BATCH)
            if run.bin_code is None:
                raise IntegrationDebugContractError("return_batch 缺少已完成作业的 Bin")
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is None:
                operation_id = new_uuid7()
                intent = sdk.wms_operations.outbound_bin_return_batch(
                    operation_id=operation_id,
                    workline_code=run.workline_code,
                    rack_id=rack_id,
                    rack_face=rack_face,
                    return_candidates=(sdk.BinReturnCandidate(1, run.bin_code, source_location_code),),
                )
                payload = encode_return_batch(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
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
            elif existing.run_id != run_id or existing.operation != BIN_RETURN_BATCH_OPERATION:
                raise IntegrationDebugConflict("client_request_id 已用于其它联调动作")
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_rack_departure(
        self,
        run_id: str,
        *,
        client_request_id: str,
        rack_id: str,
        current_location_code: str,
        current_face: str,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.RACK_DEPARTURE)
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("departure_decide 缺少 PickingTask")
            operation_id = new_uuid7()
            intent = sdk.wms_operations.outbound_rack_departure_decide(
                operation_id=operation_id,
                task_id=run.task_id,
                rack_id=rack_id,
                current_location=sdk.TransportRackPosition(current_location_code),
                current_face=current_face,
            )
            payload = encode_departure(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
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
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.TASK_COMPLETION)
            if run.task_id is None or run.picking_task_id is None:
                raise IntegrationDebugContractError("completion_confirm 缺少 PickingTask")
            task = await self._runs.get_picking_task(db, run.task_id)
            if task is None:
                raise IntegrationDebugNotFound("PickingTask 不存在")
            operation_id = new_uuid7()
            intent = sdk.wms_operations.outbound_picking_task_completion_confirm(
                operation_id=operation_id,
                task_id=run.task_id,
                last_applied_plan_revision=task.last_applied_plan_revision,
            )
            payload = encode_completion_confirm(intent, timestamp=int(timezone.to_utc(now).timestamp() * 1000))
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
        if not bin_code.strip() or scanned_at <= 0 or scanned_at > now_ms:
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
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        timestamp = int(timezone.to_utc(now).timestamp() * 1000)
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.WORK_ADMISSION)
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is None:
                if run.bin_code is None:
                    raise IntegrationDebugContractError("必须先记录 point2 实际扫码 Bin")
                operation_id = new_uuid7()
                intent = sdk.wms_operations.outbound_manual_bin_work_admission(
                    operation_id=operation_id,
                    bin_code=run.bin_code,
                    scanned_at=run.configuration_json["point2_scanned_at"],
                )
                payload = encode_admission(intent, timestamp=timestamp)
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
            elif existing.run_id != run_id or existing.operation != MANUAL_BIN_ADMISSION_OPERATION:
                raise IntegrationDebugConflict("client_request_id 已用于其它联调动作")
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
            if run.workline_code == "sorting-3":
                self._validate_sorting3_transport(IntegrationDebugPhase(run.current_phase), action)
            existing = await self._runs.get_step_by_client_request_id(db, action.client_request_id, for_update=True)
            if existing is None:
                request = build_transport_request(action)
                real_transport = profile_uses_real_transport(IntegrationDebugProfile(run.profile))
                handle = await self._transport.create_debug_task_in_session(db, request) if real_transport else None
                await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase(run.current_phase),
                    status="WAITING" if real_transport else "SUCCEEDED",
                    actor_id=actor_id,
                    client_request_id=action.client_request_id,
                    request={
                        "kind": action.kind,
                        "rack_id": action.rack_id,
                        "bin_code": action.bin_code,
                        "source": action.source,
                        "target": action.target,
                        "rcs_template_id": action.rcs_template_id,
                        "target_face": action.target_face,
                    },
                    result={} if real_transport else {"simulated": True},
                    transport_task_id=handle.transport_task_id if handle is not None else None,
                )
                run.status = (
                    IntegrationDebugRunStatus.WAITING_EXTERNAL if real_transport else IntegrationDebugRunStatus.ACTIVE
                )
                run.updated_by = actor_id
                run.increment_version()
            elif existing.run_id != run_id or (
                profile_uses_real_transport(IntegrationDebugProfile(run.profile)) and existing.transport_task_id is None
            ):
                raise IntegrationDebugConflict("client_request_id 已用于其它联调动作")
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
            if step is None or step.transport_task_id != transport_task_id:
                raise IntegrationDebugConflict("Transport 联调步骤已变化")
            step.result_summary_json = transport.result or {"status": transport.status}
            if transport.status == "SUCCEEDED":
                step.status = "SUCCEEDED"
                run.status = IntegrationDebugRunStatus.ACTIVE
            elif transport.status in {"FAILED", "REJECTED", "RECONCILING"}:
                step.status = "NEEDS_ATTENTION"
                step.reason_code = transport.reason_code or f"TRANSPORT_{transport.status}"
                run.status = IntegrationDebugRunStatus.NEEDS_ATTENTION
                run.attention_code = step.reason_code
            else:
                step.status = "WAITING"
                run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
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
                step.result_summary_json = (
                    response_payload
                    if isinstance(response_payload, dict) and response_payload
                    else {"response_result": confirmation.response_result}
                )
                if should_advance:
                    self._advance_completed_wms_action(
                        run,
                        step,
                        response_result=confirmation.response_result,
                        response_data=response_data,
                    )
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
            if response_result == "WORK_REQUIRED":
                task_id = response_data.get("task_id") if isinstance(response_data, dict) else None
                if not isinstance(task_id, str) or not task_id:
                    raise IntegrationDebugConflict("WORK_REQUIRED 响应缺少 task_id")
                run.configuration_json = {**run.configuration_json, "admission_task_id": task_id}
        elif step.operation == MANUAL_BIN_APPLY_REPORT_OPERATION:
            if step.request_summary_json.get("apply_result") == "RECONCILING":
                run.status = IntegrationDebugRunStatus.NEEDS_ATTENTION
                run.attention_code = step.request_summary_json.get("reason_code") or "MANUAL_BIN_RECONCILING"
            else:
                run.current_phase = IntegrationDebugPhase.POINT3_ROUTE
                run.status = IntegrationDebugRunStatus.ACTIVE
        elif step.operation == BIN_INBOUND_BATCH_OPERATION:
            IntegrationDebugService._advance_inbound_batch(run, response_result, response_data)
        elif step.operation == BIN_RETURN_BATCH_OPERATION:
            IntegrationDebugService._advance_return_batch(run, response_result, response_data)
        elif step.operation == RACK_DEPARTURE_OPERATION:
            IntegrationDebugService._advance_departure(run, step, response_result, response_data)
        elif step.operation == COMPLETION_CONFIRM_OPERATION:
            IntegrationDebugService._advance_completion(run, response_result)

    @staticmethod
    def _advance_inbound_batch(run: IntegrationRun, response_result: str | None, response_data: object) -> None:
        if response_result == "READY" and isinstance(response_data, dict):
            bins = response_data.get("bins")
            if not isinstance(bins, list) or not bins:
                raise IntegrationDebugConflict("inbound_batch READY 缺少 bins")
            run.configuration_json = {**run.configuration_json, "inbound_bins": bins}
            run.current_phase = IntegrationDebugPhase.BIN_TRANSPORT
            run.status = IntegrationDebugRunStatus.ACTIVE
        elif response_result in {"NO_BATCH", "RACK_FACE_DONE"}:
            run.status = IntegrationDebugRunStatus.ACTIVE
        else:
            raise IntegrationDebugConflict("inbound_batch 响应结果不在固定联合内")

    @staticmethod
    def _advance_return_batch(run: IntegrationRun, response_result: str | None, response_data: object) -> None:
        if response_result == "READY" and isinstance(response_data, dict):
            moves = response_data.get("moves")
            if not isinstance(moves, list) or not moves:
                raise IntegrationDebugConflict("return_batch READY 缺少 moves")
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
            if not isinstance(destination, dict):
                raise IntegrationDebugConflict("departure_decide READY 缺少 rack_destination")
            rack_id = step.request_summary_json.get("rack_id")
            if not isinstance(rack_id, str) or not rack_id:
                raise IntegrationDebugConflict("departure_decide READY 缺少原请求 rack_id")
            run.configuration_json = {
                **run.configuration_json,
                "rack_destination": destination,
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
            await self._append_step(
                db,
                run,
                phase=IntegrationDebugPhase.WORK_COMPLETION,
                status="SUCCEEDED",
                actor_id=actor_id,
                operation=MANUAL_BIN_COMPLETED_OPERATION,
                operation_id=operation_id,
                result={"result": data.get("result"), "completed_at": completed_at},
            )
            run.current_phase = IntegrationDebugPhase.POINT2_RELEASE
            run.status = IntegrationDebugRunStatus.ACTIVE
            run.updated_by = actor_id
            run.increment_version()
            snapshot = self._snapshot(run, await self._runs.list_steps(db, run_id))
        await self._publish(snapshot)
        return snapshot

    async def send_completion_apply_report(
        self,
        run_id: str,
        *,
        client_request_id: str,
        completion_operation_id: str,
        apply_revision: int,
        apply_result: str,
        reason_code: str | None,
        occurred_at: int,
        expected_version: int,
        actor_id: int,
    ) -> dict[str, Any]:
        now = timezone.now_for_db()
        timestamp = int(timezone.to_utc(now).timestamp() * 1000)
        async with self._sessions.begin() as db:
            run = await self._require_run(db, run_id, for_update=True)
            self._assert_action(run, expected_version, actor_id, IntegrationDebugPhase.COMPLETION_REPORT)
            admission_task_id = run.configuration_json.get("admission_task_id")
            if not isinstance(admission_task_id, str) or run.bin_code is None:
                raise IntegrationDebugContractError("完成应用报告缺少已绑定 task_id 或实际 bin_code")
            if apply_revision != 1:
                raise IntegrationDebugContractError("当前固定联调流程只发送首次 apply_revision=1")
            completion_steps = await self._runs.list_steps(db, run_id)
            bound_completion = next(
                (
                    step
                    for step in reversed(completion_steps)
                    if step.operation == MANUAL_BIN_COMPLETED_OPERATION and step.status == "SUCCEEDED"
                ),
                None,
            )
            if bound_completion is None or bound_completion.operation_id != completion_operation_id:
                raise IntegrationDebugContractError("completion_operation_id 必须等于本 run 已绑定的完成决定 identity")
            completion_evidence = await self._runs.get_evidence_by_operation(
                db,
                MANUAL_BIN_COMPLETED_OPERATION,
                completion_operation_id,
                for_update=True,
            )
            release_step = next(
                (
                    step
                    for step in reversed(completion_steps)
                    if step.phase == IntegrationDebugPhase.POINT2_RELEASE and step.status == "SUCCEEDED"
                ),
                None,
            )
            if apply_result == "APPLIED" and (
                release_step is None
                or completion_evidence is None
                or completion_evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
            ):
                raise IntegrationDebugContractError(
                    "APPLIED 前必须完成 point2 释放步骤并将完成 Evidence 标记为 APPLIED"
                )
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is None:
                report_task = await self._runs.get_picking_task(db, admission_task_id)
                if report_task is None or report_task.id is None:
                    raise IntegrationDebugContractError("完成应用报告的 WMS task_id 未关联本地 PickingTask")
                operation_id = new_uuid7()
                intent = sdk.wms_operations.outbound_manual_bin_completion_apply_report(
                    operation_id=operation_id,
                    completion_operation_id=completion_operation_id,
                    task_id=admission_task_id,
                    bin_code=run.bin_code,
                    apply_revision=apply_revision,
                    apply_result=cast("Any", apply_result),
                    reason_code=reason_code,
                    occurred_at=occurred_at,
                )
                payload = encode_apply_report(intent, timestamp=timestamp)
                step = await self._append_step(
                    db,
                    run,
                    phase=IntegrationDebugPhase.COMPLETION_REPORT,
                    status="WAITING",
                    actor_id=actor_id,
                    client_request_id=client_request_id,
                    operation=MANUAL_BIN_APPLY_REPORT_OPERATION,
                    operation_id=operation_id,
                    request=payload["data"],
                )
                acceptance = await self._confirmations.create_or_get(
                    db,
                    operation=MANUAL_BIN_APPLY_REPORT_OPERATION,
                    operation_id=operation_id,
                    picking_task_id=report_task.id,
                    request_payload=payload,
                    deadline_at=now + timedelta(minutes=30),
                    created_at=now,
                )
                if not isinstance(acceptance, WmsConfirmationAcceptance):
                    raise IntegrationDebugConflict("WMS operation identity 内容冲突")
                step.wms_confirmation_id = acceptance.confirmation.id
                defer_wakeup(db, task_queue_gateway.enqueue_wms_confirmations)
                run.status = IntegrationDebugRunStatus.WAITING_EXTERNAL
                run.current_phase = IntegrationDebugPhase.COMPLETION_REPORT
                run.updated_by = actor_id
                run.increment_version()
            elif existing.run_id != run_id or existing.operation != MANUAL_BIN_APPLY_REPORT_OPERATION:
                raise IntegrationDebugConflict("client_request_id 已用于其它联调动作")
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
            if device_code not in SORTING_3_SITE_CONFIGURATION["scan_device_codes"]:
                raise IntegrationDebugContractError("sorting-3 ECS 设备必须是 STATION_SCAN9 至 STATION_SCAN12")
            real_ecs = profile_uses_real_ecs(IntegrationDebugProfile(run.profile))
            expected_request: dict[str, object] = {
                "device_code": device_code,
                "task_type": task_type,
                "params": params,
            }
            existing = await self._runs.get_step_by_client_request_id(db, client_request_id, for_update=True)
            if existing is not None:
                if existing.run_id != run_id or existing.request_summary_json != expected_request:
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
            if simulated_snapshot is not None:
                endpoint = ""
            else:
                device = await device_service.get_device_by_code(db, device_code)
                if device is None or not device.is_active or not device.endpoint_base_url:
                    raise IntegrationDebugContractError("Run 选择的设备未登记、未启用或缺少 endpoint")
                endpoint = device.endpoint_base_url
                if existing is None:
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
            created_by=actor_id,
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
            if run.status not in {IntegrationDebugRunStatus.COMPLETED, IntegrationDebugRunStatus.NEEDS_ATTENTION}:
                raise IntegrationDebugConflict("只有 COMPLETED 或 NEEDS_ATTENTION run 可人工关闭")
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
            IntegrationDebugPhase.POINT2_RELEASE: IntegrationDebugPhase.COMPLETION_REPORT,
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
            if target is None:
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
            if current is IntegrationDebugPhase.POINT2_RELEASE:
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
    def _validate_sorting3_transport(
        phase: IntegrationDebugPhase,
        action: IntegrationTransportAction,
    ) -> None:
        if phase is IntegrationDebugPhase.RACK_TRANSPORT:
            if action.kind is not IntegrationTransportActionKind.MOVE_RACK:
                raise IntegrationDebugContractError("sorting-3 出库货架步骤只允许 MOVE_RACK")
            if action.rcs_template_id != SORTING_3_SITE_CONFIGURATION["outbound_rcs_template"]:
                raise IntegrationDebugContractError("sorting-3 出库必须使用 CTU01")
            if action.source != {"kind": "RACK", "location_code": action.rack_id}:
                raise IntegrationDebugContractError("sorting-3 出库来源必须直接使用货架号")
            allowed_targets = {
                SORTING_3_SITE_CONFIGURATION["outbound_transfer_position"],
                *SORTING_3_SITE_CONFIGURATION["bin_rack_positions"],
            }
            if (
                action.target.get("kind") != "RACK_POSITION"
                or action.target.get("location_code") not in allowed_targets
            ):
                raise IntegrationDebugContractError("sorting-3 出库目标工作位只能是 OUT65、KT16 或 KT17")
        elif phase is IntegrationDebugPhase.BIN_TRANSPORT:
            if action.kind is not IntegrationTransportActionKind.MOVE_BINS or action.target != {
                "kind": "HANDOFF_POSITION",
                "location_code": SORTING_3_SITE_CONFIGURATION["infeed_position"],
            }:
                raise IntegrationDebugContractError("sorting-3 入站料箱必须搬到 CNV0301")
        elif phase is IntegrationDebugPhase.BIN_RETURN_TRANSPORT:
            if action.kind is not IntegrationTransportActionKind.MOVE_BINS or action.source != {
                "kind": "HANDOFF_POSITION",
                "location_code": SORTING_3_SITE_CONFIGURATION["outfeed_position"],
            }:
                raise IntegrationDebugContractError("sorting-3 退箱必须从 CNV0302 发起")
        elif phase is IntegrationDebugPhase.RACK_DEPARTURE:
            if action.kind is not IntegrationTransportActionKind.MOVE_RACK:
                raise IntegrationDebugContractError("sorting-3 回库货架步骤只允许 MOVE_RACK")
            if action.rcs_template_id != SORTING_3_SITE_CONFIGURATION["return_rcs_template"]:
                raise IntegrationDebugContractError("sorting-3 回库必须使用 CTU03")
            if action.source != {"kind": "RACK", "location_code": action.rack_id}:
                raise IntegrationDebugContractError("sorting-3 回库来源必须直接使用货架号")
            if action.target != {
                "kind": "ZONE",
                "location_code": SORTING_3_SITE_CONFIGURATION["return_zone_code"],
            }:
                raise IntegrationDebugContractError("sorting-3 回库目标库区必须是 WH05")

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
