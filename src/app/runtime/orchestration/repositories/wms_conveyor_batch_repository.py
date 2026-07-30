"""E12 入口位置与五层架候选的单聚合 Repository。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select

from src.app.resource.models import (
    Bin,
    BinCellOccupancy,
    BinSlotTemplate,
    BinType,
    Rack,
    RackBinMount,
    RackBinMountStatus,
    RackKind,
    RackPlacement,
    RackPlacementStatus,
    RackSlotKind,
    RackSlotTemplate,
    RackType,
    ResourceMasterStatus,
)
from src.app.runtime.orchestration.bin_route_instance import BinRouteInstance
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.models.bin_cell_reservation import WorklineBinCellReservation
from src.app.runtime.orchestration.models.rack_position import (
    WorklineRackPosition,
    WorklineRackPositionRole,
)
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.wms_conveyor_batch_member import WmsConveyorBatchMember
from src.app.sys.models.outbox import SystemOutbox

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.services.wms_conveyor_batch_service import WmsConveyorBatchCandidate


@dataclass(frozen=True, slots=True)
class WmsConveyorSourceRow:
    """已锁定的 active mount + rack-slot 排序事实。"""

    bin_code: str
    bin_type_code: str
    rack_code: str
    rack_slot_code: str
    slot_side: str
    layer_no: int
    position_no: int
    allowed_bin_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WmsConveyorAvailabilityFacts:
    """一次批量读取的料箱格位与互斥投影。"""

    slot_templates: tuple[BinSlotTemplate, ...]
    occupancies: tuple[BinCellOccupancy, ...]
    reservations: tuple[WorklineBinCellReservation, ...]
    routed_bin_codes: frozenset[str]
    queued_bin_codes: frozenset[str]
    owned_bin_codes: frozenset[str]


@dataclass(frozen=True, slots=True)
class WmsConveyorPreparedBatchRows:
    """同一 RuntimeIntent 根下按 sequence 锁定的 E12 投影。"""

    intent: RuntimeIntentLog
    members: tuple[WmsConveyorBatchMember, ...]
    routes: tuple[BinRouteInstance, ...]


class WmsConveyorBatchRepository:
    """集中持有 E12 的 PostgreSQL 锁序与批量候选读取。"""

    async def lock_entry_queue(self, db: AsyncSession, *, workline_id: int, queue_code: str) -> None:
        """以 WorkLine + entry queue advisory xact lock 序列化所有入口变更。"""

        lock_key = f"wms-conveyor-entry:{workline_id}:{queue_code}"
        await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))

    async def lock_active_member_positions(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        queue_code: str,
    ) -> frozenset[int]:
        rows = (
            await db.execute(
                select(WmsConveyorBatchMember.reserved_queue_position)
                .where(
                    WmsConveyorBatchMember.workline_id == workline_id,
                    WmsConveyorBatchMember.queue_code == queue_code,
                    WmsConveyorBatchMember.direction == "INBOUND",
                    or_(
                        WmsConveyorBatchMember.member_state.in_(("CANDIDATE", "ACCEPTED")),
                        and_(
                            WmsConveyorBatchMember.member_state == "TERMINAL",
                            WmsConveyorBatchMember.terminal_outcome == "UNKNOWN",
                            WmsConveyorBatchMember.reservation_released_at_ms.is_(None),
                        ),
                    ),
                )
                .with_for_update()
            )
        ).scalars()
        return frozenset(int(value) for value in rows if value is not None)

    async def lock_active_entry_membership_positions(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        queue_code: str,
    ) -> frozenset[int]:
        rows = (
            await db.execute(
                select(ConveyorQueueMembership.queue_position)
                .where(
                    ConveyorQueueMembership.workline_id == workline_id,
                    ConveyorQueueMembership.queue_code == queue_code,
                    ConveyorQueueMembership.queue_role == "ENTRY",
                    ConveyorQueueMembership.membership_status.in_(("ACTIVE", "RECONCILING")),
                )
                .with_for_update()
            )
        ).scalars()
        return frozenset(int(value) for value in rows if value is not None)

    async def lock_prepared_batch(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
    ) -> WmsConveyorPreparedBatchRows | None:
        """锁定唯一 Intent root 及其 E12 member/route，不复制 batch root。"""

        intent = await db.scalar(
            select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == dispatch_key).with_for_update()
        )
        if intent is None or intent.id is None:
            return None
        members = tuple(
            (
                await db.execute(
                    select(WmsConveyorBatchMember)
                    .where(WmsConveyorBatchMember.runtime_intent_log_id == intent.id)
                    .order_by(WmsConveyorBatchMember.sequence_no)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        route_ids = tuple(member.route_instance_id for member in members)
        routes = (
            tuple(
                (
                    await db.execute(
                        select(BinRouteInstance)
                        .where(BinRouteInstance.route_instance_id.in_(route_ids))
                        .order_by(BinRouteInstance.route_instance_id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if route_ids
            else ()
        )
        return WmsConveyorPreparedBatchRows(intent=intent, members=members, routes=routes)

    async def resolve_prepared_batch_workline_id(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
    ) -> int | None:
        """在 advisory lock 前只解析稳定 WorkLine identity，不读取可变批次状态。"""

        return await db.scalar(
            select(WmsConveyorBatchMember.workline_id)
            .join(
                RuntimeIntentLog,
                RuntimeIntentLog.id == WmsConveyorBatchMember.runtime_intent_log_id,
            )
            .where(RuntimeIntentLog.dispatch_key == dispatch_key)
            .order_by(WmsConveyorBatchMember.sequence_no)
            .limit(1)
        )

    async def lock_entry_memberships(
        self,
        db: AsyncSession,
        *,
        route_instance_ids: Sequence[str],
    ) -> tuple[ConveyorQueueMembership, ...]:
        if not route_instance_ids:
            return ()
        return tuple(
            (
                await db.execute(
                    select(ConveyorQueueMembership)
                    .where(
                        ConveyorQueueMembership.route_instance_id.in_(tuple(route_instance_ids)),
                        ConveyorQueueMembership.membership_status.in_(("ACTIVE", "RECONCILING")),
                    )
                    .order_by(ConveyorQueueMembership.route_instance_id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    async def get_open_reconciliation_case_for_update(
        self,
        db: AsyncSession,
        *,
        reconciliation_case_id: int,
    ) -> ReconciliationCase | None:
        return await db.scalar(
            select(ReconciliationCase)
            .where(
                ReconciliationCase.id == reconciliation_case_id,
                ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
            )
            .with_for_update()
        )

    async def resolve_open_reconciliation_case_id(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
    ) -> int | None:
        return await db.scalar(
            select(ReconciliationCase.id).where(
                ReconciliationCase.dispatch_key == dispatch_key,
                ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
            )
        )

    async def get_frozen_request_payload(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
    ) -> dict[str, object] | None:
        payload = await db.scalar(select(SystemOutbox.payload_json).where(SystemOutbox.dispatch_key == dispatch_key))
        return dict(payload) if isinstance(payload, dict) else None

    async def lock_first_target_placement(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
    ) -> tuple[RackPlacement, Rack, RackType] | None:
        row = (
            await db.execute(
                select(RackPlacement, Rack, RackType)
                .join(Rack, Rack.rack_code == RackPlacement.rack_code)
                .join(RackType, RackType.rack_type_code == Rack.rack_type_code)
                .where(
                    RackPlacement.workline_id == workline_id,
                    RackPlacement.position_code == "TARGET_STATION",
                    RackPlacement.rack_kind == RackKind.FIVE_LAYER,
                    RackPlacement.placement_status == RackPlacementStatus.ARRIVED,
                    RackPlacement.ended_at.is_(None),
                    Rack.status == ResourceMasterStatus.ACTIVE,
                    RackType.rack_kind == RackKind.FIVE_LAYER,
                    RackType.active.is_(True),
                )
                .order_by(RackPlacement.rack_code)
                .limit(1)
                .with_for_update(of=[RackPlacement, Rack])
            )
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1], row[2]

    async def lock_target_position(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
    ) -> WorklineRackPosition | None:
        return await db.scalar(
            select(WorklineRackPosition)
            .where(
                WorklineRackPosition.workline_id == workline_id,
                WorklineRackPosition.workline_code == workline_code,
                WorklineRackPosition.position_code == "TARGET_STATION",
                WorklineRackPosition.position_role == WorklineRackPositionRole.SMT_SORTER_STATION,
                WorklineRackPosition.allowed_rack_kind == RackKind.FIVE_LAYER,
                WorklineRackPosition.enabled.is_(True),
            )
            .with_for_update()
        )

    async def lock_source_rows(
        self,
        db: AsyncSession,
        *,
        rack: Rack,
    ) -> tuple[WmsConveyorSourceRow, ...]:
        rows = (
            await db.execute(
                select(RackBinMount, RackSlotTemplate, Bin)
                .join(
                    RackSlotTemplate,
                    and_(
                        RackSlotTemplate.rack_type_code == rack.rack_type_code,
                        RackSlotTemplate.slot_code == RackBinMount.rack_slot_code,
                    ),
                )
                .join(Bin, Bin.bin_code == RackBinMount.bin_code)
                .join(BinType, BinType.bin_type_code == Bin.bin_type_code)
                .where(
                    RackBinMount.rack_code == rack.rack_code,
                    RackBinMount.mount_status == RackBinMountStatus.MOUNTED,
                    RackBinMount.ended_at.is_(None),
                    RackSlotTemplate.active.is_(True),
                    RackSlotTemplate.slot_kind == RackSlotKind.BIN_SLOT,
                    Bin.status == ResourceMasterStatus.ACTIVE,
                    BinType.active.is_(True),
                )
                .order_by(
                    RackSlotTemplate.side,
                    RackSlotTemplate.layer_no,
                    RackSlotTemplate.position_no,
                    RackSlotTemplate.slot_code,
                    RackBinMount.bin_code,
                )
                .with_for_update(of=[RackBinMount, Bin])
            )
        ).all()
        return tuple(
            WmsConveyorSourceRow(
                bin_code=mount.bin_code,
                bin_type_code=bin_master.bin_type_code,
                rack_code=mount.rack_code,
                rack_slot_code=mount.rack_slot_code,
                slot_side=getattr(slot.side, "value", str(slot.side)),
                layer_no=slot.layer_no,
                position_no=slot.position_no,
                allowed_bin_types=tuple(slot.allowed_bin_types or ()),
            )
            for mount, slot, bin_master in rows
        )

    async def lock_frozen_source_rows(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        bin_codes: Sequence[str],
    ) -> tuple[WmsConveyorSourceRow, ...]:
        """只读取 request 已冻结的成员，不在 preparation 阶段重新选择候选。"""

        if not bin_codes:
            return ()
        rows = (
            await db.execute(
                select(RackBinMount, RackSlotTemplate, Bin)
                .join(Rack, Rack.rack_code == RackBinMount.rack_code)
                .join(
                    RackSlotTemplate,
                    and_(
                        RackSlotTemplate.rack_type_code == Rack.rack_type_code,
                        RackSlotTemplate.slot_code == RackBinMount.rack_slot_code,
                    ),
                )
                .join(Bin, Bin.bin_code == RackBinMount.bin_code)
                .join(BinType, BinType.bin_type_code == Bin.bin_type_code)
                .where(
                    RackBinMount.rack_code == rack_code,
                    RackBinMount.bin_code.in_(tuple(bin_codes)),
                    RackBinMount.mount_status == RackBinMountStatus.MOUNTED,
                    RackBinMount.ended_at.is_(None),
                    Rack.status == ResourceMasterStatus.ACTIVE,
                    RackSlotTemplate.active.is_(True),
                    RackSlotTemplate.slot_kind == RackSlotKind.BIN_SLOT,
                    Bin.status == ResourceMasterStatus.ACTIVE,
                    BinType.active.is_(True),
                )
                .with_for_update(of=[RackBinMount, Rack, Bin])
            )
        ).all()
        return tuple(
            WmsConveyorSourceRow(
                bin_code=mount.bin_code,
                bin_type_code=bin_master.bin_type_code,
                rack_code=mount.rack_code,
                rack_slot_code=mount.rack_slot_code,
                slot_side=getattr(slot.side, "value", str(slot.side)),
                layer_no=slot.layer_no,
                position_no=slot.position_no,
                allowed_bin_types=tuple(slot.allowed_bin_types or ()),
            )
            for mount, slot, bin_master in rows
        )

    async def load_availability_facts(
        self,
        db: AsyncSession,
        *,
        source_rows: Sequence[WmsConveyorSourceRow],
    ) -> WmsConveyorAvailabilityFacts:
        if not source_rows:
            return WmsConveyorAvailabilityFacts(
                slot_templates=(),
                occupancies=(),
                reservations=(),
                routed_bin_codes=frozenset(),
                queued_bin_codes=frozenset(),
                owned_bin_codes=frozenset(),
            )
        bin_codes = tuple(row.bin_code for row in source_rows)
        bin_type_codes = tuple({row.bin_type_code for row in source_rows})
        slot_templates = tuple(
            (
                await db.execute(
                    select(BinSlotTemplate)
                    .where(
                        BinSlotTemplate.bin_type_code.in_(bin_type_codes),
                        BinSlotTemplate.active.is_(True),
                    )
                    .order_by(BinSlotTemplate.bin_type_code, BinSlotTemplate.bin_slot_index)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        occupancies = tuple(
            (
                await db.execute(
                    select(BinCellOccupancy)
                    .where(
                        BinCellOccupancy.bin_code.in_(bin_codes),
                        BinCellOccupancy.ended_at.is_(None),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        reservations = tuple(
            (
                await db.execute(
                    select(WorklineBinCellReservation)
                    .where(
                        WorklineBinCellReservation.bin_code.in_(bin_codes),
                        WorklineBinCellReservation.reservation_status.in_(("PLANNED", "RECONCILING")),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        routed_bin_codes = frozenset(
            (
                await db.execute(
                    select(BinRouteInstance.bin_code)
                    .where(
                        BinRouteInstance.bin_code.in_(bin_codes),
                        BinRouteInstance.lifecycle_state.in_(("ACTIVE", "RECONCILING")),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        queued_bin_codes = frozenset(
            (
                await db.execute(
                    select(ConveyorQueueMembership.bin_code)
                    .where(
                        ConveyorQueueMembership.bin_code.in_(bin_codes),
                        ConveyorQueueMembership.membership_status.in_(("ACTIVE", "RECONCILING")),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        owned_bin_codes = frozenset(
            (
                await db.execute(
                    select(MaterialFlowOwner.object_key)
                    .where(
                        MaterialFlowOwner.object_type == "BIN",
                        MaterialFlowOwner.object_key.in_(bin_codes),
                        MaterialFlowOwner.lifecycle_state.in_(("ACTIVE", "RECONCILING")),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        return WmsConveyorAvailabilityFacts(
            slot_templates=slot_templates,
            occupancies=occupancies,
            reservations=reservations,
            routed_bin_codes=routed_bin_codes,
            queued_bin_codes=queued_bin_codes,
            owned_bin_codes=owned_bin_codes,
        )

    async def add_prepared_batch(
        self,
        db: AsyncSession,
        *,
        intent_id: int,
        workline_id: int,
        queue_code: str,
        candidates: Sequence[WmsConveyorBatchCandidate],
        staged_at_ms: int,
    ) -> None:
        """同一次 flush 写入 route 与 member；事务所有权仍属于调用方。"""

        routes = [
            BinRouteInstance(
                route_instance_id=candidate.route_instance_id,
                bin_code=candidate.bin_code,
                workline_id=workline_id,
                created_by_e12_intent_id=intent_id,
                # submit/preparation 只冻结搬运请求，不构成 CTU 已取箱的物理证据。
                current_node="FIVE_RACK",
                route_version=1,
                lifecycle_state="ACTIVE",
                current_rack_code=candidate.source_rack_code,
                current_slot_code=candidate.source_slot_code,
                last_transition_source="E12_RESERVATION",
                last_transition_source_event_id=f"wms-e12-prepare:{intent_id}:{candidate.route_instance_id}",
            )
            for candidate in candidates
        ]
        members = [
            WmsConveyorBatchMember(
                runtime_intent_log_id=intent_id,
                route_instance_id=candidate.route_instance_id,
                workline_id=workline_id,
                queue_code=queue_code,
                direction="INBOUND",
                sequence_no=sequence_no,
                bin_code=candidate.bin_code,
                reserved_queue_position=candidate.reserved_queue_position,
                member_state="CANDIDATE",
                staged_at_ms=staged_at_ms,
            )
            for sequence_no, candidate in enumerate(candidates, start=1)
        ]
        db.add_all([*routes, *members])
        await db.flush()


wms_conveyor_batch_repository = WmsConveyorBatchRepository()

__all__ = [
    "WmsConveyorAvailabilityFacts",
    "WmsConveyorBatchRepository",
    "WmsConveyorPreparedBatchRows",
    "WmsConveyorSourceRow",
    "wms_conveyor_batch_repository",
]
