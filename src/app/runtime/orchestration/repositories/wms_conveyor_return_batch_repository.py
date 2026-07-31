"""E13 RETURN_QUEUE 候选窗口与 preparation claim 的单聚合 Repository。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - dataclass 运行时需要字段类型
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select, tuple_

from src.app.resource.models import (
    Rack,
    RackBinMount,
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

    from src.app.runtime.orchestration.services.wms_conveyor_return_batch_service import (
        WmsConveyorReturnCandidate,
    )


@dataclass(frozen=True, slots=True)
class WmsConveyorReturnCandidateRow:
    """已锁定且仍位于 RETURN_QUEUE 的 FIFO 候选事实。"""

    membership_id: int
    route_instance_id: str
    bin_code: str
    scan3_enqueued_at: datetime
    queue_position: int


@dataclass(frozen=True, slots=True)
class WmsConveyorReturnPreparedRows:
    """同一 RuntimeIntent 根下锁定的 E13 request/member/route/membership。"""

    intent: RuntimeIntentLog
    outbox: SystemOutbox
    members: tuple[WmsConveyorBatchMember, ...]
    routes: tuple[BinRouteInstance, ...]
    memberships: tuple[ConveyorQueueMembership, ...]


@dataclass(frozen=True, slots=True)
class WmsConveyorReturnTargetRow:
    """一个已锁定且满足 E13 work-face 资格的目标 rack-slot。"""

    placement: RackPlacement
    rack: Rack
    rack_type: RackType
    slot: RackSlotTemplate


@dataclass(frozen=True, slots=True)
class WmsConveyorReturnTerminalResources:
    """E13 终态一次批量锁定的 manifest、目标与 active mount 全集。"""

    target_position: WorklineRackPosition | None
    targets: tuple[WmsConveyorReturnTargetRow, ...]
    active_mounts: tuple[RackBinMount, ...]


class WmsConveyorReturnBatchRepository:
    """集中持有 E13 的 PostgreSQL FIFO 锁序与 preparation 写入。"""

    async def lock_fifo_candidates(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        queue_code: str,
        limit: int,
    ) -> tuple[WmsConveyorReturnCandidateRow, ...]:
        """按 SCAN3 稳定 FIFO 取有界窗口，并跳过其他事务已锁候选。"""

        rows = (
            await db.execute(
                select(ConveyorQueueMembership, BinRouteInstance)
                .join(
                    BinRouteInstance,
                    BinRouteInstance.route_instance_id == ConveyorQueueMembership.route_instance_id,
                )
                .where(
                    ConveyorQueueMembership.workline_id == workline_id,
                    ConveyorQueueMembership.queue_code == queue_code,
                    ConveyorQueueMembership.queue_role == "RETURN_QUEUE",
                    ConveyorQueueMembership.membership_status == "ACTIVE",
                    ConveyorQueueMembership.e13_claim_intent_id.is_(None),
                    BinRouteInstance.workline_id == workline_id,
                    BinRouteInstance.bin_code == ConveyorQueueMembership.bin_code,
                    BinRouteInstance.current_node == "RETURN_QUEUE",
                    BinRouteInstance.lifecycle_state == "ACTIVE",
                )
                .order_by(
                    ConveyorQueueMembership.scan3_enqueued_at,
                    ConveyorQueueMembership.queue_position,
                    ConveyorQueueMembership.bin_code,
                )
                .limit(limit)
                .with_for_update(
                    of=[ConveyorQueueMembership, BinRouteInstance],
                    skip_locked=True,
                )
            )
        ).all()
        return tuple(
            WmsConveyorReturnCandidateRow(
                membership_id=membership.id,
                route_instance_id=route.route_instance_id,
                bin_code=route.bin_code,
                scan3_enqueued_at=membership.scan3_enqueued_at,
                queue_position=membership.queue_position,
            )
            for membership, route in rows
            if membership.id is not None
            and membership.scan3_enqueued_at is not None
            and membership.queue_position is not None
        )

    async def claim_prepared_batch(
        self,
        db: AsyncSession,
        *,
        intent_id: int,
        workline_id: int,
        queue_code: str,
        claim_token: str,
        claim_until: datetime,
        candidates: Sequence[WmsConveyorReturnCandidate],
        staged_at_ms: int,
    ) -> None:
        """重锁冻结候选，写 membership lease 与 RETURN member；事务由调用方提交。"""

        membership_ids = tuple(candidate.membership_id for candidate in candidates)
        rows = (
            await db.execute(
                select(ConveyorQueueMembership, BinRouteInstance)
                .join(
                    BinRouteInstance,
                    BinRouteInstance.route_instance_id == ConveyorQueueMembership.route_instance_id,
                )
                .where(ConveyorQueueMembership.id.in_(membership_ids))
                .order_by(ConveyorQueueMembership.id)
                .with_for_update(of=[ConveyorQueueMembership, BinRouteInstance])
            )
        ).all()
        locked_by_id = {membership.id: (membership, route) for membership, route in rows}
        if len(locked_by_id) != len(candidates):
            raise ValueError("E13 frozen RETURN_QUEUE candidate is missing")

        members: list[WmsConveyorBatchMember] = []
        for sequence_no, candidate in enumerate(candidates, start=1):
            membership, route = locked_by_id[candidate.membership_id]
            if (
                membership.workline_id != workline_id
                or membership.queue_code != queue_code
                or membership.queue_role != "RETURN_QUEUE"
                or membership.membership_status != "ACTIVE"
                or membership.e13_claim_intent_id is not None
                or membership.e13_claim_token is not None
                or membership.e13_claim_until is not None
                or membership.route_instance_id != candidate.route_instance_id
                or membership.bin_code != candidate.bin_code
                or membership.scan3_enqueued_at != candidate.scan3_enqueued_at
                or membership.queue_position != candidate.queue_position
                or route.workline_id != workline_id
                or route.bin_code != candidate.bin_code
                or route.current_node != "RETURN_QUEUE"
                or route.lifecycle_state != "ACTIVE"
            ):
                raise ValueError("E13 frozen RETURN_QUEUE candidate drifted before preparation")
            membership.e13_claim_intent_id = intent_id
            membership.e13_claim_token = claim_token
            membership.e13_claim_until = claim_until
            members.append(
                WmsConveyorBatchMember(
                    runtime_intent_log_id=intent_id,
                    route_instance_id=candidate.route_instance_id,
                    source_queue_membership_id=candidate.membership_id,
                    workline_id=workline_id,
                    queue_code=queue_code,
                    direction="RETURN",
                    sequence_no=sequence_no,
                    bin_code=candidate.bin_code,
                    reserved_queue_position=None,
                    member_state="CANDIDATE",
                    staged_at_ms=staged_at_ms,
                )
            )
        db.add_all(members)
        await db.flush()

    async def lock_prepared_batch(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
    ) -> WmsConveyorReturnPreparedRows | None:
        """按 root→member→membership→route 锁序读取 ACK/reject 权威事实。"""

        intent = await db.scalar(
            select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == dispatch_key).with_for_update()
        )
        outbox = await db.scalar(
            select(SystemOutbox).where(SystemOutbox.dispatch_key == dispatch_key).with_for_update()
        )
        if intent is None or intent.id is None or outbox is None:
            return None
        members = tuple(
            (
                await db.execute(
                    select(WmsConveyorBatchMember)
                    .where(
                        WmsConveyorBatchMember.runtime_intent_log_id == intent.id,
                        WmsConveyorBatchMember.direction == "RETURN",
                    )
                    .order_by(WmsConveyorBatchMember.sequence_no)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        route_ids = tuple(member.route_instance_id for member in members)
        memberships = (
            tuple(
                (
                    await db.execute(
                        select(ConveyorQueueMembership)
                        .where(ConveyorQueueMembership.route_instance_id.in_(route_ids))
                        .order_by(ConveyorQueueMembership.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if route_ids
            else ()
        )
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
        return WmsConveyorReturnPreparedRows(
            intent=intent,
            outbox=outbox,
            members=members,
            routes=routes,
            memberships=memberships,
        )

    async def lock_terminal_resources(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        accepted_bin_codes: Sequence[str],
        target_keys: Sequence[tuple[str, str]],
    ) -> WmsConveyorReturnTerminalResources:
        """按 manifest→目标资源→active mount 的稳定顺序锁定终态聚合。"""

        target_position = await db.scalar(
            select(WorklineRackPosition)
            .where(
                WorklineRackPosition.workline_id == workline_id,
                WorklineRackPosition.position_code == "TARGET_STATION",
                WorklineRackPosition.position_role == WorklineRackPositionRole.SMT_SORTER_STATION,
                WorklineRackPosition.allowed_rack_kind == RackKind.FIVE_LAYER,
                WorklineRackPosition.enabled.is_(True),
            )
            .order_by(WorklineRackPosition.id)
            .with_for_update()
        )
        target_pairs = tuple(sorted(set(target_keys)))
        targets: tuple[WmsConveyorReturnTargetRow, ...] = ()
        if target_pairs:
            rows = (
                await db.execute(
                    select(RackPlacement, Rack, RackType, RackSlotTemplate)
                    .join(Rack, Rack.rack_code == RackPlacement.rack_code)
                    .join(RackType, RackType.rack_type_code == Rack.rack_type_code)
                    .join(
                        RackSlotTemplate,
                        and_(
                            RackSlotTemplate.rack_type_code == RackType.rack_type_code,
                            tuple_(Rack.rack_code, RackSlotTemplate.slot_code).in_(target_pairs),
                        ),
                    )
                    .where(
                        RackPlacement.workline_id == workline_id,
                        RackPlacement.position_code == "TARGET_STATION",
                        RackPlacement.rack_kind == RackKind.FIVE_LAYER,
                        RackPlacement.placement_status == RackPlacementStatus.ARRIVED,
                        RackPlacement.ended_at.is_(None),
                        Rack.status == ResourceMasterStatus.ACTIVE,
                        RackType.rack_kind == RackKind.FIVE_LAYER,
                        RackType.active.is_(True),
                        RackSlotTemplate.slot_kind == RackSlotKind.BIN_SLOT,
                        RackSlotTemplate.active.is_(True),
                    )
                    .order_by(Rack.rack_code, RackSlotTemplate.slot_code)
                    .with_for_update(of=[RackPlacement, Rack, RackType, RackSlotTemplate])
                )
            ).all()
            targets = tuple(
                WmsConveyorReturnTargetRow(
                    placement=placement,
                    rack=rack,
                    rack_type=rack_type,
                    slot=slot,
                )
                for placement, rack, rack_type, slot in rows
            )

        mount_filter = RackBinMount.bin_code.in_(tuple(accepted_bin_codes))
        if target_pairs:
            mount_filter = or_(
                mount_filter,
                tuple_(RackBinMount.rack_code, RackBinMount.rack_slot_code).in_(target_pairs),
            )
        active_mounts = tuple(
            (
                await db.execute(
                    select(RackBinMount)
                    .where(
                        mount_filter,
                        RackBinMount.ended_at.is_(None),
                    )
                    .order_by(RackBinMount.rack_code, RackBinMount.rack_slot_code, RackBinMount.bin_code)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        return WmsConveyorReturnTerminalResources(
            target_position=target_position,
            targets=targets,
            active_mounts=active_mounts,
        )

    async def get_open_reconciliation_case_for_update(
        self,
        db: AsyncSession,
        *,
        reconciliation_case_id: int,
    ) -> ReconciliationCase | None:
        """锁定与 E13 root 绑定的 OPEN reconciliation case。"""

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
        """按 E13 dispatch 锁定当前 OPEN case 身份。"""

        return await db.scalar(
            select(ReconciliationCase.id)
            .where(
                ReconciliationCase.dispatch_key == dispatch_key,
                ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
            )
            .with_for_update()
        )


wms_conveyor_return_batch_repository = WmsConveyorReturnBatchRepository()

__all__ = [
    "WmsConveyorReturnBatchRepository",
    "WmsConveyorReturnCandidateRow",
    "WmsConveyorReturnPreparedRows",
    "WmsConveyorReturnTargetRow",
    "WmsConveyorReturnTerminalResources",
    "wms_conveyor_return_batch_repository",
]
