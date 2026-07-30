"""E11 满箱交换真实 PostgreSQL 测试的共享数据与事务 helper。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from importlib.util import find_spec
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.resource.models import (
    Bin,
    BinCellOccupancy,
    BinMaterialMount,
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
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.system_capabilities.wms.effect_runtime import WmsEffectPreparationRuntime
from src.app.wms_integration.ports.fulfillment_operations import FullBoxExchangeResult
from src.app.workline.models.workline import LineType, WorkLine
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

REVISION = "f9ffbef8992a"
E11 = "wms.fulfillment.full_box_exchange@v1"
NOW = datetime(2026, 7, 30, 8, 0)


@dataclass(slots=True)
class ExchangeBinFacts:
    bin_code: str
    source_slot_id: str
    occupancy: BinCellOccupancy
    material_mounts: list[BinMaterialMount]
    source_items: list[SmtInboundHandoffSourceItem]


@dataclass(slots=True)
class ExchangeGraph:
    workline: WorkLine
    demand: SmtInboundHandoffDemand
    placement: RackPlacement
    bins: dict[str, ExchangeBinFacts]


def domain_types() -> tuple[type[Any], type[Any]]:
    module_name = "src.app.runtime.orchestration.services.full_box_exchange_service"
    assert find_spec(module_name) is not None, "FullBoxExchangeService module is missing"
    service_type = getattr(import_module(module_name), "FullBoxExchangeService", None)
    projector_type = getattr(
        import_module("src.app.runtime.orchestration.services.wms_fulfillment_domain_projector"),
        "WmsFulfillmentDomainProjector",
        None,
    )
    assert service_type is not None
    assert projector_type is not None
    return service_type, projector_type


async def with_database(
    scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]],
    *,
    revision: str = REVISION,
) -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", revision, database_url=database_url)
        engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=8, max_overflow=0)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            await scenario(session_factory)
        finally:
            await engine.dispose()


def execution_ctx(db: AsyncSession, graph: ExchangeGraph) -> dict[str, Any]:
    return {
        "db": db,
        # E11 domain authority 不承载 plugin session/work-item/inbox/binding；
        # 只传递本事务与已持久化 WorkLine 锚点。
        "workline": graph.workline,
        "trace_id": graph.demand.trace_id,
    }


async def seed_exchange_graph(
    db: AsyncSession,
    *,
    full_bins: Sequence[str] = ("FULL-1",),
    include_partial_bin: bool = False,
    materials_per_full_bin: int = 1,
    bin_slot_count: int = 1,
    bin_slot_counts: Mapping[str, int] | None = None,
    station_code: str | None = "FULL-BOX-EXCHANGE",
    rack_face: str | None = "A",
    trace_id: str | None = "trace-e11",
    graph_index: int = 1,
) -> ExchangeGraph:
    suffix = "" if graph_index == 1 else f"-{graph_index}"
    line_code = "ROUGH-10" if graph_index == 1 else f"ROUGH-{graph_index * 10}"
    resolved_trace_id = f"{trace_id}{suffix}" if suffix and trace_id == "trace-e11" else trace_id
    workline = WorkLine(
        line_code=line_code,
        line_name=f"rough sorter {graph_index * 10}",
        line_type=LineType.AUTO,
    )
    rack_type = RackType(
        rack_type_code=f"SINGLE-A-B{suffix}",
        rack_type_name="single rack",
        rack_kind=RackKind.SINGLE_LAYER,
        slot_count=4,
        has_side=True,
    )
    rack = Rack(
        rack_code=f"SINGLE-{graph_index}",
        rack_type_code=rack_type.rack_type_code,
        source_system=ResourceSourceSystem.WMS,
    )
    slot_codes = ("A-01", "A-02", "A-03", "B-01")
    slot_templates = [
        RackSlotTemplate(
            rack_type_code=rack_type.rack_type_code,
            slot_code=slot_code,
            side=RackSlotSide.B if slot_code.startswith("B-") else RackSlotSide.A,
            slot_kind=RackSlotKind.BIN_SLOT,
            position_no=index,
        )
        for index, slot_code in enumerate(slot_codes, start=1)
    ]
    db.add_all([workline, rack_type, rack, *slot_templates])
    await db.flush()
    assert workline.id is not None

    placement = RackPlacement(
        rack_code=rack.rack_code,
        rack_kind=RackKind.SINGLE_LAYER,
        workline_id=workline.id,
        workline_code=workline.line_code,
        position_code="FULL-BOX-EXCHANGE",
        placement_status="ARRIVED",
        source_system=ResourceSourceSystem.WMS,
        source_event_id=f"rack-arrived-e11{suffix}",
        started_at=NOW,
    )
    demand = SmtInboundHandoffDemand(
        demand_key=f"smt-inbound-handoff:release-e11{suffix}",
        rack_release_id=f"release-e11{suffix}",
        source_workline_id=workline.id,
        source_workline_code=workline.line_code,
        single_layer_rack_code=rack.rack_code,
        bin_snapshots_json={},
        status=SmtInboundHandoffDemandStatus.EVALUATING,
        trace_id=resolved_trace_id,
        full_box_exchange_station_code=station_code,
        full_box_exchange_rack_face=rack_face,
    )
    db.add_all([placement, demand])
    await db.flush()
    assert demand.id is not None

    requested_bins = list(full_bins)
    if include_partial_bin:
        requested_bins.append("PARTIAL-1")
    bins: dict[str, ExchangeBinFacts] = {}
    for bin_index, bin_code in enumerate(requested_bins, start=1):
        is_full = bin_code in full_bins
        material_count = materials_per_full_bin if is_full else 1
        source_slot_id = slot_codes[bin_index - 1]
        resolved_slot_count = (bin_slot_counts or {}).get(bin_code, bin_slot_count)
        bin_type = BinType(
            bin_type_code=f"E11-{bin_code}-TYPE",
            bin_type_name=f"E11 exchange bin {bin_code}",
        )
        bin_slot_templates = [
            BinSlotTemplate(
                bin_type_code=bin_type.bin_type_code,
                bin_slot_index=slot_index,
                bin_slot_code=f"CELL-{slot_index}",
                slot_size=BinSlotSize.SEVEN_INCH,
                max_depth_mm=100,
            )
            for slot_index in range(1, resolved_slot_count + 1)
        ]
        bin_master = Bin(
            bin_code=bin_code,
            bin_type_code=bin_type.bin_type_code,
            source_system=ResourceSourceSystem.WMS,
        )
        rack_mount = RackBinMount(
            rack_code=rack.rack_code,
            rack_slot_code=source_slot_id,
            bin_code=bin_code,
            mount_status="MOUNTED",
            source_system=ResourceSourceSystem.WMS,
            source_event_id=f"rack-bin:{bin_code}",
            started_at=NOW,
        )
        occupancy = BinCellOccupancy(
            bin_code=bin_code,
            bin_cell_code="CELL-1",
            bin_cell_index="1",
            material_identity_key=f"{bin_code}-IDENTITY",
            material_code=f"{bin_code}-MAT",
            reel_count=material_count,
            used_depth_mm=Decimal("90" if is_full else "20"),
            capacity_depth_mm=Decimal("100"),
            remaining_depth_mm=Decimal("10" if is_full else "80"),
            occupancy_status="FULL" if is_full else "OCCUPIED",
            source_system=ResourceSourceSystem.WES_RUNTIME,
            source_event_id=f"occupancy:{bin_code}",
            started_at=NOW,
        )
        db.add_all([bin_type, *bin_slot_templates, bin_master, rack_mount, occupancy])
        await db.flush()
        assert occupancy.id is not None
        material_mounts: list[BinMaterialMount] = []
        source_items: list[SmtInboundHandoffSourceItem] = []
        for material_index in range(1, material_count + 1):
            material_mount = BinMaterialMount(
                bin_cell_occupancy_id=occupancy.id,
                cell_stack_position=material_index,
                bin_code=bin_code,
                bin_cell_code=occupancy.bin_cell_code,
                bin_cell_index=occupancy.bin_cell_index,
                material_identity_key=f"{bin_code}-IDENTITY-{material_index}",
                pkg_code=f"{bin_code}-PKG-{material_index}",
                material_code=f"{bin_code}-MAT-{material_index}",
                qty_snapshot=10,
                mount_status="OCCUPIED",
                source_system=ResourceSourceSystem.WES_RUNTIME,
                source_event_id=f"material:{bin_code}:{material_index}",
                started_at=NOW,
            )
            source_item = SmtInboundHandoffSourceItem(
                handoff_demand_id=demand.id,
                item_key=f"release-e11:{bin_code}:{material_index}",
                bin_code=bin_code,
                bin_cell_index=1,
                bin_cell_code=occupancy.bin_cell_code,
                material_identity_key=material_mount.material_identity_key,
                pkg_code=material_mount.pkg_code,
                status=SmtInboundHandoffSourceItemStatus.READY,
            )
            material_mounts.append(material_mount)
            source_items.append(source_item)
        db.add_all([*material_mounts, *source_items])
        await db.flush()
        bins[bin_code] = ExchangeBinFacts(
            bin_code=bin_code,
            source_slot_id=source_slot_id,
            occupancy=occupancy,
            material_mounts=material_mounts,
            source_items=source_items,
        )
    return ExchangeGraph(workline=workline, demand=demand, placement=placement, bins=bins)


async def seed_runtime_intent(
    db: AsyncSession,
    *,
    intent_id: int,
    dispatch_key: str,
    idempotency_key: str,
) -> Any:
    await db.execute(text("SET session_replication_role = replica"))
    try:
        await db.execute(
            text(
                """
                INSERT INTO wes_runtime.runtime_intent_logs (
                    id,
                    execution_session_id,
                    correlation_id,
                    provider_code,
                    capability_key,
                    capability_contract_version,
                    operation_identity,
                    target_domain,
                    target_action,
                    idempotency_key,
                    request_hash,
                    dispatch_key,
                    effect_status
                )
                VALUES (
                    :intent_id,
                    1,
                    :correlation_id,
                    'WMS',
                    'wms.fulfillment.full_box_exchange',
                    'v1',
                    'wms.fulfillment.full_box_exchange@v1',
                    'wms_integration',
                    'full-box-exchange',
                    :idempotency_key,
                    :request_hash,
                    :dispatch_key,
                    'PROPOSED'
                )
                """
            ),
            {
                "intent_id": intent_id,
                "correlation_id": f"corr-{intent_id}",
                "idempotency_key": idempotency_key,
                "request_hash": f"hash-{intent_id}",
                "dispatch_key": dispatch_key,
            },
        )
    finally:
        await db.execute(text("SET session_replication_role = origin"))
    return SimpleNamespace(id=intent_id, dispatch_key=dispatch_key)


async def reserve_exchange(
    service: Any,
    db: AsyncSession,
    graph: ExchangeGraph,
    *,
    full_box_id: str,
    prefer_full_box_exchange: bool | None = None,
) -> Any:
    assert graph.demand.id is not None
    return await service.reserve_root(
        execution_ctx(db, graph),
        handoff_demand_id=graph.demand.id,
        full_box_id=full_box_id,
        prefer_full_box_exchange=prefer_full_box_exchange,
    )


async def prepare_exchange(
    *,
    service: Any,
    projector: Any,
    db: AsyncSession,
    graph: ExchangeGraph,
    full_box_id: str,
    intent_id: int,
    prefer_full_box_exchange: bool | None = None,
) -> Any:
    reservation = await reserve_exchange(
        service,
        db,
        graph,
        full_box_id=full_box_id,
        prefer_full_box_exchange=prefer_full_box_exchange,
    )
    assert reservation.created is True
    await prepare_reservation(
        projector=projector,
        db=db,
        graph=graph,
        reservation=reservation,
        intent_id=intent_id,
    )
    return reservation


async def prepare_reservation(
    *,
    projector: Any,
    db: AsyncSession,
    graph: ExchangeGraph,
    reservation: Any,
    intent_id: int,
) -> None:
    """只经正式 preparation runtime 绑定 intent、owner 与 outbox。"""

    assert reservation.operation is not None
    assert reservation.request is not None
    intent_log = await seed_runtime_intent(
        db,
        intent_id=intent_id,
        dispatch_key=reservation.request.dispatch_key,
        idempotency_key=reservation.request.exchange_request_key,
    )
    ctx = execution_ctx(db, graph)
    ctx["wms_full_box_exchange_claim"] = reservation.claim
    execution = SimpleNamespace(
        db=db,
        ctx=ctx,
        intent=SimpleNamespace(operation_key=reservation.request.exchange_request_key),
        intent_log=intent_log,
        idempotency_key=reservation.request.exchange_request_key,
    )
    await WmsEffectPreparationRuntime(
        catalog=build_provider_catalog(),
        allow_new_claim=lambda _definition: True,
        domain_projector=projector,
    ).prepare(
        reservation.operation,
        reservation.request,
        execution=execution,
    )


async def seed_selected_empty_bin(
    db: AsyncSession,
    *,
    empty_bin_id: str,
    source_slot_id: str,
) -> RackBinMount:
    bin_type = BinType(
        bin_type_code=f"EMPTY-{empty_bin_id}-TYPE",
        bin_type_name=f"empty exchange bin {empty_bin_id}",
    )
    bin_slot = BinSlotTemplate(
        bin_type_code=bin_type.bin_type_code,
        bin_slot_index=1,
        bin_slot_code="CELL-1",
        slot_size=BinSlotSize.SEVEN_INCH,
        max_depth_mm=100,
    )
    bin_master = Bin(
        bin_code=empty_bin_id,
        bin_type_code=bin_type.bin_type_code,
        source_system=ResourceSourceSystem.WMS,
    )
    five_type = RackType(
        rack_type_code=f"FIVE-{empty_bin_id}",
        rack_type_name=f"five rack for {empty_bin_id}",
        rack_kind=RackKind.FIVE_LAYER,
        slot_count=2,
        has_side=False,
    )
    five_rack = Rack(
        rack_code=f"FIVE-RACK-{empty_bin_id}",
        rack_type_code=five_type.rack_type_code,
        source_system=ResourceSourceSystem.WMS,
    )
    templates = [
        RackSlotTemplate(
            rack_type_code=five_type.rack_type_code,
            slot_code=source_slot_id,
            side=RackSlotSide.NONE,
            slot_kind=RackSlotKind.BIN_SLOT,
            position_no=1,
        ),
        RackSlotTemplate(
            rack_type_code=five_type.rack_type_code,
            slot_code="FULL-DEST",
            side=RackSlotSide.NONE,
            slot_kind=RackSlotKind.BIN_SLOT,
            position_no=2,
        ),
    ]
    empty_mount = RackBinMount(
        rack_code=five_rack.rack_code,
        rack_slot_code=source_slot_id,
        bin_code=empty_bin_id,
        mount_status="MOUNTED",
        source_system=ResourceSourceSystem.WMS,
        source_event_id=f"empty-source:{empty_bin_id}",
        started_at=NOW,
    )
    db.add_all([bin_type, bin_slot, bin_master, five_type, five_rack, *templates, empty_mount])
    await db.flush()
    return empty_mount


async def seed_destination_rack(
    db: AsyncSession,
    *,
    rack_code: str,
    slot_id: str = "FULL-DEST",
) -> Rack:
    rack_type = RackType(
        rack_type_code=f"DEST-{rack_code}",
        rack_type_name=f"destination rack for {rack_code}",
        rack_kind=RackKind.FIVE_LAYER,
        slot_count=1,
        has_side=False,
    )
    rack = Rack(
        rack_code=rack_code,
        rack_type_code=rack_type.rack_type_code,
        source_system=ResourceSourceSystem.WMS,
    )
    slot = RackSlotTemplate(
        rack_type_code=rack_type.rack_type_code,
        slot_code=slot_id,
        side=RackSlotSide.NONE,
        slot_kind=RackSlotKind.BIN_SLOT,
        position_no=1,
    )
    db.add_all([rack_type, rack, slot])
    await db.flush()
    return rack


def success_result(
    reservation: Any,
    *,
    empty_bin_id: str,
    five_rack_code: str,
    full_destination_slot_id: str = "FULL-DEST",
    source_version: str = "7",
) -> FullBoxExchangeResult:
    request = reservation.request
    return FullBoxExchangeResult(
        dispatch_key=request.dispatch_key,
        provider_reference=f"provider:{request.exchange_request_key}",
        source_version=source_version,
        exchange_request_key=request.exchange_request_key,
        full_box_id=request.full_box_id,
        selected_empty_box_id=empty_bin_id,
        full_box_destination={
            "rack_id": five_rack_code,
            "bin_id": request.full_box_id,
            "slot_id": full_destination_slot_id,
        },
        empty_box_destination={
            "rack_id": request.rack_id,
            "bin_id": empty_bin_id,
            "slot_id": request.source_slot_id,
        },
        final_relations=[
            {
                "rack_id": five_rack_code,
                "bin_id": request.full_box_id,
                "slot_id": full_destination_slot_id,
            },
            {
                "rack_id": request.rack_id,
                "bin_id": empty_bin_id,
                "slot_id": request.source_slot_id,
            },
        ],
        task_outcome="SUCCESS",
        inventory_source_version=source_version,
    )


def completed_event(result: FullBoxExchangeResult, *, occurred_at_ms: int = 3000) -> EffectReducerEvent:
    return EffectReducerEvent(
        event_type=EffectReducerEventType.STATUS_COMPLETED,
        dispatch_key=result.dispatch_key,
        occurred_at_ms=occurred_at_ms,
        source_event_id=f"completed:{result.dispatch_key}:{occurred_at_ms}",
        evidence_json={"snapshot": {"result": result.model_dump(mode="json")}},
    )


def reject_event(
    dispatch_key: str,
    *,
    event_type: EffectReducerEventType,
    occurred_at_ms: int = 3000,
) -> EffectReducerEvent:
    return EffectReducerEvent(
        event_type=event_type,
        dispatch_key=dispatch_key,
        attempt_no=1 if event_type is EffectReducerEventType.ASYNC_SUBMIT_REJECTED else None,
        occurred_at_ms=occurred_at_ms,
        source_event_id=f"rejected:{event_type.value}:{dispatch_key}",
        reason_code="NO_EMPTY_BOX_AVAILABLE",
        evidence_json={},
    )


__all__ = [
    "E11",
    "NOW",
    "REVISION",
    "ExchangeBinFacts",
    "ExchangeGraph",
    "completed_event",
    "domain_types",
    "execution_ctx",
    "prepare_exchange",
    "prepare_reservation",
    "reject_event",
    "reserve_exchange",
    "seed_destination_rack",
    "seed_exchange_graph",
    "seed_runtime_intent",
    "seed_selected_empty_bin",
    "success_result",
    "with_database",
]
