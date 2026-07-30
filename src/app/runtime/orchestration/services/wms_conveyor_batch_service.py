"""E12 输送线入口批次的冻结 identity 与事务服务。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.repositories.wms_conveyor_batch_repository import (
    WmsConveyorAvailabilityFacts,
    WmsConveyorBatchRepository,
    WmsConveyorSourceRow,
    wms_conveyor_batch_repository,
)
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import SmtSortingInboundConfig
from src.app.wms_integration.ports.fulfillment_operations import (
    MOVE_BINS_TO_CONVEYOR_ENTRY,
    ConveyorBatchItem,
    MoveBinsToConveyorEntryRequest,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.wms_integration.operation_contract import WmsOperationDefinition


@dataclass(frozen=True, slots=True)
class WmsConveyorBatchCandidate:
    """submit 前冻结的单个 E12 成员。"""

    route_instance_id: str
    bin_code: str
    source_rack_code: str
    source_slot_code: str
    reserved_queue_position: int


@dataclass(frozen=True, slots=True)
class WmsConveyorBatchIdentity:
    """RuntimeIntent batch root 与外部派发共用的确定性 identity。"""

    batch_id: str
    dispatch_key: str


@dataclass(frozen=True, slots=True)
class WmsConveyorBatchClaim:
    """reserve→Intent claim→preparation hook 同事务传播的冻结集合。"""

    workline_id: int
    binding_id: int
    binding_version: int
    plugin_config_hash: str
    queue_code: str
    entry_capacity: int
    capacity_snapshot_version: str
    source_rack_code: str
    batch_id: str
    candidates: tuple[WmsConveyorBatchCandidate, ...]


@dataclass(frozen=True, slots=True)
class WmsConveyorBatchReservation:
    """无候选时不携带 operation/request，调用方不得进入 Intent claim。"""

    created: bool
    claim: WmsConveyorBatchClaim | None
    operation: WmsOperationDefinition | None
    request: MoveBinsToConveyorEntryRequest | None


def _new_batch_token() -> str:
    return uuid4().hex


def _now_ms() -> int:
    return int(timezone.now_utc().timestamp() * 1000)


class WmsConveyorBatchService:
    """以 RuntimeIntentLog 为唯一 batch root 的 E12 reserve/preparation 服务。"""

    def __init__(
        self,
        *,
        repository: WmsConveyorBatchRepository = wms_conveyor_batch_repository,
        id_factory: Callable[[], str] = _new_batch_token,
        now_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._repository = repository
        self._id_factory = id_factory
        self._now_ms = now_ms

    @staticmethod
    def capacity_snapshot_version(
        *,
        binding_id: int,
        binding_version: int,
        plugin_config_hash: str,
        entry_capacity: int,
    ) -> str:
        """只对 pinned binding identity 与入口容量生成 canonical hash。"""

        return sha256_digest(
            {
                "binding_id": binding_id,
                "binding_version": binding_version,
                "plugin_config_hash": plugin_config_hash,
                "conveyor_entry_capacity": entry_capacity,
            }
        )

    @staticmethod
    def batch_identity(
        *,
        workline_id: int,
        queue_code: str,
        batch_token: str,
    ) -> WmsConveyorBatchIdentity:
        """按单次 reserve winner token 生成可重放、但不跨物理循环复用的 identity。"""

        digest = sha256_digest(
            {
                "workline_id": workline_id,
                "queue_code": queue_code,
                "batch_token": batch_token,
            }
        )
        identity = f"wms-e12:{workline_id}:{digest}"
        return WmsConveyorBatchIdentity(batch_id=identity, dispatch_key=identity)

    @staticmethod
    def route_instance_id(batch_id: str, *, sequence_no: int) -> str:
        """同一 batch 重放保持 route identity，新 batch 必然创建新 route。"""

        if sequence_no <= 0:
            raise ValueError("E12 route sequence_no must be positive")
        route_instance_id = f"{batch_id}:route:{sequence_no}"
        if len(route_instance_id) > 160:
            raise ValueError("E12 route_instance_id exceeds storage limit")
        return route_instance_id

    async def reserve_batch(self, ctx: dict[str, Any]) -> WmsConveyorBatchReservation:
        """锁定 entry 与单个五层架候选，winner 才冻结 E12 request。"""

        db, workline, binding_id, binding_version, plugin_config_hash, config = self._validate_context(ctx)
        workline_id = workline.id
        queue = config.conveyor_entry_queue
        await self._repository.lock_entry_queue(db, workline_id=workline_id, queue_code=queue.code)
        member_positions = await self._repository.lock_active_member_positions(
            db,
            workline_id=workline_id,
            queue_code=queue.code,
        )
        membership_positions = await self._repository.lock_active_entry_membership_positions(
            db,
            workline_id=workline_id,
            queue_code=queue.code,
        )
        occupied_positions = member_positions | membership_positions
        free_positions = tuple(
            position for position in range(1, queue.capacity + 1) if position not in occupied_positions
        )
        if not free_positions:
            return self._empty_reservation()

        target_position = await self._repository.lock_target_position(
            db,
            workline_id=workline_id,
            workline_code=workline.line_code,
        )
        if target_position is None:
            return self._empty_reservation()
        placement_row = await self._repository.lock_first_target_placement(db, workline_id=workline_id)
        if placement_row is None:
            return self._empty_reservation()
        _placement, rack, _rack_type = placement_row
        source_rows = await self._repository.lock_source_rows(db, rack=rack)
        facts = await self._repository.load_availability_facts(db, source_rows=source_rows)
        available_rows = self._available_source_rows(source_rows, facts=facts)
        batch_size = min(len(free_positions), config.ctu_basket_capacity, len(available_rows))
        if batch_size <= 0:
            return self._empty_reservation()

        identity = self.batch_identity(
            workline_id=workline_id,
            queue_code=queue.code,
            batch_token=self._id_factory(),
        )
        candidates = tuple(
            WmsConveyorBatchCandidate(
                route_instance_id=self.route_instance_id(identity.batch_id, sequence_no=sequence_no),
                bin_code=row.bin_code,
                source_rack_code=row.rack_code,
                source_slot_code=row.rack_slot_code,
                reserved_queue_position=queue_position,
            )
            for sequence_no, (row, queue_position) in enumerate(
                zip(available_rows[:batch_size], free_positions[:batch_size], strict=True),
                start=1,
            )
        )
        capacity_snapshot = self.capacity_snapshot_version(
            binding_id=binding_id,
            binding_version=binding_version,
            plugin_config_hash=plugin_config_hash,
            entry_capacity=queue.capacity,
        )
        request = MoveBinsToConveyorEntryRequest(
            dispatch_key=identity.dispatch_key,
            batch_id=identity.batch_id,
            direction="TO_CONVEYOR_ENTRY",
            source_station_code="TARGET_STATION",
            destination_station_code=queue.code,
            capacity_snapshot_version=capacity_snapshot,
            items=tuple(
                ConveyorBatchItem(
                    sequence_no=sequence_no,
                    route_instance_id=candidate.route_instance_id,
                    bin_id=candidate.bin_code,
                    source_rack_id=candidate.source_rack_code,
                    source_slot_id=candidate.source_slot_code,
                    reserved_queue_position=candidate.reserved_queue_position,
                )
                for sequence_no, candidate in enumerate(candidates, start=1)
            ),
        )
        claim = WmsConveyorBatchClaim(
            workline_id=workline_id,
            binding_id=binding_id,
            binding_version=binding_version,
            plugin_config_hash=plugin_config_hash,
            queue_code=queue.code,
            entry_capacity=queue.capacity,
            capacity_snapshot_version=capacity_snapshot,
            source_rack_code=rack.rack_code,
            batch_id=identity.batch_id,
            candidates=candidates,
        )
        ctx["wms_conveyor_batch_claim"] = claim
        return WmsConveyorBatchReservation(
            created=True,
            claim=claim,
            operation=MOVE_BINS_TO_CONVEYOR_ENTRY,
            request=request,
        )

    async def prepare_effect(
        self,
        db: AsyncSession,
        *,
        claim: WmsConveyorBatchClaim,
        request: MoveBinsToConveyorEntryRequest,
        intent_id: int,
    ) -> None:
        """在 Outbox 前校验冻结 request，并原子写 route/member 投影。"""

        self._validate_frozen_request(claim=claim, request=request)
        await self._repository.lock_entry_queue(
            db,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
        )
        member_positions = await self._repository.lock_active_member_positions(
            db,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
        )
        membership_positions = await self._repository.lock_active_entry_membership_positions(
            db,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
        )
        frozen_positions = {candidate.reserved_queue_position for candidate in claim.candidates}
        if frozen_positions & (member_positions | membership_positions):
            raise ValueError("E12 frozen entry positions are no longer available")
        if any(position < 1 or position > claim.entry_capacity for position in frozen_positions):
            raise ValueError("E12 frozen entry position exceeds pinned capacity")

        frozen_rows = await self._repository.lock_frozen_source_rows(
            db,
            rack_code=claim.source_rack_code,
            bin_codes=tuple(candidate.bin_code for candidate in claim.candidates),
        )
        facts = await self._repository.load_availability_facts(db, source_rows=frozen_rows)
        available_by_bin = {row.bin_code: row for row in self._available_source_rows(frozen_rows, facts=facts)}
        for candidate in claim.candidates:
            row = available_by_bin.get(candidate.bin_code)
            if (
                row is None
                or row.rack_code != candidate.source_rack_code
                or row.rack_slot_code != candidate.source_slot_code
            ):
                raise ValueError("E12 frozen source candidate is no longer available")
        await self._repository.add_prepared_batch(
            db,
            intent_id=intent_id,
            workline_id=claim.workline_id,
            queue_code=claim.queue_code,
            candidates=claim.candidates,
            staged_at_ms=self._now_ms(),
        )

    @classmethod
    def _validate_frozen_request(
        cls,
        *,
        claim: WmsConveyorBatchClaim,
        request: MoveBinsToConveyorEntryRequest,
    ) -> None:
        expected_snapshot = cls.capacity_snapshot_version(
            binding_id=claim.binding_id,
            binding_version=claim.binding_version,
            plugin_config_hash=claim.plugin_config_hash,
            entry_capacity=claim.entry_capacity,
        )
        if (
            request.batch_id != claim.batch_id
            or request.dispatch_key != claim.batch_id
            or request.source_station_code != "TARGET_STATION"
            or request.destination_station_code != claim.queue_code
            or request.capacity_snapshot_version != claim.capacity_snapshot_version
            or request.capacity_snapshot_version != expected_snapshot
            or len(request.items) != len(claim.candidates)
        ):
            raise ValueError("E12 frozen batch request drifted")
        expected_items = tuple(
            (
                sequence_no,
                candidate.route_instance_id,
                candidate.bin_code,
                candidate.source_rack_code,
                candidate.source_slot_code,
                candidate.reserved_queue_position,
            )
            for sequence_no, candidate in enumerate(claim.candidates, start=1)
        )
        actual_items = tuple(
            (
                item.sequence_no,
                item.route_instance_id,
                item.bin_id,
                item.source_rack_id,
                item.source_slot_id,
                item.reserved_queue_position,
            )
            for item in request.items
        )
        if actual_items != expected_items:
            raise ValueError("E12 frozen batch members drifted")

    @staticmethod
    def _empty_reservation() -> WmsConveyorBatchReservation:
        return WmsConveyorBatchReservation(created=False, claim=None, operation=None, request=None)

    @staticmethod
    def _available_source_rows(
        source_rows: tuple[WmsConveyorSourceRow, ...],
        *,
        facts: WmsConveyorAvailabilityFacts,
    ) -> tuple[WmsConveyorSourceRow, ...]:
        templates_by_type: dict[str, list[Any]] = {}
        for template in facts.slot_templates:
            templates_by_type.setdefault(template.bin_type_code, []).append(template)
        occupancy_by_cell = {
            (occupancy.bin_code, str(occupancy.bin_cell_index)): occupancy for occupancy in facts.occupancies
        }
        reserved_cells = {(reservation.bin_code, str(reservation.bin_cell_index)) for reservation in facts.reservations}
        unavailable_bins = facts.routed_bin_codes | facts.queued_bin_codes | facts.owned_bin_codes
        available: list[WmsConveyorSourceRow] = []
        for row in source_rows:
            if row.bin_code in unavailable_bins:
                continue
            if row.allowed_bin_types and row.bin_type_code not in row.allowed_bin_types:
                continue
            has_available_cell = False
            for template in templates_by_type.get(row.bin_type_code, ()):
                cell_key = (row.bin_code, str(template.bin_slot_index))
                if cell_key in reserved_cells:
                    continue
                occupancy = occupancy_by_cell.get(cell_key)
                if occupancy is None:
                    has_available_cell = True
                    break
                remaining = occupancy.remaining_depth_mm
                occupancy_status = getattr(occupancy.occupancy_status, "value", occupancy.occupancy_status)
                if occupancy_status == "OCCUPIED" and remaining is not None and Decimal(remaining) > Decimal("0"):
                    has_available_cell = True
                    break
            if has_available_cell:
                available.append(row)
        return tuple(available)

    @staticmethod
    def _validate_context(
        ctx: dict[str, Any],
    ) -> tuple[AsyncSession, Any, int, int, str, SmtSortingInboundConfig]:
        db = ctx.get("db")
        session = ctx.get("session")
        workline = ctx.get("workline")
        work_item = ctx.get("work_item")
        binding = ctx.get("plugin_binding")
        inbox = ctx.get("inbox")
        if any(value is None for value in (db, session, workline, work_item, binding, inbox)):
            raise PermissionError("E12 reserve requires locked session/work-item/binding identity")
        workline_id = getattr(workline, "id", None)
        binding_id = getattr(binding, "id", None)
        binding_version = getattr(binding, "binding_version", None)
        plugin_config_hash = getattr(binding, "typed_config_hash", None)
        if (
            not isinstance(workline_id, int)
            or not isinstance(binding_id, int)
            or not isinstance(binding_version, int)
            or not isinstance(plugin_config_hash, str)
        ):
            raise PermissionError("E12 reserve execution identity is incomplete")
        binding_identity = (
            getattr(binding, "workline_id", None),
            getattr(binding, "plugin_key", None),
            getattr(binding, "contract_version", None),
        )
        if binding_identity != (workline_id, "smt_sorting_inbound", "smt_sorting_inbound.v1"):
            raise PermissionError("E12 reserve requires pinned smt_sorting_inbound@v1 binding")
        if (
            getattr(workline, "is_active", False) is not True
            or getattr(workline, "is_deleted", False) is True
            or getattr(workline, "deleted_at", None) is not None
        ):
            raise PermissionError("E12 reserve requires active non-deleted WorkLine")
        expected_pin = (
            "smt_sorting_inbound",
            binding_id,
            binding_version,
            plugin_config_hash,
            getattr(binding, "generated_index_digest", None),
        )
        session_pin = (
            getattr(session, "plugin_key", None),
            getattr(session, "plugin_binding_id", None),
            getattr(session, "plugin_binding_version", None),
            getattr(session, "plugin_config_hash", None),
            getattr(session, "plugin_index_digest", None),
        )
        work_item_pin = (
            getattr(work_item, "plugin_key", None),
            getattr(work_item, "plugin_binding_id", None),
            getattr(work_item, "plugin_binding_version", None),
            getattr(work_item, "plugin_config_hash", None),
            getattr(work_item, "plugin_index_digest", None),
        )
        if session_pin != expected_pin or work_item_pin != expected_pin:
            raise PermissionError("E12 reserve pinned binding identity drifted")
        workline_pin = (
            getattr(workline, "plugin_key", None),
            getattr(workline, "contract_version", None),
            getattr(workline, "active_plugin_binding_id", None),
            getattr(workline, "active_plugin_binding_version", None),
            getattr(workline, "active_plugin_config_hash", None),
            getattr(workline, "active_plugin_index_digest", None),
        )
        expected_workline_pin = (
            "smt_sorting_inbound",
            "smt_sorting_inbound.v1",
            binding_id,
            binding_version,
            plugin_config_hash,
            getattr(binding, "generated_index_digest", None),
        )
        if workline_pin != expected_workline_pin:
            raise PermissionError("E12 reserve WorkLine active binding pin drifted")
        if getattr(binding, "is_enabled", True) is not True or getattr(binding, "is_revoked", False) is True:
            raise PermissionError("E12 reserve binding is disabled or revoked")
        if getattr(session, "workline_id", None) != workline_id or getattr(inbox, "workline_id", None) != workline_id:
            raise PermissionError("E12 reserve workline identity drifted")
        config = SmtSortingInboundConfig.model_validate(getattr(binding, "typed_config_json", None))
        return db, workline, binding_id, binding_version, plugin_config_hash, config


wms_conveyor_batch_service = WmsConveyorBatchService()

__all__ = [
    "WmsConveyorBatchCandidate",
    "WmsConveyorBatchClaim",
    "WmsConveyorBatchIdentity",
    "WmsConveyorBatchReservation",
    "WmsConveyorBatchService",
    "wms_conveyor_batch_service",
]
