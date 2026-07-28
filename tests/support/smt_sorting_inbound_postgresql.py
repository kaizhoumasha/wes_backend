"""SMT generated source-pick PostgreSQL heavy tests 的共用 fixture。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from src.app.contracts.external_contract_profile_catalog import WMS_MATERIAL_FLOW_SANDBOX_PROFILE
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
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
from src.app.sys.models.outbox import SystemOutbox
from src.app.workline.models import LineType, WorkLine
from src.app.workline.services.plugin_binding_service import (
    WorklinePluginBindingService,
    workline_plugin_binding_service,
)
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
        is_active=True,
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
    )
    db.add(device)
    await db.flush()
    binding = await workline_plugin_binding_service.activate(
        db,
        workline=workline,
        expected_workline_version=workline.version,
        actor="integration-test",
        reason=f"SMT generated PostgreSQL {suffix}",
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
        devices=[device],
    )
    workline.active_plugin_binding_id = binding.id
    workline.active_plugin_binding_version = binding.binding_version
    workline.active_plugin_config_hash = binding.typed_config_hash
    workline.active_plugin_index_digest = binding.generated_index_digest
    projection = WorklineRuntimeStatusProjection(
        workline_id=workline.id,
        runtime_status=WorkLineRuntimeStatus.READY.value,
    )
    demand = SmtInboundHandoffDemand(
        demand_key=f"it-smt-demand-{suffix}",
        rack_release_id=f"it-smt-release-{suffix}",
        single_layer_rack_code=f"IT-RACK-{suffix}",
        status=SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
        trace_id=f"trace-smt-pg-{suffix}",
    )
    db.add_all([workline, projection, demand])
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
            workline_id=int(workline.id),
            workline_code=workline.line_code,
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
        workline_id=int(workline.id),
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
