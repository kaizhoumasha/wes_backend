"""rack supply 在既有 Intent/Outbox/reducer 事务中的唯一履约投影。"""

from __future__ import annotations

from inspect import isawaitable
from typing import TYPE_CHECKING, Any

from src.app.resource.models import RackKind, ResourceSourceSystem
from src.app.resource.services.projection_service import ResourceProjectionService, resource_projection_service
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.models.rack_position import WorklineRackPositionRole
from src.app.runtime.orchestration.repositories.wms_fulfillment_domain_repository import (
    WmsFulfillmentDomainRepository,
    wms_fulfillment_domain_repository,
)
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.runtime.orchestration.services.rack_demand_service import WmsRackDemandClaim
from src.app.wms_integration.operation_contract import (
    WmsDomainProjectionKind,
    WmsOperationDefinition,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    RequestRackSupplyRequest,
    RequestRackSupplyResult,
)
from src.app.wms_integration.ports.operation_common import validate_json_payload
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent
    from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand


class WmsFulfillmentDomainProjector:
    """集中解释 rack supply kind 与 reducer event；不 commit，不创建第二状态机。"""

    def __init__(
        self,
        *,
        repository: WmsFulfillmentDomainRepository = wms_fulfillment_domain_repository,
        resource_projection_service: ResourceProjectionService = resource_projection_service,
        station_role_resolver: Callable[..., Any] | None = None,
        workline_code_resolver: Callable[..., Any] | None = None,
    ) -> None:
        self._repository = repository
        self._resource_projection_service = resource_projection_service
        self._station_role_resolver = station_role_resolver or self._default_station_role
        self._workline_code_resolver = workline_code_resolver or self._default_workline_code

    async def prepare_effect(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        request: BaseModel,
        execution: Any,
    ) -> None:
        """锁定 rack supply 的 PREPARING demand，并在 Outbox 前绑定 ACTIVE root。"""

        claim = self._require_claim(execution)
        demand = await self._repository.get_demand_for_update(db, claim.demand_id)
        if demand is None:
            raise RuntimeError("WMS rack demand claim is missing")
        self._validate_preparing_root(demand, claim=claim, operation=operation, request=request)
        intent_id = getattr(getattr(execution, "intent_log", None), "id", None)
        if not isinstance(intent_id, int) or intent_id <= 0:
            raise RuntimeError("WMS rack demand preparation requires claimed intent id")
        demand.root_intent_id = intent_id
        demand.lifecycle_state = "ACTIVE"
        await db.flush()

    async def project_event(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        request_payload: dict[str, Any],
        event: EffectReducerEvent,
        reduction: Any,
        frozen_ack: Any = None,
    ) -> None:
        """在唯一 reducer 后投影 rack supply domain facts。"""

        del frozen_ack
        if operation.domain_projection_kind is None:
            return
        if operation.domain_projection_kind is not WmsDomainProjectionKind.RACK_SUPPLY_DEMAND:
            raise RuntimeError("WMS fulfillment domain projection kind is unbound")
        if not bool(getattr(reduction, "state_changed", False)) or bool(getattr(reduction, "contradiction", False)):
            return
        demand = await self._repository.get_demand_by_dispatch_for_update(db, dispatch_key=event.dispatch_key)
        if demand is None:
            raise RuntimeError("WMS fulfillment domain demand is missing")
        if event.event_type in {
            EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
            EffectReducerEventType.STATUS_REJECTED,
        }:
            await self._project_reject(
                demand=demand,
                event=event,
            )
            await db.flush()
            return
        if event.event_type is not EffectReducerEventType.STATUS_COMPLETED:
            return
        result = validate_json_payload(operation.result_model, self._terminal_result_payload(event))
        if getattr(result, "task_outcome", None) != "SUCCESS":
            return
        request = validate_json_payload(operation.request_model, request_payload)
        if not isinstance(request, RequestRackSupplyRequest) or not isinstance(result, RequestRackSupplyResult):
            raise TypeError("rack supply fulfillment projection requires typed request/result")
        await self._project_e08_success(db, demand=demand, request=request, result=result, event=event)
        await db.flush()

    @staticmethod
    def _require_claim(execution: Any) -> WmsRackDemandClaim:
        ctx = getattr(execution, "ctx", None)
        if not isinstance(ctx, dict):
            raise TypeError("WMS rack demand preparation requires runtime execution context")
        claim = ctx.get("wms_rack_demand_claim")
        if not isinstance(claim, WmsRackDemandClaim):
            raise TypeError("WMS rack demand preparation claim is missing")
        return claim

    @staticmethod
    def _validate_preparing_root(
        demand: WmsRackDemand,
        *,
        claim: WmsRackDemandClaim,
        operation: WmsOperationDefinition,
        request: BaseModel,
    ) -> None:
        if (
            demand.lifecycle_state != "PREPARING"
            or demand.root_intent_id is not None
            or demand.id != claim.demand_id
            or demand.workline_id != claim.workline_id
            or demand.station_code != claim.station_code
            or demand.rack_type != claim.rack_type
        ):
            raise ValueError("WMS rack demand preparing root drifted")
        if demand.demand_generation != claim.demand_generation:
            raise ValueError("WMS rack demand generation drifted")
        if operation.domain_projection_kind is WmsDomainProjectionKind.RACK_SUPPLY_DEMAND:
            if not isinstance(request, RequestRackSupplyRequest):
                raise TypeError("rack supply demand requires its typed request")
            if (
                request.station_code != demand.station_code
                or request.rack_type != demand.rack_type
                or request.demand_generation != demand.demand_generation
            ):
                raise ValueError("WMS rack supply root request drifted from demand generation")
            return
        raise RuntimeError("WMS fulfillment domain projection kind is unbound")

    async def _project_reject(
        self,
        *,
        demand: WmsRackDemand,
        event: EffectReducerEvent,
    ) -> None:
        self._close_demand(demand, occurred_at_ms=event.occurred_at_ms)

    async def _project_e08_success(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        request: RequestRackSupplyRequest,
        result: RequestRackSupplyResult,
        event: EffectReducerEvent,
    ) -> None:
        if request.rack_type != demand.rack_type:
            raise ValueError("E08 terminal request rack type drifted from demand")
        await self._project_arrival(
            db,
            demand=demand,
            rack_code=result.rack_id,
            position_code=result.final_station_code,
            source_version=result.source_version,
            source_task_id=result.provider_reference,
            event=event,
        )
        if await self._is_piece_sorting_station(
            db,
            workline_id=demand.workline_id,
            station_code=result.final_station_code,
        ):
            await self._repository.acquire_piece_sorting_owner(
                db,
                demand=demand,
                rack_code=result.rack_id,
                source_event_id=event.source_event_id,
                occurred_at_ms=event.occurred_at_ms,
            )
        self._close_demand(demand, occurred_at_ms=event.occurred_at_ms)

    async def _project_arrival(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        rack_code: str,
        position_code: str,
        source_version: str,
        source_task_id: str,
        event: EffectReducerEvent,
    ) -> None:
        workline_code = await self._resolve(
            self._workline_code_resolver,
            db,
            workline_id=demand.workline_id,
        )
        occurred_at = timezone.to_db_datetime(event.occurred_at_ms / 1000)
        if occurred_at is None:
            raise ValueError("WMS terminal event timestamp is invalid")
        projection = await self._resource_projection_service.record_rack_arrived_at_workline_position(
            db,
            rack_code=rack_code,
            rack_kind=RackKind(demand.rack_type),
            workline_code=str(workline_code),
            position_code=position_code,
            source_system=ResourceSourceSystem.WMS,
            source_event_id=event.source_event_id,
            idempotency_key=f"wms-rack-arrival:{event.source_event_id}",
            occurred_at=occurred_at,
            source_version=source_version,
            source_task_id=source_task_id,
            external_location_code=position_code,
            workline_id=demand.workline_id,
        )
        status = getattr(getattr(projection, "status", None), "value", getattr(projection, "status", None))
        if status not in {None, "PROJECTED", "DUPLICATE"}:
            raise RuntimeError("WMS rack arrival projection did not converge")

    async def _is_piece_sorting_station(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        station_code: str,
    ) -> bool:
        role = await self._resolve(
            self._station_role_resolver,
            db,
            workline_id=workline_id,
            station_code=station_code,
        )
        return str(getattr(role, "value", role)) in {
            "PIECE_SORTING",
            "STATION_A",
            "STATION_B",
        }

    async def _default_station_role(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        station_code: str,
    ) -> str:
        position = await self._repository.get_station_position_for_update(
            db,
            workline_id=workline_id,
            station_code=station_code,
        )
        if (
            position is not None
            and position.position_role is WorklineRackPositionRole.SMT_SORTER_STATION
            and position.allowed_rack_kind is RackKind.SINGLE_LAYER
        ):
            return "PIECE_SORTING"
        return "OTHER"

    @staticmethod
    async def _default_workline_code(db: AsyncSession, *, workline_id: int) -> str:
        workline = await workline_repository.get_by_id(db, workline_id)
        if workline is None:
            raise RuntimeError("WMS fulfillment workline is missing")
        return str(workline.line_code)

    @staticmethod
    async def _resolve(resolver: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        value = resolver(*args, **kwargs)
        return await value if isawaitable(value) else value

    @staticmethod
    def _terminal_result_payload(event: EffectReducerEvent) -> dict[str, Any]:
        return WmsFulfillmentDomainProjector._terminal_result_from_evidence(event.evidence_json)

    @staticmethod
    def _terminal_result_from_evidence(evidence_json: dict[str, Any]) -> dict[str, Any]:
        result = WmsFulfillmentDomainProjector._optional_terminal_result_from_evidence(evidence_json)
        if result is None:
            raise TypeError("WMS terminal result evidence is missing")
        return result

    @staticmethod
    def _optional_terminal_result_from_evidence(evidence_json: dict[str, Any]) -> dict[str, Any] | None:
        snapshot = evidence_json.get("snapshot")
        result = snapshot.get("result") if isinstance(snapshot, dict) else None
        return result if isinstance(result, dict) else None

    @staticmethod
    def _close_demand(demand: WmsRackDemand, *, occurred_at_ms: int) -> None:
        demand.lifecycle_state = "CLOSED"
        demand.closed_at_ms = occurred_at_ms
        demand.reconciliation_case_id = None


wms_fulfillment_domain_projector = WmsFulfillmentDomainProjector()


__all__ = [
    "WmsFulfillmentDomainProjector",
    "wms_fulfillment_domain_projector",
]
