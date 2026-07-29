"""E08/E09 root Intent claim 前的 demand mutex 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.repositories.wms_fulfillment_domain_repository import (
    WmsFulfillmentDomainRepository,
    wms_fulfillment_domain_repository,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    REQUEST_RACK_SUPPLY,
    REQUEST_RACK_TRANSPORT,
    RequestRackSupplyRequest,
    RequestRackTransportRequest,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand
    from src.app.wms_integration.operation_contract import WmsOperationDefinition


@dataclass(frozen=True, slots=True)
class WmsRackDemandClaim:
    """只在 reserve→RuntimeIntent claim→preparation hook 同一事务传播。"""

    demand_id: int
    workline_id: int
    station_code: str
    rack_type: str
    demand_generation: int


@dataclass(frozen=True, slots=True)
class WmsRackDemandReservation:
    """created winner 才携带 root operation/request；loser 只复用现有 demand。"""

    demand: WmsRackDemand
    claim: WmsRackDemandClaim
    created: bool
    operation: WmsOperationDefinition | None
    request: BaseModel | None


class RackDemandService:
    """以 PostgreSQL partial unique 冻结同一 station/rack demand 的唯一 root。"""

    def __init__(
        self,
        *,
        repository: WmsFulfillmentDomainRepository = wms_fulfillment_domain_repository,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._repository = repository
        self._now_ms = now_ms or (lambda: int(timezone.now_utc().timestamp() * 1000))

    async def reserve_root(
        self,
        ctx: dict[str, Any],
        *,
        station_code: str,
        rack_type: str,
        demand_generation: int,
        dispatch_key: str,
        required_rack_code: str | None,
        source_location_code: str | None,
        destination_station_code: str | None,
    ) -> WmsRackDemandReservation:
        """先占 PREPARING mutex；调用方随后用同一 ctx claim RuntimeIntent。"""

        db, workline_id = self._validate_execution_context(ctx)
        operation, request = self._select_root(
            station_code=station_code,
            rack_type=rack_type,
            demand_generation=demand_generation,
            dispatch_key=dispatch_key,
            required_rack_code=required_rack_code,
            source_location_code=source_location_code,
            destination_station_code=destination_station_code,
        )
        demand, created = await self._repository.reserve_preparing_demand(
            db,
            workline_id=workline_id,
            station_code=station_code,
            rack_type=rack_type,
            demand_generation=demand_generation,
            required_rack_code=required_rack_code,
            root_operation_identity=operation.identity,
            opened_at_ms=self._now_ms(),
        )
        claim = self._claim(demand)
        if not created:
            return WmsRackDemandReservation(
                demand=demand,
                claim=claim,
                created=False,
                operation=None,
                request=None,
            )
        ctx["wms_rack_demand_claim"] = claim
        return WmsRackDemandReservation(
            demand=demand,
            claim=claim,
            created=True,
            operation=operation,
            request=request,
        )

    @staticmethod
    def _validate_execution_context(ctx: dict[str, Any]) -> tuple[Any, int]:
        if not isinstance(ctx, dict):
            raise TypeError("rack demand requires the runtime execution context")
        db = ctx.get("db")
        if db is None or ctx.get("session") is None:
            raise ValueError("rack demand requires existing db/session execution context")
        workline_id = getattr(ctx.get("workline"), "id", None)
        if not isinstance(workline_id, int) or workline_id <= 0:
            raise ValueError("rack demand requires an existing workline execution context")
        return db, workline_id

    @staticmethod
    def _select_root(
        *,
        station_code: str,
        rack_type: str,
        demand_generation: int,
        dispatch_key: str,
        required_rack_code: str | None,
        source_location_code: str | None,
        destination_station_code: str | None,
    ) -> tuple[WmsOperationDefinition, BaseModel]:
        if required_rack_code is None:
            if source_location_code is not None or destination_station_code is not None:
                raise ValueError("unknown rack demand must not carry source or destination")
            return (
                REQUEST_RACK_SUPPLY,
                RequestRackSupplyRequest(
                    dispatch_key=dispatch_key,
                    station_code=station_code,
                    rack_type=rack_type,
                    demand_generation=demand_generation,
                ),
            )
        if not source_location_code or not destination_station_code:
            raise ValueError("known rack demand requires source and destination")
        if destination_station_code != station_code:
            raise ValueError("known rack demand destination must equal station")
        return (
            REQUEST_RACK_TRANSPORT,
            RequestRackTransportRequest(
                dispatch_key=dispatch_key,
                rack_id=required_rack_code,
                source_location_code=source_location_code,
                destination_station_code=destination_station_code,
            ),
        )

    @staticmethod
    def _claim(demand: WmsRackDemand) -> WmsRackDemandClaim:
        if demand.id is None:
            raise RuntimeError("reserved WMS rack demand is missing id")
        return WmsRackDemandClaim(
            demand_id=demand.id,
            workline_id=demand.workline_id,
            station_code=demand.station_code,
            rack_type=demand.rack_type,
            demand_generation=demand.demand_generation,
        )


rack_demand_service = RackDemandService()


__all__ = [
    "RackDemandService",
    "WmsRackDemandClaim",
    "WmsRackDemandReservation",
    "rack_demand_service",
]
