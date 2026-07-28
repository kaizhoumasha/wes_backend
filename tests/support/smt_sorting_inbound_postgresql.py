"""SMT generated source-pick PostgreSQL heavy tests 的共用 fixture。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from src.app.callback.models import CallbackLog
from src.app.contracts.external_contract_profile_catalog import WMS_MATERIAL_FLOW_SANDBOX_PROFILE
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.runtime.orchestration.device_runtime_projection import DeviceRuntimeProjection
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.models.diagnostic import WorklineDiagnostic
from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.workline_runtime_status_projection import (
    WorkLineRuntimeStatus,
    WorklineRuntimeStatusProjection,
)
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import SmtSortingInboundConfig
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.sys.models.audit_log import AuditLog
from src.app.sys.models.outbox import SystemOutbox
from src.app.workline.models import LineType, WorkLine
from src.app.workline.services.plugin_binding_service import WorklinePluginBindingService
from src.app.workline.services.workline_service import workline_service
from src.core.conf import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class NoopQueueGateway:
    """heavy test 不触碰真实 Celery/Redis，只验证数据库事务事实。"""

    def enqueue_runtime_inbox(self, *, limit: int) -> None:
        _ = limit

    def enqueue_outbox(self, outbox_id: int | None = None, *, limit: int = 50) -> None:
        _ = (outbox_id, limit)


class SelectedRouteService:
    """把 fixture 的目标 WorkLine 固定为 route selection 结果。"""

    def __init__(self, *, workline_id: int, workline_code: str) -> None:
        self.workline_id = workline_id
        self.workline_code = workline_code

    async def resolve_route(self, _db: object, **_kwargs: object) -> Any:
        return type(
            "SelectedRoute",
            (),
            {
                "kind": "SELECTED",
                "selected_workline_id": self.workline_id,
                "selected_workline_code": self.workline_code,
                "route_evidence": {
                    "source_rack_position_code": "SOURCE_STATION_A",
                    "target_rack_position_code": "TARGET_STATION",
                    "manifest_contract_version": DEFINITION.contract_version,
                },
            },
        )()


@dataclass(frozen=True, slots=True)
class SeededSmtSourcePick:
    service: SmtInboundHandoffService
    claim: dict[str, Any]
    workline_id: int
    device_id: int
    device_code: str
    demand_id: int
    source_item_id: int
    source_inbox_id: int


@dataclass(frozen=True, slots=True)
class ProcessedSmtSourcePick:
    seeded: SeededSmtSourcePick
    source_item: SmtInboundHandoffSourceItem
    source_inbox: RuntimeInbox
    command: DeviceCommand
    outbox: SystemOutbox


RowSnapshot = tuple[tuple[tuple[str, Any], ...], ...]


@dataclass(frozen=True, slots=True)
class SmtWriteSetSnapshot:
    """SMT public closure 可能写入的 runtime/domain/audit 表完整行快照。"""

    worklines: RowSnapshot
    devices: RowSnapshot
    commands: RowSnapshot
    outboxes: RowSnapshot
    timelines: RowSnapshot
    sessions: RowSnapshot
    execution_sessions: RowSnapshot
    execution_work_items: RowSnapshot
    execution_correlations: RowSnapshot
    runtime_inboxes: RowSnapshot
    runtime_intent_logs: RowSnapshot
    runtime_holds: RowSnapshot
    idempotency_keys: RowSnapshot
    diagnostics: RowSnapshot
    runtime_status_projections: RowSnapshot
    device_runtime_projections: RowSnapshot
    dispatch_attempts: RowSnapshot
    source_items: RowSnapshot
    demands: RowSnapshot
    attempt_evidence: tuple[tuple[Any, ...], ...]
    callback_logs: RowSnapshot
    audit_logs: RowSnapshot

    def state_advance(self) -> tuple[Any, ...]:
        """排除明确允许新增的 callback/audit diagnostic，仅比较运行态写集。"""

        return (
            self.worklines,
            self.devices,
            self.commands,
            self.outboxes,
            self.timelines,
            self.sessions,
            self.execution_sessions,
            self.execution_work_items,
            self.execution_correlations,
            self.runtime_inboxes,
            self.runtime_intent_logs,
            self.runtime_holds,
            self.idempotency_keys,
            self.diagnostics,
            self.runtime_status_projections,
            self.device_runtime_projections,
            self.dispatch_attempts,
            self.source_items,
            self.demands,
            self.attempt_evidence,
        )

    def durable_effects(self, *, allowed_runtime_inbox_id: int) -> tuple[Any, ...]:
        """失败 attempt 仅允许 RuntimeInbox terminal 与诊断变化，其余写集必须回滚。"""

        return (
            self.worklines,
            self.devices,
            self.commands,
            self.outboxes,
            self.timelines,
            self.sessions,
            self.execution_sessions,
            self.execution_work_items,
            self.execution_correlations,
            _normalize_runtime_inboxes(
                self.runtime_inboxes,
                allowed_runtime_inbox_id=allowed_runtime_inbox_id,
            ),
            self.runtime_intent_logs,
            self.runtime_holds,
            self.idempotency_keys,
            self.runtime_status_projections,
            self.device_runtime_projections,
            self.dispatch_attempts,
            self.source_items,
            self.demands,
            self.attempt_evidence,
            self.callback_logs,
            self.audit_logs,
        )


async def _snapshot_rows(db: AsyncSession, model: type[Any]) -> RowSnapshot:
    primary_key_columns = tuple(model.__table__.primary_key.columns)
    rows = list((await db.scalars(select(model).order_by(*primary_key_columns))).all())
    columns = tuple(model.__table__.columns)
    return tuple(tuple((column.key, deepcopy(getattr(row, column.key))) for column in columns) for row in rows)


_RUNTIME_INBOX_TERMINAL_FIELDS = frozenset(
    {
        "status",
        "processor_token",
        "attempt_count",
        "next_retry_at",
        "lease_until",
        "last_error_code",
        "last_error_message",
        "processed_at",
        "failed_at",
    }
)


def _normalize_runtime_inboxes(
    rows: RowSnapshot,
    *,
    allowed_runtime_inbox_id: int,
) -> RowSnapshot:
    """仅屏蔽目标 Inbox 合法 terminal/retry 字段，保留其它行和所有不可变 anchor。"""

    return tuple(
        tuple(
            (field, "<allowed-runtime-inbox-terminal>")
            if dict(row).get("id") == allowed_runtime_inbox_id and field in _RUNTIME_INBOX_TERMINAL_FIELDS
            else (field, value)
            for field, value in row
        )
        for row in rows
    )


async def snapshot_smt_write_set(db: AsyncSession) -> SmtWriteSetSnapshot:
    """快照 public callback/generated/recovery 链的全部可变持久化事实。"""

    source_items = list(
        (await db.scalars(select(SmtInboundHandoffSourceItem).order_by(SmtInboundHandoffSourceItem.id))).all()
    )
    return SmtWriteSetSnapshot(
        worklines=await _snapshot_rows(db, WorkLine),
        devices=await _snapshot_rows(db, Device),
        commands=await _snapshot_rows(db, DeviceCommand),
        outboxes=await _snapshot_rows(db, SystemOutbox),
        timelines=await _snapshot_rows(db, WorklineTimeline),
        sessions=await _snapshot_rows(db, WorklineSession),
        execution_sessions=await _snapshot_rows(db, ExecutionSession),
        execution_work_items=await _snapshot_rows(db, ExecutionWorkItem),
        execution_correlations=await _snapshot_rows(db, ExecutionCorrelation),
        runtime_inboxes=await _snapshot_rows(db, RuntimeInbox),
        runtime_intent_logs=await _snapshot_rows(db, RuntimeIntentLog),
        runtime_holds=await _snapshot_rows(db, RuntimeHold),
        idempotency_keys=await _snapshot_rows(db, IdempotencyKey),
        diagnostics=await _snapshot_rows(db, WorklineDiagnostic),
        runtime_status_projections=await _snapshot_rows(db, WorklineRuntimeStatusProjection),
        device_runtime_projections=await _snapshot_rows(db, DeviceRuntimeProjection),
        dispatch_attempts=await _snapshot_rows(db, WorklineDispatchAttempt),
        source_items=await _snapshot_rows(db, SmtInboundHandoffSourceItem),
        demands=await _snapshot_rows(db, SmtInboundHandoffDemand),
        attempt_evidence=tuple(
            (
                item.id,
                item.claim_attempt_no,
                item.source_pick_inbox_id,
                item.source_pick_command_id,
                item.source_pick_command_code,
                item.source_pick_dispatch_key,
                item.status,
                item.failure_code,
                item.failure_message,
            )
            for item in source_items
        ),
        callback_logs=await _snapshot_rows(db, CallbackLog),
        audit_logs=await _snapshot_rows(db, AuditLog),
    )


async def seed_smt_source_pick_claim(db: AsyncSession, *, suffix: str) -> SeededSmtSourcePick:
    """从真实 binding activation 创建 request→bound aggregate→RuntimeInbox。"""

    config = SmtSortingInboundConfig(provider_profile=WMS_MATERIAL_FLOW_SANDBOX_PROFILE.identity)
    workline = WorkLine(
        line_code=f"IT-SMT-PG-{suffix}",
        line_name=f"SMT PostgreSQL {suffix}",
        line_type=LineType.AUTO,
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        config=config.model_dump(mode="json"),
        is_active=False,
    )
    db.add(workline)
    await db.flush()
    device = Device(
        device_code=f"IT-SMT-ARM-{suffix}",
        device_name=f"SMT Source Arm {suffix}",
        work_line_id=workline.id,
        device_role="SORTING_SOURCE_ARM",
        vendor_type="ECS",
        device_status=DeviceStatus.IDLE,
        capabilities_json={"supports_command_types": ["SORTING_SOURCE_PICK"]},
        host="127.0.0.1",
        port=1,
    )
    db.add(device)
    await db.flush()
    activated_workline = await workline_service.activate(
        db,
        int(workline.id),
        version=workline.version,
        actor="integration-test",
        reason=f"SMT generated PostgreSQL {suffix}",
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
    )
    assert activated_workline is not None
    assert activated_workline.is_active is True
    assert activated_workline.active_plugin_binding_id is not None
    projection = await db.scalar(
        select(WorklineRuntimeStatusProjection).where(
            WorklineRuntimeStatusProjection.workline_id == activated_workline.id
        )
    )
    assert projection is not None
    projection.runtime_status = WorkLineRuntimeStatus.READY.value
    demand = SmtInboundHandoffDemand(
        demand_key=f"it-smt-demand-{suffix}",
        rack_release_id=f"it-smt-release-{suffix}",
        single_layer_rack_code=f"IT-RACK-{suffix}",
        status=SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
        trace_id=f"trace-smt-pg-{suffix}",
    )
    db.add_all([projection, demand])
    await db.flush()
    source_item = SmtInboundHandoffSourceItem(
        handoff_demand_id=demand.id,
        item_key=f"it-smt-item-{suffix}",
        bin_code=f"IT-BIN-{suffix}",
        bin_cell_index=1,
        bin_cell_code=f"IT-CELL-{suffix}",
        material_identity_key=f"IT-MATERIAL-{suffix}",
        status=SmtInboundHandoffSourceItemStatus.READY,
    )
    db.add(source_item)
    await db.commit()

    service = SmtInboundHandoffService(
        route_service=SelectedRouteService(
            workline_id=int(activated_workline.id),
            workline_code=activated_workline.line_code,
        )
    )
    claim_result = await service.claim_next_source_item(
        db,
        demand_id=demand.id,
        trace_id=demand.trace_id,
    )
    assert claim_result.kind == "CLAIMED"
    await db.commit()
    [claim] = await RuntimeInboxService().claim_for_processing(
        db,
        limit=1,
        processor_token=f"lease-smt-pg-{suffix}",
        stale_after_seconds=60,
    )
    await db.commit()
    return SeededSmtSourcePick(
        service=service,
        claim=claim,
        workline_id=int(activated_workline.id),
        device_id=int(device.id),
        device_code=device.device_code,
        demand_id=int(demand.id),
        source_item_id=int(source_item.id),
        source_inbox_id=int(claim_result.inbox.id),
    )


async def process_smt_source_pick_claim(
    db: AsyncSession,
    seeded: SeededSmtSourcePick,
) -> ProcessedSmtSourcePick:
    """执行 generated decision，并返回真实 command/outbox 持久化结果。"""

    processed = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(
        db,
        claim=seeded.claim,
    )
    assert processed["success"] == 1, processed
    source_item = await db.get(SmtInboundHandoffSourceItem, seeded.source_item_id)
    source_inbox = await db.get(RuntimeInbox, seeded.source_inbox_id)
    command = await db.scalar(
        select(DeviceCommand).where(
            DeviceCommand.workline_id == seeded.workline_id,
            DeviceCommand.task_type == "SORTING_SOURCE_PICK",
        )
    )
    outbox = await db.scalar(
        select(SystemOutbox).where(SystemOutbox.dispatch_key == f"device-command:{command.command_code}")
        if command is not None
        else select(SystemOutbox).where(SystemOutbox.id == -1)
    )
    assert source_item is not None
    assert source_inbox is not None
    assert command is not None
    assert outbox is not None
    return ProcessedSmtSourcePick(
        seeded=seeded,
        source_item=source_item,
        source_inbox=source_inbox,
        command=command,
        outbox=outbox,
    )


__all__ = [
    "NoopQueueGateway",
    "ProcessedSmtSourcePick",
    "SeededSmtSourcePick",
    "process_smt_source_pick_claim",
    "seed_smt_source_pick_claim",
]
