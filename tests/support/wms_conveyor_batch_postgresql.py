"""E12 输送线入口批次的 PostgreSQL 共享数据与事务 helper。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from importlib.util import find_spec
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.resource.models import (
    Bin,
    BinCellOccupancy,
    BinCellOccupancyStatus,
    BinSlotSize,
    BinSlotTemplate,
    BinType,
    Rack,
    RackBinMount,
    RackKind,
    RackPlacement,
    RackSlotKind,
    RackSlotSide,
    RackSlotTemplate,
    RackType,
    ResourceSourceSystem,
)
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.bin_cell_reservation import WorklineBinCellReservation
from src.app.runtime.orchestration.models.rack_position import (
    WorklineRackPosition,
    WorklineRackPositionRole,
)
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.services.intent.system_capability_intent_service import (
    SystemCapabilityIntentService,
)
from src.app.runtime.system_capabilities.wms.effect_runtime import WmsEffectPreparationRuntime
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.workline.models.plugin_binding import WorklinePluginBinding
from src.app.workline.models.workline import LineType, WorkLine
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

NOW = datetime(2026, 7, 30, 9, 0)
E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"
REVISION = "9cc0848560c6"


@dataclass(frozen=True, slots=True)
class BatchGraph:
    workline_id: int
    session_id: int
    execution_session_id: int
    work_item_id: int
    binding_id: int
    inbox_id: int
    rack_code: str
    bin_codes: tuple[str, ...]
    config: dict[str, Any]


async def with_database(
    scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]],
) -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", REVISION, database_url=database_url)
        engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=8, max_overflow=0)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            await scenario(session_factory)
        finally:
            await engine.dispose()


def domain_types() -> tuple[type[Any], type[Any]]:
    module_name = "src.app.runtime.orchestration.services.wms_conveyor_batch_service"
    assert find_spec(module_name) is not None, "WmsConveyorBatchService module is missing"
    service_type = getattr(import_module(module_name), "WmsConveyorBatchService", None)
    projector_type = getattr(
        import_module("src.app.runtime.orchestration.services.wms_fulfillment_domain_projector"),
        "WmsFulfillmentDomainProjector",
        None,
    )
    assert service_type is not None
    assert projector_type is not None
    return service_type, projector_type


async def seed_batch_graph(
    db: AsyncSession,
    *,
    graph_index: int = 1,
    entry_capacity: int = 4,
    ctu_capacity: int = 3,
    bin_count: int = 4,
    occupied_remaining_by_bin: Mapping[str, Decimal | None] | None = None,
) -> BatchGraph:
    suffix = str(graph_index)
    line_code = f"SMT-E12-{suffix}"
    entry_queue_code = f"CONVEYOR_ENTRY_{suffix}"
    return_queue_code = f"RETURN_QUEUE_{suffix}"
    config = {
        "provider_profile": "default",
        "source_arm_role": "SORTING_SOURCE_ARM",
        "ctu_basket_capacity": ctu_capacity,
        "conveyor_entry_queue": {
            "code": entry_queue_code,
            "role": "ENTRY",
            "capacity": entry_capacity,
            "order_policy": "FIFO",
        },
        "return_queue": {
            "code": return_queue_code,
            "role": "RETURN_QUEUE",
            "order_policy": "FIFO",
        },
    }
    config_hash = sha256_digest(config)
    workline = WorkLine(
        line_code=line_code,
        line_name=f"SMT E12 {suffix}",
        line_type=LineType.AUTO,
        plugin_key="smt_sorting_inbound",
        contract_version="smt_sorting_inbound.v1",
        # 故意与 immutable binding 漂移；E12 不得从这里读取队列或容量。
        config={
            "ctu_basket_capacity": 99,
            "conveyor_entry_queue": {"code": "MUTABLE_WRONG", "capacity": 99},
        },
        is_active=True,
    )
    db.add(workline)
    await db.flush()
    assert workline.id is not None
    binding = WorklinePluginBinding(
        workline_id=workline.id,
        plugin_key="smt_sorting_inbound",
        contract_version="smt_sorting_inbound.v1",
        binding_version=graph_index,
        typed_config_json=config,
        typed_config_hash=config_hash,
        generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        environment="test",
        activated_at=NOW,
        activated_by="pytest",
        activated_reason="E12 PostgreSQL contract",
    )
    db.add(binding)
    await db.flush()
    assert binding.id is not None
    workline.active_plugin_binding_id = binding.id
    workline.active_plugin_binding_version = binding.binding_version
    workline.active_plugin_config_hash = binding.typed_config_hash
    workline.active_plugin_index_digest = binding.generated_index_digest

    session = WorklineSession(
        session_code=f"E12-SESSION-{suffix}",
        workline_id=workline.id,
        plugin_key=binding.plugin_key,
        contract_version=binding.contract_version,
        plugin_binding_id=binding.id,
        plugin_binding_version=binding.binding_version,
        plugin_config_hash=binding.typed_config_hash,
        plugin_index_digest=binding.generated_index_digest,
        business_key=f"e12-batch-producer:{suffix}",
        status=SessionStatus.RUNNING,
        trace_id=f"trace-e12-{suffix}",
    )
    execution_session = ExecutionSession(
        workline_id=workline.id,
        plugin_key=binding.plugin_key,
        manifest_version=binding.contract_version,
        plugin_binding_id=binding.id,
        plugin_binding_version=binding.binding_version,
        plugin_config_hash=binding.typed_config_hash,
        plugin_index_digest=binding.generated_index_digest,
        state="RUNNING",
    )
    db.add_all([session, execution_session])
    await db.flush()
    assert session.id is not None and execution_session.id is not None
    correlation_id = f"e12-batch-producer:{suffix}"
    correlation = ExecutionCorrelation(
        correlation_id=correlation_id,
        execution_session_id=execution_session.id,
        trace_id=f"trace-e12-{suffix}",
        source_event_id=f"e12-trigger-{suffix}",
        business_owner_key=f"workline:{workline.id}:entry:{entry_queue_code}",
    )
    work_item = ExecutionWorkItem(
        execution_session_id=execution_session.id,
        correlation_id=correlation_id,
        plugin_key=binding.plugin_key,
        manifest_version=binding.contract_version,
        plugin_binding_id=binding.id,
        plugin_binding_version=binding.binding_version,
        plugin_config_hash=binding.typed_config_hash,
        plugin_index_digest=binding.generated_index_digest,
        object_type="session",
        object_key=f"workline:{workline.id}",
        current_step="WMS_E12_RESERVE",
    )
    db.add_all([correlation, work_item])
    await db.flush()
    assert work_item.id is not None
    inbox = RuntimeInbox(
        execution_session_id=execution_session.id,
        workline_session_id=session.id,
        correlation_id=correlation_id,
        kind="INTERNAL_EVENT",
        workline_id=workline.id,
        trace_id=f"trace-e12-{suffix}",
        event_id=f"e12-trigger-{suffix}",
        provider_code="RUNTIME",
        event_type="WMS_E12_RESERVE",
        source_event_id=f"e12-trigger-{suffix}",
        payload_hash="e" * 64,
        payload_json={"logical_route": "WMS_E12_RESERVE"},
        payload_schema_version=1,
        claim_bucket_key=f"workline:{workline.id}:entry:{entry_queue_code}",
        received_at=1_775_000_000_000 + graph_index,
    )
    db.add(inbox)

    rack_type = RackType(
        rack_type_code=f"FIVE-E12-{suffix}",
        rack_type_name=f"E12 five-layer rack {suffix}",
        rack_kind=RackKind.FIVE_LAYER,
        slot_count=bin_count,
        has_side=True,
    )
    rack = Rack(
        rack_code=f"FIVE-E12-{suffix}",
        rack_type_code=rack_type.rack_type_code,
        source_system=ResourceSourceSystem.WMS,
    )
    rack_slots = [
        RackSlotTemplate(
            rack_type_code=rack_type.rack_type_code,
            slot_code=f"A-{index:02d}",
            side=RackSlotSide.A if index % 2 else RackSlotSide.B,
            layer_no=(index + 1) // 2,
            position_no=index,
            slot_kind=RackSlotKind.BIN_SLOT,
        )
        for index in range(1, bin_count + 1)
    ]
    placement = RackPlacement(
        rack_code=rack.rack_code,
        rack_kind=RackKind.FIVE_LAYER,
        workline_id=workline.id,
        workline_code=workline.line_code,
        position_code="TARGET_STATION",
        position_role="SORTING_INBOUND_TARGET",
        placement_status="ARRIVED",
        source_system=ResourceSourceSystem.WMS,
        source_event_id=f"e12-rack-arrived-{suffix}",
        started_at=NOW,
    )
    target_position = WorklineRackPosition(
        workline_id=workline.id,
        workline_code=workline.line_code,
        position_code="TARGET_STATION",
        position_name=f"E12 target station {suffix}",
        position_role=WorklineRackPositionRole.SMT_SORTER_STATION,
        allowed_rack_kind=RackKind.FIVE_LAYER,
        capacity=1,
        enabled=True,
    )
    db.add_all([rack_type, rack, *rack_slots, target_position, placement])

    bin_codes = tuple(f"E12-{suffix}-BIN-{index:02d}" for index in range(1, bin_count + 1))
    for index, bin_code in enumerate(bin_codes, start=1):
        bin_type = BinType(
            bin_type_code=f"{bin_code}-TYPE",
            bin_type_name=f"{bin_code} type",
        )
        bin_slot = BinSlotTemplate(
            bin_type_code=bin_type.bin_type_code,
            bin_slot_index=1,
            bin_slot_code="CELL-1",
            slot_size=BinSlotSize.SEVEN_INCH,
            max_depth_mm=100,
        )
        bin_master = Bin(
            bin_code=bin_code,
            bin_type_code=bin_type.bin_type_code,
            source_system=ResourceSourceSystem.WMS,
        )
        mount = RackBinMount(
            rack_code=rack.rack_code,
            rack_slot_code=f"A-{index:02d}",
            bin_code=bin_code,
            mount_status="MOUNTED",
            source_system=ResourceSourceSystem.WMS,
            source_event_id=f"e12-bin-mounted-{suffix}-{index}",
            started_at=NOW,
        )
        db.add_all([bin_type, bin_slot, bin_master, mount])
        remaining = (occupied_remaining_by_bin or {}).get(bin_code, Decimal("-1"))
        if remaining != Decimal("-1"):
            occupancy = BinCellOccupancy(
                bin_code=bin_code,
                bin_cell_code="CELL-1",
                bin_cell_index="1",
                material_identity_key=f"material:{bin_code}",
                reel_count=1,
                used_depth_mm=Decimal("50"),
                capacity_depth_mm=Decimal("100"),
                remaining_depth_mm=remaining,
                occupancy_status=BinCellOccupancyStatus.OCCUPIED,
                source_system=ResourceSourceSystem.WMS,
                source_event_id=f"e12-occupancy-{bin_code}",
                started_at=NOW,
            )
            db.add(occupancy)
    await db.flush()
    assert inbox.id is not None
    return BatchGraph(
        workline_id=workline.id,
        session_id=session.id,
        execution_session_id=execution_session.id,
        work_item_id=work_item.id,
        binding_id=binding.id,
        inbox_id=inbox.id,
        rack_code=rack.rack_code,
        bin_codes=bin_codes,
        config=config,
    )


async def execution_ctx(db: AsyncSession, graph: BatchGraph) -> dict[str, Any]:
    session = await db.get(WorklineSession, graph.session_id)
    workline = await db.get(WorkLine, graph.workline_id)
    work_item = await db.get(ExecutionWorkItem, graph.work_item_id)
    binding = await db.get(WorklinePluginBinding, graph.binding_id)
    inbox = await db.get(RuntimeInbox, graph.inbox_id)
    assert all(value is not None for value in (session, workline, work_item, binding, inbox))
    return {
        "db": db,
        "session": session,
        "workline": workline,
        "work_item": work_item,
        "plugin_binding": binding,
        "inbox": inbox,
        "trace_id": inbox.trace_id,
        "correlation_id": inbox.correlation_id,
    }


async def reserve_batch(service: Any, db: AsyncSession, graph: BatchGraph) -> Any:
    return await service.reserve_batch(await execution_ctx(db, graph))


def runtime_intent(ctx: dict[str, Any], reservation: Any) -> RuntimeIntent:
    assert reservation.operation is not None and reservation.request is not None
    capability_key, contract_version = reservation.operation.identity.rsplit("@", maxsplit=1)
    definition = SystemCapabilityIntentService().get_effect_definition(capability_key, contract_version)
    assert definition is not None
    return RuntimeIntent.system_capability(
        capability_key=capability_key,
        contract_version=contract_version,
        operation_key=reservation.request.batch_id,
        dispatch_key=reservation.request.dispatch_key,
        payload=reservation.request,
        precondition={"capacity_snapshot_version": reservation.request.capacity_snapshot_version},
        fact_version=reservation.request.capacity_snapshot_version,
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={
            "binding_id": ctx["plugin_binding"].id,
            "binding_version": ctx["plugin_binding"].binding_version,
        },
        provider_snapshot={"provider_code": "RUNTIME", "profile": definition.admission},
    )


async def claim_reservation(
    *,
    db: AsyncSession,
    graph: BatchGraph,
    reservation: Any,
) -> tuple[dict[str, Any], Any, Any]:
    ctx = await execution_ctx(db, graph)
    ctx["wms_conveyor_batch_claim"] = reservation.claim
    intent = runtime_intent(ctx, reservation)
    prepared = await SystemCapabilityIntentService().prepare_and_claim(ctx, intent)
    assert prepared.intent_log is not None
    execution = SimpleNamespace(
        db=db,
        ctx=ctx,
        intent=intent,
        intent_log=prepared.intent_log,
        idempotency_key=prepared.idempotency_key,
    )
    return ctx, prepared, execution


async def prepare_reservation(
    *,
    projector: Any,
    db: AsyncSession,
    graph: BatchGraph,
    reservation: Any,
) -> Any:
    _ctx, prepared, execution = await claim_reservation(db=db, graph=graph, reservation=reservation)
    await WmsEffectPreparationRuntime(
        catalog=build_provider_catalog(),
        domain_projector=projector,
    ).prepare(
        reservation.operation,
        reservation.request,
        execution=execution,
    )
    return prepared


async def seed_reserved_positions(
    db: AsyncSession,
    graph: BatchGraph,
    *,
    member_positions: Sequence[int] = (),
    membership_positions: Sequence[tuple[int, str]] = (),
) -> None:
    """只为候选 union 构造前序投影；关闭 FK/shape 后仍由正式查询读取。"""

    queue_code = str(graph.config["conveyor_entry_queue"]["code"])
    await db.execute(text("SET session_replication_role = replica"))
    try:
        for position in member_positions:
            await db.execute(
                text(
                    """
                    INSERT INTO wes_runtime.wms_conveyor_batch_members (
                        runtime_intent_log_id, route_instance_id, workline_id, queue_code,
                        direction, sequence_no, bin_code, reserved_queue_position,
                        member_state, staged_at_ms
                    )
                    VALUES (
                        :intent_id, :route_id, :workline_id, :queue_code,
                        'INBOUND', 1, :bin_code, :position, 'CANDIDATE', 1
                    )
                    """
                ),
                {
                    "intent_id": 900_000 + position + graph.workline_id,
                    "route_id": f"reserved-member-route:{graph.workline_id}:{position}",
                    "workline_id": graph.workline_id,
                    "queue_code": queue_code,
                    "bin_code": f"RESERVED-MEMBER-{graph.workline_id}-{position}",
                    "position": position,
                },
            )
        for position, status in membership_positions:
            await db.execute(
                text(
                    """
                    INSERT INTO wes_runtime.conveyor_queue_memberships (
                        bin_code, workline_id, conveyor_code, queue_code, queue_role,
                        membership_status, entered_at, route_instance_id, queue_position,
                        evidence_json
                    )
                    VALUES (
                        :bin_code, :workline_id, :queue_code, :queue_code, 'ENTRY',
                        :status, 1, :route_id, :position, '{}'::json
                    )
                    """
                ),
                {
                    "bin_code": f"RESERVED-MEMBERSHIP-{graph.workline_id}-{position}",
                    "workline_id": graph.workline_id,
                    "queue_code": queue_code,
                    "status": status,
                    "route_id": f"reserved-membership-route:{graph.workline_id}:{position}",
                    "position": position,
                },
            )
    finally:
        await db.execute(text("SET session_replication_role = origin"))
    await db.flush()


async def mark_bin_unavailable(db: AsyncSession, graph: BatchGraph, *, bin_code: str, reason: str) -> None:
    if reason == "disabled_bin":
        bin_master = await db.scalar(select(Bin).where(Bin.bin_code == bin_code))
        assert bin_master is not None
        bin_master.status = "DISABLED"
    elif reason == "disabled_type":
        bin_master = await db.scalar(select(Bin).where(Bin.bin_code == bin_code))
        assert bin_master is not None
        bin_type = await db.scalar(select(BinType).where(BinType.bin_type_code == bin_master.bin_type_code))
        assert bin_type is not None
        bin_type.active = False
    elif reason == "no_active_slot":
        bin_master = await db.scalar(select(Bin).where(Bin.bin_code == bin_code))
        assert bin_master is not None
        bin_slot = await db.scalar(
            select(BinSlotTemplate).where(BinSlotTemplate.bin_type_code == bin_master.bin_type_code)
        )
        assert bin_slot is not None
        bin_slot.active = False
    elif reason in {"full", "unknown", "occupied_unknown"}:
        occupancy = BinCellOccupancy(
            bin_code=bin_code,
            bin_cell_code="CELL-1",
            bin_cell_index="1",
            material_identity_key=f"unavailable:{bin_code}",
            reel_count=1,
            used_depth_mm=Decimal("100"),
            capacity_depth_mm=Decimal("100"),
            remaining_depth_mm=None if reason in {"unknown", "occupied_unknown"} else Decimal("0"),
            occupancy_status={
                "full": BinCellOccupancyStatus.FULL,
                "unknown": BinCellOccupancyStatus.UNKNOWN,
                "occupied_unknown": BinCellOccupancyStatus.OCCUPIED,
            }[reason],
            source_system=ResourceSourceSystem.WMS,
            source_event_id=f"unavailable:{reason}:{bin_code}",
            started_at=NOW,
        )
        db.add(occupancy)
    elif reason == "reservation":
        session = await db.get(WorklineSession, graph.session_id)
        workline = await db.get(WorkLine, graph.workline_id)
        assert session is not None and workline is not None
        db.add(
            WorklineBinCellReservation(
                reservation_key=f"unavailable-reservation:{bin_code}",
                workline_id=graph.workline_id,
                workline_code=workline.line_code,
                session_id=session.id,
                correlation_id=f"e12-batch-producer:{graph.workline_id}",
                trace_id=session.trace_id,
                pkg_code=f"PKG-{bin_code}",
                bin_code=bin_code,
                bin_cell_code="CELL-1",
                bin_cell_index="1",
                reservation_status="PLANNED",
                source_event_id=f"unavailable-reservation:{bin_code}",
                reserved_at=NOW,
            )
        )
    elif reason in {"route", "membership"}:
        await db.execute(text("SET session_replication_role = replica"))
        try:
            route_id = f"unavailable-route:{bin_code}"
            await db.execute(
                text(
                    """
                    INSERT INTO wes_runtime.bin_route_instances (
                        route_instance_id, bin_code, workline_id, created_by_e12_intent_id,
                        current_node, route_version, lifecycle_state,
                        last_transition_source, last_transition_source_event_id
                    )
                    VALUES (
                        :route_id, :bin_code, :workline_id, 999999,
                        'CTU_INBOUND_IN_FLIGHT', 1, 'ACTIVE',
                        'TEST', :route_id
                    )
                    """
                ),
                {"route_id": route_id, "bin_code": bin_code, "workline_id": graph.workline_id},
            )
            if reason == "membership":
                await db.execute(
                    text(
                        """
                        INSERT INTO wes_runtime.conveyor_queue_memberships (
                            bin_code, workline_id, conveyor_code, queue_code, queue_role,
                            membership_status, entered_at, route_instance_id, queue_position,
                            evidence_json
                        )
                        VALUES (
                            :bin_code, :workline_id, 'OTHER', 'OTHER', 'ENTRY',
                            'RECONCILING', 1, :route_id, 99, '{}'::json
                        )
                        """
                    ),
                    {"bin_code": bin_code, "workline_id": graph.workline_id, "route_id": route_id},
                )
                await db.execute(
                    text(
                        "UPDATE wes_runtime.bin_route_instances "
                        "SET lifecycle_state = 'CLOSED', current_node = 'FIVE_RACK', closed_at_ms = 1 "
                        "WHERE route_instance_id = :route_id"
                    ),
                    {"route_id": route_id},
                )
        finally:
            await db.execute(text("SET session_replication_role = origin"))
    elif reason == "owner":
        await db.execute(
            text(
                """
                INSERT INTO wes_runtime.material_flow_owners (
                    workline_id, object_type, object_key, owner_type, owner_key,
                    lifecycle_state, source_event_id, acquired_at_ms
                )
                VALUES (
                    :workline_id, 'BIN', :bin_code, 'PIECE_SORTING', :owner_key,
                    'ACTIVE', :owner_key, 1
                )
                """
            ),
            {
                "workline_id": graph.workline_id,
                "bin_code": bin_code,
                "owner_key": f"unavailable-owner:{bin_code}",
            },
        )
    else:
        raise ValueError(f"unsupported unavailable reason: {reason}")
    await db.flush()


__all__ = [
    "E12",
    "NOW",
    "BatchGraph",
    "claim_reservation",
    "domain_types",
    "execution_ctx",
    "mark_bin_unavailable",
    "prepare_reservation",
    "reserve_batch",
    "seed_batch_graph",
    "seed_reserved_positions",
    "with_database",
]
