"""E11 满箱交换根事务 Service。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.device.repositories import DeviceCommandRepository, device_command_repository
from src.app.resource.models import (
    BinMaterialMountStatus,
    RackBinMount,
    RackBinMountStatus,
    RackKind,
    RackPlacementStatus,
    ResourceSourceSystem,
    WmsConfirmationStatus,
)
from src.app.runtime.capabilities.material_flow.contracts.smt_usage_policy import (
    PREFERRED_FULL_BOX_EXCHANGE_REQUESTED_DECISION as _PREFERRED_EXCHANGE_REQUESTED_DECISION,
)
from src.app.runtime.capabilities.material_flow.contracts.smt_usage_policy import (
    REQUIRED_FULL_BOX_EXCHANGE_REQUESTED_DECISION as _REQUIRED_EXCHANGE_REQUESTED_DECISION,
)
from src.app.runtime.capabilities.material_flow.contracts.smt_usage_policy import SMT_USAGE_POLICY
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.repositories.full_box_exchange_repository import (
    FullBoxExchangeRepository,
    full_box_exchange_repository,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    FULL_BOX_EXCHANGE,
    FrozenBinCellOccupancy,
    FullBoxExchangeRequest,
    FullBoxExchangeResult,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.wms_integration.operation_contract import WmsOperationDefinition

_PREFERENCE_BY_DECISION = {
    _PREFERRED_EXCHANGE_REQUESTED_DECISION: True,
    _REQUIRED_EXCHANGE_REQUESTED_DECISION: False,
}


@dataclass(frozen=True, slots=True)
class FullBoxExchangeClaim:
    """reserve→Intent claim→preparation hook 同一事务传播的冻结资源集合。"""

    handoff_demand_id: int
    workline_id: int
    rack_code: str
    rack_face: str
    full_box_id: str
    source_slot_id: str
    exchange_request_key: str
    occupancy_ids: tuple[int, ...]
    prefer_full_box_exchange: bool


@dataclass(frozen=True, slots=True)
class FullBoxExchangeReservation:
    """winner 才携带 operation/request；active root loser 不创建第二 Intent。"""

    demand: SmtInboundHandoffDemand
    claim: FullBoxExchangeClaim | None
    created: bool
    operation: WmsOperationDefinition | None
    request: FullBoxExchangeRequest | None


class FullBoxExchangeService:
    """复用 handoff demand + RuntimeIntent，避免引入第二套 root 状态机。"""

    def __init__(
        self,
        *,
        repository: FullBoxExchangeRepository = full_box_exchange_repository,
        command_repository: DeviceCommandRepository = device_command_repository,
        handoff_service: Any | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._repository = repository
        self._command_repository = command_repository
        self._handoff_service = handoff_service
        self._now_ms = now_ms or (lambda: int(timezone.now_utc().timestamp() * 1000))

    @staticmethod
    def exchange_request_key(*, handoff_demand_id: int, full_box_id: str) -> str:
        prefix = f"wms-e11:{handoff_demand_id}:"
        key = f"{prefix}{full_box_id}"
        if len(key) <= 160:
            return key
        digest = sha256(full_box_id.encode()).hexdigest()
        return f"{prefix}{digest}"[:160]

    async def reserve_root(
        self,
        ctx: dict[str, Any],
        *,
        handoff_demand_id: int,
        full_box_id: str,
        prefer_full_box_exchange: bool | None = None,
    ) -> FullBoxExchangeReservation:
        """锁定 parent demand 后冻结阶段门事实；不在此处取得 owner。"""

        db, workline_id, workline_code = self._validate_execution_context(ctx)
        demand = await self._repository.get_demand_for_update(db, handoff_demand_id)
        if demand is None:
            raise RuntimeError("full box exchange handoff demand is missing")
        resolved_preference = self._resolve_preference(
            demand=demand,
            requested_preference=prefer_full_box_exchange,
        )
        if demand.active_full_box_exchange_intent_id is not None:
            return FullBoxExchangeReservation(
                demand=demand,
                claim=None,
                created=False,
                operation=None,
                request=None,
            )
        self._validate_demand(
            demand,
            workline_id=workline_id,
            workline_code=workline_code,
        )
        placement = await self._repository.get_active_placement_for_update(
            db,
            rack_code=demand.single_layer_rack_code,
        )
        self._validate_placement(demand, placement)
        rack_mounts = await self._repository.list_active_rack_mounts_for_update(
            db,
            rack_code=demand.single_layer_rack_code,
        )
        full_mounts = [mount for mount in rack_mounts if mount.bin_code == full_box_id]
        if len(full_mounts) != 1 or full_mounts[0].mount_status != RackBinMountStatus.MOUNTED:
            raise ValueError("full box active rack mount is missing")
        full_mount = full_mounts[0]
        slot = await self._repository.get_rack_slot_template_for_update(
            db,
            rack_code=demand.single_layer_rack_code,
            slot_code=full_mount.rack_slot_code,
        )
        rack_face = demand.full_box_exchange_rack_face
        if slot is None or slot.side.value != rack_face:
            raise ValueError("full box source slot side differs from frozen rack face")

        occupancies = await self._repository.list_occupancies_for_update(db, bin_code=full_box_id)
        material_mounts = await self._repository.list_material_mounts_for_update(db, bin_code=full_box_id)
        source_items = await self._repository.list_source_items_for_update(
            db,
            demand_id=handoff_demand_id,
            bin_code=full_box_id,
        )
        await self._validate_stage_fences(
            db,
            demand=demand,
            full_box_id=full_box_id,
            occupancies=occupancies,
            material_mounts=material_mounts,
            source_items=source_items,
            prefer_full_box_exchange=resolved_preference,
        )
        frozen_occupancies = self._freeze_occupancies(
            occupancies=occupancies,
            material_mounts=material_mounts,
            source_items=source_items,
        )
        request_key = self.exchange_request_key(
            handoff_demand_id=handoff_demand_id,
            full_box_id=full_box_id,
        )
        request = FullBoxExchangeRequest(
            dispatch_key=request_key,
            exchange_request_key=request_key,
            station_code=str(demand.full_box_exchange_station_code),
            rack_id=demand.single_layer_rack_code,
            rack_face=rack_face,
            full_box_id=full_box_id,
            source_slot_id=full_mount.rack_slot_code,
            occupancies=frozen_occupancies,
        )
        claim = FullBoxExchangeClaim(
            handoff_demand_id=handoff_demand_id,
            workline_id=workline_id,
            rack_code=demand.single_layer_rack_code,
            rack_face=rack_face,
            full_box_id=full_box_id,
            source_slot_id=full_mount.rack_slot_code,
            exchange_request_key=request_key,
            occupancy_ids=tuple(occupancy.id for occupancy in occupancies if occupancy.id is not None),
            prefer_full_box_exchange=resolved_preference,
        )
        ctx["wms_full_box_exchange_claim"] = claim
        return FullBoxExchangeReservation(
            demand=demand,
            claim=claim,
            created=True,
            operation=FULL_BOX_EXCHANGE,
            request=request,
        )

    async def reserve_next_root(
        self,
        ctx: dict[str, Any],
        *,
        handoff_demand_id: int,
        prefer_full_box_exchange: bool | None = None,
    ) -> FullBoxExchangeReservation:
        """按锁定货架挂载的稳定顺序，只 reserve 一个满足阈值的满箱。"""

        db, _workline_id, _workline_code = self._validate_execution_context(ctx)
        demand = await self._repository.get_demand_for_update(db, handoff_demand_id)
        if demand is None:
            raise RuntimeError("full box exchange handoff demand is missing")
        if demand.active_full_box_exchange_intent_id is not None:
            return FullBoxExchangeReservation(
                demand=demand,
                claim=None,
                created=False,
                operation=None,
                request=None,
            )
        self._validate_demand(
            demand,
            workline_id=_workline_id,
            workline_code=_workline_code,
        )
        resolved_preference = self._resolve_preference(
            demand=demand,
            requested_preference=prefer_full_box_exchange,
        )
        mounts = await self._repository.list_active_rack_mounts_for_update(
            db,
            rack_code=demand.single_layer_rack_code,
        )
        bin_codes = tuple(mount.bin_code for mount in mounts)
        locked_occupancies = await self._repository.list_occupancies_for_bins_for_update(
            db,
            bin_codes=bin_codes,
        )
        usage_cells_by_bin = await self._load_usage_cells(
            db,
            bin_codes=bin_codes,
            locked_occupancies=locked_occupancies,
        )
        for mount in mounts:
            usage_band = self._usage_band(usage_cells_by_bin[mount.bin_code])
            if usage_band == "REQUIRE_FULL_BOX_EXCHANGE" or (
                usage_band == "PREFERRED_FULL_BOX_EXCHANGE" and resolved_preference
            ):
                return await self.reserve_root(
                    ctx,
                    handoff_demand_id=handoff_demand_id,
                    full_box_id=mount.bin_code,
                    prefer_full_box_exchange=resolved_preference,
                )
        return FullBoxExchangeReservation(
            demand=demand,
            claim=None,
            created=False,
            operation=None,
            request=None,
        )

    async def prepare_effect(
        self,
        db: AsyncSession,
        *,
        claim: FullBoxExchangeClaim,
        request: FullBoxExchangeRequest,
        intent_id: int,
    ) -> None:
        """仅供 domain projector hook：在 Outbox 前原子取得四类 owner 并绑定 active Intent。"""

        demand = await self._repository.get_demand_for_update(db, claim.handoff_demand_id)
        if demand is None or demand.active_full_box_exchange_intent_id is not None:
            raise ValueError("full box exchange active root drifted")
        if (
            request.exchange_request_key != claim.exchange_request_key
            or request.full_box_id != claim.full_box_id
            or request.rack_id != claim.rack_code
            or request.rack_face != claim.rack_face
            or request.source_slot_id != claim.source_slot_id
        ):
            raise ValueError("full box exchange frozen request drifted")
        objects = (
            ("RACK", claim.rack_code),
            ("RACK_FACE", f"{claim.rack_code}:{claim.rack_face}"),
            ("BIN", claim.full_box_id),
            *(("OCCUPANCY", str(occupancy_id)) for occupancy_id in claim.occupancy_ids),
        )
        await self._repository.insert_full_box_exchange_owners(
            db,
            workline_id=claim.workline_id,
            owner_key=claim.exchange_request_key,
            owner_intent_id=intent_id,
            objects=objects,
            source_event_id=f"wms-e11-prepare:{claim.handoff_demand_id}:{intent_id}",
            occurred_at_ms=self._now_ms(),
        )
        demand.active_full_box_exchange_intent_id = intent_id
        frozen_decision = (
            _PREFERRED_EXCHANGE_REQUESTED_DECISION
            if claim.prefer_full_box_exchange
            else _REQUIRED_EXCHANGE_REQUESTED_DECISION
        )
        if demand.decision_status in _PREFERENCE_BY_DECISION and demand.decision_status != frozen_decision:
            raise ValueError("full box exchange threshold conflicts with frozen parent decision")
        demand.decision_status = frozen_decision
        demand.status = SmtInboundHandoffDemandStatus.WAITING_FULL_BOX_EXCHANGE
        demand.failure_code = None
        demand.failure_message = None
        await db.flush()

    async def get_demand_by_dispatch_for_update(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
    ) -> SmtInboundHandoffDemand | None:
        return await self._repository.get_demand_by_dispatch_for_update(
            db,
            dispatch_key=dispatch_key,
        )

    async def project_success(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        request: FullBoxExchangeRequest,
        result: FullBoxExchangeResult,
        occurred_at_ms: int,
        source_event_id: str,
    ) -> None:
        """按冻结集合投影交换关系；任一漂移抛错并由调用事务整体回滚。"""

        if demand.id is None or demand.active_full_box_exchange_intent_id is None:
            raise ValueError("full box exchange active intent is missing")
        occurred_at = timezone.to_db_datetime(occurred_at_ms / 1000)
        if occurred_at is None:
            raise ValueError("full box exchange terminal timestamp is invalid")
        destination_slots = await self._repository.list_rack_slots_for_update(
            db,
            rack_slot_keys=(
                (
                    result.full_box_destination.rack_id,
                    result.full_box_destination.slot_id,
                ),
                (
                    result.empty_box_destination.rack_id,
                    result.empty_box_destination.slot_id,
                ),
            ),
        )
        full_mount = await self._repository.get_active_bin_mount_for_update(
            db,
            bin_code=request.full_box_id,
        )
        empty_mount = await self._repository.get_active_bin_mount_for_update(
            db,
            bin_code=result.selected_empty_box_id,
        )
        if (
            full_mount is None
            or full_mount.rack_code != request.rack_id
            or full_mount.rack_slot_code != request.source_slot_id
            or full_mount.mount_status != RackBinMountStatus.MOUNTED
        ):
            raise ValueError("frozen full box source mount drifted")
        if empty_mount is None or empty_mount.mount_status != RackBinMountStatus.MOUNTED:
            raise ValueError("selected empty box active source mount is invalid")

        active_mounts = await self._repository.list_active_rack_mounts_for_racks_for_update(
            db,
            rack_codes=tuple(
                dict.fromkeys(
                    (
                        request.rack_id,
                        empty_mount.rack_code,
                        result.full_box_destination.rack_id,
                    )
                )
            ),
        )
        self._validate_destination_slots(
            active_mounts=active_mounts,
            destination_slots=destination_slots,
            full_mount=full_mount,
            empty_mount=empty_mount,
            request=request,
            result=result,
        )
        occupancies = await self._repository.list_occupancies_for_update(
            db,
            bin_code=request.full_box_id,
        )
        material_mounts = await self._repository.list_material_mounts_for_update(
            db,
            bin_code=request.full_box_id,
        )
        source_items = await self._repository.list_source_items_for_update(
            db,
            demand_id=demand.id,
            bin_code=request.full_box_id,
        )
        self._validate_terminal_frozen_sets(
            request=request,
            occupancies=occupancies,
            material_mounts=material_mounts,
            source_items=source_items,
        )
        if await self._repository.list_occupancies_for_update(
            db,
            bin_code=result.selected_empty_box_id,
        ):
            raise ValueError("selected empty box has active occupancy")
        if await self._repository.list_material_mounts_for_update(
            db,
            bin_code=result.selected_empty_box_id,
        ):
            raise ValueError("selected empty box has active material")
        owners = await self._repository.list_active_owners_for_update(
            db,
            owner_key=request.exchange_request_key,
        )
        self._validate_terminal_owners(
            demand=demand,
            request=request,
            occupancies=occupancies,
            owners=owners,
        )

        full_mount.mount_status = RackBinMountStatus.UNMOUNTED
        full_mount.ended_at = occurred_at
        empty_mount.mount_status = RackBinMountStatus.UNMOUNTED
        empty_mount.ended_at = occurred_at
        self._repository.add_rack_mounts(
            db,
            (
                RackBinMount(
                    rack_code=result.full_box_destination.rack_id,
                    rack_slot_code=result.full_box_destination.slot_id,
                    bin_code=request.full_box_id,
                    mount_status=RackBinMountStatus.MOUNTED,
                    source_system=ResourceSourceSystem.WMS,
                    source_event_id=source_event_id,
                    source_version=result.source_version,
                    trace_id=demand.trace_id,
                    started_at=occurred_at,
                ),
                RackBinMount(
                    rack_code=request.rack_id,
                    rack_slot_code=request.source_slot_id,
                    bin_code=result.selected_empty_box_id,
                    mount_status=RackBinMountStatus.MOUNTED,
                    source_system=ResourceSourceSystem.WMS,
                    source_event_id=source_event_id,
                    source_version=result.source_version,
                    trace_id=demand.trace_id,
                    started_at=occurred_at,
                ),
            ),
        )
        for mount in material_mounts:
            mount.wms_confirmation_status = WmsConfirmationStatus.CONFIRMED
            mount.wms_inventory_version = result.inventory_source_version
        for item in source_items:
            item.status = SmtInboundHandoffSourceItemStatus.EXCHANGED
            item.completed_at = occurred_at
        for owner in owners:
            owner.lifecycle_state = "RELEASED"
            owner.released_at_ms = occurred_at_ms
            owner.source_event_id = source_event_id
        demand.active_full_box_exchange_intent_id = None

        remaining_mounts = [
            mount
            for mount in active_mounts
            if mount.rack_code == request.rack_id and mount.bin_code != request.full_box_id and mount.ended_at is None
        ]
        remaining_bin_codes = tuple(mount.bin_code for mount in remaining_mounts)
        remaining_occupancies = await self._repository.list_occupancies_for_bins_for_update(
            db,
            bin_codes=remaining_bin_codes,
        )
        remaining_usage_cells = await self._load_usage_cells(
            db,
            bin_codes=remaining_bin_codes,
            locked_occupancies=remaining_occupancies,
        )
        if self._has_remaining_exchange_candidate(
            usage_cells_by_bin=remaining_usage_cells,
            decision_status=demand.decision_status,
        ):
            demand.status = SmtInboundHandoffDemandStatus.EVALUATING
            await db.flush()
            return
        # 最后一只满箱 terminal success 后解除 E11 waiting gate，再由既有摘要归约决定
        # READY_FOR_SORTING/COMPLETED；不能让 WAITING 状态短路该归约。
        demand.status = SmtInboundHandoffDemandStatus.EVALUATING
        handoff_service = self._handoff_service
        if handoff_service is None:
            # 延迟加载 intent 子包，避免 service registry 初始化时形成 projector 环。
            from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
                smt_inbound_handoff_service,
            )

            handoff_service = smt_inbound_handoff_service
        await handoff_service.recalculate_demand_status(
            db,
            demand,
            reason="wms_full_box_exchange_success",
        )

    @staticmethod
    def _validate_destination_slots(
        *,
        active_mounts: list[RackBinMount],
        destination_slots: list[tuple[Any, Any]],
        full_mount: RackBinMount,
        empty_mount: RackBinMount,
        request: FullBoxExchangeRequest,
        result: FullBoxExchangeResult,
    ) -> None:
        if (
            result.empty_box_destination.rack_id != request.rack_id
            or result.empty_box_destination.slot_id != request.source_slot_id
        ):
            raise ValueError("empty box destination differs from frozen source slot")
        expected_coordinates = {
            (
                result.full_box_destination.rack_id,
                result.full_box_destination.slot_id,
            ),
            (
                result.empty_box_destination.rack_id,
                result.empty_box_destination.slot_id,
            ),
        }
        actual_coordinates = {(rack.rack_code, slot.slot_code) for rack, slot in destination_slots}
        if actual_coordinates != expected_coordinates:
            raise ValueError("full box exchange destination rack/slot master is missing")
        for mount in active_mounts:
            if (
                mount.rack_code == result.full_box_destination.rack_id
                and mount.rack_slot_code == result.full_box_destination.slot_id
                and mount.id not in {full_mount.id, empty_mount.id}
            ):
                raise ValueError("full box destination slot has an active conflict")
            if (
                mount.rack_code == result.empty_box_destination.rack_id
                and mount.rack_slot_code == result.empty_box_destination.slot_id
                and mount.id != full_mount.id
            ):
                raise ValueError("empty box destination slot has an active conflict")

    @staticmethod
    def _validate_terminal_frozen_sets(
        *,
        request: FullBoxExchangeRequest,
        occupancies: list[Any],
        material_mounts: list[Any],
        source_items: list[Any],
    ) -> None:
        try:
            requested = {
                (
                    int(item.occupancy_id),
                    item.pkg_id,
                    item.material_code,
                    FullBoxExchangeService._decimal_quantity(item.quantity),
                )
                for item in request.occupancies
            }
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("frozen occupancy identity is invalid") from exc
        occupancy_ids = {occupancy.id for occupancy in occupancies}
        if occupancy_ids != {occupancy_id for occupancy_id, _pkg_id, _material_code, _quantity in requested}:
            raise ValueError("frozen occupancy set drifted")
        actual_materials = set()
        for mount in material_mounts:
            if (
                mount.ended_at is not None
                or mount.pkg_code is None
                or mount.material_code is None
                or mount.qty_snapshot is None
            ):
                raise ValueError("terminal material identity/quantity is missing")
            actual_materials.add(
                (
                    mount.bin_cell_occupancy_id,
                    mount.pkg_code,
                    mount.material_code,
                    FullBoxExchangeService._decimal_quantity(mount.qty_snapshot),
                )
            )
        if actual_materials != requested:
            raise ValueError("frozen material set drifted")
        FullBoxExchangeService._validate_material_source_multiset(
            material_mounts=material_mounts,
            source_items=source_items,
            error_message="frozen source item multiset drifted",
        )

    @staticmethod
    def _validate_terminal_owners(
        *,
        demand: SmtInboundHandoffDemand,
        request: FullBoxExchangeRequest,
        occupancies: list[Any],
        owners: list[Any],
    ) -> None:
        expected = {
            ("RACK", request.rack_id),
            ("RACK_FACE", f"{request.rack_id}:{request.rack_face}"),
            ("BIN", request.full_box_id),
            *(("OCCUPANCY", str(occupancy.id)) for occupancy in occupancies),
        }
        if {(owner.object_type, owner.object_key) for owner in owners} != expected:
            raise ValueError("full box exchange owner set drifted")
        if any(
            owner.owner_type != "FULL_BOX_EXCHANGE"
            or owner.lifecycle_state != "ACTIVE"
            or owner.owner_intent_id != demand.active_full_box_exchange_intent_id
            for owner in owners
        ):
            raise ValueError("full box exchange owner identity drifted")

    @staticmethod
    def _validate_execution_context(ctx: dict[str, Any]) -> tuple[AsyncSession, int, str]:
        if not isinstance(ctx, dict) or ctx.get("db") is None:
            raise ValueError("full box exchange requires existing db execution context")
        workline = ctx.get("workline")
        workline_id = getattr(workline, "id", None)
        workline_code = getattr(workline, "line_code", None)
        if not isinstance(workline_id, int) or workline_id <= 0 or not isinstance(workline_code, str):
            raise ValueError("full box exchange requires existing workline execution context")
        return ctx["db"], workline_id, workline_code

    @staticmethod
    def _validate_demand(
        demand: SmtInboundHandoffDemand,
        *,
        workline_id: int,
        workline_code: str,
    ) -> None:
        if demand.status not in {
            SmtInboundHandoffDemandStatus.CREATED,
            SmtInboundHandoffDemandStatus.EVALUATING,
        }:
            raise ValueError("handoff demand status does not allow full box exchange")
        if (
            demand.source_workline_id != workline_id
            or demand.source_workline_code != workline_code
            or not demand.rack_release_id
            or not isinstance(demand.trace_id, str)
            or not demand.trace_id.strip()
        ):
            raise ValueError("handoff demand workline/release/trace facts are invalid")
        if not demand.full_box_exchange_station_code or demand.full_box_exchange_rack_face not in {"A", "B"}:
            raise ValueError("full box exchange station/face stage gate is missing")

    @staticmethod
    def _validate_placement(demand: SmtInboundHandoffDemand, placement: Any) -> None:
        if placement is None:
            raise ValueError("active rack placement is missing")
        authoritative_locations = {
            value
            for value in (
                placement.position_code,
                placement.logic_location_code,
                placement.external_location_code,
                placement.location_code,
            )
            if value is not None
        }
        if (
            placement.placement_status != RackPlacementStatus.ARRIVED
            or placement.rack_kind != RackKind.SINGLE_LAYER
            or placement.workline_id != demand.source_workline_id
            or placement.workline_code != demand.source_workline_code
            or demand.full_box_exchange_station_code not in authoritative_locations
        ):
            raise ValueError("rack placement station/workline/ARRIVED facts are invalid")

    async def _validate_stage_fences(
        self,
        db: AsyncSession,
        *,
        demand: SmtInboundHandoffDemand,
        full_box_id: str,
        occupancies: list[Any],
        material_mounts: list[Any],
        source_items: list[Any],
        prefer_full_box_exchange: bool,
    ) -> None:
        if not occupancies:
            raise ValueError("full box occupancy facts are invalid")
        usage_cells = await self._load_usage_cells(
            db,
            bin_codes=(full_box_id,),
            locked_occupancies=occupancies,
        )
        usage_band = self._usage_band(usage_cells[full_box_id])
        if usage_band == "DIRECT_SORTING" or (
            usage_band == "PREFERRED_FULL_BOX_EXCHANGE" and not prefer_full_box_exchange
        ):
            raise ValueError("full box usage does not meet requested exchange threshold")
        if not material_mounts or any(
            mount.mount_status != BinMaterialMountStatus.OCCUPIED for mount in material_mounts
        ):
            raise ValueError("full box material facts are invalid")
        if not source_items or any(item.status != SmtInboundHandoffSourceItemStatus.READY for item in source_items):
            raise ValueError("full box source item facts are invalid")
        reservations = await self._repository.list_active_reservations_for_update(
            db,
            bin_code=full_box_id,
        )
        if reservations:
            raise ValueError("full box has an active reservation")
        unfinished = await self._command_repository.list_unfinished_for_workline_for_update(
            db,
            workline_id=int(demand.source_workline_id),
            trace_id=str(demand.trace_id),
        )
        if unfinished:
            raise ValueError("full box has a related unfinished command")

    @staticmethod
    def _freeze_occupancies(
        *,
        occupancies: list[Any],
        material_mounts: list[Any],
        source_items: list[Any],
    ) -> tuple[FrozenBinCellOccupancy, ...]:
        occupancy_by_id = {occupancy.id: occupancy for occupancy in occupancies}
        FullBoxExchangeService._validate_material_source_multiset(
            material_mounts=material_mounts,
            source_items=source_items,
            error_message="full box frozen material/source multiset differs",
        )
        frozen: list[FrozenBinCellOccupancy] = []
        for mount in material_mounts:
            occupancy = occupancy_by_id.get(mount.bin_cell_occupancy_id)
            if occupancy is None or mount.pkg_code is None or mount.material_code is None or mount.qty_snapshot is None:
                raise ValueError("full box material occupancy identity or quantity is missing")
            frozen.append(
                FrozenBinCellOccupancy(
                    occupancy_id=str(occupancy.id),
                    pkg_id=mount.pkg_code,
                    material_code=mount.material_code,
                    quantity=FullBoxExchangeService._decimal_quantity(mount.qty_snapshot),
                )
            )
        return tuple(frozen)

    @staticmethod
    def _usage_band(usage_cells: list[Mapping[str, Any]]) -> str:
        cells = [
            {
                "status": getattr(cell["occupancy_status"], "value", cell["occupancy_status"]),
                "used_depth_mm": cell["used_depth_mm"],
                "capacity_depth_mm": cell["capacity_depth_mm"],
            }
            for cell in usage_cells
        ]
        usage = SMT_USAGE_POLICY.resolve_rack_bin_usage(cells)
        if not usage.valid or usage.usage is None:
            raise ValueError("full box usage facts are invalid")
        return SMT_USAGE_POLICY.usage_band(usage.usage)

    async def _load_usage_cells(
        self,
        db: AsyncSession,
        *,
        bin_codes: tuple[str, ...],
        locked_occupancies: list[Any],
    ) -> dict[str, list[Mapping[str, Any]]]:
        if not bin_codes:
            if locked_occupancies:
                raise ValueError("full box usage occupancy/template mapping drifted")
            return {}
        expected_by_id: dict[int, str] = {}
        for occupancy in locked_occupancies:
            occupancy_id = getattr(occupancy, "id", None)
            bin_code = str(getattr(occupancy, "bin_code", ""))
            if (
                not isinstance(occupancy_id, int)
                or occupancy_id in expected_by_id
                or bin_code not in bin_codes
                or getattr(occupancy, "ended_at", None) is not None
            ):
                raise ValueError("full box usage locked occupancy facts are invalid")
            expected_by_id[occupancy_id] = bin_code
        rows = await self._repository.list_bin_usage_cells_for_update(
            db,
            bin_codes=bin_codes,
        )
        cells_by_bin: dict[str, list[Mapping[str, Any]]] = {bin_code: [] for bin_code in bin_codes}
        mapped_occupancy_ids: set[int] = set()
        template_keys: set[tuple[str, int]] = set()
        for row in rows:
            if row["capacity_depth_mm"] is None or not isinstance(row["bin_slot_index"], int):
                raise ValueError("full box usage capacity facts are invalid")
            bin_code = str(row["bin_code"])
            if bin_code not in cells_by_bin:
                raise ValueError("full box usage bin master facts drifted")
            template_key = (bin_code, row["bin_slot_index"])
            if template_key in template_keys:
                raise ValueError("full box usage occupancy/template mapping is not unique")
            template_keys.add(template_key)
            occupancy_id = row["occupancy_id"]
            if occupancy_id is not None:
                if (
                    not isinstance(occupancy_id, int)
                    or occupancy_id in mapped_occupancy_ids
                    or expected_by_id.get(occupancy_id) != bin_code
                ):
                    raise ValueError("full box usage occupancy/template mapping drifted")
                mapped_occupancy_ids.add(occupancy_id)
            cells_by_bin[bin_code].append(row)
        if any(not cells for cells in cells_by_bin.values()):
            raise ValueError("full box usage bin/slot master facts are missing")
        if mapped_occupancy_ids != set(expected_by_id):
            raise ValueError("full box usage occupancy/template mapping is incomplete")
        return cells_by_bin

    @classmethod
    def _has_remaining_exchange_candidate(
        cls,
        *,
        usage_cells_by_bin: Mapping[str, list[Mapping[str, Any]]],
        decision_status: str | None,
    ) -> bool:
        if decision_status not in _PREFERENCE_BY_DECISION:
            raise ValueError("full box exchange decision threshold is missing")
        for usage_cells in usage_cells_by_bin.values():
            usage_band = cls._usage_band(usage_cells)
            if usage_band == "REQUIRE_FULL_BOX_EXCHANGE":
                return True
            if (
                decision_status == _PREFERRED_EXCHANGE_REQUESTED_DECISION
                and usage_band == "PREFERRED_FULL_BOX_EXCHANGE"
            ):
                return True
        return False

    @staticmethod
    def _resolve_preference(
        *,
        demand: SmtInboundHandoffDemand,
        requested_preference: bool | None,
    ) -> bool:
        frozen_preference = _PREFERENCE_BY_DECISION.get(demand.decision_status)
        if frozen_preference is None:
            if demand.active_full_box_exchange_intent_id is not None:
                raise ValueError("active full box exchange threshold is missing from parent")
            return requested_preference is True
        if requested_preference is not None and requested_preference is not frozen_preference:
            raise ValueError("requested full box exchange threshold conflicts with frozen parent decision")
        return frozen_preference

    @staticmethod
    def _validate_material_source_multiset(
        *,
        material_mounts: list[Any],
        source_items: list[Any],
        error_message: str,
    ) -> None:
        material_identities = [
            (mount.pkg_code, mount.material_identity_key)
            for mount in material_mounts
            if mount.pkg_code is not None and mount.material_identity_key
        ]
        source_identities = [
            (item.pkg_code, item.material_identity_key)
            for item in source_items
            if item.pkg_code is not None and item.material_identity_key
        ]
        material_counts = Counter(material_identities)
        source_counts = Counter(source_identities)
        if (
            len(material_identities) != len(material_mounts)
            or len(source_identities) != len(source_items)
            or material_counts != source_counts
            or any(count != 1 for count in material_counts.values())
            or any(count != 1 for count in source_counts.values())
        ):
            raise ValueError(error_message)

    @staticmethod
    def _decimal_quantity(value: Any) -> Decimal:
        if value is None or isinstance(value, bool):
            raise ValueError("material quantity is missing")
        try:
            quantity = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("material quantity is invalid") from exc
        if not quantity.is_finite() or quantity < 0:
            raise ValueError("material quantity is invalid")
        return quantity.normalize()


full_box_exchange_service = FullBoxExchangeService()


__all__ = [
    "FullBoxExchangeClaim",
    "FullBoxExchangeReservation",
    "FullBoxExchangeService",
    "full_box_exchange_service",
]
