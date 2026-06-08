"""业务需求驱动的单层货架编排服务。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from src.app.rack.models import RackTaskType
from src.app.resource.models import RackKind
from src.app.sys.models import (
    DispatchEnvelope,
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository, outbox_repository
from src.app.wms_integration.services.transport_contract import (
    WmsTransportContractService,
    wms_transport_contract_service,
)
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.services.station_lease_service import (
    StationLeaseReasonCode,
    StationLeaseService,
    station_lease_service,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_RACK_TASK_TYPE_ALIASES = {
    "MOVE_OUT_ACTIVE_RACK": RackTaskType.MOVE_RACK.value,
}
_ACTIVE_STATION_CLAIM_STATUSES = frozenset(
    {
        SystemOutboxStatus.NEW.value,
        SystemOutboxStatus.DISPATCHING.value,
        SystemOutboxStatus.SENT.value,
        SystemOutboxStatus.BLOCKED_RESOURCE.value,
    }
)


class SingleLayerRackOrchestrationDecisionCode(StrEnum):
    """单层货架编排显式决策。"""

    WAITING = "WAITING"
    DISPATCH_WMS = "DISPATCH_WMS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class SingleLayerRackOrchestrationDecision:
    """单层货架编排结果。"""

    decision: SingleLayerRackOrchestrationDecisionCode
    reason: str | None = None
    rack_operation_request: dict[str, Any] | None = None
    fact_payload: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


class SingleLayerRackOrchestrationService:
    """把业务需求转换为单层货架 WMS 派发意图。"""

    DEFAULT_OPERATION_TYPE = "SUPPLY_SINGLE_LAYER_RACK"
    DEFAULT_TIMEOUT_SECONDS = 1800
    ROUGH_SORTER_RELEASE_FACT = "ROUGH_SORTER_RELEASE_FACT"

    def __init__(
        self,
        *,
        station_lease_service: StationLeaseService = station_lease_service,
        outbox_repository: SystemOutboxRepository = outbox_repository,
        transport_contract_service: WmsTransportContractService = wms_transport_contract_service,
    ) -> None:
        self.station_lease_service = station_lease_service
        self.outbox_repository = outbox_repository
        self.transport_contract_service = transport_contract_service

    async def plan_single_layer_rack_dispatch(
        self,
        db: AsyncSession,
        *,
        business_demand_key: str | None,
        demand_type: str | None,
        workline: Any,
        session: Any | None = None,
        station_code: str,
        rack_snapshot_ref: str | None = None,
        rack_code: str | None = None,
        dispatch_key: str | None = None,
        operation_type: str | None = None,
        target_code: str | None = None,
        trace_id: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        payload: Mapping[str, Any] | None = None,
        fact_payload: Mapping[str, Any] | None = None,
    ) -> SingleLayerRackOrchestrationDecision:
        """规划单层货架派发。

        该服务只接受上游业务需求，不从货架 ready 事实反推业务；
        Station claim 成功前不会返回 WMS dispatch。
        """

        workline_code = str(getattr(workline, "line_code", "") or "")
        workline_id = getattr(workline, "id", None)
        if getattr(workline, "runtime_status", None) != WorkLineRuntimeStatus.READY:
            return SingleLayerRackOrchestrationDecision(
                decision=SingleLayerRackOrchestrationDecisionCode.WAITING,
                reason="WORKLINE_NOT_READY",
                diagnostics={"workline_code": workline_code},
            )

        if not business_demand_key or not demand_type:
            return SingleLayerRackOrchestrationDecision(
                decision=SingleLayerRackOrchestrationDecisionCode.WAITING,
                reason="BUSINESS_DEMAND_REQUIRED",
                diagnostics={"workline_code": workline_code, "station_code": station_code},
            )

        if demand_type == self.ROUGH_SORTER_RELEASE_FACT:
            return SingleLayerRackOrchestrationDecision(
                decision=SingleLayerRackOrchestrationDecisionCode.WAITING,
                reason="ROUGH_SORTER_RELEASE_FACT_RECORDED",
                fact_payload=self._rough_sorter_release_fact_payload(
                    fact_payload,
                    business_demand_key=business_demand_key,
                    workline_code=workline_code,
                    station_code=station_code,
                ),
            )

        allow_active_rack_bound = self._allows_active_rack_bound(payload)
        operation_key = self._single_layer_operation_key(
            dispatch_key=dispatch_key,
            business_demand_key=business_demand_key,
            workline_code=workline_code,
            station_code=station_code,
        )
        station_status = await self.station_lease_service.get_station_lease_status(
            db,
            workline_id=workline_id,
            workline_code=workline_code,
            position_code=station_code,
            allow_active_operation_key=operation_key,
        )
        if not station_status.available and not (
            allow_active_rack_bound and station_status.reason_code == StationLeaseReasonCode.ACTIVE_RACK_BOUND
        ):
            return SingleLayerRackOrchestrationDecision(
                decision=SingleLayerRackOrchestrationDecisionCode.WAITING,
                reason=self._station_reason(station_status),
                diagnostics=self._station_diagnostics(station_status),
            )

        session_id = self._session_id(session)
        if session_id is None:
            return SingleLayerRackOrchestrationDecision(
                decision=SingleLayerRackOrchestrationDecisionCode.BLOCKED,
                reason="SESSION_REQUIRED_FOR_STATION_LEASE",
                diagnostics={"workline_code": workline_code, "station_code": station_code},
            )

        rack_operation_request = self.transport_contract_service.build_single_layer_rack_operation_request(
            business_demand_key=business_demand_key,
            workline_code=workline_code,
            endpoint_code=station_code,
            rack_kind=RackKind.SINGLE_LAYER.value,
            operation_type=operation_type or self.DEFAULT_OPERATION_TYPE,
            payload=self._rack_operation_payload(payload, station_code=station_code, rack_code=rack_code),
            timeout_seconds=timeout_seconds,
            rack_code=rack_code,
            rack_snapshot_ref=rack_snapshot_ref,
            dispatch_key=dispatch_key,
            target_code=target_code,
            trace_id=trace_id,
        )
        envelope = self._dispatch_envelope(
            rack_operation_request,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
        )
        claimed_outbox = await self._claim_station_dispatch_lease(
            db,
            workline_id=workline_id,
            workline_code=workline_code,
            station_code=station_code,
            envelope=envelope,
            allow_active_rack_bound=allow_active_rack_bound,
            allow_active_operation_key=envelope.operation_key,
        )
        if claimed_outbox is None:
            current_status = await self.station_lease_service.get_station_lease_status(
                db,
                workline_id=workline_id,
                workline_code=workline_code,
                position_code=station_code,
                allow_active_operation_key=envelope.operation_key,
            )
            return SingleLayerRackOrchestrationDecision(
                decision=SingleLayerRackOrchestrationDecisionCode.WAITING,
                reason=self._station_reason(current_status, fallback="STATION_LEASE_CLAIM_FAILED"),
                diagnostics=self._station_diagnostics(current_status),
            )

        return SingleLayerRackOrchestrationDecision(
            decision=SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS,
            rack_operation_request=rack_operation_request,
            diagnostics={"outbox_id": getattr(claimed_outbox, "id", None)},
        )

    async def _claim_station_dispatch_lease(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        workline_code: str,
        station_code: str,
        envelope: DispatchEnvelope,
        allow_active_rack_bound: bool,
        allow_active_operation_key: str | None = None,
    ) -> SystemOutbox | None:
        try:
            if hasattr(db, "begin_nested"):
                async with db.begin_nested():
                    return await self.station_lease_service.claim_station_dispatch_lease(
                        db,
                        workline_id=workline_id,
                        workline_code=workline_code,
                        position_code=station_code,
                        envelope=envelope,
                        allow_active_rack_bound=allow_active_rack_bound,
                        allow_active_operation_key=allow_active_operation_key,
                    )
            return await self.station_lease_service.claim_station_dispatch_lease(
                db,
                workline_id=workline_id,
                workline_code=workline_code,
                position_code=station_code,
                envelope=envelope,
                allow_active_rack_bound=allow_active_rack_bound,
                allow_active_operation_key=allow_active_operation_key,
            )
        except IntegrityError:
            existing = await self.outbox_repository.get_by_dispatch_key_for_update(db, envelope.dispatch_key)
            if existing is None:
                raise
            _ensure_existing_station_claim_outbox_shape(existing, envelope)
            return existing

    @staticmethod
    def _rack_operation_payload(
        payload: Mapping[str, Any] | None,
        *,
        station_code: str,
        rack_code: str | None = None,
    ) -> dict[str, Any]:
        resolved_payload = dict(payload or {})
        station = resolved_payload.get("station")
        station_payload = dict(station) if isinstance(station, Mapping) else {}
        station_payload["position_code"] = station_code
        resolved_payload["station"] = station_payload
        resolved_payload["station_code"] = station_code
        resolved_payload["position_code"] = station_code
        resolved_payload["target_position_code"] = station_code
        resolved_payload["rack_kind"] = RackKind.SINGLE_LAYER.value
        resolved_payload["rack_tasks"] = SingleLayerRackOrchestrationService._station_scoped_rack_tasks(
            resolved_payload.get("rack_tasks"),
            station_code=station_code,
            rack_code=rack_code,
        )
        return resolved_payload

    @staticmethod
    def _station_scoped_rack_tasks(
        value: Any, *, station_code: str, rack_code: str | None = None
    ) -> list[dict[str, Any]]:
        rack_tasks = value if isinstance(value, list) else []
        if not rack_tasks:
            rack_tasks = [
                {
                    "sequence_no": 1,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                    "actions_json": {"required": True},
                }
            ]
        scoped_tasks: list[dict[str, Any]] = []
        for index, task in enumerate(rack_tasks, start=1):
            task_payload = dict(task) if isinstance(task, Mapping) else {"sequence_no": index}
            task_payload.setdefault("sequence_no", index)
            is_move_out = SingleLayerRackOrchestrationService._is_move_out_action(task_payload)
            task_type = SingleLayerRackOrchestrationService._rack_task_type(task_payload.get("task_type"))
            task_payload["task_type"] = task_type
            task_payload["rack_kind"] = RackKind.SINGLE_LAYER.value
            if is_move_out:
                task_payload["source_position_code"] = station_code
                task_payload["target_position_code"] = None
                if rack_code is not None and not task_payload.get("rack_code"):
                    task_payload["rack_code"] = rack_code
                task_payload.setdefault("target_position_role", "SMT_EMPTY_RACK_AREA")
            else:
                task_payload["target_position_code"] = station_code
            request_json = task_payload.get("request_json")
            if isinstance(request_json, Mapping):
                request_payload = dict(request_json)
                station = request_payload.get("station")
                station_payload = dict(station) if isinstance(station, Mapping) else {}
                station_payload["position_code"] = station_code
                request_payload["station"] = station_payload
                request_payload["station_code"] = station_code
                request_payload["position_code"] = station_code
                request_payload["rack_kind"] = RackKind.SINGLE_LAYER.value
                request_payload["task_type"] = task_type
                request_payload["source_position_code"] = task_payload.get("source_position_code")
                request_payload["target_position_code"] = task_payload.get("target_position_code")
                request_payload["target_position_role"] = task_payload.get("target_position_role")
                if is_move_out and task_payload.get("rack_code"):
                    request_payload["rack_code"] = task_payload.get("rack_code")
                task_payload["request_json"] = request_payload
            scoped_tasks.append(task_payload)
        return scoped_tasks

    @staticmethod
    def _dispatch_envelope(
        rack_operation_request: Mapping[str, Any],
        *,
        workline_id: int | None,
        session_id: int | None,
        trace_id: str | None,
    ) -> DispatchEnvelope:
        payload = dict(rack_operation_request["payload"])
        operation_key = str(rack_operation_request["operation_key"])
        task = SingleLayerRackOrchestrationService._first_rack_task(payload)
        dispatch_key = SingleLayerRackOrchestrationService._rack_task_dispatch_key(task, operation_key=operation_key)
        payload_json = SingleLayerRackOrchestrationService._rack_task_payload(
            payload,
            task,
            dispatch_key=dispatch_key,
            operation_key=operation_key,
            operation_type=str(rack_operation_request["operation_type"]),
        )
        return DispatchEnvelope(
            dispatch_key=dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=str(rack_operation_request["target_code"]),
            payload_json=payload_json,
            operation_domain="RACK",
            operation_key=operation_key,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
        )

    @staticmethod
    def _session_id(session: Any | None) -> int | None:
        session_id = getattr(session, "id", None)
        if session_id is None:
            return None
        return int(session_id)

    @staticmethod
    def _single_layer_operation_key(
        *,
        dispatch_key: str | None,
        business_demand_key: str,
        workline_code: str,
        station_code: str,
    ) -> str:
        explicit_dispatch_key = _non_empty_text(dispatch_key)
        if explicit_dispatch_key is not None:
            return explicit_dispatch_key
        return f"wms-rack-operation:{business_demand_key}:{workline_code}:{station_code}"

    @staticmethod
    def _first_rack_task(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        rack_tasks = payload.get("rack_tasks")
        if not isinstance(rack_tasks, list) or not rack_tasks or not isinstance(rack_tasks[0], Mapping):
            raise ValueError("single-layer rack operation requires rack_tasks[0]")
        return rack_tasks[0]

    @staticmethod
    def _rack_task_dispatch_key(task: Mapping[str, Any], *, operation_key: str) -> str:
        explicit_dispatch_key = task.get("dispatch_key")
        if explicit_dispatch_key is not None and str(explicit_dispatch_key).strip():
            return str(explicit_dispatch_key).strip()
        sequence_no = int(task.get("sequence_no") or 1)
        task_type = SingleLayerRackOrchestrationService._rack_task_type(task.get("task_type"))
        return f"rack-operation:{operation_key}:{sequence_no}:{task_type}"

    @staticmethod
    def _rack_task_payload(
        operation_payload: Mapping[str, Any],
        task: Mapping[str, Any],
        *,
        dispatch_key: str,
        operation_key: str,
        operation_type: str,
    ) -> dict[str, Any]:
        request_json = task.get("request_json")
        payload = dict(operation_payload)
        if isinstance(request_json, Mapping):
            payload.update(dict(request_json))
        payload["dispatch_key"] = dispatch_key
        payload["operation_key"] = operation_key
        payload["operation_type"] = operation_type
        payload["sequence_no"] = int(task.get("sequence_no") or 1)
        payload["task_type"] = SingleLayerRackOrchestrationService._rack_task_type(task.get("task_type"))
        payload["rack_kind"] = task.get("rack_kind") or payload.get("rack_kind")
        rack_code = task.get("rack_code") or payload.get("rack_code")
        if rack_code:
            payload["rack_code"] = rack_code
        else:
            payload.pop("rack_code", None)
        payload["source_position_code"] = task.get("source_position_code") if "source_position_code" in task else None
        payload["target_position_code"] = task.get("target_position_code") if "target_position_code" in task else None
        payload["target_position_role"] = task.get("target_position_role") if "target_position_role" in task else None
        station = payload.get("station")
        station_payload = dict(station) if isinstance(station, Mapping) else {}
        station_payload.setdefault("position_code", str(operation_payload.get("station_code") or ""))
        payload["station"] = station_payload
        payload.setdefault("station_code", operation_payload.get("station_code"))
        return payload

    @staticmethod
    def _rack_task_type(value: Any) -> str:
        raw_value = getattr(value, "value", value)
        task_type = str(raw_value or RackTaskType.ALLOCATE_AND_MOVE_RACK.value)
        if task_type in _RACK_TASK_TYPE_ALIASES:
            return _RACK_TASK_TYPE_ALIASES[task_type]
        try:
            return RackTaskType(task_type).value
        except ValueError as exc:
            raise ValueError(f"unsupported rack task_type: {task_type}") from exc

    @staticmethod
    def _is_move_out_action(task: Mapping[str, Any]) -> bool:
        actions_json = task.get("actions_json")
        action = actions_json.get("action") if isinstance(actions_json, Mapping) else None
        return str(action or task.get("task_type") or "") == "MOVE_OUT_ACTIVE_RACK"

    @classmethod
    def _allows_active_rack_bound(cls, payload: Mapping[str, Any] | None) -> bool:
        if not isinstance(payload, Mapping):
            return False
        rack_tasks = payload.get("rack_tasks")
        if not isinstance(rack_tasks, list):
            return False
        return any(isinstance(task, Mapping) and cls._is_move_out_action(task) for task in rack_tasks)

    @staticmethod
    def _station_reason(status: Any, *, fallback: str = "STATION_LEASE_BUSY") -> str:
        reason_code = getattr(status, "reason_code", None)
        if reason_code is None:
            return fallback
        return str(getattr(reason_code, "value", reason_code))

    @staticmethod
    def _station_diagnostics(status: Any) -> dict[str, Any]:
        return {
            "workline_code": getattr(status, "workline_code", None),
            "station_code": getattr(status, "position_code", None),
            "active_rack_code": getattr(status, "active_rack_code", None),
            "active_session_id": getattr(status, "active_session_id", None),
            "active_dispatch_key": getattr(status, "active_dispatch_key", None),
        }

    @staticmethod
    def _rough_sorter_release_fact_payload(
        fact_payload: Mapping[str, Any] | None,
        *,
        business_demand_key: str,
        workline_code: str,
        station_code: str,
    ) -> dict[str, Any]:
        payload = dict(fact_payload or {})
        payload["business_demand_key"] = business_demand_key
        payload["workline_code"] = workline_code
        payload["station_code"] = station_code
        return payload


def _ensure_existing_station_claim_outbox_shape(outbox: SystemOutbox, envelope: DispatchEnvelope) -> None:
    if _enum_text(outbox.dispatch_type) != _enum_text(envelope.dispatch_type):
        raise ValueError("existing station dispatch outbox dispatch_type differs from request")
    if _enum_text(outbox.target_type) != _enum_text(envelope.target_type):
        raise ValueError("existing station dispatch outbox target_type differs from request")
    if outbox.target_code != envelope.target_code:
        raise ValueError("existing station dispatch outbox target_code differs from request")
    if outbox.operation_domain != envelope.operation_domain:
        raise ValueError("existing station dispatch outbox operation_domain differs from request")
    if outbox.operation_key != envelope.operation_key:
        raise ValueError("existing station dispatch outbox operation_key differs from request")
    if outbox.session_id != envelope.session_id:
        raise ValueError("existing station dispatch outbox session_id differs from request")
    if outbox.workline_id != envelope.workline_id:
        raise ValueError("existing station dispatch outbox workline_id differs from request")
    if not _is_active_station_claim_outbox(outbox):
        raise ValueError("existing station dispatch outbox is not active")

    existing_station = _station_position_from_payload(outbox.payload_json)
    requested_station = _station_position_from_payload(envelope.payload_json)
    if existing_station != requested_station:
        raise ValueError("existing station dispatch outbox station differs from request")


def _is_active_station_claim_outbox(outbox: SystemOutbox) -> bool:
    status = _enum_text(getattr(outbox, "status", None))
    if status not in _ACTIVE_STATION_CLAIM_STATUSES:
        return False
    return getattr(outbox, "finished_at", None) is None or status == SystemOutboxStatus.BLOCKED_RESOURCE.value


def _station_position_from_payload(payload_json: Mapping[str, Any] | None) -> str | None:
    payload = dict(payload_json or {})
    station = payload.get("station")
    if isinstance(station, Mapping):
        position_code = _non_empty_text(station.get("position_code"))
        if position_code is not None:
            return position_code
    for key in ("position_code", "target_position_code", "station_code"):
        position_code = _non_empty_text(payload.get(key))
        if position_code is not None:
            return position_code
    return None


def _enum_text(value: Any) -> str | None:
    raw_value = getattr(value, "value", value)
    return _non_empty_text(raw_value)


def _non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


single_layer_rack_orchestration_service = SingleLayerRackOrchestrationService()


__all__ = [
    "SingleLayerRackOrchestrationDecision",
    "SingleLayerRackOrchestrationDecisionCode",
    "SingleLayerRackOrchestrationService",
    "single_layer_rack_orchestration_service",
]
