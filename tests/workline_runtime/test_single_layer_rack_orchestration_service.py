from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.sys.models.outbox import DispatchEnvelope, SystemOutbox, SystemOutboxStatus
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.services.single_layer_rack_orchestration_service import (
    SingleLayerRackOrchestrationDecisionCode,
    SingleLayerRackOrchestrationService,
)
from src.app.workline.services.station_lease_service import StationLeaseReasonCode, StationLeaseResult
from src.utils.timezone import timezone
from src.workline_runtime.runtime_intent import RuntimeIntent, RuntimeIntentKind


@dataclass
class FakeStationLeaseService:
    status: StationLeaseResult
    claim_result: SystemOutbox | None = None
    expose_claim_as_busy: bool = False
    raise_integrity_error: bool = False

    def __post_init__(self) -> None:
        self.status_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self._claimed = False

    async def get_station_lease_status(
        self,
        _db: object,
        *,
        workline_id: int,
        workline_code: str,
        position_code: str,
    ) -> StationLeaseResult:
        self.status_calls.append(
            {
                "workline_id": workline_id,
                "workline_code": workline_code,
                "position_code": position_code,
            }
        )
        return self.status

    async def claim_station_dispatch_lease(
        self,
        _db: object,
        *,
        workline_id: int,
        workline_code: str,
        position_code: str,
        envelope: DispatchEnvelope,
        allow_active_rack_bound: bool = False,
    ) -> SystemOutbox | None:
        self.claim_calls.append(
            {
                "workline_id": workline_id,
                "workline_code": workline_code,
                "position_code": position_code,
                "envelope": envelope,
                "allow_active_rack_bound": allow_active_rack_bound,
            }
        )
        if self.expose_claim_as_busy and self._claimed:
            return None
        if self.raise_integrity_error:
            raise IntegrityError("INSERT INTO system_outbox", {}, Exception("duplicate dispatch_key"))
        self._claimed = self.claim_result is not None
        return self.claim_result


class FakeNestedTransaction:
    async def __aenter__(self) -> FakeNestedTransaction:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: object,
    ) -> bool:
        return False


class FakeDb:
    def begin_nested(self) -> FakeNestedTransaction:
        return FakeNestedTransaction()


class FakeOutboxRepository:
    def __init__(self, existing: SystemOutbox | None) -> None:
        self.existing = existing
        self.locked_calls: list[str] = []

    async def get_by_dispatch_key_for_update(self, _db: object, dispatch_key: str) -> SystemOutbox | None:
        self.locked_calls.append(dispatch_key)
        if self.existing is not None and self.existing.dispatch_key == dispatch_key:
            return self.existing
        return None


class FakeTransportContractService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build_single_layer_rack_operation_request(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        dispatch_key = kwargs.get("dispatch_key") or (
            f"wms-rack-operation:{kwargs['business_demand_key']}:{kwargs['workline_code']}:{kwargs['endpoint_code']}"
        )
        payload = dict(kwargs["payload"])
        payload.update(
            {
                "business_demand_key": kwargs["business_demand_key"],
                "dispatch_key": dispatch_key,
                "operation_key": dispatch_key,
                "workline_code": kwargs["workline_code"],
                "station_code": kwargs["endpoint_code"],
            }
        )
        payload.setdefault(
            "rack_tasks",
            [
                {
                    "sequence_no": 1,
                    "task_type": "ALLOCATE_AND_MOVE_RACK",
                    "rack_kind": "SINGLE_LAYER",
                    "target_position_code": kwargs["endpoint_code"],
                    "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                    "request_json": {
                        "business_demand_key": kwargs["business_demand_key"],
                        "dispatch_key": dispatch_key,
                        "operation_key": dispatch_key,
                        "workline_code": kwargs["workline_code"],
                        "station_code": kwargs["endpoint_code"],
                        "station": {"position_code": kwargs["endpoint_code"]},
                    },
                }
            ],
        )
        if kwargs.get("rack_code") is not None:
            payload["rack_code"] = kwargs["rack_code"]
        if kwargs.get("rack_snapshot_ref") is not None:
            payload["rack_snapshot_ref"] = kwargs["rack_snapshot_ref"]
        return {
            "operation_type": kwargs["operation_type"],
            "operation_key": dispatch_key,
            "target_code": kwargs.get("target_code") or "WMS_RCS_RACK_OPERATION",
            "payload": payload,
            "timeout_seconds": kwargs["timeout_seconds"],
        }


def ready_workline() -> SimpleNamespace:
    return SimpleNamespace(id=1001, line_code="WL-SMT-01", runtime_status=WorkLineRuntimeStatus.READY)


def active_session() -> SimpleNamespace:
    return SimpleNamespace(id=2001)


def stopped_workline() -> SimpleNamespace:
    return SimpleNamespace(id=1001, line_code="WL-SMT-01", runtime_status=WorkLineRuntimeStatus.STOPPED)


def available_status() -> StationLeaseResult:
    return StationLeaseResult(workline_code="WL-SMT-01", position_code="SINGLE_LAYER_A", available=True)


def busy_status(reason_code: StationLeaseReasonCode) -> StationLeaseResult:
    return StationLeaseResult(
        workline_code="WL-SMT-01",
        position_code="SINGLE_LAYER_A",
        available=False,
        reason_code=reason_code,
        active_dispatch_key="dispatch-active",
        active_session_id=601,
    )


def claimed_outbox(dispatch_key: str) -> SystemOutbox:
    return SystemOutbox(
        id=701,
        workline_id=1001,
        operation_domain="WORKLINE",
        operation_key=dispatch_key,
        dispatch_type="EXTERNAL_HTTP",
        dispatch_key=dispatch_key,
        target_type="HTTP_ENDPOINT",
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={"station": {"position_code": "SINGLE_LAYER_A"}},
        status=SystemOutboxStatus.NEW,
    )


def service(
    *,
    station_lease_service: FakeStationLeaseService,
    outbox_repository: FakeOutboxRepository | None = None,
    transport_contract_service: FakeTransportContractService | None = None,
) -> SingleLayerRackOrchestrationService:
    return SingleLayerRackOrchestrationService(
        station_lease_service=station_lease_service,
        **({"outbox_repository": outbox_repository} if outbox_repository is not None else {}),
        transport_contract_service=transport_contract_service or FakeTransportContractService(),
    )


@pytest.mark.asyncio
async def test_orchestration_waits_when_workline_not_ready() -> None:
    lease = FakeStationLeaseService(status=available_status())
    orchestrator = service(station_lease_service=lease)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-001",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=stopped_workline(),
        station_code="SINGLE_LAYER_A",
        rack_code="RACK-001",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.WAITING
    assert decision.reason == "WORKLINE_NOT_READY"
    assert decision.rack_operation_request is None
    assert lease.status_calls == []
    assert lease.claim_calls == []


@pytest.mark.asyncio
async def test_orchestration_waits_when_station_lease_busy() -> None:
    lease = FakeStationLeaseService(status=busy_status(StationLeaseReasonCode.ACTIVE_DISPATCH_LEASE))
    orchestrator = service(station_lease_service=lease)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-001",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
        rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.WAITING
    assert decision.reason == StationLeaseReasonCode.ACTIVE_DISPATCH_LEASE.value
    assert decision.rack_operation_request is None
    assert lease.status_calls == [
        {"workline_id": 1001, "workline_code": "WL-SMT-01", "position_code": "SINGLE_LAYER_A"}
    ]
    assert lease.claim_calls == []


@pytest.mark.asyncio
async def test_orchestration_dispatches_wms_load_when_business_demand_and_station_available() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:ALLOCATE_AND_MOVE_RACK"
    lease = FakeStationLeaseService(status=available_status(), claim_result=claimed_outbox(task_dispatch_key))
    orchestrator = SingleLayerRackOrchestrationService(station_lease_service=lease)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-001",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
        rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert decision.reason is None
    assert decision.rack_operation_request is not None
    runtime_intent = RuntimeIntent.rack_operation_request(**decision.rack_operation_request)
    assert runtime_intent.kind == RuntimeIntentKind.RACK_OPERATION_REQUEST
    assert runtime_intent.idempotency_key == dispatch_key
    payload = runtime_intent.payload_json
    assert payload["business_demand_key"] == "DEMAND-001"
    assert payload["dispatch_key"] == dispatch_key
    assert payload["operation_key"] == dispatch_key
    assert payload["workline_code"] == "WL-SMT-01"
    assert payload["station_code"] == "SINGLE_LAYER_A"
    assert payload["rack_snapshot_ref"] == "snapshot:WL-SMT-01:SINGLE_LAYER_A"
    assert len(lease.claim_calls) == 1
    envelope = lease.claim_calls[0]["envelope"]
    assert envelope.dispatch_key == task_dispatch_key
    assert envelope.operation_domain == "RACK"
    assert envelope.operation_key == dispatch_key
    assert envelope.session_id == 2001
    assert envelope.payload_json["station"]["position_code"] == "SINGLE_LAYER_A"
    assert envelope.payload_json["business_demand_key"] == "DEMAND-001"
    assert envelope.payload_json["operation_type"] == "SUPPLY_SINGLE_LAYER_RACK"
    assert envelope.payload_json["sequence_no"] == 1
    assert envelope.payload_json["task_type"] == "ALLOCATE_AND_MOVE_RACK"
    assert envelope.payload_json["rack_kind"] == "SINGLE_LAYER"
    assert envelope.payload_json["target_position_code"] == "SINGLE_LAYER_A"
    assert envelope.payload_json["target_position_role"] == "SMT_CLASSIFIER_SINGLE_RACK_WORK"


@pytest.mark.asyncio
async def test_orchestration_reuses_existing_station_claim_after_dispatch_key_conflict() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-RACE:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:ALLOCATE_AND_MOVE_RACK"
    existing_outbox = SystemOutbox(
        id=771,
        workline_id=1001,
        session_id=2001,
        operation_domain="RACK",
        operation_key=dispatch_key,
        dispatch_type="EXTERNAL_HTTP",
        dispatch_key=task_dispatch_key,
        target_type="HTTP_ENDPOINT",
        target_code="WMS_RCS_RACK_OPERATION",
        payload_json={"station": {"position_code": "SINGLE_LAYER_A"}},
        status=SystemOutboxStatus.NEW,
    )
    lease = FakeStationLeaseService(status=available_status(), raise_integrity_error=True)
    outbox_repo = FakeOutboxRepository(existing_outbox)
    orchestrator = service(station_lease_service=lease, outbox_repository=outbox_repo)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        FakeDb(),
        business_demand_key="DEMAND-RACE",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert decision.diagnostics == {"outbox_id": 771}
    assert outbox_repo.locked_calls == [task_dispatch_key]


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"session_id": 9999}, "session_id"),
        ({"workline_id": 9999}, "workline_id"),
        ({"payload_json": {"station": {"position_code": "OTHER_STATION"}}}, "station"),
        ({"status": SystemOutboxStatus.SENT, "finished_at": timezone.now_for_db()}, "active"),
    ],
)
@pytest.mark.asyncio
async def test_orchestration_rejects_conflicting_existing_station_claim_outbox(
    overrides: dict[str, Any],
    match: str,
) -> None:
    dispatch_key = "wms-rack-operation:DEMAND-CONFLICT:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:ALLOCATE_AND_MOVE_RACK"
    values = {
        "id": 772,
        "workline_id": 1001,
        "session_id": 2001,
        "operation_domain": "RACK",
        "operation_key": dispatch_key,
        "dispatch_type": "EXTERNAL_HTTP",
        "dispatch_key": task_dispatch_key,
        "target_type": "HTTP_ENDPOINT",
        "target_code": "WMS_RCS_RACK_OPERATION",
        "payload_json": {"station": {"position_code": "SINGLE_LAYER_A"}},
        "status": SystemOutboxStatus.NEW,
    }
    values.update(overrides)
    lease = FakeStationLeaseService(status=available_status(), raise_integrity_error=True)
    outbox_repo = FakeOutboxRepository(SystemOutbox(**values))
    orchestrator = service(station_lease_service=lease, outbox_repository=outbox_repo)

    with pytest.raises(ValueError, match=match):
        await orchestrator.plan_single_layer_rack_dispatch(
            FakeDb(),
            business_demand_key="DEMAND-CONFLICT",
            demand_type="SUPPLY_SINGLE_LAYER_RACK",
            workline=ready_workline(),
            session=active_session(),
            station_code="SINGLE_LAYER_A",
        )


@pytest.mark.asyncio
async def test_orchestration_blocks_without_session_before_claiming_station_lease() -> None:
    lease = FakeStationLeaseService(status=available_status(), claim_result=claimed_outbox("unused"))
    contract = FakeTransportContractService()
    orchestrator = service(station_lease_service=lease, transport_contract_service=contract)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-NO-SESSION",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        station_code="SINGLE_LAYER_A",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.BLOCKED
    assert decision.reason == "SESSION_REQUIRED_FOR_STATION_LEASE"
    assert decision.rack_operation_request is None
    assert lease.status_calls == [
        {"workline_id": 1001, "workline_code": "WL-SMT-01", "position_code": "SINGLE_LAYER_A"}
    ]
    assert lease.claim_calls == []
    assert contract.calls == []


@pytest.mark.asyncio
async def test_orchestration_claim_links_station_outbox_to_owning_session() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-SESSION:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:ALLOCATE_AND_MOVE_RACK"
    lease = FakeStationLeaseService(status=available_status(), claim_result=claimed_outbox(task_dispatch_key))
    orchestrator = SingleLayerRackOrchestrationService(station_lease_service=lease)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-SESSION",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert len(lease.claim_calls) == 1
    envelope = lease.claim_calls[0]["envelope"]
    assert envelope.session_id == 2001


@pytest.mark.asyncio
async def test_orchestration_dispatch_omits_rack_code_when_wms_allocates_rack() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-ALLOCATE:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:ALLOCATE_AND_MOVE_RACK"
    lease = FakeStationLeaseService(status=available_status(), claim_result=claimed_outbox(task_dispatch_key))
    orchestrator = SingleLayerRackOrchestrationService(station_lease_service=lease)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-ALLOCATE",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert len(lease.claim_calls) == 1
    envelope = lease.claim_calls[0]["envelope"]
    assert "rack_code" not in envelope.payload_json
    assert "rack_snapshot_ref" not in envelope.payload_json


@pytest.mark.asyncio
async def test_orchestration_overwrites_payload_tasks_to_claimed_station_scope() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-SCOPE:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:ALLOCATE_AND_MOVE_RACK"
    lease = FakeStationLeaseService(status=available_status(), claim_result=claimed_outbox(task_dispatch_key))
    orchestrator = service(station_lease_service=lease, transport_contract_service=FakeTransportContractService())

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-SCOPE",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
        payload={
            "station": {"position_code": "OTHER_STATION"},
            "station_code": "OTHER_STATION",
            "position_code": "OTHER_STATION",
            "target_position_code": "OTHER_STATION",
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "ALLOCATE_AND_MOVE_RACK",
                    "rack_kind": "FIVE_LAYER",
                    "target_position_code": "OTHER_STATION",
                    "request_json": {
                        "station": {"position_code": "OTHER_STATION"},
                        "station_code": "OTHER_STATION",
                        "position_code": "OTHER_STATION",
                        "target_position_code": "OTHER_STATION",
                        "rack_kind": "FIVE_LAYER",
                    },
                }
            ],
        },
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert len(lease.claim_calls) == 1
    envelope = lease.claim_calls[0]["envelope"]
    assert envelope.payload_json["station"]["position_code"] == "SINGLE_LAYER_A"
    assert envelope.payload_json["station_code"] == "SINGLE_LAYER_A"
    assert envelope.payload_json["position_code"] == "SINGLE_LAYER_A"
    assert envelope.payload_json["target_position_code"] == "SINGLE_LAYER_A"
    assert envelope.payload_json["rack_kind"] == "SINGLE_LAYER"
    assert decision.rack_operation_request is not None
    payload = decision.rack_operation_request["payload"]
    assert payload["station"]["position_code"] == "SINGLE_LAYER_A"
    assert payload["station_code"] == "SINGLE_LAYER_A"
    assert payload["position_code"] == "SINGLE_LAYER_A"
    assert payload["target_position_code"] == "SINGLE_LAYER_A"
    assert payload["rack_tasks"][0]["rack_kind"] == "SINGLE_LAYER"
    assert payload["rack_tasks"][0]["target_position_code"] == "SINGLE_LAYER_A"


@pytest.mark.asyncio
async def test_orchestration_normalizes_aliased_rack_task_type_for_station_lease_key() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-ALIAS:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:MOVE_RACK"
    lease = FakeStationLeaseService(status=available_status(), claim_result=claimed_outbox(task_dispatch_key))
    orchestrator = service(station_lease_service=lease, transport_contract_service=FakeTransportContractService())

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-ALIAS",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
        payload={
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "MOVE_OUT_ACTIVE_RACK",
                    "rack_code": "RACK-ACTIVE",
                    "source_position_code": "OTHER_STATION",
                    "target_position_code": "OTHER_STATION",
                    "target_position_role": "SMT_EMPTY_RACK_AREA",
                    "actions_json": {"action": "MOVE_OUT_ACTIVE_RACK", "required": True},
                    "request_json": {
                        "task_type": "MOVE_OUT_ACTIVE_RACK",
                        "source_position_code": "OTHER_STATION",
                        "station": {"position_code": "SINGLE_LAYER_A"},
                    },
                }
            ],
        },
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert len(lease.claim_calls) == 1
    envelope = lease.claim_calls[0]["envelope"]
    assert envelope.dispatch_key == task_dispatch_key
    assert envelope.payload_json["operation_key"] == dispatch_key
    assert envelope.payload_json["workline_code"] == "WL-SMT-01"
    assert envelope.payload_json["task_type"] == "MOVE_RACK"
    assert envelope.payload_json["rack_code"] == "RACK-ACTIVE"
    assert envelope.payload_json["source_position_code"] == "SINGLE_LAYER_A"
    assert envelope.payload_json["target_position_code"] is None
    assert envelope.payload_json["target_position_role"] == "SMT_EMPTY_RACK_AREA"
    assert decision.rack_operation_request is not None
    payload = decision.rack_operation_request["payload"]
    assert payload["rack_tasks"][0]["task_type"] == "MOVE_RACK"
    assert payload["rack_tasks"][0]["target_position_code"] is None
    assert payload["rack_tasks"][0]["target_position_role"] == "SMT_EMPTY_RACK_AREA"
    assert payload["rack_tasks"][0]["request_json"]["task_type"] == "MOVE_RACK"
    assert payload["rack_tasks"][0]["request_json"]["source_position_code"] == "SINGLE_LAYER_A"
    assert payload["rack_tasks"][0]["request_json"]["target_position_code"] is None
    assert payload["rack_tasks"][0]["request_json"]["target_position_role"] == "SMT_EMPTY_RACK_AREA"
    assert payload["rack_tasks"][0]["actions_json"]["action"] == "MOVE_OUT_ACTIVE_RACK"


@pytest.mark.asyncio
async def test_orchestration_propagates_operation_rack_code_to_move_out_task() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-MOVE-OUT-RACK:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:MOVE_RACK"
    lease = FakeStationLeaseService(
        status=busy_status(StationLeaseReasonCode.ACTIVE_RACK_BOUND),
        claim_result=claimed_outbox(task_dispatch_key),
    )
    orchestrator = service(station_lease_service=lease, transport_contract_service=FakeTransportContractService())

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-MOVE-OUT-RACK",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
        rack_code="RACK-ACTIVE",
        payload={
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "MOVE_OUT_ACTIVE_RACK",
                    "actions_json": {"action": "MOVE_OUT_ACTIVE_RACK", "required": True},
                }
            ],
        },
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert decision.rack_operation_request is not None
    task = decision.rack_operation_request["payload"]["rack_tasks"][0]
    assert task["task_type"] == "MOVE_RACK"
    assert task["rack_code"] == "RACK-ACTIVE"
    assert task["source_position_code"] == "SINGLE_LAYER_A"
    assert lease.claim_calls[0]["envelope"].payload_json["rack_code"] == "RACK-ACTIVE"


@pytest.mark.asyncio
async def test_orchestration_allows_move_out_when_station_has_active_rack_bound() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-MOVE-OUT:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:MOVE_RACK"
    lease = FakeStationLeaseService(
        status=busy_status(StationLeaseReasonCode.ACTIVE_RACK_BOUND),
        claim_result=claimed_outbox(task_dispatch_key),
    )
    orchestrator = service(station_lease_service=lease, transport_contract_service=FakeTransportContractService())

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-MOVE-OUT",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
        payload={
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "MOVE_OUT_ACTIVE_RACK",
                    "rack_code": "RACK-ACTIVE",
                    "actions_json": {"action": "MOVE_OUT_ACTIVE_RACK", "required": True},
                }
            ],
        },
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert len(lease.claim_calls) == 1
    claim = lease.claim_calls[0]
    assert claim["allow_active_rack_bound"] is True
    assert claim["envelope"].payload_json["task_type"] == "MOVE_RACK"
    assert claim["envelope"].payload_json["source_position_code"] == "SINGLE_LAYER_A"
    assert claim["envelope"].payload_json["target_position_code"] is None


@pytest.mark.asyncio
async def test_orchestration_treats_aliased_move_out_without_request_json_as_role_targeted_move() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-ALIAS-NO-REQUEST:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:MOVE_RACK"
    lease = FakeStationLeaseService(status=available_status(), claim_result=claimed_outbox(task_dispatch_key))
    orchestrator = service(station_lease_service=lease, transport_contract_service=FakeTransportContractService())

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-ALIAS-NO-REQUEST",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
        payload={
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "MOVE_OUT_ACTIVE_RACK",
                    "rack_code": "RACK-ACTIVE",
                    "source_position_code": "OTHER_STATION",
                    "target_position_code": "OTHER_STATION",
                    "target_position_role": "SMT_EMPTY_RACK_AREA",
                }
            ],
        },
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    envelope = lease.claim_calls[0]["envelope"]
    assert envelope.dispatch_key == task_dispatch_key
    assert envelope.payload_json["task_type"] == "MOVE_RACK"
    assert envelope.payload_json["target_position_code"] is None
    assert envelope.payload_json["target_position_role"] == "SMT_EMPTY_RACK_AREA"
    assert decision.rack_operation_request is not None
    payload = decision.rack_operation_request["payload"]
    assert payload["rack_tasks"][0]["task_type"] == "MOVE_RACK"
    assert payload["rack_tasks"][0]["target_position_code"] is None
    assert payload["rack_tasks"][0]["target_position_role"] == "SMT_EMPTY_RACK_AREA"


@pytest.mark.asyncio
async def test_orchestration_does_not_leak_operation_level_identity_fields_into_task_outbox() -> None:
    dispatch_key = "wms-rack-operation:DEMAND-CLEAN-TASK:WL-SMT-01:SINGLE_LAYER_A"
    task_dispatch_key = f"rack-operation:{dispatch_key}:1:ALLOCATE_AND_MOVE_RACK"
    lease = FakeStationLeaseService(status=available_status(), claim_result=claimed_outbox(task_dispatch_key))
    orchestrator = service(station_lease_service=lease, transport_contract_service=FakeTransportContractService())

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="DEMAND-CLEAN-TASK",
        demand_type="SUPPLY_SINGLE_LAYER_RACK",
        workline=ready_workline(),
        session=active_session(),
        station_code="SINGLE_LAYER_A",
        payload={
            "source_position_code": "STALE_SOURCE",
            "target_position_role": "STALE_ROLE",
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "ALLOCATE_AND_MOVE_RACK",
                    "rack_kind": "SINGLE_LAYER",
                    "target_position_code": "OTHER_STATION",
                }
            ],
        },
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    envelope = lease.claim_calls[0]["envelope"]
    assert envelope.payload_json["source_position_code"] is None
    assert envelope.payload_json["target_position_code"] == "SINGLE_LAYER_A"
    assert envelope.payload_json["target_position_role"] is None
    assert decision.rack_operation_request is not None
    payload = decision.rack_operation_request["payload"]
    assert payload["rack_tasks"][0].get("source_position_code") is None
    assert payload["rack_tasks"][0]["target_position_code"] == "SINGLE_LAYER_A"
    assert payload["rack_tasks"][0].get("target_position_role") is None


@pytest.mark.asyncio
async def test_concurrent_orchestration_claims_only_one_station_dispatch() -> None:
    first_dispatch_key = (
        "rack-operation:wms-rack-operation:DEMAND-001:WL-SMT-01:SINGLE_LAYER_A:1:ALLOCATE_AND_MOVE_RACK"
    )
    lease = FakeStationLeaseService(
        status=available_status(),
        claim_result=claimed_outbox(first_dispatch_key),
        expose_claim_as_busy=True,
    )
    orchestrator = service(station_lease_service=lease, transport_contract_service=FakeTransportContractService())

    first, second = await asyncio.gather(
        orchestrator.plan_single_layer_rack_dispatch(
            object(),
            business_demand_key="DEMAND-001",
            demand_type="SUPPLY_SINGLE_LAYER_RACK",
            workline=ready_workline(),
            session=active_session(),
            station_code="SINGLE_LAYER_A",
            rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
        ),
        orchestrator.plan_single_layer_rack_dispatch(
            object(),
            business_demand_key="DEMAND-002",
            demand_type="SUPPLY_SINGLE_LAYER_RACK",
            workline=ready_workline(),
            session=active_session(),
            station_code="SINGLE_LAYER_A",
            rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
        ),
    )

    decisions = [first.decision, second.decision]
    assert decisions.count(SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS) == 1
    assert decisions.count(SingleLayerRackOrchestrationDecisionCode.WAITING) == 1
    assert len(lease.claim_calls) == 2


@pytest.mark.asyncio
async def test_orchestration_does_not_use_rack_ready_to_select_business() -> None:
    lease = FakeStationLeaseService(status=available_status())
    contract = FakeTransportContractService()
    orchestrator = service(station_lease_service=lease, transport_contract_service=contract)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key=None,
        demand_type=None,
        workline=ready_workline(),
        station_code="SINGLE_LAYER_A",
        rack_snapshot_ref="snapshot:ready-rack",
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.WAITING
    assert decision.reason == "BUSINESS_DEMAND_REQUIRED"
    assert decision.rack_operation_request is None
    assert lease.status_calls == []
    assert lease.claim_calls == []
    assert contract.calls == []


@pytest.mark.asyncio
async def test_rough_sorter_release_records_fact_without_calling_sorter_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    original_import = builtins.__import__
    imported_sorter_modules: list[str] = []

    def tracking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if "smt_sorting_inbound" in name:
            imported_sorter_modules.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    lease = FakeStationLeaseService(status=available_status())
    orchestrator = service(station_lease_service=lease)

    decision = await orchestrator.plan_single_layer_rack_dispatch(
        object(),
        business_demand_key="ROUGH-SORTER-RELEASE-001",
        demand_type="ROUGH_SORTER_RELEASE_FACT",
        workline=ready_workline(),
        station_code="SINGLE_LAYER_A",
        rack_code="RACK-001",
        fact_payload={"event_type": "ROUGH_SORTER_RELEASED", "rack_code": "RACK-001"},
    )

    assert decision.decision == SingleLayerRackOrchestrationDecisionCode.WAITING
    assert decision.reason == "ROUGH_SORTER_RELEASE_FACT_RECORDED"
    assert decision.fact_payload == {
        "event_type": "ROUGH_SORTER_RELEASED",
        "rack_code": "RACK-001",
        "business_demand_key": "ROUGH-SORTER-RELEASE-001",
        "workline_code": "WL-SMT-01",
        "station_code": "SINGLE_LAYER_A",
    }
    assert decision.rack_operation_request is None
    assert imported_sorter_modules == []
    assert lease.status_calls == []
    assert lease.claim_calls == []
