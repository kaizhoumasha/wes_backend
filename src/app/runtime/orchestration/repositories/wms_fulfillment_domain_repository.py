"""E08/E09 demand root 与 material-flow owner 的 PostgreSQL Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.app.resource.models import RackPlacement
from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WmsFulfillmentDomainRepository:
    """集中持有 E08/E09 PostgreSQL 锁与 partial-unique 竞争语义。"""

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
        required_rack_code: str | None,
        root_operation_identity: str,
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
                    required_rack_code=required_rack_code,
                    root_operation_identity=root_operation_identity,
                    root_intent_id=None,
                    handoff_from_owner_id=None,
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

    async def require_source_rack_placement_for_update(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        workline_id: int,
        source_location_code: str,
    ) -> RackPlacement:
        table = cast("Any", RackPlacement).__table__
        placement = (
            await db.execute(
                select(RackPlacement)
                .where(
                    table.c.rack_code == rack_code,
                    table.c.ended_at.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if placement is None:
            raise RuntimeError("active rack placement is missing")
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
            placement.rack_code != rack_code
            or placement.workline_id != workline_id
            or source_location_code not in authoritative_locations
        ):
            raise RuntimeError("active rack placement differs from frozen transport source")
        return placement

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

    async def acquire_transport_owner(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        rack_code: str,
        intent_id: int,
        source_event_id: str,
        occurred_at_ms: int,
    ) -> MaterialFlowOwner:
        """安全取得或复用本 demand 的 STATION_TRANSPORT owner，不用异常破坏事务。"""

        table = cast("Any", MaterialFlowOwner).__table__
        owner_key = str(demand.id)
        inserted_id = (
            await db.execute(
                pg_insert(table)
                .values(
                    workline_id=demand.workline_id,
                    object_type="RACK",
                    object_key=rack_code,
                    owner_type="STATION_TRANSPORT",
                    owner_key=owner_key,
                    owner_intent_id=intent_id,
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
                raise RuntimeError("inserted material-flow owner is missing")
            return owner

        owner = await self.get_active_owner_for_update(db, rack_code=rack_code)
        if (
            owner is None
            or owner.owner_type != "STATION_TRANSPORT"
            or owner.owner_key != owner_key
            or owner.lifecycle_state != "ACTIVE"
        ):
            raise RuntimeError("material-flow owner conflict")
        owner.owner_intent_id = intent_id
        owner.source_event_id = source_event_id
        return owner

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

    async def transfer_transport_to_piece_sorting(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        rack_code: str,
        source_event_id: str,
    ) -> MaterialFlowOwner:
        owner = await self.get_active_owner_for_update(db, rack_code=rack_code)
        if (
            owner is None
            or owner.owner_type != "STATION_TRANSPORT"
            or owner.owner_key != str(demand.id)
            or owner.lifecycle_state != "ACTIVE"
        ):
            raise RuntimeError("material-flow transport owner is missing")
        owner.owner_type = "PIECE_SORTING"
        owner.source_event_id = source_event_id
        return owner

    async def handoff_piece_sorting_to_transport(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        rack_code: str,
        intent_id: int,
        source_event_id: str,
        occurred_at_ms: int,
    ) -> tuple[MaterialFlowOwner, MaterialFlowOwner]:
        source_owner = await self.get_active_owner_for_update(db, rack_code=rack_code)
        if (
            source_owner is None
            or source_owner.workline_id != demand.workline_id
            or source_owner.owner_type != "PIECE_SORTING"
            or source_owner.lifecycle_state != "ACTIVE"
        ):
            raise RuntimeError("material-flow piece-sorting owner is missing")
        source_owner.lifecycle_state = "RELEASED"
        source_owner.released_at_ms = occurred_at_ms
        await db.flush()
        if source_owner.id is None:
            raise RuntimeError("material-flow piece-sorting owner identity is missing")
        demand.handoff_from_owner_id = source_owner.id
        transport_owner = await self.acquire_transport_owner(
            db,
            demand=demand,
            rack_code=rack_code,
            intent_id=intent_id,
            source_event_id=source_event_id,
            occurred_at_ms=occurred_at_ms,
        )
        return source_owner, transport_owner

    async def restore_piece_sorting_handoff_after_reject(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        rack_code: str,
        source_event_id: str,
        occurred_at_ms: int,
    ) -> MaterialFlowOwner:
        if demand.handoff_from_owner_id is None:
            raise RuntimeError("material-flow handoff source owner identity is missing")
        source_owner = await db.get(
            MaterialFlowOwner,
            demand.handoff_from_owner_id,
            with_for_update=True,
        )
        if (
            source_owner is None
            or source_owner.workline_id != demand.workline_id
            or source_owner.object_type != "RACK"
            or source_owner.object_key != rack_code
            or source_owner.owner_type != "PIECE_SORTING"
            or source_owner.lifecycle_state != "RELEASED"
            or source_owner.released_at_ms is None
        ):
            raise RuntimeError("material-flow handoff source owner is invalid")
        await self.release_transport_owner(
            db,
            demand=demand,
            rack_code=rack_code,
            source_event_id=source_event_id,
            occurred_at_ms=occurred_at_ms,
        )
        await db.flush()
        source_owner.lifecycle_state = "ACTIVE"
        source_owner.released_at_ms = None
        return source_owner

    async def release_transport_owner(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        rack_code: str,
        source_event_id: str,
        occurred_at_ms: int,
    ) -> MaterialFlowOwner:
        owner = await self.get_active_owner_for_update(db, rack_code=rack_code)
        if owner is None:
            raise RuntimeError("material-flow transport owner is missing")
        if (
            owner.owner_type != "STATION_TRANSPORT"
            or owner.owner_key != str(demand.id)
            or owner.lifecycle_state != "ACTIVE"
        ):
            raise RuntimeError("material-flow transport owner conflict")
        owner.lifecycle_state = "RELEASED"
        owner.released_at_ms = occurred_at_ms
        owner.source_event_id = source_event_id
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
