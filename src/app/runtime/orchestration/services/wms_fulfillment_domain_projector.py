"""E08/E09 在既有 Intent/Outbox/reducer 事务中的唯一履约投影。"""

from __future__ import annotations

from inspect import isawaitable
from typing import TYPE_CHECKING, Any

from src.app.resource.models import RackKind, ResourceSourceSystem
from src.app.resource.services.projection_service import ResourceProjectionService, resource_projection_service
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.models.rack_position import WorklineRackPositionRole
from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffDemandStatus
from src.app.runtime.orchestration.repositories.wms_fulfillment_domain_repository import (
    WmsFulfillmentDomainRepository,
    wms_fulfillment_domain_repository,
)
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.runtime.orchestration.services.full_box_exchange_service import (
    FullBoxExchangeClaim,
    FullBoxExchangeService,
    full_box_exchange_service,
)
from src.app.runtime.orchestration.services.rack_demand_service import WmsRackDemandClaim
from src.app.wms_integration.operation_contract import (
    WmsDomainProjectionKind,
    WmsOperationDefinition,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    FullBoxExchangeRequest,
    FullBoxExchangeResult,
    RequestRackSupplyRequest,
    RequestRackSupplyResult,
    RequestRackTransportRequest,
    RequestRackTransportResult,
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
    """集中解释 E08/E09 kind 与 reducer event；不 commit，不创建第二状态机。"""

    def __init__(
        self,
        *,
        repository: WmsFulfillmentDomainRepository = wms_fulfillment_domain_repository,
        full_box_exchange: FullBoxExchangeService = full_box_exchange_service,
        resource_projection_service: ResourceProjectionService = resource_projection_service,
        station_role_resolver: Callable[..., Any] | None = None,
        workline_code_resolver: Callable[..., Any] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._repository = repository
        self._full_box_exchange = full_box_exchange
        self._resource_projection_service = resource_projection_service
        self._station_role_resolver = station_role_resolver or self._default_station_role
        self._workline_code_resolver = workline_code_resolver or self._default_workline_code
        self._now_ms = now_ms or (lambda: int(timezone.now_utc().timestamp() * 1000))

    async def prepare_effect(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        request: BaseModel,
        execution: Any,
    ) -> None:
        """锁定 PREPARING demand，校验冻结 root，并在 Outbox 前绑定 ACTIVE root。"""

        if operation.domain_projection_kind is WmsDomainProjectionKind.FULL_BOX_EXCHANGE_DEMAND:
            if not isinstance(request, FullBoxExchangeRequest):
                raise TypeError("full box exchange demand requires its typed request")
            claim = self._require_full_box_exchange_claim(execution)
            intent_id = getattr(getattr(execution, "intent_log", None), "id", None)
            if not isinstance(intent_id, int) or intent_id <= 0:
                raise RuntimeError("full box exchange preparation requires claimed intent id")
            await self._full_box_exchange.prepare_effect(
                db,
                claim=claim,
                request=request,
                intent_id=intent_id,
            )
            return
        claim = self._require_claim(execution)
        demand = await self._repository.get_demand_for_update(db, claim.demand_id)
        if demand is None:
            raise RuntimeError("WMS rack demand claim is missing")
        self._validate_preparing_root(demand, claim=claim, operation=operation, request=request)
        intent_id = getattr(getattr(execution, "intent_log", None), "id", None)
        if not isinstance(intent_id, int) or intent_id <= 0:
            raise RuntimeError("WMS rack demand preparation requires claimed intent id")
        if operation.domain_projection_kind is WmsDomainProjectionKind.RACK_TRANSPORT_DEMAND:
            transport = request
            if not isinstance(transport, RequestRackTransportRequest):
                raise TypeError("rack transport demand requires its typed request")
            await self._repository.require_source_rack_placement_for_update(
                db,
                rack_code=transport.rack_id,
                workline_id=demand.workline_id,
                source_location_code=transport.source_location_code,
            )
            source_event_id = f"wms-demand-prepare:{demand.id}:{intent_id}"
            occurred_at_ms = self._now_ms()
            if await self._is_piece_sorting_station(
                db,
                workline_id=demand.workline_id,
                station_code=transport.source_location_code,
            ):
                await self._repository.handoff_piece_sorting_to_transport(
                    db,
                    demand=demand,
                    rack_code=transport.rack_id,
                    intent_id=intent_id,
                    source_event_id=source_event_id,
                    occurred_at_ms=occurred_at_ms,
                )
            else:
                await self._repository.acquire_transport_owner(
                    db,
                    demand=demand,
                    rack_code=transport.rack_id,
                    intent_id=intent_id,
                    source_event_id=source_event_id,
                    occurred_at_ms=occurred_at_ms,
                )
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
    ) -> None:
        """在唯一 reducer 后投影 domain facts；UNKNOWN/矛盾/reconciliation 保持 owner。"""

        if operation.domain_projection_kind is None:
            return
        if not bool(getattr(reduction, "state_changed", False)) or bool(getattr(reduction, "contradiction", False)):
            return
        if operation.domain_projection_kind is WmsDomainProjectionKind.FULL_BOX_EXCHANGE_DEMAND:
            demand = await self._full_box_exchange.get_demand_by_dispatch_for_update(
                db,
                dispatch_key=event.dispatch_key,
            )
        else:
            demand = await self._repository.get_demand_by_dispatch_for_update(
                db,
                dispatch_key=event.dispatch_key,
            )
        if demand is None:
            raise RuntimeError("WMS fulfillment domain demand is missing")
        if event.event_type in {
            EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
            EffectReducerEventType.STATUS_REJECTED,
        }:
            await self._project_reject(
                db,
                demand=demand,
                operation=operation,
                request_payload=request_payload,
                event=event,
            )
            await db.flush()
            return
        if event.event_type is not EffectReducerEventType.STATUS_COMPLETED:
            return
        result_payload = self._terminal_result_payload(event)
        result = validate_json_payload(operation.result_model, result_payload)
        if getattr(result, "task_outcome", None) != "SUCCESS":
            return
        request = validate_json_payload(operation.request_model, request_payload)
        if operation.domain_projection_kind is WmsDomainProjectionKind.RACK_SUPPLY_DEMAND:
            if not isinstance(request, RequestRackSupplyRequest) or not isinstance(result, RequestRackSupplyResult):
                raise TypeError("E08 fulfillment projection requires typed request/result")
            await self._project_e08_success(db, demand=demand, request=request, result=result, event=event)
        elif operation.domain_projection_kind is WmsDomainProjectionKind.RACK_TRANSPORT_DEMAND:
            if not isinstance(request, RequestRackTransportRequest) or not isinstance(
                result, RequestRackTransportResult
            ):
                raise TypeError("E09 fulfillment projection requires typed request/result")
            await self._project_e09_success(db, demand=demand, request=request, result=result, event=event)
        elif operation.domain_projection_kind is WmsDomainProjectionKind.FULL_BOX_EXCHANGE_DEMAND:
            if not isinstance(request, FullBoxExchangeRequest) or not isinstance(result, FullBoxExchangeResult):
                raise TypeError("E11 fulfillment projection requires typed request/result")
            if not event.source_event_id:
                raise ValueError("E11 terminal projection requires source_event_id")
            await self._full_box_exchange.project_success(
                db,
                demand=demand,
                request=request,
                result=result,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
            )
            return
        else:
            raise RuntimeError("WMS fulfillment domain projection kind is unbound")
        await db.flush()

    async def project_reconciliation_opened(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        dispatch_key: str,
    ) -> None:
        """仅为 E11 reconciliation 同事务冻结 parent；不释放 owner 或 active Intent。"""

        if operation.domain_projection_kind is not WmsDomainProjectionKind.FULL_BOX_EXCHANGE_DEMAND:
            return
        demand = await self._full_box_exchange.get_demand_by_dispatch_for_update(
            db,
            dispatch_key=dispatch_key,
        )
        if demand is None:
            raise RuntimeError("full box exchange reconciliation parent demand is missing")
        demand.status = SmtInboundHandoffDemandStatus.RECONCILING
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
    def _require_full_box_exchange_claim(execution: Any) -> FullBoxExchangeClaim:
        ctx = getattr(execution, "ctx", None)
        if not isinstance(ctx, dict):
            raise TypeError("full box exchange preparation requires runtime execution context")
        claim = ctx.get("wms_full_box_exchange_claim")
        if not isinstance(claim, FullBoxExchangeClaim):
            raise TypeError("full box exchange preparation claim is missing")
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
        if demand.root_operation_identity != operation.identity:
            raise ValueError("WMS rack demand root operation drifted")
        if operation.domain_projection_kind is WmsDomainProjectionKind.RACK_SUPPLY_DEMAND:
            if not isinstance(request, RequestRackSupplyRequest):
                raise TypeError("rack supply demand requires its typed request")
            if (
                demand.required_rack_code is not None
                or request.station_code != demand.station_code
                or request.rack_type != demand.rack_type
                or request.demand_generation != demand.demand_generation
            ):
                raise ValueError("WMS rack supply root request drifted from demand generation")
            return
        if operation.domain_projection_kind is WmsDomainProjectionKind.RACK_TRANSPORT_DEMAND:
            if not isinstance(request, RequestRackTransportRequest):
                raise TypeError("rack transport demand requires its typed request")
            if demand.required_rack_code != request.rack_id or demand.station_code != request.destination_station_code:
                raise ValueError("WMS rack transport root request drifted from demand")
            return
        raise RuntimeError("WMS fulfillment domain projection kind is unbound")

    async def _project_reject(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        operation: WmsOperationDefinition,
        request_payload: dict[str, Any],
        event: EffectReducerEvent,
    ) -> None:
        if operation.domain_projection_kind is WmsDomainProjectionKind.FULL_BOX_EXCHANGE_DEMAND:
            demand.status = SmtInboundHandoffDemandStatus.RECONCILING
            return
        if operation.domain_projection_kind is WmsDomainProjectionKind.RACK_TRANSPORT_DEMAND:
            request = validate_json_payload(RequestRackTransportRequest, request_payload)
            if demand.handoff_from_owner_id is not None:
                await self._repository.restore_piece_sorting_handoff_after_reject(
                    db,
                    demand=demand,
                    rack_code=request.rack_id,
                    source_event_id=event.source_event_id,
                    occurred_at_ms=event.occurred_at_ms,
                )
            else:
                await self._repository.release_transport_owner(
                    db,
                    demand=demand,
                    rack_code=request.rack_id,
                    source_event_id=event.source_event_id,
                    occurred_at_ms=event.occurred_at_ms,
                )
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

    async def _project_e09_success(
        self,
        db: AsyncSession,
        *,
        demand: WmsRackDemand,
        request: RequestRackTransportRequest,
        result: RequestRackTransportResult,
        event: EffectReducerEvent,
    ) -> None:
        await self._project_arrival(
            db,
            demand=demand,
            rack_code=result.rack_id,
            position_code=result.final_location_code,
            source_version=result.source_version,
            source_task_id=result.provider_reference,
            event=event,
        )
        if demand.handoff_from_owner_id is not None:
            await self._repository.release_transport_owner(
                db,
                demand=demand,
                rack_code=result.rack_id,
                source_event_id=event.source_event_id,
                occurred_at_ms=event.occurred_at_ms,
            )
        elif await self._is_piece_sorting_station(
            db,
            workline_id=demand.workline_id,
            station_code=request.destination_station_code,
        ):
            await self._repository.transfer_transport_to_piece_sorting(
                db,
                demand=demand,
                rack_code=result.rack_id,
                source_event_id=event.source_event_id,
            )
        else:
            await self._repository.release_transport_owner(
                db,
                demand=demand,
                rack_code=result.rack_id,
                source_event_id=event.source_event_id,
                occurred_at_ms=event.occurred_at_ms,
            )
        self._close_demand(demand, occurred_at_ms=event.occurred_at_ms)

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
        snapshot = event.evidence_json.get("snapshot")
        result = snapshot.get("result") if isinstance(snapshot, dict) else None
        if not isinstance(result, dict):
            raise TypeError("WMS terminal result evidence is missing")
        return result

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
