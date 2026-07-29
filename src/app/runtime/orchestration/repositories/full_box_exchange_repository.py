"""E11 满箱交换阶段门、owner 与终态资源的批量锁 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import String, and_, select, tuple_
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.app.resource.models import (
    Bin,
    BinCellOccupancy,
    BinMaterialMount,
    BinSlotTemplate,
    Rack,
    RackBinMount,
    RackPlacement,
    RackSlotTemplate,
)
from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.models.bin_cell_reservation import (
    BinCellReservationStatus,
    WorklineBinCellReservation,
)
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffSourceItem,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


class FullBoxExchangeRepository:
    """全部集合按一次查询锁定，禁止随 occupancy/material 数量产生 N+1。"""

    _ACTIVE_OWNER_STATES = ("ACTIVE", "RECONCILING")

    async def get_demand_for_update(
        self,
        db: AsyncSession,
        demand_id: int,
    ) -> SmtInboundHandoffDemand | None:
        table = cast("Any", SmtInboundHandoffDemand).__table__
        return (
            await db.execute(select(SmtInboundHandoffDemand).where(table.c.id == demand_id).with_for_update())
        ).scalar_one_or_none()

    async def get_demand_by_dispatch_for_update(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
    ) -> SmtInboundHandoffDemand | None:
        demand = cast("Any", SmtInboundHandoffDemand).__table__
        intent = cast("Any", RuntimeIntentLog).__table__
        return (
            await db.execute(
                select(SmtInboundHandoffDemand)
                .join(RuntimeIntentLog, intent.c.id == demand.c.active_full_box_exchange_intent_id)
                .where(intent.c.dispatch_key == dispatch_key)
                .with_for_update(of=SmtInboundHandoffDemand)
            )
        ).scalar_one_or_none()

    async def get_active_placement_for_update(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
    ) -> RackPlacement | None:
        table = cast("Any", RackPlacement).__table__
        return (
            await db.execute(
                select(RackPlacement)
                .where(table.c.rack_code == rack_code, table.c.ended_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def list_active_rack_mounts_for_update(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
    ) -> list[RackBinMount]:
        table = cast("Any", RackBinMount).__table__
        return list(
            (
                await db.execute(
                    select(RackBinMount)
                    .where(table.c.rack_code == rack_code, table.c.ended_at.is_(None))
                    .order_by(table.c.rack_slot_code, table.c.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    async def list_active_rack_mounts_for_racks_for_update(
        self,
        db: AsyncSession,
        *,
        rack_codes: Sequence[str],
    ) -> list[RackBinMount]:
        table = cast("Any", RackBinMount).__table__
        return list(
            (
                await db.execute(
                    select(RackBinMount)
                    .where(table.c.rack_code.in_(tuple(rack_codes)), table.c.ended_at.is_(None))
                    .order_by(table.c.rack_code, table.c.rack_slot_code, table.c.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    async def get_active_bin_mount_for_update(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
    ) -> RackBinMount | None:
        table = cast("Any", RackBinMount).__table__
        return (
            await db.execute(
                select(RackBinMount).where(table.c.bin_code == bin_code, table.c.ended_at.is_(None)).with_for_update()
            )
        ).scalar_one_or_none()

    async def get_rack_slot_template_for_update(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        slot_code: str,
    ) -> RackSlotTemplate | None:
        rack = cast("Any", Rack).__table__
        slot = cast("Any", RackSlotTemplate).__table__
        return (
            await db.execute(
                select(RackSlotTemplate)
                .join(Rack, rack.c.rack_type_code == slot.c.rack_type_code)
                .where(rack.c.rack_code == rack_code, slot.c.slot_code == slot_code)
                .with_for_update(of=RackSlotTemplate)
            )
        ).scalar_one_or_none()

    async def list_rack_slots_for_update(
        self,
        db: AsyncSession,
        *,
        rack_slot_keys: Sequence[tuple[str, str]],
    ) -> list[tuple[Rack, RackSlotTemplate]]:
        rack = cast("Any", Rack).__table__
        slot = cast("Any", RackSlotTemplate).__table__
        return [
            (row[0], row[1])
            for row in (
                await db.execute(
                    select(Rack, RackSlotTemplate)
                    .join(RackSlotTemplate, rack.c.rack_type_code == slot.c.rack_type_code)
                    .where(tuple_(rack.c.rack_code, slot.c.slot_code).in_(tuple(rack_slot_keys)))
                    .order_by(rack.c.rack_code, slot.c.slot_code)
                    .with_for_update(of=(Rack, RackSlotTemplate))
                )
            ).all()
        ]

    async def list_occupancies_for_update(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
    ) -> list[BinCellOccupancy]:
        table = cast("Any", BinCellOccupancy).__table__
        return list(
            (
                await db.execute(
                    select(BinCellOccupancy)
                    .where(table.c.bin_code == bin_code, table.c.ended_at.is_(None))
                    .order_by(table.c.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    async def list_occupancies_for_bins_for_update(
        self,
        db: AsyncSession,
        *,
        bin_codes: Sequence[str],
    ) -> list[BinCellOccupancy]:
        table = cast("Any", BinCellOccupancy).__table__
        return list(
            (
                await db.execute(
                    select(BinCellOccupancy)
                    .where(table.c.bin_code.in_(tuple(bin_codes)), table.c.ended_at.is_(None))
                    .order_by(table.c.bin_code, table.c.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    async def list_bin_usage_cells_for_update(
        self,
        db: AsyncSession,
        *,
        bin_codes: Sequence[str],
    ) -> list[Any]:
        """批量锁定料箱/料格模板，并用 active occupancy 补齐每个模板格的 usage。"""

        bin_master = cast("Any", Bin).__table__
        slot = cast("Any", BinSlotTemplate).__table__
        occupancy = cast("Any", BinCellOccupancy).__table__
        return list(
            (
                await db.execute(
                    select(
                        bin_master.c.bin_code.label("bin_code"),
                        slot.c.bin_slot_index.label("bin_slot_index"),
                        slot.c.bin_slot_code.label("bin_slot_code"),
                        slot.c.max_depth_mm.label("capacity_depth_mm"),
                        occupancy.c.id.label("occupancy_id"),
                        occupancy.c.used_depth_mm.label("used_depth_mm"),
                        occupancy.c.occupancy_status.label("occupancy_status"),
                    )
                    .select_from(Bin)
                    .join(BinSlotTemplate, slot.c.bin_type_code == bin_master.c.bin_type_code)
                    .outerjoin(
                        BinCellOccupancy,
                        and_(
                            occupancy.c.bin_code == bin_master.c.bin_code,
                            occupancy.c.bin_cell_index == sql_cast(slot.c.bin_slot_index, String),
                            occupancy.c.ended_at.is_(None),
                        ),
                    )
                    .where(
                        bin_master.c.bin_code.in_(tuple(bin_codes)),
                        slot.c.active.is_(True),
                    )
                    .order_by(bin_master.c.bin_code, slot.c.bin_slot_index)
                    .with_for_update(of=(Bin, BinSlotTemplate))
                )
            )
            .mappings()
            .all()
        )

    async def list_material_mounts_for_update(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
    ) -> list[BinMaterialMount]:
        table = cast("Any", BinMaterialMount).__table__
        return list(
            (
                await db.execute(
                    select(BinMaterialMount)
                    .where(table.c.bin_code == bin_code, table.c.ended_at.is_(None))
                    .order_by(table.c.bin_cell_occupancy_id, table.c.cell_stack_position, table.c.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    async def list_source_items_for_update(
        self,
        db: AsyncSession,
        *,
        demand_id: int,
        bin_code: str,
    ) -> list[SmtInboundHandoffSourceItem]:
        table = cast("Any", SmtInboundHandoffSourceItem).__table__
        return list(
            (
                await db.execute(
                    select(SmtInboundHandoffSourceItem)
                    .where(table.c.handoff_demand_id == demand_id, table.c.bin_code == bin_code)
                    .order_by(table.c.item_key, table.c.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    async def list_active_reservations_for_update(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
    ) -> list[WorklineBinCellReservation]:
        table = cast("Any", WorklineBinCellReservation).__table__
        return list(
            (
                await db.execute(
                    select(WorklineBinCellReservation)
                    .where(
                        table.c.bin_code == bin_code,
                        table.c.reservation_status.in_(
                            (
                                BinCellReservationStatus.PLANNED,
                                BinCellReservationStatus.RECONCILING,
                            )
                        ),
                    )
                    .order_by(table.c.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    async def insert_full_box_exchange_owners(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        owner_key: str,
        owner_intent_id: int,
        objects: Sequence[tuple[str, str]],
        source_event_id: str,
        occurred_at_ms: int,
    ) -> None:
        table = cast("Any", MaterialFlowOwner).__table__
        values = [
            {
                "workline_id": workline_id,
                "object_type": object_type,
                "object_key": object_key,
                "owner_type": "FULL_BOX_EXCHANGE",
                "owner_key": owner_key,
                "owner_intent_id": owner_intent_id,
                "lifecycle_state": "ACTIVE",
                "source_event_id": source_event_id,
                "acquired_at_ms": occurred_at_ms,
                "released_at_ms": None,
                "reconciliation_case_id": None,
            }
            for object_type, object_key in objects
        ]
        inserted = (
            await db.execute(
                pg_insert(table)
                .values(values)
                .on_conflict_do_nothing(
                    index_elements=(table.c.object_type, table.c.object_key),
                    index_where=table.c.lifecycle_state.in_(self._ACTIVE_OWNER_STATES),
                )
                .returning(table.c.id)
            )
        ).scalars()
        if len(list(inserted)) != len(values):
            raise RuntimeError("material-flow owner conflict")

    async def list_active_owners_for_update(
        self,
        db: AsyncSession,
        *,
        owner_key: str,
    ) -> list[MaterialFlowOwner]:
        table = cast("Any", MaterialFlowOwner).__table__
        return list(
            (
                await db.execute(
                    select(MaterialFlowOwner)
                    .where(
                        table.c.owner_key == owner_key,
                        table.c.lifecycle_state.in_(self._ACTIVE_OWNER_STATES),
                    )
                    .order_by(table.c.object_type, table.c.object_key, table.c.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    def add_rack_mounts(db: AsyncSession, mounts: Sequence[RackBinMount]) -> None:
        db.add_all(list(mounts))


full_box_exchange_repository = FullBoxExchangeRepository()


__all__ = ["FullBoxExchangeRepository", "full_box_exchange_repository"]
