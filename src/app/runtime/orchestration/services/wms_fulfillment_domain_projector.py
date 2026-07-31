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
from src.app.runtime.orchestration.services.wms_conveyor_batch_service import (
    WmsConveyorBatchClaim,
    WmsConveyorBatchService,
    wms_conveyor_batch_service,
)
from src.app.runtime.orchestration.services.wms_conveyor_return_batch_service import (
    WmsConveyorReturnBatchClaim,
    WmsConveyorReturnBatchService,
    wms_conveyor_return_batch_service,
)
from src.app.wms_integration.operation_contract import (
    WmsDomainProjectionKind,
    WmsOperationDefinition,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    MOVE_BINS_FROM_CONVEYOR_EXIT,
    FullBoxExchangeRequest,
    FullBoxExchangeResult,
    MoveBinsFromConveyorExitRequest,
    MoveBinsFromConveyorExitResult,
    MoveBinsToConveyorEntryRequest,
    MoveBinsToConveyorEntryResult,
    RequestRackSupplyRequest,
    RequestRackSupplyResult,
    RequestRackTransportRequest,
    RequestRackTransportResult,
    WmsEffectAck,
    validate_fulfillment_ack,
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
        conveyor_batch: WmsConveyorBatchService = wms_conveyor_batch_service,
        conveyor_return_batch: WmsConveyorReturnBatchService = wms_conveyor_return_batch_service,
        resource_projection_service: ResourceProjectionService = resource_projection_service,
        station_role_resolver: Callable[..., Any] | None = None,
        workline_code_resolver: Callable[..., Any] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._repository = repository
        self._full_box_exchange = full_box_exchange
        self._conveyor_batch = conveyor_batch
        self._conveyor_return_batch = conveyor_return_batch
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

        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH:
            if not isinstance(request, MoveBinsFromConveyorExitRequest):
                raise TypeError("conveyor return batch requires its typed request")
            claim = self._require_conveyor_return_batch_claim(execution)
            intent_id = getattr(getattr(execution, "intent_log", None), "id", None)
            if not isinstance(intent_id, int) or intent_id <= 0:
                raise RuntimeError("conveyor return batch preparation requires claimed intent id")
            await self._conveyor_return_batch.prepare_effect(
                db,
                claim=claim,
                request=request,
                intent_id=intent_id,
            )
            return
        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH:
            if not isinstance(request, MoveBinsToConveyorEntryRequest):
                raise TypeError("conveyor inbound batch requires its typed request")
            claim = self._require_conveyor_batch_claim(execution)
            intent_id = getattr(getattr(execution, "intent_log", None), "id", None)
            if not isinstance(intent_id, int) or intent_id <= 0:
                raise RuntimeError("conveyor inbound batch preparation requires claimed intent id")
            await self._conveyor_batch.prepare_effect(
                db,
                claim=claim,
                request=request,
                intent_id=intent_id,
            )
            return
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
        frozen_ack: WmsEffectAck | None = None,
    ) -> None:
        """在唯一 reducer 后投影 domain facts；UNKNOWN/矛盾/reconciliation 保持 owner。"""

        if operation.domain_projection_kind is None:
            return
        if not bool(getattr(reduction, "state_changed", False)) or bool(getattr(reduction, "contradiction", False)):
            return
        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH:
            await self._project_e13_event(
                db,
                request_payload=request_payload,
                event=event,
                frozen_ack=frozen_ack,
            )
            return
        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH:
            await self._project_e12_event(
                db,
                request_payload=request_payload,
                event=event,
            )
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

    async def _project_e12_event(
        self,
        db: AsyncSession,
        *,
        request_payload: dict[str, Any],
        event: EffectReducerEvent,
    ) -> None:
        request = validate_json_payload(MoveBinsToConveyorEntryRequest, request_payload)
        if event.event_type is EffectReducerEventType.TRANSPORT_ACCEPTED:
            ack = self._typed_ack(event)
            validate_fulfillment_ack(request, ack)
            await self._conveyor_batch.project_ack(
                db,
                request=request,
                ack=ack,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
            )
            return
        if event.event_type is EffectReducerEventType.ASYNC_SUBMIT_REJECTED:
            await self._conveyor_batch.project_reject(
                db,
                request=request,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
            )
            return
        if event.event_type is EffectReducerEventType.STATUS_REJECTED:
            await self._conveyor_batch.project_status_reject(
                db,
                request=request,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
                reason_code=event.reason_code,
            )
            return
        if event.event_type is not EffectReducerEventType.STATUS_COMPLETED:
            raise RuntimeError("E12 ACK/terminal convergence is not bound")
        result = validate_json_payload(
            MoveBinsToConveyorEntryResult,
            self._terminal_result_payload(event),
        )
        if result.task_outcome != "SUCCESS":
            raise RuntimeError("E12 non-success terminal requires reconciliation projection")
        await self._conveyor_batch.project_success(
            db,
            request=request,
            result=result,
            occurred_at_ms=event.occurred_at_ms,
            source_event_id=event.source_event_id,
        )

    async def _project_e13_event(
        self,
        db: AsyncSession,
        *,
        request_payload: dict[str, Any],
        event: EffectReducerEvent,
        frozen_ack: WmsEffectAck | None = None,
    ) -> None:
        """E13 submit 与 status terminal 统一分派到 return-batch 领域服务。"""

        request = validate_json_payload(MoveBinsFromConveyorExitRequest, request_payload)
        if event.event_type is EffectReducerEventType.TRANSPORT_ACCEPTED:
            ack = self._typed_ack(event)
            validate_fulfillment_ack(request, ack)
            await self._conveyor_return_batch.project_ack(
                db,
                request=request,
                ack=ack,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
            )
            return
        if event.event_type is EffectReducerEventType.ASYNC_SUBMIT_REJECTED:
            await self._conveyor_return_batch.project_reject(
                db,
                request=request,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
            )
            return
        if event.event_type is EffectReducerEventType.STATUS_REJECTED:
            await self._conveyor_return_batch.project_reject(
                db,
                request=request,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
            )
            return
        if event.event_type is EffectReducerEventType.STATUS_COMPLETED:
            await self.project_conveyor_return_terminal_result(
                db,
                operation=MOVE_BINS_FROM_CONVEYOR_EXIT,
                request_payload=request_payload,
                result_payload=self._terminal_result_payload(event),
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id or "",
                frozen_ack=frozen_ack,
            )
            return
        raise RuntimeError("E13 terminal/status projection is not bound")

    async def project_conveyor_return_terminal_result(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        request_payload: dict[str, Any],
        result_payload: dict[str, Any],
        occurred_at_ms: int,
        source_event_id: str,
        frozen_ack: WmsEffectAck | None = None,
    ) -> None:
        """E13 all-success terminal 唯一 delegate；由 status scanner 与显式领域调用复用。"""

        if (
            operation.identity != MOVE_BINS_FROM_CONVEYOR_EXIT.identity
            or operation.domain_projection_kind is not WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH
        ):
            raise ValueError("conveyor return terminal delegate requires E13 operation")
        request = validate_json_payload(MoveBinsFromConveyorExitRequest, request_payload)
        result = validate_json_payload(MoveBinsFromConveyorExitResult, result_payload)
        if result.task_outcome != "SUCCESS":
            raise ValueError("E13 direct success delegate requires all-success result")
        await self._conveyor_return_batch.project_success(
            db,
            request=request,
            result=result,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
            frozen_ack=frozen_ack,
        )

    async def project_conveyor_return_reconciliation_result(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        request_payload: dict[str, Any],
        result_payload: dict[str, Any],
        reconciliation_case_id: int,
        occurred_at_ms: int,
        source_event_id: str,
        reason_code: str,
        frozen_ack: WmsEffectAck | None = None,
    ) -> None:
        """E13 非成功 terminal 唯一 delegate；只接受已有 OPEN case。"""

        if (
            operation.identity != MOVE_BINS_FROM_CONVEYOR_EXIT.identity
            or operation.domain_projection_kind is not WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH
        ):
            raise ValueError("conveyor return reconciliation delegate requires E13 operation")
        request = validate_json_payload(MoveBinsFromConveyorExitRequest, request_payload)
        result = validate_json_payload(MoveBinsFromConveyorExitResult, result_payload)
        if result.task_outcome == "SUCCESS":
            raise ValueError("E13 direct reconciliation delegate requires non-success result")
        await self._conveyor_return_batch.project_reconciliation(
            db,
            request=request,
            result=result,
            reconciliation_case_id=reconciliation_case_id,
            occurred_at_ms=occurred_at_ms,
            source_event_id=source_event_id,
            reason_code=reason_code,
            frozen_ack=frozen_ack,
        )

    async def project_reconciliation_opened(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        dispatch_key: str,
        reason_code: str | None = None,
        evidence_json: dict[str, Any] | None = None,
        frozen_ack: WmsEffectAck | None = None,
    ) -> None:
        """按 projection kind 在同事务冻结或收敛 E11/E12/E13 对账事实。"""

        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH:
            if not reason_code or not isinstance(evidence_json, dict):
                raise ValueError("E13 reconciliation projection requires reason and evidence")
            result_payload = self._optional_terminal_result_from_evidence(evidence_json)
            result = (
                validate_json_payload(MoveBinsFromConveyorExitResult, result_payload)
                if result_payload is not None
                else None
            )
            if result is not None and result.task_outcome == "SUCCESS":
                raise ValueError("E13 reconciliation projection requires non-success result")
            await self._conveyor_return_batch.project_reconciliation_opened(
                db,
                dispatch_key=dispatch_key,
                reason_code=reason_code,
                evidence_json=evidence_json,
                result=result,
                frozen_ack=frozen_ack,
            )
            return
        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH:
            if not reason_code or not isinstance(evidence_json, dict):
                raise ValueError("E12 reconciliation projection requires reason and evidence")
            result_payload = self._optional_terminal_result_from_evidence(evidence_json)
            result = (
                validate_json_payload(MoveBinsToConveyorEntryResult, result_payload)
                if result_payload is not None
                else None
            )
            if result is not None and result.task_outcome == "SUCCESS":
                raise ValueError("E12 reconciliation projection requires non-success result")
            await self._conveyor_batch.project_reconciliation_opened(
                db,
                dispatch_key=dispatch_key,
                result=result,
                reason_code=reason_code,
                evidence_json=evidence_json,
            )
            return
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

    async def should_reconcile_status_reject(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        dispatch_key: str,
        request_payload: dict[str, Any],
        frozen_ack: WmsEffectAck | None = None,
    ) -> bool:
        """E12/E13 STATUS_REJECTED 在 reducer 前检查 ACK 或更后的本地事实。"""

        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH:
            request = validate_json_payload(MoveBinsToConveyorEntryRequest, request_payload)
            return await self._conveyor_batch.should_reconcile_status_reject(
                db,
                dispatch_key=dispatch_key,
                queue_code=request.destination_station_code,
            )
        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH:
            if frozen_ack is not None:
                return True
            request = validate_json_payload(MoveBinsFromConveyorExitRequest, request_payload)
            return await self._conveyor_return_batch.should_reconcile_transport_failure(
                db,
                request=request,
            )
        return False

    async def should_reconcile_ack(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        request_payload: dict[str, Any],
        event: EffectReducerEvent,
    ) -> bool:
        """E13 ACK prefix 在领域写入前检查是否遗漏已动作候选。"""

        if operation.domain_projection_kind is not WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH:
            return False
        request = validate_json_payload(MoveBinsFromConveyorExitRequest, request_payload)
        ack = self._typed_ack(event)
        return await self._conveyor_return_batch.should_reconcile_ack(
            db,
            request=request,
            ack=ack,
        )

    async def should_reconcile_transport_failure(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        request_payload: dict[str, Any],
    ) -> bool:
        """E12 transport 失败在写领域结果前检查 ACK/物理事实。"""

        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH:
            request = validate_json_payload(MoveBinsToConveyorEntryRequest, request_payload)
            return await self._conveyor_batch.should_reconcile_transport_failure(
                db,
                request=request,
            )
        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH:
            request = validate_json_payload(MoveBinsFromConveyorExitRequest, request_payload)
            return await self._conveyor_return_batch.should_reconcile_transport_failure(
                db,
                request=request,
            )
        return False

    async def project_transport_not_sent_exhausted(
        self,
        db: AsyncSession,
        *,
        operation: WmsOperationDefinition,
        request_payload: dict[str, Any],
        event: EffectReducerEvent,
    ) -> None:
        """E12/E13 明确未发送且重试耗尽时释放未被消费的预约。"""

        if event.event_type is not EffectReducerEventType.TRANSPORT_NOT_SENT or not event.retry_exhausted:
            raise ValueError("conveyor transport release requires exhausted TRANSPORT_NOT_SENT")
        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH:
            request = validate_json_payload(MoveBinsToConveyorEntryRequest, request_payload)
            await self._conveyor_batch.project_transport_not_sent_exhausted(
                db,
                request=request,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
            )
            return
        if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH:
            request = validate_json_payload(MoveBinsFromConveyorExitRequest, request_payload)
            await self._conveyor_return_batch.project_reject(
                db,
                request=request,
                occurred_at_ms=event.occurred_at_ms,
                source_event_id=event.source_event_id,
            )

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
    def _require_conveyor_batch_claim(execution: Any) -> WmsConveyorBatchClaim:
        ctx = getattr(execution, "ctx", None)
        if not isinstance(ctx, dict):
            raise TypeError("conveyor inbound batch preparation requires runtime execution context")
        claim = ctx.get("wms_conveyor_batch_claim")
        if not isinstance(claim, WmsConveyorBatchClaim):
            raise TypeError("conveyor inbound batch preparation claim is missing")
        return claim

    @staticmethod
    def _require_conveyor_return_batch_claim(execution: Any) -> WmsConveyorReturnBatchClaim:
        ctx = getattr(execution, "ctx", None)
        if not isinstance(ctx, dict):
            raise TypeError("conveyor return batch preparation requires runtime execution context")
        claim = ctx.get("wms_conveyor_return_batch_claim")
        if not isinstance(claim, WmsConveyorReturnBatchClaim):
            raise TypeError("conveyor return batch preparation claim is missing")
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
    def _typed_ack(event: EffectReducerEvent) -> WmsEffectAck:
        typed_outcome = event.terminal_outcome
        if not isinstance(typed_outcome, dict):
            raise TypeError("E12 ACK evidence is missing")
        payload = typed_outcome.get("payload")
        if typed_outcome.get("kind") != "success" or not isinstance(payload, dict):
            raise TypeError("E12 ACK evidence is missing")
        return validate_json_payload(WmsEffectAck, payload)

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
