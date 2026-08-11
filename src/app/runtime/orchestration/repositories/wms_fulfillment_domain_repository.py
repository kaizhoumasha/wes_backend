"""rack supply demand root 与 material-flow owner 的 PostgreSQL Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WmsFulfillmentDomainRepository:
    """集中持有 rack supply PostgreSQL 锁与 partial-unique 竞争语义。"""

    _ACTIVE_DEMAND_STATES = ("PREPARING", "ACTIVE", "RECONCILING")
    _ACTIVE_OWNER_STATES = ("ACTIVE", "RECONCILING")

    async def reserve_preparing_demand(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        station_code: str,
        rack_type: str,
        demand_generation: int,
        opened_at_ms: int,
    ) -> tuple[WmsRackDemand, bool]:
        """insert-first 串行化首次竞争；loser 在 winner 提交后锁定并复用 active demand。"""

        table = cast("Any", WmsRackDemand).__table__
        inserted_id = (
            await db.execute(
                pg_insert(table)
                .values(
                    workline_id=workline_id,
                    station_code=station_code,
                    rack_type=rack_type,
                    demand_generation=demand_generation,
                    root_intent_id=None,
                    lifecycle_state="PREPARING",
                    opened_at_ms=opened_at_ms,
                    closed_at_ms=None,
                    reconciliation_case_id=None,
                )
                .on_conflict_do_nothing(
                    index_elements=(
                        table.c.workline_id,
                        table.c.station_code,
                        table.c.rack_type,
                    ),
                    index_where=table.c.lifecycle_state.in_(self._ACTIVE_DEMAND_STATES),
                )
                .returning(table.c.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            demand = await db.get(WmsRackDemand, int(inserted_id))
            if demand is None:
                raise RuntimeError("inserted WMS rack demand is missing")
            return demand, True

        demand = (
            await db.execute(
                select(WmsRackDemand)
                .where(
                    table.c.workline_id == workline_id,
                    table.c.station_code == station_code,
                    table.c.rack_type == rack_type,
                    table.c.lifecycle_state.in_(self._ACTIVE_DEMAND_STATES),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if demand is None:
            raise RuntimeError("competing WMS rack demand disappeared before reuse")
        return demand, False

    async def get_demand_for_update(self, db: AsyncSession, demand_id: int) -> WmsRackDemand | None:
        table = cast("Any", WmsRackDemand).__table__
        return (
            await db.execute(select(WmsRackDemand).where(table.c.id == demand_id).with_for_update())
        ).scalar_one_or_none()

    async def get_demand_by_dispatch_for_update(
        self,
        db: AsyncSession,
        *,
        dispatch_key: str,
    ) -> WmsRackDemand | None:
        demand = cast("Any", WmsRackDemand).__table__
        intent = cast("Any", RuntimeIntentLog).__table__
        return (
            await db.execute(
                select(WmsRackDemand)
                .join(RuntimeIntentLog, intent.c.id == demand.c.root_intent_id)
                .where(intent.c.dispatch_key == dispatch_key)
                .with_for_update(of=WmsRackDemand)
            )
        ).scalar_one_or_none()

    async def acquire_piece_sorting_owner(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        rack_code: str,
        source_event_id: str,
        occurred_at_ms: int,
    ) -> MaterialFlowOwner:
        table = cast("Any", MaterialFlowOwner).__table__
        owner_key = str(demand.id)
        inserted_id = (
            await db.execute(
                pg_insert(table)
                .values(
                    workline_id=demand.workline_id,
                    object_type="RACK",
                    object_key=rack_code,
                    owner_type="PIECE_SORTING",
                    owner_key=owner_key,
                    owner_intent_id=demand.root_intent_id,
                    lifecycle_state="ACTIVE",
                    source_event_id=source_event_id,
                    acquired_at_ms=occurred_at_ms,
                    released_at_ms=None,
                    reconciliation_case_id=None,
                )
                .on_conflict_do_nothing(
                    index_elements=(table.c.object_type, table.c.object_key),
                    index_where=table.c.lifecycle_state.in_(self._ACTIVE_OWNER_STATES),
                )
                .returning(table.c.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            owner = await db.get(MaterialFlowOwner, int(inserted_id))
            if owner is None:
                raise RuntimeError("inserted piece-sorting owner is missing")
            return owner
        owner = await self.get_active_owner_for_update(db, rack_code=rack_code)
        if (
            owner is None
            or owner.owner_type != "PIECE_SORTING"
            or owner.owner_key != owner_key
            or owner.lifecycle_state != "ACTIVE"
        ):
            raise RuntimeError("material-flow owner conflict")
        return owner

    async def get_active_owner_for_update(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
    ) -> MaterialFlowOwner | None:
        table = cast("Any", MaterialFlowOwner).__table__
        return (
            await db.execute(
                select(MaterialFlowOwner)
                .where(
                    table.c.object_type == "RACK",
                    table.c.object_key == rack_code,
                    table.c.lifecycle_state.in_(self._ACTIVE_OWNER_STATES),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_station_position_for_update(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        station_code: str,
    ) -> WorklineRackPosition | None:
        table = cast("Any", WorklineRackPosition).__table__
        return (
            await db.execute(
                select(WorklineRackPosition)
                .where(
                    table.c.workline_id == workline_id,
                    or_(
                        table.c.position_code == station_code,
                        table.c.logic_location_code == station_code,
                        table.c.external_location_code == station_code,
                    ),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()


wms_fulfillment_domain_repository = WmsFulfillmentDomainRepository()


__all__ = [
    "WmsFulfillmentDomainRepository",
    "wms_fulfillment_domain_repository",
]
